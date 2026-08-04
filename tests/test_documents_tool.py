"""Tests for the document tools (mocked PaperlessClient)."""

from __future__ import annotations

import base64
import datetime as dt
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.server.mcpserver.utilities.types import Image
from pypaperless.exceptions import (
    AsnRequestError,
    AuthError,
    ItemNotFoundError,
    PaperlessConnectionError,
    TaskNotFoundError,
)
from pypaperless.models import CustomField, Task
from pypaperless.models.documents.document import Document
from pypaperless.models.types import CustomFieldType
from pypaperless.runtime import PaperlessRuntime
from pypaperless.transport import PaperlessTransport

from paperless_mcp.tools import _task_polling
from tests.conftest import (
    FakeService,
    build_mcp,
    call_tool,
    document,
    make_runtime,
    make_settings,
    returns,
)


def _task(
    status: str,
    *,
    document_ids: tuple[int, ...] = (),
    result_data: Any = None,
) -> Task:
    """Build a real consume task, so a pypaperless field rename breaks here."""
    return Task.from_data(
        make_runtime(),
        {
            "id": 5,
            "task_id": "abc-task-uuid",
            "task_type": "consume_file",
            "status": status,
            "related_document_ids": list(document_ids),
            "result_data": result_data,
        },
    )


class _TaskStates:
    """Hand out one task state per poll; the last one repeats forever.

    An exception in the list is raised instead of returned, which is how
    Paperless answers for a task the worker has not registered yet.
    """

    def __init__(self, states: list[Any]) -> None:
        self._states = list(states)

    def __call__(self, _pk: Any) -> Any:
        state = self._states.pop(0) if len(self._states) > 1 else self._states[0]
        if isinstance(state, BaseException):
            raise state
        return state


