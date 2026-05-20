"""Tests for system / saved-view tools."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from paperless_mcp import __version__
from tests.conftest import build_mcp, call_tool, make_settings


@pytest.mark.asyncio
async def test_get_paperless_info_returns_version_metadata(make_paperless: Any) -> None:
    paperless = make_paperless()
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "get_paperless_info")
    assert result == {
        "paperless_version": "2.13.0",
        "paperless_api_version": "9",
        "paperless_base_url": "http://test",
        "mcp_server_version": __version__,
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
    rule = SimpleNamespace(rule_type=3, value="42")
    view = SimpleNamespace(
        id=1,
        name="Inbox",
        show_on_dashboard=True,
        show_in_sidebar=True,
        sort_field="created",
        sort_reverse=True,
        owner=None,
        filter_rules=[rule],
    )

    paperless = make_paperless()
    paperless.saved_views.get_result = view
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "get_saved_view", view_id=1)
    assert result["id"] == 1
    assert result["name"] == "Inbox"
    assert result["filter_rules"] == [{"rule_type": 3, "value": "42"}]


@pytest.mark.asyncio
async def test_run_saved_view_no_longer_exposed(make_paperless: Any) -> None:
    paperless = make_paperless()
    mcp = build_mcp(make_settings(), paperless)
    assert "run_saved_view" not in mcp._tool_manager._tools
