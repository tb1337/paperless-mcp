"""Shared helpers used by tool modules: registration, error translation, pagination."""

from __future__ import annotations

import datetime as dt
import functools
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol, cast

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import ValidationError
from pypaperless.exceptions import (
    AsnRequestError,
    AuthError,
    BadJsonResponseError,
    BulkEditError,
    BulkEditPagesError,
    DeletionError,
    DispatchError,
    DraftError,
    DraftFieldRequiredError,
    DraftNotSupportedError,
    ForbiddenError,
    InitializationError,
    ItemNotFoundError,
    JsonResponseWithError,
    NotFoundError,
    PaperlessConnectionError,
    PaperlessError,
    PaperlessTimeoutError,
    PrimaryKeyRequiredError,
    ResponseError,
    SendEmailError,
    TaskNotFoundError,
    UnexpectedStatusError,
)

log = logging.getLogger(__name__)


class ToolInputError(ValueError):
    """Raised when a tool argument is malformed.

    Surfaces to the model as ``{"error": "invalid_argument", ...}`` rather than
    as an MCP protocol error, so it can correct itself and retry.
    """


class ToolResultError(Exception):
    """Raised to report a structured error a tool cannot express as a return value.

    A tool that returns MCP content — ``Image`` — has no dict in its return
    type to put an error into, and widening the annotation is not an option:
    the SDK skips output-schema generation for a bare content type but fails
    outright on a union containing one. Raising carries the payload out
    instead, so the model still sees the usual error shape.

    Args:
        error: The machine-readable code, e.g. ``"file_too_large"``.
        detail: One sentence the model can act on.
        context: Extra fields merged into the result verbatim.
    """

    def __init__(self, error: str, detail: str, **context: Any) -> None:
        super().__init__(detail)
        self.payload: dict[str, Any] = {"error": error, "detail": detail, **context}


#: Ordered most-specific-first: the first matching entry wins, so subclasses
#: must precede their bases (``PaperlessTimeoutError`` before
#: ``PaperlessConnectionError`` before ``InitializationError``).
_ERROR_MAP: tuple[tuple[type[BaseException], str, str], ...] = (
    (ToolInputError, "invalid_argument", "A tool argument was rejected."),
    (NotFoundError, "not_found", "Paperless has no such object (HTTP 404)."),
    (ItemNotFoundError, "not_found", "The requested object does not exist."),
    (TaskNotFoundError, "not_found", "The requested task does not exist."),
    (PrimaryKeyRequiredError, "invalid_argument", "A primary key is required."),
    (
        PaperlessTimeoutError,
        "timeout",
        "Paperless did not respond in time. Retry, or raise PAPERLESS_MCP_TIMEOUT.",
    ),
    (
        PaperlessConnectionError,
        "connection_error",
        "Could not reach the Paperless server. Check PAPERLESS_URL and the network.",
    ),
    (AuthError, "auth_failed", "Paperless rejected the API token. Check PAPERLESS_TOKEN."),
    (ForbiddenError, "forbidden", "The Paperless user may not access this resource."),
    (BulkEditError, "bulk_edit_failed", "Paperless rejected the bulk edit."),
    (DraftFieldRequiredError, "missing_field", "A required field was not supplied."),
    (DraftNotSupportedError, "unsupported", "This resource cannot be created via the API."),
    (DraftError, "draft_invalid", "The new object was rejected by Paperless."),
    (DeletionError, "delete_failed", "Paperless refused the delete."),
    (AsnRequestError, "asn_failed", "Paperless could not assign the next archive serial number."),
    # A DocumentError, so the PaperlessError catch-all would swallow it as a
    # server problem. It is the opposite: a page selection the model can fix.
    (BulkEditPagesError, "invalid_argument", "The page selection cannot produce a valid PDF."),
    (SendEmailError, "email_failed", "Paperless rejected the email request."),
    (JsonResponseWithError, "paperless_error", "Paperless returned an error payload."),
    (BadJsonResponseError, "upstream_error", "Paperless returned invalid JSON."),
    (UnexpectedStatusError, "upstream_error", "Paperless returned an unexpected HTTP status."),
    (DispatchError, "unsupported", "pypaperless cannot route this operation to a service."),
    (InitializationError, "connection_error", "Could not initialize the Paperless connection."),
    (ResponseError, "upstream_error", "Paperless returned an unexpected response."),
    (ValidationError, "invalid_argument", "The supplied values are not what Paperless expects."),
    # Catch-all for anything pypaperless raises that is not enumerated above.
    (PaperlessError, "paperless_error", "The Paperless client reported an error."),
)


def translate_error(exc: BaseException) -> dict[str, Any] | None:
    """Return an LLM-friendly error dict for *exc*, or ``None`` when unmapped."""
    # Not an _ERROR_MAP entry: the code and detail travel with the exception
    # rather than being fixed per type.
    if isinstance(exc, ToolResultError):
        return exc.payload
    for exc_type, code, message in _ERROR_MAP:
        if isinstance(exc, exc_type):
            return {
                "error": code,
                "detail": message,
                "cause": str(exc) or type(exc).__name__,
            }
    return None


