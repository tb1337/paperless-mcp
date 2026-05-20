"""CRUD tools for tags, correspondents, document types, storage paths, custom fields.

Each resource has its own list/create/update/delete tools with explicit signatures
so the MCP JSON schemas stay tight and LLM-friendly.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from ..client import get_client
from ..config import Settings
from ..formatting import (
    format_correspondent,
    format_custom_field,
    format_document_type,
    format_storage_path,
    format_tag,
)


def register(mcp: FastMCP, settings: Settings) -> None:
    """Register taxonomy CRUD tools."""
    _register_tags(mcp, settings)
    _register_correspondents(mcp, settings)
    _register_document_types(mcp, settings)
    _register_storage_paths(mcp, settings)
    _register_custom_fields(mcp, settings)


# --------------------------------------------------------------------------- tags
def _register_tags(mcp: FastMCP, settings: Settings) -> None:
    @mcp.tool()
    async def list_tags(
        ctx: Context, name_contains: str | None = None, limit: int = 200
    ) -> dict[str, Any]:
        """List tags, optionally filtered by name substring."""
        paperless = get_client(ctx)
        filters: dict[str, Any] = {}
        if name_contains:
            filters["name__icontains"] = name_contains
        items = []
        async for t in paperless.tags.filter(**filters):
            items.append(format_tag(t))
            if len(items) >= limit:
                break
        return {"tags": items, "returned": len(items)}

    if settings.expose_writes:

        @mcp.tool()
        async def create_tag(
            ctx: Context,
            name: str,
            color: str | None = None,
            is_inbox_tag: bool | None = None,
            match: str | None = None,
            matching_algorithm: int | None = None,
        ) -> dict[str, Any]:
            """Create a new tag. ``color`` is a hex string like ``#cccccc``."""
            paperless = get_client(ctx)
            draft = paperless.tags.create()
            draft.name = name
            if color is not None:
                draft.color = color
            if is_inbox_tag is not None:
                draft.is_inbox_tag = is_inbox_tag
            if match is not None:
                draft.match = match
            if matching_algorithm is not None:
                draft.matching_algorithm = matching_algorithm
            new_id = await paperless.tags.save(draft)
            return {"tag": {"id": new_id, "name": name}}

        @mcp.tool()
        async def update_tag(
            ctx: Context,
            tag_id: int,
            name: str | None = None,
            color: str | None = None,
            is_inbox_tag: bool | None = None,
            match: str | None = None,
            matching_algorithm: int | None = None,
        ) -> dict[str, Any]:
            """Update an existing tag. Pass only the fields you want to change."""
            paperless = get_client(ctx)
            obj = await paperless.tags(tag_id)
            if name is not None:
                obj.name = name
            if color is not None:
                obj.color = color
            if is_inbox_tag is not None:
                obj.is_inbox_tag = is_inbox_tag
            if match is not None:
                obj.match = match
            if matching_algorithm is not None:
                obj.matching_algorithm = matching_algorithm
            await paperless.tags.update(obj)
            return format_tag(obj)

    if settings.expose_deletes:

        @mcp.tool()
        async def delete_tag(ctx: Context, tag_id: int) -> dict[str, Any]:
            """Delete a tag."""
            paperless = get_client(ctx)
            obj = await paperless.tags(tag_id)
            await paperless.tags.delete(obj)
            return {"tag_id": tag_id, "deleted": True}


