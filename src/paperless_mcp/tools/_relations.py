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

from dataclasses import dataclass

from ..client import ToolContext, get_names, invalidate_names
from ..names import NameLookup
from ..resources import RELATIONS
from ._errors import ToolInputError

#: How many candidates an unknown-name error lists before it stops.
_MAX_SUGGESTIONS = 10


def _labelled(lookup: NameLookup, pks: list[int]) -> list[str]:
    """Render IDs as ``name (ID n)``, sorted, for an error message."""
    return sorted(f"{lookup[pk]} (ID {pk})" for pk in pks)


def _match(lookup: NameLookup, name: str, *, field: str, id_arg: str) -> int | None:
    """Return the ID carrying *name*, or ``None`` when no entry does.

    An exact hit wins outright, so two entries differing only in case stay
    reachable; the case-insensitive pass runs only once nothing matched
    verbatim.

    Args:
        lookup: The ID -> name snapshot to search.
        name: The name to resolve.
        field: The relation, for the message's prose.
        id_arg: How the calling tool spells its ID argument. Derived from *field* it
            would be wrong wherever the two differ - a tag is assigned through
            ``tag_ids``, ``add_tag_ids`` or ``tags_all_ids``, and never through a
            ``tag_id`` that no tool has.

    Raises:
        ToolInputError: When several entries answer to the same name.
    """
    hits = [pk for pk, value in lookup.items() if value == name]
    if not hits:
        wanted = name.strip().casefold()
        hits = [pk for pk, value in lookup.items() if value.strip().casefold() == wanted]
    if len(hits) > 1:
        raise ToolInputError(
            f"The {field} name {name!r} is ambiguous: it matches {_labelled(lookup, hits)}. "
            f"Pass the one you mean as {id_arg}."
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


async def _resolve_name(ctx: ToolContext, *, field: str, name: str, id_arg: str) -> int:
    """Return the ID behind *name*, reloading the snapshot once before giving up.

    Raises:
        ToolInputError: When no entry carries the name, or several do.
    """
    list_tool = RELATIONS[field].list_tool
    lookup = RELATIONS[field].lookup(await get_names(ctx))
    match = _match(lookup, name, field=field, id_arg=id_arg)
    if match is not None:
        return match

    # The snapshot may be name_cache_ttl seconds old, so a miss can simply mean
    # the object was created elsewhere since it was taken. One reload settles
    # that; what is still missing afterwards does not exist.
    invalidate_names(ctx)
    fresh = RELATIONS[field].lookup(await get_names(ctx))
    match = _match(fresh, name, field=field, id_arg=id_arg)
    if match is None:
        suggestions = _candidates(fresh, name)
        hint = f" Closest by name: {suggestions}." if suggestions else ""
        raise ToolInputError(
            f"No {field} in Paperless is named {name!r}.{hint} "
            f"{list_tool} reports the ones that exist, and {id_arg} takes an ID directly. "
            f"Creating it is a separate call, never a side effect of this one."
        )
    return match


async def resolve_relation(
    ctx: ToolContext,
    *,
    field: str,
    pk: int | None,
    name: str | None,
    argument: str | None = None,
) -> int | None:
    """Resolve one relation supplied as an ID, as a name, or as both.

    Args:
        ctx: The tool context, for the shared name snapshot.
        field: The relation as the registry knows it, e.g. ``document_type``. Selects
            the lookup and the list tool named in an error.
        pk: The ``<argument>_id`` argument.
        name: The ``<argument>_name`` argument.
        argument: How the calling tool spells the pair, when that is not *field*
            itself. A tag's parent is `parent_id` / `parent_name` while the names it
            resolves against are the tags'.

    Returns:
        The ID to write, or ``None`` when neither half was supplied.

    Raises:
        ToolInputError: When the name is unknown or ambiguous, or when both
            halves were supplied and name different objects.
    """
    spelling = argument or field
    if name is None:
        return pk
    resolved = await _resolve_name(ctx, field=field, name=name, id_arg=f"{spelling}_id")
    if pk is not None and pk != resolved:
        raise ToolInputError(
            f"{spelling}_id={pk} and {spelling}_name={name!r} (ID {resolved}) are different "
            f"objects. Pass only the one you mean."
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
    resolved = [await _resolve_name(ctx, field=field, name=n, id_arg=id_field) for n in names]
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


@dataclass(frozen=True, slots=True)
class Assignment:
    """The three single-valued document relations, resolved to the IDs to write."""

    correspondent: int | None = None
    document_type: int | None = None
    storage_path: int | None = None


async def resolve_assignment(
    ctx: ToolContext,
    *,
    correspondent_id: int | None,
    correspondent_name: str | None,
    document_type_id: int | None,
    document_type_name: str | None,
    storage_path_id: int | None,
    storage_path_name: str | None,
) -> Assignment:
    """Resolve the correspondent, document type and storage path in one pass.

    Exists for the guarantee rather than for the lines: **every name is resolved
    before the first write request goes out**, so a typo cannot leave one field
    assigned and the next refused.
    """
    return Assignment(
        correspondent=await resolve_relation(
            ctx, field="correspondent", pk=correspondent_id, name=correspondent_name
        ),
        document_type=await resolve_relation(
            ctx, field="document_type", pk=document_type_id, name=document_type_name
        ),
        storage_path=await resolve_relation(
            ctx, field="storage_path", pk=storage_path_id, name=storage_path_name
        ),
    )
