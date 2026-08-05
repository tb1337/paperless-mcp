"""The published argument enums, tied back to the library they mirror.

`_arguments.py` spells its values out as `Literal` rather than reusing the
pypaperless enums, because each of those carries an `UNKNOWN` member that is a
parsing fallback and never a value to send. That is a copy, and this is what stops
it from becoming a stale one: every list is asserted against the enum it mirrors,
minus `UNKNOWN`.
"""

from __future__ import annotations

from typing import Any, get_args

import pytest
from pypaperless.models.share_links.share_link import ShareLinkFileVersion
from pypaperless.models.tasks import TaskStatus, TaskType
from pypaperless.models.types import CustomFieldType, MatchingAlgorithm

from paperless_mcp.resources import BULK_OBJECTS
from paperless_mcp.tools._arguments import (
    CUSTOM_FIELD_TYPES,
    MATCHING_ALGORITHMS,
    BulkObjectType,
    CustomFieldDataType,
    MatchingAlgorithmName,
    ShareLinkVersion,
    TaskStatusName,
    TaskTypeName,
)


def _values(alias: Any) -> set[str]:
    """The literals an alias publishes.

    ``get_args`` on a PEP 695 alias returns ``()``; the arguments are on its
    ``__value__``.
    """
    return set(get_args(alias.__value__))


@pytest.mark.parametrize(
    ("alias", "enum"),
    [
        (CustomFieldDataType, CustomFieldType),
        (ShareLinkVersion, ShareLinkFileVersion),
        (TaskStatusName, TaskStatus),
        (TaskTypeName, TaskType),
    ],
    ids=["data_type", "file_version", "task_status", "task_type"],
)
def test_the_published_values_are_the_library_values_minus_unknown(
    alias: Any, enum: type[Any]
) -> None:
    """A member added or removed upstream shows up here, not against a live server."""
    assert _values(alias) == {member.value for member in enum} - {enum.UNKNOWN.value}


def test_the_algorithm_names_cover_every_real_matching_algorithm() -> None:
    """Named rather than numbered, so the mapping is what has to stay complete."""
    assert set(MATCHING_ALGORITHMS.values()) == set(MatchingAlgorithm) - {MatchingAlgorithm.UNKNOWN}
    assert set(MATCHING_ALGORITHMS) == _values(MatchingAlgorithmName)


def test_the_custom_field_type_mapping_covers_every_published_name() -> None:
    assert set(CUSTOM_FIELD_TYPES) == _values(CustomFieldDataType)
    assert CustomFieldType.UNKNOWN not in CUSTOM_FIELD_TYPES.values()


def test_the_bulk_object_types_are_the_registry_ones() -> None:
    """One list of bulk-editable resources, published as the enum a model reads."""
    assert _values(BulkObjectType) == set(BULK_OBJECTS)
