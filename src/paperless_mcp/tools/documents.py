"""Document read/write/delete tools."""

from __future__ import annotations

import base64
import datetime as dt
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from ..client import get_client, get_settings
from ..config import Settings
from ..formatting import format_document, format_document_detail, format_note


def _build_doc_filters(
    *,
    title_contains: str | None,
    content_contains: str | None,
    tags_all: list[int] | None,
    tags_any: list[int] | None,
    tags_none: list[int] | None,
    correspondent_id: int | None,
    document_type_id: int | None,
    storage_path_id: int | None,
    archive_serial_number: int | None,
    is_in_inbox: bool | None,
    is_tagged: bool | None,
    mime_type: str | None,
    created_after: str | None,
    created_before: str | None,
    added_after: str | None,
    added_before: str | None,
) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    if title_contains:
        filters["title__icontains"] = title_contains
    if content_contains:
        filters["content__icontains"] = content_contains
    if tags_all:
        filters["tags__id__all"] = tags_all
    if tags_any:
        filters["tags__id__in"] = tags_any
    if tags_none:
        filters["tags__id__none"] = tags_none
    if correspondent_id is not None:
        filters["correspondent__id"] = correspondent_id
    if document_type_id is not None:
        filters["document_type__id"] = document_type_id
    if storage_path_id is not None:
        filters["storage_path__id"] = storage_path_id
    if archive_serial_number is not None:
        filters["archive_serial_number"] = archive_serial_number
    if is_in_inbox is not None:
        filters["is_in_inbox"] = is_in_inbox
    if is_tagged is not None:
        filters["is_tagged"] = is_tagged
    if mime_type:
        filters["mime_type"] = mime_type
    if created_after:
        filters["created__date__gte"] = created_after
    if created_before:
        filters["created__date__lte"] = created_before
    if added_after:
        filters["added__date__gte"] = added_after
    if added_before:
        filters["added__date__lte"] = added_before
    return filters


