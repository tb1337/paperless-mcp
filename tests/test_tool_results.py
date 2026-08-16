"""What a client actually receives, per return shape.

Every other test module reaches past the server and calls the tool function
directly, which is right for testing a tool's logic but skips the two layers a
real client sits behind: pydantic validating the arguments against the published
JSON schema, and the SDK converting the return value into ``CallToolResult``.

This module drives that path. Two shapes are the reason it exists.
``get_document_thumbnail`` is annotated ``-> Image``, which is why
``ToolResultError`` exists at all, and nothing else verifies that the workaround
holds end to end. And every result leaves as exactly one text block: the SDK
would otherwise send the same payload twice, once as text and once as
``structuredContent``, which is what ``register_tools`` turns off.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ImageContent, TextContent
from pypaperless.exceptions import ItemNotFoundError

from tests.conftest import (
    build_mcp,
    document,
    invoke_tool,
    make_settings,
    parse_tool_result,
    returns,
)


async def test_a_paginated_envelope_arrives_once_as_one_text_block(make_paperless: Any) -> None:
    """The whole envelope reaches the model, and it reaches it exactly once."""
    paperless = make_paperless()
    paperless.documents.filter_results = [document(1, "Rechnung")]
    mcp = build_mcp(make_settings(), paperless)

    result = await invoke_tool(mcp, "search_documents", limit=2)

    assert result.is_error is False
    assert [type(block) for block in result.content] == [TextContent]
    payload = parse_tool_result(result)
    assert set(payload) == {"documents", "returned", "offset", "limit", "total", "has_more"}
    assert payload["documents"][0]["title"] == "Rechnung"
    # The duplicate half. Every byte above would otherwise go out a second time.
    assert result.structured_content is None


async def test_a_result_carries_no_indentation(make_paperless: Any) -> None:
    """The SDK indents with two spaces; nothing reads that and every byte is paid for."""
    paperless = make_paperless()
    paperless.documents.filter_results = [document(1, "Rechnung")]
    mcp = build_mcp(make_settings(), paperless)

    result = await invoke_tool(mcp, "search_documents", limit=2)

    text = result.content[0]
    assert isinstance(text, TextContent)
    assert "\n" not in text.text
    assert '{"documents":[{"id":1,' in text.text


async def test_a_result_keeps_non_ascii_unescaped(make_paperless: Any) -> None:
    """An escaped umlaut is six characters where one would do.

    Worth pinning rather than assuming: this archive's titles are German, and the
    obvious stdlib serializer escapes by default. The saving would go quietly.
    """
    paperless = make_paperless()
    paperless.documents.filter_results = [document(1, "Grundstücksübertragung")]
    mcp = build_mcp(make_settings(), paperless)

    result = await invoke_tool(mcp, "search_documents", limit=2)

    text = result.content[0]
    assert isinstance(text, TextContent)
    assert "Grundstücksübertragung" in text.text
    assert "\\u" not in text.text


async def test_a_thumbnail_arrives_as_image_content(make_paperless: Any) -> None:
    """The point of annotating it `-> Image`: the model can look at the page."""
    paperless = make_paperless()
    paperless.documents.thumbnail = returns(
        SimpleNamespace(content=b"\x89PNG\r\n\x1a\n", content_type="image/png")
    )
    mcp = build_mcp(make_settings(), paperless)

    result = await invoke_tool(mcp, "get_document_thumbnail", document_id=1)

    assert result.is_error is False
    assert [type(block) for block in result.content] == [ImageContent]
    image = result.content[0]
    assert isinstance(image, ImageContent)
    assert image.mime_type == "image/png"
    assert result.structured_content is None


async def test_an_image_tool_can_still_report_a_structured_error(make_paperless: Any) -> None:
    """`ToolResultError` is the only way out of a tool with no dict in its return type."""
    paperless = make_paperless()
    paperless.documents.thumbnail = returns(
        SimpleNamespace(content=b"not an image", content_type="text/plain")
    )
    mcp = build_mcp(make_settings(), paperless)

    result = await invoke_tool(mcp, "get_document_thumbnail", document_id=1)

    # Not a protocol-level failure: the model gets something it can act on.
    assert result.is_error is False
    assert [type(block) for block in result.content] == [TextContent]
    payload = parse_tool_result(result)
    assert payload["error"] == "unsupported_media_type"
    assert "text/plain" in payload["detail"]


async def test_a_paperless_failure_arrives_as_a_structured_error(make_paperless: Any) -> None:
    """`safe_tool`'s whole purpose, seen from the client side."""
    paperless = make_paperless()
    paperless.documents.get_raises = ItemNotFoundError("nope")
    mcp = build_mcp(make_settings(), paperless)

    result = await invoke_tool(mcp, "get_document", document_id=9)

    assert result.is_error is False
    assert parse_tool_result(result) == {
        "error": "not_found",
        "detail": "The requested object does not exist.",
        "cause": "nope",
    }


async def test_a_bad_argument_never_reaches_the_tool(make_paperless: Any) -> None:
    """The schema rejects it before `safe_tool` can turn it into a result.

    Worth pinning: it means a constrained argument typed as an enum trades the
    `{"error": "invalid_argument"}` shape for a protocol-level error, and the
    lowlevel request handler one layer up is what a client sees it through.
    """
    mcp = build_mcp(make_settings(), make_paperless())

    with pytest.raises(ToolError, match="Input should be a valid integer"):
        await invoke_tool(mcp, "search_documents", limit="viele")


async def test_a_rejected_window_still_arrives_as_a_result(make_paperless: Any) -> None:
    """A negative offset satisfies the schema, so `safe_tool` gets to answer.

    This is the other half of the previous test, and the reason the tools validate
    ranges themselves instead of leaning on the schema for everything.
    """
    mcp = build_mcp(make_settings(), make_paperless())

    result = await invoke_tool(mcp, "search_documents", offset=-1)

    assert result.is_error is False
    assert parse_tool_result(result)["error"] == "invalid_argument"


async def test_a_window_past_the_ceiling_arrives_as_a_result(make_paperless: Any) -> None:
    """The other half of the same rule: too wide is a readable error, not a failure."""
    mcp = build_mcp(make_settings(), make_paperless())

    result = await invoke_tool(mcp, "search_documents", limit=500)

    assert result.is_error is False
    payload = parse_tool_result(result)
    assert payload["error"] == "invalid_argument"
    assert "at most 100" in payload["cause"]
