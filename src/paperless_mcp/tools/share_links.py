"""Share link tools."""

from __future__ import annotations

import datetime as dt
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from ..client import get_client
from ..config import Settings
from ..formatting import format_share_link
from ._helpers import collect, safe_tool


def register(mcp: FastMCP, settings: Settings) -> None:
    """Register share-link tools."""

    @mcp.tool()
    @safe_tool
    async def list_share_links(
        ctx: Context,
        document_id: int | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        """List share links, optionally filtered to a single document."""
        paperless = get_client(ctx)
        filters: dict[str, Any] = {}
        if document_id is not None:
            filters["document__id"] = document_id
        items, has_more = await collect(
            paperless.share_links.filter(**filters), offset=offset, limit=limit
        )
        return {
            "share_links": [format_share_link(sl) for sl in items],
            "returned": len(items),
            "offset": offset,
            "limit": limit,
            "has_more": has_more,
        }

    if settings.expose_writes:

        @mcp.tool()
        @safe_tool
        async def create_share_link(
            ctx: Context,
            document_id: int,
            expiration: str | None = None,
            file_version: str | None = None,
        ) -> dict[str, Any]:
            """Create a share link for a document.

            ``expiration`` is an ISO datetime; omit for a non-expiring link.
            ``file_version`` is ``"archive"`` or ``"original"``.
            """
            paperless = get_client(ctx)
            draft = paperless.share_links.create()
            draft.document = document_id
            if expiration is not None:
                draft.expiration = dt.datetime.fromisoformat(expiration)
            if file_version is not None:
                draft.file_version = file_version
            new_id = await paperless.share_links.save(draft)
            return {"share_link": {"id": new_id, "document": document_id}}

    if settings.expose_deletes:

        @mcp.tool()
        @safe_tool
        async def delete_share_link(ctx: Context, share_link_id: int) -> dict[str, Any]:
            """Delete a share link."""
            paperless = get_client(ctx)
            obj = await paperless.share_links(share_link_id)
            await paperless.share_links.delete(obj)
            return {"share_link_id": share_link_id, "deleted": True}
