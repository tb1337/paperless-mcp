"""Verify lifespan semantics in the ASGI app.

Two invariants matter here:

1. The PaperlessClient is opened **once per app**, not once per MCP session —
   otherwise every client handshake would spin up a fresh httpx pool.
2. The auth middleware sits in front of the MCP transport.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.routing import Mount
from starlette.testclient import TestClient

from paperless_mcp.auth import BearerAuthMiddleware
from paperless_mcp.client import CLIENT_KEY, SETTINGS_KEY
from paperless_mcp.config import Settings
from paperless_mcp.tools import register_all
from tests.conftest import make_settings


def _build_app(settings: Settings, paperless: Any, *, counter: dict[str, int]) -> Starlette:
    """Replica of ``server.build_app`` that opens ``paperless`` at app scope.

    We record every enter/exit of the *outer* lifespan to confirm it fires
    exactly once per app, regardless of how many MCP sessions are opened.
    """
    shared: dict[str, Any] = {CLIENT_KEY: None, SETTINGS_KEY: settings}

    @asynccontextmanager
    async def session_lifespan(_server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        # Per-session: just hand out the shared dict.
        yield shared

    mcp = FastMCP("paperless-mcp-lifespan-test", lifespan=session_lifespan)
    register_all(mcp, settings)
    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def app_lifespan(app: Starlette) -> AsyncIterator[None]:
        counter["enter"] += 1
        shared[CLIENT_KEY] = paperless
        try:
            async with mcp_app.router.lifespan_context(app):
                yield
        finally:
            shared[CLIENT_KEY] = None
            counter["exit"] += 1

    middleware: list[Middleware] = []
    if settings.auth_token:
        middleware.append(Middleware(BearerAuthMiddleware, token=settings.auth_token))
    return Starlette(
        routes=[Mount("/", app=mcp_app)],
        middleware=middleware,
        lifespan=app_lifespan,
    )


def test_paperless_client_lifespan_is_app_scoped(make_paperless: Any) -> None:
    """Three requests must not result in three PaperlessClient instances."""
    counter = {"enter": 0, "exit": 0}
    app = _build_app(make_settings(), make_paperless(), counter=counter)

    with TestClient(app) as client:
        for _ in range(3):
            response = client.get("/mcp", headers={"Accept": "application/json,text/event-stream"})
            # MCP transport may reject (421 from TestClient host header, 400/406
            # without a valid MCP envelope) — we only care that the request
            # reached the app at all.
            assert response.status_code in (200, 400, 406, 421)
        assert counter["enter"] == 1, "PaperlessClient must be opened once per app"
        assert counter["exit"] == 0

    assert counter["enter"] == 1
    assert counter["exit"] == 1


def test_real_build_app_opens_client_once(make_paperless: Any) -> None:
    """End-to-end: server.build_app must open PaperlessClient exactly once.

    We patch the PaperlessClient constructor so we can observe how many times
    it gets called regardless of HTTP traffic.
    """
    from paperless_mcp import server as server_mod

    paperless = make_paperless()
    instances: list[Any] = []

    @asynccontextmanager
    async def fake_client(_url: str, _token: str) -> AsyncIterator[Any]:
        instances.append(paperless)
        yield paperless

    class _FakeClass:
        def __init__(self, url: str, token: str) -> None:
            self._cm = fake_client(url, token)

        async def __aenter__(self) -> Any:
            return await self._cm.__aenter__()

        async def __aexit__(self, *exc: Any) -> Any:
            return await self._cm.__aexit__(*exc)

    with patch.object(server_mod, "PaperlessClient", _FakeClass):
        app = server_mod.build_app(make_settings())
        with TestClient(app) as client:
            for _ in range(3):
                client.get("/mcp")

    assert len(instances) == 1, f"Expected 1 PaperlessClient instance, got {len(instances)}"


def test_bearer_auth_blocks_before_lifespan_traffic(make_paperless: Any) -> None:
    """Auth runs in front of the MCP transport — unauthenticated requests must
    never reach the inner app, even though the outer lifespan is still entered
    once.
    """
    counter = {"enter": 0, "exit": 0}
    settings = make_settings()
    secured = Settings(
        paperless_url=settings.paperless_url,
        paperless_token=settings.paperless_token,
        auth_token="secret",
        host=settings.host,
        port=settings.port,
        readonly=settings.readonly,
        enable_delete=settings.enable_delete,
        max_file_bytes=settings.max_file_bytes,
    )
    app = _build_app(secured, make_paperless(), counter=counter)

    with TestClient(app) as client:
        assert client.get("/mcp").status_code == 401
        assert client.get("/mcp", headers={"Authorization": "Bearer nope"}).status_code == 401
        right = client.get(
            "/mcp",
            headers={
                "Authorization": "Bearer secret",
                "Accept": "application/json,text/event-stream",
            },
        )
        assert right.status_code != 401

    assert counter["enter"] == 1
    assert counter["exit"] == 1
