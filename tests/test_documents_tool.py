"""Tests for the document tools (mocked PaperlessClient)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tests.conftest import (
    build_mcp,
    call_tool,
    make_settings,
)


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
        archive_serial_number=None,
        original_file_name=None,
        archived_file_name=None,
        owner=None,
        page_count=None,
        mime_type=None,
        is_shared_by_requester=False,
        content="ocr text",
        custom_fields=[],
        notes=[],
    )


# ---------------------------------------------------------------- search_documents
@pytest.mark.asyncio
async def test_search_documents_paginates(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.filter_results = [_doc(i) for i in range(1, 6)]
    mcp = build_mcp(make_settings(), paperless)

    page1 = await call_tool(mcp, "search_documents", offset=0, limit=2)
    assert [d["id"] for d in page1["documents"]] == [1, 2]
    assert page1["has_more"] is True
    assert page1["returned"] == 2

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
        correspondent_id=7,
        created_after="2026-01-01",
        is_in_inbox=True,
    )
    assert paperless.documents.filter_calls == [
        {
            "title__icontains": "bill",
            "tags__id__in": [1, 2],
            "correspondent__id": 7,
            "is_in_inbox": True,
            "created__date__gte": "2026-01-01",
            "query": "invoice",
        }
    ]


# ---------------------------------------------------------------- get_document
@pytest.mark.asyncio
async def test_get_document_returns_detail(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.get_result = _doc(42, "Bill")
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "get_document", document_id=42)
    assert result["id"] == 42
    assert result["title"] == "Bill"
    assert result["content"] == "ocr text"
    assert result["notes"] == []


# ---------------------------------------------------------------- error translation
@pytest.mark.asyncio
async def test_get_document_translates_not_found(make_paperless: Any) -> None:
    class ItemNotFoundError(Exception):
        pass

    paperless = make_paperless()
    paperless.documents.get_raises = ItemNotFoundError("no such id")
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "get_document", document_id=99)
    assert result["error"] == "not_found"
    assert "does not exist" in result["detail"]


# ---------------------------------------------------------------- update_document
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
        clear_fields=["correspondent"],
    )
    assert paperless.documents.update_calls == [doc]
    assert doc.title == "new title"
    assert doc.correspondent is None  # cleared
    assert doc.document_type == 3  # untouched
    assert result["id"] == 5


@pytest.mark.asyncio
async def test_update_document_rejects_unknown_clear_field(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.get_result = _doc(5)
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "update_document", document_id=5, clear_fields=["title"])
    assert result["error"] == "invalid_argument"
    assert "title" in result["detail"]
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


# ---------------------------------------------------------------- upload_document
@pytest.mark.asyncio
async def test_upload_document_passes_content_and_metadata(make_paperless: Any) -> None:
    import base64

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
    )
    assert result == {"task_uuid": "abc-task-uuid", "filename": "invoice.pdf"}
    assert len(paperless.documents.save_calls) == 1
    draft = paperless.documents.save_calls[0]
    assert draft.document == b"%PDF-1.4 fake"
    assert draft.filename == "invoice.pdf"
    assert draft.title == "Invoice"
    assert draft.tags == [1, 2]


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
    assert result["error"] == "invalid_base64"
    assert paperless.documents.save_calls == []


# ---------------------------------------------------------------- delete gating
@pytest.mark.asyncio
async def test_delete_document_hidden_without_enable_delete(make_paperless: Any) -> None:
    paperless = make_paperless()
    mcp = build_mcp(make_settings(enable_delete=False), paperless)
    assert "delete_document" not in mcp._tool_manager._tools


@pytest.mark.asyncio
async def test_delete_document_calls_service(make_paperless: Any) -> None:
    doc = _doc(7)
    paperless = make_paperless()
    paperless.documents.get_result = doc
    mcp = build_mcp(make_settings(enable_delete=True), paperless)

    result = await call_tool(mcp, "delete_document", document_id=7)
    assert result == {"document_id": 7, "deleted": True}
    assert paperless.documents.delete_calls == [doc]


# ---------------------------------------------------------------- download size guard
@pytest.mark.asyncio
async def test_download_document_rejects_oversized(make_paperless: Any) -> None:
    paperless = make_paperless()
    # The mock has docs.download already as a FakeService — replace with a tiny stub
    # that returns a too-large payload.
    big = b"x" * (2 * 1024 * 1024)  # 2 MiB > test max_file_bytes (1 MiB)

    class _Sub:
        async def download(self, _pk: int, *, original: bool = False) -> Any:
            return SimpleNamespace(content=big, content_type="application/pdf")

        async def __call__(self, _pk: int) -> Any:
            return _doc(_pk)

    paperless.documents = _Sub()
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "download_document", document_id=1)
    assert result["error"] == "file_too_large"
    assert result["size_bytes"] == len(big)
    assert result["max_bytes"] == 1024 * 1024