class _Clock:
    """Virtual monotonic clock: time moves only when the poll loop sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    """Drive the poll loop without real waiting, recording the delays it asks for."""
    fake = _Clock()
    monkeypatch.setattr(_task_polling, "time", SimpleNamespace(monotonic=fake.monotonic))
    monkeypatch.setattr(_task_polling, "asyncio", SimpleNamespace(sleep=fake.sleep))
    return fake


async def test_search_documents_paginates(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.filter_results = [document(i) for i in range(1, 6)]
    mcp = build_mcp(make_settings(), paperless)

    page1 = await call_tool(mcp, "search_documents", offset=0, limit=2)
    assert [d["id"] for d in page1["documents"]] == [1, 2]
    assert page1["has_more"] is True
    assert page1["returned"] == 2
    assert page1["total"] == 5

    page3 = await call_tool(mcp, "search_documents", offset=4, limit=2)
    assert [d["id"] for d in page3["documents"]] == [5]
    assert page3["has_more"] is False


async def test_search_documents_passes_filters(make_paperless: Any) -> None:
    paperless = make_paperless()
    mcp = build_mcp(make_settings(), paperless)

    await call_tool(
        mcp,
        "search_documents",
        query="invoice",
        title_contains="bill",
        tags_any=[1, 2],
        tags_none=[9],
        correspondent_id=7,
        created_after="2026-01-01",
        is_in_inbox=True,
        order_by="created",
        descending=True,
    )
    assert paperless.documents.filter_calls == [
        {
            "title__icontains": "bill",
            "tags__id__in": "1,2",
            "tags__id__none": "9",
            "correspondent__id": 7,
            "is_in_inbox": True,
            "created__date__gte": "2026-01-01",
            "ordering": "-created",
            "query": "invoice",
        }
    ]


async def test_search_documents_sends_the_custom_field_query_as_json(
    make_paperless: Any,
) -> None:
    paperless = make_paperless()
    paperless.custom_fields.filter_results = [
        SimpleNamespace(id=3, name="Due", data_type=CustomFieldType.DATE)
    ]
    mcp = build_mcp(make_settings(), paperless)

    await call_tool(
        mcp,
        "search_documents",
        document_type_id=2,
        custom_field_query=["Due", "range", ["2024-08-01", "2024-09-01"]],
    )

    assert paperless.documents.filter_calls == [
        {
            "document_type__id": 2,
            "custom_field_query": '["Due", "range", ["2024-08-01", "2024-09-01"]]',
        }
    ]


async def test_search_documents_checks_the_query_against_the_definitions(
    make_paperless: Any,
) -> None:
    """The snapshot search_documents already awaits is what makes the check free."""
    paperless = make_paperless()
    paperless.custom_fields.filter_results = [
        SimpleNamespace(id=3, name="Due", data_type=CustomFieldType.DATE)
    ]
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "search_documents", custom_field_query=["Duo", "exists", True])

    assert result["error"] == "invalid_argument"
    assert "'Due'" in result["cause"]
    assert paperless.documents.filter_calls == []


async def test_search_documents_rejects_unknown_ordering(make_paperless: Any) -> None:
    mcp = build_mcp(make_settings(), make_paperless())
    result = await call_tool(mcp, "search_documents", order_by="content")
    assert result["error"] == "invalid_argument"


async def test_search_documents_rejects_bad_dates(make_paperless: Any) -> None:
    mcp = build_mcp(make_settings(), make_paperless())
    result = await call_tool(mcp, "search_documents", created_after="yesterday")
    assert result["error"] == "invalid_argument"
    assert "created_after" in result["cause"]


async def test_get_document_returns_detail(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.get_result = document(42, "Bill")
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "get_document", document_id=42)
    assert result["id"] == 42
    assert result["title"] == "Bill"
    assert result["notes"] == []
    # Only a preview of the OCR text; the full text is get_document_content's job.
    assert "content" not in result
    assert result["content_preview"] == "ocr text"
    assert result["content_characters"] == len("ocr text")


async def test_get_document_content_reports_length(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.get_result = document(3, "Bill")
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "get_document_content", document_id=3)
    assert result == {
        "document_id": 3,
        "title": "Bill",
        "characters": len("ocr text"),
        "content": "ocr text",
    }


async def test_get_document_translates_not_found(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.get_raises = ItemNotFoundError("no such id")
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "get_document", document_id=99)
    assert result["error"] == "not_found"
    assert "does not exist" in result["detail"]


async def test_update_document_clear_fields(make_paperless: Any) -> None:
    doc = document(5, "old")
    doc.correspondent = 7
    doc.document_type = 3

    paperless = make_paperless()
    paperless.documents.get_result = doc
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(
        mcp,
        "update_document",
        document_id=5,
        title="new title",
        created="2026-03-04",
        clear_fields=["correspondent"],
    )
    assert paperless.documents.update_calls == [doc]
    assert doc.title == "new title"
    assert doc.created == dt.date(2026, 3, 4)
    assert doc.correspondent is None  # cleared
    assert doc.document_type == 3  # untouched
    assert result["id"] == 5
    assert result["changed"] is True


async def test_update_document_rejects_unknown_clear_field(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.get_result = document(5)
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "update_document", document_id=5, clear_fields=["title"])
    assert result["error"] == "invalid_argument"
    assert "title" in result["cause"]
    assert paperless.documents.update_calls == []  # nothing happened


async def test_update_document_rejects_conflicting_set_and_clear(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.get_result = document(5)
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(
        mcp,
        "update_document",
        document_id=5,
        correspondent_id=9,
        clear_fields=["correspondent"],
    )
    assert result["error"] == "invalid_argument"
    assert paperless.documents.update_calls == []


async def test_upload_document_passes_content_and_metadata(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.save_returns = "abc-task-uuid"
    mcp = build_mcp(make_settings(), paperless)

    payload = base64.b64encode(b"%PDF-1.4 fake").decode("ascii")
    result = await call_tool(
        mcp,
        "upload_document",
        filename="invoice.pdf",
        content_base64=payload,
        title="Invoice",
        tag_ids=[1, 2],
        created="2026-02-03",
    )
    assert result == {
        "task_uuid": "abc-task-uuid",
        "filename": "invoice.pdf",
        "size_bytes": len(b"%PDF-1.4 fake"),
    }
    assert len(paperless.documents.save_calls) == 1
    draft = paperless.documents.save_calls[0]
    assert draft.document == b"%PDF-1.4 fake"
    assert draft.filename == "invoice.pdf"
    assert draft.title == "Invoice"
    assert draft.tags == [1, 2]
    assert draft.created == dt.datetime(2026, 2, 3, 0, 0)
    # Without poll=True the call ends at the queue, and costs no task request.
    assert paperless.tasks.get_calls == []


async def test_upload_document_poll_returns_the_new_document_id(
    make_paperless: Any, clock: _Clock
) -> None:
    paperless = make_paperless()
    paperless.documents.save_returns = "abc-task-uuid"
    paperless.tasks.get_result = _TaskStates(
        [_task("pending"), _task("started"), _task("success", document_ids=(42,))]
    )
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(
        mcp,
        "upload_document",
        filename="invoice.pdf",
        content_base64=base64.b64encode(b"%PDF-1.4").decode("ascii"),
        poll=True,
    )

    assert result["task_uuid"] == "abc-task-uuid"
    assert result["document_id"] == 42
    assert result["status"] == "success"
    assert result["timed_out"] is False
    assert result["task"]["task_type"] == "consume_file"
    # Polled by UUID, backing off between attempts, and stopped at the first
    # terminal state instead of waiting the timeout out.
    assert [pk for pk, _ in paperless.tasks.get_calls] == ["abc-task-uuid"] * 3
    assert clock.slept == [1.0, 1.5]


async def test_upload_document_poll_reports_a_timeout(make_paperless: Any, clock: _Clock) -> None:
    """A wait that runs out is not an error: the UUID is still pollable."""
    paperless = make_paperless()
    paperless.documents.save_returns = "abc-task-uuid"
    paperless.tasks.get_result = _TaskStates([_task("started")])
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(
        mcp,
        "upload_document",
        filename="scan.pdf",
        content_base64=base64.b64encode(b"%PDF-1.4").decode("ascii"),
        poll=True,
        poll_timeout_seconds=10,
    )

    assert result["timed_out"] is True
    assert result["status"] == "started"
    assert result["document_id"] is None
    assert result["task_uuid"] == "abc-task-uuid"
    assert clock.now == 10


async def test_upload_document_poll_waits_out_an_unregistered_task(
    make_paperless: Any, clock: _Clock
) -> None:
    """/api/tasks/ only knows the task once a worker picked it up."""
    paperless = make_paperless()
    paperless.documents.save_returns = "abc-task-uuid"
    paperless.tasks.get_result = _TaskStates(
        [TaskNotFoundError("abc-task-uuid"), _task("success", document_ids=(7,))]
    )
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(
        mcp,
        "upload_document",
        filename="scan.pdf",
        content_base64=base64.b64encode(b"%PDF-1.4").decode("ascii"),
        poll=True,
    )

    assert result["document_id"] == 7
    assert result["timed_out"] is False


async def test_upload_document_poll_rides_out_a_broken_connection(
    make_paperless: Any, clock: _Clock
) -> None:
    """A poll spanning a Paperless restart must not abandon a queued file."""
    paperless = make_paperless()
    paperless.documents.save_returns = "abc-task-uuid"
    paperless.tasks.get_result = _TaskStates(
        [PaperlessConnectionError("connection refused"), _task("success", document_ids=(8,))]
    )
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(
        mcp,
        "upload_document",
        filename="scan.pdf",
        content_base64=base64.b64encode(b"%PDF-1.4").decode("ascii"),
        poll=True,
    )

    assert result["document_id"] == 8
    assert result["timed_out"] is False


async def test_upload_document_poll_keeps_the_uuid_when_polling_fails(
    make_paperless: Any, clock: _Clock
) -> None:
    """The file is queued either way; the UUID is the only way back to it."""
    paperless = make_paperless()
    paperless.documents.save_returns = "abc-task-uuid"
    paperless.tasks.get_raises = AuthError("token revoked")
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(
        mcp,
        "upload_document",
        filename="scan.pdf",
        content_base64=base64.b64encode(b"%PDF-1.4").decode("ascii"),
        poll=True,
    )

    assert result["error"] == "auth_failed"
    assert result["task_uuid"] == "abc-task-uuid"
    assert result["size_bytes"] == len(b"%PDF-1.4")


async def test_upload_document_poll_surfaces_a_rejected_file(
    make_paperless: Any, clock: _Clock
) -> None:
    """A duplicate is the common failure, and only the task says so."""
    paperless = make_paperless()
    paperless.documents.save_returns = "abc-task-uuid"
    paperless.tasks.get_result = _TaskStates(
        [_task("failure", result_data={"message": "It is a duplicate of Invoice (#3)"})]
    )
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(
        mcp,
        "upload_document",
        filename="invoice.pdf",
        content_base64=base64.b64encode(b"%PDF-1.4").decode("ascii"),
        poll=True,
    )

    assert result["status"] == "failure"
    assert result["document_id"] is None
    assert result["timed_out"] is False
    assert result["task"]["result_data"] == {"message": "It is a duplicate of Invoice (#3)"}
    assert clock.slept == []


async def test_upload_document_rejects_an_out_of_range_poll_timeout(
    make_paperless: Any,
) -> None:
    paperless = make_paperless()
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(
        mcp,
        "upload_document",
        filename="invoice.pdf",
        content_base64=base64.b64encode(b"%PDF-1.4").decode("ascii"),
        poll=True,
        poll_timeout_seconds=301,
    )

    assert result["error"] == "invalid_argument"
    # Rejected before the upload, so there is no orphaned task to clean up.
    assert paperless.documents.save_calls == []


async def test_upload_document_rejects_bad_base64(make_paperless: Any) -> None:
    paperless = make_paperless()
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(
        mcp,
        "upload_document",
        filename="x.pdf",
        content_base64="!!not base64!!",
    )
    assert result["error"] == "invalid_argument"
    assert paperless.documents.save_calls == []


async def test_upload_document_rejects_empty_payload(make_paperless: Any) -> None:
    paperless = make_paperless()
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "upload_document", filename="x.pdf", content_base64="")
    assert result["error"] == "invalid_argument"
    assert paperless.documents.save_calls == []


async def test_add_document_note_creates_a_scoped_draft(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.notes = FakeService(save_returns=11)
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "add_document_note", document_id=4, note="checked")
    assert result == {"document_id": 4, "note_id": 11}
    assert paperless.documents.notes.create_calls == [{"args": (4,), "note": "checked"}]


async def test_delete_document_note_passes_the_document_pk(make_paperless: Any) -> None:
    """pypaperless v6 renamed the keyword from ``document_pk`` to ``pk``."""
    paperless = make_paperless()
    paperless.documents.notes = FakeService()
    mcp = build_mcp(make_settings(enable_delete=True), paperless)

    result = await call_tool(mcp, "delete_document_note", document_id=4, note_id=11)
    assert result == {"document_id": 4, "note_id": 11, "deleted": True}
    assert paperless.documents.notes.delete_calls == [{"obj": 11, "args": (), "pk": 4}]


async def test_delete_document_hidden_without_enable_delete(make_paperless: Any) -> None:
    mcp = build_mcp(make_settings(enable_delete=False), make_paperless())
    assert "delete_document" not in mcp._tool_manager._tools


async def test_delete_document_fetches_lazily(make_paperless: Any) -> None:
    doc = document(7)
    paperless = make_paperless()
    paperless.documents.get_result = doc
    mcp = build_mcp(make_settings(enable_delete=True), paperless)

    result = await call_tool(mcp, "delete_document", document_id=7)
    assert result == {"document_id": 7, "deleted": True}
    assert paperless.documents.get_calls == [(7, {"lazy": True})]
    assert paperless.documents.delete_calls == [{"obj": doc, "args": ()}]


async def test_download_document_returns_base64(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.download = returns(
        SimpleNamespace(
            content=b"%PDF-1.4",
            content_type="application/pdf",
            disposition_filename="invoice.pdf",
        )
    )
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "download_document", document_id=1)
    assert result["filename"] == "invoice.pdf"
    assert base64.b64decode(result["content_base64"]) == b"%PDF-1.4"


async def test_download_document_rejects_oversized(make_paperless: Any) -> None:
    big = b"x" * (2 * 1024 * 1024)  # 2 MiB > the 1 MiB test cap
    paperless = make_paperless()
    paperless.documents.download = returns(
        SimpleNamespace(content=big, content_type="application/pdf", disposition_filename="x.pdf")
    )
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "download_document", document_id=1)
    assert result["error"] == "file_too_large"
    assert result["size_bytes"] == len(big)
    assert result["max_bytes"] == 1024 * 1024


async def test_get_document_thumbnail_returns_image_content(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.thumbnail = returns(
        SimpleNamespace(content=b"webpdata", content_type="image/webp")
    )
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "get_document_thumbnail", document_id=1)
    assert isinstance(result, Image)
    assert result.data == b"webpdata"
    assert result.to_image_content().mime_type == "image/webp"


async def test_get_document_thumbnail_reports_non_image_types(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.thumbnail = returns(
        SimpleNamespace(content=b"nope", content_type="text/html")
    )
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "get_document_thumbnail", document_id=1)
    assert result["error"] == "unsupported_media_type"
    assert result["document_id"] == 1


async def test_get_document_thumbnail_rejects_oversized(make_paperless: Any) -> None:
    # The tool is declared as returning Image, so the error travels out as a
    # ToolResultError; the model must still see the same dict a JSON tool sends.
    big = b"x" * (2 * 1024 * 1024)  # 2 MiB > the 1 MiB test cap
    paperless = make_paperless()
    paperless.documents.thumbnail = returns(SimpleNamespace(content=big, content_type="image/webp"))
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "get_document_thumbnail", document_id=1)
    assert result["error"] == "file_too_large"
    assert result["size_bytes"] == len(big)
    assert result["max_bytes"] == 1024 * 1024


async def test_search_documents_resolves_ids_to_names(make_paperless: Any) -> None:
    """The names come from the shared snapshot, not from a per-document lookup."""
    paperless = make_paperless()
    doc = document(1)
    doc.correspondent = 10
    doc.tags = [40, 99]
    paperless.documents.filter_results = [doc]
    paperless.correspondents.filter_results = [SimpleNamespace(id=10, name="Utilities")]
    paperless.tags.filter_results = [SimpleNamespace(id=40, name="paid")]
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "search_documents")

    found = result["documents"][0]
    assert found["correspondent"] == 10
    assert found["correspondent_name"] == "Utilities"
    assert found["tag_names"] == ["paid", None]


async def test_get_document_warms_the_cache_before_fetching(make_paperless: Any) -> None:
    """Order matters: pypaperless enriches custom fields while parsing the document."""
    paperless = make_paperless()
    paperless.documents.get_result = document(1)
    paperless.custom_fields.filter_results = [SimpleNamespace(id=7, name="Status")]
    mcp = build_mcp(make_settings(), paperless)

    await call_tool(mcp, "get_document", document_id=1)

    assert paperless.runtime.cache.custom_fields == {7: paperless.custom_fields.filter_results[0]}


async def test_get_document_carries_the_custom_field_names(make_paperless: Any) -> None:
    """A bare ``{"field": 1, "value": "EUR6372.00"}`` tells a model nothing.

    The document is built at call time, exactly as pypaperless parses the API
    response: whether the values come back enriched depends on the cache
    already standing when that happens.
    """
    paperless = make_paperless()
    runtime = PaperlessRuntime(PaperlessTransport("http://test", "t"), paperless.runtime.cache)
    definitions = [
        CustomField.from_data(
            runtime,
            {
                "id": 1,
                "name": "Gross",
                "data_type": "monetary",
                "extra_data": {"default_currency": "EUR"},
            },
        ),
        CustomField.from_data(
            runtime,
            {
                "id": 2,
                "name": "Phase",
                "data_type": "select",
                "extra_data": {"select_options": [{"id": "opt-1", "label": "Open"}]},
            },
        ),
    ]
    paperless.custom_fields.filter_results = definitions
    paperless.documents.get_result = lambda pk: Document.from_data(
        runtime,
        {
            "id": pk,
            "title": "Bill",
            "custom_fields": [
                {"field": 1, "value": "EUR6372.00"},
                {"field": 2, "value": "opt-1"},
            ],
        },
    )
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "get_document", document_id=1)

    assert result["custom_fields"] == [
        {
            "field": 1,
            "name": "Gross",
            "data_type": "monetary",
            "label": None,
            "value": "EUR6372.00",
        },
        {"field": 2, "name": "Phase", "data_type": "select", "label": "Open", "value": "opt-1"},
    ]


async def test_search_documents_reports_a_negative_offset_as_a_result(make_paperless: Any) -> None:
    """A bad window must be answerable, not a protocol failure the model cannot see."""
    paperless = make_paperless()
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "search_documents", offset=-1)

    assert result["error"] == "invalid_argument"
    assert "non-negative" in result["cause"]


async def test_get_next_asn_reports_the_number(make_paperless: Any) -> None:
    paperless = make_paperless()

    async def _next_asn() -> int:
        return 43

    paperless.documents.get_next_asn = _next_asn
    mcp = build_mcp(make_settings(), paperless)

    assert await call_tool(mcp, "get_next_asn") == {"next_asn": 43}


async def test_get_next_asn_reports_a_refusal_as_a_structured_error(make_paperless: Any) -> None:
    paperless = make_paperless()

    async def _next_asn() -> int:
        raise AsnRequestError("no asn for you")

    paperless.documents.get_next_asn = _next_asn
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "get_next_asn")
    assert result["error"] == "asn_failed"
