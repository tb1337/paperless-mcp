"""Tests for the taxonomy CRUD tools."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tests.conftest import build_mcp, call_tool, make_settings


def _tag(tag_id: int, name: str, color: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=tag_id,
        name=name,
        slug=name.lower(),
        color=color,
        match=None,
        matching_algorithm=None,
        is_inbox_tag=False,
        document_count=0,
        owner=None,
    )


@pytest.mark.asyncio
async def test_list_tags_paginates_and_filters(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.tags.filter_results = [_tag(i, f"t{i}") for i in range(1, 6)]
    mcp = build_mcp(make_settings(), paperless)

    page = await call_tool(mcp, "list_tags", offset=1, limit=2, name_contains="t")
    assert [t["id"] for t in page["tags"]] == [2, 3]
    assert page["has_more"] is True
    assert paperless.tags.filter_calls == [{"name__icontains": "t"}]


@pytest.mark.asyncio
async def test_create_tag_saves_draft(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.tags.save_returns = 77
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "create_tag", name="Invoice", color="#abcdef")
    assert result == {"tag": {"id": 77, "name": "Invoice"}}
    assert len(paperless.tags.save_calls) == 1
    draft = paperless.tags.save_calls[0]
    assert draft.name == "Invoice"
    assert draft.color == "#abcdef"


@pytest.mark.asyncio
async def test_update_tag_only_sets_passed_fields(make_paperless: Any) -> None:
    tag = _tag(5, "Old", "#000000")
    paperless = make_paperless()
    paperless.tags.get_result = tag
    mcp = build_mcp(make_settings(), paperless)

    await call_tool(mcp, "update_tag", tag_id=5, name="New")
    assert tag.name == "New"
    assert tag.color == "#000000"  # unchanged
    assert paperless.tags.update_calls == [tag]


@pytest.mark.asyncio
async def test_delete_tag_hidden_without_enable_delete(make_paperless: Any) -> None:
    paperless = make_paperless()
    mcp = build_mcp(make_settings(enable_delete=False), paperless)
    assert "delete_tag" not in mcp._tool_manager._tools


@pytest.mark.asyncio
async def test_delete_tag_calls_service(make_paperless: Any) -> None:
    tag = _tag(5, "Old")
    paperless = make_paperless()
    paperless.tags.get_result = tag
    mcp = build_mcp(make_settings(enable_delete=True), paperless)

    result = await call_tool(mcp, "delete_tag", tag_id=5)
    assert result == {"tag_id": 5, "deleted": True}
    assert paperless.tags.delete_calls == [tag]


@pytest.mark.asyncio
async def test_create_storage_path_includes_path(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.storage_paths.save_returns = 12
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(
        mcp, "create_storage_path", name="Tax", path="{{correspondent}}/{{title}}"
    )
    assert result["storage_path"]["id"] == 12
    draft = paperless.storage_paths.save_calls[0]
    assert draft.name == "Tax"
    assert draft.path == "{{correspondent}}/{{title}}"
