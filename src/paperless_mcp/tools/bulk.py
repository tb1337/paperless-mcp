"""Bulk operations on documents."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from ..client import ToolContext, get_client
from ..config import Settings
from ._helpers import ToolInputError, safe_tool


def register(mcp: MCPServer, settings: Settings) -> None:
    """Register bulk document tools."""
    if not settings.expose_writes:
        return

    @mcp.tool()
    @safe_tool
    async def bulk_edit_documents(
        ctx: ToolContext,
        document_ids: list[int],
        correspondent_id: int | None = None,
        document_type_id: int | None = None,
        storage_path_id: int | None = None,
        add_tag_ids: list[int] | None = None,
        remove_tag_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """Apply assignments to many documents at once.

        Unlike ``update_document``, tags are *added* and *removed* individually
        rather than replaced wholesale. Every non-null argument triggers its own
        bulk-edit request; they run in order, and if one fails the ``applied``
        list reflects only the operations that already succeeded.
        """
        if not document_ids:
            raise ToolInputError("document_ids must not be empty")
        paperless = await get_client(ctx)
        applied: list[str] = []
        if correspondent_id is not None:
            await paperless.documents.bulk_edit.set_correspondent(document_ids, correspondent_id)
            applied.append("correspondent")
        if document_type_id is not None:
            await paperless.documents.bulk_edit.set_document_type(document_ids, document_type_id)
            applied.append("document_type")
        if storage_path_id is not None:
            await paperless.documents.bulk_edit.set_storage_path(document_ids, storage_path_id)
            applied.append("storage_path")
        if add_tag_ids or remove_tag_ids:
            await paperless.documents.bulk_edit.modify_tags(
                document_ids,
                add_tags=add_tag_ids or [],
                remove_tags=remove_tag_ids or [],
            )
            applied.append("tags")
        if not applied:
            raise ToolInputError("Nothing to do: pass at least one field to change.")
        return {"document_ids": document_ids, "applied": applied}

    @mcp.tool()
    @safe_tool
    async def bulk_reprocess_documents(ctx: ToolContext, document_ids: list[int]) -> dict[str, Any]:
        """Re-run OCR and metadata parsing on the given documents.

        Runs asynchronously in the Paperless task queue; follow it with
        ``list_active_tasks``.
        """
        if not document_ids:
            raise ToolInputError("document_ids must not be empty")
        paperless = await get_client(ctx)
        await paperless.documents.bulk_edit.reprocess(document_ids)
        return {"document_ids": document_ids, "reprocessing": True}

    @mcp.tool()
    @safe_tool
    async def bulk_merge_documents(
        ctx: ToolContext,
        document_ids: list[int],
        metadata_from_id: int | None = None,
        delete_originals: bool = False,
    ) -> dict[str, Any]:
        """Merge several documents into a single new one.

        ``metadata_from_id`` selects which source document supplies the metadata
        for the merged result. With ``delete_originals=True`` the sources move
        to the trash after merging.
        """
        if len(document_ids) < 2:
            raise ToolInputError("Merging needs at least two document_ids")
        if metadata_from_id is not None and metadata_from_id not in document_ids:
            raise ToolInputError("metadata_from_id must be one of document_ids")
        paperless = await get_client(ctx)
        await paperless.documents.bulk_edit.merge(
            document_ids,
            metadata_document_id=metadata_from_id,
            delete_originals=delete_originals,
        )
        return {
            "merged": document_ids,
            "metadata_from_id": metadata_from_id,
            "delete_originals": delete_originals,
        }

    @mcp.tool()
    @safe_tool
    async def bulk_rotate_documents(
        ctx: ToolContext, document_ids: list[int], degrees: int
    ) -> dict[str, Any]:
        """Rotate documents by 90, 180 or 270 degrees clockwise."""
        if not document_ids:
            raise ToolInputError("document_ids must not be empty")
        if degrees not in {90, 180, 270}:
            raise ToolInputError(f"degrees must be 90, 180 or 270, got {degrees}")
        paperless = await get_client(ctx)
        await paperless.documents.bulk_edit.rotate(document_ids, degrees)
        return {"document_ids": document_ids, "degrees": degrees}
