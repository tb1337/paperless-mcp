"""Tests for bulk_delete_objects, the tool behind /api/bulk_edit_objects/."""

from __future__ import annotations

from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from pypaperless.const import EndpointPath

from paperless_mcp.tools._paging import MAX_PAGE_LIMIT
from tests.conftest import (
    build_mcp,
    call_tool,
    invoke_tool,
    make_settings,
    named,
    tool_session,
)

_ENDPOINT = EndpointPath.BULK_EDIT_OBJECTS


def _posts(paperless: Any) -> list[dict[str, Any]]:
    posts: list[dict[str, Any]] = paperless.runtime.transport.post_calls
    return posts


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
        "filters": {},
        "object_ids": [4, 5],
        "object_ids_truncated": False,
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
    """The whole point: the selection travels as a filter, not as N ids.

    It is still reported as ids. The endpoint answers a filtered delete with a bare
    "OK" and leaves no trash behind, so what it hit is only knowable if it was read
    before the delete went out - `filters` records the request, `object_ids` the effect.
    """
    paperless = make_paperless()
    paperless.tags.filter_results = named(**{"1": "temp-a", "2": "temp-b", "3": "temp-c"})
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "bulk_delete_objects", object_type="tags", name_contains="temp-")

    assert result == {
        "object_type": "tags",
        "deleted": 3,
        "filters": {"name__icontains": "temp-"},
        "object_ids": [1, 2, 3],
        "object_ids_truncated": False,
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


async def test_a_selection_beyond_the_page_ceiling_deletes_and_reports_bounded(
    make_paperless: Any,
) -> None:
    """150 matches delete fine, and the record of them stays readable.

    The ceiling must not refuse the internal read-back ("limit too large" for a
    tool without a limit argument), but the result must not echo an unbounded
    id list either: an oversized result fails at the client *after* the
    irreversible delete already ran. One ceiling-sized window carries the count
    and the first ids; ``object_ids_truncated`` says the list was cut.
    """
    paperless = make_paperless()
    paperless.tags.filter_results = named(**{str(pk): f"stale-{pk}" for pk in range(1, 151)})
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "bulk_delete_objects", object_type="tags", name_contains="stale")

    assert result["deleted"] == 150
    assert result["object_ids"] == list(range(1, MAX_PAGE_LIMIT + 1))
    assert result["object_ids_truncated"] is True
    assert _posts(paperless)[0]["json"]["filters"] == {"name__icontains": "stale"}
    assert paperless.tags.filter_calls == [{"name__icontains": "stale"}]
    assert paperless.tags.page_calls == [{"page": 1, "page_size": MAX_PAGE_LIMIT}]


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
    """Refused by the published schema now, so it never reaches the tool.

    A `ToolError` rather than `{"error": "invalid_argument"}`: pydantic validates
    the arguments before `safe_tool` can wrap anything, and the message it raises
    names the allowed values. The trade is deliberate - a schema-aware client sees
    the enum and does not send this in the first place.
    """
    paperless = make_paperless()
    mcp = build_mcp(make_settings(), paperless)

    with pytest.raises(ToolError, match="'tags'"):
        await invoke_tool(mcp, "bulk_delete_objects", object_type="custom_fields", object_ids=[1])

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
