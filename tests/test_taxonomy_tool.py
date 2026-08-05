"""The taxonomy CRUD matrix: five resources x list/create/update/delete.

Driven over the real draft path. `create_*` builds a genuine pypaperless draft, so
`save()` runs `validate_draft()` and a missing required field fails here instead of
against a live server - which is what the tag-only versions of these tests claimed
to check and could not, having asserted on a namespace they populated themselves.

The four operations are one table because the twenty tool bodies are one template.
What is genuinely per-resource - a tag's parent and colour, a storage path's
template, a custom field's data type - stays in its own test below.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from pypaperless.models.types import CustomFieldType, MatchingAlgorithm

from tests.conftest import (
    PaperlessStub,
    build_mcp,
    call_tool,
    invoke_tool,
    make_client,
    make_settings,
    tool_session,
)


@dataclass(frozen=True, slots=True)
class Resource:
    """One master-data resource, as the twenty tools see it.

    Args:
        key: Plural name: the list tool's suffix and its envelope key.
        singular: Singular name: the create/update/delete tools' suffix, the
            ``<singular>_id`` argument, and the key a create reports under.
        row: The stub row for one object, minus ``id`` and ``name``.
        create: The arguments ``create_<singular>`` needs beyond ``name``.
    """

    key: str
    singular: str
    row: dict[str, Any] = field(default_factory=dict)
    create: dict[str, Any] = field(default_factory=dict)

    @property
    def path(self) -> str:
        return f"/api/{self.key}/"

    @property
    def id_arg(self) -> str:
        return f"{self.singular}_id"

    def rows(self, count: int) -> list[dict[str, Any]]:
        return [
            {"id": pk, "name": f"{self.singular}{pk}", **self.row} for pk in range(1, count + 1)
        ]


_MATCHING = {"matching_algorithm": 0}

RESOURCES: tuple[Resource, ...] = (
    Resource("tags", "tag", row=_MATCHING),
    Resource("correspondents", "correspondent", row=_MATCHING),
    Resource("document_types", "document_type", row=_MATCHING),
    Resource(
        "storage_paths",
        "storage_path",
        row={"path": "{{title}}", **_MATCHING},
        create={"path": "{{title}}"},
    ),
    Resource(
        "custom_fields", "custom_field", row={"data_type": "string"}, create={"data_type": "string"}
    ),
)

_IDS = [resource.key for resource in RESOURCES]


def _server(resource: Resource, count: int = 0, **settings: bool) -> tuple[Any, PaperlessStub]:
    stub = PaperlessStub(collections={resource.path: resource.rows(count)})
    paperless = make_client(stub)
    return build_mcp(make_settings(**settings), paperless), stub


@pytest.mark.parametrize("resource", RESOURCES, ids=_IDS)
async def test_listing_pages_and_filters_by_name(resource: Resource) -> None:
    mcp, stub = _server(resource, count=5)

    page = await call_tool(mcp, f"list_{resource.key}", offset=1, limit=2, name_contains="x")

    assert [item["id"] for item in page[resource.key]] == [2, 3]
    assert page["total"] == 5
    assert page["has_more"] is True
    # The filter reaches Paperless rather than being applied client-side.
    assert stub.requests[-1].params["name__icontains"] == "x"


@pytest.mark.parametrize("resource", RESOURCES, ids=_IDS)
async def test_creating_satisfies_the_real_draft(resource: Resource) -> None:
    """`save()` validates the draft, so an unfilled required field fails here."""
    mcp, stub = _server(resource)

    result = await call_tool(mcp, f"create_{resource.singular}", name="Neu", **resource.create)

    assert result[resource.singular]["name"] == "Neu"
    posted = [r for r in stub.requests if r.method == "POST" and r.path == resource.path]
    assert len(posted) == 1
    assert posted[0].json["name"] == "Neu"


@pytest.mark.parametrize("resource", RESOURCES, ids=_IDS)
async def test_updating_only_sends_what_changed(resource: Resource) -> None:
    """`changed` is the server's answer, not an assumption: an empty diff is False."""
    mcp, stub = _server(resource, count=1)

    renamed = await call_tool(
        mcp, f"update_{resource.singular}", **{resource.id_arg: 1}, name="Neu"
    )
    assert renamed["changed"] is True
    assert renamed["name"] == "Neu"

    unchanged = await call_tool(mcp, f"update_{resource.singular}", **{resource.id_arg: 1})
    assert unchanged["changed"] is False
    assert [r.method for r in stub.requests if r.path == f"{resource.path}1/"] == [
        "GET",
        "PATCH",
        "GET",
    ]


