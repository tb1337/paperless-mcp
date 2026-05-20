"""Document read/write/delete tools."""

from __future__ import annotations

import base64
import binascii
import datetime as dt
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from ..client import get_client, get_settings
from ..config import Settings
from ..formatting import format_document, format_document_detail, format_note, safe_dump
from ._helpers import collect, safe_tool

# Updatable fields that accept a "clear" instruction via the clear_fields list.
_CLEARABLE_FIELDS: frozenset[str] = frozenset(
    {"correspondent", "document_type", "storage_path", "archive_serial_number"}
)


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
    @safe_tool
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
        offset: int = 0,
        limit: int = 25,
    ) -> dict[str, Any]:
        """Search documents in Paperless-ngx.

        Pass ``query`` for a full-text search; pass any of the other arguments to
        filter server-side. Date arguments are ISO dates (``YYYY-MM-DD``). Tag
        IDs are integers. Use ``offset`` and ``limit`` to page through results;
        the response indicates ``has_more`` so the caller knows whether to fetch
        the next page.
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

        items, has_more = await collect(
            paperless.documents.filter(**filters), offset=offset, limit=limit
        )
        return {
            "documents": [format_document(d) for d in items],
            "returned": len(items),
            "offset": offset,
            "limit": limit,
            "has_more": has_more,
        }

    @mcp.tool()
    @safe_tool
    async def get_document(ctx: Context, document_id: int) -> dict[str, Any]:
        """Fetch a single document including content, notes, and custom fields."""
        paperless = get_client(ctx)
        doc = await paperless.documents(document_id)
        return format_document_detail(doc)

    @mcp.tool()
    @safe_tool
    async def get_document_content(ctx: Context, document_id: int) -> dict[str, Any]:
        """Return only the OCR'd text content of a document."""
        paperless = get_client(ctx)
        doc = await paperless.documents(document_id)
        return {"id": doc.id, "content": getattr(doc, "content", None)}

    @mcp.tool()
    @safe_tool
    async def get_document_metadata(ctx: Context, document_id: int) -> dict[str, Any]:
        """Return detailed metadata (file info, checksums, original filename, ...)."""
        paperless = get_client(ctx)
        meta = await paperless.documents.metadata(document_id)
        dumped = safe_dump(meta)
        return dumped if isinstance(dumped, dict) else {"metadata": dumped}

    @mcp.tool()
    @safe_tool
    async def get_document_notes(ctx: Context, document_id: int) -> dict[str, Any]:
        """List all notes attached to a document."""
        paperless = get_client(ctx)
        notes = await paperless.documents.notes(document_id)
        return {"document_id": document_id, "notes": [format_note(n) for n in notes]}

    @mcp.tool()
    @safe_tool
    async def get_document_history(ctx: Context, document_id: int) -> dict[str, Any]:
        """Return the audit history of a document."""
        paperless = get_client(ctx)
        history = await paperless.documents.history(document_id)
        return {"document_id": document_id, "history": [safe_dump(h) for h in history]}

    @mcp.tool()
    @safe_tool
    async def find_similar_documents(
        ctx: Context, document_id: int, offset: int = 0, limit: int = 10
    ) -> dict[str, Any]:
        """Find documents semantically similar to the given document."""
        paperless = get_client(ctx)
        items, has_more = await collect(
            paperless.documents.more_like(document_id), offset=offset, limit=limit
        )
        return {
            "reference": document_id,
            "documents": [format_document(d) for d in items],
            "returned": len(items),
            "offset": offset,
            "limit": limit,
            "has_more": has_more,
        }

    @mcp.tool()
    @safe_tool
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
    @safe_tool
    async def get_document_thumbnail(ctx: Context, document_id: int) -> dict[str, Any]:
        """Return the document's thumbnail image as base64."""
        paperless = get_client(ctx)
        cfg = get_settings(ctx)
        thumb = await paperless.documents.thumbnail(document_id)
        content: bytes = getattr(thumb, "content", b"")
        size = len(content)
        if size > cfg.max_file_bytes:
            return {
                "error": "file_too_large",
                "size_bytes": size,
                "max_bytes": cfg.max_file_bytes,
            }
        return {
            "document_id": document_id,
            "content_type": getattr(thumb, "content_type", "image/webp"),
            "size_bytes": size,
            "content_base64": base64.b64encode(content).decode("ascii"),
        }

    # -- WRITE -------------------------------------------------------------

    if settings.expose_writes:

        @mcp.tool()
        @safe_tool
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
            except (binascii.Error, ValueError) as exc:
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
        @safe_tool
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
            clear_fields: list[str] | None = None,
        ) -> dict[str, Any]:
            """Update fields on an existing document.

            Only the fields you pass are modified. To explicitly clear a foreign
            key or ASN, list its name in ``clear_fields``; allowed names are
            ``correspondent``, ``document_type``, ``storage_path``,
            ``archive_serial_number``. Passing both ``correspondent_id=5`` and
            ``clear_fields=["correspondent"]`` is an error.
            """
            paperless = get_client(ctx)

            clear_set: set[str] = set(clear_fields or [])
            invalid = clear_set - _CLEARABLE_FIELDS
            if invalid:
                return {
                    "error": "invalid_argument",
                    "detail": (
                        f"Unknown clear_fields: {sorted(invalid)}. "
                        f"Allowed: {sorted(_CLEARABLE_FIELDS)}."
                    ),
                }
            conflicts: list[str] = []
            if "correspondent" in clear_set and correspondent_id is not None:
                conflicts.append("correspondent")
            if "document_type" in clear_set and document_type_id is not None:
                conflicts.append("document_type")
            if "storage_path" in clear_set and storage_path_id is not None:
                conflicts.append("storage_path")
            if "archive_serial_number" in clear_set and archive_serial_number is not None:
                conflicts.append("archive_serial_number")
            if conflicts:
                return {
                    "error": "invalid_argument",
                    "detail": f"Fields cannot be set and cleared at once: {conflicts}.",
                }

            doc = await paperless.documents(document_id)
            if title is not None:
                doc.title = title
            if correspondent_id is not None:
                doc.correspondent = correspondent_id
            if document_type_id is not None:
                doc.document_type = document_type_id
            if storage_path_id is not None:
                doc.storage_path = storage_path_id
            if archive_serial_number is not None:
                doc.archive_serial_number = archive_serial_number
            if tag_ids is not None:
                doc.tags = tag_ids
            if content is not None:
                doc.content = content
            if created is not None:
                doc.created = dt.datetime.fromisoformat(created)
            for field in clear_set:
                setattr(doc, field, None)
            await paperless.documents.update(doc)
            return format_document(doc)

        @mcp.tool()
        @safe_tool
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
        @safe_tool
        async def delete_document(ctx: Context, document_id: int) -> dict[str, Any]:
            """Move a document to the trash (recoverable via restore_documents)."""
            paperless = get_client(ctx)
            doc = await paperless.documents(document_id)
            await paperless.documents.delete(doc)
            return {"document_id": document_id, "deleted": True}

        @mcp.tool()
        @safe_tool
        async def delete_document_note(
            ctx: Context, document_id: int, note_id: int
        ) -> dict[str, Any]:
            """Delete a single note from a document."""
            paperless = get_client(ctx)
            await paperless.documents.notes.delete(note_id, document_pk=document_id)
            return {"document_id": document_id, "note_id": note_id, "deleted": True}
