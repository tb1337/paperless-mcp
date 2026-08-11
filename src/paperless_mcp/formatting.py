"""Format pypaperless models into compact, LLM-friendly dictionaries.

The pypaperless Pydantic models carry a lot of fields, and several of them are
enums or nested models that would serialize into noise. These helpers project
just what matters for a language model and normalize the value types.

Relations are reported twice: the raw ID Paperless stores, plus the
``<field>_name`` resolved through the :class:`~paperless_mcp.names.NameMap` the
caller passes in. The ID stays authoritative — it is what every filter and
write argument takes — while the name is what makes a result readable without
a lookup call. Without a snapshot the name keys are still present and ``None``,
so the result shape never depends on whether the master data could be read.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Iterator, Mapping
from enum import Enum, StrEnum
from typing import Any, Final

from pydantic import BaseModel
from pypaperless.models import Document, Task
from pypaperless.models.status import Status, StatusType

from .names import EMPTY_NAMES, NameMap, name_of, names_of


def _iso(value: dt.date | dt.datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _safe(obj: Any, name: str) -> Any:
    return getattr(obj, name, None)


def _plain(value: Any) -> Any:
    """Unwrap an Enum to its value; pass everything else through untouched."""
    return value.value if isinstance(value, Enum) else value


#: Everything :func:`safe_dump` can produce. Spelling it out is what lets a
#: caller narrowing the result with ``isinstance`` actually narrow something,
#: rather than passing ``Any`` along.
type JsonValue = bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"] | None


def safe_dump(obj: object) -> JsonValue:
    """Best-effort serialization to a JSON-friendly structure.

    Handles Pydantic models, Mappings, iterables and scalars. Falls back to
    ``str(obj)`` when nothing else fits, so the model still sees a useful value
    instead of an exception.

    Order matters: the ``model_dump`` check has to precede the ``Mapping`` one
    (a pydantic model is not a Mapping, but a RootModel wrapping one dumps far
    better than it iterates), and the buffer types have to precede ``Iterable``
    or they serialize as a list of integers.
    """
    if obj is None or isinstance(obj, str | int | float):
        # bool is an int, so it needs no arm of its own.
        return obj
    if isinstance(obj, Enum):
        value = obj.value
        return value if isinstance(value, bool | int | float | str) else str(value)
    if isinstance(obj, bytes | bytearray | memoryview):
        # JSON has no bytes, and every buffer type is Iterable: without this a
        # PDF's first bytes came back as [37, 80, 68, 70].
        return f"<{len(obj)} bytes>"
    if isinstance(obj, BaseModel):
        dumped: JsonValue = obj.model_dump(mode="json")
        return dumped
    if isinstance(obj, Mapping):
        return {str(key): safe_dump(value) for key, value in obj.items()}
    if isinstance(obj, dt.date | dt.datetime):
        return obj.isoformat()
    if isinstance(obj, Iterable):
        return [safe_dump(item) for item in obj]
    return str(obj)


def dump_mapping(obj: object, *, key: str) -> dict[str, JsonValue]:
    """Dump *obj* to a JSON dict, parking a non-mapping result under *key*.

    Every endpoint that answers with a free-form model wants this: a dict is
    merged into the tool result as-is, and anything else still has to arrive
    under a name the model can read.
    """
    dumped = safe_dump(obj)
    return dumped if isinstance(dumped, dict) else {key: dumped}


def _matching(obj: Any) -> dict[str, Any]:
    """Project the matching fields shared by tags, correspondents, types, paths."""
    algorithm = _safe(obj, "matching_algorithm")
    return {
        "match": _safe(obj, "match"),
        "matching_algorithm": _plain(algorithm),
        "matching_algorithm_name": algorithm.name.lower() if isinstance(algorithm, Enum) else None,
        "is_insensitive": _safe(obj, "is_insensitive"),
    }


def format_document(doc: Document, names: NameMap = EMPTY_NAMES) -> dict[str, Any]:
    """Project a Document model into a compact dict, resolving its relations."""
    correspondent = doc.correspondent
    document_type = doc.document_type
    storage_path = doc.storage_path
    tags = list(doc.tags or [])
    owner = doc.owner
    return {
        "id": doc.id,
        "title": doc.title,
        "correspondent": correspondent,
        "correspondent_name": name_of(names.correspondents, correspondent),
        "document_type": document_type,
        "document_type_name": name_of(names.document_types, document_type),
        "storage_path": storage_path,
        "storage_path_name": name_of(names.storage_paths, storage_path),
        "tags": tags,
        "tag_names": names_of(names.tags, tags),
        "created": _iso(doc.created),
        "added": _iso(doc.added),
        "modified": _iso(doc.modified),
        "deleted_at": _iso(doc.deleted_at),
        "archive_serial_number": doc.archive_serial_number,
        "original_file_name": doc.original_file_name,
        "archived_file_name": doc.archived_file_name,
        "owner": owner,
        "owner_name": name_of(names.users, owner),
        "page_count": doc.page_count,
        "mime_type": doc.mime_type,
        "is_shared_by_requester": doc.is_shared_by_requester,
    }


#: What :func:`format_document_summary` keeps, in the order a result reports it.
#:
#: Identity, the relations a follow-up call filters on, and the dates a request is
#: usually phrased in. What is missing is what a list is not read for: the storage
#: path, the owner, both file names, the MIME type and ``modified`` — nine keys that
#: cost about two thirds of a window and that ``get_document`` answers for the one
#: document the model actually picks. ``deleted_at`` stays because ``list_trash``
#: formats through here and is documented as the place to read it.
_SUMMARY_KEYS: Final[tuple[str, ...]] = (
    "id",
    "title",
    "correspondent",
    "correspondent_name",
    "document_type",
    "document_type_name",
    "tags",
    "tag_names",
    "created",
    "added",
    "deleted_at",
    "archive_serial_number",
    "page_count",
)


def format_document_summary(doc: Document, names: NameMap = EMPTY_NAMES) -> dict[str, Any]:
    """Project a Document down to the fields a list result carries.

    Built by narrowing :func:`format_document` rather than by projecting the model
    a second time: two independent field lists drift, and the one that drifts is
    always the one nobody reads. A key that stops existing upstream fails here as a
    ``KeyError`` under test instead of quietly vanishing from every search result.
    """
    full = format_document(doc, names)
    return {key: full[key] for key in _SUMMARY_KEYS}


def format_custom_field_value(value: Any) -> dict[str, Any]:
    """Project one custom field value attached to a document.

    ``name``, ``data_type`` and ``label`` are not in the API payload —
    pypaperless merges them in from the custom-field cache
    :func:`~paperless_mcp.names.load_names` fills. ``label`` is the readable
    option of a ``select`` field, whose stored ``value`` is an option ID.
    """
    return {
        "field": _safe(value, "field"),
        "name": _safe(value, "name"),
        "data_type": _plain(_safe(value, "data_type")),
        "label": _safe(value, "label"),
        "value": safe_dump(_safe(value, "value")),
    }


#: How much of the OCR text ``format_document_detail`` carries as a preview.
CONTENT_PREVIEW_CHARS = 500


def format_document_detail(doc: Document, names: NameMap = EMPTY_NAMES) -> dict[str, Any]:
    """Project a Document model including notes and custom fields.

    The OCR text is capped at :data:`CONTENT_PREVIEW_CHARS` — enough to tell
    what a document is, bounded enough that the result size does not depend on
    how long the scan was. ``content_characters`` reports the untruncated
    length, so a caller can decide whether fetching the rest through
    ``get_document_content`` is worth the tokens.
    """
    base = format_document(doc, names)
    content = doc.content or ""
    base["content_preview"] = content[:CONTENT_PREVIEW_CHARS]
    base["content_characters"] = len(content)
    base["custom_fields"] = [format_custom_field_value(cf) for cf in (doc.custom_fields or [])]
    # ``Document.notes`` is the notes *service* in pypaperless v6; the embedded
    # payload lives on the aliased ``notes_`` field.
    base["notes"] = [format_note(n, names) for n in (doc.notes_ or [])]
    base["root_document"] = doc.root_document
    search_hit = doc.search_hit_
    if search_hit is not None:
        base["search_hit"] = safe_dump(search_hit)
    return base


def _owner(obj: Any, names: NameMap) -> dict[str, Any]:
    """Project the owner ID shared by every user-ownable resource, plus its name."""
    owner = _safe(obj, "owner")
    return {"owner": owner, "owner_name": name_of(names.users, owner)}


def format_tag(tag: Any, names: NameMap = EMPTY_NAMES) -> dict[str, Any]:
    """Project a Tag model into a compact dict."""
    parent = _safe(tag, "parent")
    return {
        "id": _safe(tag, "id"),
        "name": _safe(tag, "name"),
        "slug": _safe(tag, "slug"),
        "color": _safe(tag, "color"),
        "text_color": _safe(tag, "text_color"),
        "is_inbox_tag": _safe(tag, "is_inbox_tag"),
        "parent": parent,
        "parent_name": name_of(names.tags, parent),
        "document_count": _safe(tag, "document_count"),
        **_owner(tag, names),
        **_matching(tag),
    }


def format_correspondent(c: Any, names: NameMap = EMPTY_NAMES) -> dict[str, Any]:
    """Project a Correspondent model into a compact dict."""
    return {
        "id": _safe(c, "id"),
        "name": _safe(c, "name"),
        "slug": _safe(c, "slug"),
        "document_count": _safe(c, "document_count"),
        "last_correspondence": _iso(_safe(c, "last_correspondence")),
        **_owner(c, names),
        **_matching(c),
    }


def format_document_type(d: Any, names: NameMap = EMPTY_NAMES) -> dict[str, Any]:
    """Project a DocumentType model into a compact dict."""
    return {
        "id": _safe(d, "id"),
        "name": _safe(d, "name"),
        "slug": _safe(d, "slug"),
        "document_count": _safe(d, "document_count"),
        **_owner(d, names),
        **_matching(d),
    }


def format_storage_path(s: Any, names: NameMap = EMPTY_NAMES) -> dict[str, Any]:
    """Project a StoragePath model into a compact dict."""
    return {
        "id": _safe(s, "id"),
        "name": _safe(s, "name"),
        "slug": _safe(s, "slug"),
        "path": _safe(s, "path"),
        "document_count": _safe(s, "document_count"),
        **_owner(s, names),
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


def format_saved_view(v: Any, names: NameMap = EMPTY_NAMES) -> dict[str, Any]:
    """Project a SavedView model into a compact dict."""
    return {
        "id": _safe(v, "id"),
        "name": _safe(v, "name"),
        "sort_field": _safe(v, "sort_field"),
        "sort_reverse": _safe(v, "sort_reverse"),
        "page_size": _safe(v, "page_size"),
        "display_mode": _plain(_safe(v, "display_mode")),
        "display_fields": [_plain(f) for f in (_safe(v, "display_fields") or [])],
        **_owner(v, names),
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


def format_task(t: Task, names: NameMap = EMPTY_NAMES) -> dict[str, Any]:
    """Project a Task model into a compact dict.

    pypaperless v6 follows Paperless-ngx 3.0's task API: ``type`` became
    ``task_type``, ``result`` became ``result_data``, and ``related_document``
    became the list ``related_document_ids``.
    """
    return {
        "id": t.id,
        "task_id": t.task_id,
        "task_type": _plain(t.task_type),
        "task_type_display": t.task_type_display,
        "status": _plain(t.status),
        "status_display": t.status_display,
        "trigger_source": _plain(t.trigger_source),
        "acknowledged": t.acknowledged,
        "date_created": _iso(t.date_created),
        "date_started": _iso(t.date_started),
        "date_done": _iso(t.date_done),
        "duration_seconds": t.duration_seconds,
        "input_data": safe_dump(t.input_data),
        "result_data": safe_dump(t.result_data),
        "related_document_ids": list(t.related_document_ids or []),
        **_owner(t, names),
    }


def format_note(n: Any, names: NameMap = EMPTY_NAMES) -> dict[str, Any]:
    """Project a DocumentNote model into a compact dict."""
    user = _safe(n, "user")
    return {
        "id": _safe(n, "id"),
        "note": _safe(n, "note"),
        "document": _safe(n, "document"),
        "created": _iso(_safe(n, "created")),
        "user": user,
        "user_name": name_of(names.users, user),
    }


def enrich_suggestions(suggestions: Mapping[str, Any], names: NameMap) -> dict[str, Any]:
    """Add resolved ``*_names`` lists to an already-dumped suggestions payload.

    Both suggestion endpoints answer with ID lists under the same keys. The
    payload is passed through untouched apart from the added names, because it
    is a plain ``safe_dump`` of a model whose fields differ per Paperless
    version.
    """
    # The lookups are bound from the NameMap fields directly rather than through
    # a table keyed by their names: a fifth suggestion resource then means one
    # row here, not one row in each of two tables that have to agree.
    resolved = {
        target: names_of(lookup, suggestions[source])
        for source, target, lookup in (
            ("correspondents", "correspondent_names", names.correspondents),
            ("document_types", "document_type_names", names.document_types),
            ("storage_paths", "storage_path_names", names.storage_paths),
            ("tags", "tag_names", names.tags),
        )
        if source in suggestions
    }
    return {**suggestions, **resolved}


class HealthVerdict(StrEnum):
    """The rolled-up verdict :func:`summarize_status` reports."""

    OK = "ok"
    WARNING = "warning"
    ERROR = "error"
    UNKNOWN = "unknown"


#: Worst first: the first state present in the reported set decides the verdict.
_VERDICT: Final[Mapping[StatusType, HealthVerdict]] = {
    StatusType.ERROR: HealthVerdict.ERROR,
    StatusType.WARNING: HealthVerdict.WARNING,
    StatusType.UNKNOWN: HealthVerdict.UNKNOWN,
}


def _subsystems(status: Status) -> Iterator[tuple[str, StatusType | None, str | None]]:
    """Yield the health-bearing blocks of ``/api/status/``, name first.

    Read by attribute rather than through a table of field-name strings: a rename
    upstream is a type error here, where the table turned it into a subsystem that
    silently stopped being checked.
    """
    if (database := status.database) is not None:
        yield "database", database.status, database.error
    if (tasks := status.tasks) is not None:
        yield "redis", tasks.redis_status, tasks.redis_error
        yield "celery", tasks.celery_status, tasks.celery_error
        yield "index", tasks.index_status, tasks.index_error
        yield "classifier", tasks.classifier_status, tasks.classifier_error
        yield "sanity_check", tasks.sanity_check_status, tasks.sanity_check_error


def summarize_status(status: Status) -> dict[str, Any]:
    """Roll the per-subsystem flags of a Status model up into one verdict.

    Answers "is this archive healthy?" without the caller walking six nested
    blocks. ``Status.has_errors`` is not enough: it looks at four of the six
    subsystems and treats WARNING as fine.
    """
    problems: list[dict[str, Any]] = []
    reported: set[StatusType] = set()
    for name, state, error in _subsystems(status):
        if state is None:
            continue
        reported.add(state)
        if state is not StatusType.OK:
            problems.append({"subsystem": name, "status": state.value, "error": error})
    if not reported:
        # Nothing reported is not the same as nothing wrong.
        return {"health": HealthVerdict.UNKNOWN, "problems": problems}
    verdict = next((v for state, v in _VERDICT.items() if state in reported), HealthVerdict.OK)
    return {"health": verdict, "problems": problems}


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
