"""Global search across documents and master data."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

from mcp.server.mcpserver import MCPServer

from ..client import ToolContext, get_client, get_names
from ..config import Settings
from ..formatting import (
    format_document_summary,
    format_saved_view,
)
from ..names import NameMap
from ..resources import RESOURCES
from ._errors import ToolInputError
from ._master_data import FORMATTERS
from ._paging import check_limit
from ._registry import read_tool, register_tools

#: The result categories this server exposes, paired with their formatter. The
#: master-data ones come from the resource registry, so a new resource is reported
#: here as soon as it has a formatter.
#:
#: Users, groups, mail rules and accounts, and workflows are deliberately left
#: out: they are the admin-tier resources the tool surface does not carry, and
#: none of them resolves to something a document filter accepts.
#:
#: Documents are always summarized and this tool takes no ``fields`` argument:
#: it answers "what is this thing called in Paperless?" across seven categories at
#: once, so a full projection would pay for a document's file names and owner
#: seven categories deep to hand back an ID.
_CATEGORIES: Final[tuple[tuple[str, Callable[[Any, NameMap], dict[str, Any]]], ...]] = (
    ("documents", format_document_summary),
    *((resource.key, FORMATTERS[resource.key]) for resource in RESOURCES),
    ("saved_views", format_saved_view),
)


async def search_everywhere(
    ctx: ToolContext,
    query: str,
    db_only: bool = False,
    limit: int = 10,
) -> dict[str, Any]:
    """Search documents and master data at once, to turn a name into an ID.

    One call answers "what is this thing called in Paperless?" across
    documents, tags, correspondents, document types, storage paths, custom
    fields and saved views — instead of guessing an ID or paging three
    ``list_*`` tools to find it. Use it first when a request names something
    ("the Telekom invoices") and the matching IDs are not known yet.

    This is not the tool for finding documents by criteria: it takes one
    search string and cannot filter, sort or page. Once the IDs are known,
    ``search_documents`` is the one that does all three.

    ``query`` is plain text; Whoosh field syntax such as
    ``correspondent:telekom`` applies to the document hits. ``db_only``
    skips the full-text index and matches stored field values only, which
    is faster and finds titles rather than scan contents.

    ``limit`` caps each category separately, not the total, and may not
    exceed 100. Every category key is always present, empty when nothing
    matched, and ``truncated`` says whether any of them hit the cap.
    """
    if not query.strip():
        raise ToolInputError("query must not be empty")
    if limit < 1:
        raise ToolInputError(f"limit must be at least 1, got {limit}")
    check_limit(limit)

    paperless = await get_client(ctx)
    # Before the search, not after: the same call primes the custom-field
    # cache that enriches the Document hits while they are being parsed.
    names = await get_names(ctx)
    # Omitted rather than sent as false, so the server applies its own
    # default instead of parsing a string that is truthy in the wrong hands.
    result = await paperless.search(query, db_only=True if db_only else None)

    payload: dict[str, Any] = {"query": query, "total": result.total, "limit": limit}
    truncated = False
    for key, formatter in _CATEGORIES:
        items = list(getattr(result, key, None) or [])
        truncated = truncated or len(items) > limit
        payload[key] = [formatter(item, names) for item in items[:limit]]
    payload["truncated"] = truncated
    return payload


async def search_autocomplete(ctx: ToolContext, term: str, limit: int = 10) -> dict[str, Any]:
    """Complete a partial word against the full-text index.

    Returns words that actually occur in the scanned documents, which is
    the one thing guesswork cannot supply: whether an archive spells it
    "Rechnung", "Rechnungen" or "RECHNUNG" decides whether a
    ``search_documents`` query matches anything at all.

    It only knows vocabulary. Field names and query syntax
    (``correspondent:``, ``tag:``, ``created:[… TO …]``) are not part of
    the index and never show up here — those are documented on
    ``search_documents``. For finding which *entity* a name refers to, use
    ``search_everywhere`` instead; this answers a narrower question.

    ``limit`` may not exceed 100.
    """
    if not term.strip():
        raise ToolInputError("term must not be empty")
    if limit < 1:
        raise ToolInputError(f"limit must be at least 1, got {limit}")
    check_limit(limit)
    paperless = await get_client(ctx)
    suggestions = await paperless.search.autocomplete(term, limit)
    return {"term": term, "suggestions": list(suggestions), "limit": limit}


def register(mcp: MCPServer, settings: Settings) -> None:
    """Register the global search tools this deployment exposes."""
    register_tools(
        mcp,
        settings,
        (
            read_tool(search_everywhere),
            read_tool(search_autocomplete),
        ),
    )
