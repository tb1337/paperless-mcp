"""The monthly close-out workflow.

The date window is worked out here rather than left to the model: month
boundaries, leap days and "the month before the one you asked for" are exactly
the arithmetic a model gets subtly wrong, and every search in the plan is
anchored to them.
"""

from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass

from mcp.server.mcpserver import MCPServer

from ..config import Settings
from ._helpers import capability_note, sections

_INTRO = """\
Close out {label} in the Paperless-ngx archive: {start} to {end} inclusive."""

_SCOPE = """\
Two different questions, and a monthly close needs both.

- What is *dated* in the window:
  `search_documents(created_after="{start}", created_before="{end}", order_by="created",
  limit=100)`
- What *arrived* in the window: the same call with `added_after` / `added_before`.

They are not the same set, and the gap between them is where the findings live. A letter scanned
six weeks late is in the second and not the first; an invoice dated inside the window that is
still sitting in a pile is in neither. Page both with `offset` until `has_more` is false — a
close-out that quietly stopped at the first 100 documents is worse than none, because it reads
like a complete one."""

_INVENTORY = """\
1. Inventory. Group the dated set by `correspondent_name` and `document_type_name` — both come
   back on every result, so grouping costs no extra call. Report the counts per group and the
   total, and name the two or three documents that stand out."""

_HYGIENE = """\
2. Hygiene — the point of a close is catching what slipped through.

   - Never filed: `search_documents(is_in_inbox=true, added_before="{end}")`. Everything it
     returns has been waiting at least since the window closed.
   - Untagged: `search_documents(is_tagged=false, added_after="{start}",
     added_before="{end}")`.
   - Unattributed: from the inventory above, the documents whose `correspondent` or
     `document_type` is null.
   - Never arrived: `list_tasks(status="failure", limit=25)`. A document whose consumption failed
     appears in no search result at all, which is precisely what makes it easy to miss. Check
     `date_created` against the window and read `result_data` for the reason.
   - `get_statistics()` for the archive-wide counters — total documents, inbox size, file types —
     as the backdrop the window sits in."""

_ABSENCES = """\
3. Absences. The one check a search cannot do for you, because it is about what is *not* there.
   Run the same two searches over the previous window ({prev_start} to {prev_end}), take the
   set of correspondents that appear there and subtract the ones in this window. A rent
   payment, a payslip or a utility bill that shows up eleven months out of twelve and not the
   twelfth is a finding, not a rounding error. Name the sender and when it was last seen."""

_DUPLICATES = """\
4. Duplicates, cheaply. Within the window, any two documents sharing a correspondent, a `created`
   date and a `page_count` are worth one `find_similar_documents` call. If more than a couple turn
   up, stop here and run the `find_duplicates` prompt instead — it does this properly."""

_FIX = """\
5. Fix what is cheap and certain — an obvious missing correspondent, a missing tag — with
   `bulk_edit_documents`, and do it as a named step *after* the report rather than quietly while
   surveying. Anything that needs a judgement call goes in the report as a recommendation, not
   into a write."""

_REPORT = """\
{step}. Report, in this order: what came in (counts by document type and by sender), what needs
   attention (unfiled, untagged, unattributed, failed consumption), what is missing (absent
   recurring senders), and — only if there is one — the single thing worth doing before the next
   close. Keep it short enough to read in one go; the detail belongs in the tables above it."""


@dataclass(frozen=True, slots=True)
class MonthWindow:
    """The two inclusive date windows a close-out compares."""

    label: str
    start: dt.date
    end: dt.date
    prev_start: dt.date
    prev_end: dt.date


def _bounds(day: dt.date) -> tuple[dt.date, dt.date]:
    """Return the first and last day of the month *day* falls in."""
    last = calendar.monthrange(day.year, day.month)[1]
    return day.replace(day=1), day.replace(day=last)


def month_window(month: str | None, today: dt.date) -> MonthWindow:
    """Resolve a ``YYYY-MM`` argument into the window and the one before it.

    Args:
        month: The month to close out, or ``None`` for the one before *today*.
            Defaulting to the previous month is what makes the prompt useful on
            the first of the month, which is when a close-out actually happens.
        today: The current date, passed in so the default is testable.

    Raises:
        ValueError: When *month* is not a ``YYYY-MM`` string naming a real month.
    """
    if month is None or not month.strip():
        target = today.replace(day=1) - dt.timedelta(days=1)
    else:
        try:
            target = dt.datetime.strptime(month.strip(), "%Y-%m").date()
        except ValueError as exc:
            raise ValueError(f"month must look like YYYY-MM (e.g. 2026-03), got {month!r}") from exc

    start, end = _bounds(target)
    # Stepping back one day from the first of the month lands in the previous
    # one whatever its length is, so no month arithmetic is needed.
    prev_start, prev_end = _bounds(start - dt.timedelta(days=1))
    return MonthWindow(
        label=f"{target:%Y-%m} ({target:%B %Y})",
        start=start,
        end=end,
        prev_start=prev_start,
        prev_end=prev_end,
    )


def register(mcp: MCPServer, settings: Settings) -> None:
    """Register the monthly review prompt."""

    @mcp.prompt(title="Monthly review")
    async def monthly_review(month: str | None = None) -> str:
        """Close out one month in the Paperless archive.

        Inventories what was dated and what arrived in the month, hunts for the
        documents that slipped through (still in the inbox, untagged,
        unattributed, consumption failed) and names the recurring senders that
        went silent. ``month`` is ``YYYY-MM`` and defaults to last month.
        """
        # Local rather than UTC on purpose: Paperless stores wall-clock dates,
        # so "last month" has to mean the month the archive itself is in.
        today = dt.datetime.now(dt.UTC).astimezone().date()
        window = month_window(month, today)
        return sections(
            _INTRO.format(label=window.label, start=window.start, end=window.end),
            capability_note(settings),
            _SCOPE.format(start=window.start, end=window.end),
            _INVENTORY,
            _HYGIENE.format(start=window.start, end=window.end),
            _ABSENCES.format(prev_start=window.prev_start, prev_end=window.prev_end),
            _DUPLICATES,
            _FIX if settings.expose_writes else None,
            _REPORT.format(step=6 if settings.expose_writes else 5),
        )
