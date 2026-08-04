"""Structured errors: the shape every tool answers with instead of raising.

A protocol-level failure gives the model nothing to recover from, so every tool is
wrapped in :func:`safe_tool` and a mapped exception becomes a result it can read.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast

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
