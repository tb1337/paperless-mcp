"""Tool registration entry point."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from ..config import Settings
from . import ai, bulk, documents, share_links, system, tasks, taxonomy, trash


def register_all(mcp: FastMCP, settings: Settings) -> None:
    """Register every enabled tool module on the FastMCP instance."""
    documents.register(mcp, settings)
    taxonomy.register(mcp, settings)
    bulk.register(mcp, settings)
    trash.register(mcp, settings)
    tasks.register(mcp, settings)
    system.register(mcp, settings)
    ai.register(mcp, settings)
    share_links.register(mcp, settings)
