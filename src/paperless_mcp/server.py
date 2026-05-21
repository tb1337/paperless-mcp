"""FastMCP server wiring: lifespans, tool registration, HTTP app.

Architecture note: FastMCP's ``lifespan`` parameter is invoked **per MCP
session**, not once per ASGI app — see ``mcp/server/lowlevel/server.py`` where
``self.lifespan(self)`` is entered inside ``Server.run()``. Naively opening a
PaperlessClient there would create a brand-new httpx connection pool on every
client handshake. We therefore open the PaperlessClient in the outer Starlette
lifespan (which runs once per app) and let the FastMCP per-session lifespan
simply hand back a reference to it.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import uvicorn
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from pypaperless import PaperlessClient
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.routing import Mount

from .auth import BearerAuthMiddleware
from .client import CLIENT_KEY, SETTINGS_KEY
from .config import Settings, load_settings
from .tools import register_all

log = logging.getLogger("paperless_mcp")


def build_mcp(settings: Settings) -> FastMCP:
    """Build a configured FastMCP instance with all enabled tools registered.

    The returned server's per-session lifespan yields a placeholder dict whose
    paperless client is ``None``; in production this is replaced by
    :func:`build_app` which wires the long-lived client into the lifespan.
    """
    shared: dict[str, Any] = {CLIENT_KEY: None, SETTINGS_KEY: settings}

    @asynccontextmanager
    async def session_lifespan(_server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        if shared[CLIENT_KEY] is None:
            # We're being run without an outer app lifespan (e.g. from tests).
            # Open and close a one-shot client so tools that try to use it can
            # at least operate.
            http_client = httpx.AsyncClient(verify=settings.verify_ssl)
            async with PaperlessClient(
                settings.paperless_url, settings.paperless_token, client=http_client
            ) as p:
                yield {CLIENT_KEY: p, SETTINGS_KEY: settings}
            return
        yield shared

    mcp = FastMCP(
        "paperless-mcp",
        instructions=(
            "Tools for reading and (optionally) writing documents in a Paperless-ngx "
            "instance via pypaperless. Document discovery uses Django-style filters; "
            "use search_documents for full-text queries."
        ),
        lifespan=session_lifespan,
    )
    register_all(mcp, settings)
    # Attach the shared dict so build_app can populate it without re-wiring.
    mcp._paperless_mcp_shared = shared  # type: ignore[attr-defined]
    return mcp


def build_app(settings: Settings) -> Starlette:
    """Build the Starlette ASGI app with the PaperlessClient hosted at app scope.

    Auth middleware (if a token is configured) is layered in front of the
    FastMCP streamable-HTTP app.
    """
    mcp = build_mcp(settings)
    shared: dict[str, Any] = mcp._paperless_mcp_shared  # type: ignore[attr-defined]
    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def app_lifespan(app: Starlette) -> AsyncIterator[None]:
        http_client = httpx.AsyncClient(verify=settings.verify_ssl)
        async with PaperlessClient(
            settings.paperless_url, settings.paperless_token, client=http_client
        ) as paperless:
            log.info(
                "Connected to Paperless-ngx %s (API v%s)",
                getattr(paperless, "host_version", "?"),
                getattr(paperless, "host_api_version", "?"),
            )
            shared[CLIENT_KEY] = paperless
            try:
                # Delegate to the inner FastMCP app's own ASGI lifespan so its
                # session manager (a background task group) starts and stops
                # cleanly. Without this the streamable-HTTP transport would
                # not be ready to accept sessions.
                async with mcp_app.router.lifespan_context(app):
                    yield
            finally:
                shared[CLIENT_KEY] = None

    middleware: list[Middleware] = []
    if settings.auth_token:
        middleware.append(Middleware(BearerAuthMiddleware, token=settings.auth_token))
    else:
        log.warning(
            "PAPERLESS_MCP_AUTH_TOKEN is not set — the MCP endpoint is unauthenticated. "
            "Only acceptable on a trusted network or behind a reverse proxy."
        )

    return Starlette(
        routes=[Mount("/", app=mcp_app)],
        middleware=middleware,
        lifespan=app_lifespan,
    )


def serve_stdio() -> None:
    """Entry point: load settings and run the MCP server over stdio.

    This mode is used when Claude Desktop (or another MCP client) launches the
    server as a subprocess.  No HTTP listener is started; all communication
    happens over stdin/stdout.
    """
    load_dotenv()
    # Keep logging quiet so it does not pollute the stdio transport stream.
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = load_settings()
    mcp = build_mcp(settings)
    mcp.run()  # defaults to stdio transport


def serve() -> None:
    """Entry point: load settings, build the app and run uvicorn."""
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = load_settings()
    log.info(
        "Starting paperless-mcp on %s:%d (readonly=%s, enable_delete=%s)",
        settings.host,
        settings.port,
        settings.readonly,
        settings.enable_delete,
    )
    app = build_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
