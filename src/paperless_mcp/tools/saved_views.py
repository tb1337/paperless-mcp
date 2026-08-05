"""Saved views: the queries the user already curated and named.

A saved view is what a request like "unpaid invoices" or "this year's tax stuff"
actually means to this user, so running the view beats guessing at the same filters.
``run_saved_view`` is what makes that possible: it translates the view's stored
filter rules into one document query, server-side, in the view's own sort order.

Its own module rather than a corner of ``system.py``, because a resource area gets
one — and because the 199-line ``_saved_view_filters`` translation table sits next to
it as its private neighbour rather than next to the version probe.
"""

from __future__ import annotations

from functools import partial
from typing import Any

from mcp.server.mcpserver import MCPServer

from ..client import ToolContext, get_client, get_names
from ..config import Settings
from ..formatting import format_document, format_saved_view
from ._paging import named_page, page_result, paginate
from ._registry import read_tool, register_tools
from ._saved_view_filters import translate_filter_rules, view_ordering


async def list_saved_views(ctx: ToolContext, offset: int = 0, limit: int = 50) -> dict[str, Any]:
    """List all saved views (the user's stored document filters)."""
    return await named_page(
        ctx,
        "saved_views",
        lambda paperless: paperless.saved_views,
        format_saved_view,
        offset=offset,
        limit=limit,
    )


async def get_saved_view(ctx: ToolContext, view_id: int) -> dict[str, Any]:
    """Return a saved view's full configuration, including its filter rules.

    Filter rules use Paperless' internal numeric ``rule_type`` codes, which
    are rarely what you want: ``run_saved_view`` executes the view and
    answers with the documents. Read the rules to inspect or adapt a view —
    to filter it down further with ``search_documents``, or to translate by
    hand the one rule ``run_saved_view`` reported it cannot.
    """
    paperless = await get_client(ctx)
    names = await get_names(ctx)
    view = await paperless.saved_views(view_id)
    out = format_saved_view(view, names)
    out["filter_rules"] = [
        {"rule_type": rule.rule_type, "value": rule.value} for rule in (view.filter_rules or [])
    ]
    return out


async def run_saved_view(
    ctx: ToolContext, view_id: int, offset: int = 0, limit: int = 25
) -> dict[str, Any]:
    """Execute a saved view and return the documents it selects.

    A saved view is a query the user built and named in the Paperless web
    UI — "Unpaid invoices", "Tax 2024", "Scans to file". This runs it: its
    stored filter rules become one document query, in the view's own sort
    order, and the matching documents come back paged like any search.
    ``list_saved_views`` reports the ``view_id``.

    Prefer this over rebuilding a view's rules as a ``search_documents``
    call. The rules are numeric codes whose meaning is internal to
    Paperless, and the view is what the user actually curated — running it
    is the only way to answer with the documents they mean by its name.

    The result carries the ``filters`` the rules translated into, so the
    query is checkable and can be taken to ``search_documents`` to narrow
    it further.

    A rule this server cannot translate — one newer than its table — is
    never dropped. The call then fails with ``unsupported_filter_rule`` and
    names the rule types, because a view answered with too many documents
    is worse than a view left unanswered; ``get_saved_view`` hands you the
    raw rules to work from.
    """
    paperless = await get_client(ctx)
    # Before the documents are fetched, so they carry their custom fields.
    names = await get_names(ctx)
    view = await paperless.saved_views(view_id)
    query = translate_filter_rules(view.filter_rules or [])
    if query.unsupported:
        return {
            "error": "unsupported_filter_rule",
            "detail": (
                f"Saved view {view_id} filters on rule types this server cannot "
                f"translate: {', '.join(str(t) for t in query.unsupported)}. Running "
                "it would answer with more documents than the view selects."
            ),
            "view_id": view_id,
            "view_name": view.name,
            "unsupported_rule_types": list(query.unsupported),
            "hint": (
                "get_saved_view returns every rule; translate the unsupported ones "
                "into search_documents arguments by hand."
            ),
        }

    filters = dict(query.filters)
    ordering = view_ordering(view.sort_field, view.sort_reverse)
    if ordering is not None:
        filters["ordering"] = ordering
    items, total = await paginate(paperless.documents, filters, offset=offset, limit=limit)
    return page_result(
        "documents",
        items,
        offset=offset,
        limit=limit,
        total=total,
        formatter=partial(format_document, names=names),
        view_id=view_id,
        view_name=view.name,
        filters=filters,
    )


def register(mcp: MCPServer, settings: Settings) -> None:
    """Register the saved view tools this deployment exposes."""
    register_tools(
        mcp,
        settings,
        (
            read_tool(list_saved_views),
            read_tool(get_saved_view),
            read_tool(run_saved_view),
        ),
    )
