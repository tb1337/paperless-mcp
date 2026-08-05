"""Suggestion tools: classifier suggestions and (optional) LLM suggestions."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from ..client import ToolContext, get_client, get_names
from ..config import Settings
from ..formatting import enrich_suggestions, safe_dump
from ._registry import read_tool, register_tools


async def get_document_suggestions(ctx: ToolContext, document_id: int) -> dict[str, Any]:
    """Return Paperless' classifier-based suggestions for a document.

    Suggested correspondents, tags, document types, storage paths and dates
    from the locally trained classifier — cheap, no LLM involved. Each ID
    list is accompanied by a ``*_names`` list holding the resolved names in
    the same order.

    The document is identified by ``document_id`` at the top level. Paperless' own
    payload under ``suggestions`` repeats it as ``id``; that is its field, passed
    through unaltered, and it holds the same number.
    """
    paperless = await get_client(ctx)
    names = await get_names(ctx)
    dumped = safe_dump(await paperless.documents.suggestions(document_id))
    # Not dump_mapping: a payload that is not an object passes through as it
    # came rather than being wrapped under an invented key.
    enriched = enrich_suggestions(dumped, names) if isinstance(dumped, dict) else dumped
    return {"document_id": document_id, "suggestions": enriched}


async def get_document_ai_suggestions(ctx: ToolContext, document_id: int) -> dict[str, Any]:
    """Return LLM-generated suggestions for a document.

    Requires the AI features to be enabled on the Paperless-ngx side
    (``PAPERLESS_AI_ENABLED``); otherwise Paperless answers with an error,
    which is returned as a structured error result. Unlike
    ``get_document_suggestions`` this can also propose *new* tag and
    correspondent names, returned in the ``suggested_*`` lists. The
    ``*_names`` lists hold the names of the *existing* objects the ID lists
    point at.
    """
    paperless = await get_client(ctx)
    names = await get_names(ctx)
    dumped = safe_dump(await paperless.documents.ai_suggestions(document_id))
    enriched = enrich_suggestions(dumped, names) if isinstance(dumped, dict) else dumped
    return {"document_id": document_id, "ai_suggestions": enriched}


def register(mcp: MCPServer, settings: Settings) -> None:
    """Register the suggestion tools this deployment exposes."""
    register_tools(
        mcp,
        settings,
        (
            read_tool(get_document_suggestions),
            read_tool(get_document_ai_suggestions),
        ),
    )
