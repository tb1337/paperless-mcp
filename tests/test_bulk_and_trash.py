"""Tests for the bulk-edit and trash tools."""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import build_mcp, call_tool, make_settings


# ----------------------------------------------------------------------- bulk
class _BulkRecorder:
    """Records every bulk-edit call so tests can assert exact ordering."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str):
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
async def test_bulk_edit_documents_with_no_ops_returns_empty_applied(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.bulk_edit = _BulkRecorder()
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "bulk_edit_documents", document_ids=[1])
    assert result == {"document_ids": [1], "applied": []}


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


# ----------------------------------------------------------------------- trash
@pytest.mark.asyncio
async def test_list_trash_paginates(make_paperless: Any) -> None:
    from types import SimpleNamespace

    paperless = make_paperless()
    paperless.trash.filter_results = [
        SimpleNamespace(
            id=i,
            title=f"t{i}",
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
        )
        for i in range(1, 4)
    ]
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "list_trash", limit=2)
    assert [d["id"] for d in result["trashed"]] == [1, 2]
    assert result["has_more"] is True


@pytest.mark.asyncio
async def test_restore_documents_forwards_ids(make_paperless: Any) -> None:
    recorded: list[list[int]] = []

    class _Trash:
        def filter(self, **_kw: Any):
            return _Trash._Empty()

        class _Empty:
            def __aiter__(self):
                async def gen():
                    return
                    yield  # pragma: no cover

                return gen()

        async def restore(self, ids: list[int]) -> None:
            recorded.append(ids)

        async def empty(self, ids: list[int]) -> None:
            recorded.append(ids or ["ALL"])

    paperless = make_paperless()
    paperless.trash = _Trash()
    mcp = build_mcp(make_settings(), paperless)

    await call_tool(mcp, "restore_documents", document_ids=[7, 8])
    assert recorded == [[7, 8]]


@pytest.mark.asyncio
async def test_empty_trash_purges_all_when_no_ids(make_paperless: Any) -> None:
    recorded: list[list[int]] = []

    class _Trash:
        def filter(self, **_kw: Any):
            class _E:
                def __aiter__(self):
                    async def gen():
                        return
                        yield  # pragma: no cover

                    return gen()

            return _E()

        async def restore(self, ids: list[int]) -> None: ...

        async def empty(self, ids: list[int]) -> None:
            recorded.append(list(ids))

    paperless = make_paperless()
    paperless.trash = _Trash()
    mcp = build_mcp(make_settings(enable_delete=True), paperless)

    result = await call_tool(mcp, "empty_trash")
    assert result == {"purged": "all"}
    assert recorded == [[]]
