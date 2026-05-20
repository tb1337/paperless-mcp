"""Trash management tools."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from ..client import get_client
from ..config import Settings
from ..formatting import format_document
from ._helpers import collect, safe_tool


def register(mcp: FastMCP, settings: Settings) -> None:
    """Register trash tools."""

    @mcp.tool()
    @safe_tool
    async def list_trash(ctx: Context, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        """List documents currently in the trash."""
        paperless = get_client(ctx)
        items, has_more = await collect(paperless.trash.filter(), offset=offset, limit=limit)
        return {
            "trashed": [format_document(d) for d in items],
            "returned": len(items),
            "offset": offset,
            "limit": limit,
            "has_more": has_more,
        }

    if settings.expose_writes:

        @mcp.tool()
        @safe_tool
        async def restore_documents(ctx: Context, document_ids: list[int]) -> dict[str, Any]:
            """Restore one or more trashed documents."""
            paperless = get_client(ctx)
            await paperless.trash.restore(document_ids)
            return {"restored": document_ids}

    if settings.expose_deletes:

        @mcp.tool()
        @safe_tool
        async def empty_trash(
            ctx: Context, document_ids: list[int] | None = None
        ) -> dict[str, Any]:
            """Permanently delete trashed documents.

            With no ``document_ids`` the entire trash is emptied. Otherwise only
            the listed documents are purged.
            """
            paperless = get_client(ctx)
            await paperless.trash.empty(document_ids or [])
            return {"purged": document_ids if document_ids else "all"}
