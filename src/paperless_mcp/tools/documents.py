"""Document read/write/delete tools."""

from __future__ import annotations

import base64
import binascii
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.utilities.types import Image

from ..client import ToolContext, get_client, get_settings
from ..config import Settings
from ..formatting import (
    format_document,
    format_document_detail,
    format_history_entry,
    format_note,
    safe_dump,
)
from ._helpers import (
    ToolInputError,
    delete_tool,
    page_result,
    paginate,
    parse_date,
    parse_datetime,
    read_tool,
    safe_tool,
    window,
    write_tool,
)

# Updatable fields that accept a "clear" instruction via the clear_fields list.
_CLEARABLE_FIELDS: frozenset[str] = frozenset(
    {"correspondent", "document_type", "storage_path", "archive_serial_number"}
)

# Paperless-ngx' DocumentViewSet ordering_fields. Anything else makes the API
# silently ignore the parameter, so we reject it up front instead.
_ORDER_FIELDS: frozenset[str] = frozenset(
    {
        "id",
        "title",
        "created",
        "modified",
        "added",
        "archive_serial_number",
        "correspondent__name",
        "document_type__name",
        "num_notes",
        "owner",
        "page_count",
    }
)

_IMAGE_FORMATS: dict[str, str] = {
    "image/webp": "webp",
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/gif": "gif",
}


def _build_doc_filters(
    *,
    title_contains: str | None = None,
    content_contains: str | None = None,
    title_or_content: str | None = None,
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
    order_by: str | None = None,
    descending: bool = False,
) -> dict[str, Any]:
    """Translate the tool's flat arguments into Paperless' Django-style lookups."""
    filters: dict[str, Any] = {}
    if title_contains:
        filters["title__icontains"] = title_contains
    if content_contains:
        filters["content__icontains"] = content_contains
    if title_or_content:
        filters["title_content"] = title_or_content
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
        filters["created__date__gte"] = parse_date(created_after, field="created_after").isoformat()
    if created_before:
        filters["created__date__lte"] = parse_date(
            created_before, field="created_before"
        ).isoformat()
    if added_after:
        filters["added__date__gte"] = parse_date(added_after, field="added_after").isoformat()
    if added_before:
        filters["added__date__lte"] = parse_date(added_before, field="added_before").isoformat()
    if order_by:
        if order_by not in _ORDER_FIELDS:
            raise ToolInputError(
                f"Unknown order_by {order_by!r}. Allowed: {sorted(_ORDER_FIELDS)}."
            )
        filters["ordering"] = f"-{order_by}" if descending else order_by
    return filters


def _as_image(content: bytes, content_type: str | None) -> Image | None:
    """Wrap raw bytes as MCP image content when the media type is an image."""
    image_format = _IMAGE_FORMATS.get((content_type or "").split(";")[0].strip().lower())
    if image_format is None:
        return None
    return Image(data=content, format=image_format)


def register(mcp: MCPServer, settings: Settings) -> None:
    """Register document tools according to the configured visibility flags."""
    _register_reads(mcp)
    if settings.expose_writes:
        _register_writes(mcp)
    if settings.expose_deletes:
        _register_deletes(mcp)


