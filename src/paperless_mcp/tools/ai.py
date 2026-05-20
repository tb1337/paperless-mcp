"""AI-assisted tools: suggestions, AI suggestions, document chat."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from ..client import get_client
from ..config import Settings


def _dump(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return obj


def register(mcp: FastMCP, settings: Settings) -> None:
    """Register AI tools (read-only)."""

    @mcp.tool()
    async def get_document_suggestions(ctx: Context, document_id: int) -> dict[str, Any]:
        """Return Paperless' classifier-based suggestions for a document.

        Suggested tags, correspondent, document type, etc. based on the trained
        ML classifier. Cheap to call.
        """
        paperless = get_client(ctx)
        suggestions = await paperless.documents.suggestions(document_id)
        return {"document_id": document_id, "suggestions": _dump(suggestions)}

    @mcp.tool()
    async def get_document_ai_suggestions(ctx: Context, document_id: int) -> dict[str, Any]:
        """Return AI-generated suggestions (requires Paperless AI to be configured).

        Uses the LLM backend Paperless is configured against.
        """
        paperless = get_client(ctx)
        suggestions = await paperless.documents.ai_suggestions(document_id)
        return {"document_id": document_id, "ai_suggestions": _dump(suggestions)}

    @mcp.tool()
    async def chat_with_documents(
        ctx: Context, query: str, document_id: int | None = None
    ) -> dict[str, Any]:
        """Run a natural-language question against Paperless' chat endpoint.

        Pass ``document_id`` to scope the conversation to a single document;
        omit it to chat across the whole collection.
        """
        paperless = get_client(ctx)
        response = await paperless.documents.chat(query, document_id=document_id)
        return {"query": query, "document_id": document_id, "response": _dump(response)}