# --------------------------------------------------------------------- correspondents
def _register_correspondents(mcp: FastMCP, settings: Settings) -> None:
    @mcp.tool()
    async def list_correspondents(
        ctx: Context, name_contains: str | None = None, limit: int = 200
    ) -> dict[str, Any]:
        """List correspondents, optionally filtered by name substring."""
        paperless = get_client(ctx)
        filters: dict[str, Any] = {}
        if name_contains:
            filters["name__icontains"] = name_contains
        items = []
        async for c in paperless.correspondents.filter(**filters):
            items.append(format_correspondent(c))
            if len(items) >= limit:
                break
        return {"correspondents": items, "returned": len(items)}

    if settings.expose_writes:

        @mcp.tool()
        async def create_correspondent(
            ctx: Context,
            name: str,
            match: str | None = None,
            matching_algorithm: int | None = None,
        ) -> dict[str, Any]:
            """Create a new correspondent."""
            paperless = get_client(ctx)
            draft = paperless.correspondents.create()
            draft.name = name
            if match is not None:
                draft.match = match
            if matching_algorithm is not None:
                draft.matching_algorithm = matching_algorithm
            new_id = await paperless.correspondents.save(draft)
            return {"correspondent": {"id": new_id, "name": name}}

        @mcp.tool()
        async def update_correspondent(
            ctx: Context,
            correspondent_id: int,
            name: str | None = None,
            match: str | None = None,
            matching_algorithm: int | None = None,
        ) -> dict[str, Any]:
            """Update an existing correspondent."""
            paperless = get_client(ctx)
            obj = await paperless.correspondents(correspondent_id)
            if name is not None:
                obj.name = name
            if match is not None:
                obj.match = match
            if matching_algorithm is not None:
                obj.matching_algorithm = matching_algorithm
            await paperless.correspondents.update(obj)
            return format_correspondent(obj)

    if settings.expose_deletes:

        @mcp.tool()
        async def delete_correspondent(ctx: Context, correspondent_id: int) -> dict[str, Any]:
            """Delete a correspondent."""
            paperless = get_client(ctx)
            obj = await paperless.correspondents(correspondent_id)
            await paperless.correspondents.delete(obj)
            return {"correspondent_id": correspondent_id, "deleted": True}


# -------------------------------------------------------------------- document_types
def _register_document_types(mcp: FastMCP, settings: Settings) -> None:
    @mcp.tool()
    async def list_document_types(
        ctx: Context, name_contains: str | None = None, limit: int = 200
    ) -> dict[str, Any]:
        """List document types, optionally filtered by name substring."""
        paperless = get_client(ctx)
        filters: dict[str, Any] = {}
        if name_contains:
            filters["name__icontains"] = name_contains
        items = []
        async for d in paperless.document_types.filter(**filters):
            items.append(format_document_type(d))
            if len(items) >= limit:
                break
        return {"document_types": items, "returned": len(items)}

    if settings.expose_writes:

        @mcp.tool()
        async def create_document_type(
            ctx: Context,
            name: str,
            match: str | None = None,
            matching_algorithm: int | None = None,
        ) -> dict[str, Any]:
            """Create a new document type."""
            paperless = get_client(ctx)
            draft = paperless.document_types.create()
            draft.name = name
            if match is not None:
                draft.match = match
            if matching_algorithm is not None:
                draft.matching_algorithm = matching_algorithm
            new_id = await paperless.document_types.save(draft)
            return {"document_type": {"id": new_id, "name": name}}

        @mcp.tool()
        async def update_document_type(
            ctx: Context,
            document_type_id: int,
            name: str | None = None,
            match: str | None = None,
            matching_algorithm: int | None = None,
        ) -> dict[str, Any]:
            """Update an existing document type."""
            paperless = get_client(ctx)
            obj = await paperless.document_types(document_type_id)
            if name is not None:
                obj.name = name
            if match is not None:
                obj.match = match
            if matching_algorithm is not None:
                obj.matching_algorithm = matching_algorithm
            await paperless.document_types.update(obj)
            return format_document_type(obj)

    if settings.expose_deletes:

        @mcp.tool()
        async def delete_document_type(ctx: Context, document_type_id: int) -> dict[str, Any]:
            """Delete a document type."""
            paperless = get_client(ctx)
            obj = await paperless.document_types(document_type_id)
            await paperless.document_types.delete(obj)
            return {"document_type_id": document_type_id, "deleted": True}


