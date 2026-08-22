"""Tests for system, saved-view, task and share-link tools."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from pypaperless.exceptions import ForbiddenError, ItemNotFoundError
from pypaperless.models.statistics import Statistic
from pypaperless.models.status import Status

from paperless_mcp import __version__
from tests.conftest import (
    FakeService,
    build_mcp,
    call_tool,
    document,
    invoke_tool,
    make_runtime,
    make_settings,
    returns,
    rule,
)


async def test_get_paperless_info_returns_version_metadata(make_paperless: Any) -> None:
    """Every setting that changes what a call does, so it can be read rather than probed.

    Without the three limits, a model cannot tell why a download was refused, why a
    rename made in the web UI is still invisible, or when a slow call will give up -
    and a live check has no way to test those paths at all.
    """
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
        "max_file_bytes": 1024 * 1024,
        "name_cache_ttl": 300.0,
        "request_timeout": 30.0,
    }


async def test_get_statistics_serializes_pydantic_model(make_paperless: Any) -> None:
    """Built from the real Statistic model, not a stand-in that only quacks.

    ``safe_dump`` dispatches on ``BaseModel``, so a duck-typed fake carrying only
    a ``model_dump`` method would take the ``str(obj)`` fallback here and prove
    nothing about the endpoint this tool actually calls.
    """
    paperless = make_paperless()
    runtime = make_runtime()
    stats = Statistic.from_data(runtime, {"documents_total": 42, "documents_inbox": 3})

    async def _stats() -> Any:
        return stats

    paperless.statistics = _stats
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "get_statistics")
    assert result["documents_total"] == 42
    assert result["documents_inbox"] == 3


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


def _view(
    rules: list[SimpleNamespace] | None = None,
    *,
    sort_field: str | None = "created",
    sort_reverse: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=7,
        name="Unpaid invoices",
        sort_field=sort_field,
        sort_reverse=sort_reverse,
        page_size=25,
        display_mode=None,
        display_fields=None,
        owner=None,
        filter_rules=rules or [],
    )


async def _run_view(make_paperless: Any, view: SimpleNamespace, **kwargs: Any) -> Any:
    paperless = make_paperless()
    paperless.saved_views.get_result = view
    paperless.documents.filter_results = [document(1, title="Doc 1"), document(2, title="Doc 2")]
    mcp = build_mcp(make_settings(), paperless)
    result = await call_tool(mcp, "run_saved_view", view_id=7, **kwargs)
    return result, paperless


async def test_run_saved_view_executes_the_view(make_paperless: Any) -> None:
    view = _view([rule(3, "42"), rule(5, "true")])

    result, paperless = await _run_view(make_paperless, view)

    assert [d["id"] for d in result["documents"]] == [1, 2]
    assert result["view_id"] == 7
    assert result["view_name"] == "Unpaid invoices"
    assert paperless.documents.filter_calls == [
        {
            "correspondent__id": "42",
            "is_in_inbox": 1,
            "ordering": "-created",
            "truncate_content": "true",
        }
    ]


async def test_run_saved_view_reports_the_query_it_ran(make_paperless: Any) -> None:
    """The translation is only trustworthy if the caller can see its result."""
    result, _ = await _run_view(make_paperless, _view([rule(19, "invoice")]))

    assert result["filters"] == {"title_content": "invoice", "ordering": "-created"}


async def test_run_saved_view_joins_repeated_rules_of_one_type(make_paperless: Any) -> None:
    """Paperless stores ``tags__id__all=1,2,3`` as three separate rules."""
    view = _view([rule(6, "1"), rule(6, "2"), rule(6, "3")], sort_field=None)

    _, paperless = await _run_view(make_paperless, view)

    assert paperless.documents.filter_calls == [
        {"tags__id__all": "1,2,3", "truncate_content": "true"}
    ]


async def test_run_saved_view_translates_the_isnull_sentinels(make_paperless: Any) -> None:
    """A relation encodes "is (not) set" in the value: ``None`` / ``"-1"``."""
    view = _view([rule(3, None), rule(25, "-1")], sort_field=None)

    _, paperless = await _run_view(make_paperless, view)

    assert paperless.documents.filter_calls == [
        {"correspondent__isnull": 1, "storage_path__isnull": 0, "truncate_content": "true"}
    ]


async def test_run_saved_view_sends_booleans_as_digits(make_paperless: Any) -> None:
    view = _view([rule(7, "true"), rule(41, "false")], sort_field=None)

    _, paperless = await _run_view(make_paperless, view)

    assert paperless.documents.filter_calls == [
        {"is_tagged": 1, "has_custom_fields": 0, "truncate_content": "true"}
    ]


async def test_run_saved_view_honours_ascending_sort(make_paperless: Any) -> None:
    view = _view(sort_field="title", sort_reverse=False)

    _, paperless = await _run_view(make_paperless, view)

    assert paperless.documents.filter_calls == [{"ordering": "title", "truncate_content": "true"}]


async def test_run_saved_view_without_rules_matches_everything(make_paperless: Any) -> None:
    """A view can legitimately be nothing but a sort order."""
    result, paperless = await _run_view(make_paperless, _view(sort_field=None))

    assert paperless.documents.filter_calls == [{"truncate_content": "true"}]
    assert result["total"] == 2


async def test_run_saved_view_refuses_an_untranslatable_rule(make_paperless: Any) -> None:
    """Dropping a filter would answer with documents the view excludes."""
    view = _view([rule(3, "42"), rule(999, "x")])

    result, paperless = await _run_view(make_paperless, view)

    assert result["error"] == "unsupported_filter_rule"
    assert result["unsupported_rule_types"] == [999]
    assert result["view_name"] == "Unpaid invoices"
    assert paperless.documents.filter_calls == []


async def test_run_saved_view_paginates(make_paperless: Any) -> None:
    result, _ = await _run_view(make_paperless, _view(sort_field=None), limit=1)

    assert [d["id"] for d in result["documents"]] == [1]
    assert result["has_more"] is True


async def test_run_saved_view_reports_a_missing_view(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.saved_views.get_raises = ItemNotFoundError("no such view")
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "run_saved_view", view_id=404)
    assert result["error"] == "not_found"


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


async def test_list_active_tasks_windows_the_plain_list(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.tasks.active_results = [_task(i) for i in range(1, 4)]
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "list_active_tasks", limit=2)
    assert [t["id"] for t in result["tasks"]] == [1, 2]
    assert result["total"] == 3
    assert result["has_more"] is True


async def test_list_tasks_requests_newest_first(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.tasks.filter_results = [_task(i) for i in range(1, 3)]
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "list_tasks", status="failure")
    assert [t["id"] for t in result["tasks"]] == [1, 2]
    assert paperless.tasks.filter_calls == [{"ordering": "-date_created", "status": "failure"}]


async def test_get_task_accepts_a_uuid(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.tasks.get_result = _task(9)
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "get_task", task_id="c0ffee-uuid")
    assert result["id"] == 9
    assert paperless.tasks.get_calls == [("c0ffee-uuid", {})]


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


async def test_list_share_links_uses_the_document_scoped_endpoint(make_paperless: Any) -> None:
    """The share-links collection has no document filter in Paperless."""
    paperless = make_paperless()
    paperless.documents.share_links = returns([_link(1), _link(2)])
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "list_share_links", document_id=1)
    assert [link["id"] for link in result["share_links"]] == [1, 2]
    assert paperless.share_links.filter_calls == []


async def test_list_share_links_pages_the_collection(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.share_links.filter_results = [_link(i) for i in range(1, 4)]
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "list_share_links", limit=2)
    assert [link["id"] for link in result["share_links"]] == [1, 2]
    assert result["has_more"] is True


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


async def test_create_share_link_rejects_an_unknown_file_version(make_paperless: Any) -> None:
    """Refused by the published schema now, so it never reaches the tool.

    A `ToolError` rather than `{"error": "invalid_argument"}`: pydantic validates the
    arguments before `safe_tool` can wrap anything, and the message it raises names
    the allowed values. The trade is deliberate - a schema-aware client sees the enum
    and does not send this in the first place.
    """
    paperless = make_paperless()
    mcp = build_mcp(make_settings(), paperless)

    with pytest.raises(ToolError, match="'archive'"):
        await invoke_tool(mcp, "create_share_link", document_id=1, file_version="thumbnail")

    assert paperless.share_links.save_calls == []


def _status(**tasks: Any) -> Status:
    return Status.model_validate(
        {
            "pngx_version": "3.0.1",
            "storage": {"total": 100, "available": 40},
            "database": {"type": "postgres", "status": tasks.pop("database_status", "OK")},
            "tasks": {"celery_status": "OK", **tasks},
        }
    )


async def test_get_system_status_reports_a_healthy_archive(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.status = returns(_status(redis_status="OK"))
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "get_system_status")

    assert result["health"] == "ok"
    assert result["problems"] == []
    # The untouched payload travels alongside the verdict.
    assert result["pngx_version"] == "3.0.1"
    assert result["storage"]["available"] == 40


async def test_get_system_status_names_the_failing_subsystems(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.status = returns(
        _status(
            redis_status="ERROR",
            redis_error="connection refused",
            index_status="WARNING",
            index_error="stale",
        )
    )
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "get_system_status")

    assert result["health"] == "error"
    assert result["problems"] == [
        {"subsystem": "redis", "status": "ERROR", "error": "connection refused"},
        {"subsystem": "index", "status": "WARNING", "error": "stale"},
    ]


async def test_get_system_status_reports_a_warning_without_an_error(make_paperless: Any) -> None:
    paperless = make_paperless()
    # sanity_check is one of the two subsystems Status.has_errors ignores.
    paperless.status = returns(_status(sanity_check_status="WARNING"))
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "get_system_status")

    assert result["health"] == "warning"
    assert [p["subsystem"] for p in result["problems"]] == ["sanity_check"]


async def test_get_system_status_turns_a_missing_permission_into_an_error(
    make_paperless: Any,
) -> None:
    paperless = make_paperless()

    async def _forbidden() -> Any:
        raise ForbiddenError(httpx.Response(403, request=httpx.Request("GET", "http://test/")))

    paperless.status = _forbidden
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "get_system_status")
    assert result["error"] == "forbidden"
