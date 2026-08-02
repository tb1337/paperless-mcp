"""Wait for a Paperless background task to reach a terminal state.

Consumption is asynchronous: an upload hands back a Celery task UUID and the
document exists only once the consumer is done with it. Waiting here rather
than handing the UUID to the model turns "upload, then call ``get_task`` until
something changes" into one tool call, which is what makes an upload usable as
a step inside a longer piece of work instead of the end of it.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress

from pypaperless import PaperlessClient
from pypaperless.exceptions import (
    ItemNotFoundError,
    NotFoundError,
    PaperlessConnectionError,
    TaskNotFoundError,
)
from pypaperless.models import Task
from pypaperless.models.tasks import TaskStatus

#: Hard ceiling on how long a tool may wait. An MCP client enforces its own
#: request timeout, and a call that outlives it is worse than one that hands
#: back the UUID: the work happens anyway, but nobody is left to read the
#: result.
MAX_POLL_TIMEOUT_SECONDS = 300

#: Statuses Paperless never moves away from. Held as plain strings because
#: ``TaskStatus`` inherits ``Enum.__hash__``, which hashes by member name — a
#: set of members would never match the equal string.
_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {TaskStatus.SUCCESS.value, TaskStatus.FAILURE.value, TaskStatus.REVOKED.value}
)

#: How long to wait between polls: short at first so a small PDF comes back
#: quickly, then backing off so a five-minute OCR run does not cost hundreds of
#: requests.
_FIRST_DELAY_SECONDS = 1.0
_MAX_DELAY_SECONDS = 5.0
_DELAY_FACTOR = 1.5


def task_status(task: Task | None) -> str | None:
    """Return *task*'s status as a plain string, or ``None`` when it has none."""
    if task is None or task.status is None:
        return None
    return task.status.value


def task_document_id(task: Task | None) -> int | None:
    """Return the document a finished consume task created, if it reported one.

    Paperless resolves the relation itself — the API derives
    ``related_document_ids`` from the consumer's result — so there is no result
    message to parse here.
    """
    if task is None or not task.related_document_ids:
        return None
    return task.related_document_ids[0]


async def wait_for_task(
    paperless: PaperlessClient, task_uuid: str, *, timeout: float
) -> tuple[Task | None, bool]:
    """Poll *task_uuid* until it finishes or *timeout* seconds have passed.

    Args:
        paperless: The connected client to poll through.
        task_uuid: The Celery UUID a create call handed back.
        timeout: How long to keep waiting, in seconds.

    Returns:
        ``(task, timed_out)``. ``task`` is the last state seen and is ``None``
        only when Paperless never showed the task at all; ``timed_out`` says
        the wait ran out while the task was still pending or running.
    """
    deadline = time.monotonic() + timeout
    delay = _FIRST_DELAY_SECONDS
    task: Task | None = None

    while True:
        # Two not-yets rather than failures: a just-queued task is absent from
        # /api/tasks/ until a worker registers it, and a poll that spans a
        # container restart or a slow moment sees the connection break.
        # Aborting on either would strand the caller mid-wait; the deadline is
        # what ends this loop.
        with suppress(
            TaskNotFoundError, ItemNotFoundError, NotFoundError, PaperlessConnectionError
        ):
            task = await paperless.tasks(task_uuid)
        if task_status(task) in _TERMINAL_STATUSES:
            return task, False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return task, True
        await asyncio.sleep(min(delay, remaining))
        delay = min(delay * _DELAY_FACTOR, _MAX_DELAY_SECONDS)
