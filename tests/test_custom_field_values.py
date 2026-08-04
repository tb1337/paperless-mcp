"""Tests for writing custom field values onto a document.

These drive the real pypaperless models rather than stubs: the whole point of
the tools is that a value survives ``draft_value`` and the document dump in the
shape Paperless stores, which a namespace stand-in could not show.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pypaperless.exceptions import ItemNotFoundError
from pypaperless.models import CustomField
from pypaperless.models.documents.document import Document
from pypaperless.runtime import PaperlessRuntime
from pypaperless.services.documents.document import DocumentService
from pypaperless.transport import PaperlessTransport

from tests.conftest import build_mcp, call_tool, make_settings

_FIELDS: list[dict[str, Any]] = [
    {"id": 1, "name": "Note", "data_type": "string"},
    {"id": 2, "name": "Memo", "data_type": "longtext"},
    {"id": 3, "name": "Homepage", "data_type": "url"},
    {"id": 4, "name": "Due", "data_type": "date"},
    {"id": 5, "name": "Paid", "data_type": "boolean"},
    {"id": 6, "name": "Count", "data_type": "integer"},
    {"id": 7, "name": "Rate", "data_type": "float"},
    {
        "id": 8,
        "name": "Gross",
        "data_type": "monetary",
        "extra_data": {"default_currency": "EUR"},
    },
    {"id": 9, "name": "Net", "data_type": "monetary"},
    {
        "id": 10,
        "name": "Status",
        "data_type": "select",
        "extra_data": {
            "select_options": [{"id": "opt-1", "label": "Open"}, {"id": "opt-2", "label": "Closed"}]
        },
    },
    {"id": 11, "name": "Related", "data_type": "documentlink"},
]


def _setup(
    make_paperless: Any,
    stored: list[dict[str, Any]] | None = None,
    *,
    readonly: bool = False,
    with_custom_fields: bool = True,
    extra_fields: tuple[dict[str, Any], ...] = (),
) -> tuple[Any, Any, Document]:
    """Build an MCP server over a fake client holding one document."""
    paperless = make_paperless()
    runtime = PaperlessRuntime(PaperlessTransport("http://test", "t"), paperless.runtime.cache)
    definitions = [CustomField.from_data(runtime, payload) for payload in [*_FIELDS, *extra_fields]]
    paperless.custom_fields.filter_results = definitions
    # Filled before the document is parsed, exactly as load_names does it in
    # production - that is what enriches the values it carries.
    paperless.runtime.cache.custom_fields = {field.id: field for field in definitions}

    payload: dict[str, Any] = {"id": 1, "title": "Doc"}
    if with_custom_fields:
        payload["custom_fields"] = stored or []
    document = Document.from_data(runtime, payload)
    paperless.documents.get_result = document
    mcp = build_mcp(make_settings(readonly=readonly), paperless)
    return mcp, paperless, document


def _stored(document: Document, custom_field_id: int) -> Any:
    """Return the value as it would go out to Paperless in the document PATCH."""
    dumped = document.api_dump()["custom_fields"]
    return next(item["value"] for item in dumped if item["field"] == custom_field_id)


@pytest.mark.parametrize(
    ("custom_field_id", "value", "expected"),
    [
        (1, "hello", "hello"),
        (2, "a longer text", "a longer text"),
        (3, "https://example.com/doc", "https://example.com/doc"),
        (4, "2026-01-31", "2026-01-31"),
        (5, True, True),
        (5, False, False),
        (6, 42, 42),
        (7, 2, 2.0),
        (7, 2.5, 2.5),
        (8, 6589, "EUR6589.00"),
        (8, 6589.5, "EUR6589.50"),
        (8, "EUR6589.00", "EUR6589.00"),
        (8, -12.5, "EUR-12.50"),
        # No default_currency on the definition: the fallback fills in.
        (9, 5, "EUR5.00"),
        (10, "opt-2", "opt-2"),
        # A select can be addressed by its label; Paperless stores the option ID.
        (10, "Closed", "opt-2"),
    ],
)
async def test_set_stores_each_data_type(
    make_paperless: Any, custom_field_id: int, value: Any, expected: Any
) -> None:
    mcp, paperless, document = _setup(make_paperless)

    result = await call_tool(
        mcp,
        "set_document_custom_field",
        document_id=1,
        custom_field_id=custom_field_id,
        value=value,
    )

    assert result["changed"] is True
    assert result["created"] is True
    assert result["previous_value"] is None
    assert result["value"] == expected
    assert _stored(document, custom_field_id) == expected
    assert paperless.documents.update_calls == [document]


async def test_set_reports_the_field_definition(make_paperless: Any) -> None:
    mcp, _paperless, _document = _setup(make_paperless)

    result = await call_tool(
        mcp, "set_document_custom_field", document_id=1, custom_field_id=8, value=1
    )

    assert result["document_id"] == 1
    assert result["custom_field_id"] == 8
    assert result["field_name"] == "Gross"
    assert result["data_type"] == "monetary"


async def test_set_replaces_an_existing_value_instead_of_appending(make_paperless: Any) -> None:
    """pypaperless' ``add()`` appends: a second entry for the field would be silent."""
    mcp, paperless, document = _setup(make_paperless, [{"field": 1, "value": "old"}])

    result = await call_tool(
        mcp, "set_document_custom_field", document_id=1, custom_field_id=1, value="new"
    )

    assert result["created"] is False
    assert result["previous_value"] == "old"
    assert result["value"] == "new"
    assert document.api_dump()["custom_fields"] == [{"field": 1, "value": "new"}]
    assert paperless.documents.update_calls == [document]


