"""Shared test fixtures: an in-process MCPServer harness over a fake PaperlessClient.

The fakes mirror pypaperless v6's service shape: ``filter()`` is an async
context manager that scopes a subsequent ``pages()`` call, and ``pages()``
yields page objects with a server-reported ``count``.
"""

from __future__ import annotations

import json
import math
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from mcp.server.mcpserver import MCPServer
from pypaperless.cache import PaperlessCache
from pypaperless.exceptions import NotFoundError

from paperless_mcp.client import CLIENT_KEY, SETTINGS_KEY
from paperless_mcp.config import Settings
from paperless_mcp.names import NameCache, NameMap
from paperless_mcp.tools import register_all


def make_settings(*, readonly: bool = False, enable_delete: bool = True) -> Settings:
    """Build a Settings instance for tests."""
    return Settings(
        paperless_url="http://test",
        paperless_token="t",
        transport="stdio",
        auth_token=None,
        host="127.0.0.1",
        port=0,
        readonly=readonly,
        enable_delete=enable_delete,
        max_file_bytes=1024 * 1024,
    )


class FakePage:
    """Stand-in for ``pypaperless.pagination.Page``."""

    def __init__(self, items: list[Any], *, count: int, is_last_page: bool) -> None:
        self._items = items
        self.count = count
        self.is_last_page = is_last_page

    def __iter__(self) -> Any:
        return iter(self._items)


class FakePageGenerator:
    """Stand-in for ``pypaperless.pagination.PageGenerator``."""

    def __init__(self, items: list[Any], *, page: int, page_size: int) -> None:
        self._items = items
        self._page = page
        self._page_size = max(page_size, 1)
        self.closed = False

    def __aiter__(self) -> FakePageGenerator:
        return self

    async def __anext__(self) -> FakePage:
        total = len(self._items)
        last_page = max(math.ceil(total / self._page_size), 1)
        if self._page > last_page:
            # Matches DRF: asking for a page past the end is a 404, not an
            # empty result set.
            raise NotFoundError(
                httpx.Response(404, request=httpx.Request("GET", "http://test/api/"))
            )
        start = (self._page - 1) * self._page_size
        chunk = self._items[start : start + self._page_size]
        is_last = self._page >= last_page
        self._page += 1
        return FakePage(chunk, count=total, is_last_page=is_last)

    async def aclose(self) -> None:
        self.closed = True


