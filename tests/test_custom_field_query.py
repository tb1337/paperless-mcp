"""Tests for the custom_field_query DSL translation."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pypaperless.models import CustomField

from paperless_mcp.tools._custom_field_query import (
    _DATE_COMPONENTS,
    _OPS_BY_CATEGORY,
    build_custom_field_query,
)
from paperless_mcp.tools._errors import ToolInputError
from paperless_mcp.tools.documents import search_documents
from tests.conftest import make_runtime

_RUNTIME = make_runtime()


def _field(pk: int, name: str, data_type: str, **extra: Any) -> CustomField:
    return CustomField.from_data(
        _RUNTIME, {"id": pk, "name": name, "data_type": data_type, **extra}
    )


@pytest.fixture
def fields() -> dict[int, CustomField]:
    """One definition per data type family the operator table distinguishes."""
    return {
        1: _field(1, "Due", "date"),
        2: _field(2, "Gross", "monetary"),
        3: _field(
            3,
            "Status",
            "select",
            extra_data={"select_options": [{"id": "o1", "label": "Open"}]},
        ),
        4: _field(4, "Related", "documentlink"),
        5: _field(5, "Notes", "longtext"),
        6: _field(6, "Paid", "boolean"),
    }


def _built(query: Any, fields: dict[int, CustomField]) -> Any:
    return json.loads(build_custom_field_query(query, fields))


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        pytest.param(
            ["Due", "range", ["2024-08-01", "2024-09-01"]],
            ["Due", "range", ["2024-08-01", "2024-09-01"]],
            id="atom",
        ),
        pytest.param([1, "exists", True], [1, "exists", True], id="atom-by-id"),
        pytest.param(
            ["Status", "in", ["o1", "o2"]],
            ["Status", "in", ["o1", "o2"]],
            id="in",
        ),
        pytest.param(["Due", "month__exact", 8], ["Due", "month__exact", 8], id="date-component"),
        pytest.param(
            [4, "contains", [7, 8]],
            [4, "contains", [7, 8]],
            id="documentlink-contains",
        ),
        pytest.param(
            ["NOT", ["Paid", "exact", True]],
            ["NOT", ["Paid", "exact", True]],
            id="not",
        ),
        pytest.param(
            ["AND", [["Due", "gte", "2024-08-01"], ["Gross", "gt", 100]]],
            ["AND", [["Due", "gte", "2024-08-01"], ["Gross", "gt", 100]]],
            id="and",
        ),
        pytest.param(
            ["or", [["Due", "exists", True], ["Gross", "gt", 100]]],
            ["OR", [["Due", "exists", True], ["Gross", "gt", 100]]],
            id="lowercase-operator",
        ),
    ],
)
def test_builds_the_expression_paperless_expects(
    query: Any, expected: Any, fields: dict[int, CustomField]
) -> None:
    assert _built(query, fields) == expected


def test_unwraps_a_group_holding_one_expression(fields: dict[int, CustomField]) -> None:
    """Same meaning, one depth level cheaper — which matters against the cap of 10."""
    assert _built(["AND", [["Due", "exists", True]]], fields) == ["Due", "exists", True]


def test_flattens_a_chain_instead_of_nesting_it(fields: dict[int, CustomField]) -> None:
    """Three OR'd atoms are one group, not two — a nested pair costs a depth level."""
    query = [
        "OR",
        [["Due", "exists", True], ["Gross", "gt", 100], ["Notes", "icontains", "urgent"]],
    ]

    assert _built(query, fields) == query


def test_keeps_mixed_operators_grouped(fields: dict[int, CustomField]) -> None:
    query = [
        "AND",
        [
            ["OR", [["Due", "exists", True], ["Gross", "gt", 5]]],
            ["NOT", ["Notes", "icontains", "draft"]],
        ],
    ]

    assert _built(query, fields) == query


def test_accepts_the_expression_as_json_text(fields: dict[int, CustomField]) -> None:
    """MCP clients send either; rejecting one form would cost the model a retry."""
    text = '["Due", "range", ["2024-08-01", "2024-09-01"]]'

    assert build_custom_field_query(text, fields) == build_custom_field_query(
        json.loads(text), fields
    )


def test_rejects_text_that_is_not_json(fields: dict[int, CustomField]) -> None:
    with pytest.raises(ToolInputError, match="not valid JSON"):
        build_custom_field_query("due after august", fields)


def test_rejects_an_unknown_field_naming_the_defined_ones(
    fields: dict[int, CustomField],
) -> None:
    with pytest.raises(ToolInputError, match=r"'Duo'.*'Due'"):
        build_custom_field_query(["Duo", "exists", True], fields)


