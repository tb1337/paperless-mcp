"""Write the custom field *values* a document carries.

The field definitions belong to :mod:`~paperless_mcp.tools.taxonomy`; this
module owns the values attached to a document. Paperless has no per-field
endpoint for them: a document PATCH replaces the whole ``custom_fields``
array, so both tools read the document, change one entry and write it back.

pypaperless validates a drafted value leniently — ``1`` becomes ``True`` for a
boolean field and ``1.0`` becomes ``1`` for an integer one — which would let a
model store something it did not mean. The checks here run first and reject
those instead, so a mismatch comes back as a correctable error rather than as
a silently coerced value.
"""

from __future__ import annotations

import re
from typing import Any, Never

from mcp.server.mcpserver import MCPServer
from pypaperless import PaperlessClient
from pypaperless.models import CustomField, DocumentCustomFieldList
from pypaperless.models.types import (
    CustomFieldMonetaryValue,
    CustomFieldType,
    CustomFieldValue,
)

from ..client import ToolContext, get_client, get_names
from ..config import Settings
from ..formatting import safe_dump
from ..names import cached_custom_field
from ._dates import parse_date
from ._errors import ToolInputError, ToolResultError, safe_tool
from ._paging import paginate
from ._registry import write_tool

#: An amount with an optional ISO 4217 prefix: ``6589``, ``-6589.00``, ``EUR6589.00``.
_MONETARY = re.compile(r"^(?P<currency>[A-Za-z]{3})?(?P<amount>-?\d+(?:\.\d+)?)$")

_CURRENCY_CODE = re.compile(r"^[A-Za-z]{3}$")

#: Paperless keeps the currency inside the stored value, so every monetary
#: write has to name one. This is the last resort when neither the call nor the
#: field definition does.
_FALLBACK_CURRENCY = "EUR"


def _type_name(field: CustomField) -> str:
    """Return the field's data type as the string ``list_custom_fields`` reports."""
    return field.data_type.value if field.data_type is not None else "unknown"


def _reject(field: CustomField, expected: str, value: Any) -> Never:
    """Raise the uniform "that value does not fit this data type" input error."""
    raise ToolInputError(
        f"Custom field {field.id} ({field.name!r}) is of type {_type_name(field)}, "
        f"which takes {expected}; got {value!r}."
    )


def _definition(paperless: PaperlessClient, custom_field_id: int) -> CustomField:
    """Return the cached definition of *custom_field_id*.

    Raises:
        ToolResultError: When no custom field carries that ID.
    """
    field = cached_custom_field(paperless, custom_field_id)
    if field is None:
        raise ToolResultError(
            "not_found",
            f"No custom field is defined with ID {custom_field_id} — this is the ID of the "
            f"field itself, not of a document. list_custom_fields reports the valid ones; a "
            f"field created outside this server shows up once the name snapshot expires.",
            custom_field_id=custom_field_id,
        )
    return field


def _option_id(field: CustomField, value: Any) -> str:
    """Resolve a select value given as an option ID or as its exact label."""
    options = list(field.extra_data.select_options) if field.extra_data else []
    for option in options:
        if option is None or option.id is None:
            continue
        if str(value) == option.id or value == option.label:
            return option.id
    labels = [option.label for option in options if option is not None]
    raise ToolInputError(
        f"Custom field {field.id} ({field.name!r}) has no option {value!r}. "
        f"Pass one of these labels or its option ID: {labels}."
    )


def _link_ids(field: CustomField, value: Any) -> list[int]:
    """Validate a documentlink value as a list of document IDs."""
    if not isinstance(value, list) or any(
        isinstance(item, bool) or not isinstance(item, int) for item in value
    ):
        _reject(field, "a list of document IDs", value)
    return list(value)


