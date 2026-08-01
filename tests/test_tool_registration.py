"""Smoke tests for tool registration under the different visibility modes."""

from __future__ import annotations

from dataclasses import replace

import pytest

from paperless_mcp import __version__
from paperless_mcp.config import Settings
from paperless_mcp.server import build_mcp
from tests.conftest import make_settings

_READ_TOOLS = frozenset(
    {
        "search_documents",
        "get_document",
        "get_document_content",
        "get_document_metadata",
        "get_document_notes",
        "get_document_history",
        "find_similar_documents",
        "download_document",
        "get_document_thumbnail",
        "list_tags",
        "list_correspondents",
        "list_document_types",
        "list_storage_paths",
        "list_custom_fields",
        "list_share_links",
        "list_saved_views",
        "get_saved_view",
        "list_trash",
        "list_active_tasks",
        "list_tasks",
        "get_task",
        "get_statistics",
        "get_paperless_info",
        "get_document_suggestions",
        "get_document_ai_suggestions",
    }
)

_WRITE_TOOLS = frozenset(
    {
        "upload_document",
        "update_document",
        "add_document_note",
        "bulk_edit_documents",
        "bulk_reprocess_documents",
        "bulk_merge_documents",
        "bulk_rotate_documents",
        "acknowledge_tasks",
        "create_tag",
        "update_tag",
        "create_correspondent",
        "update_correspondent",
        "create_document_type",
        "update_document_type",
        "create_storage_path",
        "update_storage_path",
        "create_custom_field",
        "update_custom_field",
        "create_share_link",
        "restore_documents",
    }
)

_DELETE_TOOLS = frozenset(
    {
        "delete_document",
        "delete_document_note",
        "delete_tag",
        "delete_correspondent",
        "delete_document_type",
        "delete_storage_path",
        "delete_custom_field",
        "delete_share_link",
        "empty_trash",
    }
)


def _settings(*, readonly: bool, enable_delete: bool) -> Settings:
    return replace(make_settings(), readonly=readonly, enable_delete=enable_delete)


async def _tool_names(settings: Settings) -> set[str]:
    return {tool.name for tool in await build_mcp(settings).list_tools()}


@pytest.mark.asyncio
async def test_readonly_hides_writes_and_deletes() -> None:
    names = await _tool_names(_settings(readonly=True, enable_delete=True))
    assert names >= _READ_TOOLS
    assert not (_WRITE_TOOLS | _DELETE_TOOLS) & names


@pytest.mark.asyncio
async def test_default_exposes_writes_but_not_deletes() -> None:
    names = await _tool_names(_settings(readonly=False, enable_delete=False))
    assert names >= _READ_TOOLS
    assert names >= _WRITE_TOOLS
    assert not _DELETE_TOOLS & names


@pytest.mark.asyncio
async def test_enable_delete_exposes_deletes() -> None:
    names = await _tool_names(_settings(readonly=False, enable_delete=True))
    assert names >= _DELETE_TOOLS


@pytest.mark.asyncio
async def test_every_tool_has_a_description() -> None:
    """Claude Desktop shows the description verbatim; an empty one is unusable."""
    tools = await build_mcp(_settings(readonly=False, enable_delete=True)).list_tools()
    missing = [tool.name for tool in tools if not (tool.description or "").strip()]
    assert missing == []


@pytest.mark.asyncio
async def test_tool_schemas_are_json_serializable() -> None:
    """A schema that cannot be serialized breaks the tools/list response."""
    import json

    tools = await build_mcp(_settings(readonly=False, enable_delete=True)).list_tools()
    for tool in tools:
        json.dumps(tool.input_schema)


def test_handshake_reports_our_own_version() -> None:
    """Without this the client sees the MCP SDK's version as the server's."""
    mcp = build_mcp(_settings(readonly=False, enable_delete=False))
    options = mcp._lowlevel_server.create_initialization_options()
    assert options.server_name == "paperless-mcp"
    assert options.server_version == __version__
    assert options.instructions and "Paperless-ngx" in options.instructions


def test_every_tool_receives_the_lifespan_context() -> None:
    """MCPServer injects ``ctx`` only when it recognises the annotation.

    ``ToolContext`` is a parameterized ``Context[...]``; if that ever stops
    being detected, every tool would be called without a client and the ``ctx``
    parameter would leak into the public input schema instead.
    """
    mcp = build_mcp(_settings(readonly=False, enable_delete=True))
    registered = mcp._tool_manager._tools
    assert registered
    for name, tool in registered.items():
        assert tool.context_kwarg == "ctx", f"{name} would not receive a Context"
        assert "ctx" not in tool.parameters.get("properties", {})
