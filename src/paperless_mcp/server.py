"""MCP server wiring: lifespans, tool registration, transports.

Architecture note: MCPServer's ``lifespan`` parameter runs **per MCP session**,
not once per ASGI app — see ``mcp/server/lowlevel/server.py``, where
``self.lifespan(self)`` is entered inside ``Server.run()``. Opening a
:class:`~paperless_mcp.client.PaperlessConnection` there would build a fresh
httpx connection pool on every client handshake, so for the HTTP transport the
connection is opened in the outer Starlette lifespan (which runs once per app)
and the per-session lifespan just hands back a reference. Over stdio there is
exactly one session for the lifetime of the process, so the session lifespan
owns the connection directly.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from mcp.server.mcpserver import MCPServer
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from . import __version__
from .auth import BearerAuthMiddleware
from .client import CLIENT_KEY, SETTINGS_KEY, PaperlessConnection
from .config import Settings
from .prompts import register_all as register_prompts
from .tools import register_all as register_tools

log = logging.getLogger("paperless_mcp")

#: Unauthenticated liveness endpoint used by the container healthcheck.
HEALTH_PATH = "/healthz"

INSTRUCTIONS = """\
Tools for searching, reading and curating documents in a Paperless-ngx archive.

Start with `search_documents` (full-text `query` plus Django-style filters). No
tool returns a document's full OCR'd text unless you ask for it: `get_document`
gives you the fields plus a short `content_preview`, and `get_document_content`
the whole text. Tags, correspondents, document types and storage paths are
referenced by numeric ID everywhere, and every result reports the resolved name
next to the ID as `<field>_name` — read those instead of looking IDs up. Going
the other way, from a name you were given to the ID a filter or a write needs,
is what `list_tags`, `list_correspondents`, `list_document_types` and
`list_storage_paths` are for.

List-shaped tools page with `offset`/`limit` and report `total` plus `has_more`.
Failures come back as `{"error": ..., "detail": ..., "cause": ...}` rather than
as exceptions, so read the result before retrying.

The server also ships workflow prompts — `triage_inbox`, `monthly_review` and
`find_duplicates` — which chain these tools into the jobs they exist for. They
are the user's to start, so point at one by name when a request matches it
instead of improvising the same sequence.
"""


def configure_logging(settings: Settings) -> None:
    """Send logs to stderr.

    Over stdio, stdout carries the JSON-RPC framing — a single stray byte there
    breaks the session, so every handler must target stderr.
    """
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
        force=True,
    )


def build_mcp(settings: Settings, connection: PaperlessConnection | None = None) -> MCPServer:
    """Build an MCPServer instance with every enabled tool registered.

    Args:
        settings: Resolved runtime configuration.
        connection: A connection opened and owned by the caller (HTTP mode).
            When ``None`` the per-session lifespan opens and closes its own,
            which is what stdio mode wants.
    """

    @asynccontextmanager
    async def session_lifespan(_server: MCPServer) -> AsyncIterator[dict[str, Any]]:
        if connection is not None:
            yield {CLIENT_KEY: connection, SETTINGS_KEY: settings}
            return
        async with PaperlessConnection(settings) as owned:
            yield {CLIENT_KEY: owned, SETTINGS_KEY: settings}

    mcp = MCPServer(
        "paperless-mcp",
        instructions=INSTRUCTIONS,
        version=__version__,
        lifespan=session_lifespan,
    )
    register_tools(mcp, settings)
    register_prompts(mcp, settings)
    return mcp


def build_app(settings: Settings) -> Starlette:
    """Build the Starlette ASGI app with the Paperless connection at app scope."""
    connection = PaperlessConnection(settings)
    mcp = build_mcp(settings, connection)
    # `host` is not a bind address here — the transport turns it into DNS
    # rebinding protection, auto-allowing only localhost Host/Origin headers
    # when it looks like a loopback address. Passing the configured host keeps
    # the container default (0.0.0.0) reachable under its own hostname.
    mcp_app = mcp.streamable_http_app(host=settings.host)

    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "version": __version__})

    @asynccontextmanager
    async def app_lifespan(app: Starlette) -> AsyncIterator[None]:
        # The inner MCP app's own ASGI lifespan has to run too: it starts
        # and stops the session manager (a background task group), without
        # which the streamable-HTTP transport never accepts sessions.
        async with connection, mcp_app.router.lifespan_context(app):
            yield

    middleware: list[Middleware] = []
    if settings.auth_token:
        middleware.append(
            Middleware(
                BearerAuthMiddleware,
                token=settings.auth_token,
                # The container healthcheck must work without the shared secret.
                exempt_paths=(HEALTH_PATH,),
            )
        )
    else:
        log.warning(
            "PAPERLESS_MCP_AUTH_TOKEN is not set - the MCP endpoint is unauthenticated. "
            "Only acceptable on a trusted network or behind a reverse proxy."
        )

    return Starlette(
        routes=[Route(HEALTH_PATH, health), Mount("/", app=mcp_app)],
        middleware=middleware,
        lifespan=app_lifespan,
    )


def serve_stdio(settings: Settings) -> None:
    """Run the MCP server over stdio.

    This is the transport an MCP client such as Claude Desktop uses when it
    launches the server as a subprocess: no listener is opened, and all
    communication happens over stdin/stdout.
    """
    log.info(
        "Starting paperless-mcp %s over stdio (readonly=%s, deletes=%s)",
        __version__,
        settings.readonly,
        settings.expose_deletes,
    )
    build_mcp(settings).run(transport="stdio")


def serve_http(settings: Settings) -> None:
    """Run the MCP server over Streamable HTTP behind uvicorn."""
    log.info(
        "Starting paperless-mcp %s on http://%s:%d/mcp (readonly=%s, deletes=%s)",
        __version__,
        settings.host,
        settings.port,
        settings.readonly,
        settings.expose_deletes,
    )
    uvicorn.run(
        build_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


def serve(settings: Settings) -> None:
    """Run the server using the transport named in *settings*."""
    if settings.transport == "http":
        serve_http(settings)
    else:
        serve_stdio(settings)
