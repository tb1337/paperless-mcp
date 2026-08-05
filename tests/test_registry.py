"""Tool registration: the derived display title, and the annotations a client receives."""

from __future__ import annotations

from typing import Any, Literal, get_args, get_origin, get_type_hints

import pytest

from paperless_mcp.formatting import JsonValue
from paperless_mcp.tools._arguments import (
    ClearableDocumentField,
    CustomFieldQuery,
    MatchingAlgorithmName,
)
from paperless_mcp.tools._registry import humanize, inline_aliases
from tests.conftest import literal_values


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("search_documents", "Search documents"),
        ("get_document_thumbnail", "Get document thumbnail"),
        # Acronyms and proper nouns survive; a bare capitalize() would not.
        ("get_document_ai_suggestions", "Get document AI suggestions"),
        ("get_paperless_info", "Get Paperless info"),
        ("empty_trash", "Empty trash"),
    ],
)
def test_humanize_derives_a_display_title(name: str, expected: str) -> None:
    assert humanize(name) == expected


def test_a_plain_type_is_returned_unchanged() -> None:
    """Nothing to expand means nothing to rebuild — identity, not a copy."""
    assert inline_aliases(int) is int
    assert inline_aliases(Literal["a", "b"]) == Literal["a", "b"]


def test_a_bare_alias_expands_to_its_literal() -> None:
    assert inline_aliases(MatchingAlgorithmName) == MatchingAlgorithmName.__value__


def _optional_alias(algorithm: MatchingAlgorithmName | None = None) -> None:
    """A signature shaped like the tools', to read a resolved annotation off.

    `get_type_hints` turns `X | None` into `typing.Optional[X]`, whose origin is
    `typing.Union` rather than `types.UnionType` - two branches in the rebuild, and
    the one production actually takes cannot be written down here: spelling
    `Optional[...]` in a test is what `UP045` refuses, and rightly.
    """
    del algorithm


@pytest.mark.parametrize(
    "annotation",
    [
        MatchingAlgorithmName | None,
        get_type_hints(_optional_alias)["algorithm"],
        list[MatchingAlgorithmName],
        dict[str, MatchingAlgorithmName],
        list[MatchingAlgorithmName] | None,
    ],
    ids=["union", "resolved-optional", "list", "dict-value", "list-in-union"],
)
def test_an_alias_expands_at_any_nesting_depth(annotation: Any) -> None:
    """The aliases are used as `X | None` and `list[X]`, never bare, in the signatures."""
    assert "MatchingAlgorithmName" not in str(inline_aliases(annotation))
    assert "regex" in str(inline_aliases(annotation))


def test_the_clearable_field_list_keeps_its_shape() -> None:
    """`update_document.clear_fields` is a list of enums, and has to stay one.

    Asserted structurally rather than against a rebuilt type: spelling the expected
    annotation out would restate the four values a fourth time.
    """
    inner, missing = get_args(inline_aliases(list[ClearableDocumentField] | None))
    assert get_origin(inner) is list
    assert set(get_args(get_args(inner)[0])) == literal_values(ClearableDocumentField)
    assert missing is type(None)


def test_a_self_referential_alias_is_left_alone() -> None:
    """It cannot be expanded: a recursive schema *is* a `$ref`, and this must terminate.

    `JsonValue` is the real one in this tree, so the guard is tested against the shape
    that would hang rather than a contrived one.
    """
    assert inline_aliases(JsonValue) is JsonValue
    assert inline_aliases(list[JsonValue] | None) == list[JsonValue] | None


def test_the_query_alias_expands_because_it_is_not_recursive() -> None:
    """`custom_field_query` gave up its recursion precisely so it could be published."""
    assert inline_aliases(CustomFieldQuery) == CustomFieldQuery.__value__
    assert get_args(inline_aliases(CustomFieldQuery))
