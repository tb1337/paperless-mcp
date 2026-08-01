"""Tests for the document tools (mocked PaperlessClient)."""

from __future__ import annotations

import base64
import datetime as dt
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.server.mcpserver.utilities.types import Image
from pypaperless.exceptions import ItemNotFoundError
from pypaperless.models import CustomField
from pypaperless.models.documents.document import Document
from pypaperless.models.types import CustomFieldType
from pypaperless.runtime import PaperlessRuntime

from tests.conftest import FakeService, build_mcp, call_tool, make_settings


def _doc(doc_id: int = 1, title: str = "Test") -> SimpleNamespace:
    return SimpleNamespace(
        id=doc_id,
        title=title,
        correspondent=None,
        document_type=None,
        storage_path=None,
        tags=[],
        created=None,
        added=None,
        modified=None,
        deleted_at=None,
        archive_serial_number=None,
        original_file_name=None,
        archived_file_name=None,
        owner=None,
        page_count=None,
        mime_type=None,
        is_shared_by_requester=False,
        content="ocr text",
        custom_fields=[],
        notes_=[],
        root_document=None,
        search_hit_=None,
    )


@pytest.mark.asyncio
async def test_search_documents_paginates(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.filter_results = [_doc(i) for i in range(1, 6)]
    mcp = build_mcp(make_settings(), paperless)

    page1 = await call_tool(mcp, "search_documents", offset=0, limit=2)
    assert [d["id"] for d in page1["documents"]] == [1, 2]
    assert page1["has_more"] is True
    assert page1["returned"] == 2
    assert page1["total"] == 5

    page3 = await call_tool(mcp, "search_documents", offset=4, limit=2)
    assert [d["id"] for d in page3["documents"]] == [5]
    assert page3["has_more"] is False


@pytest.mark.asyncio
async def test_search_documents_passes_filters(make_paperless: Any) -> None:
    paperless = make_paperless()
    mcp = build_mcp(make_settings(), paperless)

    await call_tool(
        mcp,
        "search_documents",
        query="invoice",
        title_contains="bill",
        tags_any=[1, 2],
        tags_none=[9],
        correspondent_id=7,
        created_after="2026-01-01",
        is_in_inbox=True,
        order_by="created",
        descending=True,
    )
    assert paperless.documents.filter_calls == [
        {
            "title__icontains": "bill",
            "tags__id__in": "1,2",
            "tags__id__none": "9",
            "correspondent__id": 7,
            "is_in_inbox": True,
            "created__date__gte": "2026-01-01",
            "ordering": "-created",
            "query": "invoice",
        }
    ]


@pytest.mark.asyncio
async def test_search_documents_sends_the_custom_field_query_as_json(
    make_paperless: Any,
) -> None:
    paperless = make_paperless()
    paperless.custom_fields.filter_results = [
        SimpleNamespace(id=3, name="Due", data_type=CustomFieldType.DATE)
    ]
    mcp = build_mcp(make_settings(), paperless)

    await call_tool(
        mcp,
        "search_documents",
        document_type_id=2,
        custom_field_query=["Due", "range", ["2024-08-01", "2024-09-01"]],
    )

    assert paperless.documents.filter_calls == [
        {
            "document_type__id": 2,
            "custom_field_query": '["Due", "range", ["2024-08-01", "2024-09-01"]]',
        }
    ]


@pytest.mark.asyncio
async def test_search_documents_checks_the_query_against_the_definitions(
    make_paperless: Any,
) -> None:
    """The snapshot search_documents already awaits is what makes the check free."""
    paperless = make_paperless()
    paperless.custom_fields.filter_results = [
        SimpleNamespace(id=3, name="Due", data_type=CustomFieldType.DATE)
    ]
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "search_documents", custom_field_query=["Duo", "exists", True])

    assert result["error"] == "invalid_argument"
    assert "'Due'" in result["cause"]
    assert paperless.documents.filter_calls == []