class FakeService:
    """pypaperless-v6-style resource service stub.

    - ``await service(pk)`` -> ``get_result`` (or raises ``get_raises``)
    - ``async with service.filter(**kw) as s: s.pages(...)`` -> ``filter_results``
    - ``service.active()`` -> async iterator over ``active_results``
    - ``service.create(**kw)`` -> a draft namespace carrying the kwargs
    - ``update`` / ``delete`` / ``save`` record their calls
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
        self.page_calls: list[dict[str, Any]] = []
        self.create_calls: list[dict[str, Any]] = []
        self.get_calls: list[tuple[Any, dict[str, Any]]] = []
        self.update_calls: list[Any] = []
        self.delete_calls: list[Any] = []
        self.save_calls: list[Any] = []
        self.generators: list[FakePageGenerator] = []

    async def __call__(self, pk: Any = None, **kwargs: Any) -> Any:
        self.get_calls.append((pk, kwargs))
        if self.get_raises is not None:
            raise self.get_raises
        if callable(self.get_result):
            return self.get_result(pk)
        return self.get_result

    async def __aiter__(self) -> AsyncIterator[Any]:
        # Mirrors ``IterableService.__aiter__``: page through and flatten.
        pages = self.pages()
        try:
            async for page in pages:
                for item in page:
                    yield item
                if page.is_last_page:
                    break
        finally:
            await pages.aclose()

    @asynccontextmanager
    async def filter(self, **kwargs: Any) -> AsyncIterator[FakeService]:
        self.filter_calls.append(kwargs)
        yield self

    def pages(self, page: int = 1, page_size: int = 150) -> FakePageGenerator:
        self.page_calls.append({"page": page, "page_size": page_size})
        generator = FakePageGenerator(self.filter_results, page=page, page_size=page_size)
        self.generators.append(generator)
        return generator

    async def active(self, **_kwargs: Any) -> AsyncIterator[Any]:
        for item in self.active_results:
            yield item

    def create(self, *args: Any, **kwargs: Any) -> Any:
        self.create_calls.append({"args": args, **kwargs})
        return SimpleNamespace(**kwargs)

    async def update(self, obj: Any, **_kwargs: Any) -> bool:
        self.update_calls.append(obj)
        return True

    async def delete(self, obj: Any, *args: Any, **kwargs: Any) -> None:
        self.delete_calls.append({"obj": obj, "args": args, **kwargs})

    async def save(self, draft: Any) -> Any:
        self.save_calls.append(draft)
        return self.save_returns


class FakePaperless:
    """Bare scaffold of a PaperlessClient - fill it in per test."""

    def __init__(self) -> None:
        self.host_version = "3.0.0"
        self.host_api_version = 10
        self.base_url = "http://test"
        # ``load_names`` writes the custom-field cache here, exactly as it does
        # on the real client's runtime.
        self.runtime = SimpleNamespace(cache=PaperlessCache())


class FakeConnection:
    """Stand-in for PaperlessConnection that hands back a fixed fake client.

    Carries a real :class:`NameCache`, so the tools exercise the same warm-up
    and invalidation path they do in production.
    """

    def __init__(self, paperless: Any) -> None:
        self._paperless = paperless
        self._names = NameCache(ttl=0)

    async def client(self) -> Any:
        return self._paperless

    async def names(self) -> NameMap:
        return await self._names.get(self._paperless)

    def invalidate_names(self) -> None:
        self._names.invalidate()


def build_mcp(settings: Settings, paperless: Any) -> MCPServer:
    """Build an MCPServer whose lifespan yields the supplied fake client."""

    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[dict[str, Any]]:
        yield {CLIENT_KEY: FakeConnection(paperless), SETTINGS_KEY: settings}

    mcp = MCPServer("paperless-mcp-test", lifespan=lifespan)
    register_all(mcp, settings)
    return mcp


class _FakeRequestContext:
    def __init__(self, lifespan_context: dict[str, Any]) -> None:
        self.lifespan_context = lifespan_context


class _FakeContext:
    def __init__(self, lifespan_context: dict[str, Any]) -> None:
        self.request_context = _FakeRequestContext(lifespan_context)


type ToolCaller = Callable[..., Awaitable[Any]]


@asynccontextmanager
async def tool_session(mcp: MCPServer) -> AsyncIterator[ToolCaller]:
    """Keep one lifespan open across several tool calls.

    ``call_tool`` enters and leaves the lifespan per call, which hands each call
    a fresh connection - and with it a cold name cache. Use this whenever a test
    is about state that outlives a single call.
    """
    async with mcp._lowlevel_server.lifespan(mcp._lowlevel_server) as lifespan_ctx:
        ctx = _FakeContext(lifespan_ctx)

        async def call(tool_name: str, /, **kwargs: Any) -> Any:
            return await mcp._tool_manager._tools[tool_name].fn(ctx=ctx, **kwargs)

        yield call


async def call_tool(mcp: MCPServer, tool_name: str, /, **kwargs: Any) -> Any:
    """Invoke a registered tool's underlying function with a fake Context.

    Bypasses MCPServer's request pipeline (which only spins up inside a real MCP
    session) so tool bodies can be unit-tested directly. ``tool_name`` is
    positional-only so kwargs like ``name=...`` reach the tool.
    """
    async with tool_session(mcp) as call:
        return await call(tool_name, **kwargs)


def parse_tool_result(result: Any) -> Any:
    """Decode an MCPServer call_tool() return into a structured payload."""
    if isinstance(result, tuple) and len(result) == 2:
        content, structured = result
        if structured is not None:
            return structured
        result = content
    if hasattr(result, "structured_content") and result.structured_content is not None:
        return result.structured_content
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
            "users",
            "share_links",
            "saved_views",
            "tasks",
            "trash",
            "statistics",
            "search",
            "status",
        ):
            setattr(p, attr, FakeService())
        return p

    return _factory