@pytest.mark.parametrize("resource", RESOURCES, ids=_IDS)
async def test_deleting_removes_the_object(resource: Resource) -> None:
    mcp, stub = _server(resource, count=1, enable_delete=True)

    result = await call_tool(mcp, f"delete_{resource.singular}", **{resource.id_arg: 1})

    assert result == {resource.id_arg: 1, "deleted": True}
    assert stub.collections[resource.path] == []
    # Fetched lazily: a delete needs the primary key, not the whole object.
    assert [r.method for r in stub.requests if r.path == f"{resource.path}1/"] == ["DELETE"]


@pytest.mark.parametrize("resource", RESOURCES, ids=_IDS)
async def test_delete_is_hidden_without_enable_delete(resource: Resource) -> None:
    mcp, _ = _server(resource, enable_delete=False)
    assert f"delete_{resource.singular}" not in mcp._tool_manager._tools


@pytest.mark.parametrize("resource", RESOURCES, ids=_IDS)
async def test_a_missing_object_is_reported_not_raised(resource: Resource) -> None:
    mcp, _ = _server(resource)

    result = await call_tool(mcp, f"update_{resource.singular}", **{resource.id_arg: 99}, name="x")

    assert result["error"] == "not_found"


async def test_create_tag_fills_the_full_matching_triple() -> None:
    """TagDraft requires colour and all three matching fields, not just a name."""
    mcp, stub = _server(RESOURCES[0])

    await call_tool(mcp, "create_tag", name="Invoice", color="#abcdef")

    posted = next(r for r in stub.requests if r.method == "POST").json
    assert posted["color"] == "#abcdef"
    assert posted["is_inbox_tag"] is False
    assert posted["match"] == ""
    # The wire form is the integer Paperless expects, not the enum member - which
    # is exactly what a test asserting on a hand-built namespace could not see.
    assert posted["matching_algorithm"] == MatchingAlgorithm.NONE.value
    assert posted["is_insensitive"] is True


async def test_create_tag_defaults_the_colour() -> None:
    mcp, stub = _server(RESOURCES[0])

    await call_tool(mcp, "create_tag", name="Invoice")

    assert next(r for r in stub.requests if r.method == "POST").json["color"].startswith("#")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("auto", MatchingAlgorithm.AUTO),
        ("literal", MatchingAlgorithm.LITERAL),
        ("none", MatchingAlgorithm.NONE),
        ("any", MatchingAlgorithm.ANY),
        ("all", MatchingAlgorithm.ALL),
        ("regex", MatchingAlgorithm.REGEX),
        ("fuzzy", MatchingAlgorithm.FUZZY),
    ],
)
async def test_create_tag_maps_the_algorithm_name_to_the_wire_integer(
    name: str, expected: MatchingAlgorithm
) -> None:
    """The model names it; Paperless takes 0-6, and the mapping happens here."""
    mcp, stub = _server(RESOURCES[0])

    await call_tool(mcp, "create_tag", name="Invoice", match="acme", matching_algorithm=name)

    posted = next(r for r in stub.requests if r.method == "POST").json
    assert posted["matching_algorithm"] == expected.value


