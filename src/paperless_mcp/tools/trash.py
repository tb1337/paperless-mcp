"""Trash management tools."""

from __future__ import annotations

from functools import partial
from typing import Any

from mcp.server.mcpserver import MCPServer

from ..client import ToolContext, get_client, get_names
from ..config import Settings
from ..formatting import format_document
from ._errors import ToolInputError
from ._paging import page_result, paginate
from ._registry import delete_tool, read_tool, register_tools, write_tool


async def list_trash(ctx: ToolContext, offset: int = 0, limit: int = 50) -> dict[str, Any]:
    """List documents currently in the trash, newest deletion first."""
    paperless = await get_client(ctx)
    names = await get_names(ctx)
    items, total = await paginate(paperless.trash, offset=offset, limit=limit)
    return page_result(
        "trashed",
        items,
        offset=offset,
        limit=limit,
        total=total,
        formatter=partial(format_document, names=names),
    )


async def restore_documents(ctx: ToolContext, document_ids: list[int]) -> dict[str, Any]:
    """Restore one or more trashed documents."""
    if not document_ids:
        raise ToolInputError("document_ids must not be empty")
    paperless = await get_client(ctx)
    await paperless.trash.restore(document_ids)
    return {"restored": document_ids}


async def empty_trash(ctx: ToolContext, document_ids: list[int] | None = None) -> dict[str, Any]:
    """Permanently delete trashed documents. This cannot be undone.

    With no ``document_ids`` the *entire* trash is emptied; otherwise only the
    listed documents are purged. ``purged_all`` says which of the two happened, so
    ``purged`` is always a list — an empty one when the whole trash went.
    """
    paperless = await get_client(ctx)
    # Passing an empty list would ask Paperless to purge nothing at all,
    # so an omitted argument has to stay None to mean "everything".
    await paperless.trash.empty(document_ids or None)
    return {"purged": document_ids or [], "purged_all": not document_ids}


def register(mcp: MCPServer, settings: Settings) -> None:
    """Register the trash tools this deployment exposes."""
    register_tools(
        mcp,
        settings,
        (
            read_tool(list_trash),
            write_tool(restore_documents, destructive=False, idempotent=True),
            delete_tool(empty_trash),
        ),
    )
