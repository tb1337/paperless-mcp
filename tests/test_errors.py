"""Structured errors: what a tool answers with instead of raising."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import httpx
import pytest
from pypaperless.exceptions import (
    DeletionError,
    ItemNotFoundError,
    NotFoundError,
    PaperlessTimeoutError,
)

from paperless_mcp.tools._errors import (
    _ERROR_MAP,
    ToolInputError,
    ToolResultError,
    safe_tool,
    translate_error,
)


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


def _not_found() -> NotFoundError:
    request = httpx.Request("GET", "http://test/api/documents/")
    return NotFoundError(httpx.Response(404, request=request))


def test_a_deletion_error_without_an_http_cause_stays_a_refusal() -> None:
    """The `DeletionError` entry in the table is still what answers.

    `transport.delete()` always chains an `httpx.HTTPStatusError` today, which is what
    lets the 404 be recognized. A future path that raises the same exception without
    one must still be reported rather than fall through to the catch-all.
    """
    translated = translate_error(DeletionError("Paperless said no"))

    assert translated == {
        "error": "delete_failed",
        "detail": "Paperless refused the delete.",
        "cause": "Paperless said no",
    }


def _refused_delete(status: int) -> DeletionError:
    """A DeletionError chaining the HTTPStatusError transport.delete() wraps."""
    request = httpx.Request("DELETE", "http://test/api/documents/9/")
    failure = httpx.HTTPStatusError(
        "boom", request=request, response=httpx.Response(status, request=request)
    )
    error = DeletionError("wrapped")
    error.__cause__ = failure
    return error


def test_a_deleted_404_reads_exactly_like_a_read_404() -> None:
    """One condition, one spelling, whatever the verb.

    The delete path takes its code and detail from the same ``_ERROR_MAP`` rows
    every other verb answers with, so rewording a row cannot fork GET from
    DELETE — the two-spellings problem ``_translate_delete`` exists to remove.
    """
    read = translate_error(_not_found())
    deleted = translate_error(_refused_delete(404))

    assert read is not None
    assert deleted is not None
    assert (deleted["error"], deleted["detail"]) == (read["error"], read["detail"])


def test_a_refused_delete_answers_with_the_maps_wording() -> None:
    refused = translate_error(_refused_delete(409))
    row = next((code, detail) for exc, code, detail in _ERROR_MAP if exc is DeletionError)

    assert refused is not None
    assert (refused["error"], refused["detail"]) == row
    assert "409" in refused["cause"]


def test_every_error_code_is_documented() -> None:
    """A code a client cannot look up is a code it cannot branch on.

    The README table is the only place the closed set is written down, so it is tied to
    the map here rather than maintained beside it. The three codes built inline are
    listed by hand because they are raised at their call site rather than mapped from an
    exception type — if a fourth one appears, add it here too.
    """
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    inline = ("file_too_large", "unsupported_media_type", "unsupported_filter_rule")
    codes = {code for _, code, _ in _ERROR_MAP} | set(inline)

    undocumented = sorted(code for code in codes if f"`{code}`" not in readme)

    assert undocumented == []
