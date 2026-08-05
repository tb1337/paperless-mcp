"""The tool bodies no fake could reach.

Eight tools were registered and pinned by `test_tool_registration.py` but never
*called*, because the service-level fake carried none of the document
sub-services, `tasks.acknowledge` or `trash`'s actions. On the transport harness
they need no stubbing at all - the sub-services are real, and the stub answers
their endpoints.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import PaperlessStub, build_mcp, call_tool, make_client, make_settings

_DOC_ID = 4


def _server(stub: PaperlessStub, **settings: bool) -> Any:
    return build_mcp(make_settings(**settings), make_client(stub))


@pytest.fixture
def stub() -> PaperlessStub:
    """A document, a tag it can be suggested, and the sub-service endpoints."""
    return PaperlessStub(
        collections={
            "/api/documents/": [{"id": _DOC_ID, "title": "Rechnung", "tags": [9]}],
            "/api/tags/": [{"id": 9, "name": "paid", "matching_algorithm": 0}],
            "/api/correspondents/": [{"id": 1, "name": "Stadtwerke", "matching_algorithm": 0}],
            "/api/saved_views/": [{"id": 7, "name": "Unpaid", "filter_rules": []}],
            "/api/share_links/": [{"id": 3, "document": _DOC_ID, "file_version": "archive"}],
            "/api/tasks/": [],
        }
    )


async def test_get_document_suggestions_resolves_the_id_lists(stub: PaperlessStub) -> None:
    """Each ID list is answered with a `*_names` list in the same order."""
    stub.routes[("GET", f"/api/documents/{_DOC_ID}/suggestions/")] = {
        "correspondents": [1],
        "tags": [9, 404],
        "dates": ["2026-01-01"],
    }

    result = await call_tool(_server(stub), "get_document_suggestions", document_id=_DOC_ID)

    suggestions = result["suggestions"]
    assert suggestions["correspondent_names"] == ["Stadtwerke"]
    # A hole stays a hole: an unknown ID must not shift its neighbours.
    assert suggestions["tag_names"] == ["paid", None]
    assert suggestions["dates"] == ["2026-01-01"]


async def test_get_document_ai_suggestions_keeps_the_proposed_names(stub: PaperlessStub) -> None:
    """Unlike the classifier, the LLM can propose objects that do not exist yet."""
    stub.routes[("GET", f"/api/documents/{_DOC_ID}/ai_suggestions/")] = {
        "title": "Stromrechnung 2026",
        "tags": [9],
        "suggested_tags": ["Strom"],
    }

    result = await call_tool(_server(stub), "get_document_ai_suggestions", document_id=_DOC_ID)

    ai = result["ai_suggestions"]
    assert ai["title"] == "Stromrechnung 2026"
    assert ai["tag_names"] == ["paid"]
    assert ai["suggested_tags"] == ["Strom"]


async def test_get_document_metadata_returns_the_payload_as_a_dict(stub: PaperlessStub) -> None:
    stub.routes[("GET", f"/api/documents/{_DOC_ID}/metadata/")] = {
        "original_filename": "rechnung.pdf",
        "original_size": 1234,
    }

    result = await call_tool(_server(stub), "get_document_metadata", document_id=_DOC_ID)

    assert result["original_filename"] == "rechnung.pdf"
    assert result["original_size"] == 1234


async def test_get_document_metadata_names_the_document_it_describes(
    stub: PaperlessStub,
) -> None:
    """The endpoint carries no identity, so the model's `id` is always null.

    Reported as-is it reads like a document without an ID; the three neighbouring
    sub-service tools all answer with `document_id` instead.
    """
    stub.routes[("GET", f"/api/documents/{_DOC_ID}/metadata/")] = {
        "original_filename": "rechnung.pdf"
    }

    result = await call_tool(_server(stub), "get_document_metadata", document_id=_DOC_ID)

    assert result["document_id"] == _DOC_ID
    assert "id" not in result


async def test_get_document_notes_resolves_the_author(stub: PaperlessStub) -> None:
    stub.collections["/api/users/"] = [{"id": 6, "username": "clerk"}]
    stub.routes[("GET", f"/api/documents/{_DOC_ID}/notes/")] = [
        {"id": 1, "note": "geprüft", "user": 6, "document": _DOC_ID}
    ]

    result = await call_tool(_server(stub), "get_document_notes", document_id=_DOC_ID)

    assert result["document_id"] == _DOC_ID
    assert [note["note"] for note in result["notes"]] == ["geprüft"]
    assert result["notes"][0]["user_name"] == "clerk"


async def test_get_document_notes_pages(stub: PaperlessStub) -> None:
    """A 300-note document used to come back whole: no offset, limit or total."""
    stub.routes[("GET", f"/api/documents/{_DOC_ID}/notes/")] = [
        {"id": pk, "note": f"n{pk}", "document": _DOC_ID} for pk in (1, 2, 3, 4, 5)
    ]

    result = await call_tool(
        _server(stub), "get_document_notes", document_id=_DOC_ID, offset=1, limit=2
    )

    assert [note["id"] for note in result["notes"]] == [2, 3]
    assert result["total"] == 5
    assert result["has_more"] is True
    # The keys the older shape carried are still there, so this is additive.
    assert result["document_id"] == _DOC_ID


async def test_get_document_history_pages(stub: PaperlessStub) -> None:
    stub.routes[("GET", f"/api/documents/{_DOC_ID}/history/")] = [
        {"id": pk, "action": "update", "changes": {}, "actor": None} for pk in (1, 2, 3)
    ]

    result = await call_tool(
        _server(stub), "get_document_history", document_id=_DOC_ID, offset=1, limit=1
    )

    assert [entry["id"] for entry in result["history"]] == [2]
    assert result["total"] == 3
    assert result["has_more"] is True


async def test_find_similar_documents_asks_paperless_to_compare(stub: PaperlessStub) -> None:
    """`more_like_id` is server-side: Paperless ranks by its own index."""
    stub.collections["/api/documents/"].append({"id": 5, "title": "Ähnlich", "tags": []})

    result = await call_tool(_server(stub), "find_similar_documents", document_id=_DOC_ID, limit=5)

    assert result["reference"] == _DOC_ID
    assert stub.requests[-1].params["more_like_id"] == str(_DOC_ID)


async def test_acknowledge_tasks_reports_the_server_count(stub: PaperlessStub) -> None:
    stub.routes[("POST", "/api/tasks/acknowledge/")] = {"result": 2}

    result = await call_tool(_server(stub), "acknowledge_tasks", task_ids=[1, 2])

    assert result == {"task_ids": [1, 2], "acknowledged": 2}
    assert stub.requests[-1].json == {"tasks": [1, 2]}


async def test_acknowledge_tasks_refuses_an_empty_list(stub: PaperlessStub) -> None:
    """An empty list would acknowledge nothing while reading as success."""
    result = await call_tool(_server(stub), "acknowledge_tasks", task_ids=[])

    assert result["error"] == "invalid_argument"
    assert [r for r in stub.requests if r.method == "POST"] == []


async def test_list_saved_views_pages(stub: PaperlessStub) -> None:
    result = await call_tool(_server(stub), "list_saved_views")

    assert [view["id"] for view in result["saved_views"]] == [7]
    assert result["total"] == 1


async def test_delete_share_link_revokes_public_access(stub: PaperlessStub) -> None:
    mcp = _server(stub, enable_delete=True)

    result = await call_tool(mcp, "delete_share_link", share_link_id=3)

    assert result == {"share_link_id": 3, "deleted": True}
    assert stub.collections["/api/share_links/"] == []
    # Lazily: a delete needs the primary key, not the whole object.
    assert [r.method for r in stub.requests if r.path == "/api/share_links/3/"] == ["DELETE"]


@pytest.mark.parametrize(
    ("status", "expected"),
    [(403, "forbidden"), (404, "not_found")],
)
async def test_a_failing_sub_service_is_reported_not_raised(
    stub: PaperlessStub, status: int, expected: str
) -> None:
    """`safe_tool` covers the sub-services too, not just the top-level ones."""
    stub.status[f"/api/documents/{_DOC_ID}/metadata/"] = status

    result = await call_tool(_server(stub), "get_document_metadata", document_id=_DOC_ID)

    assert result["error"] == expected


async def test_merge_refuses_metadata_from_a_document_it_is_not_merging(
    stub: PaperlessStub,
) -> None:
    """The winner has to be one of the merged documents, or Paperless drops it."""
    stub.routes[("POST", "/api/documents/merge/")] = {"result": "OK"}

    result = await call_tool(
        _server(stub), "bulk_merge_documents", document_ids=[1, 2], metadata_from_id=99
    )

    assert result["error"] == "invalid_argument"
    assert [r for r in stub.requests if r.method == "POST"] == []


async def test_search_everywhere_reports_custom_fields_too(stub: PaperlessStub) -> None:
    """The one category whose formatter takes no name snapshot."""
    stub.collections["/api/custom_fields/"] = [{"id": 8, "name": "Fällig", "data_type": "date"}]
    stub.routes[("GET", "/api/search/")] = {
        "total": 1,
        "custom_fields": [{"id": 8, "name": "Fällig", "data_type": "date"}],
    }

    result = await call_tool(_server(stub), "search_everywhere", query="Fällig")

    assert [field["name"] for field in result["custom_fields"]] == ["Fällig"]
