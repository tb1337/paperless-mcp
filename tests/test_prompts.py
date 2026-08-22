"""Tests for what the workflow prompts actually render."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from mcp.types import GetPromptResult, TextContent

from paperless_mcp.config import Settings
from paperless_mcp.prompts._helpers import sections
from paperless_mcp.prompts.review import month_window
from paperless_mcp.server import build_mcp
from paperless_mcp.tools._paging import MAX_PAGE_LIMIT
from tests.conftest import make_settings


async def render(settings: Settings, name: str, /, **arguments: Any) -> str:
    """Render a prompt the way a client would and return its text.

    No context is passed: ``get_prompt`` then builds one that raises on every
    access, which is what pins these prompts to being pure templates. A prompt
    that reached for Paperless would fail this call rather than quietly work in
    tests and break in a client.
    """
    result = await build_mcp(settings).get_prompt(name, arguments or None)
    # Narrowed rather than assumed: the other arms are an elicitation request and
    # the non-text content blocks, none of which a template prompt produces.
    assert isinstance(result, GetPromptResult), result
    blocks = [message.content for message in result.messages]
    assert all(isinstance(block, TextContent) for block in blocks), blocks
    return "\n\n".join(block.text for block in blocks if isinstance(block, TextContent))


@pytest.mark.parametrize("name", ["triage_inbox", "monthly_review", "find_duplicates"])
async def test_every_prompt_renders_without_a_paperless_connection(name: str) -> None:
    assert len(await render(make_settings(), name)) > 500


async def test_triage_names_the_three_evidence_sources_and_the_inbox_tag() -> None:
    """Chaining these three is the whole point of the prompt."""
    text = await render(make_settings(), "triage_inbox")
    for tool in (
        "get_document_suggestions",
        "get_document_ai_suggestions",
        "find_similar_documents",
    ):
        assert tool in text
    assert "is_inbox_tag" in text


async def test_triage_limit_reaches_both_the_intro_and_the_search_call() -> None:
    text = await render(make_settings(), "triage_inbox", limit=3)
    assert "3 documents in this pass" in text
    assert "limit=3)" in text


async def test_triage_vocabulary_step_stays_within_the_ceiling() -> None:
    """The plan must not script calls the server refuses.

    The vocabulary lists used to instruct ``limit=200``, which the ceiling
    refuses on every one of the four calls — so the step asks for the ceiling
    itself and says how to page past it.
    """
    text = await render(make_settings(), "triage_inbox")

    assert f"list_tags(limit={MAX_PAGE_LIMIT})" in text
    assert f"list_correspondents(limit={MAX_PAGE_LIMIT})" in text
    assert "limit=200" not in text
    assert "page on with `offset`" in text


async def test_triage_clamps_the_limit_to_the_ceiling() -> None:
    """A user-supplied pass size above the ceiling would script a refused call."""
    text = await render(make_settings(), "triage_inbox", limit=250)

    assert "limit=250" not in text
    assert f"limit={MAX_PAGE_LIMIT})" in text


async def test_duplicates_clamps_the_limit_to_the_ceiling() -> None:
    text = await render(make_settings(), "find_duplicates", limit=250)

    assert "limit=250" not in text
    assert f"limit={MAX_PAGE_LIMIT})" in text


@pytest.mark.parametrize("limit", [0, -5])
async def test_triage_floors_the_limit_at_one(limit: int) -> None:
    """The clamp bounds both sides.

    A negative limit scripts a call ``check_window`` refuses, and ``limit=0``
    is a count-only call whose empty document list reads as "the inbox is
    clear" — either way the plan's first step would be wrong.
    """
    text = await render(make_settings(), "triage_inbox", limit=limit)

    assert f"limit={limit}" not in text
    assert 'order_by="added", limit=1)' in text


@pytest.mark.parametrize("limit", [0, -5])
async def test_duplicates_floors_the_limit_at_one(limit: int) -> None:
    text = await render(make_settings(), "find_duplicates", limit=limit)

    assert f"limit={limit}" not in text
    assert "limit=1)" in text


async def test_review_searches_within_the_ceiling() -> None:
    """The close-out's searches carry the ceiling, not a stale literal."""
    text = await render(make_settings(), "monthly_review", month="2026-03")

    assert f"limit={MAX_PAGE_LIMIT})" in text


