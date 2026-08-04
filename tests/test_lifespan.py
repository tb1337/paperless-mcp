"""Verify connection lifetime and transport wiring.

Three invariants matter here:

1. Over HTTP the Paperless connection is opened **once per app**, not once per
   MCP session - otherwise every client handshake spins up a fresh httpx pool.
2. Over stdio the session lifespan owns the connection, so it opens and closes
   with the process.
3. A Paperless instance that is down at startup must not abort the MCP
   handshake; the tools return a structured error instead.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from dataclasses import replace
from typing import Any, ClassVar
from unittest.mock import patch

import pytest
from pypaperless import PaperlessClient
from pypaperless.exceptions import PaperlessConnectionError
from starlette.testclient import TestClient

from paperless_mcp import server as server_mod
from paperless_mcp.client import PaperlessConnection
from paperless_mcp.config import Settings
from tests.conftest import call_tool, make_settings


class _FakePaperlessClient:
    """Records initialize/close calls in place of the real client."""

    instances: ClassVar[list[_FakePaperlessClient]] = []

    def __init__(self, url: str, token: str | None = None, *, client: Any = None) -> None:
        self.url = url
        self.token = token
        self.http = client
        self.is_initialized = False
        self.initialize_calls = 0
        self.close_calls = 0
        self.host_version = "3.0.0"
        self.host_api_version = 10
        self.base_url = url
        self.initialize_error: BaseException | None = None
        type(self).instances.append(self)

    async def initialize(self) -> None:
        self.initialize_calls += 1
        if self.initialize_error is not None:
            raise self.initialize_error
        self.is_initialized = True

    async def close(self) -> None:
        self.close_calls += 1


@pytest.fixture
def fake_client_class() -> Iterator[type[_FakePaperlessClient]]:
    _FakePaperlessClient.instances = []
    with patch("paperless_mcp.client.PaperlessClient", _FakePaperlessClient):
        yield _FakePaperlessClient


def _as_fake(client: object) -> _FakePaperlessClient:
    """Narrow what ``connection.client()`` returns to the patched class.

    An assert rather than a cast, so it also proves the patch took effect - the
    recorder attributes below are meaningless if the real client slipped through.
    """
    assert isinstance(client, _FakePaperlessClient)
    return client


def test_the_fake_client_matches_the_surface_the_connection_uses() -> None:
    """The fake stands in for PaperlessClient, so it has to answer the same calls.

    It cannot subclass: `is_initialized`, `host_version` and `base_url` are
    read-only properties upstream, and the fake has to set them. So the agreement
    is asserted instead - without this, a renamed member would leave every test
    below green and the connection broken.
    """
    # Against instances, not the classes: the real client exposes these as
    # properties while the fake assigns them in __init__, and only the instance
    # view is what `PaperlessConnection` actually reads.
    fake = _FakePaperlessClient("http://test", "t")
    real = PaperlessClient("http://test", "t")
    for member in ("initialize", "close", "is_initialized", "host_version", "host_api_version"):
        assert hasattr(real, member), member
        assert hasattr(fake, member), member
    for method in ("initialize", "close"):
        assert list(inspect.signature(getattr(real, method)).parameters) == list(
            inspect.signature(getattr(fake, method)).parameters
        ), method


async def test_connection_opens_and_closes_both_halves(fake_client_class: Any) -> None:
    async with PaperlessConnection(make_settings()) as connection:
        client = _as_fake(await connection.client())
        assert client.initialize_calls == 1
        # The httpx client is ours, so pypaperless will not close it for us.
        assert client.http is not None
    assert client.close_calls == 1
    assert client.http.is_closed


async def test_close_releases_the_pool_even_when_the_client_refuses(
    fake_client_class: Any,
) -> None:
    """A failure on the Paperless half used to skip aclose(), leaking sockets."""
    connection = PaperlessConnection(make_settings())
    await connection.open()
    client = fake_client_class.instances[-1]
    with (
        patch.object(_FakePaperlessClient, "close", autospec=True, side_effect=_always_fail),
        pytest.raises(PaperlessConnectionError),
    ):
        await connection.close()
    assert client.http.is_closed
    # Cleared before the failing await, so teardown can be retried.
    await connection.close()


async def test_opening_twice_is_refused_rather_than_leaking(fake_client_class: Any) -> None:
    """Overwriting the pair would drop the first one unclosed."""
    async with PaperlessConnection(make_settings()) as connection:
        with pytest.raises(RuntimeError, match="already awaited"):
            await connection.open()
    assert len(fake_client_class.instances) == 1


async def test_connection_retries_after_a_failed_startup(fake_client_class: Any) -> None:
    """A Paperless that is down at launch must not kill the MCP session."""
    connection = PaperlessConnection(make_settings())
    with patch.object(
        _FakePaperlessClient,
        "initialize",
        autospec=True,
        side_effect=_fail_once(),
    ):
        await connection.open()  # must not raise
        try:
            client = await connection.client()
        finally:
            await connection.close()
    assert client.is_initialized


def _fail_once() -> Any:
    state = {"calls": 0}

    async def _initialize(self: Any) -> None:
        state["calls"] += 1
        if state["calls"] == 1:
            raise PaperlessConnectionError("paperless is down")
        self.is_initialized = True

    return _initialize


async def test_connection_reuses_an_initialized_client(fake_client_class: Any) -> None:
    async with PaperlessConnection(make_settings()) as connection:
        first = _as_fake(await connection.client())
        second = _as_fake(await connection.client())
    assert first is second
    assert first.initialize_calls == 1


async def test_stdio_session_lifespan_owns_its_connection(fake_client_class: Any) -> None:
    mcp = server_mod.build_mcp(make_settings())
    async with mcp._lowlevel_server.lifespan(mcp._lowlevel_server) as ctx:
        connection = ctx["paperless"]
        assert isinstance(connection, PaperlessConnection)
        await connection.client()
    assert len(fake_client_class.instances) == 1
    assert fake_client_class.instances[0].close_calls == 1


async def test_tools_report_a_down_paperless_instead_of_crashing(
    fake_client_class: Any,
) -> None:
    mcp = server_mod.build_mcp(make_settings())
    with patch.object(
        _FakePaperlessClient,
        "initialize",
        autospec=True,
        side_effect=_always_fail,
    ):
        result = await call_tool(mcp, "get_paperless_info")
    assert result["error"] == "connection_error"


async def _always_fail(_self: Any) -> None:
    raise PaperlessConnectionError("paperless is down")


def test_http_app_opens_the_connection_once(fake_client_class: Any) -> None:
    app = server_mod.build_app(make_settings())
    with TestClient(app) as client:
        for _ in range(3):
            client.get("/mcp", headers={"Accept": "application/json,text/event-stream"})
    assert len(fake_client_class.instances) == 1, "the connection must be app-scoped"
    assert fake_client_class.instances[0].close_calls == 1


def test_http_app_serves_an_unauthenticated_health_endpoint(fake_client_class: Any) -> None:
    app = server_mod.build_app(_secured(make_settings(), "secret"))
    with TestClient(app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"


def test_bearer_auth_guards_the_mcp_endpoint(fake_client_class: Any) -> None:
    app = server_mod.build_app(_secured(make_settings(), "secret"))
    with TestClient(app) as client:
        assert client.get("/mcp").status_code == 401
        assert client.get("/mcp", headers={"Authorization": "Bearer nope"}).status_code == 401
        allowed = client.get(
            "/mcp",
            headers={
                "Authorization": "Bearer secret",
                "Accept": "application/json,text/event-stream",
            },
        )
        assert allowed.status_code != 401


def _secured(settings: Settings, token: str) -> Settings:
    return replace(settings, auth_token=token, transport="http")