def _currency_code(field: CustomField, explicit: str | None, embedded: str | None) -> str:
    """Pick the currency for a monetary write: the call, the value, the definition."""
    if explicit is not None:
        if embedded is not None and explicit.upper() != embedded.upper():
            raise ToolInputError(
                f"value names the currency {embedded!r} but currency says {explicit!r}; "
                f"pass only one of them."
            )
        if _CURRENCY_CODE.match(explicit) is None:
            raise ToolInputError(
                f"currency must be a three-letter ISO 4217 code such as 'EUR', got {explicit!r}."
            )
        return explicit.upper()
    if embedded is not None:
        return embedded.upper()
    default = field.extra_data.default_currency if field.extra_data else None
    if default is not None and _CURRENCY_CODE.match(default) is not None:
        return default.upper()
    return _FALLBACK_CURRENCY


def _monetary_value(field: CustomField, value: Any, currency: str | None) -> CustomFieldValue:
    """Draft a monetary value as the ``<CUR><amount>`` string Paperless stores."""
    embedded: str | None = None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        _reject(field, "a number or a string such as 'EUR6589.00'", value)
    if isinstance(value, str):
        match = _MONETARY.match(value.strip())
        if match is None:
            _reject(field, "a number or a string such as 'EUR6589.00'", value)
        embedded = match["currency"]
        amount = float(match["amount"])
    else:
        amount = float(value)

    money = field.draft_value(None, CustomFieldMonetaryValue)
    # Order matters: the amount setter reads the currency back off the value it
    # is about to extend, and formats the amount to two decimals.
    money.currency = _currency_code(field, currency, embedded)
    money.amount = amount
    return money


def _plain_value(field: CustomField, value: Any) -> Any:
    """Validate *value* against the field's data type and return what to store."""
    match field.data_type:
        case CustomFieldType.STRING | CustomFieldType.LONGTEXT | CustomFieldType.URL:
            if not isinstance(value, str):
                _reject(field, "a string", value)
            return value
        case CustomFieldType.DATE:
            if not isinstance(value, str):
                _reject(field, "an ISO date string (YYYY-MM-DD)", value)
            return parse_date(value, field="value")
        case CustomFieldType.BOOLEAN:
            if not isinstance(value, bool):
                _reject(field, "true or false", value)
            return value
        case CustomFieldType.INTEGER:
            if isinstance(value, bool) or not isinstance(value, int):
                _reject(field, "a whole number", value)
            return value
        case CustomFieldType.FLOAT:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                _reject(field, "a number", value)
            return float(value)
        case CustomFieldType.SELECT:
            return _option_id(field, value)
        case CustomFieldType.DOCUMENT_LINK:
            return _link_ids(field, value)
        case _:
            # A data type this pypaperless does not know: pass the value on and
            # let Paperless be the judge rather than reject something valid.
            return value


def _draft_value(field: CustomField, value: Any, currency: str | None) -> CustomFieldValue:
    """Draft the typed value to store for *field*."""
    if field.data_type is CustomFieldType.MONETARY:
        return _monetary_value(field, value, currency)
    if currency is not None:
        raise ToolInputError(
            f"currency applies to monetary custom fields only; field {field.id} "
            f"({field.name!r}) is of type {_type_name(field)}."
        )
    return field.draft_value(_plain_value(field, value))


async def _require_documents(paperless: PaperlessClient, document_ids: list[int]) -> None:
    """Fail before storing a link that points at a document which does not exist.

    Raises:
        ToolResultError: Naming every ID that Paperless does not know.
    """
    wanted = sorted(set(document_ids))
    if not wanted:
        return
    found, _ = await paginate(paperless.documents, {"id__in": wanted}, limit=len(wanted))
    missing = sorted(set(wanted) - {document.id for document in found})
    if missing:
        raise ToolResultError(
            "not_found",
            f"No document exists with ID {', '.join(str(pk) for pk in missing)}; "
            f"nothing was written.",
            missing_document_ids=missing,
        )