async def test_create_tag_can_ask_for_case_sensitive_matching() -> None:
    """The create-time default is insensitive, so False has to be sent explicitly."""
    mcp, stub = _server(RESOURCES[0])

    await call_tool(mcp, "create_tag", name="ACME", match="ACME", is_insensitive=False)

    assert next(r for r in stub.requests if r.method == "POST").json["is_insensitive"] is False


@pytest.mark.parametrize("bad", ["banana", 6, 99])
async def test_create_tag_rejects_an_unknown_matching_algorithm(bad: object) -> None:
    """Refused by the schema, including the integers the argument used to take.

    `MatchingAlgorithm` maps anything unrecognised to `UNKNOWN` rather than raising,
    so this used to need a hand-written check. The enum makes the seven names the
    only accepted values - and rejects the old numeric spelling, which is the
    breaking half of this change.
    """
    mcp, stub = _server(RESOURCES[0])

    with pytest.raises(ToolError, match="'none'"):
        await invoke_tool(mcp, "create_tag", name="Invoice", matching_algorithm=bad)

    assert [r for r in stub.requests if r.method == "POST"] == []


async def test_update_tag_does_not_leak_the_create_time_defaults() -> None:
    """`match=""` is a create-time default; an update must not send it unasked."""
    mcp, stub = _server(RESOURCES[0], count=1)

    await call_tool(mcp, "update_tag", tag_id=1, name="New")

    patched = next(r for r in stub.requests if r.method == "PATCH").json
    assert patched == {"name": "New"}


async def test_list_tags_resolves_the_parent_name() -> None:
    mcp, stub = _server(RESOURCES[0], count=2)
    stub.collections["/api/tags/"][1]["parent"] = 1

    page = await call_tool(mcp, "list_tags")

    assert page["tags"][1]["parent"] == 1
    assert page["tags"][1]["parent_name"] == "tag1"


async def test_create_storage_path_sends_the_template() -> None:
    mcp, stub = _server(RESOURCES[3])

    await call_tool(mcp, "create_storage_path", name="Tax", path="{{correspondent}}/{{title}}")

    assert next(r for r in stub.requests if r.method == "POST").json["path"] == (
        "{{correspondent}}/{{title}}"
    )


async def test_create_custom_field_converts_the_data_type() -> None:
    mcp, stub = _server(RESOURCES[4])

    result = await call_tool(mcp, "create_custom_field", name="Amount", data_type="monetary")

    assert result["custom_field"]["data_type"] == "monetary"
    posted = next(r for r in stub.requests if r.method == "POST").json
    assert posted["data_type"] == CustomFieldType.MONETARY.value


async def test_create_custom_field_rejects_an_unknown_type() -> None:
    """The ten data types are the schema's enum now, not a docstring list."""
    mcp, stub = _server(RESOURCES[4])

    with pytest.raises(ToolError, match="'string'"):
        await invoke_tool(mcp, "create_custom_field", name="X", data_type="quaternion")

    assert [r for r in stub.requests if r.method == "POST"] == []


async def test_creating_a_tag_invalidates_the_name_snapshot() -> None:
    """A tag created through this server must not stay nameless until the TTL.

    Asserted on the observable - the new tag's name resolves on the next read -
    rather than by counting requests, which would pass for any number of extra
    reads including a pathological loop.
    """
    mcp, stub = _server(RESOURCES[0], count=1)

    async with tool_session(mcp) as call:
        first = await call("list_tags")
        assert [tag["name"] for tag in first["tags"]] == ["tag1"]

        await call("create_tag", name="Electricity")
        # The child points at the tag that was just created, so its parent_name
        # can only resolve if the snapshot was rebuilt.
        stub.collections["/api/tags/"].append(
            {"id": 3, "name": "Sub", "parent": 2, "matching_algorithm": 0}
        )
        again = await call("list_tags")

    assert [tag["name"] for tag in again["tags"]] == ["tag1", "Electricity", "Sub"]
    assert again["tags"][2]["parent_name"] == "Electricity"
