"""Shared building blocks for the workflow prompts."""

from __future__ import annotations

from ..config import Settings

_READONLY = """\
This server is configured read-only — no write or delete tool is registered. Everything below
produces a report for a human to act on, not a change to make. Do not go looking for a write
tool; there is none."""

_NO_DELETES = """\
This server exposes read and write tools. Delete tools are not registered
(`PAPERLESS_MCP_ENABLE_DELETE` is off), so nothing here can move a document to the trash."""

_FULL = "This server exposes read, write and delete tools."


def sections(*parts: str | None) -> str:
    """Join the non-empty *parts* into one message body, a blank line apart.

    Passing ``None`` for a part is how a prompt drops the step this deployment
    cannot perform, rather than walking the model through tools that were never
    registered.
    """
    return "\n\n".join(stripped for part in parts if (stripped := (part or "").strip()))


def capability_note(settings: Settings) -> str:
    """State which verbs this deployment exposes, in the prompt's own words.

    A prompt is rendered once and then read as fact, so the visibility flags
    have to be spelled out in it: a plan that ends in a write the server never
    advertised is a plan the model will follow until it fails.
    """
    if settings.readonly:
        return _READONLY
    return _FULL if settings.expose_deletes else _NO_DELETES