# ---------------------------------------------------------------------- storage_paths
def _register_storage_paths(mcp: FastMCP, settings: Settings) -> None:
    @mcp.tool()
    async def list_storage_paths(
        ctx: Context, name_contains: str | None = None, limit: int = 200
    ) -> dict[str, Any]:
        """List storage paths, optionally filtered by name substring."""
        paperless = get_client(ctx)
        filters: dict[str, Any] = {}
        if name_contains:
            filters["name__icontains"] = name_contains
        items = []
        async for s in paperless.storage_paths.filter(**filters):
            items.append(format_storage_path(s))
            if len(items) >= limit:
                break
        return {"storage_paths": items, "returned": len(items)}

    if settings.expose_writes:

        @mcp.tool()
        async def create_storage_path(
            ctx: Context,
            name: str,
            path: str,
            match: str | None = None,
            matching_algorithm: int | None = None,
        ) -> dict[str, Any]:
            """Create a new storage path. ``path`` is the Paperless path template."""
            paperless = get_client(ctx)
            draft = paperless.storage_paths.create()
            draft.name = name
            draft.path = path
            if match is not None:
                draft.match = match
            if matching_algorithm is not None:
                draft.matching_algorithm = matching_algorithm
            new_id = await paperless.storage_paths.save(draft)
            return {"storage_path": {"id": new_id, "name": name, "path": path}}

        @mcp.tool()
        async def update_storage_path(
            ctx: Context,
            storage_path_id: int,
            name: str | None = None,
            path: str | None = None,
            match: str | None = None,
            matching_algorithm: int | None = None,
        ) -> dict[str, Any]:
            """Update an existing storage path."""
            paperless = get_client(ctx)
            obj = await paperless.storage_paths(storage_path_id)
            if name is not None:
                obj.name = name
            if path is not None:
                obj.path = path
            if match is not None:
                obj.match = match
            if matching_algorithm is not None:
                obj.matching_algorithm = matching_algorithm
            await paperless.storage_paths.update(obj)
            return format_storage_path(obj)

    if settings.expose_deletes:

        @mcp.tool()
        async def delete_storage_path(ctx: Context, storage_path_id: int) -> dict[str, Any]:
            """Delete a storage path."""
            paperless = get_client(ctx)
            obj = await paperless.storage_paths(storage_path_id)
            await paperless.storage_paths.delete(obj)
            return {"storage_path_id": storage_path_id, "deleted": True}


# ---------------------------------------------------------------------- custom_fields
def _register_custom_fields(mcp: FastMCP, settings: Settings) -> None:
    @mcp.tool()
    async def list_custom_fields(ctx: Context, limit: int = 200) -> dict[str, Any]:
        """List all custom field definitions."""
        paperless = get_client(ctx)
        items = []
        async for cf in paperless.custom_fields.filter():
            items.append(format_custom_field(cf))
            if len(items) >= limit:
                break
        return {"custom_fields": items, "returned": len(items)}

    if settings.expose_writes:

        @mcp.tool()
        async def create_custom_field(
            ctx: Context,
            name: str,
            data_type: str,
            extra_data: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            """Create a custom field. ``data_type`` is one of Paperless' field types.

            Common values: ``string``, ``url``, ``date``, ``boolean``, ``integer``,
            ``float``, ``monetary``, ``documentlink``, ``select``.
            """
            paperless = get_client(ctx)
            draft = paperless.custom_fields.create()
            draft.name = name
            draft.data_type = data_type
            if extra_data is not None:
                draft.extra_data = extra_data
            new_id = await paperless.custom_fields.save(draft)
            return {"custom_field": {"id": new_id, "name": name, "data_type": data_type}}

        @mcp.tool()
        async def update_custom_field(
            ctx: Context,
            custom_field_id: int,
            name: str | None = None,
            extra_data: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            """Update an existing custom field. ``data_type`` cannot be changed."""
            paperless = get_client(ctx)
            obj = await paperless.custom_fields(custom_field_id)
            if name is not None:
                obj.name = name
            if extra_data is not None:
                obj.extra_data = extra_data
            await paperless.custom_fields.update(obj)
            return format_custom_field(obj)

    if settings.expose_deletes:

        @mcp.tool()
        async def delete_custom_field(ctx: Context, custom_field_id: int) -> dict[str, Any]:
            """Delete a custom field definition."""
            paperless = get_client(ctx)
            obj = await paperless.custom_fields(custom_field_id)
            await paperless.custom_fields.delete(obj)
            return {"custom_field_id": custom_field_id, "deleted": True}
