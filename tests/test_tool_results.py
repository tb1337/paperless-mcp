"""What a client actually receives, per return shape.

Every other test module reaches past the server and calls the tool function
directly, which is right for testing a tool's logic but skips the two layers a
real client sits behind: pydantic validating the arguments against the published
JSON schema, and the SDK converting the return value into ``CallToolResult``.

This module drives that path. It exists mainly for one shape:
``get_document_thumbnail`` is annotated ``-> Image`` because the SDK refuses to
build an output schema for a union containing a content type, which is why
``ToolResultError`` exists at all. Nothing verified that the workaround holds
end to end.
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


async def test_a_paginated_envelope_arrives_as_structured_output(make_paperless: Any) -> None:
    """A model reads `structured_content`; the text block is the same JSON."""
    paperless = make_paperless()
    paperless.documents.filter_results = [document(1, "Rechnung")]
    mcp = build_mcp(make_settings(), paperless)

    result = await invoke_tool(mcp, "search_documents", limit=2)

    assert result.is_error is False
    assert result.structured_content is not None
    assert set(result.structured_content) == {
        "documents",
        "returned",
        "offset",
        "limit",
        "total",
        "has_more",
    }
    assert result.structured_content["documents"][0]["title"] == "Rechnung"
    # The unstructured half has to carry the same payload, for a client that
    # reads only text.
    assert parse_tool_result(result) == result.structured_content


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
    # No output schema is generated for a bare content type, so there is nothing
    # structured to send - and that is the reason ToolResultError exists.
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
    assert result.structured_content == {
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
    assert result.structured_content is not None
    assert result.structured_content["error"] == "invalid_argument"
