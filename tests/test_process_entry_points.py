"""The transport dispatch and the connection's own accessors.

`serve()` decides which transport a deployment gets, which is worth two lines of
test even though `run()` and `uvicorn.run()` themselves are not. And
`PaperlessConnection.names()` / `.invalidate_names()` were never executed on the
real class: every tool test goes through `FakeConnection`, which shortcuts them.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import Any

import httpx
import pytest
import uvicorn
from mcp.server.mcpserver import MCPServer
from pypaperless import PaperlessClient

from paperless_mcp import server as server_mod
from paperless_mcp.__main__ import main
from paperless_mcp.client import PaperlessConnection
from paperless_mcp.config import Transport
from tests.conftest import PaperlessStub, make_settings


@pytest.mark.parametrize("transport", list(Transport))
def test_serve_dispatches_on_the_configured_transport(
    monkeypatch: pytest.MonkeyPatch, transport: Transport
) -> None:
    started: list[str] = []
    monkeypatch.setattr(server_mod, "serve_stdio", lambda _s: started.append("stdio"))
    monkeypatch.setattr(server_mod, "serve_http", lambda _s: started.append("http"))

    server_mod.serve(replace(make_settings(), transport=transport))

    assert started == [transport]


def test_serve_stdio_runs_the_mcp_server(monkeypatch: pytest.MonkeyPatch) -> None:
    used: list[Any] = []
    monkeypatch.setattr(MCPServer, "run", lambda _self, **kwargs: used.append(kwargs))

    server_mod.serve_stdio(make_settings())

    assert used == [{"transport": "stdio"}]


def test_serve_http_hands_the_app_and_bind_address_to_uvicorn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    used: list[dict[str, Any]] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: used.append({"app": app, **kw}))

    settings = replace(make_settings(), transport=Transport.HTTP, host="127.0.0.1", port=9123)
    server_mod.serve_http(settings)

    assert used[0]["host"] == "127.0.0.1"
    assert used[0]["port"] == 9123
    # uvicorn wants its level lower-cased; the setting is upper-case.
    assert used[0]["log_level"] == settings.log_level.lower()


def test_configure_logging_sends_records_to_stderr() -> None:
    """Over stdio, a single stray byte on stdout breaks the JSON-RPC framing."""
    server_mod.configure_logging(make_settings())
    handlers = logging.getLogger().handlers
    assert handlers
    assert all(getattr(handler, "stream", None) is not None for handler in handlers)


async def test_using_a_connection_before_opening_it_is_a_programming_error() -> None:
    """Not a structured result: no tool can reach this, so it must be loud."""
    connection = PaperlessConnection(make_settings())

    with pytest.raises(RuntimeError, match="was never awaited"):
        await connection.client()


async def test_the_connection_loads_and_invalidates_its_own_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real accessors, which every tool test shortcuts through FakeConnection."""
    stub = PaperlessStub(
        collections={"/api/tags/": [{"id": 1, "name": "paid", "matching_algorithm": 0}]}
    )
    monkeypatch.setattr(PaperlessConnection, "open", _open_over(stub))

    connection = PaperlessConnection(make_settings())
    await connection.open()
    try:
        first = await connection.names()
        assert first.tags == {1: "paid"}
        assert await connection.names() is first

        stub.collections["/api/tags/"].append({"id": 2, "name": "urgent", "matching_algorithm": 0})
        # Still the cached snapshot, because nothing invalidated it.
        assert await connection.names() is first

        connection.invalidate_names()
        assert (await connection.names()).tags == {1: "paid", 2: "urgent"}
    finally:
        await connection.close()


def _open_over(stub: PaperlessStub) -> Any:
    """Replace ``open()`` with one that dials the stub instead of the network."""

    async def open_(self: PaperlessConnection) -> None:
        http = httpx.AsyncClient(transport=httpx.MockTransport(stub.handle))
        self._http = http
        self._client = PaperlessClient("http://test", "t", client=http)

    return open_


def test_main_reports_success_when_the_server_shuts_down_cleanly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit 0 is the path no test reached: 1 is a bind failure, 2 a bad config."""
    monkeypatch.setenv("PAPERLESS_URL", "http://test")
    monkeypatch.setenv("PAPERLESS_TOKEN", "t")
    monkeypatch.setattr("paperless_mcp.__main__.serve", lambda _s: None)

    assert main(["--env-file", "/nonexistent"]) == 0
    assert capsys.readouterr().out == ""


async def test_two_callers_share_one_handshake(monkeypatch: pytest.MonkeyPatch) -> None:
    """The lock exists so a cold connection is initialized once, not per caller."""
    stub = PaperlessStub()
    monkeypatch.setattr(PaperlessConnection, "open", _open_over(stub))

    connection = PaperlessConnection(make_settings())
    await connection.open()
    try:
        first, second = await asyncio.gather(connection.client(), connection.client())
        assert first is second
        assert [r.path for r in stub.requests] == ["/api/schema/"]
    finally:
        await connection.close()