async def test_set_leaves_the_other_fields_alone(make_paperless: Any) -> None:
    mcp, _paperless, document = _setup(
        make_paperless, [{"field": 1, "value": "keep"}, {"field": 6, "value": 1}]
    )

    await call_tool(mcp, "set_document_custom_field", document_id=1, custom_field_id=6, value=2)

    assert _stored(document, 1) == "keep"
    assert _stored(document, 6) == 2


async def test_set_is_a_no_op_when_the_value_already_matches(make_paperless: Any) -> None:
    mcp, paperless, _document = _setup(make_paperless, [{"field": 8, "value": "EUR10.00"}])

    result = await call_tool(
        mcp, "set_document_custom_field", document_id=1, custom_field_id=8, value=10
    )

    assert result["changed"] is False
    assert result["created"] is False
    assert result["previous_value"] == "EUR10.00"
    assert result["value"] == "EUR10.00"
    assert paperless.documents.update_calls == []


async def test_set_starts_the_array_on_a_document_without_custom_fields(
    make_paperless: Any,
) -> None:
    mcp, paperless, document = _setup(make_paperless, with_custom_fields=False)

    result = await call_tool(
        mcp, "set_document_custom_field", document_id=1, custom_field_id=1, value="first"
    )

    assert result["changed"] is True
    assert document.api_dump()["custom_fields"] == [{"field": 1, "value": "first"}]
    assert paperless.documents.update_calls == [document]


async def test_set_reads_the_definition_from_the_shared_snapshot(make_paperless: Any) -> None:
    """One master-data pass per snapshot, not one custom-field fetch per call."""
    mcp, paperless, _document = _setup(make_paperless)

    await call_tool(mcp, "set_document_custom_field", document_id=1, custom_field_id=1, value="x")

    assert paperless.custom_fields.get_calls == []


@pytest.mark.parametrize(
    ("custom_field_id", "value"),
    [
        (1, 5),  # string field, number given
        (4, "yesterday"),  # date field, not ISO
        (4, 20260131),  # date field, not a string
        (5, "true"),  # boolean field, string given
        (5, 1),  # boolean field, int given
        (6, 1.0),  # integer field, float given
        (6, True),  # integer field, bool given
        (7, "3"),  # float field, string given
        (8, "6.589,00"),  # monetary field, not a parsable amount
        (8, True),  # monetary field, bool given
        (11, 5),  # documentlink field, single ID instead of a list
        (11, [1, "2"]),  # documentlink field, non-numeric member
    ],
)
async def test_set_rejects_a_value_that_does_not_fit_the_data_type(
    make_paperless: Any, custom_field_id: int, value: Any
) -> None:
    mcp, paperless, _document = _setup(make_paperless)

    result = await call_tool(
        mcp,
        "set_document_custom_field",
        document_id=1,
        custom_field_id=custom_field_id,
        value=value,
    )

    assert result["error"] == "invalid_argument"
    assert paperless.documents.update_calls == []


async def test_set_rejects_an_unknown_select_label_and_names_the_valid_ones(
    make_paperless: Any,
) -> None:
    mcp, paperless, _document = _setup(make_paperless)

    result = await call_tool(
        mcp, "set_document_custom_field", document_id=1, custom_field_id=10, value="Pending"
    )

    assert result["error"] == "invalid_argument"
    assert "Open" in result["cause"]
    assert "Closed" in result["cause"]
    assert paperless.documents.update_calls == []