@pytest.mark.parametrize(
    ("query", "match"),
    [
        pytest.param(["Due", "icontains", "x"], "does not support", id="string-op-on-date"),
        pytest.param(["Status", "gt", 1], "does not support", id="arithmetic-op-on-select"),
        pytest.param(["Paid", "range", [1, 2]], "does not support", id="range-on-boolean"),
        pytest.param(["Notes", "contains", [1]], "does not support", id="contains-on-text"),
        pytest.param(
            ["Notes", "month__exact", 8], "Only a date field", id="date-component-on-text"
        ),
        pytest.param(["Due", "nope__gte", 1], "Only a date field", id="unknown-component"),
    ],
)
def test_rejects_operators_the_data_type_does_not_support(
    query: Any, match: str, fields: dict[int, CustomField]
) -> None:
    with pytest.raises(ToolInputError, match=match):
        build_custom_field_query(query, fields)


@pytest.mark.parametrize(
    ("query", "match"),
    [
        pytest.param(["Due", "exists", "yes"], "true or false", id="exists-not-bool"),
        pytest.param(["Due", "isnull", 1], "true or false", id="isnull-not-bool"),
        pytest.param(["Due", "in", []], "non-empty list", id="in-empty"),
        pytest.param(["Due", "in", "2024-08-01"], "non-empty list", id="in-scalar"),
        pytest.param(["Due", "range", ["2024-08-01"]], "exactly two", id="range-one-ended"),
        pytest.param(["Due", "range", [1, 2, 3]], "exactly two", id="range-three-ended"),
    ],
)
def test_rejects_values_that_do_not_fit_the_operator(
    query: Any, match: str, fields: dict[int, CustomField]
) -> None:
    with pytest.raises(ToolInputError, match=match):
        build_custom_field_query(query, fields)


@pytest.mark.parametrize(
    ("query", "match"),
    [
        pytest.param(["Due", "exists"], "is not a logical operator", id="atom-missing-value"),
        pytest.param(["AND", []], "non-empty list", id="empty-group"),
        pytest.param(["AND", ["Due"]], "not a custom field query expression", id="bad-member"),
        pytest.param(["Due", "exact", 1, 2], "not a custom field query expression", id="too-long"),
        pytest.param("42", "not a custom field query expression", id="not-a-list"),
        pytest.param([["Due"], "exact", 1], "referenced by its name or its ID", id="bad-reference"),
        pytest.param([True, "exact", 1], "referenced by its name or its ID", id="bool-reference"),
        pytest.param(["Due", 7, 1], "is not a custom field query operator", id="bad-operator"),
    ],
)
def test_rejects_malformed_expressions(
    query: Any, match: str, fields: dict[int, CustomField]
) -> None:
    with pytest.raises(ToolInputError, match=match):
        build_custom_field_query(query, fields)


def test_rejects_nesting_deeper_than_paperless_allows(fields: dict[int, CustomField]) -> None:
    query: Any = ["Due", "exists", True]
    for _ in range(10):
        query = ["NOT", query]

    with pytest.raises(ToolInputError, match="nest more than 10"):
        build_custom_field_query(query, fields)


def test_allows_nesting_up_to_the_limit(fields: dict[int, CustomField]) -> None:
    query: Any = ["Due", "exists", True]
    for _ in range(9):
        query = ["NOT", query]

    assert _built(query, fields) == query


def test_rejects_more_conditions_than_paperless_allows(fields: dict[int, CustomField]) -> None:
    query = ["OR", [["Due", "exists", True]] * 21]

    with pytest.raises(ToolInputError, match="at most 20 conditions"):
        build_custom_field_query(query, fields)


def test_passes_the_expression_through_without_a_snapshot() -> None:
    """An unreadable /api/custom_fields/ must not reject a query that would work."""
    query = ["Whatever", "icontains", "x"]

    assert _built(query, {}) == query


def test_still_checks_the_shape_without_a_snapshot() -> None:
    with pytest.raises(ToolInputError, match="not a custom field query expression"):
        build_custom_field_query(["Whatever"], {})


def test_leaves_an_unknown_data_type_to_paperless() -> None:
    """pypaperless maps a data type it does not know to UNKNOWN; that is not a rejection."""
    fields = {1: _field(1, "Exotic", "quantum")}

    assert _built(["Exotic", "icontains", "x"], fields) == ["Exotic", "icontains", "x"]


def test_search_documents_docstring_carries_the_operator_matrix() -> None:
    """The prose copy of the validator tables cannot be allowed to drift.

    The recursive query type deliberately publishes a bare ``type: "array"``,
    so the ``search_documents`` docstring is the only documentation the model
    gets for the operators and date components — a table edit has to fail here
    until the docstring copy follows.
    """
    doc = search_documents.__doc__ or ""
    missing = sorted(
        {op for ops in _OPS_BY_CATEGORY.values() for op in ops if f"``{op}``" not in doc}
        | {component for component in _DATE_COMPONENTS if f"``{component}``" not in doc}
    )
    assert missing == []
