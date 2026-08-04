"""Tests for resolving relations passed by name instead of by ID."""

from __future__ import annotations

import base64
from typing import Any

from tests.conftest import (
    BulkRecorder,
    build_mcp,
    call_tool,
    document,
    make_settings,
    named,
    tool_session,
)


async def test_update_document_resolves_a_document_type_name(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.get_result = document(1)
    paperless.document_types.filter_results = named(**{"10": "Bescheid", "11": "Kündigung"})
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "update_document", document_id=1, document_type_name="Kündigung")

    assert result["document_type"] == 11
    assert result["document_type_name"] == "Kündigung"
    assert paperless.documents.update_calls[0].document_type == 11


async def test_a_name_matches_case_insensitively(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.get_result = document(1)
    paperless.document_types.filter_results = named(**{"6": "Rechnung"})
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "update_document", document_id=1, document_type_name="  rechnung")

    assert result["document_type"] == 6


async def test_an_exact_hit_wins_over_a_case_variant(make_paperless: Any) -> None:
    """Two entries differing only in case stay individually reachable."""
    paperless = make_paperless()
    paperless.documents.get_result = document(1)
    paperless.tags.filter_results = named(**{"1": "Bank", "2": "bank"})
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "update_document", document_id=1, tag_names=["bank"])

    assert result["tags"] == [2]


async def test_an_ambiguous_name_is_rejected(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.get_result = document(1)
    paperless.tags.filter_results = named(**{"1": "Bank", "2": "bank"})
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "update_document", document_id=1, tag_names=["BANK"])

    assert result["error"] == "invalid_argument"
    assert "Bank (ID 1)" in result["cause"]
    assert "bank (ID 2)" in result["cause"]
    assert paperless.documents.update_calls == []


async def test_an_unknown_name_is_rejected_with_near_misses(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.get_result = document(1)
    paperless.document_types.filter_results = named(**{"6": "Rechnung", "12": "Kontoauszug"})
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "update_document", document_id=1, document_type_name="Rechnun")

    assert result["error"] == "invalid_argument"
    assert "Rechnung (ID 6)" in result["cause"]
    assert "list_document_types" in result["cause"]
    assert paperless.documents.update_calls == []


async def test_an_unknown_name_never_creates_the_object(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.get_result = document(1)
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "update_document", document_id=1, tag_names=["brandneu"])

    assert result["error"] == "invalid_argument"
    assert paperless.tags.create_calls == []
    assert paperless.tags.save_calls == []


async def test_a_miss_reloads_the_snapshot_once_before_giving_up(make_paperless: Any) -> None:
    """Master data created elsewhere since the snapshot must still resolve."""
    paperless = make_paperless()
    paperless.documents.get_result = document(1)
    paperless.document_types.filter_results = named(**{"6": "Rechnung"})
    mcp = build_mcp(make_settings(), paperless)

    async with tool_session(mcp) as call:
        await call("update_document", document_id=1, title="warms the snapshot")
        assert len(paperless.document_types.page_calls) == 1

        paperless.document_types.filter_results = named(
            **{"6": "Rechnung", "17": "Spendenquittung"}
        )
        result = await call("update_document", document_id=1, document_type_name="Spendenquittung")

    assert result["document_type"] == 17
    assert len(paperless.document_types.page_calls) == 2


