"""Tests for the global search tool."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tests.conftest import FakeService, build_mcp, call_tool, make_settings

_CATEGORY_KEYS = {
    "documents",
    "tags",
    "correspondents",
    "document_types",
    "storage_paths",
    "custom_fields",
    "saved_views",
}


def _document(pk: int, **extra: Any) -> SimpleNamespace:
    return SimpleNamespace(id=pk, title=f"Doc {pk}", **{"tags": [], **extra})


def _result(**categories: Any) -> SimpleNamespace:
    return SimpleNamespace(total=categories.pop("total", 0), **categories)


def _with_search(paperless: Any, result: Any) -> Any:
    paperless.search = FakeService(get_result=result)
    return paperless


@pytest.mark.asyncio
async def test_search_everywhere_always_reports_every_category(make_paperless: Any) -> None:
    paperless = _with_search(make_paperless(), _result(total=0))
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "search_everywhere", query="telekom")

    assert result.keys() >= _CATEGORY_KEYS
    assert all(result[key] == [] for key in _CATEGORY_KEYS)
    assert result["query"] == "telekom"
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_search_everywhere_formats_hits_and_resolves_names(make_paperless: Any) -> None:
    paperless = make_paperless()
    paperless.tags.filter_results = [SimpleNamespace(id=7, name="Invoice")]
    _with_search(
        paperless,
        _result(
            total=1,
            documents=[_document(1, tags=[7])],
            tags=[SimpleNamespace(id=7, name="Invoice")],
        ),
    )
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "search_everywhere", query="invoice")

    assert result["total"] == 1
    assert result["documents"][0]["id"] == 1
    assert result["documents"][0]["tag_names"] == ["Invoice"]
    assert result["tags"][0]["name"] == "Invoice"


@pytest.mark.asyncio
async def test_search_everywhere_leaves_out_the_admin_categories(make_paperless: Any) -> None:
    paperless = _with_search(
        make_paperless(),
        _result(
            users=[SimpleNamespace(id=1, username="root")],
            groups=[SimpleNamespace(id=1, name="admins")],
            workflows=[SimpleNamespace(id=1, name="flow")],
            mail_rules=[SimpleNamespace(id=1, name="rule")],
            mail_accounts=[SimpleNamespace(id=1, name="account")],
        ),
    )
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "search_everywhere", query="root")

    for key in ("users", "groups", "workflows", "mail_rules", "mail_accounts"):
        assert key not in result


@pytest.mark.asyncio
async def test_search_everywhere_caps_each_category_and_flags_it(make_paperless: Any) -> None:
    paperless = _with_search(
        make_paperless(),
        _result(
            documents=[_document(pk) for pk in range(5)],
            tags=[SimpleNamespace(id=1, name="one")],
        ),
    )
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "search_everywhere", query="doc", limit=2)

    assert len(result["documents"]) == 2
    # The cap is per category, so a short one is untouched.
    assert len(result["tags"]) == 1
    assert result["limit"] == 2
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_search_everywhere_omits_db_only_unless_asked(make_paperless: Any) -> None:
    paperless = _with_search(make_paperless(), _result())
    mcp = build_mcp(make_settings(), paperless)

    await call_tool(mcp, "search_everywhere", query="a")
    assert paperless.search.get_calls[-1] == ("a", {"db_only": None})

    await call_tool(mcp, "search_everywhere", query="a", db_only=True)
    assert paperless.search.get_calls[-1] == ("a", {"db_only": True})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"query": "   "}, "empty"),
        ({"query": "a", "limit": 0}, "at least 1"),
    ],
)
async def test_search_everywhere_rejects_bad_input(
    make_paperless: Any, kwargs: dict[str, Any], reason: str
) -> None:
    paperless = _with_search(make_paperless(), _result())
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "search_everywhere", **kwargs)

    assert result["error"] == "invalid_argument"
    assert reason in result["cause"]
    assert paperless.search.get_calls == []


@pytest.mark.asyncio
async def test_search_autocomplete_returns_the_index_terms(make_paperless: Any) -> None:
    paperless = make_paperless()
    calls: list[tuple[Any, ...]] = []

    async def _autocomplete(term: str, limit: int) -> list[str]:
        calls.append((term, limit))
        return ["invoice", "invoices", "invoiced"]

    paperless.search = SimpleNamespace(autocomplete=_autocomplete)
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "search_autocomplete", term="inv", limit=3)

    assert result == {
        "term": "inv",
        "suggestions": ["invoice", "invoices", "invoiced"],
        "limit": 3,
    }
    assert calls == [("inv", 3)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [({"term": "  "}, "empty"), ({"term": "inv", "limit": 0}, "at least 1")],
)
async def test_search_autocomplete_rejects_bad_input(
    make_paperless: Any, kwargs: dict[str, Any], reason: str
) -> None:
    paperless = make_paperless()
    called = False

    async def _autocomplete(term: str, limit: int) -> list[str]:
        nonlocal called
        called = True
        return []

    paperless.search = SimpleNamespace(autocomplete=_autocomplete)
    mcp = build_mcp(make_settings(), paperless)

    result = await call_tool(mcp, "search_autocomplete", **kwargs)

    assert result["error"] == "invalid_argument"
    assert reason in result["cause"]
    assert called is False
