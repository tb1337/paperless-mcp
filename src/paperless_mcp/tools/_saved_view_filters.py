"""Translate a saved view's filter rules into Paperless' document query parameters.

A saved view stores its query as a list of ``(rule_type, value)`` pairs, where
``rule_type`` is a number Paperless-ngx assigns in ``src/documents/models.py``
and its own web UI resolves to a query parameter through
``src-ui/src/app/data/filter-rule-type.ts``. Nothing in the REST API exposes
that table, so a client that wants to *run* a view rather than read it has to
carry a copy — which is what this module is. Where it and the file above drift
apart, this one is wrong.

Three properties of the storage format do the work here, all of them visible in
``queryParamsFromFilterRules`` in ``src-ui/src/app/utils/query-params.ts``:

- A multi-valued filter is stored as one rule per value, so ``tags__id__all``
  arrives as several rule-6 rows that have to be joined back together.
- The three relations that can be filtered on "is unset" encode that in the
  rule's value instead of in a rule of its own: ``None`` means *is null*, the
  sentinel ``"-1"`` means *is not null*, anything else is an ID.
- A boolean rule stores the string ``"true"`` or ``"false"``, which Paperless
  itself sends as ``1`` / ``0``.

Unlike that function, the two deprecated text rules keep their own parameters
(``title__icontains``, ``title_content``) instead of being upgraded to the
Tantivy-backed ``title_search`` / ``text`` a 3.0 UI rewrites them to. The
substring lookups they were saved as are still served, and are not the same
query as a full-text search; the upgrade would quietly change which documents a
stored view selects, and would reach for a parameter that a pre-3.0 server does
not have at all.

An unknown ``rule_type`` is never dropped. A filter this table cannot read is
one the caller would silently get a *larger* result set for, and a saved view
answered with the wrong documents is worse than a saved view that is not
answered at all — so unknown types are collected and handed back for the tool
to refuse on.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ._paging import normalize_csv_filters

#: What a rule of the three relations that have an "is unset" variant stores in
#: place of an ID to mean *is not null*. ``None`` is the *is null* counterpart.
_NOT_NULL = "-1"


@dataclass(frozen=True, slots=True)
class _Rule:
    """One row of Paperless' filter-rule table.

    Args:
        param: The document query parameter this rule's value belongs in.
        multi: Whether several rules of this type combine into one
            comma-separated parameter instead of overwriting each other.
        isnull_param: The companion parameter carrying the "is (not) set"
            variants, for the three relations that have one.
        boolean: Whether the stored value is a ``"true"`` / ``"false"`` string
            that Paperless expects as ``1`` / ``0``.
    """

    param: str
    multi: bool = False
    isnull_param: str | None = None
    boolean: bool = False


#: Mirrors ``FILTER_RULE_TYPES``. The numbers are Paperless' own and are stable
#: — changing one needs a database migration there — so they are spelled out
#: rather than derived from anything.
_RULES: dict[int, _Rule] = {
    0: _Rule("title__icontains"),
    1: _Rule("content__icontains"),
    2: _Rule("archive_serial_number"),
    3: _Rule("correspondent__id", isnull_param="correspondent__isnull"),
    4: _Rule("document_type__id", isnull_param="document_type__isnull"),
    5: _Rule("is_in_inbox", boolean=True),
    6: _Rule("tags__id__all", multi=True),
    7: _Rule("is_tagged", boolean=True),
    8: _Rule("created__date__lt"),
    9: _Rule("created__date__gt"),
    10: _Rule("created__year"),
    11: _Rule("created__month"),
    12: _Rule("created__day"),
    13: _Rule("added__date__lt"),
    14: _Rule("added__date__gt"),
    15: _Rule("modified__date__lt"),
    16: _Rule("modified__date__gt"),
    17: _Rule("tags__id__none", multi=True),
    18: _Rule("archive_serial_number__isnull", boolean=True),
    19: _Rule("title_content"),
    20: _Rule("query"),
    21: _Rule("more_like_id"),
    22: _Rule("tags__id__in", multi=True),
    23: _Rule("archive_serial_number__gt"),
    24: _Rule("archive_serial_number__lt"),
    25: _Rule("storage_path__id", isnull_param="storage_path__isnull"),
    26: _Rule("correspondent__id__in", multi=True),
    27: _Rule("correspondent__id__none", multi=True),
    28: _Rule("document_type__id__in", multi=True),
    29: _Rule("document_type__id__none", multi=True),
    30: _Rule("storage_path__id__in", multi=True),
    31: _Rule("storage_path__id__none", multi=True),
    32: _Rule("owner__id"),
    33: _Rule("owner__id__in", multi=True),
    34: _Rule("owner__isnull", boolean=True),
    35: _Rule("owner__id__none", multi=True),
    36: _Rule("custom_fields__icontains"),
    37: _Rule("shared_by__id", multi=True),
    38: _Rule("custom_fields__id__all", multi=True),
    39: _Rule("custom_fields__id__in", multi=True),
    40: _Rule("custom_fields__id__none", multi=True),
    41: _Rule("has_custom_fields", boolean=True),
    42: _Rule("custom_field_query"),
    43: _Rule("created__date__lte"),
    44: _Rule("created__date__gte"),
    45: _Rule("added__date__lte"),
    46: _Rule("added__date__gte"),
    47: _Rule("mime_type"),
    48: _Rule("title_search"),
    49: _Rule("text"),
}


@dataclass(frozen=True, slots=True)
class ViewQuery:
    """The document query one saved view's filter rules add up to.

    Args:
        filters: The query parameters to hand to the documents service, in the
            comma-joined form they go out in.
        unsupported: The distinct ``rule_type`` codes that are not in the
            table, in the order they first appeared. Non-empty means *do not
            run this view*: the filters are only the ones that could be read,
            so using them would answer a broader question than the view asks.
    """

    filters: dict[str, Any] = field(default_factory=dict)
    unsupported: tuple[int | None, ...] = ()


def translate_filter_rules(rules: Iterable[Any]) -> ViewQuery:
    """Turn a saved view's filter rules into Paperless document query parameters.

    Args:
        rules: The view's ``SavedViewFilterRule`` entries, each carrying a
            numeric ``rule_type`` and its ``value`` as text.

    Returns:
        The translated :class:`ViewQuery`. Rules whose type is unknown are
        reported in ``unsupported`` rather than raising, so the caller can name
        every one of them at once instead of one per round trip.
    """
    collected: dict[str, Any] = {}
    # A dict rather than a set: the report reads better in the order the view
    # stores its rules than in whatever order a set would hand them back.
    unsupported: dict[int | None, None] = {}
    for rule in rules:
        rule_type = getattr(rule, "rule_type", None)
        spec = _RULES.get(rule_type) if isinstance(rule_type, int) else None
        if spec is None:
            unsupported[rule_type] = None
            continue
        _apply(collected, spec, getattr(rule, "value", None))
    return ViewQuery(filters=normalize_csv_filters(collected), unsupported=tuple(unsupported))


def _apply(filters: dict[str, Any], spec: _Rule, value: str | None) -> None:
    """Merge one rule's value into the query parameters being built."""
    if spec.isnull_param is not None and (value is None or value == _NOT_NULL):
        filters[spec.isnull_param] = 1 if value is None else 0
    elif spec.boolean:
        filters[spec.param] = 1 if value in ("true", "1") else 0
    elif value is None:
        # Every remaining rule needs a value to filter on, and Paperless' own UI
        # omits the parameter rather than sending an empty one — so a view with
        # such a rule genuinely selects the wider set. This is the one place a
        # rule does not reach the query, and it is not a rule going missing.
        return
    elif spec.multi:
        filters.setdefault(spec.param, []).append(value)
    else:
        filters[spec.param] = value


def view_ordering(sort_field: str | None, sort_reverse: bool | None) -> str | None:
    """Return the ``ordering`` parameter a view's sort settings ask for.

    The field is passed through unchecked, unlike the one ``search_documents``
    takes from the model: this one was stored by Paperless, which also sorts by
    columns that tool does not offer (a storage path's name, a custom field).
    An unsortable field costs the view its order, never its documents — DRF
    drops the parameter and falls back to the default ordering.
    """
    if not sort_field:
        return None
    return f"-{sort_field}" if sort_reverse else sort_field
