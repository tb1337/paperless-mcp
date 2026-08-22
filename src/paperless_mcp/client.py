"""Ownership and lazy (re)connection of the shared :class:`PaperlessClient`.

An MCP client such as Claude Desktop launches this server as a subprocess and
treats a failed startup as "server disconnected" — the user then sees no tools
at all and no explanation. Paperless-ngx being briefly unreachable (laptop on
the wrong network, container still booting) must therefore never abort the
handshake. :class:`PaperlessConnection` connects eagerly for a useful log line,
but tolerates failure and retries on the next tool call; until it succeeds the
tools return a structured ``connection_error`` result the model can relay.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Self, TypedDict

import httpx
from mcp.server.mcpserver import Context
from pypaperless import PaperlessClient
from pypaperless.exceptions import PaperlessError

from .config import Settings
from .names import NameCache, NameMap

log = logging.getLogger(__name__)


class LifespanContext(TypedDict):
    """What the session lifespan hands every tool handler.

    A ``TypedDict`` rather than ``dict[str, Any]`` plus two string constants: the
    keys are checked now, so a typo is a type error instead of a ``KeyError`` at
    tool-call time.
    """

    paperless: PaperlessConnection
    settings: Settings


#: The ``Context`` flavour every tool handler receives. It must stay a plain
#: assignment: MCPServer finds the context parameter with ``issubclass``, and
#: pydantic's generic machinery makes ``Context[...]`` a real class, whereas a
#: PEP 695 ``type`` alias would resolve to a TypeAliasType and go undetected.
#: The parameters are ``Context[LifespanContextT, RequestT]``. Because it is an
#: assignment it is evaluated at import despite ``from __future__ import
#: annotations``, which is why ``Settings`` is imported at runtime above.
ToolContext = Context[LifespanContext, Any]


class PaperlessConnection:
    """Own the httpx client and the :class:`PaperlessClient` built on top of it.

    pypaperless never closes an ``httpx.AsyncClient`` it did not create, so the
    connection object closes both halves itself.
    """

    def __init__(self, settings: Settings) -> None:
        """Store settings; no I/O happens until :meth:`open` is awaited."""
        self._settings = settings
        self._http: httpx.AsyncClient | None = None
        self._client: PaperlessClient | None = None
        self._lock = asyncio.Lock()
        self._names = NameCache(settings.name_cache_ttl)

    async def __aenter__(self) -> Self:
        """Open the connection and probe Paperless once, tolerating failure."""
        await self.open()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        """Close the Paperless client and the httpx pool we created."""
        await self.close()

    async def open(self) -> None:
        """Create the HTTP pool and attempt an initial handshake with Paperless.

        Raises:
            RuntimeError: When the connection is already open. Overwriting the
                pair would drop the previous one unclosed, along with its
                sockets; ``httpx.AsyncClient`` refuses a second open for the
                same reason.
        """
        if self._client is not None:
            raise RuntimeError("PaperlessConnection.open() was already awaited")
        self._http = httpx.AsyncClient(
            verify=self._settings.verify_ssl,
            timeout=httpx.Timeout(self._settings.request_timeout),
            follow_redirects=True,
        )
        self._client = PaperlessClient(
            self._settings.paperless_url,
            self._settings.paperless_token,
            client=self._http,
        )
        try:
            await self._client.initialize()
        except PaperlessError as exc:
            log.warning(
                "Could not reach Paperless-ngx at %s yet (%s: %s). Tools will retry on first use.",
                self._settings.paperless_url,
                type(exc).__name__,
                exc,
            )
        else:
            log.info(
                "Connected to Paperless-ngx %s (REST API v%s) at %s",
                self._client.host_version,
                self._client.host_api_version,
                self._client.base_url,
            )

    async def close(self) -> None:
        """Release the Paperless client and the underlying HTTP pool.

        The pool is closed in a ``finally``, so a failure on the Paperless half
        cannot leak the sockets it holds. Both attributes are cleared first, so
        a second call is a no-op even if the first one raised.
        """
        client, http = self._client, self._http
        self._client = None
        self._http = None
        self._names.invalidate()
        try:
            if client is not None:
                await client.close()
        finally:
            if http is not None:
                await http.aclose()

    async def client(self) -> PaperlessClient:
        """Return an initialized :class:`PaperlessClient`, connecting on demand.

        Raises:
            RuntimeError: When the connection was never opened.
            PaperlessError: When Paperless-ngx is unreachable or rejects the token.
        """
        client = self._client
        if client is None:
            raise RuntimeError("PaperlessConnection.open() was never awaited")
        if client.is_initialized:
            return client
        async with self._lock:
            if not client.is_initialized:
                await client.initialize()
                log.info(
                    "Connected to Paperless-ngx %s (REST API v%s)",
                    client.host_version,
                    client.host_api_version,
                )
        return client

    async def names(self) -> NameMap:
        """Return the ``id -> name`` snapshot, loading the master data on first use.

        Deliberately not warmed in :meth:`open`: a Paperless that is briefly
        unreachable must not cost the client its tools, which is the whole
        reason the connection is lazy in the first place.
        """
        return await self._names.get(await self.client())

    def invalidate_names(self) -> None:
        """Discard the name snapshot after a change to the master data."""
        self._names.invalidate()


def _connection(ctx: ToolContext) -> PaperlessConnection:
    return ctx.request_context.lifespan_context["paperless"]


async def get_client(ctx: ToolContext) -> PaperlessClient:
    """Return the shared PaperlessClient, (re)connecting if necessary."""
    return await _connection(ctx).client()


async def get_names(ctx: ToolContext) -> NameMap:
    """Return the shared ``id -> name`` snapshot for the formatters.

    Await this *before* fetching documents: it is also what populates the
    custom-field cache pypaperless enriches a document from while parsing it.
    """
    return await _connection(ctx).names()


def invalidate_names(ctx: ToolContext) -> None:
    """Discard the name snapshot; call after creating, renaming or deleting."""
    _connection(ctx).invalidate_names()


def get_settings(ctx: ToolContext) -> Settings:
    """Return the Settings attached to the MCPServer lifespan context."""
    return ctx.request_context.lifespan_context["settings"]
