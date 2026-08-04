"""Smoke tests for tool registration under the different visibility modes."""

from __future__ import annotations

import json
from typing import Any

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
        "run_saved_view",
        "list_trash",
        "list_active_tasks",
        "list_tasks",
        "get_task",
        "get_statistics",
        "get_system_status",
        "get_paperless_info",
        "get_next_asn",
        "search_everywhere",
        "search_autocomplete",
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
        "split_document",
        "delete_document_pages",
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
        "set_document_custom_field",
        "remove_document_custom_field",
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
        "bulk_delete_objects",
    }
)


_ALL_TOOLS = _READ_TOOLS | _WRITE_TOOLS | _DELETE_TOOLS


async def _tools_by_name(settings: Settings) -> dict[str, Any]:
    return {tool.name: tool for tool in await build_mcp(settings).list_tools()}


async def _full_surface() -> dict[str, Any]:
    return await _tools_by_name(make_settings(readonly=False, enable_delete=True))


async def _tool_names(settings: Settings) -> set[str]:
    return {tool.name for tool in await build_mcp(settings).list_tools()}


async def test_readonly_hides_writes_and_deletes() -> None:
    names = await _tool_names(make_settings(readonly=True, enable_delete=True))
    assert names >= _READ_TOOLS
    assert not (_WRITE_TOOLS | _DELETE_TOOLS) & names


async def test_default_exposes_writes_but_not_deletes() -> None:
    names = await _tool_names(make_settings(readonly=False, enable_delete=False))
    assert names >= _READ_TOOLS
    assert names >= _WRITE_TOOLS
    assert not _DELETE_TOOLS & names


async def test_enable_delete_exposes_deletes() -> None:
    names = await _tool_names(make_settings(readonly=False, enable_delete=True))
    assert names >= _DELETE_TOOLS


async def test_every_tool_has_a_description() -> None:
    """Claude Desktop shows the description verbatim; an empty one is unusable."""
    tools = await build_mcp(make_settings(readonly=False, enable_delete=True)).list_tools()
    missing = [tool.name for tool in tools if not (tool.description or "").strip()]
    assert missing == []


async def test_tool_schemas_are_json_serializable() -> None:
    """A schema that cannot be serialized breaks the tools/list response."""
    tools = await build_mcp(make_settings(readonly=False, enable_delete=True)).list_tools()
    for tool in tools:
        json.dumps(tool.input_schema)


def test_handshake_reports_our_own_version() -> None:
    """Without this the client sees the MCP SDK's version as the server's."""
    mcp = build_mcp(make_settings(readonly=False, enable_delete=False))
    options = mcp._lowlevel_server.create_initialization_options()
    assert options.server_name == "paperless-mcp"
    assert options.server_version == __version__
    assert options.instructions is not None
    assert "Paperless-ngx" in options.instructions


async def test_every_tool_is_annotated_and_titled() -> None:
    """An unannotated tool tells a client nothing about what a call will do."""
    tools = await _full_surface()
    assert set(tools) == _ALL_TOOLS
    unannotated = [name for name, tool in tools.items() if tool.annotations is None]
    untitled = [name for name, tool in tools.items() if not (tool.title or "").strip()]
    assert unannotated == []
    assert untitled == []


async def test_read_tools_are_marked_read_only_and_nothing_else_is() -> None:
    """``readOnlyHint`` is what lets a client run a tool without a confirmation."""
    tools = await _full_surface()
    read_only = {name for name, tool in tools.items() if tool.annotations.read_only_hint}
    assert read_only == set(_READ_TOOLS)


async def test_read_tools_omit_the_hints_that_only_apply_to_writes() -> None:
    """The spec gives destructive/idempotent meaning only when writes are possible."""
    tools = await _full_surface()
    for name in _READ_TOOLS:
        annotations = tools[name].annotations
        assert annotations.destructive_hint is None, name
        assert annotations.idempotent_hint is None, name


async def test_write_and_delete_tools_declare_both_write_hints() -> None:
    """A missing hint falls back to the spec default, which is not our claim."""
    tools = await _full_surface()
    for name in _WRITE_TOOLS | _DELETE_TOOLS:
        annotations = tools[name].annotations
        assert annotations.read_only_hint is False, name
        assert annotations.destructive_hint is not None, name
        assert annotations.idempotent_hint is not None, name


async def test_deletes_are_destructive_and_idempotent() -> None:
    tools = await _full_surface()
    for name in _DELETE_TOOLS:
        annotations = tools[name].annotations
        assert annotations.destructive_hint is True, name
        assert annotations.idempotent_hint is True, name


async def test_additive_writes_are_not_flagged_destructive() -> None:
    """Uploading or creating something never overwrites what was already there."""
    tools = await _full_surface()
    additive = {
        "upload_document",
        "add_document_note",
        "restore_documents",
        "acknowledge_tasks",
        "create_share_link",
        *(name for name in _WRITE_TOOLS if name.startswith("create_")),
    }
    for name in additive:
        assert tools[name].annotations.destructive_hint is False, name


async def test_repeatable_writes_are_flagged_non_idempotent() -> None:
    """These accumulate: a retry is not free, and a client must not assume it is."""
    tools = await _full_surface()
    # Rotating twice by 90 degrees lands at 180; merging mints another document;
    # reprocessing queues another task; creating adds another row.
    accumulating = {
        "bulk_rotate_documents",
        "bulk_merge_documents",
        "bulk_reprocess_documents",
        "upload_document",
        "add_document_note",
        *(name for name in _WRITE_TOOLS if name.startswith("create_")),
    }
    for name in accumulating:
        assert tools[name].annotations.idempotent_hint is False, name


async def test_no_tool_claims_an_open_world() -> None:
    """The archive is one known server, not an unbounded set of external entities."""
    tools = await _full_surface()
    for name, tool in tools.items():
        assert tool.annotations.open_world_hint is False, name


async def test_annotations_go_out_under_their_camel_case_aliases() -> None:
    """The wire format is camelCase; a snake_case payload would be ignored."""
    tools = await _full_surface()
    dumped = tools["search_documents"].model_dump(by_alias=True, exclude_none=True)
    assert dumped["annotations"] == {"readOnlyHint": True, "openWorldHint": False}


def test_every_tool_receives_the_lifespan_context() -> None:
    """MCPServer injects ``ctx`` only when it recognises the annotation.

    ``ToolContext`` is a parameterized ``Context[...]``; if that ever stops
    being detected, every tool would be called without a client and the ``ctx``
    parameter would leak into the public input schema instead.
    """
    mcp = build_mcp(make_settings(readonly=False, enable_delete=True))
    registered = mcp._tool_manager._tools
    assert registered
    for name, tool in registered.items():
        assert tool.context_kwarg == "ctx", f"{name} would not receive a Context"
        assert "ctx" not in tool.parameters.get("properties", {})
