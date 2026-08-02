"""Resolve the numeric IDs Paperless returns into names a model can read.

Paperless serializes correspondents, document types, storage paths, tags and
owners as bare IDs, and its REST API offers no way to expand them — which is
why the web UI keeps a master-data store of its own. This module is that
store: one pass over the small master-data endpoints per connection, so every
formatted object can carry a ``<field>_name`` without costing a request per
document.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pypaperless.exceptions import PaperlessError

if TYPE_CHECKING:
    from pypaperless import PaperlessClient
    from pypaperless.models import CustomField

log = logging.getLogger(__name__)

#: ``id -> name`` for a single resource.
type NameLookup = Mapping[int, str]


@dataclass(frozen=True, slots=True)
class NameMap:
    """Snapshot of the names behind every ID a formatted object can carry."""

    correspondents: NameLookup = field(default_factory=dict)
    document_types: NameLookup = field(default_factory=dict)
    storage_paths: NameLookup = field(default_factory=dict)
    tags: NameLookup = field(default_factory=dict)
    users: NameLookup = field(default_factory=dict)


#: What a formatter falls back to when it is called without a snapshot: every
#: lookup misses and the ``<field>_name`` keys come back ``None``.
EMPTY_NAMES = NameMap()


def name_of(lookup: NameLookup, pk: Any) -> str | None:
    """Return the name behind *pk*, or ``None`` when it is unset or unknown."""
    return lookup.get(pk) if isinstance(pk, int) else None


def names_of(lookup: NameLookup, pks: Iterable[Any] | None) -> list[str | None]:
    """Resolve a list of IDs positionally, keeping ``None`` where one is unknown.

    The result lines up index by index with the ID list it came from, so a name
    that could not be resolved leaves a hole instead of shifting its neighbours.
    """
    return [name_of(lookup, pk) for pk in (pks or [])]


async def _collect(service: AsyncIterable[Any], *, resource: str) -> list[Any]:
    """Read every item of a master-data service, tolerating one we cannot access.

    ``/api/users/`` is closed to tokens without the right permissions, and
    losing the owner names must not cost us the correspondent names too.
    """
    try:
        return [item async for item in service]
    except PaperlessError as exc:
        log.warning(
            "Cannot read %s for name resolution (%s: %s); those names stay unresolved.",
            resource,
            type(exc).__name__,
            exc,
        )
        return []


def _index(items: list[Any], attribute: str) -> dict[int, str]:
    """Build ``id -> <attribute>`` over the items that carry both."""
    return {
        item.id: getattr(item, attribute)
        for item in items
        if isinstance(item.id, int) and isinstance(getattr(item, attribute, None), str)
    }


async def load_names(paperless: PaperlessClient) -> NameMap:
    """Read the master-data endpoints once and build a fresh snapshot.

    Also fills pypaperless' custom-field cache, which is what makes a
    ``Document`` carry the name, type and select options of its custom fields.
    That enrichment happens while the document is being validated, so this has
    to run *before* the documents are fetched — afterwards is too late.
    """
    collected = await asyncio.gather(
        _collect(paperless.correspondents, resource="correspondents"),
        _collect(paperless.document_types, resource="document types"),
        _collect(paperless.storage_paths, resource="storage paths"),
        _collect(paperless.tags, resource="tags"),
        _collect(paperless.users, resource="users"),
        _collect(paperless.custom_fields, resource="custom fields"),
    )
    correspondents, document_types, storage_paths, tags, users, custom_fields = collected

    paperless.runtime.cache.custom_fields = {
        item.id: item for item in custom_fields if isinstance(item.id, int)
    }
    return NameMap(
        correspondents=_index(correspondents, "name"),
        document_types=_index(document_types, "name"),
        storage_paths=_index(storage_paths, "name"),
        tags=_index(tags, "name"),
        users=_index(users, "username"),
    )


def cached_custom_fields(paperless: PaperlessClient) -> Mapping[int, CustomField]:
    """Return every known custom field definition, keyed by ID.

    Reads the cache :func:`load_names` fills, so a caller that needs the
    definitions — their data types, their select options — pays for them once
    per snapshot instead of once per call. Await the snapshot first; until then,
    and whenever ``/api/custom_fields/`` could not be read, this is empty.
    """
    return paperless.runtime.cache.custom_fields or {}


def cached_custom_field(paperless: PaperlessClient, pk: int) -> CustomField | None:
    """Return the custom field definition behind *pk*, or ``None`` when unknown."""
    return cached_custom_fields(paperless).get(pk)


class NameCache:
    """Hold one :class:`NameMap` per connection, reloading it when it ages out.

    Args:
        ttl: How many seconds a snapshot stays valid. Renames made outside this
            server (the web UI, another client) are invisible until it expires;
            ``0`` disables expiry, leaving explicit invalidation as the only
            way to refresh.
    """

    def __init__(self, ttl: float) -> None:
        """Start out empty; the first read loads the snapshot."""
        self._ttl = ttl
        self._snapshot: NameMap | None = None
        self._loaded_at = 0.0
        self._lock = asyncio.Lock()

    def invalidate(self) -> None:
        """Drop the snapshot so the next read rebuilds it."""
        self._snapshot = None

    async def get(self, paperless: PaperlessClient) -> NameMap:
        """Return the current snapshot, loading it once for concurrent callers."""
        cached = self._fresh()
        if cached is not None:
            return cached
        async with self._lock:
            cached = self._fresh()
            if cached is None:
                cached = await load_names(paperless)
                self._snapshot = cached
                self._loaded_at = time.monotonic()
            return cached

    def _fresh(self) -> NameMap | None:
        if self._snapshot is None:
            return None
        if self._ttl and time.monotonic() - self._loaded_at >= self._ttl:
            return None
        return self._snapshot
