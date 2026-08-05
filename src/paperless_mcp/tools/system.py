"""System information and saved view tools."""

from __future__ import annotations

from functools import partial
from typing import Any

from mcp.server.mcpserver import MCPServer

from .. import __version__
from ..client import ToolContext, get_client, get_names, get_settings
from ..config import Settings
from ..formatting import dump_mapping, format_document, format_saved_view, summarize_status
from ._paging import named_page, page_result, paginate
from ._registry import read_tool, register_tools
from ._saved_view_filters import translate_filter_rules, view_ordering


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
    """Register the system / saved view tools this deployment exposes."""
    register_tools(
        mcp,
        settings,
        (
            read_tool(get_paperless_info),
            read_tool(get_statistics),
            read_tool(get_system_status),
            read_tool(list_saved_views),
            read_tool(get_saved_view),
            read_tool(run_saved_view),
        ),
    )