async def test_set_skips_select_options_without_an_id(make_paperless: Any) -> None:
    """Both halves of a ``select_options`` entry are optional in the API schema."""
    incomplete = {
        "id": 12,
        "name": "Phase",
        "data_type": "select",
        "extra_data": {
            "select_options": [None, {"label": "Draft"}, {"id": "opt-9", "label": "Done"}]
        },
    }
    mcp, _paperless, document = _setup(make_paperless, extra_fields=(incomplete,))

    result = await call_tool(
        mcp, "set_document_custom_field", document_id=1, custom_field_id=12, value="Done"
    )

    assert result["value"] == "opt-9"
    assert _stored(document, 12) == "opt-9"


async def test_set_passes_a_data_type_this_library_does_not_know_through(
    make_paperless: Any,
) -> None:
    """A type a newer Paperless added is Paperless' call to accept or reject."""
    exotic = {"id": 12, "name": "Where", "data_type": "geo"}
    mcp, _paperless, document = _setup(make_paperless, extra_fields=(exotic,))

    result = await call_tool(
        mcp, "set_document_custom_field", document_id=1, custom_field_id=12, value={"lat": 1}
    )

    assert result["data_type"] == "unknown"
    assert result["value"] == {"lat": 1}
    assert _stored(document, 12) == {"lat": 1}


async def test_set_reports_an_unknown_custom_field_as_such(make_paperless: Any) -> None:
    mcp, paperless, _document = _setup(make_paperless)

    result = await call_tool(
        mcp, "set_document_custom_field", document_id=1, custom_field_id=404, value="x"
    )

    assert result["error"] == "not_found"
    assert "404" in result["detail"]
    assert "list_custom_fields" in result["detail"]
    assert paperless.documents.update_calls == []


async def test_set_reports_an_unknown_document(make_paperless: Any) -> None:
    mcp, paperless, _document = _setup(make_paperless)
    paperless.documents.get_raises = ItemNotFoundError("no such document")

    result = await call_tool(
        mcp, "set_document_custom_field", document_id=99, custom_field_id=1, value="x"
    )

    assert result["error"] == "not_found"
    assert paperless.documents.update_calls == []


async def test_set_takes_an_explicit_currency(make_paperless: Any) -> None:
    mcp, _paperless, document = _setup(make_paperless)

    result = await call_tool(
        mcp,
        "set_document_custom_field",
        document_id=1,
        custom_field_id=8,
        value=100,
        currency="usd",
    )

    assert result["value"] == "USD100.00"
    assert _stored(document, 8) == "USD100.00"


async def test_set_rejects_a_currency_that_contradicts_the_value(make_paperless: Any) -> None:
    mcp, paperless, _document = _setup(make_paperless)

    result = await call_tool(
        mcp,
        "set_document_custom_field",
        document_id=1,
        custom_field_id=8,
        value="USD5.00",
        currency="EUR",
    )

    assert result["error"] == "invalid_argument"
    assert paperless.documents.update_calls == []


async def test_set_rejects_a_currency_that_is_not_a_code(make_paperless: Any) -> None:
    mcp, _paperless, _document = _setup(make_paperless)

    result = await call_tool(
        mcp,
        "set_document_custom_field",
        document_id=1,
        custom_field_id=8,
        value=5,
        currency="Euro",
    )

    assert result["error"] == "invalid_argument"


async def test_set_rejects_a_currency_on_a_field_that_is_not_monetary(make_paperless: Any) -> None:
    mcp, paperless, _document = _setup(make_paperless)

    result = await call_tool(
        mcp,
        "set_document_custom_field",
        document_id=1,
        custom_field_id=1,
        value="x",
        currency="EUR",
    )

    assert result["error"] == "invalid_argument"
    assert paperless.documents.update_calls == []


async def test_set_replaces_the_whole_documentlink_list(make_paperless: Any) -> None:
    mcp, paperless, document = _setup(make_paperless, [{"field": 11, "value": [2070]}])
    # What ``id__in=2109`` matches server-side.
    paperless.documents.filter_results = [SimpleNamespace(id=2109)]

    result = await call_tool(
        mcp, "set_document_custom_field", document_id=1, custom_field_id=11, value=[2109]
    )

    assert result["previous_value"] == [2070]
    assert result["value"] == [2109]
    assert _stored(document, 11) == [2109]
    assert paperless.documents.filter_calls == [{"id__in": "2109"}]


