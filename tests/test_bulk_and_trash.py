"""Tests for the bulk-edit and trash tools."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pypaperless.exceptions import BulkEditError, BulkEditPagesError

from tests.conftest import (
    BulkRecorder,
    FakeService,
    build_mcp,
    call_tool,
    document,
    make_settings,
    named,
)


class _FailingBulk(BulkRecorder):
    """Records like BulkRecorder, but the named operation raises after recording."""

    def __init__(self, failing: str) -> None:
        super().__init__()
        self._failing = failing

    def __getattr__(self, name: str) -> Any:
        if name != self._failing:
            return super().__getattr__(name)

        async def _fail(*args: Any, **kwargs: Any) -> None:
            self.calls.append((name, args, kwargs))
            raise BulkEditError("Paperless refused it")

        return _fail


async def test_bulk_edit_documents_runs_only_passed_operations(make_paperless: Any) -> None:
    paperless = make_paperless()
    recorder = BulkRecorder()
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


async def test_bulk_edit_documents_reports_what_landed_before_a_failure(
    make_paperless: Any,
) -> None:
    """The docstring's promise: ``applied`` survives into the error result.

    ``set_correspondent`` has already landed on every document when
    ``set_document_type`` fails; a bare error would hide that and invite either
    a blind retry of all the operations or none.
    """
    paperless = make_paperless()
    recorder = _FailingBulk("set_document_type")
    paperless.documents.bulk_edit = recorder
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(
        mcp,
        "bulk_edit_documents",
        document_ids=[1, 2],
        correspondent_id=5,
        document_type_id=7,
        add_tag_ids=[10],
    )

    assert result["error"] == "bulk_edit_failed"
    assert result["applied"] == ["correspondent"]
    assert result["failed"] == "document_type"
    assert result["document_ids"] == [1, 2]
    # The failure stopped the sequence: the tag edit was never attempted.
    assert [name for name, _, _ in recorder.calls] == ["set_correspondent", "set_document_type"]


async def test_bulk_edit_documents_rejects_a_tag_added_and_removed(make_paperless: Any) -> None:
    """Case-variant names resolve to one ID, and what add+remove of that ID means
    is Paperless server internals — refused rather than forwarded.
    """
    paperless = make_paperless()
    paperless.tags.filter_results = named(**{"7": "Steuer"})
    recorder = BulkRecorder()
    paperless.documents.bulk_edit = recorder
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(
        mcp,
        "bulk_edit_documents",
        document_ids=[1],
        add_tag_names=["Steuer"],
        remove_tag_names=["steuer"],
    )

    assert result["error"] == "invalid_argument"
    assert "7" in result["cause"]
    assert recorder.calls == []


async def test_bulk_edit_documents_rejects_a_no_op(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.bulk_edit = BulkRecorder()
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "bulk_edit_documents", document_ids=[1])
    assert result["error"] == "invalid_argument"


async def test_bulk_edit_documents_rejects_empty_ids(make_paperless: Any) -> None:
    paperless = make_paperless()
    recorder = BulkRecorder()
    paperless.documents.bulk_edit = recorder
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "bulk_edit_documents", document_ids=[], correspondent_id=1)
    assert result["error"] == "invalid_argument"
    assert recorder.calls == []


async def test_bulk_merge_documents_forwards_options(make_paperless: Any) -> None:
    paperless = make_paperless()
    recorder = BulkRecorder()
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


async def test_bulk_merge_documents_needs_two_documents(make_paperless: Any) -> None:
    paperless = make_paperless()
    recorder = BulkRecorder()
    paperless.documents.bulk_edit = recorder
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "bulk_merge_documents", document_ids=[1])
    assert result["error"] == "invalid_argument"
    assert recorder.calls == []


async def test_bulk_rotate_rejects_odd_angles(make_paperless: Any) -> None:
    paperless = make_paperless()
    recorder = BulkRecorder()
    paperless.documents.bulk_edit = recorder
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "bulk_rotate_documents", document_ids=[1], degrees=45)
    assert result["error"] == "invalid_argument"
    assert recorder.calls == []


async def test_list_trash_paginates(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.trash.filter_results = [document(i, title=f"t{i}") for i in range(1, 4)]
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


async def test_restore_documents_forwards_ids(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.trash = _Trash()
    mcp = build_mcp(make_settings(), paperless)

    await call_tool(mcp, "restore_documents", document_ids=[7, 8])
    assert paperless.trash.restored == [[7, 8]]


async def test_restore_documents_rejects_empty_ids(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.trash = _Trash()
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "restore_documents", document_ids=[])
    assert result["error"] == "invalid_argument"
    assert paperless.trash.restored == []


async def test_empty_trash_purges_everything_when_no_ids(make_paperless: Any) -> None:
    """Only a genuinely omitted argument means "everything"."""
    paperless = make_paperless()
    paperless.trash = _Trash()
    mcp = build_mcp(make_settings(enable_delete=True), paperless)

    result = await call_tool(mcp, "empty_trash")
    # `purged` is always a list; `purged_all` is what says the whole trash went.
    assert result == {"purged": [], "purged_all": True}
    assert paperless.trash.emptied == [None]


async def test_empty_trash_purges_selected_ids(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.trash = _Trash()
    mcp = build_mcp(make_settings(enable_delete=True), paperless)

    result = await call_tool(mcp, "empty_trash", document_ids=[3, 4])
    assert result == {"purged": [3, 4], "purged_all": False}
    assert paperless.trash.emptied == [[3, 4]]


async def test_empty_trash_refuses_an_empty_id_list(make_paperless: Any) -> None:
    """An explicit ``[]`` must not purge the whole trash.

    It is exactly what a model passes after building its selection from a
    search that matched nothing — and pypaperless sends ``None`` and ``[]``
    alike as the purge-everything request, which nothing can undo.
    """
    paperless = make_paperless()
    paperless.trash = _Trash()
    mcp = build_mcp(make_settings(enable_delete=True), paperless)

    result = await call_tool(mcp, "empty_trash", document_ids=[])

    assert result["error"] == "invalid_argument"
    assert "empty" in result["cause"]
    assert paperless.trash.emptied == []


def _with_pages(make_paperless: Any, page_count: int | None) -> tuple[Any, BulkRecorder]:
    paperless = make_paperless()
    recorder = BulkRecorder()
    paperless.documents = FakeService(get_result=SimpleNamespace(page_count=page_count))
    paperless.documents.bulk_edit = recorder
    return paperless, recorder


async def test_split_document_partitions_the_pages(make_paperless: Any) -> None:
    paperless, recorder = _with_pages(make_paperless, 5)
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "split_document", document_id=42, page_groups=[[1, 2], [3, 4, 5]])

    assert recorder.calls == [("split", (42, [[1, 2], [3, 4, 5]]), {"delete_originals": False})]
    assert result["documents_created"] == 2
    assert result["document_id"] == 42


async def test_split_document_takes_the_page_count_without_a_lookup(make_paperless: Any) -> None:
    paperless, recorder = _with_pages(make_paperless, 5)
    mcp = build_mcp(make_settings(), paperless)

    await call_tool(mcp, "split_document", document_id=42, page_groups=[[1], [2]], page_count=2)

    assert paperless.documents.get_calls == []
    assert len(recorder.calls) == 1


async def test_split_document_refuses_to_discard_the_pages_left_out(make_paperless: Any) -> None:
    """Paperless drops unlisted pages silently; a short list must not lose sheets."""
    paperless, recorder = _with_pages(make_paperless, 5)
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "split_document", document_id=42, page_groups=[[1], [2]])

    assert result["error"] == "invalid_argument"
    assert "[3, 4, 5]" in result["cause"]
    assert "delete_document_pages" in result["cause"]
    assert recorder.calls == []


@pytest.mark.parametrize(
    ("page_groups", "reason"),
    [
        ([[1, 2, 3, 4, 5]], "at least two"),
        ([[1, 2], []], "empty group"),
        ([[1, 2], [2, 3, 4, 5]], "more than one group"),
        ([[1, 2], [3, 4, 5, 9]], "out of range"),
    ],
)
async def test_split_document_rejects_a_malformed_partition(
    make_paperless: Any, page_groups: list[list[int]], reason: str
) -> None:
    paperless, recorder = _with_pages(make_paperless, 5)
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "split_document", document_id=42, page_groups=page_groups)

    assert result["error"] == "invalid_argument"
    assert reason in result["cause"]
    assert recorder.calls == []


async def test_split_document_reports_a_missing_page_count(make_paperless: Any) -> None:
    paperless, recorder = _with_pages(make_paperless, None)
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "split_document", document_id=42, page_groups=[[1], [2]])

    assert result["error"] == "invalid_argument"
    assert "no page count" in result["cause"]
    assert recorder.calls == []


async def test_delete_document_pages_passes_through(make_paperless: Any) -> None:
    paperless, recorder = _with_pages(make_paperless, 5)
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "delete_document_pages", document_id=42, pages=[4, 2, 2])

    assert recorder.calls == [("delete_pages", (42, [4, 2, 2]), {"page_count": None})]
    assert result == {"document_id": 42, "pages_removed": [2, 4]}


async def test_delete_document_pages_forwards_a_known_page_count(make_paperless: Any) -> None:
    paperless, recorder = _with_pages(make_paperless, 5)
    mcp = build_mcp(make_settings(), paperless)

    await call_tool(mcp, "delete_document_pages", document_id=42, pages=[2], page_count=5)

    assert recorder.calls == [("delete_pages", (42, [2]), {"page_count": 5})]
    # The library owns the lookup for this one, so the tool must not add its own.
    assert paperless.documents.get_calls == []


async def test_delete_document_pages_rejects_an_empty_selection(make_paperless: Any) -> None:
    paperless, recorder = _with_pages(make_paperless, 5)
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "delete_document_pages", document_id=42, pages=[])

    assert result["error"] == "invalid_argument"
    assert recorder.calls == []


async def test_a_rejected_page_selection_is_an_invalid_argument(make_paperless: Any) -> None:
    """BulkEditPagesError is a DocumentError, so the catch-all would call it a server fault."""
    paperless, _ = _with_pages(make_paperless, 5)

    async def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise BulkEditPagesError("delete_pages() must keep at least one page.")

    paperless.documents.bulk_edit = SimpleNamespace(delete_pages=_boom)
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "delete_document_pages", document_id=42, pages=[1, 2, 3, 4, 5])

    assert result["error"] == "invalid_argument"
    assert "keep at least one page" in result["cause"]
