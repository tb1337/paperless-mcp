"""FastMCP server wiring: lifespan, tool registration, HTTP app."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
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


def _build_lifespan(settings: Settings):
    @asynccontextmanager
    async def lifespan(_server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        async with PaperlessClient(settings.paperless_url, settings.paperless_token) as paperless:
            log.info(
                "Connected to Paperless-ngx %s (API v%s)",
                getattr(paperless, "host_version", "?"),
                getattr(paperless, "host_api_version", "?"),
            )
            yield {CLIENT_KEY: paperless, SETTINGS_KEY: settings}

    return lifespan


def build_mcp(settings: Settings) -> FastMCP:
    """Build a configured FastMCP instance with all enabled tools registered."""
    mcp = FastMCP(
        "paperless-mcp",
        instructions=(
            "Tools for reading and (optionally) writing documents in a Paperless-ngx "
            "instance via pypaperless. Document discovery uses Django-style filters; "
            "use search_documents for full-text queries."
        ),
        lifespan=_build_lifespan(settings),
    )
    register_all(mcp, settings)
    return mcp


def build_app(settings: Settings) -> Starlette:
    """Build the Starlette ASGI app with auth middleware in front of FastMCP."""
    mcp = build_mcp(settings)
    mcp_app = mcp.streamable_http_app()

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
        lifespan=mcp_app.router.lifespan_context,
    )


def serve() -> None:
    """Entry point: load settings, build the app and run uvicorn."""
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