@pytest.mark.asyncio
async def test_search_documents_rejects_unknown_ordering(make_paperless: Any) -> None:
    mcp = build_mcp(make_settings(), make_paperless())
    result = await call_tool(mcp, "search_documents", order_by="content")
    assert result["error"] == "invalid_argument"


@pytest.mark.asyncio
async def test_search_documents_rejects_bad_dates(make_paperless: Any) -> None:
    mcp = build_mcp(make_settings(), make_paperless())
    result = await call_tool(mcp, "search_documents", created_after="yesterday")
    assert result["error"] == "invalid_argument"
    assert "created_after" in result["cause"]


@pytest.mark.asyncio
async def test_get_document_returns_detail(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.get_result = _doc(42, "Bill")
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "get_document", document_id=42)
    assert result["id"] == 42
    assert result["title"] == "Bill"
    assert result["notes"] == []
    # Only a preview of the OCR text; the full text is get_document_content's job.
    assert "content" not in result
    assert result["content_preview"] == "ocr text"
    assert result["content_characters"] == len("ocr text")


@pytest.mark.asyncio
async def test_get_document_content_reports_length(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.get_result = _doc(3, "Bill")
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "get_document_content", document_id=3)
    assert result == {
        "document_id": 3,
        "title": "Bill",
        "characters": len("ocr text"),
        "content": "ocr text",
    }


@pytest.mark.asyncio
async def test_get_document_translates_not_found(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.get_raises = ItemNotFoundError("no such id")
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "get_document", document_id=99)
    assert result["error"] == "not_found"
    assert "does not exist" in result["detail"]


@pytest.mark.asyncio
async def test_update_document_clear_fields(make_paperless: Any) -> None:
    doc = _doc(5, "old")
    doc.correspondent = 7
    doc.document_type = 3

    paperless = make_paperless()
    paperless.documents.get_result = doc
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(
        mcp,
        "update_document",
        document_id=5,
        title="new title",
        created="2026-03-04",
        clear_fields=["correspondent"],
    )
    assert paperless.documents.update_calls == [doc]
    assert doc.title == "new title"
    assert doc.created == dt.date(2026, 3, 4)
    assert doc.correspondent is None  # cleared
    assert doc.document_type == 3  # untouched
    assert result["id"] == 5
    assert result["changed"] is True


@pytest.mark.asyncio
async def test_update_document_rejects_unknown_clear_field(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.get_result = _doc(5)
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "update_document", document_id=5, clear_fields=["title"])
    assert result["error"] == "invalid_argument"
    assert "title" in result["cause"]
    assert paperless.documents.update_calls == []  # nothing happened