def _register_reads(mcp: MCPServer) -> None:
    @read_tool(mcp)
    @safe_tool
    async def search_documents(
        ctx: ToolContext,
        query: str | None = None,
        title_contains: str | None = None,
        content_contains: str | None = None,
        title_or_content: str | None = None,
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
        order_by: str | None = None,
        descending: bool = False,
        offset: int = 0,
        limit: int = 25,
    ) -> dict[str, Any]:
        """Search documents in Paperless-ngx.

        Pass ``query`` for a full-text search over the OCR index (Whoosh syntax,
        e.g. ``invoice AND 2024``); pass any of the other arguments to filter
        server-side. Combining both is allowed. Date arguments are ISO dates
        (``YYYY-MM-DD``); tag/correspondent/type arguments are numeric IDs, which
        you can look up with ``list_tags`` / ``list_correspondents`` /
        ``list_document_types``.

        ``order_by`` accepts ``created``, ``added``, ``modified``, ``title``,
        ``archive_serial_number``, ``correspondent__name``,
        ``document_type__name``, ``num_notes``, ``owner``, ``page_count``, ``id``.

        Results are paged: ``total`` is the number of matches and ``has_more``
        tells you whether to request the next ``offset``. Document text is *not*
        included — call ``get_document_content`` for that.
        """
        paperless = await get_client(ctx)
        filters = _build_doc_filters(
            title_contains=title_contains,
            content_contains=content_contains,
            title_or_content=title_or_content,
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
            order_by=order_by,
            descending=descending,
        )
        if query:
            filters["query"] = query

        items, total = await paginate(paperless.documents, filters, offset=offset, limit=limit)
        return page_result(
            "documents",
            items,
            offset=offset,
            limit=limit,
            total=total,
            formatter=format_document,
        )

    @read_tool(mcp)
    @safe_tool
    async def get_document(ctx: ToolContext, document_id: int) -> dict[str, Any]:
        """Fetch a single document including OCR content, notes and custom fields."""
        paperless = await get_client(ctx)
        doc = await paperless.documents(document_id)
        return format_document_detail(doc)

    @read_tool(mcp)
    @safe_tool
    async def get_document_content(ctx: ToolContext, document_id: int) -> dict[str, Any]:
        """Return only the OCR'd text content of a document."""
        paperless = await get_client(ctx)
        doc = await paperless.documents(document_id)
        content = doc.content or ""
        return {
            "document_id": doc.id,
            "title": doc.title,
            "characters": len(content),
            "content": content,
        }

    @read_tool(mcp)
    @safe_tool
    async def get_document_metadata(ctx: ToolContext, document_id: int) -> dict[str, Any]:
        """Return file-level metadata (checksums, sizes, original filename, ...)."""
        paperless = await get_client(ctx)
        meta = await paperless.documents.metadata(document_id)
        dumped = safe_dump(meta)
        return dumped if isinstance(dumped, dict) else {"metadata": dumped}

    @read_tool(mcp)
    @safe_tool
    async def get_document_notes(ctx: ToolContext, document_id: int) -> dict[str, Any]:
        """List all notes attached to a document."""
        paperless = await get_client(ctx)
        notes = await paperless.documents.notes(document_id)
        return {"document_id": document_id, "notes": [format_note(n) for n in notes]}

    @read_tool(mcp)
    @safe_tool
    async def get_document_history(
        ctx: ToolContext, document_id: int, offset: int = 0, limit: int = 50
    ) -> dict[str, Any]:
        """Return the audit history (who changed what, when) of a document."""
        paperless = await get_client(ctx)
        entries = await paperless.documents.history(document_id)
        items, total = window(list(entries), offset=offset, limit=limit)
        return page_result(
            "history",
            items,
            offset=offset,
            limit=limit,
            total=total,
            formatter=format_history_entry,
            document_id=document_id,
        )

    @read_tool(mcp)
    @safe_tool
    async def find_similar_documents(
        ctx: ToolContext, document_id: int, offset: int = 0, limit: int = 10
    ) -> dict[str, Any]:
        """Find documents whose text is similar to the given document.

        Uses Paperless' full-text index ("more like this"), so it needs the
        index to be built.
        """
        paperless = await get_client(ctx)
        items, total = await paginate(
            paperless.documents,
            {"more_like_id": document_id},
            offset=offset,
            limit=limit,
        )
        return page_result(
            "documents",
            items,
            offset=offset,
            limit=limit,
            total=total,
            formatter=format_document,
            reference=document_id,
        )

    @read_tool(mcp)
    @safe_tool
    async def download_document(
        ctx: ToolContext, document_id: int, original: bool = False
    ) -> dict[str, Any]:
        """Return the document file as base64.

        Set ``original=True`` for the file as it was consumed instead of the
        archived (OCR'd PDF) version. Returns an error result if the file
        exceeds ``PAPERLESS_MCP_MAX_FILE_BYTES``. Prefer
        ``get_document_content`` when you only need the text — it is far
        cheaper.
        """
        paperless = await get_client(ctx)
        cfg = get_settings(ctx)
        downloaded = await paperless.documents.download(document_id, original=original)
        content: bytes = downloaded.content or b""
        size = len(content)
        if size > cfg.max_file_bytes:
            return {
                "error": "file_too_large",
                "detail": f"The file is {size} bytes; the configured cap is {cfg.max_file_bytes}.",
                "size_bytes": size,
                "max_bytes": cfg.max_file_bytes,
                "hint": "Raise PAPERLESS_MCP_MAX_FILE_BYTES or fetch the file from the Web UI.",
            }
        return {
            "document_id": document_id,
            "filename": downloaded.disposition_filename,
            "content_type": downloaded.content_type,
            "size_bytes": size,
            "content_base64": base64.b64encode(content).decode("ascii"),
        }

    @read_tool(mcp)
    @safe_tool
    async def get_document_thumbnail(ctx: ToolContext, document_id: int) -> Image:
        """Return the document's thumbnail as a viewable image.

        Oversized or non-image responses come back as a structured error result
        instead of image content.
        """
        paperless = await get_client(ctx)
        cfg = get_settings(ctx)
        thumb = await paperless.documents.thumbnail(document_id)
        content: bytes = thumb.content or b""
        size = len(content)
        if size > cfg.max_file_bytes:
            # The declared return type is Image, but MCPServer serializes any
            # other value as text content, which is how error results surface.
            return {  # type: ignore[return-value]
                "error": "file_too_large",
                "detail": f"The thumbnail is {size} bytes; the cap is {cfg.max_file_bytes}.",
                "size_bytes": size,
                "max_bytes": cfg.max_file_bytes,
            }
        image = _as_image(content, thumb.content_type)
        if image is None:
            return {  # type: ignore[return-value]
                "error": "unsupported_media_type",
                "detail": f"Paperless returned {thumb.content_type!r}, which is not an image.",
                "document_id": document_id,
            }
        return image


