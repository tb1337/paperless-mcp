"""Formatting tests against the *real* pypaperless v6 models.

These are the tests that catch a field rename in a pypaperless upgrade: they
build models from raw API payloads exactly as the library does, so a projection
that reaches for a field that no longer exists shows up here.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from pypaperless.models import Document, SavedView, Tag, Task
from pypaperless.models.status import Status
from pypaperless.models.types import MatchingAlgorithm
from pypaperless.runtime import PaperlessRuntime

from paperless_mcp.formatting import (
    CONTENT_PREVIEW_CHARS,
    dump_mapping,
    enrich_suggestions,
    format_document,
    format_document_detail,
    format_document_summary,
    format_saved_view,
    format_tag,
    format_task,
    safe_dump,
    summarize_status,
)
from paperless_mcp.names import NameMap
from tests.conftest import make_runtime


@pytest.fixture
def runtime() -> PaperlessRuntime:
    return make_runtime()


def test_format_document_detail_reads_embedded_notes(runtime: PaperlessRuntime) -> None:
    """``Document.notes`` is the notes *service*; the payload lives on ``notes_``."""
    doc = Document.from_data(
        runtime,
        {
            "id": 42,
            "title": "Invoice",
            "created": "2026-01-02",
            "content": "ocr text",
            "tags": [1, 2],
            "notes": [{"id": 7, "note": "checked", "created": "2026-01-03T10:00:00Z"}],
            "custom_fields": [{"field": 3, "value": "open"}],
        },
    )
    result = format_document_detail(doc)

    assert result["id"] == 42
    assert result["created"] == "2026-01-02"
    assert result["tags"] == [1, 2]
    assert "content" not in result
    assert result["content_preview"] == "ocr text"
    assert result["notes"] == [
        {
            "id": 7,
            "note": "checked",
            "document": 42,
            "created": "2026-01-03T10:00:00+00:00",
            "user": None,
            "user_name": None,
        }
    ]
    assert result["custom_fields"] == [
        {"field": 3, "name": None, "data_type": None, "label": None, "value": "open"}
    ]


def test_format_document_detail_caps_the_content_preview(runtime: PaperlessRuntime) -> None:
    """A long scan must not blow up the result: preview is capped, length is not."""
    ocr = "x" * (CONTENT_PREVIEW_CHARS * 3)
    doc = Document.from_data(runtime, {"id": 1, "title": "Scan", "content": ocr})

    result = format_document_detail(doc)

    assert result["content_preview"] == "x" * CONTENT_PREVIEW_CHARS
    assert result["content_characters"] == len(ocr)


def test_format_document_detail_handles_a_document_without_content(
    runtime: PaperlessRuntime,
) -> None:
    doc = Document.from_data(runtime, {"id": 1, "title": "Scan", "content": None})

    result = format_document_detail(doc)

    assert result["content_preview"] == ""
    assert result["content_characters"] == 0


def test_format_tag_unwraps_the_matching_algorithm_enum(runtime: PaperlessRuntime) -> None:
    tag = Tag.from_data(
        runtime,
        {
            "id": 1,
            "name": "Invoice",
            "slug": "invoice",
            "color": "#ff0000",
            "matching_algorithm": 6,
            "match": "acme",
            "is_insensitive": True,
            "is_inbox_tag": False,
            "document_count": 12,
        },
    )
    result = format_tag(tag)

    assert result["matching_algorithm"] == 6
    assert result["matching_algorithm_name"] == "auto"
    assert result["color"] == "#ff0000"
    assert result["document_count"] == 12


def test_format_task_uses_the_v6_field_names(runtime: PaperlessRuntime) -> None:
    task = Task.from_data(
        runtime,
        {
            "id": 5,
            "task_id": "c0ffee-uuid",
            "task_type": "consume_file",
            "task_type_display": "Consume file",
            "status": "success",
            "status_display": "Success",
            "trigger_source": "api_upload",
            "date_created": "2026-01-02T10:00:00Z",
            "date_done": "2026-01-02T10:00:30Z",
            "duration_seconds": 30.0,
            "result_data": {"document_id": 99},
            "related_document_ids": [99],
            "acknowledged": False,
        },
    )
    result = format_task(task)

    assert result["task_id"] == "c0ffee-uuid"
    assert result["task_type"] == "consume_file"
    assert result["status"] == "success"
    assert result["trigger_source"] == "api_upload"
    assert result["result_data"] == {"document_id": 99}
    assert result["related_document_ids"] == [99]
    assert result["duration_seconds"] == 30.0


def test_format_saved_view_projects_display_configuration(runtime: PaperlessRuntime) -> None:
    view = SavedView.from_data(
        runtime,
        {
            "id": 3,
            "name": "Inbox",
            "sort_field": "created",
            "sort_reverse": True,
            "page_size": 50,
            "display_mode": "table",
            "display_fields": ["title", "created", "custom_field_8"],
            "filter_rules": [{"rule_type": 6, "value": "1"}],
        },
    )
    result = format_saved_view(view)

    assert result["display_mode"] == "table"
    assert result["display_fields"] == ["title", "created", "custom_field_8"]
    assert result["page_size"] == 50


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("text", "text"),
        (3, 3),
        (True, True),
        ({"a": 1}, {"a": 1}),
        ([1, 2], [1, 2]),
        ((1, 2), [1, 2]),
        ({1: "a"}, {"1": "a"}),
        (dt.date(2026, 1, 2), "2026-01-02"),
        (MatchingAlgorithm.LITERAL, 3),
        # A buffer is Iterable, so without an arm of its own it dumped as a list
        # of integers: safe_dump(b"%PDF") was [37, 80, 68, 70].
        (b"%PDF", "<4 bytes>"),
        (bytearray(b"ab"), "<2 bytes>"),
        (memoryview(b"abc"), "<3 bytes>"),
    ],
)
def test_safe_dump_produces_json_friendly_values(value: Any, expected: Any) -> None:
    assert safe_dump(value) == expected


def test_safe_dump_falls_back_to_str_for_anything_else() -> None:
    assert safe_dump(object) == str(object)


def test_safe_dump_serializes_pydantic_models(runtime: PaperlessRuntime) -> None:
    tag = Tag.from_data(runtime, {"id": 1, "name": "x", "matching_algorithm": 1})
    dumped = safe_dump(tag)
    assert isinstance(dumped, dict)
    assert dumped["id"] == 1
    assert dumped["matching_algorithm"] == 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"total": 3}, {"total": 3}),
        ("not an object", {"statistics": "not an object"}),
        (None, {"statistics": None}),
    ],
)
def test_dump_mapping_parks_a_non_mapping_under_its_key(value: Any, expected: Any) -> None:
    assert dump_mapping(value, key="statistics") == expected


def test_format_document_resolves_every_relation(runtime: PaperlessRuntime) -> None:
    doc = Document.from_data(
        runtime,
        {
            "id": 1,
            "correspondent": 10,
            "document_type": 20,
            "storage_path": 30,
            "tags": [40, 99],
            "owner": 50,
        },
    )
    names = NameMap(
        correspondents={10: "Utilities"},
        document_types={20: "Invoice"},
        storage_paths={30: "Archive"},
        tags={40: "paid"},
        users={50: "clerk"},
    )

    result = format_document(doc, names)

    assert result["correspondent"] == 10
    assert result["correspondent_name"] == "Utilities"
    assert result["document_type_name"] == "Invoice"
    assert result["storage_path_name"] == "Archive"
    assert result["owner_name"] == "clerk"
    # Unknown tag 99 keeps its slot rather than shifting "paid" onto it.
    assert result["tags"] == [40, 99]
    assert result["tag_names"] == ["paid", None]


def test_format_document_summary_is_a_strict_subset_of_the_full_projection(
    runtime: PaperlessRuntime,
) -> None:
    """The summary narrows the full projection; it never renames or recomputes.

    A key that only the summary knows would be a second projection to keep in step,
    and the whole point of building one from the other is that there is only one.
    """
    doc = Document.from_data(runtime, {"id": 1, "title": "Rechnung", "tags": [40]})

    summary = format_document_summary(doc)
    full = format_document(doc)

    assert set(summary) < set(full)
    assert all(summary[key] == full[key] for key in summary)


def test_format_document_summary_keeps_what_a_hit_is_judged_on(
    runtime: PaperlessRuntime,
) -> None:
    """Pinned by name: dropping one of these silently is a regression in the results.

    `deleted_at` is in the list because `list_trash` formats through here and its
    docstring sends the model to that field.
    """
    doc = Document.from_data(runtime, {"id": 1, "title": "Rechnung"})

    assert set(format_document_summary(doc)) == {
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
    }


def test_format_document_without_a_snapshot_still_has_the_name_keys(
    runtime: PaperlessRuntime,
) -> None:
    """The result shape must not depend on whether the master data could be read."""
    doc = Document.from_data(runtime, {"id": 1, "correspondent": 10, "tags": [40]})

    result = format_document(doc)

    assert result["correspondent_name"] is None
    assert result["tag_names"] == [None]


def test_format_tag_resolves_its_parent_and_owner(runtime: PaperlessRuntime) -> None:
    tag = Tag.from_data(runtime, {"id": 2, "name": "Electricity", "parent": 1, "owner": 50})

    result = format_tag(tag, NameMap(tags={1: "Contract"}, users={50: "clerk"}))

    assert result["parent"] == 1
    assert result["parent_name"] == "Contract"
    assert result["owner_name"] == "clerk"


def test_enrich_suggestions_adds_names_beside_the_id_lists() -> None:
    suggestions = {"correspondents": [10], "tags": [40, 99], "dates": ["2026-01-01"]}

    result = enrich_suggestions(
        suggestions, NameMap(correspondents={10: "Utilities"}, tags={40: "paid"})
    )

    assert result["correspondent_names"] == ["Utilities"]
    assert result["tag_names"] == ["paid", None]
    # Keys the payload does not carry must not be invented.
    assert "storage_path_names" not in result
    assert result["dates"] == ["2026-01-01"]


def test_summarize_status_covers_the_subsystems_has_errors_ignores() -> None:
    """``Status.has_errors`` looks at four subsystems and treats WARNING as fine."""
    status = Status.model_validate(
        {
            "database": {"status": "OK"},
            "tasks": {
                "redis_status": "OK",
                "celery_status": "OK",
                "classifier_status": "OK",
                "index_status": "ERROR",
                "index_error": "index missing",
                "sanity_check_status": "WARNING",
            },
        }
    )

    assert status.has_errors is False
    result = summarize_status(status)

    assert result["health"] == "error"
    assert result["problems"] == [
        {"subsystem": "index", "status": "ERROR", "error": "index missing"},
        {"subsystem": "sanity_check", "status": "WARNING", "error": None},
    ]


def test_summarize_status_reads_unknown_as_unknown() -> None:
    status = Status.model_validate({"database": {"status": "UNKNOWN"}, "tasks": None})

    assert summarize_status(status) == {
        "health": "unknown",
        "problems": [{"subsystem": "database", "status": "UNKNOWN", "error": None}],
    }


def test_summarize_status_does_not_claim_health_it_was_not_told() -> None:
    """An empty payload must not read as OK — nothing reported is not nothing wrong."""
    assert summarize_status(Status.model_validate({})) == {"health": "unknown", "problems": []}
