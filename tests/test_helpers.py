"""Unit tests for the shared tool helpers."""

from __future__ import annotations

import datetime as dt
import inspect
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pypaperless import PaperlessClient
from pypaperless.exceptions import (
    ItemNotFoundError,
    NotFoundError,
    PaperlessTimeoutError,
)

from paperless_mcp.tools._helpers import (
    ToolInputError,
    ToolResultError,
    humanize,
    normalize_csv_filters,
    page_result,
    paginate,
    parse_date,
    parse_datetime,
    safe_tool,
    translate_error,
    window,
)
from tests.conftest import FakeService


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("search_documents", "Search documents"),
        ("get_document_thumbnail", "Get document thumbnail"),
        # Acronyms and proper nouns survive; a bare capitalize() would not.
        ("get_document_ai_suggestions", "Get document AI suggestions"),
        ("get_paperless_info", "Get Paperless info"),
        ("empty_trash", "Empty trash"),
    ],
)
def test_humanize_derives_a_display_title(name: str, expected: str) -> None:
    assert humanize(name) == expected


def _not_found() -> NotFoundError:
    request = httpx.Request("GET", "http://test/api/documents/")
    return NotFoundError(httpx.Response(404, request=request))


@pytest.mark.parametrize(
    ("items", "offset", "limit", "expected", "expected_total"),
    [
        ([1, 2, 3], 0, 10, [1, 2, 3], 3),
        ([1, 2, 3, 4, 5], 0, 2, [1, 2], 5),
        ([1, 2, 3, 4, 5], 2, 2, [3, 4], 5),
        # An offset that does not land on a page boundary: the window straddles
        # two server pages and the leading items are dropped.
        (list(range(1, 11)), 3, 4, [4, 5, 6, 7], 10),
        # Paperless answers 404 for a page past the end; that is an empty window,
        # and the count is unknown rather than zero.
        ([1, 2, 3], 100, 5, [], None),
        # limit=0 still costs one request, because the total is the point.
        ([1, 2, 3], 0, 0, [], 3),
    ],
    ids=["under-limit", "capped", "offset", "unaligned-offset", "past-end", "limit-zero"],
)
async def test_paginate_windows_a_result_set(
    items: list[int],
    offset: int,
    limit: int,
    expected: list[int],
    expected_total: int | None,
) -> None:
    got, total = await paginate(FakeService(filter_results=items), offset=offset, limit=limit)
    assert got == expected
    assert total == expected_total


async def test_paginate_asks_the_server_for_the_right_page() -> None:
    """A large offset must become a page number, not a client-side skip."""
    service = FakeService(filter_results=list(range(1, 101)))
    items, _ = await paginate(service, offset=80, limit=10)
    assert items == list(range(81, 91))
    assert service.page_calls == [{"page": 9, "page_size": 10}]


async def test_paginate_closes_the_page_generator() -> None:
    service = FakeService(filter_results=list(range(20)))
    await paginate(service, offset=0, limit=2)
    assert [g.closed for g in service.generators] == [True]


async def test_paginate_forwards_filters_to_the_service() -> None:
    service = FakeService(filter_results=[])
    await paginate(service, {"tags__id__none": [1, 2]}, offset=0, limit=5)
    assert service.filter_calls == [{"tags__id__none": "1,2"}]


async def test_paginate_rejects_negative() -> None:
    # ToolInputError, not a bare ValueError: only the mapped type reaches the
    # model as a structured result instead of a protocol-level failure.
    with pytest.raises(ToolInputError, match="non-negative"):
        await paginate(FakeService(), offset=-1, limit=5)


async def test_paginate_lets_other_errors_propagate() -> None:
    class _Exploding(FakeService):
        def pages(self, page: int = 1, page_size: int = 150) -> Any:
            raise ItemNotFoundError("boom")

    with pytest.raises(ItemNotFoundError):
        await paginate(_Exploding(), offset=0, limit=5)


def test_normalize_csv_filters_joins_lists() -> None:
    assert normalize_csv_filters({"tags__id__none": [1, 2], "is_tagged": True}) == {
        "tags__id__none": "1,2",
        "is_tagged": True,
    }


def test_window_slices_and_reports_total() -> None:
    items, total = window([1, 2, 3, 4], offset=1, limit=2)
    assert items == [2, 3]
    assert total == 4


def test_window_rejects_negative() -> None:
    with pytest.raises(ToolInputError, match="non-negative"):
        window([1], offset=0, limit=-1)


def test_page_result_computes_has_more_from_total() -> None:
    assert page_result("xs", [1, 2], offset=0, limit=2, total=5, formatter=str) == {
        "xs": ["1", "2"],
        "returned": 2,
        "offset": 0,
        "limit": 2,
        "total": 5,
        "has_more": True,
    }


