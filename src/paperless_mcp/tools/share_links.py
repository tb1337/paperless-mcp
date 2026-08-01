"""Share link tools."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from pypaperless.models.types import ShareLinkFileVersion

from ..client import ToolContext, get_client
from ..config import Settings
from ..formatting import format_share_link
from ._helpers import (
    ToolInputError,
    page_result,
    paginate,
    parse_datetime,
    safe_tool,
    window,
)

_FILE_VERSIONS = ("archive", "original")


def register(mcp: MCPServer, settings: Settings) -> None:
    """Register share-link tools."""

    @mcp.tool()
    @safe_tool
    async def list_share_links(
        ctx: ToolContext,
        document_id: int | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        """List share links, optionally only those of a single document."""
        paperless = await get_client(ctx)
        items: list[Any]
        total: int | None
        if document_id is not None:
            # The share-links collection has no document filter; Paperless
            # exposes a dedicated per-document endpoint instead.
            links = await paperless.documents.share_links(document_id)
            items, total = window(list(links), offset=offset, limit=limit)
        else:
            items, total = await paginate(paperless.share_links, offset=offset, limit=limit)
        return page_result(
            "share_links",
            items,
            offset=offset,
            limit=limit,
            total=total,
            formatter=format_share_link,
            document_id=document_id,
        )

    if settings.expose_writes:

        @mcp.tool()
        @safe_tool
        async def create_share_link(
            ctx: ToolContext,
            document_id: int,
            expiration: str | None = None,
            file_version: str = "archive",
        ) -> dict[str, Any]:
            """Create a publicly reachable share link for a document.

            Anyone holding the returned slug can fetch the file without logging
            in, so prefer setting ``expiration`` (an ISO datetime) — omitting it
            creates a link that never expires. ``file_version`` is ``archive``
            (the OCR'd PDF) or ``original``.
            """
            if file_version not in _FILE_VERSIONS:
                raise ToolInputError(
                    f"file_version must be one of {list(_FILE_VERSIONS)}, got {file_version!r}"
                )
            paperless = await get_client(ctx)
            draft = paperless.share_links.create(
                document=document_id,
                file_version=ShareLinkFileVersion(file_version),
                expiration=(
                    parse_datetime(expiration, field="expiration")
                    if expiration is not None
                    else None
                ),
            )
            new_id = await paperless.share_links.save(draft)
            link = await paperless.share_links(new_id)
            return {"share_link": format_share_link(link)}

    if settings.expose_deletes:

        @mcp.tool()
        @safe_tool
        async def delete_share_link(ctx: ToolContext, share_link_id: int) -> dict[str, Any]:
            """Delete a share link, revoking public access to that document."""
            paperless = await get_client(ctx)
            obj = await paperless.share_links(share_link_id, lazy=True)
            await paperless.share_links.delete(obj)
            return {"share_link_id": share_link_id, "deleted": True}
