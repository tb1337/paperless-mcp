"""Unit tests for the shared tool helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from paperless_mcp.tools._helpers import collect, safe_tool


async def _gen(items: list[Any]) -> AsyncIterator[Any]:
    for x in items:
        yield x


@pytest.mark.asyncio
async def test_collect_returns_all_when_under_limit() -> None:
    items, has_more = await collect(_gen([1, 2, 3]), offset=0, limit=10)
    assert items == [1, 2, 3]
    assert has_more is False


@pytest.mark.asyncio
async def test_collect_caps_at_limit_and_reports_has_more() -> None:
    items, has_more = await collect(_gen([1, 2, 3, 4, 5]), offset=0, limit=2)
    assert items == [1, 2]
    assert has_more is True


@pytest.mark.asyncio
async def test_collect_respects_offset() -> None:
    items, has_more = await collect(_gen([1, 2, 3, 4, 5]), offset=2, limit=2)
    assert items == [3, 4]
    assert has_more is True


@pytest.mark.asyncio
async def test_collect_offset_past_end() -> None:
    items, has_more = await collect(_gen([1, 2, 3]), offset=10, limit=5)
    assert items == []
    assert has_more is False


@pytest.mark.asyncio
async def test_collect_limit_zero_reports_has_more() -> None:
    items, has_more = await collect(_gen([1, 2, 3]), offset=0, limit=0)
    assert items == []
    assert has_more is True


@pytest.mark.asyncio
async def test_collect_rejects_negative() -> None:
    with pytest.raises(ValueError):
        await collect(_gen([1]), offset=-1, limit=5)


@pytest.mark.asyncio
async def test_safe_tool_translates_known_exception() -> None:
    class ItemNotFoundError(Exception):
        pass

    @safe_tool
    async def tool() -> dict[str, Any]:
        raise ItemNotFoundError("doc 42 not found")

    result = await tool()
    assert result["error"] == "not_found"
    assert "does not exist" in result["detail"]
    assert "42" in result["cause"]


@pytest.mark.asyncio
async def test_safe_tool_reraises_unknown_exception() -> None:
    @safe_tool
    async def tool() -> dict[str, Any]:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await tool()


@pytest.mark.asyncio
async def test_safe_tool_passes_through_normal_result() -> None:
    @safe_tool
    async def tool() -> dict[str, Any]:
        return {"ok": True}

    assert await tool() == {"ok": True}
