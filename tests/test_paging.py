"""Offset/limit windows over Paperless' page-numbered API."""

from __future__ import annotations

from typing import Any

import pytest
from pypaperless import PaperlessClient
from pypaperless.exceptions import ItemNotFoundError

from paperless_mcp.tools._errors import ToolInputError
from paperless_mcp.tools._paging import normalize_csv_filters, page_result, paginate, window
from tests.conftest import FakeService, PaperlessStub, make_client


@pytest.mark.parametrize(
    ("count", "offset", "limit", "expected", "expected_total"),
    [
        (3, 0, 10, [1, 2, 3], 3),
        (5, 0, 2, [1, 2], 5),
        (5, 2, 2, [3, 4], 5),
        # An offset that does not land on a page boundary: the window straddles
        # two server pages and the leading items are dropped.
        (10, 3, 4, [4, 5, 6, 7], 10),
        # Paperless answers 404 for a page past the end; that is an empty window,
        # and the count is unknown rather than zero.
        (3, 100, 5, [], None),
        # limit=0 still costs one request, because the total is the point.
        (3, 0, 0, [], 3),
        # Fewer items than the window asks for, from the last page onwards: this
        # is the only case that reaches the loop's natural exit rather than
        # breaking out on `is_last_page`.
        (5, 4, 10, [5], 5),
    ],
    ids=[
        "under-limit",
        "capped",
        "offset",
        "unaligned-offset",
        "past-end",
        "limit-zero",
        "exhausted-before-limit",
    ],
)
async def test_paginate_windows_a_result_set(
    count: int,
    offset: int,
    limit: int,
    expected: list[int],
    expected_total: int | None,
) -> None:
    paperless, _ = _tags(count)
    items, total = await paginate(paperless.tags, offset=offset, limit=limit)
    assert [tag.id for tag in items] == expected
    assert total == expected_total


async def test_paginate_asks_the_server_for_the_right_page() -> None:
    """A large offset must become a page number, not a client-side skip."""
    paperless, stub = _tags(100)
    items, _ = await paginate(paperless.tags, offset=80, limit=10)
    assert [tag.id for tag in items] == list(range(81, 91))
    assert [request.params for request in stub.requests] == [{"page": "9", "page_size": "10"}]


async def test_paginate_does_not_fetch_a_page_it_will_not_use() -> None:
    """What ``aclose()`` is for: the generator prefetches the next page eagerly.

    Asserted through the request log rather than a flag on the fake, so it is the
    observable effect being pinned and not the bookkeeping.
    """
    paperless, stub = _tags(20)
    await paginate(paperless.tags, offset=0, limit=2)
    assert [request.params["page"] for request in stub.requests] == ["1"]


async def test_paginate_forwards_filters_to_the_service() -> None:
    paperless, stub = _tags(0)
    await paginate(paperless.tags, {"tags__id__none": [1, 2]}, offset=0, limit=5)
    assert stub.requests[-1].params["tags__id__none"] == "1,2"


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


def _tags(count: int) -> tuple[PaperlessClient, PaperlessStub]:
    """A real client over a stub holding *count* tags, plus the request log.

    Driven through the real ``TagService`` rather than a stub service: the paging
    arithmetic here is only correct if ``PageGenerator`` follows ``next`` and ends
    with ``StopAsyncIteration``, and a re-implementation of those cannot prove it.
    """
    stub = PaperlessStub(
        collections={
            "/api/tags/": [
                {"id": pk, "name": f"tag{pk}", "matching_algorithm": 0}
                for pk in range(1, count + 1)
            ]
        }
    )
    return make_client(stub), stub
