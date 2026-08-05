"""Background task tools."""

from __future__ import annotations

from functools import partial
from typing import Any

from mcp.server.mcpserver import MCPServer

from ..client import ToolContext, get_client, get_names
from ..config import Settings
from ..formatting import format_task
from ._arguments import TaskStatusName, TaskTypeName
from ._errors import ToolInputError
from ._paging import page_result, paginate, window
from ._registry import read_tool, register_tools, write_tool


async def list_active_tasks(ctx: ToolContext, offset: int = 0, limit: int = 50) -> dict[str, Any]:
    """List pending and running tasks (consume queue, reprocessing, ...).

    Paperless caps this endpoint at 50 tasks server-side. Use ``list_tasks``
    for the full, filterable history.
    """
    paperless = await get_client(ctx)
    names = await get_names(ctx)
    tasks = [task async for task in paperless.tasks.active()]
    items, total = window(tasks, offset=offset, limit=limit)
    return page_result(
        "tasks",
        items,
        offset=offset,
        limit=limit,
        total=total,
        formatter=partial(format_task, names=names),
    )


async def list_tasks(
    ctx: ToolContext,
    status: TaskStatusName | None = None,
    task_type: TaskTypeName | None = None,
    acknowledged: bool | None = None,
    offset: int = 0,
    limit: int = 25,
) -> dict[str, Any]:
    """List tasks from the full task history.

    ``status`` is one of ``pending``, ``started``, ``success``,
    ``failure``, ``revoked``. ``task_type`` is e.g. ``consume_file``,
    ``train_classifier``, ``sanity_check``, ``index_optimize``,
    ``mail_fetch``, ``empty_trash``. Newest tasks come first where
    Paperless supports ordering on this endpoint; check ``date_created``
    rather than relying on position.
    """
    paperless = await get_client(ctx)
    names = await get_names(ctx)
    # `ordering` is a DRF OrderingFilter parameter rather than a FilterSet
    # field — pypaperless 6.0.0rc2 dropped it from TaskFilters for that
    # reason. Whether /api/tasks/ honours it is version-dependent; Paperless
    # ignores it silently when it does not, so requesting it is free.
    filters: dict[str, Any] = {"ordering": "-date_created"}
    if status:
        filters["status"] = status
    if task_type:
        filters["task_type"] = task_type
    if acknowledged is not None:
        filters["acknowledged"] = acknowledged
    items, total = await paginate(paperless.tasks, filters, offset=offset, limit=limit)
    return page_result(
        "tasks",
        items,
        offset=offset,
        limit=limit,
        total=total,
        formatter=partial(format_task, names=names),
    )


async def get_task(ctx: ToolContext, task_id: str) -> dict[str, Any]:
    """Fetch a single task by primary key (numeric string) or Celery UUID.

    The UUID returned by ``upload_document`` goes here to check whether
    consumption succeeded.
    """
    paperless = await get_client(ctx)
    names = await get_names(ctx)
    pk: int | str
    try:
        pk = int(task_id)
    except ValueError:
        pk = task_id
    task = await paperless.tasks(pk)
    return format_task(task, names)


async def acknowledge_tasks(ctx: ToolContext, task_ids: list[int]) -> dict[str, Any]:
    """Acknowledge tasks by primary key, clearing them from the UI's alert list."""
    if not task_ids:
        raise ToolInputError("task_ids must not be empty")
    paperless = await get_client(ctx)
    count = await paperless.tasks.acknowledge(task_ids)
    return {"task_ids": task_ids, "acknowledged": count}


def register(mcp: MCPServer, settings: Settings) -> None:
    """Register the task tools this deployment exposes."""
    register_tools(
        mcp,
        settings,
        (
            read_tool(list_active_tasks),
            read_tool(list_tasks),
            read_tool(get_task),
            write_tool(acknowledge_tasks, destructive=False, idempotent=True),
        ),
    )