def test_page_result_without_total_falls_back_to_a_full_page() -> None:
    assert page_result("xs", [1, 2], offset=0, limit=2, total=None, formatter=str)["has_more"]
    assert not page_result("xs", [1], offset=0, limit=2, total=None, formatter=str)["has_more"]


def test_page_result_does_not_promise_more_for_an_empty_window() -> None:
    """``len([]) == 0 == limit`` used to read as "a full page, so there is more"."""
    empty = page_result("xs", [], offset=0, limit=0, total=None, formatter=str)
    assert empty["returned"] == 0
    assert empty["has_more"] is False
    # Past the end of an unknown-length result set, likewise.
    assert not page_result("xs", [], offset=99, limit=25, total=None, formatter=str)["has_more"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-01-02", dt.date(2026, 1, 2)),
        # A datetime keeps only its date half.
        ("2026-01-02T13:45:00", dt.date(2026, 1, 2)),
    ],
)
def test_parse_date(value: str, expected: dt.date) -> None:
    assert parse_date(value, field="created") == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-01-02T13:45:00", dt.datetime(2026, 1, 2, 13, 45)),
        # A bare date widens to midnight.
        ("2026-01-02", dt.datetime(2026, 1, 2, 0, 0)),
    ],
)
def test_parse_datetime(value: str, expected: dt.datetime) -> None:
    assert parse_datetime(value, field="expiration") == expected


@pytest.mark.parametrize(
    ("parse", "field"), [(parse_date, "created"), (parse_datetime, "expiration")]
)
def test_a_date_that_is_not_iso_names_the_field_it_came_from(
    parse: Callable[..., object], field: str
) -> None:
    with pytest.raises(ToolInputError, match=field):
        parse("last tuesday", field=field)


def test_translate_error_prefers_the_most_specific_match() -> None:
    # PaperlessTimeoutError subclasses PaperlessConnectionError, so ordering in
    # the map is what decides which entry wins.
    translated = translate_error(PaperlessTimeoutError())
    assert translated is not None
    assert translated["error"] == "timeout"


def test_translate_error_maps_a_404_to_not_found() -> None:
    translated = translate_error(_not_found())
    assert translated is not None
    assert translated["error"] == "not_found"


def test_translate_error_returns_none_for_foreign_exceptions() -> None:
    assert translate_error(RuntimeError("boom")) is None


def test_translate_error_returns_a_tool_result_error_payload_verbatim() -> None:
    translated = translate_error(ToolResultError("file_too_large", "Too big.", size_bytes=9))
    assert translated == {"error": "file_too_large", "detail": "Too big.", "size_bytes": 9}


def test_tool_result_error_is_not_swallowed_by_the_error_map() -> None:
    # It is checked before the map, so a future entry matching Exception cannot
    # replace the carried payload with a generic one.
    assert translate_error(ToolResultError("boom", "Detail.")) == {
        "error": "boom",
        "detail": "Detail.",
    }


async def test_safe_tool_translates_known_exception() -> None:
    @safe_tool
    async def tool() -> dict[str, Any]:
        raise ItemNotFoundError("doc 42 not found")

    result = await tool()
    assert result["error"] == "not_found"
    assert "42" in result["cause"]


async def test_safe_tool_translates_tool_input_error() -> None:
    @safe_tool
    async def tool() -> dict[str, Any]:
        raise ToolInputError("limit must be positive")

    result = await tool()
    assert result["error"] == "invalid_argument"
    assert result["cause"] == "limit must be positive"


async def test_safe_tool_reraises_unknown_exception() -> None:
    @safe_tool
    async def tool() -> dict[str, Any]:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await tool()


async def test_safe_tool_passes_through_normal_result() -> None:
    @safe_tool
    async def tool() -> dict[str, Any]:
        return {"ok": True}

    assert await tool() == {"ok": True}


def test_safe_tool_preserves_the_wrapped_signature() -> None:
    """MCPServer derives each tool's schema from the signature, so it must survive."""

    @safe_tool
    async def tool(document_id: int, title: str | None = None) -> dict[str, Any]:
        """Doc."""
        return {}

    assert list(inspect.signature(tool).parameters) == ["document_id", "title"]
    assert tool.__doc__ == "Doc."


@pytest.mark.parametrize(
    "service_name",
    [
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
    ],
)
def test_paginated_services_keep_the_filter_plus_pages_contract(service_name: str) -> None:
    """`paginate()` needs `filter()` as an async CM whose scope exposes `pages()`.

    Checked against the real pypaperless services rather than the test fakes:
    6.0.0rc2 removed `TrashService.filter()` because that endpoint declares no
    filters, and only a check like this notices an override changing shape.
    """
    # Constructing the client performs no I/O; the services are cached properties.
    service = getattr(PaperlessClient("http://test", "token"), service_name)

    assert hasattr(service, "pages"), f"{type(service).__name__} must expose pages()"
    scoped = service.filter()
    assert hasattr(scoped, "__aenter__"), (
        f"{type(service).__name__}.filter() must be an async context manager"
    )
