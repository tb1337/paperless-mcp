"""System information: what this Paperless is, how big it is, and whether it is well."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from .. import __version__
from ..client import ToolContext, get_client, get_settings
from ..config import Settings
from ..formatting import dump_mapping, summarize_status
from ._registry import read_tool, register_tools


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


async def get_statistics(ctx: ToolContext) -> dict[str, Any]:
    """Return aggregate statistics: document totals, inbox count, file types."""
    paperless = await get_client(ctx)
    stats = await paperless.statistics()
    return dump_mapping(stats, key="statistics")


async def get_system_status(ctx: ToolContext) -> dict[str, Any]:
    """Report Paperless-ngx system health: database, Redis, Celery, index, classifier.

    ``health`` is the rolled-up verdict (``ok``, ``warning``, ``error`` or
    ``unknown``) and ``problems`` lists only the subsystems that are not OK,
    with the error text Paperless reported. The remaining keys carry the
    untouched payload: versions, disk space, migration state and the task
    counts of the last few days.

    Reading this needs the ``view_system_monitoring`` permission or a staff
    account; a token without it comes back as ``{"error": "forbidden"}``.
    """
    paperless = await get_client(ctx)
    status = await paperless.status()
    return {**summarize_status(status), **dump_mapping(status, key="status")}


def register(mcp: MCPServer, settings: Settings) -> None:
    """Register the system information tools this deployment exposes."""
    register_tools(
        mcp,
        settings,
        (
            read_tool(get_paperless_info),
            read_tool(get_statistics),
            read_tool(get_system_status),
        ),
    )