@pytest.mark.asyncio
async def test_update_document_rejects_conflicting_set_and_clear(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.get_result = _doc(5)
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(
        mcp,
        "update_document",
        document_id=5,
        correspondent_id=9,
        clear_fields=["correspondent"],
    )
    assert result["error"] == "invalid_argument"
    assert paperless.documents.update_calls == []


@pytest.mark.asyncio
async def test_upload_document_passes_content_and_metadata(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.save_returns = "abc-task-uuid"
    mcp = build_mcp(make_settings(), paperless)

    payload = base64.b64encode(b"%PDF-1.4 fake").decode("ascii")
    result = await call_tool(
        mcp,
        "upload_document",
        filename="invoice.pdf",
        content_base64=payload,
        title="Invoice",
        tag_ids=[1, 2],
        created="2026-02-03",
    )
    assert result == {
        "task_uuid": "abc-task-uuid",
        "filename": "invoice.pdf",
        "size_bytes": len(b"%PDF-1.4 fake"),
    }
    assert len(paperless.documents.save_calls) == 1
    draft = paperless.documents.save_calls[0]
    assert draft.document == b"%PDF-1.4 fake"
    assert draft.filename == "invoice.pdf"
    assert draft.title == "Invoice"
    assert draft.tags == [1, 2]
    assert draft.created == dt.datetime(2026, 2, 3, 0, 0)


@pytest.mark.asyncio
async def test_upload_document_rejects_bad_base64(make_paperless: Any) -> None:
    paperless = make_paperless()
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(
        mcp,
        "upload_document",
        filename="x.pdf",
        content_base64="!!not base64!!",
    )
    assert result["error"] == "invalid_argument"
    assert paperless.documents.save_calls == []


@pytest.mark.asyncio
async def test_upload_document_rejects_empty_payload(make_paperless: Any) -> None:
    paperless = make_paperless()
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "upload_document", filename="x.pdf", content_base64="")
    assert result["error"] == "invalid_argument"
    assert paperless.documents.save_calls == []


@pytest.mark.asyncio
async def test_add_document_note_creates_a_scoped_draft(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.notes = FakeService(save_returns=11)
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "add_document_note", document_id=4, note="checked")
    assert result == {"document_id": 4, "note_id": 11}
    assert paperless.documents.notes.create_calls == [{"args": (4,), "note": "checked"}]


@pytest.mark.asyncio
async def test_delete_document_note_passes_the_document_pk(make_paperless: Any) -> None:
    """pypaperless v6 renamed the keyword from ``document_pk`` to ``pk``."""
    paperless = make_paperless()
    paperless.documents.notes = FakeService()
    mcp = build_mcp(make_settings(enable_delete=True), paperless)

    result = await call_tool(mcp, "delete_document_note", document_id=4, note_id=11)
    assert result == {"document_id": 4, "note_id": 11, "deleted": True}
    assert paperless.documents.notes.delete_calls == [{"obj": 11, "args": (), "pk": 4}]


@pytest.mark.asyncio
async def test_delete_document_hidden_without_enable_delete(make_paperless: Any) -> None:
    mcp = build_mcp(make_settings(enable_delete=False), make_paperless())
    assert "delete_document" not in mcp._tool_manager._tools


@pytest.mark.asyncio
async def test_delete_document_fetches_lazily(make_paperless: Any) -> None:
    doc = _doc(7)
    paperless = make_paperless()
    paperless.documents.get_result = doc
    mcp = build_mcp(make_settings(enable_delete=True), paperless)

    result = await call_tool(mcp, "delete_document", document_id=7)
    assert result == {"document_id": 7, "deleted": True}
    assert paperless.documents.get_calls == [(7, {"lazy": True})]
    assert paperless.documents.delete_calls == [{"obj": doc, "args": ()}]


@pytest.mark.asyncio
async def test_download_document_returns_base64(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.download = _returns(
        SimpleNamespace(
            content=b"%PDF-1.4",
            content_type="application/pdf",
            disposition_filename="invoice.pdf",
        )
    )
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "download_document", document_id=1)
    assert result["filename"] == "invoice.pdf"
    assert base64.b64decode(result["content_base64"]) == b"%PDF-1.4"


@pytest.mark.asyncio
async def test_download_document_rejects_oversized(make_paperless: Any) -> None:
    big = b"x" * (2 * 1024 * 1024)  # 2 MiB > the 1 MiB test cap
    paperless = make_paperless()
    paperless.documents.download = _returns(
        SimpleNamespace(content=big, content_type="application/pdf", disposition_filename="x.pdf")
    )
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "download_document", document_id=1)
    assert result["error"] == "file_too_large"
    assert result["size_bytes"] == len(big)
    assert result["max_bytes"] == 1024 * 1024


@pytest.mark.asyncio
async def test_get_document_thumbnail_returns_image_content(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.thumbnail = _returns(
        SimpleNamespace(content=b"webpdata", content_type="image/webp")
    )
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "get_document_thumbnail", document_id=1)
    assert isinstance(result, Image)
    assert result.data == b"webpdata"
    assert result.to_image_content().mime_type == "image/webp"


