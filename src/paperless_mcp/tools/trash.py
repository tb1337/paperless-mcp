"""Trash management tools."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..client import ToolContext, get_client
from ..config import Settings
from ..formatting import format_document
from ._helpers import ToolInputError, page_result, paginate, safe_tool


def register(mcp: FastMCP, settings: Settings) -> None:
    """Register trash tools."""

    @mcp.tool()
    @safe_tool
    async def list_trash(ctx: ToolContext, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        """List documents currently in the trash, newest deletion first."""
        paperless = await get_client(ctx)
        items, total = await paginate(paperless.trash, offset=offset, limit=limit)
        return page_result(
            "trashed",
            items,
            offset=offset,
            limit=limit,
            total=total,
            formatter=format_document,
        )

    if settings.expose_writes:

        @mcp.tool()
        @safe_tool
        async def restore_documents(ctx: ToolContext, document_ids: list[int]) -> dict[str, Any]:
            """Restore one or more trashed documents."""
            if not document_ids:
                raise ToolInputError("document_ids must not be empty")
            paperless = await get_client(ctx)
            await paperless.trash.restore(document_ids)
            return {"restored": document_ids}

    if settings.expose_deletes:

        @mcp.tool()
        @safe_tool
        async def empty_trash(
            ctx: ToolContext, document_ids: list[int] | None = None
        ) -> dict[str, Any]:
            """Permanently delete trashed documents. This cannot be undone.

            With no ``document_ids`` the *entire* trash is emptied; otherwise
            only the listed documents are purged.
            """
            paperless = await get_client(ctx)
            # Passing an empty list would ask Paperless to purge nothing at all,
            # so an omitted argument has to stay None to mean "everything".
            await paperless.trash.empty(document_ids or None)
            return {"purged": document_ids if document_ids else "all"}
