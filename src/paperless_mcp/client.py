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
from typing import TYPE_CHECKING, Any, Self

import httpx
from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession
from pypaperless import PaperlessClient
from pypaperless.exceptions import PaperlessError

if TYPE_CHECKING:
    from .config import Settings

log = logging.getLogger(__name__)

CLIENT_KEY = "paperless"
SETTINGS_KEY = "settings"

#: The ``Context`` flavour every tool handler receives. It must stay a plain
#: assignment: FastMCP finds the context parameter with ``issubclass``, and
#: pydantic's generic machinery makes ``Context[...]`` a real class, whereas a
#: PEP 695 ``type`` alias would resolve to a TypeAliasType and go undetected.
ToolContext = Context[ServerSession, dict[str, Any], Any]


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

    async def __aenter__(self) -> Self:
        """Open the connection and probe Paperless once, tolerating failure."""
        await self.open()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        """Close the Paperless client and the httpx pool we created."""
        await self.close()

    async def open(self) -> None:
        """Create the HTTP pool and attempt an initial handshake with Paperless."""
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
        """Release the Paperless client and the underlying HTTP pool."""
        if self._client is not None:
            await self._client.close()
            self._client = None
        if self._http is not None:
            await self._http.aclose()
            self._http = None

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


def _lifespan(ctx: ToolContext) -> dict[str, Any]:
    return ctx.request_context.lifespan_context


async def get_client(ctx: ToolContext) -> PaperlessClient:
    """Return the shared PaperlessClient, (re)connecting if necessary."""
    connection: PaperlessConnection = _lifespan(ctx)[CLIENT_KEY]
    return await connection.client()


def get_settings(ctx: ToolContext) -> Settings:
    """Return the Settings attached to the FastMCP lifespan context."""
    settings: Settings = _lifespan(ctx)[SETTINGS_KEY]
    return settings
