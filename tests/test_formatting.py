"""Formatting tests against the *real* pypaperless v6 models.

These are the tests that catch a field rename in a pypaperless upgrade: they
build models from raw API payloads exactly as the library does, so a projection
that reaches for a field that no longer exists shows up here.
"""

from __future__ import annotations

from typing import Any

import pytest
from pypaperless.cache import PaperlessCache
from pypaperless.models import Document, SavedView, Tag, Task
from pypaperless.runtime import PaperlessRuntime
from pypaperless.transport import PaperlessTransport

from paperless_mcp.formatting import (
    format_document_detail,
    format_saved_view,
    format_tag,
    format_task,
    safe_dump,
)


@pytest.fixture
def runtime() -> PaperlessRuntime:
    return PaperlessRuntime(PaperlessTransport("http://test", "token"), PaperlessCache())


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
    assert result["content"] == "ocr text"
    assert result["tags"] == [1, 2]
    assert result["notes"] == [
        {
            "id": 7,
            "note": "checked",
            "document": 42,
            "created": "2026-01-03T10:00:00+00:00",
            "user": None,
        }
    ]
    assert result["custom_fields"] == [
        {"field": 3, "name": None, "data_type": None, "value": "open"}
    ]


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
        ({"a": 1}, {"a": 1}),
        ([1, 2], [1, 2]),
    ],
)
def test_safe_dump_passes_scalars_through(value: Any, expected: Any) -> None:
    assert safe_dump(value) == expected


def test_safe_dump_serializes_pydantic_models(runtime: PaperlessRuntime) -> None:
    tag = Tag.from_data(runtime, {"id": 1, "name": "x", "matching_algorithm": 1})
    dumped = safe_dump(tag)
    assert dumped["id"] == 1
    assert dumped["matching_algorithm"] == 1