def register(mcp: FastMCP, settings: Settings) -> None:
    """Register document tools according to the configured visibility flags."""
    # -- READ --------------------------------------------------------------

    @mcp.tool()
    async def search_documents(
        ctx: Context,
        query: str | None = None,
        title_contains: str | None = None,
        content_contains: str | None = None,
        tags_all: list[int] | None = None,
        tags_any: list[int] | None = None,
        tags_none: list[int] | None = None,
        correspondent_id: int | None = None,
        document_type_id: int | None = None,
        storage_path_id: int | None = None,
        archive_serial_number: int | None = None,
        is_in_inbox: bool | None = None,
        is_tagged: bool | None = None,
        mime_type: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        added_after: str | None = None,
        added_before: str | None = None,
        limit: int = 25,
    ) -> dict[str, Any]:
        """Search documents in Paperless-ngx.

        Pass ``query`` for a full-text search; pass any of the other arguments to
        filter server-side. Date arguments are ISO dates (``YYYY-MM-DD``). Tag
        IDs are integers. Returns up to ``limit`` documents.
        """
        paperless = get_client(ctx)
        filters = _build_doc_filters(
            title_contains=title_contains,
            content_contains=content_contains,
            tags_all=tags_all,
            tags_any=tags_any,
            tags_none=tags_none,
            correspondent_id=correspondent_id,
            document_type_id=document_type_id,
            storage_path_id=storage_path_id,
            archive_serial_number=archive_serial_number,
            is_in_inbox=is_in_inbox,
            is_tagged=is_tagged,
            mime_type=mime_type,
            created_after=created_after,
            created_before=created_before,
            added_after=added_after,
            added_before=added_before,
        )
        if query:
            filters["query"] = query

        results: list[dict[str, Any]] = []
        async for doc in paperless.documents.filter(**filters):
            results.append(format_document(doc))
            if len(results) >= limit:
                break
        return {"documents": results, "returned": len(results), "limit": limit}

    @mcp.tool()
    async def get_document(ctx: Context, document_id: int) -> dict[str, Any]:
        """Fetch a single document including content, notes, and custom fields."""
        paperless = get_client(ctx)
        doc = await paperless.documents(document_id)
        return format_document_detail(doc)

    @mcp.tool()
    async def get_document_content(ctx: Context, document_id: int) -> dict[str, Any]:
        """Return only the OCR'd text content of a document."""
        paperless = get_client(ctx)
        doc = await paperless.documents(document_id)
        return {"id": doc.id, "content": getattr(doc, "content", None)}

    @mcp.tool()
    async def get_document_metadata(ctx: Context, document_id: int) -> dict[str, Any]:
        """Return detailed metadata (file info, checksums, original filename, ...)."""
        paperless = get_client(ctx)
        meta = await paperless.documents.metadata(document_id)
        if hasattr(meta, "model_dump"):
            return meta.model_dump(mode="json")
        return dict(meta)

    @mcp.tool()
    async def get_document_notes(ctx: Context, document_id: int) -> dict[str, Any]:
        """List all notes attached to a document."""
        paperless = get_client(ctx)
        notes = await paperless.documents.notes(document_id)
        return {"document_id": document_id, "notes": [format_note(n) for n in notes]}

    @mcp.tool()
    async def get_document_history(ctx: Context, document_id: int) -> dict[str, Any]:
        """Return the audit history of a document."""
        paperless = get_client(ctx)
        history = await paperless.documents.history(document_id)
        entries = []
        for h in history:
            entries.append(h.model_dump(mode="json") if hasattr(h, "model_dump") else dict(h))
        return {"document_id": document_id, "history": entries}

    @mcp.tool()
    async def find_similar_documents(
        ctx: Context, document_id: int, limit: int = 10
    ) -> dict[str, Any]:
        """Find documents semantically similar to the given document."""
        paperless = get_client(ctx)
        results: list[dict[str, Any]] = []
        async for doc in paperless.documents.more_like(document_id):
            results.append(format_document(doc))
            if len(results) >= limit:
                break
        return {"reference": document_id, "documents": results}

    @mcp.tool()
    async def download_document(
        ctx: Context, document_id: int, original: bool = False
    ) -> dict[str, Any]:
        """Return the document file as base64.

        Set ``original=True`` to retrieve the originally consumed file instead
        of the archived (typically OCR'd PDF) version. Returns an error if the
        file exceeds ``PAPERLESS_MCP_MAX_FILE_BYTES``.
        """
        paperless = get_client(ctx)
        cfg = get_settings(ctx)
        downloaded = await paperless.documents.download(document_id, original=original)
        content: bytes = getattr(downloaded, "content", b"")
        size = len(content)
        if size > cfg.max_file_bytes:
            return {
                "error": "file_too_large",
                "size_bytes": size,
                "max_bytes": cfg.max_file_bytes,
                "hint": "Increase PAPERLESS_MCP_MAX_FILE_BYTES or fetch via the Paperless UI.",
            }
        return {
            "document_id": document_id,
            "filename": getattr(downloaded, "disposition_filename", None)
            or getattr(downloaded, "filename", None),
            "content_type": getattr(downloaded, "content_type", None),
            "size_bytes": size,
            "content_base64": base64.b64encode(content).decode("ascii"),
        }

    @mcp.tool()
    async def get_document_thumbnail(ctx: Context, document_id: int) -> dict[str, Any]:
        """Return the document's thumbnail image as base64."""
        paperless = get_client(ctx)
        cfg = get_settings(ctx)
        thumb = await paperless.documents.thumbnail(document_id)
        content: bytes = getattr(thumb, "content", b"")
        size = len(content)
        if size > cfg.max_file_bytes:
            return {"error": "file_too_large", "size_bytes": size, "max_bytes": cfg.max_file_bytes}
        return {
            "document_id": document_id,
            "content_type": getattr(thumb, "content_type", "image/webp"),
            "size_bytes": size,
            "content_base64": base64.b64encode(content).decode("ascii"),
        }

    # -- WRITE -------------------------------------------------------------

    if settings.expose_writes:

        @mcp.tool()
        async def upload_document(
            ctx: Context,
            filename: str,
            content_base64: str,
            title: str | None = None,
            correspondent_id: int | None = None,
            document_type_id: int | None = None,
            storage_path_id: int | None = None,
            tag_ids: list[int] | None = None,
            archive_serial_number: int | None = None,
            created: str | None = None,
        ) -> dict[str, Any]:
            """Upload a new document.

            ``content_base64`` must be the raw file bytes, base64-encoded.
            ``created`` is an optional ISO datetime; Paperless will default to
            the consumption time if omitted. Returns the task UUID that tracks
            consumption.
            """
            paperless = get_client(ctx)
            try:
                content = base64.b64decode(content_base64, validate=True)
            except Exception as exc:
                return {"error": "invalid_base64", "detail": str(exc)}

            draft = paperless.documents.create()
            draft.document = content
            draft.filename = filename
            if title is not None:
                draft.title = title
            if correspondent_id is not None:
                draft.correspondent = correspondent_id
            if document_type_id is not None:
                draft.document_type = document_type_id
            if storage_path_id is not None:
                draft.storage_path = storage_path_id
            if tag_ids is not None:
                draft.tags = tag_ids
            if archive_serial_number is not None:
                draft.archive_serial_number = archive_serial_number
            if created is not None:
                draft.created = dt.datetime.fromisoformat(created)

            task_uuid = await paperless.documents.save(draft)
            return {"task_uuid": task_uuid, "filename": filename}

        @mcp.tool()
        async def update_document(
            ctx: Context,
            document_id: int,
            title: str | None = None,
            correspondent_id: int | None = None,
            document_type_id: int | None = None,
            storage_path_id: int | None = None,
            tag_ids: list[int] | None = None,
            archive_serial_number: int | None = None,
            content: str | None = None,
            created: str | None = None,
        ) -> dict[str, Any]:
            """Update fields on an existing document.

            Only the fields you pass will be modified. To clear an FK pass
            ``-1``; the call will be translated to ``None``.
            """
            paperless = get_client(ctx)
            doc = await paperless.documents(document_id)
            if title is not None:
                doc.title = title
            if correspondent_id is not None:
                doc.correspondent = None if correspondent_id == -1 else correspondent_id
            if document_type_id is not None:
                doc.document_type = None if document_type_id == -1 else document_type_id
            if storage_path_id is not None:
                doc.storage_path = None if storage_path_id == -1 else storage_path_id
            if tag_ids is not None:
                doc.tags = tag_ids
            if archive_serial_number is not None:
                doc.archive_serial_number = (
                    None if archive_serial_number == -1 else archive_serial_number
                )
            if content is not None:
                doc.content = content
            if created is not None:
                doc.created = dt.datetime.fromisoformat(created)
            await paperless.documents.update(doc)
            return format_document(doc)

        @mcp.tool()
        async def add_document_note(ctx: Context, document_id: int, note: str) -> dict[str, Any]:
            """Add a free-text note to a document."""
            paperless = get_client(ctx)
            draft = paperless.documents.notes.create(document_id)
            draft.note = note
            note_id = await paperless.documents.notes.save(draft)
            return {"document_id": document_id, "note_id": note_id}

    # -- DELETE ------------------------------------------------------------

    if settings.expose_deletes:

        @mcp.tool()
        async def delete_document(ctx: Context, document_id: int) -> dict[str, Any]:
            """Move a document to the trash (recoverable via restore_documents)."""
            paperless = get_client(ctx)
            doc = await paperless.documents(document_id)
            await paperless.documents.delete(doc)
            return {"document_id": document_id, "deleted": True}

        @mcp.tool()
        async def delete_document_note(
            ctx: Context, document_id: int, note_id: int
        ) -> dict[str, Any]:
            """Delete a single note from a document."""
            paperless = get_client(ctx)
            await paperless.documents.notes.delete(note_id, document_pk=document_id)
            return {"document_id": document_id, "note_id": note_id, "deleted": True}
