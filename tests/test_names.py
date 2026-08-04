"""Tests for the master-data snapshot that resolves IDs to names."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pypaperless.exceptions import ForbiddenError
from pypaperless.models.custom_fields import CustomField
from pypaperless.models.documents.document import Document
from pypaperless.runtime import PaperlessRuntime

from paperless_mcp import names as names_mod
from paperless_mcp.names import NameCache, load_names, name_of, names_of
from tests.conftest import FakeService, named


def _populated(paperless: Any) -> Any:
    paperless.correspondents.filter_results = named(**{"1": "Utilities"})
    paperless.document_types.filter_results = named(**{"2": "Invoice"})
    paperless.storage_paths.filter_results = named(**{"3": "Archive"})
    paperless.tags.filter_results = named(**{"4": "paid", "5": "urgent"})
    paperless.users.filter_results = [SimpleNamespace(id=6, username="clerk")]
    paperless.custom_fields.filter_results = [
        SimpleNamespace(id=7, name="Contract status", data_type="string", extra_data=None)
    ]
    return paperless


def test_name_of_ignores_a_missing_or_unknown_id() -> None:
    assert name_of({1: "Utilities"}, 1) == "Utilities"
    assert name_of({1: "Utilities"}, 2) is None
    assert name_of({1: "Utilities"}, None) is None


def test_names_of_keeps_unknown_ids_positional() -> None:
    """A hole must stay a hole; shifting names onto the wrong tag is worse than None."""
    assert names_of({1: "a", 3: "c"}, [1, 2, 3]) == ["a", None, "c"]
    assert names_of({1: "a"}, None) == []


async def test_load_names_indexes_every_resource(make_paperless: Any) -> None:
    paperless = _populated(make_paperless())

    names = await load_names(paperless)

    assert names.correspondents == {1: "Utilities"}
    assert names.document_types == {2: "Invoice"}
    assert names.storage_paths == {3: "Archive"}
    assert names.tags == {4: "paid", 5: "urgent"}
    assert names.users == {6: "clerk"}


async def test_load_names_fills_the_pypaperless_custom_field_cache(make_paperless: Any) -> None:
    """That cache is what makes a Document carry its custom field names."""
    paperless = _populated(make_paperless())

    await load_names(paperless)

    assert list(paperless.runtime.cache.custom_fields) == [7]


async def test_load_names_survives_an_endpoint_it_may_not_read(make_paperless: Any) -> None:
    """A token without user permissions still gets every other name."""
    paperless = _populated(make_paperless())
    forbidden = ForbiddenError(httpx.Response(403, request=httpx.Request("GET", "http://test/")))
    paperless.users = FakeService()
    paperless.users.pages = _raising(forbidden)

    names = await load_names(paperless)

    assert names.users == {}
    assert names.correspondents == {1: "Utilities"}


def _raising(exc: BaseException) -> Any:
    def pages(**_kwargs: Any) -> Any:
        raise exc

    return pages


async def test_cache_loads_once_and_reloads_after_invalidation(make_paperless: Any) -> None:
    paperless = _populated(make_paperless())
    cache = NameCache(ttl=0)

    first = await cache.get(paperless)
    assert await cache.get(paperless) is first
    assert len(paperless.tags.page_calls) == 1

    cache.invalidate()
    assert await cache.get(paperless) is not first
    assert len(paperless.tags.page_calls) == 2


async def test_concurrent_readers_share_one_load(make_paperless: Any) -> None:
    """Six master-data requests per waiting tool call is what the lock prevents."""
    paperless = _populated(make_paperless())
    cache = NameCache(ttl=0)

    first, second, third = await asyncio.gather(*(cache.get(paperless) for _ in range(3)))

    assert first is second is third
    assert len(paperless.tags.page_calls) == 1


async def test_an_invalidation_during_a_load_is_not_lost(
    monkeypatch: pytest.MonkeyPatch, make_paperless: Any
) -> None:
    """A write while the snapshot is loading must not publish the pre-write read.

    The loading coroutine holds the lock, so ``invalidate()`` finds the snapshot
    already ``None`` and has nothing to clear. Without a generation counter the
    load then stores data read *before* the write, and a just-created tag stays
    invisible for the whole TTL.
    """
    paperless = _populated(make_paperless())
    cache = NameCache(ttl=0)
    started = asyncio.Event()
    release = asyncio.Event()
    original_load = names_mod.load_names

    async def blocking_load(client: Any) -> Any:
        started.set()
        await release.wait()
        return await original_load(client)

    with monkeypatch.context() as blocked:
        blocked.setattr(names_mod, "load_names", blocking_load)
        in_flight = asyncio.ensure_future(cache.get(paperless))
        await started.wait()
        cache.invalidate()
        release.set()
        stale = await in_flight

    # The discarded snapshot was still handed to its own caller - it is the
    # freshest data that existed - but must not be served to the next one. The
    # real loader is back, so this read cannot block whether it reloads or not.
    assert await cache.get(paperless) is not stale


async def test_cache_reloads_once_its_ttl_has_passed(make_paperless: Any) -> None:
    paperless = _populated(make_paperless())
    # A snapshot that expires immediately: the second read has to fetch again.
    cache = NameCache(ttl=1e-9)

    first = await cache.get(paperless)
    assert await cache.get(paperless) is not first


async def test_a_select_custom_field_resolves_to_its_label(make_paperless: Any) -> None:
    """Paperless stores the option ID; only the cached definition holds the label."""
    runtime = PaperlessRuntime(SimpleNamespace(), SimpleNamespace(custom_fields=None))
    payload = {
        "id": 8,
        "name": "Contract status",
        "data_type": "select",
        "extra_data": {"select_options": [{"id": "abc-1", "label": "Active"}]},
    }
    runtime.cache.custom_fields = {8: CustomField.from_data(runtime, payload)}

    doc = Document.from_data(runtime, {"id": 1, "custom_fields": [{"field": 8, "value": "abc-1"}]})

    value = doc.custom_fields.root[0]
    assert value.name == "Contract status"
    assert value.label == "Active"
