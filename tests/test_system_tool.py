"""Tests for system, saved-view, task and share-link tools."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from paperless_mcp import __version__
from tests.conftest import FakeService, build_mcp, call_tool, make_settings


@pytest.mark.asyncio
async def test_get_paperless_info_returns_version_metadata(make_paperless: Any) -> None:
    paperless = make_paperless()
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "get_paperless_info")
    assert result == {
        "paperless_version": "3.0.0",
        "paperless_api_version": 10,
        "paperless_base_url": "http://test",
        "mcp_server_version": __version__,
        "readonly": False,
        "deletes_enabled": True,
    }


@pytest.mark.asyncio
async def test_get_statistics_serializes_pydantic_model(make_paperless: Any) -> None:
    class _Stats:
        def model_dump(self, mode: str = "python") -> dict[str, int]:
            return {"documents_total": 42, "documents_inbox": 3}

    paperless = make_paperless()

    async def _stats() -> Any:
        return _Stats()

    paperless.statistics = _stats
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "get_statistics")
    assert result == {"documents_total": 42, "documents_inbox": 3}


@pytest.mark.asyncio
async def test_get_saved_view_returns_rules(make_paperless: Any) -> None:
    view = SimpleNamespace(
        id=1,
        name="Inbox",
        sort_field="created",
        sort_reverse=True,
        page_size=25,
        display_mode=None,
        display_fields=None,
        owner=None,
        filter_rules=[SimpleNamespace(rule_type=3, value="42")],
    )
    paperless = make_paperless()
    paperless.saved_views.get_result = view
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "get_saved_view", view_id=1)
    assert result["id"] == 1
    assert result["name"] == "Inbox"
    assert result["filter_rules"] == [{"rule_type": 3, "value": "42"}]


@pytest.mark.asyncio
async def test_run_saved_view_is_not_exposed(make_paperless: Any) -> None:
    mcp = build_mcp(make_settings(), make_paperless())
    assert "run_saved_view" not in mcp._tool_manager._tools


def _task(task_id: int, status: str = "success") -> SimpleNamespace:
    return SimpleNamespace(
        id=task_id,
        task_id=f"uuid-{task_id}",
        task_type="consume_file",
        task_type_display="Consume file",
        status=status,
        status_display=status.title(),
        trigger_source="api_upload",
        acknowledged=False,
        date_created=None,
        date_started=None,
        date_done=None,
        duration_seconds=None,
        input_data=None,
        result_data=None,
        related_document_ids=[],
        owner=None,
    )


@pytest.mark.asyncio
async def test_list_active_tasks_windows_the_plain_list(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.tasks.active_results = [_task(i) for i in range(1, 4)]
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "list_active_tasks", limit=2)
    assert [t["id"] for t in result["tasks"]] == [1, 2]
    assert result["total"] == 3
    assert result["has_more"] is True


@pytest.mark.asyncio
async def test_list_tasks_requests_newest_first(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.tasks.filter_results = [_task(i) for i in range(1, 3)]
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "list_tasks", status="failure")
    assert [t["id"] for t in result["tasks"]] == [1, 2]
    assert paperless.tasks.filter_calls == [{"ordering": "-date_created", "status": "failure"}]


@pytest.mark.asyncio
async def test_get_task_accepts_a_uuid(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.tasks.get_result = _task(9)
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "get_task", task_id="c0ffee-uuid")
    assert result["id"] == 9
    assert paperless.tasks.get_calls == [("c0ffee-uuid", {})]


@pytest.mark.asyncio
async def test_get_task_accepts_a_numeric_pk(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.tasks.get_result = _task(9)
    mcp = build_mcp(make_settings(), paperless)

    await call_tool(mcp, "get_task", task_id="9")
    assert paperless.tasks.get_calls == [(9, {})]


def _link(link_id: int, document: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        id=link_id,
        document=document,
        slug=f"slug{link_id}",
        file_version="archive",
        expiration=None,
        created=None,
    )


@pytest.mark.asyncio
async def test_list_share_links_uses_the_document_scoped_endpoint(make_paperless: Any) -> None:
    """The share-links collection has no document filter in Paperless."""
    paperless = make_paperless()
    paperless.documents.share_links = _returns([_link(1), _link(2)])
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "list_share_links", document_id=1)
    assert [link["id"] for link in result["share_links"]] == [1, 2]
    assert paperless.share_links.filter_calls == []


@pytest.mark.asyncio
async def test_list_share_links_pages_the_collection(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.share_links.filter_results = [_link(i) for i in range(1, 4)]
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "list_share_links", limit=2)
    assert [link["id"] for link in result["share_links"]] == [1, 2]
    assert result["has_more"] is True


@pytest.mark.asyncio
async def test_create_share_link_defaults_to_the_archive_version(make_paperless: Any) -> None:
    """ShareLinkDraft requires file_version, so it cannot be left unset."""
    paperless = make_paperless()
    paperless.share_links = FakeService(save_returns=5, get_result=_link(5))
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "create_share_link", document_id=1)
    assert result["share_link"]["id"] == 5
    draft = paperless.share_links.save_calls[0]
    assert draft.file_version == "archive"
    assert draft.document == 1
    assert draft.expiration is None


@pytest.mark.asyncio
async def test_create_share_link_rejects_an_unknown_file_version(make_paperless: Any) -> None:
    paperless = make_paperless()
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "create_share_link", document_id=1, file_version="thumbnail")
    assert result["error"] == "invalid_argument"
    assert paperless.share_links.save_calls == []


def _returns(value: Any) -> Any:
    async def _call(*_args: Any, **_kwargs: Any) -> Any:
        return value

    return _call
