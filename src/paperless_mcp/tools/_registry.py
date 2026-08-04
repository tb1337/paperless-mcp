"""Tool registration: the annotations, the display title and the visibility gate.

Declaring a tool through :func:`read_tool` / :func:`write_tool` / :func:`delete_tool`
rather than a bare ``@mcp.tool()`` is what keeps the behaviour hints consistent
across all 64 tools instead of being retyped per call site.

They are factories rather than decorators, so a tool function lives at module level
and the declarations gather into one table per module. That table is where an
inconsistency between the twenty CRUD tools is visible; scattered across 700 lines
of nested definitions it is not. It also means :func:`safe_tool` is applied in one
place instead of 64, so a tool cannot reach a client unwrapped.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from ._errors import safe_tool

if TYPE_CHECKING:
    from ..config import Settings

type ToolFunc = Callable[..., Awaitable[Any]]

#: Which visibility flag decides whether a tool is registered at all.
type Gate = Literal["read", "write", "delete"]

#: Proper nouns and acronyms a derived title must not lower-case.
_TITLE_WORDS: Final[Mapping[str, str]] = {
    "ai": "AI",
    "asn": "ASN",
    "ocr": "OCR",
    "paperless": "Paperless",
}

#: Value for ``openWorldHint``. Every tool here talks to the one configured
#: Paperless instance, so the domain of interaction is closed in the sense the
#: hint means: a call cannot reach an open-ended set of external entities the
#: way a web search or an email send can.
_OPEN_WORLD: Final = False


def humanize(name: str) -> str:
    """Derive a display title from a tool's function name.

    ``Tool.title`` is what a client puts in front of the user, so deriving it
    keeps it from drifting away from the name the model sees. Only the first
    word is capitalized — usually the verb, but ``bulk_*`` leads with its scope.
    """
    verb, *rest = name.split("_")
    return " ".join([verb.capitalize(), *(_TITLE_WORDS.get(word, word) for word in rest)])


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One tool, the hints it advertises and the flag that exposes it.

    Args:
        fn: The tool coroutine, before :func:`safe_tool` wraps it.
        annotations: The MCP hints a client reads before deciding to call.
        gate: Which visibility flag decides whether it is registered.
    """

    fn: ToolFunc
    annotations: ToolAnnotations
    gate: Gate


def read_tool(fn: ToolFunc) -> ToolSpec:
    """Declare a tool that only reads from Paperless.

    ``destructiveHint`` and ``idempotentHint`` stay unset on purpose: the spec
    gives them meaning only when ``readOnlyHint`` is false, so sending them
    here would be noise a client has to ignore.
    """
    return ToolSpec(fn, ToolAnnotations(read_only_hint=True, open_world_hint=_OPEN_WORLD), "read")


def write_tool(fn: ToolFunc, *, destructive: bool, idempotent: bool) -> ToolSpec:
    """Declare a tool that creates or modifies data.

    Args:
        fn: The tool coroutine.
        destructive: Whether a call can overwrite or discard data that was
            already there. Purely additive tools (upload, create, note,
            restore) are not destructive; replacing a field value is.
        idempotent: Whether repeating the identical call converges on the same
            state. False for anything that adds another row, queues another
            task or accumulates (rotation!), so that a client cannot treat a
            retry as free.
    """
    return ToolSpec(
        fn,
        ToolAnnotations(
            read_only_hint=False,
            destructive_hint=destructive,
            idempotent_hint=idempotent,
            open_world_hint=_OPEN_WORLD,
        ),
        "write",
    )


def delete_tool(fn: ToolFunc) -> ToolSpec:
    """Declare a tool that removes data.

    Deletes are destructive by definition and idempotent in effect: a second
    call finds the object already gone and leaves the archive as it was.
    """
    return ToolSpec(
        fn,
        ToolAnnotations(
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=True,
            open_world_hint=_OPEN_WORLD,
        ),
        "delete",
    )


def register_tools(mcp: MCPServer, settings: Settings, specs: Iterable[ToolSpec]) -> None:
    """Register the specs this deployment exposes, annotated, titled and wrapped.

    The gate is checked here rather than at call time, so a read-only deployment
    simply does not advertise the tool.
    """
    exposed: Mapping[Gate, bool] = {
        "read": True,
        "write": settings.expose_writes,
        "delete": settings.expose_deletes,
    }
    for spec in specs:
        if exposed[spec.gate]:
            mcp.tool(title=humanize(spec.fn.__name__), annotations=spec.annotations)(
                safe_tool(spec.fn)
            )
