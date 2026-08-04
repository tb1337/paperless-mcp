"""Shared test fixtures: an in-process MCPServer harness over a fake PaperlessClient.

The fakes mirror pypaperless v6's service shape: ``filter()`` is an async
context manager that scopes a subsequent ``pages()`` call, and ``pages()``
yields page objects with a server-reported ``count``.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Final, cast

import httpx
import pytest
from mcp.server.lowlevel.server import ServerRequestContext
from mcp.server.mcpserver import Context, MCPServer
from mcp.types import LATEST_PROTOCOL_VERSION, CallToolResult, TextContent
from pypaperless import PaperlessClient
from pypaperless.cache import PaperlessCache
from pypaperless.const import EndpointPath
from pypaperless.exceptions import NotFoundError

if TYPE_CHECKING:
    from mcp.server.session import ServerSession

from paperless_mcp.client import CLIENT_KEY, SETTINGS_KEY
from paperless_mcp.config import Settings
from paperless_mcp.names import NameCache, NameMap
from paperless_mcp.tools import register_all

#: ``/api/tags/3/`` -> its collection and primary key, so detail CRUD needs no
#: per-resource routing table.
_DETAIL_PATH: Final = re.compile(r"^(?P<collection>/api/[a-z_]+/)(?P<pk>[^/]+)/$")


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


def named(**by_id: str) -> list[SimpleNamespace]:
    """Build master-data stubs from ``{"7": "Temp"}`` pairs.

    Keys are strings because they arrive as keyword arguments; they name the ID.
    """
    return [SimpleNamespace(id=int(pk), name=name) for pk, name in by_id.items()]


def returns(value: Any) -> Callable[..., Awaitable[Any]]:
    """Build an async callable that ignores its arguments and answers *value*."""

    async def _call(*_args: Any, **_kwargs: Any) -> Any:
        return value

    return _call


def rule(rule_type: int | None, value: str | None = None) -> SimpleNamespace:
    """Build one saved-view filter rule."""
    return SimpleNamespace(rule_type=rule_type, value=value)


def document(doc_id: int = 1, title: str = "Test", **overrides: Any) -> SimpleNamespace:
    """A Document stand-in carrying every field the formatters read.

    Every field is present because a missing one reads as ``None`` through
    ``formatting._safe`` rather than failing, which would hide a projection that
    reaches for a field the model does not have.
    """
    fields: dict[str, Any] = {
        "id": doc_id,
        "title": title,
        "correspondent": None,
        "document_type": None,
        "storage_path": None,
        "tags": [],
        "created": None,
        "added": None,
        "modified": None,
        "deleted_at": None,
        "archive_serial_number": None,
        "original_file_name": None,
        "archived_file_name": None,
        "owner": None,
        "page_count": None,
        "mime_type": None,
        "is_shared_by_requester": False,
        "content": "ocr text",
        "custom_fields": [],
        "notes_": [],
        "root_document": None,
        "search_hit_": None,
    }
    return SimpleNamespace(**(fields | overrides))


class BulkRecorder:
    """Records every bulk-edit call so tests can assert exact ordering."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        async def _record(*args: Any, **kwargs: Any) -> None:
            self.calls.append((name, args, kwargs))

        return _record


@dataclass(frozen=True, slots=True)
class RecordedRequest:
    """One request the stub answered, as the test wants to assert on it."""

    method: str
    path: str
    params: dict[str, str]
    json: Any


@dataclass
class PaperlessStub:
    """An in-memory Paperless behind ``httpx.MockTransport``.

    The seam is the HTTP transport, not the service layer, so everything the
    tools actually touch is pypaperless' own code: drafts and their
    ``extra="forbid"``, ``validate_draft()``, ``Page`` deserialization,
    ``PageGenerator`` following ``next`` and ending with ``StopAsyncIteration``,
    ``update()``'s change detection, and the status-to-exception mapping. A
    re-implementation of those drifts from the library by definition; this cannot.

    Args:
        collections: ``list path -> rows``, e.g. ``{"/api/tags/": [{"id": 1, ...}]}``.
            Serves DRF list pagination and, through it, detail CRUD.
        routes: ``(method, path) -> payload`` for the endpoints that are not a
            collection: bulk edits, metadata, notes, suggestions.
        status: ``path -> status code``, to force a failure.
        requests: Every request that arrived, in order.
    """

    collections: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    routes: dict[tuple[str, str], Any] = field(default_factory=dict)
    status: dict[str, int] = field(default_factory=dict)
    requests: list[RecordedRequest] = field(default_factory=list)
    version: str = "3.0.1"
    api_version: int = 10

    def handle(self, request: httpx.Request) -> httpx.Response:
        """Answer one request, recording it first."""
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        self.requests.append(RecordedRequest(request.method, path, dict(request.url.params), body))

        if forced := self.status.get(path):
            return httpx.Response(forced, json={"detail": "forced by the stub"})
        if path == EndpointPath.INDEX:
            return httpx.Response(
                200,
                json={},
                headers={"x-version": self.version, "x-api-version": str(self.api_version)},
            )
        if (route := self.routes.get((request.method, path))) is not None:
            return httpx.Response(200, json=route)
        if path in self.collections:
            return self._collection(request, path, body)
        if match := _DETAIL_PATH.match(path):
            return self._detail(request, match["collection"], match["pk"], body)
        return httpx.Response(404, json={"detail": f"no stub route for {request.method} {path}"})

    def _collection(self, request: httpx.Request, path: str, body: Any) -> httpx.Response:
        rows = self.collections[path]
        if request.method == "POST":
            created = {"id": max((row["id"] for row in rows), default=0) + 1, **(body or {})}
            rows.append(created)
            return httpx.Response(201, json=created)

        params = request.url.params
        page = int(params.get("page", 1))
        page_size = int(params.get("page_size", 25))
        last_page = max(math.ceil(len(rows) / page_size), 1)
        if page > last_page:
            # DRF answers a page past the end with 404, not an empty envelope.
            return httpx.Response(404, json={"detail": "Invalid page."})

        def link(target: int) -> str:
            # Every parameter is carried over, as DRF does: PageGenerator follows
            # this URL, and a dropped page_size silently reshapes the paging.
            return str(request.url.copy_merge_params({"page": str(target)}))

        start = (page - 1) * page_size
        return httpx.Response(
            200,
            json={
                "count": len(rows),
                "next": link(page + 1) if page < last_page else None,
                "previous": link(page - 1) if page > 1 else None,
                "results": rows[start : start + page_size],
            },
        )

    def _detail(
        self, request: httpx.Request, collection: str, pk: str, body: Any
    ) -> httpx.Response:
        rows = self.collections.get(collection, [])
        row = next((candidate for candidate in rows if str(candidate.get("id")) == pk), None)
        if row is None:
            return httpx.Response(404, json={"detail": "Not found."})
        if request.method == "DELETE":
            rows.remove(row)
            return httpx.Response(204)
        if request.method in {"PATCH", "PUT"}:
            row.update(body or {})
        return httpx.Response(200, json=row)


