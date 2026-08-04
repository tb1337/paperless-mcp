"""Turn the readable half of a relation argument into the ID Paperless stores.

Every tool that assigns or filters a relation takes it twice: as ``<field>_id``
and as ``<field>_name``. The name is what a request is phrased in and what a
result already reports back, and — unlike an ID — a wrong one cannot quietly
hit a different valid object: mistaking document type 10 for 11 relabels a
document with no error anywhere, while ``"Kündigun"`` resolves to nothing and
says so. It is also the half a human reading along can veto.

Matching is exact, then case-insensitive, and never fuzzy: an archive holding a
tag ``MR-ST 1337`` next to ``MR-ST 1337_2`` leaves no room for a guess. When
both halves arrive they are cross-checked instead of ranked, so the redundancy
works as a checksum rather than hiding a disagreement.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ..client import ToolContext, get_names, invalidate_names
from ..names import NameLookup, NameMap
from ._errors import ToolInputError

#: How many candidates an unknown-name error lists before it stops.
_MAX_SUGGESTIONS = 10

type _Lookup = Callable[[NameMap], NameLookup]

#: Relation, spelled as the tool arguments spell it, mapped to its ``id -> name``
#: lookup and to the tool that lists the resource in full.
_RELATIONS: Mapping[str, tuple[_Lookup, str]] = {
    "correspondent": (lambda names: names.correspondents, "list_correspondents"),
    "document_type": (lambda names: names.document_types, "list_document_types"),
    "storage_path": (lambda names: names.storage_paths, "list_storage_paths"),
    "tag": (lambda names: names.tags, "list_tags"),
}


def _labelled(lookup: NameLookup, pks: list[int]) -> list[str]:
    """Render IDs as ``name (ID n)``, sorted, for an error message."""
    return sorted(f"{lookup[pk]} (ID {pk})" for pk in pks)


def _match(lookup: NameLookup, name: str, *, field: str) -> int | None:
    """Return the ID carrying *name*, or ``None`` when no entry does.

    An exact hit wins outright, so two entries differing only in case stay
    reachable; the case-insensitive pass runs only once nothing matched
    verbatim.

    Raises:
        ToolInputError: When several entries answer to the same name.
    """
    hits = [pk for pk, value in lookup.items() if value == name]
    if not hits:
        wanted = name.strip().casefold()
        hits = [pk for pk, value in lookup.items() if value.strip().casefold() == wanted]
    if len(hits) > 1:
        raise ToolInputError(
            f"{field}_name={name!r} is ambiguous: it matches {_labelled(lookup, hits)}. "
            f"Pass the one you mean as {field}_id."
        )
    return hits[0] if hits else None


def _candidates(lookup: NameLookup, name: str) -> list[str]:
    """Return the entries whose name overlaps *name*, for a "did you mean" hint."""
    wanted = name.strip().casefold()
    overlapping = [
        pk
        for pk, value in lookup.items()
        if wanted and (wanted in value.casefold() or value.strip().casefold() in wanted)
    ]
    return _labelled(lookup, overlapping)[:_MAX_SUGGESTIONS]


async def _resolve_name(ctx: ToolContext, *, field: str, name: str) -> int:
    """Return the ID behind *name*, reloading the snapshot once before giving up.

    Raises:
        ToolInputError: When no entry carries the name, or several do.
    """
    lookup, list_tool = _RELATIONS[field]
    match = _match(lookup(await get_names(ctx)), name, field=field)
    if match is not None:
        return match

    # The snapshot may be name_cache_ttl seconds old, so a miss can simply mean
    # the object was created elsewhere since it was taken. One reload settles
    # that; what is still missing afterwards does not exist.
    invalidate_names(ctx)
    fresh = lookup(await get_names(ctx))
    match = _match(fresh, name, field=field)
    if match is None:
        suggestions = _candidates(fresh, name)
        hint = f" Closest by name: {suggestions}." if suggestions else ""
        raise ToolInputError(
            f"No {field} in Paperless is named {name!r}.{hint} "
            f"{list_tool} reports the ones that exist, and {field}_id takes an ID directly. "
            f"Creating it is a separate call, never a side effect of this one."
        )
    return match


async def resolve_relation(
    ctx: ToolContext,
    *,
    field: str,
    pk: int | None,
    name: str | None,
) -> int | None:
    """Resolve one relation supplied as an ID, as a name, or as both.

    Args:
        ctx: The tool context, for the shared name snapshot.
        field: The relation as the tool spells it, e.g. ``document_type``.
        pk: The ``<field>_id`` argument.
        name: The ``<field>_name`` argument.

    Returns:
        The ID to write, or ``None`` when neither half was supplied.

    Raises:
        ToolInputError: When the name is unknown or ambiguous, or when both
            halves were supplied and name different objects.
    """
    if name is None:
        return pk
    resolved = await _resolve_name(ctx, field=field, name=name)
    if pk is not None and pk != resolved:
        raise ToolInputError(
            f"{field}_id={pk} and {field}_name={name!r} (ID {resolved}) are different objects. "
            f"Pass only the one you mean."
        )
    return resolved


async def resolve_relations(
    ctx: ToolContext,
    *,
    field: str,
    pks: list[int] | None,
    names: list[str] | None,
    id_field: str,
    name_field: str,
) -> list[int] | None:
    """Resolve several members of one relation supplied as IDs, as names, or as both.

    Args:
        ctx: The tool context, for the shared name snapshot.
        field: The relation as the tool spells it, e.g. ``document_type``.
        pks: The ID list argument.
        names: The name list argument.
        id_field: How the tool spells the ID list, for the error message.
        name_field: How the tool spells the name list, for the error message.

    Returns:
        The IDs to write, or ``None`` when neither list was supplied. An empty
        name list resolves to an empty ID list, which is how a tag list is
        cleared.

    Raises:
        ToolInputError: When a name is unknown or ambiguous, or when both lists
            were supplied and describe different sets of objects.
    """
    if names is None:
        return pks
    resolved = [await _resolve_name(ctx, field=field, name=name) for name in names]
    if pks is not None and sorted(pks) != sorted(resolved):
        raise ToolInputError(
            f"{id_field}={pks} and {name_field}={names} (IDs {resolved}) are different sets "
            f"of {field}s. Pass only the one you mean."
        )
    return resolved


async def resolve_tags(
    ctx: ToolContext,
    *,
    pks: list[int] | None,
    names: list[str] | None,
    id_field: str,
    name_field: str,
) -> list[int] | None:
    """Resolve a list of tags supplied as IDs, as names, or as both.

    Args:
        ctx: The tool context, for the shared name snapshot.
        pks: The ID list argument.
        names: The name list argument.
        id_field: How the tool spells the ID list, for the error message.
        name_field: How the tool spells the name list, for the error message.

    Returns:
        The IDs to write, or ``None`` when neither list was supplied. An empty
        name list resolves to an empty ID list, which is how a tag list is
        cleared.

    Raises:
        ToolInputError: When a name is unknown or ambiguous, or when both lists
            were supplied and describe different sets of tags.
    """
    return await resolve_relations(
        ctx,
        field="tag",
        pks=pks,
        names=names,
        id_field=id_field,
        name_field=name_field,
    )
