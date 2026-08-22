"""The CRUD bodies the twenty master-data tools share.

Five resources times list/create/update/delete is one template instantiated twenty
times, and it was written out twenty times: the five delete bodies were identical
modulo three identifiers, the five list and five update bodies likewise.

The twenty *signatures* stay hand-written, one per tool — they are the JSON schema
the model reads, and the contract says to spell every parameter out. Only the bodies
come from here, so "did I remember to invalidate the name snapshot?" stops being a
question asked twenty times.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from functools import partial
from typing import Any, Final

from ..client import ToolContext, get_client, get_names, invalidate_names
from ..formatting import (
    format_correspondent,
    format_custom_field,
    format_document_type,
    format_storage_path,
    format_tag,
)
from ..names import EMPTY_NAMES, NameMap
from ..resources import Resource
from ._paging import page_result, paginate

type Formatter = Callable[..., dict[str, Any]]


def _custom_field(cf: Any, names: NameMap) -> dict[str, Any]:
    """Adapt the names-free custom field formatter to the shared signature.

    A custom field carries no relation to resolve, so its formatter takes no
    snapshot - and every other one does.
    """
    del names
    return format_custom_field(cf)


#: Projection per resource, keyed the way the registry keys them. Lives in the
#: tools layer rather than on ``Resource`` itself, because the registry sits above
#: ``formatting`` and must not import it.
FORMATTERS: Final[Mapping[str, Formatter]] = {
    "tags": format_tag,
    "correspondents": format_correspondent,
    "document_types": format_document_type,
    "storage_paths": format_storage_path,
    "custom_fields": _custom_field,
}


def apply_values(obj: Any, values: Mapping[str, Any]) -> None:
    """Assign every non-``None`` value onto the model instance.

    ``None`` means "not supplied" for an update argument, so it cannot also mean
    "set this to null" — clearing a field is a separate, explicit argument where a
    tool offers it at all.
    """
    for name, value in values.items():
        if value is not None:
            setattr(obj, name, value)


async def _snapshot(ctx: ToolContext, resource: Resource) -> NameMap:
    """The name snapshot this resource's formatter needs, or the empty one.

    A resource with no relation to resolve - a custom field - would otherwise pay
    six master-data requests for names its projection never reads.
    """
    return await get_names(ctx) if resource.names_field else EMPTY_NAMES


async def list_resource(
    ctx: ToolContext,
    resource: Resource,
    *,
    name_contains: str | None,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    """Return one window of a master-data resource, filtered by name substring.

    The snapshot is awaited before the fetch, never after: the same call fills the
    custom-field cache pypaperless enriches an object from while parsing it.
    """
    paperless = await get_client(ctx)
    names = await _snapshot(ctx, resource)
    filters = {"name__icontains": name_contains} if name_contains else {}
    items, total = await paginate(resource.service(paperless), filters, offset=offset, limit=limit)
    return page_result(
        resource.key,
        items,
        offset=offset,
        limit=limit,
        total=total,
        formatter=partial(FORMATTERS[resource.key], names=names),
    )


async def create_resource(ctx: ToolContext, resource: Resource, **values: Any) -> dict[str, Any]:
    """Create one object and report it the way an update reports one.

    Costs one request more than the create: ``save()`` reads the new ID out of
    Paperless' response and discards the rest, so the object has to be read back. It
    is the request the model made anyway — ``{"id": ..., "name": ...}`` was not enough
    to confirm that the matching algorithm, the colour or the path arrived as intended,
    and confirming meant a second call either way.

    The snapshot is taken before the write, as in :func:`update_resource`: it is only
    read for the relations the new object points *at*, and those existed already.
    """
    paperless = await get_client(ctx)
    names = await _snapshot(ctx, resource)
    service = resource.service(paperless)
    new_id = await service.save(service.create(**values))
    created = await service(new_id)
    invalidate_names(ctx)
    return {resource.singular: FORMATTERS[resource.key](created, names=names)}


async def update_resource(
    ctx: ToolContext,
    resource: Resource,
    pk: int,
    values: Mapping[str, Any],
    *,
    clear: Iterable[str] = (),
) -> dict[str, Any]:
    """Assign every supplied value onto one object and write it back.

    ``changed`` is the server's own answer: pypaperless diffs the model against the
    snapshot it was parsed from, so an update that changes nothing reports ``False``
    rather than claiming success.

    ``clear`` names the fields to set to ``None`` — the explicit argument
    :func:`apply_values` points to, since a ``None`` value means "not supplied".
    """
    paperless = await get_client(ctx)
    names = await _snapshot(ctx, resource)
    service = resource.service(paperless)
    obj = await service(pk)
    apply_values(obj, values)
    for field_name in clear:
        setattr(obj, field_name, None)
    changed = await service.update(obj)
    invalidate_names(ctx)
    return {"changed": changed, **FORMATTERS[resource.key](obj, names=names)}


async def delete_resource(ctx: ToolContext, resource: Resource, pk: int) -> dict[str, Any]:
    """Delete one object and drop the snapshot it was named in.

    Fetched lazily: a delete needs the primary key, not the object's fields, so
    this costs one request rather than two.
    """
    service = resource.service(await get_client(ctx))
    await service.delete(await service(pk, lazy=True))
    invalidate_names(ctx)
    return {resource.id_argument: pk, "deleted": True}
