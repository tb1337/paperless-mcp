"""CRUD tools for tags, correspondents, document types, storage paths, custom fields.

Each resource gets its own list/create/update/delete tools with explicit
signatures, so the MCP JSON schemas stay tight and LLM-friendly.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mcp.server.mcpserver import MCPServer
from pypaperless import PaperlessClient
from pypaperless.const import EndpointPath
from pypaperless.exceptions import BulkEditError
from pypaperless.models.types import CustomFieldType, MatchingAlgorithm

from ..client import ToolContext, get_client, invalidate_names
from ..config import Settings
from ..resources import (
    BULK_OBJECTS,
    CORRESPONDENTS,
    CUSTOM_FIELDS,
    DOCUMENT_TYPES,
    STORAGE_PATHS,
    TAGS,
)
from ._errors import ToolInputError
from ._master_data import create_resource, delete_resource, list_resource, update_resource
from ._paging import paginate
from ._registry import delete_tool, read_tool, register_tools, write_tool
from ._relations import resolve_relations

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


#: Filter argument -> lookup, for the ones every object type understands. All
#: four are the case-insensitive variants, because that is all the FilterSets
#: behind this endpoint offer for a name.
_BULK_NAME_LOOKUPS: Mapping[str, str] = {
    "name_contains": "name__icontains",
    "name_startswith": "name__istartswith",
    "name_endswith": "name__iendswith",
    "name_exact": "name__iexact",
}


def _bulk_filters(
    object_type: str,
    *,
    names: Mapping[str, str | None],
    path_contains: str | None,
    is_root: bool | None,
) -> dict[str, Any]:
    """Translate the filter arguments into the lookups this object type accepts.

    A filter the type does not know is refused rather than sent: Paperless feeds
    the dict to a django-filter FilterSet, which drops keys it does not
    recognize — and a dropped filter does not narrow the selection, it widens it
    to everything.

    Raises:
        ToolInputError: When a filter does not apply to *object_type*.
    """
    filters: dict[str, Any] = {
        _BULK_NAME_LOOKUPS[argument]: value for argument, value in names.items() if value
    }
    if path_contains:
        if object_type != "storage_paths":
            raise ToolInputError(f"path_contains filters storage_paths, not {object_type}")
        filters["path__icontains"] = path_contains
    if is_root is not None:
        if object_type != "tags":
            raise ToolInputError(f"is_root filters tags, not {object_type}")
        filters["is_root"] = is_root
    return filters


async def _delete_objects(
    paperless: PaperlessClient, object_type: str, selection: dict[str, Any]
) -> None:
    """POST a delete to ``/api/bulk_edit_objects/`` and check that it was accepted.

    Goes through the transport rather than
    ``paperless.bulk_edit_objects.delete()`` because that one only ever sends an
    ``objects`` list — pypaperless 6.0.0 predates the ``all`` + ``filters``
    selection this endpoint gained in API v10.

    Raises:
        BulkEditError: When Paperless answers with anything but ``result: OK``.
    """
    data = await paperless.runtime.transport.post(
        EndpointPath.BULK_EDIT_OBJECTS,
        json={"operation": "delete", "object_type": object_type, **selection},
    )
    if not isinstance(data, dict) or data.get("result") != "OK":
        raise BulkEditError(f"Paperless answered {data!r}")


async def list_tags(
    ctx: ToolContext,
    name_contains: str | None = None,
    offset: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    """List tags, optionally filtered by a case-insensitive name substring."""
    return await list_resource(ctx, TAGS, name_contains=name_contains, offset=offset, limit=limit)


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
    new_id = await create_resource(
        ctx,
        TAGS,
        name=name,
        color=color or _DEFAULT_TAG_COLOR,
        is_inbox_tag=is_inbox_tag,
        parent=parent_id,
        **_matching_kwargs(match, matching_algorithm, is_insensitive, for_create=True),
    )
    return {"tag": {"id": new_id, "name": name}}


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
    return await update_resource(
        ctx,
        TAGS,
        tag_id,
        {
            "name": name,
            "color": color,
            "is_inbox_tag": is_inbox_tag,
            "parent": parent_id,
            **_matching_kwargs(match, matching_algorithm, is_insensitive, for_create=False),
        },
    )


async def delete_tag(ctx: ToolContext, tag_id: int) -> dict[str, Any]:
    """Delete a tag. It is removed from every document that carries it."""
    return await delete_resource(ctx, TAGS, tag_id)


async def list_correspondents(
    ctx: ToolContext,
    name_contains: str | None = None,
    offset: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    """List correspondents, optionally filtered by a name substring."""
    return await list_resource(
        ctx, CORRESPONDENTS, name_contains=name_contains, offset=offset, limit=limit
    )


async def create_correspondent(
    ctx: ToolContext,
    name: str,
    match: str | None = None,
    matching_algorithm: int | None = None,
    is_insensitive: bool | None = None,
) -> dict[str, Any]:
    """Create a new correspondent (the sender or recipient of documents)."""
    new_id = await create_resource(
        ctx,
        CORRESPONDENTS,
        name=name,
        **_matching_kwargs(match, matching_algorithm, is_insensitive, for_create=True),
    )
    return {"correspondent": {"id": new_id, "name": name}}


async def update_correspondent(
    ctx: ToolContext,
    correspondent_id: int,
    name: str | None = None,
    match: str | None = None,
    matching_algorithm: int | None = None,
    is_insensitive: bool | None = None,
) -> dict[str, Any]:
    """Update an existing correspondent."""
    return await update_resource(
        ctx,
        CORRESPONDENTS,
        correspondent_id,
        {
            "name": name,
            **_matching_kwargs(match, matching_algorithm, is_insensitive, for_create=False),
        },
    )


async def delete_correspondent(ctx: ToolContext, correspondent_id: int) -> dict[str, Any]:
    """Delete a correspondent."""
    return await delete_resource(ctx, CORRESPONDENTS, correspondent_id)


async def list_document_types(
    ctx: ToolContext,
    name_contains: str | None = None,
    offset: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    """List document types, optionally filtered by a name substring."""
    return await list_resource(
        ctx, DOCUMENT_TYPES, name_contains=name_contains, offset=offset, limit=limit
    )


async def create_document_type(
    ctx: ToolContext,
    name: str,
    match: str | None = None,
    matching_algorithm: int | None = None,
    is_insensitive: bool | None = None,
) -> dict[str, Any]:
    """Create a new document type (invoice, contract, ...)."""
    new_id = await create_resource(
        ctx,
        DOCUMENT_TYPES,
        name=name,
        **_matching_kwargs(match, matching_algorithm, is_insensitive, for_create=True),
    )
    return {"document_type": {"id": new_id, "name": name}}


async def update_document_type(
    ctx: ToolContext,
    document_type_id: int,
    name: str | None = None,
    match: str | None = None,
    matching_algorithm: int | None = None,
    is_insensitive: bool | None = None,
) -> dict[str, Any]:
    """Update an existing document type."""
    return await update_resource(
        ctx,
        DOCUMENT_TYPES,
        document_type_id,
        {
            "name": name,
            **_matching_kwargs(match, matching_algorithm, is_insensitive, for_create=False),
        },
    )


async def delete_document_type(ctx: ToolContext, document_type_id: int) -> dict[str, Any]:
    """Delete a document type."""
    return await delete_resource(ctx, DOCUMENT_TYPES, document_type_id)


async def list_storage_paths(
    ctx: ToolContext,
    name_contains: str | None = None,
    offset: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    """List storage paths, optionally filtered by a name substring."""
    return await list_resource(
        ctx, STORAGE_PATHS, name_contains=name_contains, offset=offset, limit=limit
    )


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
    new_id = await create_resource(
        ctx,
        STORAGE_PATHS,
        name=name,
        path=path,
        **_matching_kwargs(match, matching_algorithm, is_insensitive, for_create=True),
    )
    return {"storage_path": {"id": new_id, "name": name, "path": path}}


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
    return await update_resource(
        ctx,
        STORAGE_PATHS,
        storage_path_id,
        {
            "name": name,
            "path": path,
            **_matching_kwargs(match, matching_algorithm, is_insensitive, for_create=False),
        },
    )


async def delete_storage_path(ctx: ToolContext, storage_path_id: int) -> dict[str, Any]:
    """Delete a storage path."""
    return await delete_resource(ctx, STORAGE_PATHS, storage_path_id)


async def list_custom_fields(
    ctx: ToolContext,
    name_contains: str | None = None,
    offset: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    """List custom field definitions."""
    return await list_resource(
        ctx, CUSTOM_FIELDS, name_contains=name_contains, offset=offset, limit=limit
    )


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
    field_type = CustomFieldType(data_type)
    if field_type is CustomFieldType.UNKNOWN:
        raise ToolInputError(f"Unknown data_type {data_type!r}. Allowed: {_CUSTOM_FIELD_TYPES}.")
    new_id = await create_resource(
        ctx, CUSTOM_FIELDS, name=name, data_type=field_type, extra_data=extra_data
    )
    return {"custom_field": {"id": new_id, "name": name, "data_type": field_type.value}}


async def update_custom_field(
    ctx: ToolContext,
    custom_field_id: int,
    name: str | None = None,
    extra_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update a custom field definition. ``data_type`` cannot be changed."""
    return await update_resource(
        ctx, CUSTOM_FIELDS, custom_field_id, {"name": name, "extra_data": extra_data}
    )


