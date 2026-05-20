"""System information and saved view tools."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from .. import __version__
from ..client import get_client
from ..config import Settings
from ..formatting import format_saved_view, safe_dump
from ._helpers import collect, safe_tool


def register(mcp: FastMCP, settings: Settings) -> None:
    """Register system / saved view tools."""

    @mcp.tool()
    @safe_tool
    async def get_paperless_info(ctx: Context) -> dict[str, Any]:
        """Return Paperless-ngx version info and the MCP server version."""
        paperless = get_client(ctx)
        return {
            "paperless_version": getattr(paperless, "host_version", None),
            "paperless_api_version": getattr(paperless, "host_api_version", None),
            "paperless_base_url": getattr(paperless, "base_url", None),
            "mcp_server_version": __version__,
        }

    @mcp.tool()
    @safe_tool
    async def get_statistics(ctx: Context) -> dict[str, Any]:
        """Return aggregate document statistics (total counts, inbox, etc.)."""
        paperless = get_client(ctx)
        stats = await paperless.statistics()
        dumped = safe_dump(stats)
        return dumped if isinstance(dumped, dict) else {"statistics": dumped}

    @mcp.tool()
    @safe_tool
    async def list_saved_views(ctx: Context, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        """List all saved views (a.k.a. dashboards)."""
        paperless = get_client(ctx)
        items, has_more = await collect(paperless.saved_views.filter(), offset=offset, limit=limit)
        return {
            "saved_views": [format_saved_view(v) for v in items],
            "returned": len(items),
            "offset": offset,
            "limit": limit,
            "has_more": has_more,
        }

    @mcp.tool()
    @safe_tool
    async def get_saved_view(ctx: Context, view_id: int) -> dict[str, Any]:
        """Return the full configuration of a saved view, including filter rules.

        Note: filter rules use Paperless' internal numeric ``rule_type`` codes
        (see Paperless-ngx source for the canonical mapping). To actually run
        the view, translate the rules into ``search_documents`` arguments
        yourself — there is no automatic execution.
        """
        paperless = get_client(ctx)
        view = await paperless.saved_views(view_id)
        rules = []
        for rule in getattr(view, "filter_rules", []) or []:
            rules.append(
                {
                    "rule_type": getattr(rule, "rule_type", None),
                    "value": getattr(rule, "value", None),
                }
            )
        out = format_saved_view(view)
        out["filter_rules"] = rules
        return out