async def test_an_id_and_a_name_pointing_elsewhere_are_rejected(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.get_result = document(1)
    paperless.document_types.filter_results = named(**{"10": "Bescheid", "11": "Kündigung"})
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(
        mcp,
        "update_document",
        document_id=1,
        document_type_id=10,
        document_type_name="Kündigung",
    )

    assert result["error"] == "invalid_argument"
    assert paperless.documents.update_calls == []


async def test_an_id_and_a_name_that_agree_are_accepted(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.get_result = document(1)
    paperless.document_types.filter_results = named(**{"11": "Kündigung"})
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(
        mcp,
        "update_document",
        document_id=1,
        document_type_id=11,
        document_type_name="Kündigung",
    )

    assert result["document_type"] == 11


async def test_tag_names_replace_the_tag_list(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.get_result = document(1)
    paperless.tags.filter_results = named(**{"21": "Bank", "3": "Steuer"})
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "update_document", document_id=1, tag_names=["Steuer", "Bank"])

    assert result["tags"] == [3, 21]
    assert result["tag_names"] == ["Steuer", "Bank"]


async def test_an_empty_tag_name_list_clears_the_tags(make_paperless: Any) -> None:
    paperless = make_paperless()
    doc = document(1)
    doc.tags = [3, 21]
    paperless.documents.get_result = doc
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "update_document", document_id=1, tag_names=[])

    assert result["tags"] == []


async def test_tag_ids_and_tag_names_describing_different_sets_are_rejected(
    make_paperless: Any,
) -> None:
    paperless = make_paperless()
    paperless.documents.get_result = document(1)
    paperless.tags.filter_results = named(**{"21": "Bank", "3": "Steuer"})
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(
        mcp, "update_document", document_id=1, tag_ids=[21], tag_names=["Steuer"]
    )

    assert result["error"] == "invalid_argument"
    assert paperless.documents.update_calls == []


async def test_tag_ids_and_tag_names_agree_in_any_order(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.get_result = document(1)
    paperless.tags.filter_results = named(**{"21": "Bank", "3": "Steuer"})
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(
        mcp, "update_document", document_id=1, tag_ids=[21, 3], tag_names=["Steuer", "Bank"]
    )

    assert result["tags"] == [3, 21]


async def test_setting_by_name_still_conflicts_with_clear_fields(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.get_result = document(1)
    paperless.document_types.filter_results = named(**{"11": "Kündigung"})
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(
        mcp,
        "update_document",
        document_id=1,
        document_type_name="Kündigung",
        clear_fields=["document_type"],
    )

    assert result["error"] == "invalid_argument"
    assert paperless.documents.update_calls == []


async def test_search_documents_filters_by_name(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.correspondents.filter_results = named(**{"5": "Finanzamt"})
    paperless.document_types.filter_results = named(**{"6": "Rechnung"})
    paperless.tags.filter_results = named(**{"21": "Bank", "3": "Steuer"})
    mcp = build_mcp(make_settings(), paperless)

    await call_tool(
        mcp,
        "search_documents",
        correspondent_name="Finanzamt",
        document_type_name="Rechnung",
        tags_all_names=["Bank"],
        tags_none_names=["Steuer"],
    )

    sent = paperless.documents.filter_calls[0]
    assert sent["correspondent__id"] == 5
    assert sent["document_type__id"] == 6
    assert sent["tags__id__all"] == "21"
    assert sent["tags__id__none"] == "3"


async def test_bulk_edit_documents_resolves_names(make_paperless: Any) -> None:
    paperless = make_paperless()
    recorder = BulkRecorder()
    paperless.documents.bulk_edit = recorder
    paperless.correspondents.filter_results = named(**{"5": "Finanzamt"})
    paperless.tags.filter_results = named(**{"21": "Bank", "3": "Steuer"})
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(
        mcp,
        "bulk_edit_documents",
        document_ids=[1, 2],
        correspondent_name="Finanzamt",
        add_tag_names=["Bank"],
        remove_tag_names=["Steuer"],
    )

    assert result["applied"] == ["correspondent", "tags"]
    assert recorder.calls[0][1] == ([1, 2], 5)
    assert recorder.calls[1][2] == {"add_tags": [21], "remove_tags": [3]}


async def test_bulk_edit_rejects_an_unknown_name_before_the_first_request(
    make_paperless: Any,
) -> None:
    """Resolution runs up front, so a typo cannot leave a half-applied edit."""
    paperless = make_paperless()
    recorder = BulkRecorder()
    paperless.documents.bulk_edit = recorder
    paperless.correspondents.filter_results = named(**{"5": "Finanzamt"})
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(
        mcp,
        "bulk_edit_documents",
        document_ids=[1, 2],
        correspondent_name="Finanzamt",
        add_tag_names=["gibtsnicht"],
    )

    assert result["error"] == "invalid_argument"
    assert recorder.calls == []


async def test_upload_document_resolves_names(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.documents.save_returns = "task-uuid"
    paperless.document_types.filter_results = named(**{"6": "Rechnung"})
    paperless.tags.filter_results = named(**{"21": "Bank"})
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(
        mcp,
        "upload_document",
        filename="scan.pdf",
        content_base64=base64.b64encode(b"%PDF-1.4").decode(),
        document_type_name="Rechnung",
        tag_names=["Bank"],
    )

    assert result["task_uuid"] == "task-uuid"
    draft = paperless.documents.create_calls[0]
    assert draft["document_type"] == 6
    assert draft["tags"] == [21]
