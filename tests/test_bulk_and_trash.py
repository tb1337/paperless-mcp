"""Tests for the bulk-edit and trash tools."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tests.conftest import FakeService, build_mcp, call_tool, make_settings


class _BulkRecorder:
    """Records every bulk-edit call so tests can assert exact ordering."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        async def _record(*args: Any, **kwargs: Any) -> None:
            self.calls.append((name, args, kwargs))

        return _record


@pytest.mark.asyncio
async def test_bulk_edit_documents_runs_only_passed_operations(make_paperless: Any) -> None:
    paperless = make_paperless()
    recorder = _BulkRecorder()
    paperless.documents.bulk_edit = recorder
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(
        mcp,
        "bulk_edit_documents",
        document_ids=[1, 2, 3],
        correspondent_id=5,
        add_tag_ids=[10, 11],
    )
    assert result["applied"] == ["correspondent", "tags"]
    assert recorder.calls[0][0] == "set_correspondent"
    assert recorder.calls[0][1] == ([1, 2, 3], 5)
    assert recorder.calls[1][0] == "modify_tags"
    assert recorder.calls[1][2] == {"add_tags": [10, 11], "remove_tags": []}


@pytest.mark.asyncio
async def test_bulk_edit_documents_rejects_a_no_op(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.bulk_edit = _BulkRecorder()
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "bulk_edit_documents", document_ids=[1])
    assert result["error"] == "invalid_argument"


@pytest.mark.asyncio
async def test_bulk_edit_documents_rejects_empty_ids(make_paperless: Any) -> None:
    paperless = make_paperless()
    recorder = _BulkRecorder()
    paperless.documents.bulk_edit = recorder
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "bulk_edit_documents", document_ids=[], correspondent_id=1)
    assert result["error"] == "invalid_argument"
    assert recorder.calls == []


@pytest.mark.asyncio
async def test_bulk_merge_documents_forwards_options(make_paperless: Any) -> None:
    paperless = make_paperless()
    recorder = _BulkRecorder()
    paperless.documents.bulk_edit = recorder
    mcp = build_mcp(make_settings(), paperless)

    await call_tool(
        mcp,
        "bulk_merge_documents",
        document_ids=[1, 2],
        metadata_from_id=2,
        delete_originals=True,
    )
    name, args, kwargs = recorder.calls[0]
    assert name == "merge"
    assert args == ([1, 2],)
    assert kwargs == {"metadata_document_id": 2, "delete_originals": True}


@pytest.mark.asyncio
async def test_bulk_merge_documents_needs_two_documents(make_paperless: Any) -> None:
    paperless = make_paperless()
    recorder = _BulkRecorder()
    paperless.documents.bulk_edit = recorder
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "bulk_merge_documents", document_ids=[1])
    assert result["error"] == "invalid_argument"
    assert recorder.calls == []


@pytest.mark.asyncio
async def test_bulk_rotate_rejects_odd_angles(make_paperless: Any) -> None:
    paperless = make_paperless()
    recorder = _BulkRecorder()
    paperless.documents.bulk_edit = recorder
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "bulk_rotate_documents", document_ids=[1], degrees=45)
    assert result["error"] == "invalid_argument"
    assert recorder.calls == []


def _trashed(doc_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=doc_id,
        title=f"t{doc_id}",
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
    )


@pytest.mark.asyncio
async def test_list_trash_paginates(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.trash.filter_results = [_trashed(i) for i in range(1, 4)]
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "list_trash", limit=2)
    assert [d["id"] for d in result["trashed"]] == [1, 2]
    assert result["has_more"] is True
    assert result["total"] == 3


class _Trash(FakeService):
    """Trash service stub recording restore/empty calls."""

    def __init__(self) -> None:
        super().__init__()
        self.restored: list[list[int]] = []
        self.emptied: list[list[int] | None] = []

    async def restore(self, documents: list[int]) -> None:
        self.restored.append(documents)

    async def empty(self, documents: list[int] | None = None) -> None:
        self.emptied.append(documents)


@pytest.mark.asyncio
async def test_restore_documents_forwards_ids(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.trash = _Trash()
    mcp = build_mcp(make_settings(), paperless)

    await call_tool(mcp, "restore_documents", document_ids=[7, 8])
    assert paperless.trash.restored == [[7, 8]]


@pytest.mark.asyncio
async def test_restore_documents_rejects_empty_ids(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.trash = _Trash()
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "restore_documents", document_ids=[])
    assert result["error"] == "invalid_argument"
    assert paperless.trash.restored == []


@pytest.mark.asyncio
async def test_empty_trash_purges_everything_when_no_ids(make_paperless: Any) -> None:
    """An empty list would purge *nothing*, so the argument must stay None."""
    paperless = make_paperless()
    paperless.trash = _Trash()
    mcp = build_mcp(make_settings(enable_delete=True), paperless)

    result = await call_tool(mcp, "empty_trash")
    assert result == {"purged": "all"}
    assert paperless.trash.emptied == [None]


@pytest.mark.asyncio
async def test_empty_trash_purges_selected_ids(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.trash = _Trash()
    mcp = build_mcp(make_settings(enable_delete=True), paperless)

    result = await call_tool(mcp, "empty_trash", document_ids=[3, 4])
    assert result == {"purged": [3, 4]}
    assert paperless.trash.emptied == [[3, 4]]
