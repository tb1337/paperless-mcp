"""Suggestion tools: classifier suggestions and (optional) LLM suggestions."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..client import ToolContext, get_client
from ..config import Settings
from ..formatting import safe_dump
from ._helpers import safe_tool


def register(mcp: FastMCP, settings: Settings) -> None:
    """Register suggestion tools (read-only)."""

    @mcp.tool()
    @safe_tool
    async def get_document_suggestions(ctx: ToolContext, document_id: int) -> dict[str, Any]:
        """Return Paperless' classifier-based suggestions for a document.

        Suggested correspondents, tags, document types, storage paths and dates
        from the locally trained classifier — cheap, no LLM involved. The values
        are IDs; resolve them with ``list_tags`` and friends.
        """
        paperless = await get_client(ctx)
        suggestions = await paperless.documents.suggestions(document_id)
        return {"document_id": document_id, "suggestions": safe_dump(suggestions)}

    @mcp.tool()
    @safe_tool
    async def get_document_ai_suggestions(ctx: ToolContext, document_id: int) -> dict[str, Any]:
        """Return LLM-generated suggestions for a document.

        Requires the AI features to be enabled on the Paperless-ngx side
        (``PAPERLESS_AI_ENABLED``); otherwise Paperless answers with an error,
        which is returned as a structured error result. Unlike
        ``get_document_suggestions`` this can also propose *new* tag and
        correspondent names, returned in the ``suggested_*`` lists.
        """
        paperless = await get_client(ctx)
        suggestions = await paperless.documents.ai_suggestions(document_id)
        return {"document_id": document_id, "ai_suggestions": safe_dump(suggestions)}
