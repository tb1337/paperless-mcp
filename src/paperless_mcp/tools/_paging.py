"""Offset/limit windows over Paperless' page-numbered API.

Paperless paginates by page number, so an arbitrary offset becomes "start at the
page holding it, then drop the leading items". Every list-shaped tool returns the
envelope :func:`page_result` builds, so ``total`` and ``has_more`` mean the same
thing everywhere.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from typing import Any, Protocol

from pypaperless.exceptions import NotFoundError

from ._errors import ToolInputError


class Page[ItemT](Protocol):
    """Structural type of one page of a paginated Paperless response.

    ``count`` is declared ``int`` because the real ``Page`` declares it that way -
    it defaults to 0 and is never absent. The fallback that used to guard it was
    defensiveness against a shape the library cannot produce.
    """

    count: int

    def __iter__(self) -> Iterator[ItemT]:
        """Yield this page's deserialized items."""
        ...


class Filterable(Protocol):
    """Structural type of a pypaperless service that filters and paginates."""

    def filter(self, **kwargs: Any) -> Any:
        """Return an async context manager scoping subsequent iteration."""
        ...


def normalize_csv_filters(filters: Mapping[str, Any]) -> dict[str, Any]:
    """Join list-valued lookups into the comma-separated form Paperless expects.

    ``IterableService.pages()`` already does this for ``__in`` and ``__all``;
    ``__none`` would otherwise go out as repeated query parameters, of which
    Django reads only the last one.
    """
    return {
        key: ",".join(str(item) for item in value) if isinstance(value, list) else value
        for key, value in filters.items()
    }


def _slice_plan(offset: int, limit: int) -> tuple[int, int, int]:
    """Return ``(page_size, first_page, skip_within_first_page)`` for a window.

    Paperless paginates by page number, so an arbitrary offset becomes "start
    at the page holding it, then drop the leading items". Using
    ``page_size == limit`` keeps every window to at most two requests.
    """
    page_size = max(limit, 1)
    return page_size, offset // page_size + 1, offset % page_size


async def paginate(
    service: Filterable,
    filters: Mapping[str, Any] | None = None,
    *,
    offset: int = 0,
    limit: int = 25,
) -> tuple[list[Any], int | None]:
    """Fetch a single offset/limit window from an iterable pypaperless service.

    Paging happens server-side, so the cost does not grow with ``offset``.

    Returns:
        ``(items, total)``, where ``total`` is the match count reported by
        Paperless or ``None`` when it did not report one.

    Raises:
        ToolInputError: When ``offset`` or ``limit`` is negative.
    """
    if offset < 0 or limit < 0:
        raise ToolInputError("offset and limit must be non-negative")

    params = normalize_csv_filters(filters or {})
    page_size, first_page, skip = _slice_plan(offset, limit)

    async with service.filter(**params) as scoped:
        pages = scoped.pages(page=first_page, page_size=page_size)
        try:
            return await _drain(pages, skip=skip, limit=limit)
        except NotFoundError:
            # DRF answers 404 for a page number past the end of the result set.
            return [], None
        finally:
            await pages.aclose()


async def _drain[ItemT](
    pages: AsyncIterator[Page[ItemT]], *, skip: int, limit: int
) -> tuple[list[ItemT], int | None]:
    """Collect up to *limit* items from *pages*, dropping the leading *skip*.

    Runs the generator out rather than stopping on ``page.is_last_page``. That
    check was redundant: pypaperless stops following pages once a response
    carries no ``next``, which is the same response that reports
    ``is_last_page``, so the loop ended on the same page either way — and it
    costs no extra request, because there is no ``next`` left to prefetch.
    """
    items: list[ItemT] = []
    total: int | None = None

    async for page in pages:
        if total is None:
            total = page.count
        if limit == 0:
            # One request is still worth it: the caller gets an accurate total.
            break
        for item in page:
            if skip > 0:
                skip -= 1
                continue
            items.append(item)
            if len(items) >= limit:
                return items, total
    return items, total


def window[ItemT](items: list[ItemT], *, offset: int, limit: int) -> tuple[list[ItemT], int]:
    """Apply an offset/limit window to an already-materialized list.

    Used for the Paperless endpoints that answer with a bare list instead of a
    paginated envelope (document notes, a document's share links, active tasks).

    Raises:
        ToolInputError: When ``offset`` or ``limit`` is negative.
    """
    if offset < 0 or limit < 0:
        raise ToolInputError("offset and limit must be non-negative")
    return items[offset : offset + limit], len(items)


def page_result[ItemT](
    key: str,
    items: list[ItemT],
    *,
    offset: int,
    limit: int,
    total: int | None,
    formatter: Callable[[ItemT], Any],
    **extra: Any,
) -> dict[str, Any]:
    """Build the uniform envelope every list-shaped tool returns."""
    if total is not None:
        has_more = (offset + len(items)) < total
    else:
        # Without a server-reported count, a full window is the only hint that
        # more may follow. `items` has to be non-empty for that: `limit=0`
        # returns nothing and would otherwise claim there is more.
        has_more = bool(items) and len(items) == limit
    return {
        key: [formatter(item) for item in items],
        "returned": len(items),
        "offset": offset,
        "limit": limit,
        "total": total,
        "has_more": has_more,
        **extra,
    }
