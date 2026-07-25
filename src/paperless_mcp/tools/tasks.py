"""Background task tools."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..client import ToolContext, get_client
from ..config import Settings
from ..formatting import format_task
from ._helpers import ToolInputError, page_result, paginate, safe_tool, window


def register(mcp: FastMCP, settings: Settings) -> None:
    """Register task tools."""

    @mcp.tool()
    @safe_tool
    async def list_active_tasks(
        ctx: ToolContext, offset: int = 0, limit: int = 50
    ) -> dict[str, Any]:
        """List pending and running tasks (consume queue, reprocessing, ...).

        Paperless caps this endpoint at 50 tasks server-side. Use ``list_tasks``
        for the full, filterable history.
        """
        paperless = await get_client(ctx)
        tasks = [task async for task in paperless.tasks.active()]
        items, total = window(tasks, offset=offset, limit=limit)
        return page_result(
            "tasks", items, offset=offset, limit=limit, total=total, formatter=format_task
        )

    @mcp.tool()
    @safe_tool
    async def list_tasks(
        ctx: ToolContext,
        status: str | None = None,
        task_type: str | None = None,
        acknowledged: bool | None = None,
        offset: int = 0,
        limit: int = 25,
    ) -> dict[str, Any]:
        """List tasks from the full history, newest first.

        ``status`` is one of ``pending``, ``started``, ``success``,
        ``failure``, ``revoked``. ``task_type`` is e.g. ``consume_file``,
        ``train_classifier``, ``sanity_check``, ``index_optimize``,
        ``mail_fetch``, ``empty_trash``.
        """
        paperless = await get_client(ctx)
        filters: dict[str, Any] = {"ordering": "-date_created"}
        if status:
            filters["status"] = status
        if task_type:
            filters["task_type"] = task_type
        if acknowledged is not None:
            filters["acknowledged"] = acknowledged
        items, total = await paginate(paperless.tasks, filters, offset=offset, limit=limit)
        return page_result(
            "tasks", items, offset=offset, limit=limit, total=total, formatter=format_task
        )

    @mcp.tool()
    @safe_tool
    async def get_task(ctx: ToolContext, task_id: str) -> dict[str, Any]:
        """Fetch a single task by primary key (numeric string) or Celery UUID.

        The UUID returned by ``upload_document`` goes here to check whether
        consumption succeeded.
        """
        paperless = await get_client(ctx)
        pk: int | str
        try:
            pk = int(task_id)
        except ValueError:
            pk = task_id
        task = await paperless.tasks(pk)
        return format_task(task)

    if settings.expose_writes:

        @mcp.tool()
        @safe_tool
        async def acknowledge_tasks(ctx: ToolContext, task_ids: list[int]) -> dict[str, Any]:
            """Acknowledge tasks by primary key, clearing them from the UI's alert list."""
            if not task_ids:
                raise ToolInputError("task_ids must not be empty")
            paperless = await get_client(ctx)
            count = await paperless.tasks.acknowledge(task_ids)
            return {"task_ids": task_ids, "acknowledged": count}