async def test_set_refuses_a_link_to_a_document_that_does_not_exist(make_paperless: Any) -> None:
    mcp, paperless, document = _setup(make_paperless, [{"field": 11, "value": [2070]}])
    paperless.documents.filter_results = [SimpleNamespace(id=2070)]

    result = await call_tool(
        mcp, "set_document_custom_field", document_id=1, custom_field_id=11, value=[2070, 4711]
    )

    assert result["error"] == "not_found"
    assert result["missing_document_ids"] == [4711]
    assert "4711" in result["detail"]
    assert _stored(document, 11) == [2070]  # untouched
    assert paperless.documents.update_calls == []


async def test_set_accepts_an_empty_documentlink_list(make_paperless: Any) -> None:
    """Clearing the links needs no existence check - and must not do one."""
    mcp, paperless, document = _setup(make_paperless, [{"field": 11, "value": [2070]}])

    result = await call_tool(
        mcp, "set_document_custom_field", document_id=1, custom_field_id=11, value=[]
    )

    assert result["value"] == []
    assert _stored(document, 11) == []
    assert paperless.documents.filter_calls == []


class _RecordingTransport(PaperlessTransport):
    """Capture the request pypaperless would send instead of performing it.

    A real subclass, so the runtime it is handed to is the one the library builds
    and a renamed transport method shows up as a type error rather than as a
    method that is simply never called.
    """

    def __init__(self) -> None:
        super().__init__("http://test", "t")
        self.patches: list[dict[str, Any]] = []

    async def patch(
        self,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        self.patches.append({"path": path, "json": json})
        return {"id": 1, "title": "Doc", **(json or {})}


async def test_the_write_goes_out_as_a_patch_of_the_whole_array(make_paperless: Any) -> None:
    """Paperless has no per-field endpoint, which is why the tools read first.

    Driven through the real update service: what the tool leaves on the
    document has to survive pypaperless' change detection and dump as the
    ``custom_fields`` array the API replaces wholesale.
    """
    mcp, paperless, document = _setup(
        make_paperless, [{"field": 1, "value": "old"}, {"field": 6, "value": 3}]
    )
    await call_tool(mcp, "set_document_custom_field", document_id=1, custom_field_id=1, value="new")

    transport = _RecordingTransport()
    service = DocumentService(PaperlessRuntime(transport, paperless.runtime.cache))
    await service.update(document)

    assert transport.patches == [
        {
            "path": "/api/documents/1/",
            # Untouched fields ride along; the replaced one moves to the end.
            "json": {"custom_fields": [{"field": 6, "value": 3}, {"field": 1, "value": "new"}]},
        }
    ]


async def test_remove_drops_the_value_from_the_document(make_paperless: Any) -> None:
    mcp, paperless, document = _setup(
        make_paperless, [{"field": 1, "value": "gone"}, {"field": 6, "value": 3}]
    )

    result = await call_tool(mcp, "remove_document_custom_field", document_id=1, custom_field_id=1)

    assert result == {
        "changed": True,
        "removed": True,
        "document_id": 1,
        "custom_field_id": 1,
        "previous_value": "gone",
    }
    assert document.api_dump()["custom_fields"] == [{"field": 6, "value": 3}]
    assert paperless.documents.update_calls == [document]


async def test_remove_is_a_no_op_when_the_field_is_not_set(make_paperless: Any) -> None:
    """The end state is what the caller wanted; an error would read as a bug."""
    mcp, paperless, _document = _setup(make_paperless, [{"field": 1, "value": "other"}])

    result = await call_tool(mcp, "remove_document_custom_field", document_id=1, custom_field_id=6)

    assert result == {
        "changed": False,
        "removed": False,
        "document_id": 1,
        "custom_field_id": 6,
        "previous_value": None,
    }
    assert paperless.documents.update_calls == []


async def test_remove_is_a_no_op_on_a_document_without_custom_fields(make_paperless: Any) -> None:
    mcp, paperless, _document = _setup(make_paperless, with_custom_fields=False)

    result = await call_tool(mcp, "remove_document_custom_field", document_id=1, custom_field_id=1)

    assert result["removed"] is False
    assert paperless.documents.update_calls == []


async def test_remove_reports_an_unknown_document(make_paperless: Any) -> None:
    mcp, paperless, _document = _setup(make_paperless)
    paperless.documents.get_raises = ItemNotFoundError("no such document")

    result = await call_tool(mcp, "remove_document_custom_field", document_id=99, custom_field_id=1)

    assert result["error"] == "not_found"
    assert paperless.documents.update_calls == []


async def test_readonly_hides_both_tools(make_paperless: Any) -> None:
    mcp, _paperless, _document = _setup(make_paperless, readonly=True)

    registered = mcp._tool_manager._tools
    assert "set_document_custom_field" not in registered
    assert "remove_document_custom_field" not in registered
