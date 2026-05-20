"""Background task tools."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from ..client import get_client
from ..config import Settings
from ..formatting import format_task
from ._helpers import safe_tool


def register(mcp: FastMCP, settings: Settings) -> None:
    """Register task tools."""

    @mcp.tool()
    @safe_tool
    async def list_active_tasks(ctx: Context) -> dict[str, Any]:
        """List currently pending and running tasks (consumer queue, etc.)."""
        paperless = get_client(ctx)
        items = []
        async for t in paperless.tasks.active():
            items.append(format_task(t))
        return {"tasks": items}

    @mcp.tool()
    @safe_tool
    async def get_task(ctx: Context, task_id: str) -> dict[str, Any]:
        """Fetch a single task by primary key (integer string) or Celery UUID."""
        paperless = get_client(ctx)
        try:
            pk: int | str = int(task_id)
        except ValueError:
            pk = task_id
        task = await paperless.tasks(pk)
        return format_task(task)
