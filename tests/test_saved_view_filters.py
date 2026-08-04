"""Tests for the saved-view filter-rule translation table."""

from __future__ import annotations

import pytest

from paperless_mcp.tools._saved_view_filters import (
    _RULES,
    translate_filter_rules,
    view_ordering,
)
from tests.conftest import rule


def test_the_table_covers_every_rule_paperless_defines() -> None:
    """Contiguous by construction, so a gap is a typo rather than a decision."""
    assert sorted(_RULES) == list(range(50))


def test_no_two_rules_claim_the_same_parameter() -> None:
    """Two rules on one parameter would silently overwrite each other."""
    params = [entry.param for entry in _RULES.values()]
    assert len(params) == len(set(params))


@pytest.mark.parametrize(
    ("rule_type", "value", "expected"),
    [
        (0, "invoice", {"title__icontains": "invoice"}),
        (1, "total", {"content__icontains": "total"}),
        (2, "17", {"archive_serial_number": "17"}),
        (3, "4", {"correspondent__id": "4"}),
        (9, "2024-01-01", {"created__date__gt": "2024-01-01"}),
        (11, "8", {"created__month": "8"}),
        (20, "invoice AND 2024", {"query": "invoice AND 2024"}),
        (21, "12", {"more_like_id": "12"}),
        (42, '["Due", "exists", true]', {"custom_field_query": '["Due", "exists", true]'}),
        (44, "2024-08-01", {"created__date__gte": "2024-08-01"}),
        (47, "application/pdf", {"mime_type": "application/pdf"}),
    ],
)
def test_single_valued_rules_map_to_their_parameter(
    rule_type: int, value: str, expected: dict[str, str]
) -> None:
    assert translate_filter_rules([rule(rule_type, value)]).filters == expected


@pytest.mark.parametrize("rule_type", [6, 17, 22, 26, 27, 28, 29, 30, 31, 33, 35, 37, 38, 39, 40])
def test_multi_valued_rules_join_instead_of_overwriting(rule_type: int) -> None:
    query = translate_filter_rules([rule(rule_type, "1"), rule(rule_type, "2")])
    assert list(query.filters.values()) == ["1,2"]


@pytest.mark.parametrize(("value", "expected"), [("true", 1), ("1", 1), ("false", 0), ("0", 0)])
def test_boolean_rules_become_digits(value: str, expected: int) -> None:
    """Paperless stores "true"/"false" but its own UI sends 1/0."""
    assert translate_filter_rules([rule(5, value)]).filters == {"is_in_inbox": expected}


@pytest.mark.parametrize(
    ("rule_type", "param"),
    [(3, "correspondent__isnull"), (4, "document_type__isnull"), (25, "storage_path__isnull")],
)
def test_a_missing_value_asks_for_the_unset_relation(rule_type: int, param: str) -> None:
    assert translate_filter_rules([rule(rule_type, None)]).filters == {param: 1}


@pytest.mark.parametrize(
    ("rule_type", "param"),
    [(3, "correspondent__isnull"), (4, "document_type__isnull"), (25, "storage_path__isnull")],
)
def test_the_negative_sentinel_asks_for_any_relation(rule_type: int, param: str) -> None:
    """``-1`` is Paperless' NEGATIVE_NULL_FILTER_VALUE: the field is set, to anything."""
    assert translate_filter_rules([rule(rule_type, "-1")]).filters == {param: 0}


def test_a_rule_without_an_isnull_variant_keeps_a_negative_value() -> None:
    """The sentinel is only a sentinel for the three relations that define one."""
    assert translate_filter_rules([rule(2, "-1")]).filters == {"archive_serial_number": "-1"}


@pytest.mark.parametrize("rule_type", [0, 20, 47, 6])
def test_a_valueless_rule_filters_on_nothing(rule_type: int) -> None:
    """Only the three relations read a missing value as "is unset"."""
    assert translate_filter_rules([rule(rule_type, None)]).filters == {}


def test_a_valueless_entry_does_not_cost_the_rest_of_a_multi_rule() -> None:
    query = translate_filter_rules([rule(6, "1"), rule(6, None), rule(6, "2")])
    assert query.filters == {"tags__id__all": "1,2"}


def test_a_valueless_boolean_rule_reads_as_false() -> None:
    assert translate_filter_rules([rule(41, None)]).filters == {"has_custom_fields": 0}


def test_the_deprecated_text_rules_keep_their_own_lookups() -> None:
    """Upgrading them to Tantivy's ``text`` would change which documents match."""
    query = translate_filter_rules([rule(19, "rent")])
    assert query.filters == {"title_content": "rent"}


def test_rules_of_different_types_combine() -> None:
    query = translate_filter_rules([rule(6, "1"), rule(6, "2"), rule(3, "9"), rule(7, "true")])
    assert query.filters == {"tags__id__all": "1,2", "correspondent__id": "9", "is_tagged": 1}
    assert query.unsupported == ()


def test_no_rules_is_an_empty_query() -> None:
    assert translate_filter_rules([]) == translate_filter_rules([])
    assert translate_filter_rules([]).filters == {}


def test_unknown_rule_types_are_collected_not_dropped() -> None:
    query = translate_filter_rules([rule(3, "1"), rule(98), rule(99)])
    assert query.unsupported == (98, 99)
    # Still translated, so the caller can report what it did understand.
    assert query.filters == {"correspondent__id": "1"}


def test_one_unknown_type_is_reported_once_however_often_it_appears() -> None:
    query = translate_filter_rules([rule(99, "1"), rule(98), rule(99, "2")])
    assert query.unsupported == (99, 98)


def test_a_rule_without_a_type_counts_as_unsupported() -> None:
    assert translate_filter_rules([rule(None, "x")]).unsupported == (None,)


@pytest.mark.parametrize(
    ("sort_field", "sort_reverse", "expected"),
    [
        ("created", True, "-created"),
        ("created", False, "created"),
        ("title", None, "title"),
        # Sortable in Paperless, absent from search_documents' allowlist.
        ("custom_field_3", True, "-custom_field_3"),
        (None, True, None),
        ("", True, None),
    ],
)
def test_view_ordering(sort_field: str | None, sort_reverse: bool | None, expected: str) -> None:
    assert view_ordering(sort_field, sort_reverse) == expected
