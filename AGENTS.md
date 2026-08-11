# AGENTS.md

Guidance for AI coding agents working in this repo.

## Project overview

MCP (Model Context Protocol) server exposing Paperless-ngx to LLM clients, built on
**pypaperless**. Package manager: **uv**. Layout is a `src/` package.

Public entry point: the `paperless-mcp` console script → `paperless_mcp.__main__:main`;
in-process it is `build_mcp(settings)` / `serve(settings)` in `server.py`.

- `src/paperless_mcp/` — server source
  - `tools/` — one module per resource area (`documents`, `taxonomy`, `custom_field_values`,
    `bulk`, `trash`, `tasks`, `system`, `saved_views`, `ai`, `share_links`), each exposing a
    `register(mcp, settings)` function;
    `tools/__init__.py` calls them all from `register_all()`
  - `tools/_errors.py` — `safe_tool` (exception → structured error), the ordered `_ERROR_MAP`,
    `ToolInputError`, `ToolResultError`
  - `tools/_registry.py` — `ToolSpec` plus the three factories that build one (`read_tool`,
    `write_tool`, `delete_tool`), `register_tools()` which applies `safe_tool`, the
    visibility gate, the two passes over a signature's annotations without which an
    argument reaches the model as `{}` (`inline_aliases()` for the `$ref`,
    `flatten_optionals()` for the `anyOf`), the `structured_output=False` that stops every
    result being serialized twice, and the `humanize()` that derives each display title
  - `tools/_arguments.py` — the `Literal` aliases the constrained arguments publish as schema
    enums, plus the name → pypaperless-enum mappings
  - `tools/_dates.py` — `parse_date` / `parse_datetime` for the ISO arguments
  - `tools/_paging.py` — `paginate` / `page_result` (offset/limit → Paperless pages), `window`
    for the endpoints that answer with a bare list, `MAX_PAGE_LIMIT` with the
    `check_limit` / `check_window` that enforce it, and the `Page` / `Filterable` protocols
  - `tools/_relations.py` — `<field>_name` → ID for the relation arguments (`resolve_relation`,
    `resolve_tags`)
  - `prompts/` — one module per workflow (`triage`, `review`, `duplicates`), same
    `register(mcp, settings)` / `register_all()` shape as `tools/`;
    `prompts/_helpers.py` holds `sections()` and `capability_note()`
  - `resources.py` — the master-data registry (`Resource`, `RESOURCES`, `RELATIONS`,
    `BULK_OBJECTS`), sitting above `tools/` and `names`/`formatting` so a resource is
    declared once. Five tables used to enumerate the same list; adding one meant five
    edits, four of them failing silently when missed
  - top-level: `server.py` (MCPServer wiring, lifespans, stdio + Streamable HTTP transports),
    `client.py` (`PaperlessConnection`, lazy connect, `get_client` / `get_names` /
    `invalidate_names` / `get_settings` / `ToolContext`, and the `LifespanContext` TypedDict
    they read), `config.py` (env-driven `Settings` dataclass with `Transport` / `LogLevel`
    as StrEnums, `load_settings()`), `names.py` (`NameMap` snapshot of the master data,
    `load_names()`, the TTL'd `NameCache`), `formatting.py` (pypaperless models → plain dicts),
    `auth.py` (bearer-token middleware for the HTTP transport), `healthcheck.py`
    (unauthenticated `/healthz` probe)
- `tests/` — pytest driving the real `MCPServer` in-process over a fake PaperlessClient
  (`tests/conftest.py`); no network in tests
- `script/` — `bootstrap` (resync dev venv), `setup` (devcontainer entry point)
- `run/debug.py` — scratch runner: builds the real server, opens one session and calls tools
  against the live instance from `.env`; the target of the VS Code debug configuration
- `examples/` — ready-made `claude_desktop_config.json` variants

Current code surface (trust these over older docs): MCP SDK 2.x — the server class is `MCPServer`
from `mcp.server.mcpserver`, not `FastMCP` · pypaperless is pinned exactly (`==6.0.0`), so a
version bump is a deliberate change with a test run, never an automerge · 64 tools (30 read,
24 write, 10 delete), enumerated in `tests/test_tool_registration.py` · 3 workflow prompts,
enumerated in `tests/test_prompt_registration.py`.

Every request goes through a pypaperless service, with one exception:
`taxonomy._delete_objects` posts to `/api/bulk_edit_objects/` through
`paperless.runtime.transport`, because the service there only ever sends an `objects` list and
pypaperless 6.0.0 predates the `all` + `filters` selection API v10 added. Once pypaperless carries
those, that helper is what to delete. It is not a licence for a second one: reaching past a
service that does cover the endpoint loses the model parsing, the caches and the error types.

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
   There is no smoketest suite; call the tool from `run/debug.py`, which drives the real server
   against the instance in your `.env`.

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
- **A constrained argument is an enum, not a `str`.** Its allowed values belong in
  `tools/_arguments.py` as a `Literal` alias, so the schema publishes them and pydantic refuses the
  rest — never in a module constant checked by hand and re-listed in the docstring, which is three
  copies of one list. Two consequences worth knowing: a rejected value comes back as a
  protocol-level error rather than `{"error": "invalid_argument"}`, because pydantic runs before
  `safe_tool`; and **an alias is not published by itself**. A PEP 695 alias is a `TypeAliasType`,
  which pydantic renders as a named `$defs` entry plus a `$ref` — valid JSON Schema, and invisible: a
  live check against a real client showed all fifteen of these arguments arriving at the model as a
  bare `{}`, values correct and unreachable. `_registry.inline_aliases` therefore expands every alias
  in a tool's annotations at registration, so the values land inline in the property a client reads.
  Adding an alias needs nothing extra; adding a *recursive* one does not work at all, since a
  recursive schema can only be a `$ref` — which is why `custom_field_query` types just its outer
  shape and leaves the nesting to its own validator. The aliases mirror pypaperless enums minus their
  `UNKNOWN` member; `tests/test_arguments.py` ties each list back to the library and
  `tests/test_tool_registration.py` asserts the values reach the published schema, so neither half
  can go stale.
- **A published property carries its own `type`, never an `anyOf`.** Inlining the aliases only got
  the values out of `$defs`; the follow-up check found twelve of the fifteen still arriving as `{}`,
  and this time it was not an enum problem at all. `X | None` renders as
  `anyOf: [<X>, {"type": "null"}]` — a property with no `type` of its own — and 123 of the 222
  arguments here were in that shape, `title` and `color` and `tag_ids` along with the enums. The
  three that did arrive were exactly the three non-optional ones; `create_tag.is_inbox_tag` (`bool`)
  against `update_tag.is_inbox_tag` (`bool | None`) is the controlled pair. `_registry`
  `flatten_optionals` therefore attaches a `WithJsonSchema` that publishes the branch type inline,
  dropping the `null` a client can read back off `required` and `default`. It replaces what a tool
  *advertises* and not what it *accepts* — the signature stays `X | None`, so an explicit `null`
  still validates and the enums are still enforced by pydantic. A client that validates *outgoing*
  arguments against the published schema will refuse an explicit `null` and expect the argument to
  be omitted, which is how one is meant to be passed anyway.
  The `type` must be a **scalar**, never a list. `custom_field_query` published
  `type: ["array", "string"]` — accurate, and the one argument a follow-up check still found
  arriving as `{}` once every scalar-typed one came through. A client that drops `anyOf` drops a
  `type` list the same way, so only the **first** branch of a union is published and the rest stay
  accepted but unadvertised. A multi-branch annotation therefore has to lead with the form callers
  should reach for; the docstring is what documents the others. Three assertions guard the whole
  mechanism: no published schema contains an `anyOf`, none publishes a list of types, and every
  property has a `type` — bar `set_document_custom_field.value`, which is `Any`, where the empty
  schema is the correct answer rather than a lost one.
- **Tools live at module level and are declared in one table per module.** A module's
  `register()` is a single `register_tools(mcp, settings, (...))` call over `read_tool(fn)` /
  `write_tool(fn, destructive=…, idempotent=…)` / `delete_tool(fn)` — never a bare
  `@mcp.tool()`, and never a function nested inside `register()`. `register_tools` is what
  applies `safe_tool` and checks the visibility flag, so neither is repeated per tool and a
  tool cannot reach a client unwrapped. The table is also the point: the flags' whole value is
  consistency across 64 tools, and an inconsistency between the twenty CRUD tools is visible in
  a 26-line block and invisible when spread over 700 lines.
  The two `write_tool` flags are a judgement call worth making deliberately: `destructive`
  means the call can overwrite data that was already stored, `idempotent` means repeating the
  identical call converges on the same state — false for anything that adds a row, queues a
  task or accumulates (rotation being the obvious trap).
  `tests/test_tool_registration.py` pins the non-obvious ones.
- **List tools paginate, and the window has a ceiling.** Anything list-shaped takes
  `offset` / `limit` and returns `total` and `has_more` via `page_result`. Do not add a tool that
  can return an unbounded result set. `limit` may not exceed `_paging.MAX_PAGE_LIMIT` (100),
  enforced once in `check_window` rather than per tool, because the model picks the window and
  "more at once" is the tempting choice: 100 documents serialize to ~42k tokens and 250 to ~105k,
  past the cap a client puts on one tool result — which arrives as a protocol-level failure the
  model cannot read. Over the ceiling is **refused**, never clamped: `limit` is echoed back in
  every envelope and cannot mean "what you asked for" in one result and "what you got" in the
  next. A tool that takes a `limit` outside `paginate` / `window` calls `check_limit` itself, so
  the ceiling has no exceptions for a model to discover.
- **Results go out once.** Tools are registered with `structured_output=False`. The SDK builds
  both halves of a `CallToolResult` from the same return value — a JSON text block *and*
  `structuredContent` — and the wire model carries both, so every byte is paid for twice: one
  25-document search measured 23,646 characters of text next to 18,478 of structured content.
  The cost lands hardest on the largest results, which are exactly the ones nobody re-measures,
  so `tests/test_tool_registration.py` pins that no tool publishes an `outputSchema`. Note what
  this retires: `ToolResultError` exists because the SDK could not build an output schema for a
  union containing MCP content, and there is no output schema any more — the type is still how a
  `-> Image` tool reports an error, but that is now its only job.
- **IDs come with names.** A relation is reported as the raw ID *plus* a `<field>_name` resolved
  through the `NameMap` a tool passes into the formatter. Await `get_names(ctx)` before fetching
  documents, never after: the same call fills pypaperless' custom-field cache, which enriches a
  `Document` while it is being parsed. Anything that creates, renames or deletes master data
  calls `invalidate_names(ctx)`.
- **Names come back in.** Every argument that assigns or filters a relation exists twice —
  `<field>_id` and `<field>_name`, `tag_ids` and `tag_names` — resolved through
  `_relations.resolve_relation` / `resolve_tags`. A wrong ID silently hits another valid object;
  a wrong name cannot, and it is the half a human reading the call along can veto. Matching is
  exact, then case-insensitive, **never fuzzy**, and ambiguity is an error rather than a pick. A
  miss buys one `invalidate_names` + reload before it is refused, which covers master data
  created elsewhere since the snapshot. Supplying both halves cross-checks them: a disagreement
  is rejected, never ranked. Resolution runs before the first write request, so a typo cannot
  leave a half-applied bulk edit. A tool that gains a relation argument gains both spellings, and
  its docstring carries the rule verbatim: *pass `*_name` when the value comes from the
  conversation, `*_id` only when you have it verbatim from a tool result; passing both is allowed
  but they must agree*.
- **Writes and deletes are gated.** `settings.expose_writes` and `settings.expose_deletes` decide
  whether a tool is registered at all, never whether it fails at call time, so a read-only
  deployment simply does not advertise the tool. Declaring it with `write_tool` / `delete_tool` is
  what applies the gate — `register_tools` reads the flag, so no module branches on it itself.
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
