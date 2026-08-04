"""Bulk operations on documents."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from pypaperless import PaperlessClient
from pypaperless.models import Document

from ..client import ToolContext, get_client
from ..config import Settings
from ._errors import ToolInputError
from ._registry import register_tools, write_tool
from ._relations import resolve_relation, resolve_tags


async def _resolve_page_count(
    paperless: PaperlessClient, document_id: int, given: int | None
) -> int:
    """Return the document's page count, reading the record when not supplied."""
    if given is not None:
        return given
    # Annotated because the service is generic over its resource, so the call
    # itself types as Any and would leak through the int return.
    document: Document = await paperless.documents(document_id)
    count = document.page_count
    if count is None:
        raise ToolInputError(
            f"Document {document_id} reports no page count — it is not a PDF, or "
            "has not been processed yet. Pass page_count if you know it."
        )
    return count


async def bulk_edit_documents(
    ctx: ToolContext,
    document_ids: list[int],
    correspondent_id: int | None = None,
    correspondent_name: str | None = None,
    document_type_id: int | None = None,
    document_type_name: str | None = None,
    storage_path_id: int | None = None,
    storage_path_name: str | None = None,
    add_tag_ids: list[int] | None = None,
    add_tag_names: list[str] | None = None,
    remove_tag_ids: list[int] | None = None,
    remove_tag_names: list[str] | None = None,
) -> dict[str, Any]:
    """Apply assignments to many documents at once.

    Unlike ``update_document``, tags are *added* and *removed* individually
    rather than replaced wholesale. Every non-null argument triggers its own
    bulk-edit request; they run in order, and if one fails the ``applied``
    list reflects only the operations that already succeeded.

    Correspondent, document type, storage path and both tag lists each take
    names or IDs: pass ``*_name`` / ``*_names`` when the value comes from
    the conversation, ``*_id`` / ``*_ids`` only when you have it verbatim
    from a tool result. Passing both is allowed but they must agree; a
    mismatch is rejected rather than resolved. Names are resolved before the
    first request goes out, so an unknown one costs no half-applied edit.
    """
    if not document_ids:
        raise ToolInputError("document_ids must not be empty")
    paperless = await get_client(ctx)
    correspondent_id = await resolve_relation(
        ctx, field="correspondent", pk=correspondent_id, name=correspondent_name
    )
    document_type_id = await resolve_relation(
        ctx, field="document_type", pk=document_type_id, name=document_type_name
    )
    storage_path_id = await resolve_relation(
        ctx, field="storage_path", pk=storage_path_id, name=storage_path_name
    )
    add_tag_ids = await resolve_tags(
        ctx,
        pks=add_tag_ids,
        names=add_tag_names,
        id_field="add_tag_ids",
        name_field="add_tag_names",
    )
    remove_tag_ids = await resolve_tags(
        ctx,
        pks=remove_tag_ids,
        names=remove_tag_names,
        id_field="remove_tag_ids",
        name_field="remove_tag_names",
    )
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


async def split_document(
    ctx: ToolContext,
    document_id: int,
    page_groups: list[list[int]],
    delete_original: bool = False,
    page_count: int | None = None,
) -> dict[str, Any]:
    """Split one document into several new ones, one per page group.

    ``page_groups`` partitions the document: ``[[1, 2], [3, 4, 5]]`` turns a
    five-page scan into a two-page and a three-page document, in that order.

    Every page must appear exactly once. Paperless keeps only the pages it
    is handed and discards the rest without complaint, and losing sheets
    from an archive is not something to infer from a short list — so a gap
    is refused here instead. To drop pages, call ``delete_document_pages``;
    to do both, split first and delete afterwards.

    The results inherit the source metadata unchanged, so there is no
    "(split 1)" suffix in their titles — rename them afterwards if that
    matters. ``delete_original=True`` moves the source to the trash.
    ``page_count`` skips the read the coverage check needs;
    ``get_document`` already reports it.
    """
    if len(page_groups) < 2:
        raise ToolInputError("Splitting needs at least two page_groups")
    if any(not group for group in page_groups):
        raise ToolInputError("page_groups must not contain an empty group")
    pages = [page for group in page_groups for page in group]
    duplicates = sorted({page for page in pages if pages.count(page) > 1})
    if duplicates:
        raise ToolInputError(f"Pages {duplicates} appear in more than one group")

    paperless = await get_client(ctx)
    total = await _resolve_page_count(paperless, document_id, page_count)
    out_of_range = sorted({page for page in pages if not 1 <= page <= total})
    if out_of_range:
        raise ToolInputError(f"Pages {out_of_range} are out of range for a {total} page document")
    missing = sorted(set(range(1, total + 1)) - set(pages))
    if missing:
        raise ToolInputError(
            f"page_groups must cover all {total} pages; {missing} would be "
            "discarded. Use delete_document_pages to remove pages on purpose."
        )

    await paperless.documents.bulk_edit.split(
        document_id, page_groups, delete_originals=delete_original
    )
    return {
        "document_id": document_id,
        "page_groups": page_groups,
        "documents_created": len(page_groups),
        "delete_original": delete_original,
    }


async def delete_document_pages(
    ctx: ToolContext,
    document_id: int,
    pages: list[int],
    page_count: int | None = None,
) -> dict[str, Any]:
    """Remove pages from a document, leaving a shorter new version of it.

    ``pages`` are 1-based numbers in the document as it stands *now*.
    Repeating the call does not repeat the edit: the survivors are
    renumbered afterwards, so a second ``[2, 4]`` removes what have since
    become pages 2 and 4 — different sheets. Read the document again before
    retrying.

    Not atomic. Paperless takes the pages to keep, so the complement is
    computed from a page count read in an earlier request; a document that
    gains a version in between loses the wrong pages. ``page_count`` skips
    that read — ``get_document`` reports it — but does not close the window.
    """
    if not pages:
        raise ToolInputError("pages must not be empty")
    paperless = await get_client(ctx)
    await paperless.documents.bulk_edit.delete_pages(document_id, pages, page_count=page_count)
    return {"document_id": document_id, "pages_removed": sorted(set(pages))}


def register(mcp: MCPServer, settings: Settings) -> None:
    """Register the bulk document tools this deployment exposes."""
    register_tools(
        mcp,
        settings,
        (
            write_tool(bulk_edit_documents, destructive=True, idempotent=True),
            write_tool(bulk_reprocess_documents, destructive=True, idempotent=False),
            write_tool(bulk_merge_documents, destructive=True, idempotent=False),
            write_tool(bulk_rotate_documents, destructive=True, idempotent=False),
            write_tool(split_document, destructive=True, idempotent=False),
            write_tool(delete_document_pages, destructive=True, idempotent=False),
        ),
    )
