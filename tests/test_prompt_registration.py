"""Smoke tests for prompt registration under the different visibility modes."""

from __future__ import annotations

from dataclasses import replace

import pytest

from paperless_mcp.config import Settings
from paperless_mcp.server import build_mcp
from tests.conftest import make_settings

_PROMPTS = frozenset({"triage_inbox", "monthly_review", "find_duplicates"})

_MODES = [
    pytest.param(True, True, id="readonly"),
    pytest.param(False, False, id="writes"),
    pytest.param(False, True, id="writes+deletes"),
]


def _settings(*, readonly: bool, enable_delete: bool) -> Settings:
    return replace(make_settings(), readonly=readonly, enable_delete=enable_delete)


def _full_surface() -> Settings:
    return _settings(readonly=False, enable_delete=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(("readonly", "enable_delete"), _MODES)
async def test_every_mode_advertises_every_prompt(readonly: bool, enable_delete: bool) -> None:
    """A prompt adapts its text to the visibility flags; it is never withheld.

    Even read-only, every workflow here still produces something worth having —
    a filing proposal, a close-out, a duplicate report.
    """
    mcp = build_mcp(_settings(readonly=readonly, enable_delete=enable_delete))
    assert {prompt.name for prompt in await mcp.list_prompts()} == _PROMPTS


@pytest.mark.asyncio
async def test_every_prompt_has_a_title_and_a_description() -> None:
    """Both are what a client puts in the slash-command picker."""
    prompts = await build_mcp(_full_surface()).list_prompts()
    assert [p.name for p in prompts if not (p.title or "").strip()] == []
    assert [p.name for p in prompts if not (p.description or "").strip()] == []


@pytest.mark.asyncio
async def test_no_prompt_argument_is_required() -> None:
    """Every workflow has to be runnable by pressing enter on the slash command."""
    prompts = await build_mcp(_full_surface()).list_prompts()
    required = {(p.name, a.name) for p in prompts for a in (p.arguments or []) if a.required}
    assert required == set()


@pytest.mark.asyncio
async def test_prompts_expose_the_arguments_they_document() -> None:
    prompts = {p.name: p for p in await build_mcp(_full_surface()).list_prompts()}
    arguments = {name: {a.name for a in (p.arguments or [])} for name, p in prompts.items()}
    assert arguments == {
        "triage_inbox": {"limit"},
        "monthly_review": {"month"},
        "find_duplicates": {"query", "limit"},
    }


def test_no_prompt_asks_for_a_context() -> None:
    """The SDK's ``validate_call`` wrapper makes an injected Context unusable.

    ``Prompt.from_function`` re-validates arguments against their annotations,
    and a parameterized ``Context[...]`` is a distinct class to pydantic: the
    context is rebuilt field by field, the private request attributes are lost,
    and the first ``ctx.request_context`` access raises at render time. A prompt
    that needs live data has to become a tool instead.
    """
    registered = build_mcp(_full_surface())._prompt_manager._prompts
    assert set(registered) == _PROMPTS
    for name, prompt in registered.items():
        assert prompt.context_kwarg is None, f"{name} would render with a dead Context"
