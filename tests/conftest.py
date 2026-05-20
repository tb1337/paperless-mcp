"""Shared test fixtures: in-process FastMCP harness against a mock PaperlessClient.

Each test builds its own narrow mock with only the services it touches; this
keeps tests independent and the failure messages local.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP

from paperless_mcp.client import CLIENT_KEY, SETTINGS_KEY
from paperless_mcp.config import Settings
from paperless_mcp.tools import register_all


# ----------------------------------------------------------------------- settings
def make_settings(*, readonly: bool = False, enable_delete: bool = True) -> Settings:
    """Build a Settings instance for tests."""
    return Settings(
        paperless_url="http://test",
        paperless_token="t",
        auth_token=None,
        host="127.0.0.1",
        port=0,
        readonly=readonly,
        enable_delete=enable_delete,
        max_file_bytes=1024 * 1024,
    )


# ----------------------------------------------------------------------- mock client
class FakeAsyncIter:
    """Reusable async iterator over a fixed list."""

    def __init__(self, items: Iterable[Any]) -> None:
        self._items = list(items)

    def __aiter__(self) -> AsyncIterator[Any]:
        async def gen() -> AsyncIterator[Any]:
            for x in self._items:
                yield x

        return gen()


class FakeService:
    """Pypaperless-style resource service stub.

    - ``await service(pk)`` → ``get_result`` (or raises ``get_raises``)
    - ``service.filter(**kw)`` → async iterator over ``filter_results``
    - ``service.active()`` → async iterator over ``active_results``
    - ``await service.update(obj)`` / ``delete(obj)`` / ``save(draft)``
      → record the call and return ``save_returns``
    - ``service.create()`` returns a fresh draft object (a SimpleNamespace).
    """

    def __init__(
        self,
        *,
        get_result: Any = None,
        get_raises: BaseException | None = None,
        filter_results: Iterable[Any] = (),
        active_results: Iterable[Any] = (),
        save_returns: Any = 999,
    ) -> None:
        self.get_result = get_result
        self.get_raises = get_raises
        self.filter_results = list(filter_results)
        self.active_results = list(active_results)
        self.save_returns = save_returns
        self.filter_calls: list[dict[str, Any]] = []
        self.update_calls: list[Any] = []
        self.delete_calls: list[Any] = []
        self.save_calls: list[Any] = []

    async def __call__(self, pk: Any) -> Any:
        if self.get_raises is not None:
            raise self.get_raises
        if callable(self.get_result):
            return self.get_result(pk)
        return self.get_result

    def filter(self, **kwargs: Any) -> FakeAsyncIter:
        self.filter_calls.append(kwargs)
        return FakeAsyncIter(self.filter_results)

    def active(self) -> FakeAsyncIter:
        return FakeAsyncIter(self.active_results)

    def create(self) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace()

    async def update(self, obj: Any) -> bool:
        self.update_calls.append(obj)
        return True

    async def delete(self, obj: Any) -> None:
        self.delete_calls.append(obj)

    async def save(self, draft: Any) -> Any:
        self.save_calls.append(draft)
        return self.save_returns


class FakePaperless:
    """Bare scaffold of a PaperlessClient — fill it in per-test."""

    def __init__(self) -> None:
        self.host_version = "2.13.0"
        self.host_api_version = "9"
        self.base_url = "http://test"


# ----------------------------------------------------------------------- harness
def build_mcp(settings: Settings, paperless: Any) -> FastMCP:
    """Build a FastMCP server with a lifespan that yields the supplied paperless client."""

    @asynccontextmanager
    async def lifespan(_server: FastMCP):
        yield {CLIENT_KEY: paperless, SETTINGS_KEY: settings}

    mcp = FastMCP("paperless-mcp-test", lifespan=lifespan)
    register_all(mcp, settings)
    return mcp


class _FakeRequestContext:
    def __init__(self, lifespan_context: dict[str, Any]) -> None:
        self.lifespan_context = lifespan_context


class _FakeContext:
    def __init__(self, lifespan_context: dict[str, Any]) -> None:
        self.request_context = _FakeRequestContext(lifespan_context)


async def call_tool(mcp: FastMCP, tool_name: str, /, **kwargs: Any) -> Any:
    """Invoke a registered tool's underlying function with a fake Context.

    Bypasses FastMCP's request pipeline (which only spins up inside a real
    MCP session) so we can unit-test tool bodies directly. ``tool_name`` is
    positional-only so kwargs like ``name=...`` reach the tool.
    """
    tool = mcp._tool_manager._tools[tool_name]
    async with mcp._mcp_server.lifespan(mcp._mcp_server) as lifespan_ctx:  # type: ignore[arg-type]
        ctx = _FakeContext(lifespan_ctx)
        return await tool.fn(ctx=ctx, **kwargs)


def parse_tool_result(result: Any) -> Any:
    """Decode a FastMCP call_tool() return into a structured payload."""
    if isinstance(result, tuple) and len(result) == 2:
        content, structured = result
        if structured is not None:
            return structured
        result = content
    if hasattr(result, "structuredContent") and result.structuredContent is not None:
        return result.structuredContent
    if hasattr(result, "content"):
        result = result.content
    if isinstance(result, list) and result:
        first = result[0]
        text = getattr(first, "text", None)
        if text is None:
            return first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return result


# ----------------------------------------------------------------------- pytest plumbing
@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def make_paperless():
    """Factory: build a fresh FakePaperless with default empty services."""

    def _factory() -> FakePaperless:
        p = FakePaperless()
        # Pre-populate every service slot the tools assume exists.
        for attr in (
            "documents",
            "tags",
            "correspondents",
            "document_types",
            "storage_paths",
            "custom_fields",
            "share_links",
            "saved_views",
            "tasks",
            "trash",
        ):
            setattr(p, attr, FakeService())
        return p

    return _factory
