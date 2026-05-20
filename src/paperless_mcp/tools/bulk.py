"""Bulk operations on documents."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from ..client import get_client
from ..config import Settings
from ._helpers import safe_tool


def register(mcp: FastMCP, settings: Settings) -> None:
    """Register bulk document tools."""
    if not settings.expose_writes:
        return

    @mcp.tool()
    @safe_tool
    async def bulk_edit_documents(
        ctx: Context,
        document_ids: list[int],
        correspondent_id: int | None = None,
        document_type_id: int | None = None,
        storage_path_id: int | None = None,
        add_tag_ids: list[int] | None = None,
        remove_tag_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """Apply assignments to many documents at once.

        Every non-null argument triggers its own bulk-edit operation against the
        Paperless backend. Operations run sequentially; if one fails, the
        ``applied`` list reflects only the ones that succeeded.
        """
        paperless = get_client(ctx)
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
        return {"document_ids": document_ids, "applied": applied}

    @mcp.tool()
    @safe_tool
    async def bulk_reprocess_documents(ctx: Context, document_ids: list[int]) -> dict[str, Any]:
        """Re-run OCR / metadata parsing on the given documents."""
        paperless = get_client(ctx)
        await paperless.documents.bulk_edit.reprocess(document_ids)
        return {"document_ids": document_ids, "reprocessing": True}

    @mcp.tool()
    @safe_tool
    async def bulk_merge_documents(
        ctx: Context,
        document_ids: list[int],
        metadata_from_id: int | None = None,
        delete_originals: bool = False,
    ) -> dict[str, Any]:
        """Merge several documents into a single new one.

        ``metadata_from_id`` selects which source document supplies the metadata
        for the merged result. When ``delete_originals`` is true the source
        documents are moved to the trash after merging.
        """
        paperless = get_client(ctx)
        await paperless.documents.bulk_edit.merge(
            document_ids,
            metadata_document_id=metadata_from_id,
            delete_originals=delete_originals,
        )
        return {"merged": document_ids, "metadata_from_id": metadata_from_id}