async def test_triage_argument_arrives_as_a_string_from_the_wire() -> None:
    """MCP sends prompt arguments as strings; the int annotation has to coerce."""
    assert "limit=3)" in await render(make_settings(), "triage_inbox", limit="3")


async def test_readonly_triage_proposes_instead_of_writing() -> None:
    text = await render(make_settings(readonly=True), "triage_inbox")
    assert "read-only" in text
    assert "bulk_edit_documents" not in text
    assert "proposal" in text


async def test_monthly_review_anchors_every_search_to_a_computed_window() -> None:
    text = await render(make_settings(), "monthly_review", month="2024-02")
    assert "2024-02 (February 2024)" in text
    assert 'created_after="2024-02-01"' in text
    # The leap day is exactly what a model gets wrong when left to work it out.
    assert 'created_before="2024-02-29"' in text
    assert "(2024-01-01 to 2024-01-31)" in text


async def test_monthly_review_rejects_a_month_it_cannot_parse() -> None:
    with pytest.raises(ValueError, match="YYYY-MM"):
        await render(make_settings(), "monthly_review", month="February")


async def test_monthly_review_offers_to_fix_only_when_writes_exist() -> None:
    assert "bulk_edit_documents" in await render(make_settings(), "monthly_review")
    assert "bulk_edit_documents" not in await render(make_settings(readonly=True), "monthly_review")


async def test_duplicates_hunts_recent_arrivals_without_a_query() -> None:
    text = await render(make_settings(), "find_duplicates", limit=5)
    assert 'order_by="added", descending=true, limit=5' in text
    assert "query=" not in text


async def test_duplicates_searches_the_query_it_was_given() -> None:
    text = await render(make_settings(), "find_duplicates", query="Stromrechnung")
    assert 'query="Stromrechnung", limit=25' in text


async def test_duplicates_only_reaches_for_the_trash_when_deletes_are_on() -> None:
    with_deletes = await render(make_settings(enable_delete=True), "find_duplicates")
    assert "delete_document(id)" in with_deletes

    without_deletes = await render(make_settings(enable_delete=False), "find_duplicates")
    assert "delete_document" not in without_deletes
    # Tagging keeps the finding alive past the conversation instead.
    assert 'create_tag(name="duplicate")' in without_deletes

    readonly = await render(make_settings(readonly=True), "find_duplicates")
    assert "delete_document" not in readonly
    assert "create_tag" not in readonly


@pytest.mark.parametrize(
    ("month", "expected"),
    [
        ("2026-03", (dt.date(2026, 3, 1), dt.date(2026, 3, 31))),
        ("2026-1", (dt.date(2026, 1, 1), dt.date(2026, 1, 31))),
        (" 2026-04 ", (dt.date(2026, 4, 1), dt.date(2026, 4, 30))),
    ],
)
def test_month_window_bounds(month: str, expected: tuple[dt.date, dt.date]) -> None:
    window = month_window(month, dt.date(2026, 8, 1))
    assert (window.start, window.end) == expected


@pytest.mark.parametrize("month", [None, "", "   "])
def test_month_window_defaults_to_the_month_before_today(month: str | None) -> None:
    """On the 1st, "this month" is empty — last month is the one to close out."""
    window = month_window(month, dt.date(2026, 1, 1))
    assert (window.start, window.end) == (dt.date(2025, 12, 1), dt.date(2025, 12, 31))


def test_month_window_steps_back_across_a_year_boundary() -> None:
    window = month_window("2026-01", dt.date(2026, 8, 1))
    assert (window.prev_start, window.prev_end) == (dt.date(2025, 12, 1), dt.date(2025, 12, 31))


def test_month_window_previous_month_keeps_its_own_length() -> None:
    window = month_window("2024-03", dt.date(2026, 8, 1))
    assert (window.prev_start, window.prev_end) == (dt.date(2024, 2, 1), dt.date(2024, 2, 29))


def test_sections_drops_the_parts_a_deployment_cannot_perform() -> None:
    assert sections("first", None, "   ", "second") == "first\n\nsecond"
