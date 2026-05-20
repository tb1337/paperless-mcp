"""Helpers for accessing the shared PaperlessClient from inside tool handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp.server.fastmcp import Context

if TYPE_CHECKING:
    from pypaperless import PaperlessClient

    from .config import Settings


CLIENT_KEY = "paperless"
SETTINGS_KEY = "settings"


def get_client(ctx: Context) -> PaperlessClient:
    """Return the PaperlessClient attached to the FastMCP lifespan context."""
    return ctx.request_context.lifespan_context[CLIENT_KEY]


def get_settings(ctx: Context) -> Settings:
    """Return the Settings attached to the FastMCP lifespan context."""
    return ctx.request_context.lifespan_context[SETTINGS_KEY]