async def delete_custom_field(ctx: ToolContext, custom_field_id: int) -> dict[str, Any]:
    """Delete a custom field definition and all of its stored values."""
    return await delete_resource(ctx, CUSTOM_FIELDS, custom_field_id)


async def bulk_delete_objects(
    ctx: ToolContext,
    object_type: str,
    object_ids: list[int] | None = None,
    object_names: list[str] | None = None,
    name_contains: str | None = None,
    name_startswith: str | None = None,
    name_endswith: str | None = None,
    name_exact: str | None = None,
    path_contains: str | None = None,
    is_root: bool | None = None,
) -> dict[str, Any]:
    """Delete many tags, correspondents, document types or storage paths at once.

    Permanent, with no trash to restore from — unlike a document. The
    documents keep existing; they only lose the assignment, the way
    ``delete_tag`` takes one tag off every document carrying it.

    ``object_type`` is ``tags``, ``correspondents``, ``document_types`` or
    ``storage_paths``. Custom fields are not part of this endpoint;
    ``delete_custom_field`` removes those one at a time.

    Select the objects *either* by naming them *or* by filter, never both —
    a filtered call ignores the list rather than intersecting with it:

    - ``object_ids`` / ``object_names`` spell them out. Pass
      ``object_names`` when the value comes from the conversation,
      ``object_ids`` only when you have it verbatim from a tool result;
      passing both is allowed but they must agree.
    - ``name_contains``, ``name_startswith``, ``name_endswith`` and
      ``name_exact`` (all case-insensitive), plus ``is_root`` for tags and
      ``path_contains`` for storage paths, leave the selecting to Paperless.
      That is the point of them: clearing out 400 stale correspondents costs
      one filter instead of 400 IDs that have to be listed first. Several
      filters combine as AND. A filter matching nothing is an error, not a
      silent no-op.

    ``deleted`` is how many objects the selection covered, counted just
    before the delete went out. For ``tags`` it can understate: Paperless
    also removes the descendants of every matched tag.
    """
    if object_type not in BULK_OBJECTS:
        raise ToolInputError(
            f"object_type must be one of {sorted(BULK_OBJECTS)}, got {object_type!r}"
        )
    resource = BULK_OBJECTS[object_type]
    filters = _bulk_filters(
        object_type,
        names={
            "name_contains": name_contains,
            "name_startswith": name_startswith,
            "name_endswith": name_endswith,
            "name_exact": name_exact,
        },
        path_contains=path_contains,
        is_root=is_root,
    )
    # Before the first request, so a typo cannot leave half the selection
    # deleted and the rest refused.
    object_ids = await resolve_relations(
        ctx,
        field=resource.singular,
        pks=object_ids,
        names=object_names,
        id_field="object_ids",
        name_field="object_names",
    )
    if object_ids and filters:
        raise ToolInputError(
            f"Pass either the {object_type} to delete or a filter selecting them, not both: "
            "a filtered call never looks at object_ids/object_names."
        )

    paperless = await get_client(ctx)
    if filters:
        # limit=0 still costs one request and reports the server's match
        # count, which is the only feedback there is: the endpoint answers a
        # filtered delete with a bare "OK".
        _, matched = await paginate(resource.service(paperless), filters, offset=0, limit=0)
        if not matched:
            raise ToolInputError(
                f"No {object_type} match {filters} — nothing was deleted. "
                f"list_{object_type} shows what exists."
            )
        await _delete_objects(paperless, object_type, {"all": True, "filters": filters})
        selection: dict[str, Any] = filters
        deleted = matched
    elif object_ids:
        await _delete_objects(paperless, object_type, {"objects": object_ids})
        selection = {"object_ids": object_ids}
        deleted = len(object_ids)
    else:
        raise ToolInputError(
            "Nothing selected: pass object_ids/object_names, or a filter over the "
            f"{object_type} to delete."
        )
    invalidate_names(ctx)
    return {"object_type": object_type, "deleted": deleted, "selection": selection}


def register(mcp: MCPServer, settings: Settings) -> None:
    """Register the taxonomy CRUD tools this deployment exposes."""
    register_tools(
        mcp,
        settings,
        (
            read_tool(list_tags),
            write_tool(create_tag, destructive=False, idempotent=False),
            write_tool(update_tag, destructive=True, idempotent=True),
            delete_tool(delete_tag),
            read_tool(list_correspondents),
            write_tool(create_correspondent, destructive=False, idempotent=False),
            write_tool(update_correspondent, destructive=True, idempotent=True),
            delete_tool(delete_correspondent),
            read_tool(list_document_types),
            write_tool(create_document_type, destructive=False, idempotent=False),
            write_tool(update_document_type, destructive=True, idempotent=True),
            delete_tool(delete_document_type),
            read_tool(list_storage_paths),
            write_tool(create_storage_path, destructive=False, idempotent=False),
            write_tool(update_storage_path, destructive=True, idempotent=True),
            delete_tool(delete_storage_path),
            read_tool(list_custom_fields),
            write_tool(create_custom_field, destructive=False, idempotent=False),
            write_tool(update_custom_field, destructive=True, idempotent=True),
            delete_tool(delete_custom_field),
            delete_tool(bulk_delete_objects),
        ),
    )
