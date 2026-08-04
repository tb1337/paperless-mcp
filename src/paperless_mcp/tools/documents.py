"""Document read/write/delete tools."""

from __future__ import annotations

import base64
import binascii
from functools import partial
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.utilities.types import Image
from pypaperless.exceptions import PaperlessError

from ..client import ToolContext, get_client, get_names, get_settings
from ..config import Settings
from ..formatting import (
    dump_mapping,
    format_document,
    format_document_detail,
    format_history_entry,
    format_note,
    format_task,
)
from ..names import cached_custom_fields
from ._custom_field_query import build_custom_field_query
from ._dates import parse_date, parse_datetime
from ._errors import ToolInputError, ToolResultError, translate_error
from ._paging import page_result, paginate, window
from ._registry import delete_tool, read_tool, register_tools, write_tool
from ._relations import resolve_relation, resolve_tags
from ._task_polling import (
    MAX_POLL_TIMEOUT_SECONDS,
    task_document_id,
    task_status,
    wait_for_task,
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


async def search_documents(
    ctx: ToolContext,
    query: str | None = None,
    title_contains: str | None = None,
    content_contains: str | None = None,
    title_or_content: str | None = None,
    tags_all: list[int] | None = None,
    tags_all_names: list[str] | None = None,
    tags_any: list[int] | None = None,
    tags_any_names: list[str] | None = None,
    tags_none: list[int] | None = None,
    tags_none_names: list[str] | None = None,
    correspondent_id: int | None = None,
    correspondent_name: str | None = None,
    document_type_id: int | None = None,
    document_type_name: str | None = None,
    storage_path_id: int | None = None,
    storage_path_name: str | None = None,
    archive_serial_number: int | None = None,
    is_in_inbox: bool | None = None,
    is_tagged: bool | None = None,
    mime_type: str | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    added_after: str | None = None,
    added_before: str | None = None,
    custom_field_query: Any = None,
    order_by: str | None = None,
    descending: bool = False,
    offset: int = 0,
    limit: int = 25,
) -> dict[str, Any]:
    """Search documents in Paperless-ngx.

    Pass ``query`` for a full-text search over the OCR index (Whoosh syntax,
    e.g. ``invoice AND 2024``); pass any of the other arguments to filter
    server-side. Combining both is allowed. Date arguments are ISO dates
    (``YYYY-MM-DD``).

    Tags, correspondent, document type and storage path each filter by name
    or by ID: pass ``*_name`` / ``*_names`` when the value comes from the
    conversation, ``*_id`` / the ID list only when you have it verbatim from
    a tool result. Passing both is allowed but they must agree — a mismatch
    is rejected, not resolved. A name that matches nothing is an error
    listing the near misses, so no filter silently matches everything.

    ``custom_field_query`` filters on custom field *values*, which no other
    argument reaches — "invoices due in August" is one call instead of a
    walk over the archive. It takes one expression, either as JSON text or
    as the structure itself:

    - an atom, ``[field, operator, value]``, where ``field`` is a custom
      field's name or its ID: ``["Due", "range", ["2024-08-01", "2024-08-31"]]``
    - ``["AND", [expr, ...]]``, ``["OR", [expr, ...]]``, ``["NOT", expr]``
      around other expressions, nested as deep as needed

    Which operators an atom may use depends on the field's ``data_type``, as
    reported by ``list_custom_fields``:

    - any type: ``exact``, ``in`` (a non-empty list), ``isnull`` and
      ``exists`` (both take true/false — ``exists`` asks whether the
      document carries the field at all)
    - ``string``, ``longtext``, ``url``, ``monetary``: ``icontains``,
      ``istartswith``, ``iendswith``
    - ``date``, ``integer``, ``float``, ``monetary``: ``gt``, ``gte``,
      ``lt``, ``lte``, ``range`` (``[start, end]``, both ends inclusive)
    - ``documentlink``: ``contains``, taking a list of document IDs and
      matching the documents linked to *all* of them
    - a ``date`` field also takes a component in front of the operator:
      ``["Due", "month__exact", 8]`` matches every August, over ``year``,
      ``month``, ``day``, ``quarter``, ``week``, ``week_day``, ``iso_year``
      and ``iso_week_day``

    ``order_by`` accepts ``created``, ``added``, ``modified``, ``title``,
    ``archive_serial_number``, ``correspondent__name``,
    ``document_type__name``, ``num_notes``, ``owner``, ``page_count``, ``id``.

    Results are paged: ``total`` is the number of matches and ``has_more``
    tells you whether to request the next ``offset``. Document text is *not*
    included — call ``get_document_content`` for that.
    """
    paperless = await get_client(ctx)
    tags_all = await resolve_tags(
        ctx,
        pks=tags_all,
        names=tags_all_names,
        id_field="tags_all",
        name_field="tags_all_names",
    )
    tags_any = await resolve_tags(
        ctx,
        pks=tags_any,
        names=tags_any_names,
        id_field="tags_any",
        name_field="tags_any_names",
    )
    tags_none = await resolve_tags(
        ctx,
        pks=tags_none,
        names=tags_none_names,
        id_field="tags_none",
        name_field="tags_none_names",
    )
    correspondent_id = await resolve_relation(
        ctx, field="correspondent", pk=correspondent_id, name=correspondent_name
    )
    document_type_id = await resolve_relation(
        ctx, field="document_type", pk=document_type_id, name=document_type_name
    )
    storage_path_id = await resolve_relation(
        ctx, field="storage_path", pk=storage_path_id, name=storage_path_name
    )
    names = await get_names(ctx)
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
    if custom_field_query is not None:
        # Not part of _build_doc_filters: checking the expression needs the
        # field definitions the snapshot above cached.
        filters["custom_field_query"] = build_custom_field_query(
            custom_field_query, cached_custom_fields(paperless)
        )

    items, total = await paginate(paperless.documents, filters, offset=offset, limit=limit)
    return page_result(
        "documents",
        items,
        offset=offset,
        limit=limit,
        total=total,
        formatter=partial(format_document, names=names),
    )


async def get_document(ctx: ToolContext, document_id: int) -> dict[str, Any]:
    """Fetch a single document's fields, notes and custom fields.

    The OCR text comes back only as ``content_preview`` (the first 500
    characters), with ``content_characters`` giving its full length. Call
    ``get_document_content`` when the preview is not enough — that keeps
    inspecting a document's tags or dates from costing a whole scan.
    """
    paperless = await get_client(ctx)
    # Before the fetch, not after: this is what fills the custom-field cache
    # pypaperless enriches the document from while it is being parsed.
    names = await get_names(ctx)
    doc = await paperless.documents(document_id)
    return format_document_detail(doc, names)


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


async def get_document_metadata(ctx: ToolContext, document_id: int) -> dict[str, Any]:
    """Return file-level metadata (checksums, sizes, original filename, ...)."""
    paperless = await get_client(ctx)
    meta = await paperless.documents.metadata(document_id)
    return dump_mapping(meta, key="metadata")


async def get_document_notes(ctx: ToolContext, document_id: int) -> dict[str, Any]:
    """List all notes attached to a document."""
    paperless = await get_client(ctx)
    names = await get_names(ctx)
    notes = await paperless.documents.notes(document_id)
    return {"document_id": document_id, "notes": [format_note(n, names) for n in notes]}


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


async def find_similar_documents(
    ctx: ToolContext, document_id: int, offset: int = 0, limit: int = 10
) -> dict[str, Any]:
    """Find documents whose text is similar to the given document.

    Uses Paperless' full-text index ("more like this"), so it needs the
    index to be built.
    """
    paperless = await get_client(ctx)
    names = await get_names(ctx)
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
        formatter=partial(format_document, names=names),
        reference=document_id,
    )


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
        raise ToolResultError(
            "file_too_large",
            f"The thumbnail is {size} bytes; the cap is {cfg.max_file_bytes}.",
            size_bytes=size,
            max_bytes=cfg.max_file_bytes,
        )
    image = _as_image(content, thumb.content_type)
    if image is None:
        raise ToolResultError(
            "unsupported_media_type",
            f"Paperless returned {thumb.content_type!r}, which is not an image.",
            document_id=document_id,
        )
    return image


