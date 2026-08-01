"""Prompt registration entry point.

An MCP prompt here is a *workflow*: the plan for a job that takes a dozen tool
calls in a particular order, with the Paperless-specific judgement calls baked
in — which of three suggestion sources to believe, when a similar document is a
duplicate and when it is just last month's bill again. The tools stay the API;
these are the recipes that turn them into work.

Two properties they all share, both deliberate:

*They render from their arguments and the server's own settings alone, and
never touch Paperless.* A slash command must not be able to fail because the
archive was briefly unreachable — the same reason
:class:`~paperless_mcp.client.PaperlessConnection` connects lazily — and a
rendered-in document list would only compete with the search the model is about
to run anyway, while being one render older.

*None of them declares a ``Context`` parameter, and none should.* The SDK wraps
every prompt function in pydantic's ``validate_call``, which re-validates a
parameterized ``Context[...]`` — the tool-style
:data:`~paperless_mcp.client.ToolContext` — into a fresh instance and drops the
private attributes carrying the request with it. The injected context then
raises "Context is not available outside of a request" on first use, at render
time, in the client.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from ..config import Settings
from . import duplicates, review, triage


def register_all(mcp: MCPServer, settings: Settings) -> None:
    """Register every workflow prompt on the MCPServer instance."""
    triage.register(mcp, settings)
    review.register(mcp, settings)
    duplicates.register(mcp, settings)