def register(mcp: MCPServer, settings: Settings) -> None:
    """Register the custom field value tools when writes are exposed."""
    if not settings.expose_writes:
        return

    @write_tool(mcp, destructive=True, idempotent=True)
    @safe_tool
    async def set_document_custom_field(
        ctx: ToolContext,
        document_id: int,
        custom_field_id: int,
        value: Any,
        currency: str | None = None,
    ) -> dict[str, Any]:
        """Set or replace the value of one custom field on a document.

        This is an upsert: a field the document does not carry yet is added, an
        existing one has its value replaced. Look ``custom_field_id`` up with
        ``list_custom_fields`` — its ``data_type`` decides what ``value`` has to
        be:

        - ``string``, ``longtext``, ``url``: a string.
        - ``date``: an ISO date, ``YYYY-MM-DD``.
        - ``boolean``: true or false — ``"true"`` and ``1`` are rejected.
        - ``integer``: a whole number; ``1.0`` is rejected, not rounded.
        - ``float``: a number.
        - ``monetary``: a number (``6589``) or a prefixed string
          (``"EUR6589.00"``). The currency comes from ``currency``, else from
          the field's ``default_currency``, else ``EUR``; the amount is stored
          with two decimals.
        - ``select``: an option ID or its exact label.
        - ``documentlink``: a list of document IDs.

        Two traps with ``documentlink``: the list **replaces** the stored one,
        so adding a link means reading the current list from ``get_document``
        and sending it back with the new ID appended — otherwise the existing
        links are dropped. And Paperless maintains the reverse link itself:
        linking A to B makes B show A, so never set both directions.

        Nothing is written when the field already holds this value; the result
        says ``changed: false``. A write rewrites the document's entire custom
        field array, so a value another client stored between this call's read
        and its write is lost.
        """
        paperless = await get_client(ctx)
        # Fills pypaperless' custom field cache: without it a drafted value
        # stays untyped and the document's stored values arrive unenriched.
        await get_names(ctx)
        field = _definition(paperless, custom_field_id)
        draft = _draft_value(field, value, currency)
        if field.data_type is CustomFieldType.DOCUMENT_LINK:
            # Called again on the way out: it is what narrows the drafted value
            # back to the ID list, which the model carries as ``Any``.
            await _require_documents(paperless, _link_ids(field, draft.value))

        document = await paperless.documents(document_id)
        fields = document.custom_fields
        stored = fields.default(custom_field_id) if fields is not None else None
        result = {
            "created": stored is None,
            "document_id": document_id,
            "custom_field_id": custom_field_id,
            "field_name": field.name,
            "data_type": _type_name(field),
            "previous_value": safe_dump(stored.value) if stored is not None else None,
            "value": safe_dump(draft.value),
        }
        if stored is not None and stored.value == draft.value:
            return {"changed": False, **result}

        if fields is None:
            fields = DocumentCustomFieldList(root=[])
            document.custom_fields = fields
        # An upsert, not an append: add() alone would leave a second entry for
        # the same field in the array.
        fields.remove(custom_field_id)
        fields.add(draft)
        changed = await paperless.documents.update(document)
        return {"changed": changed, **result}

    @write_tool(mcp, destructive=True, idempotent=True)
    @safe_tool
    async def remove_document_custom_field(
        ctx: ToolContext, document_id: int, custom_field_id: int
    ) -> dict[str, Any]:
        """Remove one custom field's value from a document.

        Only the assignment on this document goes away — the field definition
        and its values on every other document stay untouched, which is what
        ``delete_custom_field`` would destroy instead. Removing a
        ``documentlink`` value also drops the reverse link Paperless keeps on
        the documents it pointed at.

        A field the document does not carry is not an error: the call reports
        ``removed: false`` and changes nothing.
        """
        paperless = await get_client(ctx)
        await get_names(ctx)
        document = await paperless.documents(document_id)
        fields = document.custom_fields
        stored = fields.default(custom_field_id) if fields is not None else None
        if fields is None or stored is None:
            return {
                "changed": False,
                "removed": False,
                "document_id": document_id,
                "custom_field_id": custom_field_id,
                "previous_value": None,
            }

        previous = safe_dump(stored.value)
        fields.remove(custom_field_id)
        changed = await paperless.documents.update(document)
        return {
            "changed": changed,
            "removed": True,
            "document_id": document_id,
            "custom_field_id": custom_field_id,
            "previous_value": previous,
        }
