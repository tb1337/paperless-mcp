"""Translate the ``custom_field_query`` filter into the string Paperless expects.

Paperless-ngx filters documents by custom field *values* through a single query
parameter carrying a JSON expression: an atom ``[field, operator, value]``, or
``["AND"|"OR", [expr, ...]]`` / ``["NOT", expr]`` wrapped around others. Which
operators an atom may use depends on the referenced field's data type, and a
rejected query comes back as a DRF validation payload keyed by position —
``{"1": {"0": ["'due' is not a valid custom field."]}}`` — which costs a round
trip and still leaves the model guessing which atom was wrong.

So the expression is checked here first, against the definitions
:func:`~paperless_mcp.names.load_names` has cached, and only then handed to
pypaperless' :class:`~pypaperless.builders.CustomFieldQuery`, which owns the
serialization. The tables below mirror ``CustomFieldQueryParser`` in
Paperless-ngx' ``src/documents/filters.py``; where they drift apart, this check
is the one that is wrong.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, cast

from pypaperless.builders import CustomFieldQuery
from pypaperless.models import CustomField
from pypaperless.models.types import CustomFieldType

from ._errors import ToolInputError

_OPS_BY_CATEGORY: dict[str, tuple[str, ...]] = {
    "basic": ("exact", "in", "isnull", "exists"),
    "string": ("icontains", "istartswith", "iendswith"),
    "arithmetic": ("gt", "gte", "lt", "lte", "range"),
    "containment": ("contains",),
}

_CATEGORIES_BY_TYPE: dict[CustomFieldType, tuple[str, ...]] = {
    CustomFieldType.STRING: ("basic", "string"),
    CustomFieldType.LONGTEXT: ("basic", "string"),
    CustomFieldType.URL: ("basic", "string"),
    CustomFieldType.DATE: ("basic", "arithmetic"),
    CustomFieldType.BOOLEAN: ("basic",),
    CustomFieldType.INTEGER: ("basic", "arithmetic"),
    CustomFieldType.FLOAT: ("basic", "arithmetic"),
    CustomFieldType.MONETARY: ("basic", "string", "arithmetic"),
    CustomFieldType.DOCUMENT_LINK: ("basic", "containment"),
    CustomFieldType.SELECT: ("basic",),
}

#: What each data type accepts, flattened out of the two tables above.
_OPS_BY_TYPE: dict[CustomFieldType, frozenset[str]] = {
    data_type: frozenset(op for category in categories for op in _OPS_BY_CATEGORY[category])
    for data_type, categories in _CATEGORIES_BY_TYPE.items()
}

#: Lookups a ``date`` field admits in front of an operator, as in
#: ``month__exact``. They compare against a component of the stored date, so
#: they take an integer rather than a date.
_DATE_COMPONENTS: frozenset[str] = frozenset(
    {"year", "iso_year", "month", "day", "week", "week_day", "iso_week_day", "quarter"}
)

#: Both are hard-coded in Paperless-ngx' ``documents/filters.py`` rather than
#: configurable, so rejecting here can never be stricter than the server.
_MAX_DEPTH = 10
_MAX_ATOMS = 20

_SHAPE_HELP = (
    'an atom ["field", "operator", value], ["AND", [expr, ...]], '
    '["OR", [expr, ...]] or ["NOT", expr]'
)


def build_custom_field_query(query: Any, fields: Mapping[int, CustomField]) -> str:
    """Validate a custom field query expression and serialize it for the API.

    Args:
        query: The expression, either as the JSON text Paperless documents or
            as the decoded lists themselves, which is what arrives when the
            model sends the structure instead of a string.
        fields: The known custom field definitions, keyed by ID. An empty
            mapping skips every check that needs one: an unreadable
            ``/api/custom_fields/`` must not turn a valid query into an error.

    Returns:
        The JSON string to pass as the ``custom_field_query`` filter.

    Raises:
        ToolInputError: When the expression is malformed, references a field
            that does not exist, or uses an operator the field's data type does
            not support.
    """
    return str(_Compiler(fields).compile(_decode(query)))


def _decode(query: Any) -> Any:
    """Accept the expression as JSON text or as the decoded lists themselves."""
    if not isinstance(query, str):
        return query
    try:
        return json.loads(query)
    except json.JSONDecodeError as exc:
        raise ToolInputError(
            f"custom_field_query must be {_SHAPE_HELP}, either as JSON text or as the "
            f"structure itself; the text given is not valid JSON ({exc})."
        ) from exc


def _check_value(op: str, bare: str, value: Any) -> None:
    """Reject the value shapes whose server-side error only names a position."""
    if bare in ("exists", "isnull"):
        if not isinstance(value, bool):
            raise ToolInputError(f"{op!r} takes true or false, got {value!r}.")
    elif bare == "in":
        if not isinstance(value, (list, tuple)) or not value:
            raise ToolInputError(f"{op!r} takes a non-empty list of values, got {value!r}.")
    elif bare == "range" and (not isinstance(value, (list, tuple)) or len(value) != 2):
        raise ToolInputError(
            f"{op!r} takes a list of exactly two values, [start, end], got {value!r}."
        )


class _Compiler:
    """One pass over one expression, carrying the limits Paperless enforces.

    Args:
        fields: The custom field definitions to resolve references against.
    """

    def __init__(self, fields: Mapping[int, CustomField]) -> None:
        """Index the definitions by name as well, since a reference may use either."""
        self._by_id = fields
        self._by_name = {
            field.name: field for field in fields.values() if isinstance(field.name, str)
        }
        self._atoms = 0

    def compile(self, expr: Any, depth: int = 1) -> CustomFieldQuery:
        """Turn one expression — atom or logical — into a builder node.

        Raises:
            ToolInputError: When *expr* is neither an atom nor a logical group,
                or when it nests deeper than Paperless allows.
        """
        if depth > _MAX_DEPTH:
            raise ToolInputError(
                f"A custom field query may not nest more than {_MAX_DEPTH} levels deep."
            )
        if isinstance(expr, (list, tuple)):
            if len(expr) == 2:
                return self._logical(expr[0], expr[1], depth)
            if len(expr) == 3:
                return self._atom(expr[0], expr[1], expr[2])
        raise ToolInputError(f"{expr!r} is not a custom field query expression. Use {_SHAPE_HELP}.")

    def _logical(self, op: Any, args: Any, depth: int) -> CustomFieldQuery:
        """Combine sub-expressions under ``AND``, ``OR`` or ``NOT``.

        Raises:
            ToolInputError: When *op* is not a logical operator, or when
                ``AND``/``OR`` get anything but a non-empty list.
        """
        name = op.lower() if isinstance(op, str) else op
        if name == "not":
            return ~self.compile(args, depth + 1)
        if name not in ("and", "or"):
            # A two-element expression is read as a logical group, so this is
            # also where an atom that forgot its value lands.
            raise ToolInputError(
                f"{op!r} is not a logical operator. A two-element expression is "
                f'["AND", [expr, ...]], ["OR", [expr, ...]] or ["NOT", expr]; an atom '
                f"needs all three of [field, operator, value]."
            )
        if not isinstance(args, (list, tuple)) or not args:
            raise ToolInputError(
                f"{op!r} takes a non-empty list of expressions to combine, got {args!r}."
            )
        combined = self.compile(args[0], depth + 1)
        for arg in args[1:]:
            other = self.compile(arg, depth + 1)
            combined = combined & other if name == "and" else combined | other
        return combined

    def _atom(self, reference: Any, op: Any, value: Any) -> CustomFieldQuery:
        """Check one ``[field, operator, value]`` triple and build it.

        Raises:
            ToolInputError: When the field reference or the operator is not of a
                usable type, or when the query holds too many conditions.
        """
        self._atoms += 1
        if self._atoms > _MAX_ATOMS:
            raise ToolInputError(f"A custom field query may hold at most {_MAX_ATOMS} conditions.")
        # bool before int: `True` is an int, and would look up field ID 1.
        if isinstance(reference, bool) or not isinstance(reference, (int, str)):
            raise ToolInputError(
                f"A custom field is referenced by its name or its ID, got {reference!r}."
            )
        if not isinstance(op, str):
            raise ToolInputError(f"{op!r} is not a custom field query operator.")
        bare = self._check_operator(self._field(reference), op)
        _check_value(op, bare, value)
        # pypaperless narrows the operator to the bare ones; Paperless also takes
        # a date component in front of it, which _check_operator has cleared.
        return CustomFieldQuery(reference, cast("Any", op), value)

    def _field(self, reference: int | str) -> CustomField | None:
        """Resolve a reference, or ``None`` when there is no snapshot to check against.

        Raises:
            ToolInputError: When the snapshot holds no field of that name or ID.
        """
        if not self._by_id:
            return None
        found = (
            self._by_id.get(reference)
            if isinstance(reference, int)
            else self._by_name.get(reference)
        )
        if found is None:
            known = sorted(self._by_name)
            raise ToolInputError(
                f"No custom field is called {reference!r}. Defined: {known}. "
                f"list_custom_fields reports them with the IDs and data types."
            )
        return found

    def _check_operator(self, field: CustomField | None, op: str) -> str:
        """Return the operator without its date component, having checked both.

        Raises:
            ToolInputError: When the field's data type does not support *op*.
        """
        prefix, _, bare = op.rpartition("__")
        data_type = field.data_type if field is not None else None
        allowed = _OPS_BY_TYPE.get(data_type) if data_type is not None else None
        if field is None or data_type is None or allowed is None:
            # No snapshot, or a data type this pypaperless does not know: pass
            # the operator on and let Paperless be the judge.
            return bare
        if prefix and not (data_type is CustomFieldType.DATE and prefix in _DATE_COMPONENTS):
            raise ToolInputError(
                f"{op!r} is not an operator. Only a date field takes a component in front of "
                f"one, and only these: {sorted(_DATE_COMPONENTS)}."
            )
        if bare not in allowed:
            raise ToolInputError(
                f"Custom field {field.id} ({field.name!r}) is of type {data_type.value}, "
                f"which does not support {op!r}. It supports: {sorted(allowed)}."
            )
        return bare
