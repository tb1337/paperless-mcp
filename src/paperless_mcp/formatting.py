"""Format pypaperless models into compact, LLM-friendly dictionaries.

The pypaperless Pydantic models carry a lot of fields, and several of them are
enums or nested models that would serialize into noise. These helpers project
just what matters for a language model and normalize the value types.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from enum import Enum
from typing import Any


def _iso(value: dt.date | dt.datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _safe(obj: Any, name: str) -> Any:
    return getattr(obj, name, None)


def _plain(value: Any) -> Any:
    """Unwrap an Enum to its value; pass everything else through untouched."""
    return value.value if isinstance(value, Enum) else value


def safe_dump(obj: Any) -> Any:
    """Best-effort serialization to a JSON-friendly structure.

    Handles Pydantic models, Mappings, iterables and scalars. Falls back to
    ``str(obj)`` when nothing else fits, so the model still sees a useful value
    instead of an exception.
    """
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, Enum):
        return _plain(obj)
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, Mapping):
        return {k: safe_dump(v) for k, v in obj.items()}
    if isinstance(obj, (dt.date, dt.datetime)):
        return obj.isoformat()
    if isinstance(obj, (list, tuple, set)):
        return [safe_dump(x) for x in obj]
    if isinstance(obj, Iterable):
        return [safe_dump(x) for x in obj]
    return str(obj)


def _matching(obj: Any) -> dict[str, Any]:
    """Project the matching fields shared by tags, correspondents, types, paths."""
    algorithm = _safe(obj, "matching_algorithm")
    return {
        "match": _safe(obj, "match"),
        "matching_algorithm": _plain(algorithm),
        "matching_algorithm_name": algorithm.name.lower() if isinstance(algorithm, Enum) else None,
        "is_insensitive": _safe(obj, "is_insensitive"),
    }


def format_document(doc: Any) -> dict[str, Any]:
    """Project a Document model into a compact dict."""
    return {
        "id": _safe(doc, "id"),
        "title": _safe(doc, "title"),
        "correspondent": _safe(doc, "correspondent"),
        "document_type": _safe(doc, "document_type"),
        "storage_path": _safe(doc, "storage_path"),
        "tags": list(_safe(doc, "tags") or []),
        "created": _iso(_safe(doc, "created")),
        "added": _iso(_safe(doc, "added")),
        "modified": _iso(_safe(doc, "modified")),
        "deleted_at": _iso(_safe(doc, "deleted_at")),
        "archive_serial_number": _safe(doc, "archive_serial_number"),
        "original_file_name": _safe(doc, "original_file_name"),
        "archived_file_name": _safe(doc, "archived_file_name"),
        "owner": _safe(doc, "owner"),
        "page_count": _safe(doc, "page_count"),
        "mime_type": _safe(doc, "mime_type"),
        "is_shared_by_requester": _safe(doc, "is_shared_by_requester"),
    }


def format_custom_field_value(value: Any) -> dict[str, Any]:
    """Project one custom field value attached to a document."""
    return {
        "field": _safe(value, "field"),
        "name": _safe(value, "name"),
        "data_type": _plain(_safe(value, "data_type")),
        "value": safe_dump(_safe(value, "value")),
    }


#: How much of the OCR text ``format_document_detail`` carries as a preview.
CONTENT_PREVIEW_CHARS = 500


def format_document_detail(doc: Any) -> dict[str, Any]:
    """Project a Document model including notes and custom fields.

    The OCR text is capped at :data:`CONTENT_PREVIEW_CHARS` — enough to tell
    what a document is, bounded enough that the result size does not depend on
    how long the scan was. ``content_characters`` reports the untruncated
    length, so a caller can decide whether fetching the rest through
    ``get_document_content`` is worth the tokens.
    """
    base = format_document(doc)
    content = _safe(doc, "content") or ""
    base["content_preview"] = content[:CONTENT_PREVIEW_CHARS]
    base["content_characters"] = len(content)
    base["custom_fields"] = [
        format_custom_field_value(cf) for cf in (_safe(doc, "custom_fields") or [])
    ]
    # ``Document.notes`` is the notes *service* in pypaperless v6; the embedded
    # payload lives on the aliased ``notes_`` field.
    base["notes"] = [format_note(n) for n in (_safe(doc, "notes_") or [])]
    base["root_document"] = _safe(doc, "root_document")
    search_hit = _safe(doc, "search_hit_")
    if search_hit is not None:
        base["search_hit"] = safe_dump(search_hit)
    return base


def format_tag(tag: Any) -> dict[str, Any]:
    """Project a Tag model into a compact dict."""
    return {
        "id": _safe(tag, "id"),
        "name": _safe(tag, "name"),
        "slug": _safe(tag, "slug"),
        "color": _safe(tag, "color"),
        "text_color": _safe(tag, "text_color"),
        "is_inbox_tag": _safe(tag, "is_inbox_tag"),
        "parent": _safe(tag, "parent"),
        "document_count": _safe(tag, "document_count"),
        "owner": _safe(tag, "owner"),
        **_matching(tag),
    }


def format_correspondent(c: Any) -> dict[str, Any]:
    """Project a Correspondent model into a compact dict."""
    return {
        "id": _safe(c, "id"),
        "name": _safe(c, "name"),
        "slug": _safe(c, "slug"),
        "document_count": _safe(c, "document_count"),
        "last_correspondence": _iso(_safe(c, "last_correspondence")),
        "owner": _safe(c, "owner"),
        **_matching(c),
    }


def format_document_type(d: Any) -> dict[str, Any]:
    """Project a DocumentType model into a compact dict."""
    return {
        "id": _safe(d, "id"),
        "name": _safe(d, "name"),
        "slug": _safe(d, "slug"),
        "document_count": _safe(d, "document_count"),
        "owner": _safe(d, "owner"),
        **_matching(d),
    }


def format_storage_path(s: Any) -> dict[str, Any]:
    """Project a StoragePath model into a compact dict."""
    return {
        "id": _safe(s, "id"),
        "name": _safe(s, "name"),
        "slug": _safe(s, "slug"),
        "path": _safe(s, "path"),
        "document_count": _safe(s, "document_count"),
        "owner": _safe(s, "owner"),
        **_matching(s),
    }


def format_custom_field(cf: Any) -> dict[str, Any]:
    """Project a CustomField definition into a compact dict."""
    return {
        "id": _safe(cf, "id"),
        "name": _safe(cf, "name"),
        "data_type": _plain(_safe(cf, "data_type")),
        "extra_data": safe_dump(_safe(cf, "extra_data")),
        "document_count": _safe(cf, "document_count"),
    }


def format_saved_view(v: Any) -> dict[str, Any]:
    """Project a SavedView model into a compact dict."""
    return {
        "id": _safe(v, "id"),
        "name": _safe(v, "name"),
        "sort_field": _safe(v, "sort_field"),
        "sort_reverse": _safe(v, "sort_reverse"),
        "page_size": _safe(v, "page_size"),
        "display_mode": _plain(_safe(v, "display_mode")),
        "display_fields": [_plain(f) for f in (_safe(v, "display_fields") or [])],
        "owner": _safe(v, "owner"),
    }


def format_share_link(sl: Any) -> dict[str, Any]:
    """Project a ShareLink model into a compact dict."""
    return {
        "id": _safe(sl, "id"),
        "document": _safe(sl, "document"),
        "slug": _safe(sl, "slug"),
        "file_version": _plain(_safe(sl, "file_version")),
        "expiration": _iso(_safe(sl, "expiration")),
        "created": _iso(_safe(sl, "created")),
    }


def format_task(t: Any) -> dict[str, Any]:
    """Project a Task model into a compact dict.

    pypaperless v6 follows Paperless-ngx 3.0's task API: ``type`` became
    ``task_type``, ``result`` became ``result_data``, and ``related_document``
    became the list ``related_document_ids``.
    """
    return {
        "id": _safe(t, "id"),
        "task_id": _safe(t, "task_id"),
        "task_type": _plain(_safe(t, "task_type")),
        "task_type_display": _safe(t, "task_type_display"),
        "status": _plain(_safe(t, "status")),
        "status_display": _safe(t, "status_display"),
        "trigger_source": _plain(_safe(t, "trigger_source")),
        "acknowledged": _safe(t, "acknowledged"),
        "date_created": _iso(_safe(t, "date_created")),
        "date_started": _iso(_safe(t, "date_started")),
        "date_done": _iso(_safe(t, "date_done")),
        "duration_seconds": _safe(t, "duration_seconds"),
        "input_data": safe_dump(_safe(t, "input_data")),
        "result_data": safe_dump(_safe(t, "result_data")),
        "related_document_ids": list(_safe(t, "related_document_ids") or []),
        "owner": _safe(t, "owner"),
    }


def format_note(n: Any) -> dict[str, Any]:
    """Project a DocumentNote model into a compact dict."""
    return {
        "id": _safe(n, "id"),
        "note": _safe(n, "note"),
        "document": _safe(n, "document"),
        "created": _iso(_safe(n, "created")),
        "user": _safe(n, "user"),
    }


def format_history_entry(h: Any) -> dict[str, Any]:
    """Project a DocumentHistory audit-log entry into a compact dict."""
    actor = _safe(h, "actor")
    return {
        "id": _safe(h, "id"),
        "timestamp": _iso(_safe(h, "timestamp")),
        "action": _plain(_safe(h, "action")),
        "changes": safe_dump(_safe(h, "changes")),
        "actor": (
            {"id": _safe(actor, "id"), "username": _safe(actor, "username")}
            if actor is not None
            else None
        ),
    }
