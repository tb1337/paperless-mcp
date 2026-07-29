"""System information and saved view tools."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from .. import __version__
from ..client import ToolContext, get_client, get_settings
from ..config import Settings
from ..formatting import format_saved_view, safe_dump
from ._helpers import page_result, paginate, safe_tool


def register(mcp: FastMCP, settings: Settings) -> None:
    """Register system / saved view tools."""

    @mcp.tool()
    @safe_tool
    async def get_paperless_info(ctx: ToolContext) -> dict[str, Any]:
        """Return Paperless-ngx version info and this MCP server's configuration."""
        paperless = await get_client(ctx)
        cfg = get_settings(ctx)
        return {
            "paperless_version": paperless.host_version,
            "paperless_api_version": paperless.host_api_version,
            "paperless_base_url": paperless.base_url,
            "mcp_server_version": __version__,
            "readonly": cfg.readonly,
            "deletes_enabled": cfg.expose_deletes,
        }

    @mcp.tool()
    @safe_tool
    async def get_statistics(ctx: ToolContext) -> dict[str, Any]:
        """Return aggregate statistics: document totals, inbox count, file types."""
        paperless = await get_client(ctx)
        stats = await paperless.statistics()
        dumped = safe_dump(stats)
        return dumped if isinstance(dumped, dict) else {"statistics": dumped}

    @mcp.tool()
    @safe_tool
    async def list_saved_views(
        ctx: ToolContext, offset: int = 0, limit: int = 50
    ) -> dict[str, Any]:
        """List all saved views (the user's stored document filters)."""
        paperless = await get_client(ctx)
        items, total = await paginate(paperless.saved_views, offset=offset, limit=limit)
        return page_result(
            "saved_views",
            items,
            offset=offset,
            limit=limit,
            total=total,
            formatter=format_saved_view,
        )

    @mcp.tool()
    @safe_tool
    async def get_saved_view(ctx: ToolContext, view_id: int) -> dict[str, Any]:
        """Return a saved view's full configuration, including its filter rules.

        Filter rules use Paperless' internal numeric ``rule_type`` codes. There
        is no automatic execution: translate the rules into ``search_documents``
        arguments yourself.
        """
        paperless = await get_client(ctx)
        view = await paperless.saved_views(view_id)
        out = format_saved_view(view)
        out["filter_rules"] = [
            {"rule_type": rule.rule_type, "value": rule.value} for rule in (view.filter_rules or [])
        ]
        return out
