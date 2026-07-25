"""CRUD tools for tags, correspondents, document types, storage paths, custom fields.

Each resource gets its own list/create/update/delete tools with explicit
signatures, so the MCP JSON schemas stay tight and LLM-friendly.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from pypaperless.models.types import CustomFieldType, MatchingAlgorithm

from ..client import ToolContext, get_client
from ..config import Settings
from ..formatting import (
    format_correspondent,
    format_custom_field,
    format_document_type,
    format_storage_path,
    format_tag,
)
from ._helpers import ToolInputError, page_result, paginate, safe_tool

#: Paperless requires the full matching triple on create; these are the
#: "no automatic matching" defaults, which is the safe choice for objects an
#: LLM creates on the user's behalf.
_MATCH_DEFAULTS: dict[str, Any] = {
    "match": "",
    "matching_algorithm": MatchingAlgorithm.NONE,
    "is_insensitive": True,
}

#: One of the colours from the Paperless-ngx tag palette. ``TagDraft`` requires
#: a colour, so we pick a stable one instead of failing the create.
_DEFAULT_TAG_COLOR = "#a6cee3"

_MATCHING_HELP = (
    "matching_algorithm: 0=none, 1=any word, 2=all words, 3=literal, "
    "4=regex, 5=fuzzy, 6=auto. Defaults to 0 (no auto-matching) on create."
)

_CUSTOM_FIELD_TYPES = ", ".join(
    t.value for t in CustomFieldType if t is not CustomFieldType.UNKNOWN
)


def _matching_kwargs(
    match: str | None,
    matching_algorithm: int | None,
    is_insensitive: bool | None,
    *,
    for_create: bool,
) -> dict[str, Any]:
    """Build the matching-field kwargs, filling create-time defaults."""
    values: dict[str, Any] = dict(_MATCH_DEFAULTS) if for_create else {}
    if match is not None:
        values["match"] = match
    if matching_algorithm is not None:
        values["matching_algorithm"] = _matching_algorithm(matching_algorithm)
    if is_insensitive is not None:
        values["is_insensitive"] = is_insensitive
    return values


def _matching_algorithm(value: int) -> MatchingAlgorithm:
    # MatchingAlgorithm._missing_ maps anything unrecognised to UNKNOWN rather
    # than raising, so an explicit check is what catches a bad value.
    algorithm = MatchingAlgorithm(value)
    if algorithm is MatchingAlgorithm.UNKNOWN:
        raise ToolInputError(f"Unknown matching_algorithm {value!r}. {_MATCHING_HELP}")
    return algorithm


def _apply(obj: Any, values: dict[str, Any]) -> None:
    """Assign every non-``None`` value onto the model instance."""
    for name, value in values.items():
        if value is not None:
            setattr(obj, name, value)


def _name_filters(name_contains: str | None) -> dict[str, Any]:
    return {"name__icontains": name_contains} if name_contains else {}


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
    @safe_tool
    async def list_tags(
        ctx: ToolContext,
        name_contains: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        """List tags, optionally filtered by a case-insensitive name substring."""
        paperless = await get_client(ctx)
        items, total = await paginate(
            paperless.tags, _name_filters(name_contains), offset=offset, limit=limit
        )
        return page_result(
            "tags", items, offset=offset, limit=limit, total=total, formatter=format_tag
        )

    if settings.expose_writes:

        @mcp.tool()
        @safe_tool
        async def create_tag(
            ctx: ToolContext,
            name: str,
            color: str | None = None,
            is_inbox_tag: bool = False,
            parent_id: int | None = None,
            match: str | None = None,
            matching_algorithm: int | None = None,
            is_insensitive: bool | None = None,
        ) -> dict[str, Any]:
            """Create a new tag.

            ``color`` is a hex string like ``#cccccc``; when omitted Paperless
            gets a neutral default. ``matching_algorithm``: 0=none, 1=any word,
            2=all words, 3=literal, 4=regex, 5=fuzzy, 6=auto — defaults to 0,
            i.e. the tag is only ever applied explicitly.
            """
            paperless = await get_client(ctx)
            draft = paperless.tags.create(
                name=name,
                color=color or _DEFAULT_TAG_COLOR,
                is_inbox_tag=is_inbox_tag,
                parent=parent_id,
                **_matching_kwargs(match, matching_algorithm, is_insensitive, for_create=True),
            )
            new_id = await paperless.tags.save(draft)
            return {"tag": {"id": new_id, "name": name}}

        @mcp.tool()
        @safe_tool
        async def update_tag(
            ctx: ToolContext,
            tag_id: int,
            name: str | None = None,
            color: str | None = None,
            is_inbox_tag: bool | None = None,
            parent_id: int | None = None,
            match: str | None = None,
            matching_algorithm: int | None = None,
            is_insensitive: bool | None = None,
        ) -> dict[str, Any]:
            """Update an existing tag. Pass only the fields you want to change."""
            paperless = await get_client(ctx)
            obj = await paperless.tags(tag_id)
            _apply(
                obj,
                {
                    "name": name,
                    "color": color,
                    "is_inbox_tag": is_inbox_tag,
                    "parent": parent_id,
                    **_matching_kwargs(match, matching_algorithm, is_insensitive, for_create=False),
                },
            )
            changed = await paperless.tags.update(obj)
            return {"changed": changed, **format_tag(obj)}

    if settings.expose_deletes:

        @mcp.tool()
        @safe_tool
        async def delete_tag(ctx: ToolContext, tag_id: int) -> dict[str, Any]:
            """Delete a tag. It is removed from every document that carries it."""
            paperless = await get_client(ctx)
            obj = await paperless.tags(tag_id, lazy=True)
            await paperless.tags.delete(obj)
            return {"tag_id": tag_id, "deleted": True}


# --------------------------------------------------------------------- correspondents
def _register_correspondents(mcp: FastMCP, settings: Settings) -> None:
    @mcp.tool()
    @safe_tool
    async def list_correspondents(
        ctx: ToolContext,
        name_contains: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        """List correspondents, optionally filtered by a name substring."""
        paperless = await get_client(ctx)
        items, total = await paginate(
            paperless.correspondents, _name_filters(name_contains), offset=offset, limit=limit
        )
        return page_result(
            "correspondents",
            items,
            offset=offset,
            limit=limit,
            total=total,
            formatter=format_correspondent,
        )

    if settings.expose_writes:

        @mcp.tool()
        @safe_tool
        async def create_correspondent(
            ctx: ToolContext,
            name: str,
            match: str | None = None,
            matching_algorithm: int | None = None,
            is_insensitive: bool | None = None,
        ) -> dict[str, Any]:
            """Create a new correspondent (the sender or recipient of documents)."""
            paperless = await get_client(ctx)
            draft = paperless.correspondents.create(
                name=name,
                **_matching_kwargs(match, matching_algorithm, is_insensitive, for_create=True),
            )
            new_id = await paperless.correspondents.save(draft)
            return {"correspondent": {"id": new_id, "name": name}}

        @mcp.tool()
        @safe_tool
        async def update_correspondent(
            ctx: ToolContext,
            correspondent_id: int,
            name: str | None = None,
            match: str | None = None,
            matching_algorithm: int | None = None,
            is_insensitive: bool | None = None,
        ) -> dict[str, Any]:
            """Update an existing correspondent."""
            paperless = await get_client(ctx)
            obj = await paperless.correspondents(correspondent_id)
            _apply(
                obj,
                {
                    "name": name,
                    **_matching_kwargs(match, matching_algorithm, is_insensitive, for_create=False),
                },
            )
            changed = await paperless.correspondents.update(obj)
            return {"changed": changed, **format_correspondent(obj)}

    if settings.expose_deletes:

        @mcp.tool()
        @safe_tool
        async def delete_correspondent(ctx: ToolContext, correspondent_id: int) -> dict[str, Any]:
            """Delete a correspondent."""
            paperless = await get_client(ctx)
            obj = await paperless.correspondents(correspondent_id, lazy=True)
            await paperless.correspondents.delete(obj)
            return {"correspondent_id": correspondent_id, "deleted": True}


# -------------------------------------------------------------------- document_types
def _register_document_types(mcp: FastMCP, settings: Settings) -> None:
    @mcp.tool()
    @safe_tool
    async def list_document_types(
        ctx: ToolContext,
        name_contains: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        """List document types, optionally filtered by a name substring."""
        paperless = await get_client(ctx)
        items, total = await paginate(
            paperless.document_types, _name_filters(name_contains), offset=offset, limit=limit
        )
        return page_result(
            "document_types",
            items,
            offset=offset,
            limit=limit,
            total=total,
            formatter=format_document_type,
        )

    if settings.expose_writes:

        @mcp.tool()
        @safe_tool
        async def create_document_type(
            ctx: ToolContext,
            name: str,
            match: str | None = None,
            matching_algorithm: int | None = None,
            is_insensitive: bool | None = None,
        ) -> dict[str, Any]:
            """Create a new document type (invoice, contract, ...)."""
            paperless = await get_client(ctx)
            draft = paperless.document_types.create(
                name=name,
                **_matching_kwargs(match, matching_algorithm, is_insensitive, for_create=True),
            )
            new_id = await paperless.document_types.save(draft)
            return {"document_type": {"id": new_id, "name": name}}

        @mcp.tool()
        @safe_tool
        async def update_document_type(
            ctx: ToolContext,
            document_type_id: int,
            name: str | None = None,
            match: str | None = None,
            matching_algorithm: int | None = None,
            is_insensitive: bool | None = None,
        ) -> dict[str, Any]:
            """Update an existing document type."""
            paperless = await get_client(ctx)
            obj = await paperless.document_types(document_type_id)
            _apply(
                obj,
                {
                    "name": name,
                    **_matching_kwargs(match, matching_algorithm, is_insensitive, for_create=False),
                },
            )
            changed = await paperless.document_types.update(obj)
            return {"changed": changed, **format_document_type(obj)}

    if settings.expose_deletes:

        @mcp.tool()
        @safe_tool
        async def delete_document_type(ctx: ToolContext, document_type_id: int) -> dict[str, Any]:
            """Delete a document type."""
            paperless = await get_client(ctx)
            obj = await paperless.document_types(document_type_id, lazy=True)
            await paperless.document_types.delete(obj)
            return {"document_type_id": document_type_id, "deleted": True}


# ---------------------------------------------------------------------- storage_paths
def _register_storage_paths(mcp: FastMCP, settings: Settings) -> None:
    @mcp.tool()
    @safe_tool
    async def list_storage_paths(
        ctx: ToolContext,
        name_contains: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        """List storage paths, optionally filtered by a name substring."""
        paperless = await get_client(ctx)
        items, total = await paginate(
            paperless.storage_paths, _name_filters(name_contains), offset=offset, limit=limit
        )
        return page_result(
            "storage_paths",
            items,
            offset=offset,
            limit=limit,
            total=total,
            formatter=format_storage_path,
        )

    if settings.expose_writes:

        @mcp.tool()
        @safe_tool
        async def create_storage_path(
            ctx: ToolContext,
            name: str,
            path: str,
            match: str | None = None,
            matching_algorithm: int | None = None,
            is_insensitive: bool | None = None,
        ) -> dict[str, Any]:
            """Create a new storage path.

            ``path`` is a Paperless path template, e.g.
            ``{{ created_year }}/{{ correspondent }}/{{ title }}``.
            """
            paperless = await get_client(ctx)
            draft = paperless.storage_paths.create(
                name=name,
                path=path,
                **_matching_kwargs(match, matching_algorithm, is_insensitive, for_create=True),
            )
            new_id = await paperless.storage_paths.save(draft)
            return {"storage_path": {"id": new_id, "name": name, "path": path}}

        @mcp.tool()
        @safe_tool
        async def update_storage_path(
            ctx: ToolContext,
            storage_path_id: int,
            name: str | None = None,
            path: str | None = None,
            match: str | None = None,
            matching_algorithm: int | None = None,
            is_insensitive: bool | None = None,
        ) -> dict[str, Any]:
            """Update an existing storage path."""
            paperless = await get_client(ctx)
            obj = await paperless.storage_paths(storage_path_id)
            _apply(
                obj,
                {
                    "name": name,
                    "path": path,
                    **_matching_kwargs(match, matching_algorithm, is_insensitive, for_create=False),
                },
            )
            changed = await paperless.storage_paths.update(obj)
            return {"changed": changed, **format_storage_path(obj)}

    if settings.expose_deletes:

        @mcp.tool()
        @safe_tool
        async def delete_storage_path(ctx: ToolContext, storage_path_id: int) -> dict[str, Any]:
            """Delete a storage path."""
            paperless = await get_client(ctx)
            obj = await paperless.storage_paths(storage_path_id, lazy=True)
            await paperless.storage_paths.delete(obj)
            return {"storage_path_id": storage_path_id, "deleted": True}


# ---------------------------------------------------------------------- custom_fields
def _register_custom_fields(mcp: FastMCP, settings: Settings) -> None:
    @mcp.tool()
    @safe_tool
    async def list_custom_fields(
        ctx: ToolContext,
        name_contains: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        """List custom field definitions."""
        paperless = await get_client(ctx)
        items, total = await paginate(
            paperless.custom_fields, _name_filters(name_contains), offset=offset, limit=limit
        )
        return page_result(
            "custom_fields",
            items,
            offset=offset,
            limit=limit,
            total=total,
            formatter=format_custom_field,
        )

    if settings.expose_writes:

        @mcp.tool()
        @safe_tool
        async def create_custom_field(
            ctx: ToolContext,
            name: str,
            data_type: str,
            extra_data: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            """Create a custom field definition.

            ``data_type`` is one of: string, url, date, boolean, integer,
            float, monetary, documentlink, select, longtext. For ``select``
            fields pass ``extra_data={"select_options": [{"label": "Open"}]}``;
            for ``monetary`` you may pass ``{"default_currency": "EUR"}``.
            """
            paperless = await get_client(ctx)
            field_type = CustomFieldType(data_type)
            if field_type is CustomFieldType.UNKNOWN:
                raise ToolInputError(
                    f"Unknown data_type {data_type!r}. Allowed: {_CUSTOM_FIELD_TYPES}."
                )
            draft = paperless.custom_fields.create(
                name=name,
                data_type=field_type,
                extra_data=extra_data,
            )
            new_id = await paperless.custom_fields.save(draft)
            return {"custom_field": {"id": new_id, "name": name, "data_type": field_type.value}}

        @mcp.tool()
        @safe_tool
        async def update_custom_field(
            ctx: ToolContext,
            custom_field_id: int,
            name: str | None = None,
            extra_data: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            """Update a custom field definition. ``data_type`` cannot be changed."""
            paperless = await get_client(ctx)
            obj = await paperless.custom_fields(custom_field_id)
            _apply(obj, {"name": name, "extra_data": extra_data})
            changed = await paperless.custom_fields.update(obj)
            return {"changed": changed, **format_custom_field(obj)}

    if settings.expose_deletes:

        @mcp.tool()
        @safe_tool
        async def delete_custom_field(ctx: ToolContext, custom_field_id: int) -> dict[str, Any]:
            """Delete a custom field definition and all of its stored values."""
            paperless = await get_client(ctx)
            obj = await paperless.custom_fields(custom_field_id, lazy=True)
            await paperless.custom_fields.delete(obj)
            return {"custom_field_id": custom_field_id, "deleted": True}
