"""Tool registration: the annotations and the display title every tool carries.

Registering through these rather than a bare ``@mcp.tool()`` is what keeps the
behaviour hints consistent across all 64 tools instead of being retyped per call
site.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

type ToolFunc = Callable[..., Awaitable[Any]]

#: A decorator that registers its argument as a tool and hands it back. The
#: precise signature is not preserved because a registered tool is never called
#: directly from Python — the MCP server owns every invocation.
type ToolDecorator = Callable[[ToolFunc], ToolFunc]

#: Proper nouns and acronyms a derived title must not lower-case.
_TITLE_WORDS: dict[str, str] = {
    "ai": "AI",
    "asn": "ASN",
    "ocr": "OCR",
    "paperless": "Paperless",
}

#: Value for ``openWorldHint``. Every tool here talks to the one configured
#: Paperless instance, so the domain of interaction is closed in the sense the
#: hint means: a call cannot reach an open-ended set of external entities the
#: way a web search or an email send can.
_OPEN_WORLD = False


def humanize(name: str) -> str:
    """Derive a display title from a tool's function name.

    ``Tool.title`` is what a client puts in front of the user, so deriving it
    keeps it from drifting away from the name the model sees. Only the first
    word is capitalized — usually the verb, but ``bulk_*`` leads with its scope.
    """
    verb, *rest = name.split("_")
    return " ".join([verb.capitalize(), *(_TITLE_WORDS.get(word, word) for word in rest)])


def _register(mcp: MCPServer, annotations: ToolAnnotations) -> ToolDecorator:
    """Return a decorator registering a tool with *annotations* and a derived title."""

    def decorate(func: ToolFunc) -> ToolFunc:
        return mcp.tool(title=humanize(func.__name__), annotations=annotations)(func)

    return decorate


def read_tool(mcp: MCPServer) -> ToolDecorator:
    """Register a tool that only reads from Paperless.

    ``destructiveHint`` and ``idempotentHint`` stay unset on purpose: the spec
    gives them meaning only when ``readOnlyHint`` is false, so sending them
    here would be noise a client has to ignore.
    """
    return _register(mcp, ToolAnnotations(read_only_hint=True, open_world_hint=_OPEN_WORLD))


def write_tool(mcp: MCPServer, *, destructive: bool, idempotent: bool) -> ToolDecorator:
    """Register a tool that creates or modifies data.

    Args:
        mcp: The server to register on.
        destructive: Whether a call can overwrite or discard data that was
            already there. Purely additive tools (upload, create, note,
            restore) are not destructive; replacing a field value is.
        idempotent: Whether repeating the identical call converges on the same
            state. False for anything that adds another row, queues another
            task or accumulates (rotation!), so that a client cannot treat a
            retry as free.
    """
    return _register(
        mcp,
        ToolAnnotations(
            read_only_hint=False,
            destructive_hint=destructive,
            idempotent_hint=idempotent,
            open_world_hint=_OPEN_WORLD,
        ),
    )


def delete_tool(mcp: MCPServer) -> ToolDecorator:
    """Register a tool that removes data.

    Deletes are destructive by definition and idempotent in effect: a second
    call finds the object already gone and leaves the archive as it was.
    """
    return _register(
        mcp,
        ToolAnnotations(
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=True,
            open_world_hint=_OPEN_WORLD,
        ),
    )
