"""Every ``search_documents`` argument, against the lookup it becomes.

A wrong Django lookup is the worst kind of bug here: Paperless feeds the query
dict to a FilterSet, which **drops** a key it does not recognise. A dropped filter
does not narrow the selection — it widens it to everything, silently. So the pairing
is asserted one argument at a time, on the request the stub actually received.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from paperless_mcp.tools.documents import (
    _DATE_FILTERS,
    _FILTERS,
    _build_doc_filters,
    search_documents,
)
from tests.conftest import (
    PaperlessStub,
    build_mcp,
    call_tool,
    invoke_tool,
    make_client,
    make_settings,
)


async def _params(**kwargs: Any) -> dict[str, str]:
    """Run ``search_documents`` and return the query Paperless was asked."""
    stub = PaperlessStub(collections={"/api/documents/": []})
    mcp = build_mcp(make_settings(), make_client(stub))
    result = await call_tool(mcp, "search_documents", **kwargs)
    assert "error" not in result, result
    listing = [r for r in stub.requests if r.path == "/api/documents/"]
    assert len(listing) == 1, [r.path for r in stub.requests]
    return listing[0].params


@pytest.mark.parametrize(
    ("argument", "value", "lookup", "sent"),
    [
        ("title_contains", "rechnung", "title__icontains", "rechnung"),
        ("content_contains", "gesamtbetrag", "content__icontains", "gesamtbetrag"),
        ("title_or_content", "miete", "title_content", "miete"),
        ("tags_all", [1, 2], "tags__id__all", "1,2"),
        ("tags_any", [1, 2], "tags__id__in", "1,2"),
        # __none would otherwise go out as repeated parameters, of which Django
        # reads only the last.
        ("tags_none", [1, 2], "tags__id__none", "1,2"),
        ("correspondent_id", 4, "correspondent__id", "4"),
        ("document_type_id", 5, "document_type__id", "5"),
        ("storage_path_id", 6, "storage_path__id", "6"),
        ("archive_serial_number", 17, "archive_serial_number", "17"),
        ("is_in_inbox", True, "is_in_inbox", "true"),
        ("is_tagged", False, "is_tagged", "false"),
        ("mime_type", "application/pdf", "mime_type", "application/pdf"),
        # The date arguments are normalised to a plain date, so a datetime is
        # accepted and truncated rather than refused.
        ("created_after", "2026-01-02", "created__date__gte", "2026-01-02"),
        ("created_before", "2026-01-31T23:59:00", "created__date__lte", "2026-01-31"),
        ("added_after", "2026-02-01", "added__date__gte", "2026-02-01"),
        ("added_before", "2026-02-28", "added__date__lte", "2026-02-28"),
    ],
)
async def test_each_argument_becomes_its_paperless_lookup(
    argument: str, value: Any, lookup: str, sent: str
) -> None:
    params = await _params(**{argument: value})
    assert params[lookup] == sent


@pytest.mark.parametrize(("descending", "expected"), [(False, "created"), (True, "-created")])
async def test_ordering_carries_the_direction(descending: bool, expected: str) -> None:
    params = await _params(order_by="created", descending=descending)
    assert params["ordering"] == expected


async def test_an_unknown_order_field_never_reaches_paperless() -> None:
    """Paperless ignores an ordering field it does not know, which is why it is an enum.

    The schema refuses it now, so no request goes out - the same outcome as the
    hand-written check it replaced, one layer earlier.
    """
    stub = PaperlessStub(collections={"/api/documents/": []})
    mcp = build_mcp(make_settings(), make_client(stub))

    with pytest.raises(ToolError, match="'created'"):
        await invoke_tool(mcp, "search_documents", order_by="colour")

    assert [r for r in stub.requests if r.path == "/api/documents/"] == []


async def test_arguments_that_were_not_passed_send_nothing() -> None:
    """An unasked filter must not appear at all, not even as an empty value.

    ``truncate_content`` is the one constant: the list projections never read
    the OCR text, so every window asks Paperless not to send it.
    """
    params = await _params(limit=5)
    assert set(params) == {"page", "page_size", "truncate_content"}


async def test_several_filters_combine_into_one_query() -> None:
    params = await _params(title_contains="miete", correspondent_id=4, is_in_inbox=False)
    assert params["title__icontains"] == "miete"
    assert params["correspondent__id"] == "4"
    assert params["is_in_inbox"] == "false"


@pytest.mark.parametrize(
    ("argument", "value", "field", "sent"),
    [
        ("title", "Neu", "title", "Neu"),
        ("correspondent_id", 4, "correspondent", 4),
        ("document_type_id", 5, "document_type", 5),
        ("storage_path_id", 6, "storage_path", 6),
        ("archive_serial_number", 17, "archive_serial_number", 17),
        ("tag_ids", [1, 2], "tags", [1, 2]),
        ("content", "korrigierter OCR-Text", "content", "korrigierter OCR-Text"),
        ("created", "2026-01-02", "created", "2026-01-02"),
    ],
)
async def test_update_document_patches_only_the_field_it_was_given(
    argument: str, value: Any, field: str, sent: Any
) -> None:
    """Eight legs of one setter ladder; four had never been executed."""
    stub = PaperlessStub(collections={"/api/documents/": [{"id": 4, "title": "Alt"}]})
    mcp = build_mcp(make_settings(), make_client(stub))

    result = await call_tool(mcp, "update_document", document_id=4, **{argument: value})

    assert result["changed"] is True
    patched = next(r for r in stub.requests if r.method == "PATCH")
    assert patched.json == {field: sent}


@pytest.mark.parametrize(
    "field", ["correspondent", "document_type", "storage_path", "archive_serial_number"]
)
async def test_a_cleared_field_is_sent_as_null(field: str) -> None:
    """`clear_fields` is the only way to unset one: omitting it means "leave alone"."""
    stub = PaperlessStub(
        collections={
            "/api/documents/": [
                {
                    "id": 4,
                    "title": "Alt",
                    "correspondent": 1,
                    "document_type": 2,
                    "storage_path": 3,
                    "archive_serial_number": 9,
                }
            ]
        }
    )
    mcp = build_mcp(make_settings(), make_client(stub))

    result = await call_tool(mcp, "update_document", document_id=4, clear_fields=[field])

    assert result["changed"] is True
    assert next(r for r in stub.requests if r.method == "PATCH").json == {field: None}


async def test_setting_and_clearing_the_same_field_is_refused() -> None:
    stub = PaperlessStub(collections={"/api/documents/": [{"id": 4, "title": "Alt"}]})
    mcp = build_mcp(make_settings(), make_client(stub))

    result = await call_tool(
        mcp, "update_document", document_id=4, correspondent_id=1, clear_fields=["correspondent"]
    )

    assert result["error"] == "invalid_argument"
    assert [r for r in stub.requests if r.method == "PATCH"] == []


async def test_a_full_text_hit_carries_its_relevance_data() -> None:
    """`search_hit_` only exists on a document that came back from a `query=`."""
    stub = PaperlessStub(
        collections={
            "/api/documents/": [
                {
                    "id": 4,
                    "title": "Rechnung",
                    "__search_hit__": {"score": 1.5, "rank": 0, "highlights": "Rechnung"},
                }
            ]
        }
    )
    mcp = build_mcp(make_settings(), make_client(stub))

    result = await call_tool(mcp, "get_document", document_id=4)

    assert result["search_hit"]["score"] == 1.5


def test_the_filter_table_covers_every_argument_search_documents_forwards() -> None:
    """`**supplied` is the loose end: a table entry missing means a dropped filter.

    `_build_doc_filters` raises rather than ignoring a name it has no lookup for, so
    an argument added to `search_documents` without a row here fails loudly instead
    of being silently discarded - which is the failure mode that widens a selection
    to everything.
    """
    with pytest.raises(TypeError, match="not a document filter"):
        _build_doc_filters(invented_filter="x")


def test_every_table_argument_is_a_search_documents_parameter() -> None:
    """The other direction: a row for an argument no tool passes is dead weight."""
    parameters = set(inspect.signature(search_documents).parameters)
    assert {argument for argument, _ in _FILTERS} <= parameters
    assert parameters >= _DATE_FILTERS