def make_client(stub: PaperlessStub | None = None) -> PaperlessClient:
    """Build a real PaperlessClient whose only fake part is the transport."""
    http = httpx.AsyncClient(transport=httpx.MockTransport((stub or PaperlessStub()).handle))
    return PaperlessClient("http://test", "t", client=http)


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
        self._first_page = page
        self._page_size = max(page_size, 1)
        self.closed = False

    def __aiter__(self) -> FakePageGenerator:
        return self

    async def __anext__(self) -> FakePage:
        total = len(self._items)
        last_page = max(math.ceil(total / self._page_size), 1)
        if self._page > last_page:
            if self._page > self._first_page:
                # Walking off the end ends the iteration, as the real
                # PageGenerator does once a response carries no `next`. Raising
                # here instead is what forced a non-mirroring `break` into
                # FakeService.__aiter__ and made _drain's loop exit untestable.
                raise StopAsyncIteration
            # Matches DRF: *asking* for a page past the end is a 404, not an
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
        # Mirrors ``IterableService.__aiter__``: page through and flatten. No
        # break on ``is_last_page`` - the real one has none either, and the
        # generator terminating is what ends the loop.
        pages = self.pages()
        try:
            async for page in pages:
                for item in page:
                    yield item
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


class FakeTransport:
    """Stand-in for ``pypaperless.transport.PaperlessTransport``.

    Only the endpoints no pypaperless service covers go out this way, so the
    fake records the calls and answers with the bulk-edit envelope.
    """

    def __init__(self) -> None:
        self.post_result: Any = {"result": "OK"}
        self.post_calls: list[dict[str, Any]] = []

    async def post(self, path: str, *, json: dict[str, Any] | None = None, **_kw: Any) -> Any:
        self.post_calls.append({"path": path, "json": json})
        return self.post_result


class FakePaperless:
    """Bare scaffold of a PaperlessClient - fill it in per test."""

    def __init__(self) -> None:
        self.host_version = "3.0.0"
        self.host_api_version = 10
        self.base_url = "http://test"
        # ``load_names`` writes the custom-field cache here, exactly as it does
        # on the real client's runtime.
        self.runtime = SimpleNamespace(cache=PaperlessCache(), transport=FakeTransport())


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


def parse_tool_result(result: CallToolResult) -> Any:
    """Decode a ``CallToolResult`` the way a client would read it.

    Structured output wins when the SDK produced any; otherwise the single
    ``TextContent`` block carries the JSON. A result whose content is not text -
    an image - is handed back as the content list, because there is nothing to
    decode.
    """
    if result.structured_content is not None:
        return result.structured_content
    if len(result.content) == 1 and isinstance(result.content[0], TextContent):
        return json.loads(result.content[0].text)
    return result.content


async def invoke_tool(mcp: MCPServer, tool_name: str, /, **kwargs: Any) -> CallToolResult:
    """Call a tool through MCPServer's own pipeline and return the raw result.

    Unlike :func:`call_tool`, this does not reach past the server to the tool
    function: the arguments go through the published JSON schema and the return
    value through the SDK's result conversion. That is the only way to see what a
    client actually receives - including whether an ``Image`` survives, and
    whether a tool annotated ``-> Image`` can still report an error.

    Raises:
        ToolError: When the arguments do not satisfy the tool's schema. A schema
            rejection never becomes a ``CallToolResult``; the lowlevel request
            handler one layer up is what turns it into an error response.
    """
    async with mcp._lowlevel_server.lifespan(mcp._lowlevel_server) as lifespan_ctx:
        request_context = ServerRequestContext(
            # The tools read nothing but `lifespan_context` off this, and a real
            # ServerSession needs a live transport pair to exist.
            session=cast("ServerSession", SimpleNamespace()),
            lifespan_context=lifespan_ctx,
            protocol_version=LATEST_PROTOCOL_VERSION,
            method="tools/call",
        )
        context: Any = Context(request_context=request_context, mcp_server=mcp)
        return await mcp.call_tool(tool_name, kwargs, context)


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
