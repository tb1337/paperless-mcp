"""Tool registration entry point."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from ..config import Settings
from . import (
    ai,
    bulk,
    custom_field_values,
    documents,
    saved_views,
    search,
    share_links,
    system,
    tasks,
    taxonomy,
    trash,
)


def register_all(mcp: MCPServer, settings: Settings) -> None:
    """Register every enabled tool module on the MCPServer instance."""
    documents.register(mcp, settings)
    search.register(mcp, settings)
    taxonomy.register(mcp, settings)
    custom_field_values.register(mcp, settings)
    bulk.register(mcp, settings)
    trash.register(mcp, settings)
    tasks.register(mcp, settings)
    system.register(mcp, settings)
    saved_views.register(mcp, settings)
    ai.register(mcp, settings)
    share_links.register(mcp, settings)
