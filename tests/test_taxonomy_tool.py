"""Tests for the taxonomy CRUD tools."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pypaperless.models.types import CustomFieldType, MatchingAlgorithm

from tests.conftest import build_mcp, call_tool, make_settings, tool_session


def _tag(tag_id: int, name: str, color: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=tag_id,
        name=name,
        slug=name.lower(),
        color=color,
        text_color=None,
        match=None,
        matching_algorithm=None,
        is_insensitive=None,
        is_inbox_tag=False,
        parent=None,
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
    assert page["total"] == 5
    assert paperless.tags.filter_calls == [{"name__icontains": "t"}]


@pytest.mark.asyncio
async def test_create_tag_fills_the_required_draft_fields(make_paperless: Any) -> None:
    """TagDraft requires colour and the full matching triple, not just a name."""
    paperless = make_paperless()
    paperless.tags.save_returns = 77
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "create_tag", name="Invoice", color="#abcdef")
    assert result == {"tag": {"id": 77, "name": "Invoice"}}

    draft = paperless.tags.save_calls[0]
    assert draft.name == "Invoice"
    assert draft.color == "#abcdef"
    assert draft.is_inbox_tag is False
    assert draft.match == ""
    assert draft.matching_algorithm is MatchingAlgorithm.NONE
    assert draft.is_insensitive is True


@pytest.mark.asyncio
async def test_create_tag_defaults_the_colour(make_paperless: Any) -> None:
    paperless = make_paperless()
    mcp = build_mcp(make_settings(), paperless)

    await call_tool(mcp, "create_tag", name="Invoice")
    assert paperless.tags.save_calls[0].color.startswith("#")


@pytest.mark.asyncio
async def test_create_tag_converts_the_matching_algorithm(make_paperless: Any) -> None:
    paperless = make_paperless()
    mcp = build_mcp(make_settings(), paperless)

    await call_tool(mcp, "create_tag", name="Invoice", match="acme", matching_algorithm=6)
    assert paperless.tags.save_calls[0].matching_algorithm is MatchingAlgorithm.AUTO


@pytest.mark.asyncio
async def test_create_tag_rejects_an_unknown_matching_algorithm(make_paperless: Any) -> None:
    paperless = make_paperless()
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "create_tag", name="Invoice", matching_algorithm=99)
    assert result["error"] == "invalid_argument"
    assert paperless.tags.save_calls == []


@pytest.mark.asyncio
async def test_update_tag_only_sets_passed_fields(make_paperless: Any) -> None:
    tag = _tag(5, "Old", "#000000")
    paperless = make_paperless()
    paperless.tags.get_result = tag
    mcp = build_mcp(make_settings(), paperless)

    await call_tool(mcp, "update_tag", tag_id=5, name="New")
    assert tag.name == "New"
    assert tag.color == "#000000"  # unchanged
    assert tag.match is None  # create-time defaults must not leak into updates
    assert paperless.tags.update_calls == [tag]


@pytest.mark.asyncio
async def test_delete_tag_hidden_without_enable_delete(make_paperless: Any) -> None:
    mcp = build_mcp(make_settings(enable_delete=False), make_paperless())
    assert "delete_tag" not in mcp._tool_manager._tools


@pytest.mark.asyncio
async def test_delete_tag_fetches_lazily(make_paperless: Any) -> None:
    tag = _tag(5, "Old")
    paperless = make_paperless()
    paperless.tags.get_result = tag
    mcp = build_mcp(make_settings(enable_delete=True), paperless)

    result = await call_tool(mcp, "delete_tag", tag_id=5)
    assert result == {"tag_id": 5, "deleted": True}
    assert paperless.tags.get_calls == [(5, {"lazy": True})]
    assert paperless.tags.delete_calls == [{"obj": tag, "args": ()}]


@pytest.mark.asyncio
async def test_create_correspondent_fills_matching_defaults(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.correspondents.save_returns = 4
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "create_correspondent", name="ACME")
    assert result == {"correspondent": {"id": 4, "name": "ACME"}}
    draft = paperless.correspondents.save_calls[0]
    assert draft.name == "ACME"
    assert draft.matching_algorithm is MatchingAlgorithm.NONE


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


@pytest.mark.asyncio
async def test_create_custom_field_converts_the_data_type(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.custom_fields.save_returns = 9
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "create_custom_field", name="Amount", data_type="monetary")
    assert result["custom_field"] == {"id": 9, "name": "Amount", "data_type": "monetary"}
    assert paperless.custom_fields.save_calls[0].data_type is CustomFieldType.MONETARY


@pytest.mark.asyncio
async def test_create_custom_field_rejects_an_unknown_type(make_paperless: Any) -> None:
    paperless = make_paperless()
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "create_custom_field", name="X", data_type="quaternion")
    assert result["error"] == "invalid_argument"
    assert paperless.custom_fields.save_calls == []


@pytest.mark.asyncio
async def test_list_tags_resolves_the_parent_name(make_paperless: Any) -> None:
    parent = _tag(1, "Contract")
    child = _tag(2, "Electricity")
    child.parent = 1
    paperless = make_paperless()
    paperless.tags.filter_results = [parent, child]
    mcp = build_mcp(make_settings(), paperless)

    page = await call_tool(mcp, "list_tags")

    assert page["tags"][1]["parent_name"] == "Contract"


@pytest.mark.asyncio
async def test_creating_a_tag_invalidates_the_name_snapshot(make_paperless: Any) -> None:
    """A tag created through this server must not stay nameless until the TTL."""
    paperless = make_paperless()
    paperless.tags.filter_results = [_tag(1, "Contract")]
    mcp = build_mcp(make_settings(), paperless)

    async with tool_session(mcp) as call:
        await call("list_tags")
        reads_before = len(paperless.tags.page_calls)

        await call("create_tag", name="Electricity")
        await call("list_tags")

    # The create dropped the snapshot, so the second list had to reload it.
    assert len(paperless.tags.page_calls) > reads_before + 1
