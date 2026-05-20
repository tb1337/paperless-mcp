"""Smoke tests for tool registration under different visibility modes."""

from __future__ import annotations

import pytest

from paperless_mcp.config import Settings
from paperless_mcp.server import build_mcp


def _make_settings(*, readonly: bool, enable_delete: bool) -> Settings:
    return Settings(
        paperless_url="http://paperless:8000",
        paperless_token="dummy",
        auth_token=None,
        host="0.0.0.0",
        port=8000,
        readonly=readonly,
        enable_delete=enable_delete,
        max_file_bytes=25_000_000,
    )


async def _tool_names(settings: Settings) -> set[str]:
    mcp = build_mcp(settings)
    tools = await mcp.list_tools()
    return {t.name for t in tools}


@pytest.mark.asyncio
async def test_readonly_hides_writes_and_deletes() -> None:
    names = await _tool_names(_make_settings(readonly=True, enable_delete=True))
    # Reads must be present.
    assert "search_documents" in names
    assert "get_document" in names
    assert "list_tags" in names
    assert "get_saved_view" in names
    # Writes/deletes must be absent.
    for forbidden in (
        "upload_document",
        "update_document",
        "create_tag",
        "delete_tag",
        "delete_document",
        "empty_trash",
    ):
        assert forbidden not in names, f"{forbidden} should be hidden in readonly mode"


@pytest.mark.asyncio
async def test_default_exposes_writes_but_not_deletes() -> None:
    names = await _tool_names(_make_settings(readonly=False, enable_delete=False))
    assert "upload_document" in names
    assert "update_document" in names
    assert "create_tag" in names
    # Deletes hidden by default.
    assert "delete_document" not in names
    assert "delete_tag" not in names
    assert "empty_trash" not in names


@pytest.mark.asyncio
async def test_enable_delete_exposes_deletes() -> None:
    names = await _tool_names(_make_settings(readonly=False, enable_delete=True))
    assert "delete_document" in names
    assert "delete_tag" in names
    assert "delete_correspondent" in names
    assert "delete_document_type" in names
    assert "delete_storage_path" in names
    assert "delete_custom_field" in names
    assert "delete_share_link" in names
    assert "empty_trash" in names
