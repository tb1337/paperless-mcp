"""The constrained argument types the tool signatures publish as schema enums.

A parameter typed ``str`` publishes ``{"type": "string"}``: the allowed values then
live only in the docstring, get re-listed in a module constant for the hand-written
check, and a model that guesses wrong pays a round trip to find out. A ``Literal``
publishes ``{"enum": [...]}``, so the schema *is* the documentation and pydantic
rejects the rest before the tool body runs.

Publishing them is not automatic, and that is worth knowing before adding one. These
are PEP 695 aliases, so each is a ``TypeAliasType`` — a *named* type, which pydantic
renders as an entry in ``$defs`` plus a ``$ref`` pointing at it. Valid JSON Schema,
and invisible in practice: a live check against a real client showed all fifteen of
these arguments reaching the model as a bare ``{}``, values correct and unreachable,
because the client never dereferenced. ``_registry.inline_aliases`` therefore expands
every alias in a tool's annotations before the tool is registered, so what the client
receives carries the values inline. Nothing else is needed here — a new alias is
picked up by that step automatically.

Spelled as ``Literal`` rather than as the pypaperless enums these mirror: every one
of those carries an ``UNKNOWN`` member that is a parsing fallback for a value
Paperless sent, never a value to send back. ``tests/test_arguments.py`` is what ties
each list to the library, and ``tests/test_tool_registration.py`` pins that the
published schema really carries the values inline, so neither can go stale.

The trade this makes deliberately: a value the schema rejects comes back as a
protocol-level error rather than as ``{"error": "invalid_argument"}``, because
pydantic runs before ``safe_tool``. Prevention beats a nicer message for a failure a
schema-aware client no longer produces, and the pydantic message names the allowed
values — which the schema now does too, so the model should not reach that point.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final, Literal

from pypaperless.models.types import CustomFieldType, MatchingAlgorithm

#: How Paperless matches a tag, correspondent, document type or storage path to a
#: document automatically. Named rather than numbered: the API takes 0-6, but a
#: model writing ``"regex"`` needs no legend, and the legend was missing from seven
#: of the ten docstrings that accept this.
type MatchingAlgorithmName = Literal["none", "any", "all", "literal", "regex", "fuzzy", "auto"]

#: Name -> the enum member pypaperless serializes back to Paperless' integer.
MATCHING_ALGORITHMS: Final[Mapping[str, MatchingAlgorithm]] = {
    "none": MatchingAlgorithm.NONE,
    "any": MatchingAlgorithm.ANY,
    "all": MatchingAlgorithm.ALL,
    "literal": MatchingAlgorithm.LITERAL,
    "regex": MatchingAlgorithm.REGEX,
    "fuzzy": MatchingAlgorithm.FUZZY,
    "auto": MatchingAlgorithm.AUTO,
}

#: What a custom field stores. ``data_type`` cannot be changed after creation.
type CustomFieldDataType = Literal[
    "string",
    "url",
    "date",
    "boolean",
    "integer",
    "float",
    "monetary",
    "documentlink",
    "select",
    "longtext",
]

#: Name -> the enum member, so the tool does not re-derive it from the string.
CUSTOM_FIELD_TYPES: Final[Mapping[str, CustomFieldType]] = {
    member.value: member for member in CustomFieldType if member is not CustomFieldType.UNKNOWN
}

#: Which rendition a share link exposes. ``archive`` is the OCR'd PDF Paperless
#: produced; ``original`` is the file as it arrived.
type ShareLinkVersion = Literal["archive", "original"]

#: Paperless-ngx' ``DocumentViewSet.ordering_fields``. Anything else makes the API
#: ignore the parameter silently, which is why it is an enum rather than a string.
type DocumentOrderField = Literal[
    "id",
    "title",
    "created",
    "modified",
    "added",
    "archive_serial_number",
    "correspondent__name",
    "document_type__name",
    "num_notes",
    "owner",
    "page_count",
]

#: The document fields ``update_document`` can unset. Only these four are nullable
#: in Paperless; a title or a content is replaced, never cleared.
type ClearableDocumentField = Literal[
    "correspondent", "document_type", "storage_path", "archive_serial_number"
]

#: The resources ``/api/bulk_edit_objects/`` accepts. Custom fields are absent
#: because the endpoint has no branch for them.
type BulkObjectType = Literal["tags", "correspondents", "document_types", "storage_paths"]

#: A queued task's state. Only ``success`` and ``failure`` are terminal.
type TaskStatusName = Literal["pending", "started", "success", "failure", "revoked"]

#: What a queued task is doing. ``consume_file`` is the one an upload produces.
type TaskTypeName = Literal[
    "consume_file",
    "train_classifier",
    "sanity_check",
    "index_optimize",
    "mail_fetch",
    "llm_index",
    "empty_trash",
    "check_workflows",
    "bulk_update",
    "reprocess_document",
    "build_share_link",
    "bulk_delete",
]

#: One ``custom_field_query``: an expression list — an atom ``[field, operator,
#: value]`` or a logical group ``["AND", [...]]`` — or the JSON text of one.
#:
#: Only the outer shape is typed, and this is the one alias where that is a decision
#: rather than an omission. The expression nests arbitrarily deep, a self-referential
#: alias can only be published as a ``$ref``, and a ``$ref`` is exactly what does not
#: arrive. ``_custom_field_query`` validates the nesting instead, and its rejection
#: names the four accepted forms — a better message than a recursive schema.
#:
#: The list comes first because only the first branch of a union is published; see
#: ``_registry._flat_schema``. Both forms stay accepted, but the expression is the one
#: a caller should reach for, so it is the one the schema advertises.
type CustomFieldQuery = list[Any] | str | None
