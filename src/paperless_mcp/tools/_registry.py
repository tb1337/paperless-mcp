"""Tool registration: the annotations, the display title and the visibility gate.

Declaring a tool through :func:`read_tool` / :func:`write_tool` / :func:`delete_tool`
rather than a bare ``@mcp.tool()`` is what keeps the behaviour hints consistent
across all 64 tools instead of being retyped per call site.

They are factories rather than decorators, so a tool function lives at module level
and the declarations gather into one table per module. That table is where an
inconsistency between the twenty CRUD tools is visible; scattered across 700 lines
of nested definitions it is not. It also means :func:`safe_tool` is applied in one
place instead of 64, so a tool cannot reach a client unwrapped.

:func:`register_tools` also resolves each signature's annotations before handing the
function over, which is what makes the ``Literal`` aliases in ``_arguments`` visible
to a model rather than merely correct. It takes two passes, because a value can go
missing at either of two layers: :func:`inline_aliases` removes the ``$ref`` into
``$defs``, and :func:`flatten_optionals` removes the ``anyOf`` that wraps whatever the
first pass produced. Both are about what a client *reads*, never what a tool accepts.
"""

from __future__ import annotations

import operator
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import reduce
from types import NoneType, UnionType
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Final,
    ForwardRef,
    Literal,
    TypeAliasType,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import TypeAdapter, WithJsonSchema

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


#: Returned by :func:`_expand` for a subtree that names a type by string. Not an error
#: and not a value: it means "this cannot be rebuilt", which is a different answer from
#: "this expanded to nothing".
_FORWARD: Final = object()


def _expand(annotation: Any) -> Any:
    """Expand the aliases in one annotation, or report a forward reference."""
    if isinstance(annotation, str | ForwardRef):
        return _FORWARD
    if isinstance(annotation, TypeAliasType):
        expanded = _expand(annotation.__value__)
        # A recursive alias refers to itself by name, because the name does not exist
        # yet while its body is being written. Rebuilding it would hand pydantic an
        # unresolved reference, and it has to stay a `$ref` regardless: that is the
        # only way to write a recursive schema.
        return annotation if expanded is _FORWARD else expanded
    args = get_args(annotation)
    # Nothing to walk into, or a `Literal` — whose arguments are values rather than
    # types. Without that second half every enum here would read as a forward
    # reference and none of them would expand.
    if not args or get_origin(annotation) is Literal:
        return annotation
    expanded = tuple(_expand(arg) for arg in args)
    if any(arg is _FORWARD for arg in expanded):
        return _FORWARD
    if expanded == args:
        return annotation
    # `X | None` has to be rebuilt through the operator; everything else — including the
    # `Optional[X]` that `get_type_hints` produces — is subscriptable by its origin.
    origin = get_origin(annotation)
    return reduce(operator.or_, expanded) if origin is UnionType else origin[expanded]


def inline_aliases(annotation: Any) -> Any:
    """Return *annotation* with every PEP 695 alias replaced by the type it stands for.

    A ``type X = Literal[...]`` alias is a *named* type, and pydantic publishes a named
    type as an entry in ``$defs`` plus a ``$ref`` to it. That is valid JSON Schema and it
    is also how the enums in ``_arguments`` went missing: a live check found all fifteen
    constrained arguments arriving at the model as a bare ``{}``, because the client never
    followed the reference. Expanded, the values sit inline in the property a client reads.

    An alias that names a type by string — every recursive one does — is returned as it
    came in, so pydantic still resolves it in the namespace where it is defined.
    """
    expanded = _expand(annotation)
    return annotation if expanded is _FORWARD else expanded


def _optional_branches(annotation: Any) -> tuple[Any, ...] | None:
    """The non-``None`` members of ``X | None``, or ``None`` for anything else."""
    if get_origin(annotation) not in (UnionType, Union):
        return None
    args = get_args(annotation)
    if NoneType not in args:
        return None
    return tuple(arg for arg in args if arg is not NoneType)


def _flat_schema(branches: tuple[Any, ...]) -> dict[str, Any] | None:
    """The schema for an optional's *first* branch, carrying a scalar ``type``.

    Only the first branch is published, and for a union of several that is a
    deliberate narrowing rather than a merge. A list-valued ``type`` is the only
    accurate way to write "an array or a string", and a live check found the one
    argument published that way — ``custom_field_query`` — arriving as a bare ``{}``
    while every scalar-typed argument arrived intact. So a client that drops ``anyOf``
    drops a ``type`` list too, and advertising the primary form beats advertising
    nothing: the remaining branches stay accepted, and the docstring is what documents
    them. An argument's annotation must therefore lead with the form callers should
    reach for first.

    Returns ``None`` when the first branch cannot be published on its own — pydantic
    renders it without a ``type`` (``Any``), or it needs ``$defs`` to be understood.
    Bailing out leaves pydantic's ``anyOf``, which is worse to read but never wrong.
    """
    schema = TypeAdapter(branches[0]).json_schema()
    if not isinstance(schema.get("type"), str) or "$defs" in schema:
        return None
    return schema