async def get_next_asn(ctx: ToolContext) -> dict[str, Any]:
    """Return the next free archive serial number.

    The ASN is the number written on the paper original before it is filed,
    so a physical archive can be walked back to its scan. Paperless derives
    it from the highest one currently stored.

    The value is only free until something claims it: fetch it immediately
    before the ``upload_document`` or ``update_document`` call that uses it,
    and never hand the same number to two documents. Asking twice in a row
    returns the same number, not two.
    """
    paperless = await get_client(ctx)
    return {"next_asn": await paperless.documents.get_next_asn()}


async def upload_document(
    ctx: ToolContext,
    filename: str,
    content_base64: str,
    title: str | None = None,
    correspondent_id: int | None = None,
    correspondent_name: str | None = None,
    document_type_id: int | None = None,
    document_type_name: str | None = None,
    storage_path_id: int | None = None,
    storage_path_name: str | None = None,
    tag_ids: list[int] | None = None,
    tag_names: list[str] | None = None,
    archive_serial_number: int | None = None,
    created: str | None = None,
    poll: bool = False,
    poll_timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Upload a new document into the Paperless consume queue.

    ``content_base64`` is the raw file, base64-encoded. ``created`` is an
    optional ISO date/datetime; Paperless falls back to the consumption
    time.

    Correspondent, document type, storage path and tags each take a name or
    an ID: pass ``*_name`` / ``tag_names`` when the value comes from the
    conversation, ``*_id`` / ``tag_ids`` only when you have it verbatim from
    a tool result. Passing both is allowed but they must agree, and a name
    no object carries is rejected instead of created.

    Consumption is asynchronous, so by default the call returns as soon as
    the file is queued and the ``task_uuid`` is yours to poll with
    ``get_task``. Pass ``poll=True`` to wait for the consumer instead and
    get the new ``document_id`` back from this same call — the useful
    choice whenever the next step is tagging, linking or reading the
    document.

    While polling, the wait ends after ``poll_timeout_seconds`` (default
    30, maximum 300 — raise it for OCR-heavy scans). Running out is not an
    error: the result then carries ``timed_out: true`` and the
    ``task_uuid`` to keep polling with. ``status`` is ``success``,
    ``failure`` or ``revoked`` once consumption finished, and ``task``
    holds the full task record — read its ``result_data`` when the status
    is ``failure``, which most often means Paperless rejected the file as a
    duplicate of a document it already has.
    """
    if poll and not 1 <= poll_timeout_seconds <= MAX_POLL_TIMEOUT_SECONDS:
        raise ToolInputError(
            "poll_timeout_seconds must be between 1 and "
            f"{MAX_POLL_TIMEOUT_SECONDS}, got {poll_timeout_seconds}"
        )
    paperless = await get_client(ctx)
    try:
        content = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ToolInputError(f"content_base64 is not valid base64: {exc}") from exc
    if not content:
        raise ToolInputError("content_base64 decoded to an empty file")

    correspondent_id = await resolve_relation(
        ctx, field="correspondent", pk=correspondent_id, name=correspondent_name
    )
    document_type_id = await resolve_relation(
        ctx, field="document_type", pk=document_type_id, name=document_type_name
    )
    storage_path_id = await resolve_relation(
        ctx, field="storage_path", pk=storage_path_id, name=storage_path_name
    )
    tag_ids = await resolve_tags(
        ctx, pks=tag_ids, names=tag_names, id_field="tag_ids", name_field="tag_names"
    )

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
    queued = {"task_uuid": task_uuid, "filename": filename, "size_bytes": len(content)}
    if not poll:
        return queued

    try:
        task, timed_out = await wait_for_task(
            paperless, str(task_uuid), timeout=poll_timeout_seconds
        )
    except PaperlessError as exc:
        # The file is queued either way. Answering with the bare error would
        # drop the UUID that is the only way back to what it became.
        return {**queued, **(translate_error(exc) or {})}
    names = await get_names(ctx)
    return {
        **queued,
        "status": task_status(task),
        "document_id": task_document_id(task),
        "timed_out": timed_out,
        "task": format_task(task, names) if task is not None else None,
    }


async def update_document(
    ctx: ToolContext,
    document_id: int,
    title: str | None = None,
    correspondent_id: int | None = None,
    correspondent_name: str | None = None,
    document_type_id: int | None = None,
    document_type_name: str | None = None,
    storage_path_id: int | None = None,
    storage_path_name: str | None = None,
    tag_ids: list[int] | None = None,
    tag_names: list[str] | None = None,
    archive_serial_number: int | None = None,
    content: str | None = None,
    created: str | None = None,
    clear_fields: list[str] | None = None,
) -> dict[str, Any]:
    """Update fields on an existing document.

    Only the fields you pass are modified, and the tag list is *replaced*
    (use ``bulk_edit_documents`` to add or remove individual tags).

    Correspondent, document type, storage path and tags each take a name or
    an ID: pass ``*_name`` / ``tag_names`` when the value comes from the
    conversation, ``*_id`` / ``tag_ids`` only when you have it verbatim from
    a tool result — which is what every result reports next to the ID.
    Passing both is allowed but they must agree; a mismatch is rejected
    rather than resolved, and a name that matches nothing is an error rather
    than a newly created tag.

    To explicitly unset a foreign key or the ASN, list its name in
    ``clear_fields``: ``correspondent``, ``document_type``,
    ``storage_path``, ``archive_serial_number``. Setting and clearing the
    same field in one call is rejected.
    """
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
    tag_ids = await resolve_tags(
        ctx, pks=tag_ids, names=tag_names, id_field="tag_ids", name_field="tag_names"
    )
    names = await get_names(ctx)

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
    return {"changed": changed, **format_document(doc, names)}


async def add_document_note(ctx: ToolContext, document_id: int, note: str) -> dict[str, Any]:
    """Add a free-text note to a document."""
    paperless = await get_client(ctx)
    draft = paperless.documents.notes.create(document_id, note=note)
    note_id = await paperless.documents.notes.save(draft)
    return {"document_id": document_id, "note_id": note_id}


async def delete_document(ctx: ToolContext, document_id: int) -> dict[str, Any]:
    """Move a document to the trash (recoverable with ``restore_documents``)."""
    paperless = await get_client(ctx)
    doc = await paperless.documents(document_id, lazy=True)
    await paperless.documents.delete(doc)
    return {"document_id": document_id, "deleted": True}


async def delete_document_note(ctx: ToolContext, document_id: int, note_id: int) -> dict[str, Any]:
    """Delete a single note from a document."""
    paperless = await get_client(ctx)
    await paperless.documents.notes.delete(note_id, pk=document_id)
    return {"document_id": document_id, "note_id": note_id, "deleted": True}


def register(mcp: MCPServer, settings: Settings) -> None:
    """Register the document tools this deployment exposes."""
    register_tools(
        mcp,
        settings,
        (
            read_tool(search_documents),
            read_tool(get_document),
            read_tool(get_document_content),
            read_tool(get_document_metadata),
            read_tool(get_document_notes),
            read_tool(get_document_history),
            read_tool(find_similar_documents),
            read_tool(download_document),
            read_tool(get_document_thumbnail),
            read_tool(get_next_asn),
            write_tool(upload_document, destructive=False, idempotent=False),
            write_tool(update_document, destructive=True, idempotent=True),
            write_tool(add_document_note, destructive=False, idempotent=False),
            delete_tool(delete_document),
            delete_tool(delete_document_note),
        ),
    )