def _register_writes(mcp: MCPServer) -> None:
    @write_tool(mcp, destructive=False, idempotent=False)
    @safe_tool
    async def upload_document(
        ctx: ToolContext,
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
        """Upload a new document into the Paperless consume queue.

        ``content_base64`` is the raw file, base64-encoded. ``created`` is an
        optional ISO date/datetime; Paperless falls back to the consumption
        time. Consumption is asynchronous — the returned ``task_uuid`` can be
        polled with ``get_task``.
        """
        paperless = await get_client(ctx)
        try:
            content = base64.b64decode(content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ToolInputError(f"content_base64 is not valid base64: {exc}") from exc
        if not content:
            raise ToolInputError("content_base64 decoded to an empty file")

        draft = paperless.documents.create(
            document=content,
            filename=filename,
            title=title,
            correspondent=correspondent_id,
            document_type=document_type_id,
            storage_path=storage_path_id,
            tags=tag_ids,
            archive_serial_number=archive_serial_number,
            created=parse_datetime(created, field="created") if created else None,
        )
        task_uuid = await paperless.documents.save(draft)
        return {"task_uuid": task_uuid, "filename": filename, "size_bytes": len(content)}

    @write_tool(mcp, destructive=True, idempotent=True)
    @safe_tool
    async def update_document(
        ctx: ToolContext,
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

        Only the fields you pass are modified, and ``tag_ids`` *replaces* the
        tag list (use ``bulk_edit_documents`` to add or remove individual tags).
        To explicitly unset a foreign key or the ASN, list its name in
        ``clear_fields``: ``correspondent``, ``document_type``,
        ``storage_path``, ``archive_serial_number``. Setting and clearing the
        same field in one call is rejected.
        """
        paperless = await get_client(ctx)

        clear_set = set(clear_fields or [])
        invalid = clear_set - _CLEARABLE_FIELDS
        if invalid:
            raise ToolInputError(
                f"Unknown clear_fields: {sorted(invalid)}. Allowed: {sorted(_CLEARABLE_FIELDS)}."
            )
        supplied = {
            "correspondent": correspondent_id,
            "document_type": document_type_id,
            "storage_path": storage_path_id,
            "archive_serial_number": archive_serial_number,
        }
        conflicts = sorted(name for name in clear_set if supplied[name] is not None)
        if conflicts:
            raise ToolInputError(f"Fields cannot be set and cleared at once: {conflicts}.")

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
            doc.created = parse_date(created, field="created")
        for field in clear_set:
            setattr(doc, field, None)

        changed = await paperless.documents.update(doc)
        return {"changed": changed, **format_document(doc)}

    @write_tool(mcp, destructive=False, idempotent=False)
    @safe_tool
    async def add_document_note(ctx: ToolContext, document_id: int, note: str) -> dict[str, Any]:
        """Add a free-text note to a document."""
        paperless = await get_client(ctx)
        draft = paperless.documents.notes.create(document_id, note=note)
        note_id = await paperless.documents.notes.save(draft)
        return {"document_id": document_id, "note_id": note_id}


def _register_deletes(mcp: MCPServer) -> None:
    @delete_tool(mcp)
    @safe_tool
    async def delete_document(ctx: ToolContext, document_id: int) -> dict[str, Any]:
        """Move a document to the trash (recoverable with ``restore_documents``)."""
        paperless = await get_client(ctx)
        doc = await paperless.documents(document_id, lazy=True)
        await paperless.documents.delete(doc)
        return {"document_id": document_id, "deleted": True}

    @delete_tool(mcp)
    @safe_tool
    async def delete_document_note(
        ctx: ToolContext, document_id: int, note_id: int
    ) -> dict[str, Any]:
        """Delete a single note from a document."""
        paperless = await get_client(ctx)
        await paperless.documents.notes.delete(note_id, pk=document_id)
        return {"document_id": document_id, "note_id": note_id, "deleted": True}
