"""System information and saved view tools."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from .. import __version__
from ..client import get_client
from ..config import Settings
from ..formatting import format_document, format_saved_view


def _dump(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return obj


def register(mcp: FastMCP, settings: Settings) -> None:
    """Register system / saved view tools."""

    @mcp.tool()
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
    async def get_statistics(ctx: Context) -> dict[str, Any]:
        """Return aggregate document statistics (total counts, inbox, etc.)."""
        paperless = get_client(ctx)
        stats = await paperless.statistics()
        return _dump(stats)

    @mcp.tool()
    async def list_saved_views(ctx: Context) -> dict[str, Any]:
        """List all saved views (a.k.a. dashboards)."""
        paperless = get_client(ctx)
        items = []
        async for v in paperless.saved_views.filter():
            items.append(format_saved_view(v))
        return {"saved_views": items}

    @mcp.tool()
    async def run_saved_view(ctx: Context, view_id: int, limit: int = 50) -> dict[str, Any]:
        """Execute a saved view's filter rules and return matching documents."""
        paperless = get_client(ctx)
        view = await paperless.saved_views(view_id)
        filters: dict[str, Any] = {}
        for rule in getattr(view, "filter_rules", []) or []:
            rule_type = getattr(rule, "rule_type", None)
            value = getattr(rule, "value", None)
            if rule_type is None:
                continue
            # We forward the raw rule_type as a string key; Paperless honors
            # the same numeric rule types here as in the documents endpoint.
            filters[f"filter_rule_{rule_type}"] = value

        documents: list[dict[str, Any]] = []
        if filters:
            async for doc in paperless.documents.filter(**filters):
                documents.append(format_document(doc))
                if len(documents) >= limit:
                    break
        return {
            "view": format_saved_view(view),
            "documents": documents,
            "returned": len(documents),
        }
