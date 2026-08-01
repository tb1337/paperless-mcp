# AGENTS.md

Guidance for AI coding agents working in this repo.

## Project overview

MCP (Model Context Protocol) server exposing Paperless-ngx to LLM clients, built on
**pypaperless**. Package manager: **uv**. Layout is a `src/` package.

- `src/paperless_mcp/`
  - `tools/` — one module per resource area (`documents`, `taxonomy`, `bulk`, `trash`,
    `tasks`, `system`, `ai`, `share_links`), each exposing a `register(mcp, settings)`
    function; `tools/__init__.py` calls them all from `register_all()`
  - `tools/_helpers.py` — `safe_tool` (exception → structured error), `paginate` /
    `page_result` (offset/limit → Paperless pages), `ToolInputError`
  - `server.py` — MCPServer wiring, lifespans, stdio + Streamable HTTP transports
  - `client.py` — `PaperlessConnection`, lazy connect, `get_client` / `ToolContext`
  - `config.py` — env-driven `Settings` dataclass and `load_settings()`
  - `formatting.py` — pypaperless models → plain dicts for tool responses
  - `auth.py` — bearer-token middleware for the HTTP transport
  - `healthcheck.py` — unauthenticated `/healthz` probe helper
- `tests/` — pytest with an in-process MCPServer harness over a fake PaperlessClient
  (`tests/conftest.py`); no network in tests
- `script/` — `bootstrap` (resync dev venv), `setup` (devcontainer entry point)
- `examples/` — ready-made `claude_desktop_config.json` variants

## Commands

```bash
script/bootstrap             # uv sync --group dev
uv run pytest                # full suite + coverage (gate: 80 %)
uv run pytest -x -q          # fast loop
uv run mypy                  # strict, on the paperless_mcp package
uv run ruff check --fix .    # lint
uv run ruff format .         # format
prek run --all-files         # everything the CI lints, in one go
```

## Conventions that matter here

**The tool surface is the public API.** Renaming a tool, dropping a parameter or
changing a return shape breaks every MCP client in the field. Treat it like a
released interface: additive changes are cheap, everything else is a breaking
change and gets the `breaking-change` label.

**Tools never raise.** Every tool is wrapped in `@safe_tool`, which turns
pypaperless exceptions into `{"error": ..., "detail": ..., "cause": ...}`. A
protocol-level failure gives the model nothing to recover from; a structured
error lets it retry or pick a different call. Bad arguments raise
`ToolInputError`, which maps to `invalid_argument`. When adding an exception
mapping to `_ERROR_MAP`, keep it ordered most-specific-first — the first match
wins, so subclasses must precede their bases.

**Explicit signatures, not `**kwargs`.** The tool signature becomes the JSON
schema the model sees. Spell out every parameter with a type and a docstring;
that schema is the only documentation the model gets.

**List tools paginate.** Anything list-shaped takes `offset`/`limit` and returns
`total` and `has_more` via `page_result`. Do not add a tool that can return an
unbounded result set.

**Writes and deletes are gated.** `settings.expose_writes` and
`settings.expose_deletes` decide whether a tool is registered at all — check them
in `register()` rather than failing at call time, so a read-only deployment
simply does not advertise the tool.

**Register new modules in two places**: `tools/__init__.py` and the expected-tool
list in `tests/test_tool_registration.py`.

**pypaperless is pinned exactly.** The MCP layer is written against one library
surface, so a version bump is a deliberate change with a test run, never an
automerge.

## Testing

Tests drive the real `MCPServer` in-process against fakes that mirror pypaperless
v6's shape: `filter()` is an async context manager scoping a subsequent `pages()`
call, and `pages()` yields page objects carrying a server-reported `count`. If a
fake and the real library drift apart, the tests pass and production breaks —
when pypaperless changes, check the fakes in `tests/conftest.py` first.

Coverage gate is 80 %. New behavior needs a test; `tests/*` is exempt from the
pydocstyle rules.

## Pull requests

Every PR needs one of these labels or CI fails: `breaking-change`, `bugfix`, `ci`,
`dependencies`, `documentation`, `enhancement`, `maintenance`, `new-feature`,
`performance`, `refactor`. The label drives both the release-notes category and
the version bump, so pick the one that matches the user-visible effect.

Do not commit directly to `main` — a pre-commit hook blocks it.
