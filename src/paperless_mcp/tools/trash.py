"""Trash management tools."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from ..client import get_client
from ..config import Settings
from ..formatting import format_document


def register(mcp: FastMCP, settings: Settings) -> None:
    """Register trash tools."""

    @mcp.tool()
    async def list_trash(ctx: Context, limit: int = 100) -> dict[str, Any]:
        """List documents currently in the trash."""
        paperless = get_client(ctx)
        items = []
        async for doc in paperless.trash.filter():
            items.append(format_document(doc))
            if len(items) >= limit:
                break
        return {"trashed": items, "returned": len(items)}

    if settings.expose_writes:

        @mcp.tool()
        async def restore_documents(ctx: Context, document_ids: list[int]) -> dict[str, Any]:
            """Restore one or more trashed documents."""
            paperless = get_client(ctx)
            await paperless.trash.restore(document_ids)
            return {"restored": document_ids}

    if settings.expose_deletes:

        @mcp.tool()
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
