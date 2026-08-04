"""Pin the harness to pypaperless' real call surface.

``PaperlessStub`` fakes one thing, the HTTP transport, so the model and service
logic in the suite is the library's own and cannot drift from itself. What is left
is the set of methods the tools call and the keywords they pass: an upstream
rename would leave every test green and every call broken in production.

pypaperless is pinned exactly (``==6.0.0``); this file is the list to re-read when
that pin moves.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from pypaperless import PaperlessClient
from pypaperless.models.correspondents import CorrespondentDraft
from pypaperless.models.custom_fields import CustomFieldDraft
from pypaperless.models.document_types import DocumentTypeDraft
from pypaperless.models.share_links.share_link import ShareLinkDraft
from pypaperless.models.storage_paths import StoragePathDraft
from pypaperless.models.tags import TagDraft

from tests.conftest import make_client

type Shape = list[tuple[str, str, bool]]


def _shape(fn: Any) -> Shape:
    """``(name, kind, has-default)`` per parameter, minus ``self``.

    Annotations are excluded on purpose: mypy checks those, while a call site
    depends on the names and on the positional/keyword split.
    """
    return [
        (param.name, param.kind.name, param.default is not inspect.Parameter.empty)
        for param in inspect.signature(fn).parameters.values()
        if param.name != "self"
    ]


#: Every service member a tool body reaches for, by service attribute.
_CALLED_BY_TOOLS: dict[str, frozenset[str]] = {
    "documents": frozenset(
        {
            "filter",
            "pages",
            "create",
            "save",
            "update",
            "delete",
            "metadata",
            "notes",
            "history",
            "download",
            "thumbnail",
            "suggestions",
            "ai_suggestions",
            "bulk_edit",
            "get_next_asn",
            "share_links",
        }
    ),
    "tags": frozenset({"filter", "pages", "create", "save", "update", "delete"}),
    "correspondents": frozenset({"filter", "pages", "create", "save", "update", "delete"}),
    "document_types": frozenset({"filter", "pages", "create", "save", "update", "delete"}),
    "storage_paths": frozenset({"filter", "pages", "create", "save", "update", "delete"}),
    "custom_fields": frozenset({"filter", "pages", "create", "save", "update", "delete"}),
    "saved_views": frozenset({"filter", "pages"}),
    "share_links": frozenset({"create", "save", "delete"}),
    "tasks": frozenset({"filter", "pages", "active", "acknowledge"}),
    "trash": frozenset({"filter", "pages", "restore", "empty"}),
    "users": frozenset({"pages"}),
}


@pytest.mark.parametrize("service_name", sorted(_CALLED_BY_TOOLS))
def test_every_member_the_tools_call_exists(service_name: str) -> None:
    service = getattr(make_client(), service_name)
    missing = sorted(m for m in _CALLED_BY_TOOLS[service_name] if not hasattr(service, m))
    assert missing == []


def test_trash_pages_but_declares_no_document_filters() -> None:
    """``/api/trash/`` ignores the document filters, per TrashService's own docstring.

    So it does carry ``filter``/``pages`` - ``paginate`` needs them - and
    ``list_trash`` must never grow a filter argument on the strength of that: the
    endpoint would drop it silently, which widens the selection instead of
    narrowing it.
    """
    trash = make_client().trash
    assert hasattr(trash, "filter")
    assert hasattr(trash, "pages")
    assert "declares no query filters" in (type(trash).__doc__ or "")


#: The calls whose *keywords* the tools depend on, not just their existence.
_PINNED: tuple[tuple[str, str, Shape], ...] = (
    (
        "tags",
        "__call__",
        [("pk", "POSITIONAL_OR_KEYWORD", False), ("lazy", "KEYWORD_ONLY", True)],
    ),
    (
        "tags",
        "pages",
        [("page", "POSITIONAL_OR_KEYWORD", True), ("page_size", "POSITIONAL_OR_KEYWORD", True)],
    ),
    (
        "tags",
        "update",
        [("model", "POSITIONAL_OR_KEYWORD", False), ("only_changed", "KEYWORD_ONLY", True)],
    ),
    (
        "tags",
        "delete",
        [("model", "POSITIONAL_OR_KEYWORD", False), ("silent_fail", "KEYWORD_ONLY", True)],
    ),
    # TaskService is the one that names its argument differently.
    ("tasks", "__call__", [("task_id", "POSITIONAL_OR_KEYWORD", False)]),
    ("tasks", "acknowledge", [("tasks", "POSITIONAL_OR_KEYWORD", False)]),
    ("trash", "restore", [("documents", "POSITIONAL_OR_KEYWORD", False)]),
    # Defaulted, because empty() with no argument purges the whole trash.
    ("trash", "empty", [("documents", "POSITIONAL_OR_KEYWORD", True)]),
)


@pytest.mark.parametrize(("service_name", "method", "expected"), _PINNED)
def test_call_signatures_are_what_the_tools_pass(
    service_name: str, method: str, expected: Shape
) -> None:
    assert _shape(getattr(getattr(make_client(), service_name), method)) == expected


#: What each ``create_*`` tool has to fill, or ``save()`` answers with a 400.
_REQUIRED_DRAFT_FIELDS: tuple[tuple[type[Any], set[str]], ...] = (
    (TagDraft, {"name", "color", "is_inbox_tag", "match", "matching_algorithm", "is_insensitive"}),
    (CorrespondentDraft, {"name", "match", "matching_algorithm", "is_insensitive"}),
    (DocumentTypeDraft, {"name", "match", "matching_algorithm", "is_insensitive"}),
    (StoragePathDraft, {"name", "path", "match", "matching_algorithm", "is_insensitive"}),
    (CustomFieldDraft, {"name", "data_type"}),
    (ShareLinkDraft, {"document", "file_version"}),
)


@pytest.mark.parametrize(("draft_cls", "expected"), _REQUIRED_DRAFT_FIELDS)
def test_draft_required_fields_are_what_the_create_tools_fill(
    draft_cls: type[Any], expected: set[str]
) -> None:
    """``validate_draft()`` runs inside ``save()``, so a new one is a failed create."""
    assert draft_cls._create_required_fields == expected


def test_constructing_a_client_performs_no_io() -> None:
    """What lets every test above build a client without a transport of its own."""
    client = PaperlessClient("http://test", "token")
    assert client.is_initialized is False
    assert client.host_version is None
