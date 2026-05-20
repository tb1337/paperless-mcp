"""Shared helpers used by tool modules: pagination and error translation."""

from __future__ import annotations

import functools
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)


# Mapping of pypaperless exception class names to (error_code, http-ish hint).
# We match by class name so we don't have to import every exception (and so
# the helper works even if pypaperless adds new ones).
_ERROR_MAP: dict[str, tuple[str, str]] = {
    "ItemNotFoundError": ("not_found", "The requested object does not exist."),
    "TaskNotFoundError": ("not_found", "The requested task does not exist."),
    "PrimaryKeyRequiredError": ("invalid_argument", "A primary key is required."),
    "AuthError": ("auth_failed", "Paperless rejected our API token."),
    "InvalidTokenError": ("auth_failed", "Paperless API token is invalid."),
    "InactiveOrDeletedError": ("auth_failed", "The Paperless user is inactive or deleted."),
    "ForbiddenError": ("forbidden", "Paperless denied access to this resource."),
    "BulkEditError": ("bulk_edit_failed", "Paperless rejected the bulk edit."),
    "DraftFieldRequiredError": ("missing_field", "A required field was not set on the draft."),
    "DraftError": ("draft_invalid", "The draft was rejected by Paperless."),
    "DeletionError": ("delete_failed", "Paperless refused the delete."),
    "AsnRequestError": ("asn_failed", "Paperless could not assign a next ASN."),
    "SendEmailError": ("email_failed", "Paperless rejected the email request."),
    "JsonResponseWithError": ("paperless_error", "Paperless returned an error payload."),
    "BadJsonResponseError": ("upstream_error", "Paperless returned invalid JSON."),
    "PaperlessConnectionError": ("connection_error", "Could not reach the Paperless server."),
    "ResponseError": ("upstream_error", "Paperless returned an unexpected response."),
}


def safe_tool[F: Callable[..., Awaitable[Any]]](func: F) -> F:
    """Translate pypaperless errors into LLM-friendly dict results.

    Tools wrapped with this decorator never raise on expected pypaperless
    failures; instead they return ``{"error": <code>, "detail": <message>}``.
    Unexpected exceptions still propagate so they show up as MCP errors and
    in the server log.
    """

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await func(*args, **kwargs)
        except Exception as exc:
            name = type(exc).__name__
            if name not in _ERROR_MAP:
                # Re-raise unexpected exceptions so MCP marks the result as
                # an error and the trace ends up in the logs.
                raise
            code, message = _ERROR_MAP[name]
            log.info("%s raised %s: %s", func.__name__, name, exc)
            return {"error": code, "detail": message, "cause": str(exc) or name}

    return wrapper  # type: ignore[return-value]


async def collect[T](
    aiter: AsyncIterator[T],
    *,
    offset: int = 0,
    limit: int = 25,
) -> tuple[list[T], bool]:
    """Materialize an async iterator with offset/limit, returning (items, has_more).

    ``has_more`` is True when at least one more item was available past
    ``offset + limit``. We probe by attempting one extra element rather than
    consuming the whole iterator.
    """
    if offset < 0 or limit < 0:
        raise ValueError("offset and limit must be non-negative")
    if limit == 0:
        # Still report whether anything would have been available.
        async for _ in aiter:
            return [], True
        return [], False

    items: list[T] = []
    skipped = 0
    has_more = False
    async for item in aiter:
        if skipped < offset:
            skipped += 1
            continue
        if len(items) < limit:
            items.append(item)
            continue
        has_more = True
        break
    return items, has_more
