"""Format pypaperless models into compact, LLM-friendly dictionaries.

The pypaperless Pydantic models contain a lot of fields and some return
internal references that aren't useful to a language model. These helpers
project just the fields that matter and serialize them consistently.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from typing import Any


def _iso(value: dt.date | dt.datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _safe(obj: Any, name: str) -> Any:
    return getattr(obj, name, None)


def safe_dump(obj: Any) -> Any:
    """Best-effort serialization to a JSON-friendly structure.

    Handles Pydantic models, Mappings, iterables and scalars. Falls back to
    ``str(obj)`` when nothing else fits, so the LLM still sees a useful value
    instead of an exception.
    """
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, Mapping):
        return {k: safe_dump(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [safe_dump(x) for x in obj]
    if isinstance(obj, (dt.date, dt.datetime)):
        return obj.isoformat()
    if isinstance(obj, Iterable):
        return [safe_dump(x) for x in obj]
    return str(obj)


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
        "archive_serial_number": _safe(doc, "archive_serial_number"),
        "original_file_name": _safe(doc, "original_file_name"),
        "archived_file_name": _safe(doc, "archived_file_name"),
        "owner": _safe(doc, "owner"),
        "page_count": _safe(doc, "page_count"),
        "mime_type": _safe(doc, "mime_type"),
        "is_shared_by_requester": _safe(doc, "is_shared_by_requester"),
    }


def format_document_detail(doc: Any) -> dict[str, Any]:
    """Project a Document model including OCR content and custom fields."""
    base = format_document(doc)
    base["content"] = _safe(doc, "content")
    custom = _safe(doc, "custom_fields") or []
    base["custom_fields"] = [
        {"field": _safe(cf, "field"), "value": _safe(cf, "value")} for cf in custom
    ]
    base["notes"] = [
        {
            "id": _safe(n, "id"),
            "note": _safe(n, "note"),
            "created": _iso(_safe(n, "created")),
            "user": _safe(n, "user"),
        }
        for n in (_safe(doc, "notes") or [])
    ]
    return base


def format_tag(tag: Any) -> dict[str, Any]:
    """Project a Tag model into a compact dict."""
    return {
        "id": _safe(tag, "id"),
        "name": _safe(tag, "name"),
        "slug": _safe(tag, "slug"),
        "color": _safe(tag, "color"),
        "match": _safe(tag, "match"),
        "matching_algorithm": _safe(tag, "matching_algorithm"),
        "is_inbox_tag": _safe(tag, "is_inbox_tag"),
        "document_count": _safe(tag, "document_count"),
        "owner": _safe(tag, "owner"),
    }


def format_correspondent(c: Any) -> dict[str, Any]:
    """Project a Correspondent model into a compact dict."""
    return {
        "id": _safe(c, "id"),
        "name": _safe(c, "name"),
        "slug": _safe(c, "slug"),
        "match": _safe(c, "match"),
        "matching_algorithm": _safe(c, "matching_algorithm"),
        "document_count": _safe(c, "document_count"),
        "last_correspondence": _iso(_safe(c, "last_correspondence")),
        "owner": _safe(c, "owner"),
    }


def format_document_type(d: Any) -> dict[str, Any]:
    """Project a DocumentType model into a compact dict."""
    return {
        "id": _safe(d, "id"),
        "name": _safe(d, "name"),
        "slug": _safe(d, "slug"),
        "match": _safe(d, "match"),
        "matching_algorithm": _safe(d, "matching_algorithm"),
        "document_count": _safe(d, "document_count"),
        "owner": _safe(d, "owner"),
    }


def format_storage_path(s: Any) -> dict[str, Any]:
    """Project a StoragePath model into a compact dict."""
    return {
        "id": _safe(s, "id"),
        "name": _safe(s, "name"),
        "slug": _safe(s, "slug"),
        "path": _safe(s, "path"),
        "match": _safe(s, "match"),
        "matching_algorithm": _safe(s, "matching_algorithm"),
        "document_count": _safe(s, "document_count"),
        "owner": _safe(s, "owner"),
    }


def format_custom_field(cf: Any) -> dict[str, Any]:
    """Project a CustomField model into a compact dict."""
    return {
        "id": _safe(cf, "id"),
        "name": _safe(cf, "name"),
        "data_type": _safe(cf, "data_type"),
        "extra_data": _safe(cf, "extra_data"),
    }


def format_saved_view(v: Any) -> dict[str, Any]:
    """Project a SavedView model into a compact dict."""
    return {
        "id": _safe(v, "id"),
        "name": _safe(v, "name"),
        "show_on_dashboard": _safe(v, "show_on_dashboard"),
        "show_in_sidebar": _safe(v, "show_in_sidebar"),
        "sort_field": _safe(v, "sort_field"),
        "sort_reverse": _safe(v, "sort_reverse"),
        "owner": _safe(v, "owner"),
    }


def format_share_link(sl: Any) -> dict[str, Any]:
    """Project a ShareLink model into a compact dict."""
    return {
        "id": _safe(sl, "id"),
        "document": _safe(sl, "document"),
        "expiration": _iso(_safe(sl, "expiration")),
        "slug": _safe(sl, "slug"),
        "file_version": _safe(sl, "file_version"),
        "created": _iso(_safe(sl, "created")),
    }


def format_task(t: Any) -> dict[str, Any]:
    """Project a Task model into a compact dict."""
    return {
        "id": _safe(t, "id"),
        "task_id": _safe(t, "task_id"),
        "task_file_name": _safe(t, "task_file_name"),
        "type": _safe(t, "type"),
        "status": _safe(t, "status"),
        "result": _safe(t, "result"),
        "acknowledged": _safe(t, "acknowledged"),
        "date_created": _iso(_safe(t, "date_created")),
        "date_started": _iso(_safe(t, "date_started")),
        "date_done": _iso(_safe(t, "date_done")),
        "related_document": _safe(t, "related_document"),
        "owner": _safe(t, "owner"),
    }


def format_note(n: Any) -> dict[str, Any]:
    """Project a DocumentNote model into a compact dict."""
    return {
        "id": _safe(n, "id"),
        "note": _safe(n, "note"),
        "created": _iso(_safe(n, "created")),
        "user": _safe(n, "user"),
    }
