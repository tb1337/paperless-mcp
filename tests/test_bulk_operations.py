"""The bulk operations, against the request each one actually sends.

Driven over the real ``DocumentBulkEditService`` rather than a recorder that
answers to any attribute name. The recorder the older bulk tests use has a
``__getattr__``, so a method renamed upstream would still be recorded and every
assertion would still pass — while the call failed against a live server. Here the
method has to exist, and its request body is what is asserted.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import PaperlessStub, build_mcp, call_tool, make_client, make_settings

_BULK_EDIT = "/api/documents/bulk_edit/"


@pytest.fixture
def stub() -> PaperlessStub:
    return PaperlessStub(
        routes={
            ("POST", _BULK_EDIT): {"result": "OK"},
            ("POST", "/api/documents/reprocess/"): {"result": "OK"},
            ("POST", "/api/documents/rotate/"): {"result": "OK"},
        }
    )


def _server(stub: PaperlessStub) -> Any:
    return build_mcp(make_settings(), make_client(stub))


@pytest.mark.parametrize(
    ("argument", "method", "parameters"),
    [
        ("correspondent_id", "set_correspondent", {"correspondent": 5}),
        ("document_type_id", "set_document_type", {"document_type": 5}),
        ("storage_path_id", "set_storage_path", {"storage_path": 5}),
    ],
)
async def test_each_assignment_becomes_its_bulk_edit_method(
    stub: PaperlessStub, argument: str, method: str, parameters: dict[str, int]
) -> None:
    result = await call_tool(
        _server(stub), "bulk_edit_documents", document_ids=[1, 2], **{argument: 5}
    )

    assert result["document_ids"] == [1, 2]
    posted = [r for r in stub.requests if r.path == _BULK_EDIT]
    assert len(posted) == 1
    assert posted[0].json == {"documents": [1, 2], "method": method, "parameters": parameters}


async def test_tag_changes_are_added_and_removed_in_one_request(stub: PaperlessStub) -> None:
    """Two lists, one call: Paperless applies them together or not at all."""
    result = await call_tool(
        _server(stub),
        "bulk_edit_documents",
        document_ids=[1],
        add_tag_ids=[10],
        remove_tag_ids=[11],
    )

    assert result["applied"] == ["tags"]
    posted = next(r for r in stub.requests if r.path == _BULK_EDIT)
    assert posted.json["method"] == "modify_tags"
    assert posted.json["parameters"] == {"add_tags": [10], "remove_tags": [11]}


async def test_several_assignments_run_in_the_documented_order(stub: PaperlessStub) -> None:
    result = await call_tool(
        _server(stub),
        "bulk_edit_documents",
        document_ids=[1],
        storage_path_id=6,
        correspondent_id=5,
        add_tag_ids=[10],
    )

    assert result["applied"] == ["correspondent", "storage_path", "tags"]
    assert [r.json["method"] for r in stub.requests if r.path == _BULK_EDIT] == [
        "set_correspondent",
        "set_storage_path",
        "modify_tags",
    ]


async def test_reprocess_queues_the_documents(stub: PaperlessStub) -> None:
    result = await call_tool(_server(stub), "bulk_reprocess_documents", document_ids=[1, 2])

    assert result == {"document_ids": [1, 2], "reprocessing": True}
    assert stub.requests[-1].path == "/api/documents/reprocess/"
    assert stub.requests[-1].json == {"documents": [1, 2]}


@pytest.mark.parametrize("degrees", [90, 180, 270])
async def test_rotate_sends_the_angle(stub: PaperlessStub, degrees: int) -> None:
    result = await call_tool(
        _server(stub), "bulk_rotate_documents", document_ids=[1], degrees=degrees
    )

    assert result == {"document_ids": [1], "degrees": degrees}
    assert stub.requests[-1].json["degrees"] == degrees


@pytest.mark.parametrize(
    ("tool", "kwargs", "reason"),
    [
        ("bulk_edit_documents", {"document_ids": [1]}, "Nothing to do"),
        ("bulk_edit_documents", {"document_ids": [], "correspondent_id": 1}, "must not be empty"),
        ("bulk_reprocess_documents", {"document_ids": []}, "must not be empty"),
        ("bulk_rotate_documents", {"document_ids": [], "degrees": 90}, "must not be empty"),
        # Anything but a right angle would re-encode the pages for nothing.
        ("bulk_rotate_documents", {"document_ids": [1], "degrees": 45}, "90, 180 or 270"),
    ],
)
async def test_an_impossible_bulk_call_sends_no_request(
    stub: PaperlessStub, tool: str, kwargs: dict[str, Any], reason: str
) -> None:
    """Validated before the first request, so nothing is half-applied."""
    result = await call_tool(_server(stub), tool, **kwargs)

    assert result["error"] == "invalid_argument"
    assert reason in result["cause"]
    assert [r for r in stub.requests if r.method == "POST"] == []


@pytest.mark.parametrize(
    ("argument", "value", "sent"),
    [
        ("status", "failure", "failure"),
        ("task_type", "consume_file", "consume_file"),
        ("acknowledged", False, "false"),
    ],
)
async def test_each_task_filter_reaches_paperless(argument: str, value: Any, sent: str) -> None:
    """Three legs of one `if` ladder, none of which had a test."""
    stub = PaperlessStub(collections={"/api/tasks/": []})
    mcp = build_mcp(make_settings(), make_client(stub))

    await call_tool(mcp, "list_tasks", **{argument: value})

    listing = next(r for r in stub.requests if r.path == "/api/tasks/")
    assert listing.params[argument] == sent
    # Newest first, always: the ordering is not one of the arguments.
    assert listing.params["ordering"] == "-date_created"