def safe_tool[F: Callable[..., Awaitable[Any]]](func: F) -> F:
    """Translate pypaperless errors into structured tool results.

    Tools wrapped with this decorator never raise on expected pypaperless
    failures; instead they return ``{"error": <code>, "detail": ..., "cause":
    ...}``. Unexpected exceptions still propagate so they surface as MCP errors
    and land in the server log.
    """

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await func(*args, **kwargs)
        except Exception as exc:
            translated = translate_error(exc)
            if translated is None:
                raise
            log.info("%s raised %s: %s", func.__name__, type(exc).__name__, exc)
            return translated

    return cast("F", wrapper)


type ToolFunc = Callable[..., Awaitable[Any]]

#: A decorator that registers its argument as a tool and hands it back. The
#: precise signature is not preserved because a registered tool is never called
#: directly from Python — the MCP server owns every invocation.
type ToolDecorator = Callable[[ToolFunc], ToolFunc]

#: Proper nouns and acronyms a derived title must not lower-case.
_TITLE_WORDS: dict[str, str] = {
    "ai": "AI",
    "asn": "ASN",
    "ocr": "OCR",
    "paperless": "Paperless",
}

#: Value for ``openWorldHint``. Every tool here talks to the one configured
#: Paperless instance, so the domain of interaction is closed in the sense the
#: hint means: a call cannot reach an open-ended set of external entities the
#: way a web search or an email send can.
_OPEN_WORLD = False


def humanize(name: str) -> str:
    """Derive a display title from a tool's function name.

    ``Tool.title`` is what a client puts in front of the user, so deriving it
    keeps it from drifting away from the name the model sees. Only the first
    word is capitalized — usually the verb, but ``bulk_*`` leads with its scope.
    """
    verb, *rest = name.split("_")
    return " ".join([verb.capitalize(), *(_TITLE_WORDS.get(word, word) for word in rest)])


def _register(mcp: MCPServer, annotations: ToolAnnotations) -> ToolDecorator:
    """Return a decorator registering a tool with *annotations* and a derived title."""

    def decorate(func: ToolFunc) -> ToolFunc:
        return mcp.tool(title=humanize(func.__name__), annotations=annotations)(func)

    return decorate


def read_tool(mcp: MCPServer) -> ToolDecorator:
    """Register a tool that only reads from Paperless.

    ``destructiveHint`` and ``idempotentHint`` stay unset on purpose: the spec
    gives them meaning only when ``readOnlyHint`` is false, so sending them
    here would be noise a client has to ignore.
    """
    return _register(mcp, ToolAnnotations(read_only_hint=True, open_world_hint=_OPEN_WORLD))


def write_tool(mcp: MCPServer, *, destructive: bool, idempotent: bool) -> ToolDecorator:
    """Register a tool that creates or modifies data.

    Args:
        mcp: The server to register on.
        destructive: Whether a call can overwrite or discard data that was
            already there. Purely additive tools (upload, create, note,
            restore) are not destructive; replacing a field value is.
        idempotent: Whether repeating the identical call converges on the same
            state. False for anything that adds another row, queues another
            task or accumulates (rotation!), so that a client cannot treat a
            retry as free.
    """
    return _register(
        mcp,
        ToolAnnotations(
            read_only_hint=False,
            destructive_hint=destructive,
            idempotent_hint=idempotent,
            open_world_hint=_OPEN_WORLD,
        ),
    )


def delete_tool(mcp: MCPServer) -> ToolDecorator:
    """Register a tool that removes data.

    Deletes are destructive by definition and idempotent in effect: a second
    call finds the object already gone and leaves the archive as it was.
    """
    return _register(
        mcp,
        ToolAnnotations(
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=True,
            open_world_hint=_OPEN_WORLD,
        ),
    )


def parse_date(value: str, *, field: str) -> dt.date:
    """Parse an ISO date (``YYYY-MM-DD``) or datetime, keeping only the date part.

    Raises:
        ToolInputError: When *value* is not ISO 8601.
    """
    try:
        return dt.datetime.fromisoformat(value).date()
    except ValueError:
        pass
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ToolInputError(
            f"{field} must be an ISO date (YYYY-MM-DD) or datetime, got {value!r}"
        ) from exc


def parse_datetime(value: str, *, field: str) -> dt.datetime:
    """Parse an ISO datetime, widening a bare date to midnight.

    Raises:
        ToolInputError: When *value* is not ISO 8601.
    """
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        pass
    try:
        return dt.datetime.combine(dt.date.fromisoformat(value), dt.time.min)
    except ValueError as exc:
        raise ToolInputError(f"{field} must be an ISO datetime or date, got {value!r}") from exc


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


async def _drain(pages: Any, *, skip: int, limit: int) -> tuple[list[Any], int | None]:
    """Collect up to *limit* items from *pages*, dropping the leading *skip*."""
    items: list[Any] = []
    total: int | None = None

    async for page in pages:
        if total is None:
            total = page.count if isinstance(getattr(page, "count", None), int) else None
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
        if page.is_last_page:
            break
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


def page_result(
    key: str,
    items: list[Any],
    *,
    offset: int,
    limit: int,
    total: int | None,
    formatter: Callable[[Any], Any],
    **extra: Any,
) -> dict[str, Any]:
    """Build the uniform envelope every list-shaped tool returns."""
    has_more = (offset + len(items)) < total if total is not None else len(items) == limit
    return {
        key: [formatter(item) for item in items],
        "returned": len(items),
        "offset": offset,
        "limit": limit,
        "total": total,
        "has_more": has_more,
        **extra,
    }
