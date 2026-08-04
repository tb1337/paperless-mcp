"""The master-data registry, and the tables that must derive from it.

The registry exists so that adding a resource is one edit instead of five. These
tests are what makes that true rather than merely intended: each asserts that a
table which used to restate the list now agrees with it.
"""

from __future__ import annotations

from dataclasses import fields

import pytest

from paperless_mcp.names import NameMap
from paperless_mcp.resources import BULK_OBJECTS, CUSTOM_FIELDS, RELATIONS, RESOURCES, Resource
from paperless_mcp.tools.search import _CATEGORIES
from tests.conftest import make_client


@pytest.mark.parametrize("resource", RESOURCES, ids=[r.key for r in RESOURCES])
def test_every_resource_reaches_its_service_on_a_real_client(resource: Resource) -> None:
    """A typo in the service accessor would only surface on a live call."""
    assert resource.service(make_client()) is not None


@pytest.mark.parametrize("resource", RESOURCES, ids=[r.key for r in RESOURCES])
def test_the_derived_names_follow_the_convention(resource: Resource) -> None:
    assert resource.list_tool == f"list_{resource.key}"
    assert resource.id_argument == f"{resource.singular}_id"


def test_every_relation_lookup_names_a_real_namemap_field() -> None:
    """`lookup()` reads the snapshot by field name, so a typo would read as empty."""
    known = {field.name for field in fields(NameMap)}
    assert {resource.names_field for resource in RELATIONS.values()} <= known


def test_a_resource_without_a_lookup_resolves_to_nothing() -> None:
    """Custom fields carry no `<field>_name` argument, so they have no lookup."""
    assert CUSTOM_FIELDS.names_field is None
    assert CUSTOM_FIELDS.lookup(NameMap(tags={1: "paid"})) == {}


def test_the_relation_arguments_are_exactly_the_resources_with_a_lookup() -> None:
    assert set(RELATIONS) == {r.singular for r in RESOURCES if r.names_field}


def test_bulk_editable_excludes_custom_fields() -> None:
    """`/api/bulk_edit_objects/` has no branch for them, which is why they are out."""
    assert set(BULK_OBJECTS) == {"tags", "correspondents", "document_types", "storage_paths"}
    assert CUSTOM_FIELDS.key not in BULK_OBJECTS


def test_search_reports_every_master_data_resource() -> None:
    """`search_everywhere`'s categories used to be a fifth copy of the list."""
    categories = {key for key, _ in _CATEGORIES}
    assert {resource.key for resource in RESOURCES} <= categories
    # Plus the two that are not master data.
    assert categories - {r.key for r in RESOURCES} == {"documents", "saved_views"}
