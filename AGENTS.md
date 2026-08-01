# AGENTS.md

Guidance for AI coding agents working in this repo.

## Project overview

MCP (Model Context Protocol) server exposing Paperless-ngx to LLM clients, built on
**pypaperless**. Package manager: **uv**. Layout is a `src/` package.

Public entry point: the `paperless-mcp` console script → `paperless_mcp.__main__:main`;
in-process it is `build_mcp(settings)` / `serve(settings)` in `server.py`.

- `src/paperless_mcp/` — server source
  - `tools/` — one module per resource area (`documents`, `taxonomy`, `custom_field_values`,
    `bulk`, `trash`, `tasks`, `system`, `ai`, `share_links`), each exposing a
    `register(mcp, settings)` function;
    `tools/__init__.py` calls them all from `register_all()`
  - `tools/_helpers.py` — registration decorators (`read_tool`, `write_tool`, `delete_tool`),
    `safe_tool` (exception → structured error), `paginate` / `page_result` (offset/limit →
    Paperless pages), `ToolInputError`
  - `prompts/` — one module per workflow (`triage`, `review`, `duplicates`), same
    `register(mcp, settings)` / `register_all()` shape as `tools/`;
    `prompts/_helpers.py` holds `sections()` and `capability_note()`
  - top-level: `server.py` (MCPServer wiring, lifespans, stdio + Streamable HTTP transports),
    `client.py` (`PaperlessConnection`, lazy connect, `get_client` / `get_names` /
    `invalidate_names` / `get_settings` / `ToolContext`), `config.py` (env-driven `Settings`
    dataclass, `load_settings()`), `names.py` (`NameMap` snapshot of the master data,
    `load_names()`, the TTL'd `NameCache`), `formatting.py` (pypaperless models → plain dicts),
    `auth.py` (bearer-token middleware for the HTTP transport), `healthcheck.py`
    (unauthenticated `/healthz` probe)
- `tests/` — pytest driving the real `MCPServer` in-process over a fake PaperlessClient
  (`tests/conftest.py`); no network in tests
- `script/` — `bootstrap` (resync dev venv), `setup` (devcontainer entry point)
- `examples/` — ready-made `claude_desktop_config.json` variants

Current code surface (trust these over older docs): MCP SDK 2.x — the server class is `MCPServer`
from `mcp.server.mcpserver`, not `FastMCP` · pypaperless is pinned exactly (`==6.0.0rc2`), so a
version bump is a deliberate change with a test run, never an automerge · 56 tools (25 read,
22 write, 9 delete), enumerated in `tests/test_tool_registration.py` · 3 workflow prompts,
enumerated in `tests/test_prompt_registration.py`.

## Dev Commands

- `script/bootstrap` — install deps (`uv sync --group dev`); idempotent, safe to rerun
- `uv run pytest` — full suite + coverage (gate: 80 %)
- `uv run pytest -x -q` — fast loop
- `uv run mypy` — static type check (strict, on the `paperless_mcp` package)
- `uv run ruff check --fix .` — lint
- `uv run ruff format .` — format (Markdown too: it formats Python snippets inside code fences)
- `uv run codespell` — spell check
- `uv run yamllint .` — lint YAML
- `prek run --all-files` — every hook CI runs, in one go

Local dev instance credentials live in the git-ignored `.env` (`PAPERLESS_URL`,
`PAPERLESS_TOKEN`) — copy `.env.example` to `.env`. That file documents every `PAPERLESS_MCP_*`
knob, so read it before adding a setting. `script/setup` is the devcontainer entry point and also
installs the prek hooks.

## Testing instructions

1. `uv run pytest -x -q` — always required, all green, coverage ≥ 80 %.
2. Try the change against a live Paperless-ngx instance — **only** when it could affect live API
   interaction: a new or changed tool, or edits to `client.py`, `config.py` or `formatting.py`.
   There is no smoketest script; run the server against your `.env` and call the tool.

For docs, pure refactors and docstrings, run the unit tests only and state that the live check was
skipped and why. Report both results (or the skip reason) before closing the task.

The fakes in `tests/conftest.py` mirror pypaperless v6's shape: `filter()` is an async context
manager scoping a subsequent `pages()` call, and `pages()` yields page objects carrying a
server-reported `count`. If a fake and the real library drift apart, the tests pass and production
breaks — when pypaperless changes, check the fakes first.

New behavior needs a test. `tests/*` is exempt from the pydocstyle rules.

## Tool contract

The tool surface is the public API. Renaming a tool, dropping a parameter or changing a return
shape breaks every MCP client in the field: additive changes are cheap, everything else is a
breaking change and gets the `breaking-change` label.

- **Tools never raise.** Every tool is wrapped in `@safe_tool`, which turns pypaperless exceptions
  into `{"error": ..., "detail": ..., "cause": ...}`. A protocol-level failure gives the model
  nothing to recover from; a structured error lets it retry or pick a different call. Bad
  arguments raise `ToolInputError`, which maps to `invalid_argument`.
- **`_ERROR_MAP` stays ordered most-specific-first** — the first match wins, so subclasses must
  precede their bases.
- **Explicit signatures, never `**kwargs`.** The signature becomes the JSON schema the model sees;
  that schema plus the docstring is the only documentation it ever gets. Spell out every parameter
  with a type, and describe the non-obvious ones in the docstring body.
- **Register through `read_tool` / `write_tool` / `delete_tool`**, never a bare `@mcp.tool()`.
  Those helpers attach the MCP annotations and derive the display title from the function name, so
  the hints stay consistent across 56 tools instead of being retyped per call site. The two
  `write_tool` flags are a judgement call worth making deliberately: `destructive` means the call
  can overwrite data that was already stored, `idempotent` means repeating the identical call
  converges on the same state — false for anything that adds a row, queues a task or accumulates
  (rotation being the obvious trap). `tests/test_tool_registration.py` pins the non-obvious ones.
- **List tools paginate.** Anything list-shaped takes `offset` / `limit` and returns `total` and
  `has_more` via `page_result`. Do not add a tool that can return an unbounded result set.
- **IDs come with names.** A relation is reported as the raw ID *plus* a `<field>_name` resolved
  through the `NameMap` a tool passes into the formatter. Await `get_names(ctx)` before fetching
  documents, never after: the same call fills pypaperless' custom-field cache, which enriches a
  `Document` while it is being parsed. Anything that creates, renames or deletes master data
  calls `invalidate_names(ctx)`.
- **Writes and deletes are gated.** `settings.expose_writes` and `settings.expose_deletes` decide
  whether a tool is registered at all — check them in `register()` rather than failing at call
  time, so a read-only deployment simply does not advertise the tool.
- **A new tool module goes in two places**: `tools/__init__.py` and the expected-tool list in
  `tests/test_tool_registration.py`.

## Prompt contract

A prompt is a *workflow*: the plan for a job that takes a dozen tool calls in a set order, with
the Paperless-specific judgement written into it. The tools stay the API; prompts are the recipes.
Like the tool names, a prompt name is public — renaming one breaks the slash command a user
already has.

- **Prompts never touch Paperless.** They render from their arguments and `settings` alone. A
  slash command that can fail because the archive was briefly unreachable defeats the point of
  `PaperlessConnection` being lazy, and a rendered-in document list only competes with the search
  the model is about to run. Anything that needs live data is a tool, not a prompt.
- **Never give a prompt a `Context` parameter.** The SDK wraps every prompt function in pydantic's
  `validate_call`, which re-validates the parameterized `ToolContext` into a fresh instance and
  drops the private attributes carrying the request; the injected context then raises at render
  time, in the client. `tests/test_prompt_registration.py` pins `context_kwarg is None`.
- **Every argument is optional**, because a client renders the slash command as a form and the
  useful default is "just run it". MCP sends arguments as strings, so an `int` annotation is
  relying on pydantic to coerce `"3"` — fine, but keep the failure message readable.
- **The text adapts to the visibility flags**, it is not withheld: `capability_note(settings)` up
  front, and `sections(...)` drops the step this deployment cannot perform (`None` for the write
  branch under `readonly`). Describing a tool that was never registered is the one failure mode
  that matters here.
- **A new prompt module goes in two places**: `prompts/__init__.py` and the expected-prompt set in
  `tests/test_prompt_registration.py`.

## PR instructions

- Branch off `main`; keep the branch scoped to one logical change. A pre-commit hook blocks
  committing to `main` directly.
- Title format: `<type>: <summary>` (e.g. `fix:`, `feat:`, `docs:`, `ci:`); a breaking change is
  `feat!:`.
- Every PR needs at least one of `breaking-change`, `bugfix`, `ci`, `dependencies`,
  `documentation`, `enhancement`, `maintenance`, `new-feature`, `performance`, `refactor` or CI
  fails. The label drives both the release-notes category and the version bump, so pick the one
  that matches the user-visible effect.
- Before opening a PR, all `## Dev Commands` pass and the `## Testing instructions` are satisfied
  (report the live-check result or the skip reason).
- Fill in the PR template and do not uncheck/remove its checkboxes.

## Code style

- Ruff and mypy (strict) must report **0 findings** on new/modified code. Line length 100, target
  Python 3.13.
- `# noqa`, `# type: ignore` and all other suppressions are **forbidden** — fix the root cause.
  The tree contains none; keep it that way. Only `# noqa: F401` on re-export lines in
  `__init__.py` is allowed without asking. A type that cannot be expressed is a design problem:
  `ToolResultError` exists because `get_document_thumbnail` must be annotated `-> Image` (the SDK
  fails to build an output schema for a union containing MCP content) yet still has to report an
  error.
- PEP 695 syntax is in use (`type X = ...`, `def f[T](...)`), and every module starts with
  `from __future__ import annotations`.
- Docstrings: Google convention (ruff pydocstyle), one-line summary first. Tool docstrings are
  written for the model, not the maintainer.
- `formatting.py` owns the model → dict conversion. Tools return plain JSON-able dicts, never
  pypaperless model objects.

## Good practices

- Comments explain *why* (non-obvious constraints, surprises, workarounds), never *what*. Prefer
  one short line, or none. Never justify a change by referencing what the code used to be.
- No section/divider comments (e.g. `# --- Triggers ---`) — they go stale. A module that needs
  them is a module that wants splitting.
- Keep try-clauses minimal: wrap only the statement that can raise, catch only expected exceptions.
- Version pins carry their reasoning in `pyproject.toml` — when you touch a pin, update its
  comment.
- Anything user-facing (a new env var, a new tool, a changed default) lands in `README.md` and
  `.env.example` in the same change.
