"""Tests for bulk_delete_objects, the tool behind /api/bulk_edit_objects/."""

from __future__ import annotations

from typing import Any

import pytest
from pypaperless.const import EndpointPath

from tests.conftest import build_mcp, call_tool, make_settings, named, tool_session

_ENDPOINT = EndpointPath.BULK_EDIT_OBJECTS


def _posts(paperless: Any) -> list[dict[str, Any]]:
    return paperless.runtime.transport.post_calls


async def test_hidden_without_enable_delete(make_paperless: Any) -> None:
    mcp = build_mcp(make_settings(enable_delete=False), make_paperless())
    assert "bulk_delete_objects" not in mcp._tool_manager._tools


async def test_an_id_list_goes_out_as_objects(make_paperless: Any) -> None:
    paperless = make_paperless()
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(
        mcp, "bulk_delete_objects", object_type="correspondents", object_ids=[4, 5]
    )

    assert result == {
        "object_type": "correspondents",
        "deleted": 2,
        "selection": {"object_ids": [4, 5]},
    }
    assert _posts(paperless) == [
        {
            "path": _ENDPOINT,
            "json": {
                "operation": "delete",
                "objects": [4, 5],
                "object_type": "correspondents",
            },
        }
    ]


async def test_names_resolve_to_ids(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.tags.filter_results = named(**{"7": "Temp", "8": "Keep"})
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "bulk_delete_objects", object_type="tags", object_names=["Temp"])

    assert result["deleted"] == 1
    assert _posts(paperless)[0]["json"]["objects"] == [7]


async def test_an_unknown_name_is_refused_before_the_delete(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.tags.filter_results = named(**{"7": "Temp"})
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "bulk_delete_objects", object_type="tags", object_names=["Nope"])

    assert result["error"] == "invalid_argument"
    assert _posts(paperless) == []


async def test_a_filter_replaces_the_id_list(make_paperless: Any) -> None:
    """The whole point: the selection travels as a filter, not as N ids."""
    paperless = make_paperless()
    paperless.tags.filter_results = named(**{"1": "temp-a", "2": "temp-b", "3": "temp-c"})
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "bulk_delete_objects", object_type="tags", name_contains="temp-")

    assert result == {
        "object_type": "tags",
        "deleted": 3,
        "selection": {"name__icontains": "temp-"},
    }
    assert _posts(paperless) == [
        {
            "path": _ENDPOINT,
            "json": {
                "operation": "delete",
                "all": True,
                "filters": {"name__icontains": "temp-"},
                "object_type": "tags",
            },
        }
    ]


async def test_the_match_count_costs_one_request_and_no_items(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.tags.filter_results = named(**{"1": "a", "2": "b"})
    mcp = build_mcp(make_settings(), paperless)

    await call_tool(mcp, "bulk_delete_objects", object_type="tags", name_exact="a")

    assert paperless.tags.filter_calls == [{"name__iexact": "a"}]
    assert paperless.tags.page_calls == [{"page": 1, "page_size": 1}]


async def test_every_name_lookup_has_its_argument(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.storage_paths.filter_results = named(**{"1": "Archive"})
    mcp = build_mcp(make_settings(), paperless)

    await call_tool(
        mcp,
        "bulk_delete_objects",
        object_type="storage_paths",
        name_contains="a",
        name_startswith="b",
        name_endswith="c",
        name_exact="d",
        path_contains="e",
    )

    assert _posts(paperless)[0]["json"]["filters"] == {
        "name__icontains": "a",
        "name__istartswith": "b",
        "name__iendswith": "c",
        "name__iexact": "d",
        "path__icontains": "e",
    }


async def test_is_root_selects_root_tags(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.tags.filter_results = named(**{"1": "Contract"})
    mcp = build_mcp(make_settings(), paperless)

    await call_tool(mcp, "bulk_delete_objects", object_type="tags", is_root=True)

    assert _posts(paperless)[0]["json"]["filters"] == {"is_root": True}


@pytest.mark.parametrize(
    ("object_type", "kwargs"),
    [
        ("correspondents", {"is_root": True}),
        ("tags", {"path_contains": "x"}),
    ],
)
async def test_a_filter_the_type_ignores_is_refused(
    make_paperless: Any, object_type: str, kwargs: dict[str, Any]
) -> None:
    """A FilterSet drops a lookup it does not know, which widens the delete."""
    paperless = make_paperless()
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "bulk_delete_objects", object_type=object_type, **kwargs)

    assert result["error"] == "invalid_argument"
    assert _posts(paperless) == []


async def test_ids_and_filters_together_are_refused(make_paperless: Any) -> None:
    """Paperless ignores `objects` once `all` is set, so this cannot mean AND."""
    paperless = make_paperless()
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(
        mcp, "bulk_delete_objects", object_type="tags", object_ids=[1], name_contains="temp"
    )

    assert result["error"] == "invalid_argument"
    assert _posts(paperless) == []


@pytest.mark.parametrize("kwargs", [{}, {"object_ids": []}, {"object_names": []}])
async def test_an_empty_selection_is_refused(make_paperless: Any, kwargs: dict[str, Any]) -> None:
    """Without a selection the endpoint would take `all` to mean every tag."""
    paperless = make_paperless()
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "bulk_delete_objects", object_type="tags", **kwargs)

    assert result["error"] == "invalid_argument"
    assert _posts(paperless) == []


async def test_a_filter_matching_nothing_is_refused(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.tags.filter_results = []
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "bulk_delete_objects", object_type="tags", name_contains="nope")

    assert result["error"] == "invalid_argument"
    assert _posts(paperless) == []


async def test_an_unknown_object_type_is_refused(make_paperless: Any) -> None:
    paperless = make_paperless()
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(
        mcp, "bulk_delete_objects", object_type="custom_fields", object_ids=[1]
    )

    assert result["error"] == "invalid_argument"
    assert _posts(paperless) == []


async def test_a_result_other_than_ok_is_an_error(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.runtime.transport.post_result = {"result": "NOT OK"}
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "bulk_delete_objects", object_type="tags", object_ids=[1])

    assert result["error"] == "bulk_edit_failed"


async def test_the_name_snapshot_is_invalidated(make_paperless: Any) -> None:
    """The deleted objects must not keep resolving until the TTL runs out."""
    paperless = make_paperless()
    paperless.tags.filter_results = named(**{"1": "Temp"})
    mcp = build_mcp(make_settings(), paperless)

    async with tool_session(mcp) as call:
        await call("list_tags")
        reads_before = len(paperless.tags.page_calls)

        await call("bulk_delete_objects", object_type="tags", object_ids=[1])
        await call("list_tags")

    assert len(paperless.tags.page_calls) > reads_before + 1
