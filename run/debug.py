"""Scratch pad for debugging paperless-mcp against a live Paperless-ngx.

Start it from the VS Code debug view ("Python: paperless-mcp debug script") or
with ``uv run python run/debug.py``. Connection settings come from the
git-ignored ``.env`` in the repo root — the same file the server itself reads.

Tools are invoked the way ``tests/conftest.py`` invokes them: MCPServer's
request pipeline only exists inside a real MCP session, so the server lifespan
is entered by hand and the registered function is called with that context.
Edit :func:`main` freely; it is a scratch file, not a fixture.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from mcp.server.mcpserver import MCPServer

from paperless_mcp.__main__ import resolve_settings
from paperless_mcp.server import build_mcp

#: Resolved from ``__file__`` so the script works from any working directory.
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def tool_session(mcp: MCPServer) -> AsyncIterator[Callable[..., Awaitable[Any]]]:
    """Open the server lifespan once and yield a caller for the registered tools.

    Keeping a single session open across calls is what a real client does, and
    it keeps the Paperless connection and its name cache warm between them.
    """
    async with mcp._lowlevel_server.lifespan(mcp._lowlevel_server) as lifespan_ctx:
        ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context=lifespan_ctx))

        async def call(tool_name: str, /, **kwargs: Any) -> Any:
            return await mcp._tool_manager._tools[tool_name].fn(ctx=ctx, **kwargs)

        yield call


async def main() -> None:
    """Poke the tool surface; put a breakpoint wherever it gets interesting."""
    settings = resolve_settings(["--env-file", str(ENV_FILE)])
    mcp = build_mcp(settings)

    async with tool_session(mcp) as call:
        info = await call("get_paperless_info")
        print(info)

        docs = await call("search_documents", limit=3)
        print(docs)

        a = 1  # breakpoint


if __name__ == "__main__":
    asyncio.run(main())