@pytest.mark.asyncio
async def test_get_document_thumbnail_reports_non_image_types(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.thumbnail = _returns(
        SimpleNamespace(content=b"nope", content_type="text/html")
    )
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "get_document_thumbnail", document_id=1)
    assert result["error"] == "unsupported_media_type"
    assert result["document_id"] == 1


@pytest.mark.asyncio
async def test_get_document_thumbnail_rejects_oversized(make_paperless: Any) -> None:
    # The tool is declared as returning Image, so the error travels out as a
    # ToolResultError; the model must still see the same dict a JSON tool sends.
    big = b"x" * (2 * 1024 * 1024)  # 2 MiB > the 1 MiB test cap
    paperless = make_paperless()
    paperless.documents.thumbnail = _returns(
        SimpleNamespace(content=big, content_type="image/webp")
    )
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "get_document_thumbnail", document_id=1)
    assert result["error"] == "file_too_large"
    assert result["size_bytes"] == len(big)
    assert result["max_bytes"] == 1024 * 1024


def _returns(value: Any) -> Any:
    async def _call(*_args: Any, **_kwargs: Any) -> Any:
        return value

    return _call


@pytest.mark.asyncio
async def test_search_documents_resolves_ids_to_names(make_paperless: Any) -> None:
    """The names come from the shared snapshot, not from a per-document lookup."""
    paperless = make_paperless()
    doc = _doc(1)
    doc.correspondent = 10
    doc.tags = [40, 99]
    paperless.documents.filter_results = [doc]
    paperless.correspondents.filter_results = [SimpleNamespace(id=10, name="Utilities")]
    paperless.tags.filter_results = [SimpleNamespace(id=40, name="paid")]
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "search_documents")

    found = result["documents"][0]
    assert found["correspondent"] == 10
    assert found["correspondent_name"] == "Utilities"
    assert found["tag_names"] == ["paid", None]


@pytest.mark.asyncio
async def test_get_document_warms_the_cache_before_fetching(make_paperless: Any) -> None:
    """Order matters: pypaperless enriches custom fields while parsing the document."""
    paperless = make_paperless()
    paperless.documents.get_result = _doc(1)
    paperless.custom_fields.filter_results = [SimpleNamespace(id=7, name="Status")]
    mcp = build_mcp(make_settings(), paperless)

    await call_tool(mcp, "get_document", document_id=1)

    assert paperless.runtime.cache.custom_fields == {7: paperless.custom_fields.filter_results[0]}


@pytest.mark.asyncio
async def test_get_document_carries_the_custom_field_names(make_paperless: Any) -> None:
    """A bare ``{"field": 1, "value": "EUR6372.00"}`` tells a model nothing.

    The document is built at call time, exactly as pypaperless parses the API
    response: whether the values come back enriched depends on the cache
    already standing when that happens.
    """
    paperless = make_paperless()
    runtime = PaperlessRuntime(SimpleNamespace(), paperless.runtime.cache)
    definitions = [
        CustomField.from_data(
            runtime,
            {
                "id": 1,
                "name": "Gross",
                "data_type": "monetary",
                "extra_data": {"default_currency": "EUR"},
            },
        ),
        CustomField.from_data(
            runtime,
            {
                "id": 2,
                "name": "Phase",
                "data_type": "select",
                "extra_data": {"select_options": [{"id": "opt-1", "label": "Open"}]},
            },
        ),
    ]
    paperless.custom_fields.filter_results = definitions
    paperless.documents.get_result = lambda pk: Document.from_data(
        runtime,
        {
            "id": pk,
            "title": "Bill",
            "custom_fields": [
                {"field": 1, "value": "EUR6372.00"},
                {"field": 2, "value": "opt-1"},
            ],
        },
    )
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "get_document", document_id=1)

    assert result["custom_fields"] == [
        {
            "field": 1,
            "name": "Gross",
            "data_type": "monetary",
            "label": None,
            "value": "EUR6372.00",
        },
        {"field": 2, "name": "Phase", "data_type": "select", "label": "Open", "value": "opt-1"},
    ]
