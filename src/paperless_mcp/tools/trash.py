"""Trash management tools."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from ..client import ToolContext, get_client
from ..config import Settings
from ._arguments import DOCUMENT_PROJECTIONS, DocumentFields
from ._errors import ToolInputError
from ._paging import named_page
from ._registry import delete_tool, read_tool, register_tools, write_tool


async def list_trash(
    ctx: ToolContext,
    fields: DocumentFields = "compact",
    offset: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    """List documents currently in the trash.

    In whatever order Paperless sends them: ``/api/trash/`` declares no ordering
    parameter and no filters, so the order cannot be asked for and is not
    ``deleted_at`` — on a real archive it follows the document ID. Read each item's
    ``deleted_at`` when the age matters rather than trusting the position; it is
    carried under either ``fields``, which otherwise works as it does on
    ``search_documents``.
    """
    return await named_page(
        ctx,
        "trashed",
        lambda paperless: paperless.trash,
        DOCUMENT_PROJECTIONS[fields],
        offset=offset,
        limit=limit,
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

    With ``document_ids`` *omitted* the entire trash is emptied; otherwise only
    the listed documents are purged, and an empty list is refused rather than
    read as "everything". ``purged_all`` says which of the two happened, so
    ``purged`` is always a list — an empty one when the whole trash went.
    """
    # An explicit [] must not fall through to the purge-everything request: it
    # is what a model passes when the selection it built matched nothing.
    if document_ids is not None and not document_ids:
        raise ToolInputError(
            "document_ids must not be empty. Omit it entirely to empty the whole trash."
        )
    paperless = await get_client(ctx)
    await paperless.trash.empty(document_ids)
    return {"purged": document_ids or [], "purged_all": document_ids is None}


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
