"""Tool registration: the display title derived from each tool's name."""

from __future__ import annotations

import pytest

from paperless_mcp.tools._registry import humanize


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("search_documents", "Search documents"),
        ("get_document_thumbnail", "Get document thumbnail"),
        # Acronyms and proper nouns survive; a bare capitalize() would not.
        ("get_document_ai_suggestions", "Get document AI suggestions"),
        ("get_paperless_info", "Get Paperless info"),
        ("empty_trash", "Empty trash"),
    ],
)
def test_humanize_derives_a_display_title(name: str, expected: str) -> None:
    assert humanize(name) == expected