def flatten_optionals(annotation: Any) -> Any:
    """Return *annotation* publishing its type inline rather than as an ``anyOf``.

    An optional argument is ``X | None``, which pydantic renders as
    ``anyOf: [<X>, {"type": "null"}]`` — a property with no ``type`` of its own. Every
    argument a live check found arriving as a bare ``{}`` was in that shape, and 123 of
    the 222 arguments on this surface were: the twelve constrained ones, and ``title``,
    ``color`` and ``tag_ids`` with them. Everything that did arrive had a ``type``, so
    the union wrapper is what gets dropped — and :func:`inline_aliases` alone cannot
    help, because it lifts the values out of ``$defs`` and the ``anyOf`` then swallows
    them one layer further out. The controlled pair sits inside one tool pair:
    ``create_tag.is_inbox_tag`` (``bool``) came through, ``update_tag.is_inbox_tag``
    (``bool | None``) did not.

    ``WithJsonSchema`` replaces what an argument *publishes* and leaves what it
    *accepts* on ``X | None``, so an explicit ``null`` still validates and no tool
    changes behaviour. What the published type no longer says is that ``null`` is
    allowed; a client reads that off the argument's absence from ``required`` and its
    ``default`` instead. A client that validates *outgoing* arguments against the
    published schema will refuse an explicit ``null`` and expect the argument to be
    omitted, which is the intended way to pass one anyway.
    """
    branches = _optional_branches(annotation)
    if branches is None:
        return annotation
    flat = _flat_schema(branches)
    return annotation if flat is None else Annotated[annotation, WithJsonSchema(flat)]


def _publishable(name: str, hint: Any) -> Any:
    """Resolve one annotation into the form the schema should be built from.

    ``return`` only gets the aliases inlined. With structured output off nothing reads
    it — the SDK stops at the argument model — so there is no output schema left for a
    flattened return annotation to reach.
    """
    inlined = inline_aliases(hint)
    return inlined if name == "return" else flatten_optionals(inlined)


def register_tools(mcp: MCPServer, settings: Settings, specs: Iterable[ToolSpec]) -> None:
    """Register the specs this deployment exposes, annotated, titled and wrapped.

    The gate is checked here rather than at call time, so a read-only deployment
    simply does not advertise the tool.

    The expanded annotations go on both the tool function and its wrapper, and both
    halves are load-bearing. The SDK builds the schema from the signature, which it
    reads through ``__wrapped__``, so the function has to carry them; it looks for the
    ``Context`` parameter on the object it was handed, so the wrapper has to as well.
    ``functools.wraps`` cannot bridge that: on Python 3.14 it copies ``__annotate__``
    instead of ``__annotations__``, and assigning ``__annotations__`` sets
    ``__annotate__`` to ``None`` — which leaves the wrapper with no annotations at all,
    and a tool that advertises ``ctx`` as an argument for the client to fill.

    Resolving here also means an annotation that cannot be evaluated fails at startup
    rather than on the first call.

    ``structured_output=False`` is what keeps a result from going out twice. The SDK
    builds *both* halves of a ``CallToolResult`` from the same return value — a JSON
    text block and ``structuredContent`` — and the wire model carries both, so every
    byte a tool returns is paid for two times over. It is not a small tax on a list
    tool: one search window of 25 documents measured 23,646 characters of text
    alongside 18,478 of structured content, and a client that caps a tool result by
    tokens rejects the whole call rather than the duplicate half. What this gives up
    is the published ``outputSchema``, which describes a result the model has already
    received; what it buys is 44% of every response.
    """
    exposed: Mapping[Gate, bool] = {
        "read": True,
        "write": settings.expose_writes,
        "delete": settings.expose_deletes,
    }
    for spec in specs:
        if not exposed[spec.gate]:
            continue
        expanded = {
            name: _publishable(name, hint) for name, hint in get_type_hints(spec.fn).items()
        }
        spec.fn.__annotations__ = expanded
        wrapped = safe_tool(spec.fn)
        wrapped.__annotations__ = expanded
        mcp.tool(
            title=humanize(spec.fn.__name__),
            annotations=spec.annotations,
            structured_output=False,
        )(wrapped)
