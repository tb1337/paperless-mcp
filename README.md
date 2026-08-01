# paperless-mcp

A [Model Context Protocol][mcp] server for [Paperless-ngx][pngx], powered by
[pypaperless][pyp] 6.0. It speaks **stdio** — so Claude Desktop (or any other
MCP client) can launch it directly — and **Streamable HTTP**, so one instance
can serve a whole network.

[mcp]: https://modelcontextprotocol.io/
[pngx]: https://github.com/paperless-ngx/paperless-ngx
[pyp]: https://github.com/tb1337/pypaperless

> [!WARNING]
>
> **Highly experimental — use at your own risk.**
>
> This project is young and moving fast. Tool names, parameters and return
> shapes still change; such changes are labelled `breaking-change` in the
> release notes, but they do happen, and there is no deprecation window yet.
> It also rides on a pypaperless **release candidate** and needs a
> Paperless-ngx 3.0 that is itself fresh.
>
> Nothing here has been proven against your instance, and the caller is an LLM
> deciding on its own which tools to invoke. If the documents matter to you:
> have backups, start with
> [`PAPERLESS_MCP_READONLY=true`](#configuration), and leave the delete tools
> switched off (that is the default) until you trust the setup.

## By an LLM, for LLMs

The thing on the other end of this protocol is a language model — and so is the
thing that wrote most of the code behind it. [@tb1337](https://github.com/tb1337)
builds this project deliberately together with **Claude**: he sets the
direction, writes the specs and reviews every diff; Claude does the typing.
That is a stated design choice, not an embarrassing detail buried in the commit
log.

It also shapes the server itself. Every decision here optimises for a model as
the caller, not a human clicking through a UI:

- errors come back as structured results the model can read and recover from,
  never as protocol-level failures that just end the conversation,
- list tools page server-side, so a model can walk deep into a result set
  without burning its context on 500 documents it did not ask for,
- the tool surface can be narrowed (read-only, deletes off by default), because
  an autonomous caller should not be handed a destructive verb by accident,
- and things like thumbnails come back as real images the model can actually
  look at.

## Features

- **Two transports**: `stdio` (default, for locally spawned clients) and
  `http` (Streamable HTTP at `/mcp`, for network use).
- **Bearer-token auth** on the HTTP endpoint, plus an unauthenticated
  `/healthz` for container probes.
- **Tunable surface**: writes can be disabled (`PAPERLESS_MCP_READONLY=true`)
  and deletes require explicit opt-in (`PAPERLESS_MCP_ENABLE_DELETE=true`).
  45 tools by default, 54 with deletes enabled.
- **Server-side pagination** on every list-shaped tool: `offset`/`limit` are
  translated into Paperless page requests, so paging deep into a result set
  costs at most two HTTP calls and each response reports `total` and
  `has_more`.
- **Structured errors**: pypaperless exceptions become results like
  `{"error": "not_found", "detail": "...", "cause": "..."}` instead of
  protocol-level failures, so the model can recover rather than give up.
- **Survives an unreachable Paperless**: the connection is established lazily
  and retried per call, so the MCP handshake never fails just because the
  server was briefly down.
- **Thumbnails as real images**, viewable inline in the client.
- Built on **pypaperless 6.0.0rc2** and the **MCP Python SDK 2.0** — requires
  **Paperless-ngx 3.0+**.

## Requirements

- **[uv](https://docs.astral.sh/uv/)** on the machine that runs the MCP client.
  It manages the virtualenv *and* the Python toolchain — no system Python 3.13
  needed, `uv` fetches one if it has to.
- **Paperless-ngx 3.0+** with an API token
  (**Settings → API tokens**, or `/api/token/`).

## Installation — uv

[`paperless-mcp` is on PyPI](https://pypi.org/project/paperless-mcp/), so `uv`
can install it without a clone. One wrinkle applies to every command below: this
release depends on `pypaperless==6.0.0rc2`, a release **candidate**, and uv
ignores pre-releases unless told otherwise. Two flags handle that:

```
--prerelease=if-necessary-or-explicit --with 'pypaperless==6.0.0rc2'
```

Naming pypaperless explicitly makes it the *one* package allowed to be a
pre-release, so everything else stays on stable versions — the result is the
same dependency set this repo's `uv.lock` pins and CI tests against. Do not
reach for a bare `--prerelease=allow` instead: it opens the door for every
dependency, uv then picks `httpx==1.0.dev3`, and the server dies on import with
`module 'httpx' has no attribute 'AsyncClient'`. Both flags become unnecessary
once pypaperless 6.0.0 final ships.

### Install it as a tool (recommended)

```bash
uv tool install --prerelease=if-necessary-or-explicit \
  --with 'pypaperless==6.0.0rc2' paperless-mcp
```

That leaves a standalone `paperless-mcp` executable on your `PATH`. Note down
its absolute path — the MCP client will need it — and check it runs:

```bash
command -v paperless-mcp   # macOS/Linux, typically ~/.local/bin/paperless-mcp
where paperless-mcp        # Windows
paperless-mcp --help
```

Later releases: `uv tool upgrade paperless-mcp`, then restart the client.

### Or run it straight from PyPI, without installing

```bash
uvx --prerelease=if-necessary-or-explicit --with 'pypaperless==6.0.0rc2' \
  --from paperless-mcp paperless-mcp --help
```

`uvx` resolves into a cache on first use and reuses it afterwards. Handy for a
quick look or for running the HTTP transport ad hoc; for a client that spawns
the server on every launch, the installed tool above is the tidier option.

### Connect Claude Desktop

Add the `paperless` entry to your `claude_desktop_config.json` and restart the
app completely (quit it, don't just close the window — the config is read once
at startup).

With the tool installed, `command` is the only path involved:

```json
{
  "mcpServers": {
    "paperless": {
      "command": "/absolute/path/to/paperless-mcp",
      "env": {
        "PAPERLESS_URL": "https://paperless.example.com",
        "PAPERLESS_TOKEN": "your-paperless-api-token",
        "PAPERLESS_MCP_READONLY": "false",
        "PAPERLESS_MCP_ENABLE_DELETE": "false"
      }
    }
  }
}
```

Or let the client drive `uvx`, with the resolver flags moved into `args`:

```json
{
  "mcpServers": {
    "paperless": {
      "command": "/absolute/path/to/uvx",
      "args": [
        "--prerelease=if-necessary-or-explicit",
        "--with", "pypaperless==6.0.0rc2",
        "--from", "paperless-mcp",
        "paperless-mcp"
      ],
      "env": {
        "PAPERLESS_URL": "https://paperless.example.com",
        "PAPERLESS_TOKEN": "your-paperless-api-token",
        "PAPERLESS_MCP_READONLY": "false",
        "PAPERLESS_MCP_ENABLE_DELETE": "false"
      }
    }
  }
}
```

`command` must be **absolute** in both cases. Claude Desktop does not inherit
your shell `PATH`, so a bare `paperless-mcp` or `uvx` usually fails to resolve —
paste what `command -v` / `where` reported above.

The last two `env` entries are the safety switches, spelled out here so they are
easy to find: `PAPERLESS_MCP_READONLY=true` hides every write and delete tool,
`PAPERLESS_MCP_ENABLE_DELETE=true` adds the delete tools on top of the default
read+write set. See [Configuration](#configuration) for the rest.

Config file locations:

| OS | Path |
|---|---|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |

Ready-made files live in [`examples/`](examples/):
[`claude_desktop_config.json`](examples/claude_desktop_config.json) for the
installed tool, and
[`claude_desktop_config.local-checkout.json`](examples/claude_desktop_config.local-checkout.json)
for the clone below.

Any other MCP client that spawns stdio servers takes the same
command/args/env triple.

### From a git clone instead

For hacking on the server, or to run something newer than the last release:

```bash
git clone https://github.com/tb1337/paperless-mcp.git
cd paperless-mcp
uv sync
uv run paperless-mcp --help
```

The clone needs no resolver flags — `uv.lock` already pins the pre-release. Give
the clone a permanent home and point the client at it; both paths absolute:

```json
{
  "mcpServers": {
    "paperless": {
      "command": "/absolute/path/to/uv",
      "args": ["run", "--directory", "/absolute/path/to/paperless-mcp", "paperless-mcp"],
      "env": {
        "PAPERLESS_URL": "https://paperless.example.com",
        "PAPERLESS_TOKEN": "your-paperless-api-token",
        "PAPERLESS_MCP_READONLY": "false",
        "PAPERLESS_MCP_ENABLE_DELETE": "false"
      }
    }
  }
}
```

`uv run` switches into `--directory` and uses that project's environment,
whatever working directory the client happens to start it in. After every
`git pull`, run `uv sync` again so the environment matches the committed
lockfile, then restart the client.

### Troubleshooting

- **`No solution found when resolving tool dependencies` naming
  `pypaperless==6.0.0rc2`** — the pre-release flags are missing; see above.
- **`module 'httpx' has no attribute 'AsyncClient'`** — resolved with
  `--prerelease=allow`, which let uv pick an httpx dev build. Use the targeted
  flags above.
- **"Server disconnected" right after startup** — almost always the `command`.
  Use an absolute path; see above.
- **"No such file or directory" / the server starts but nothing works** — for
  the clone setup, the `--directory` path does not point at the clone (it needs
  the directory containing `pyproject.toml`).
- **Tools appear but every call returns `connection_error`** — `PAPERLESS_URL`
  is wrong or unreachable from the machine running the client. The server
  starts anyway by design; the error text names the cause.
- **`auth_failed`** — the API token is wrong, or belongs to a deactivated user.
- **Self-signed certificate** — set `"PAPERLESS_MCP_VERIFY_SSL": "false"`.
- Logs go to stderr and end up in Claude Desktop's MCP log
  (`~/Library/Logs/Claude/mcp-server-paperless.log` on macOS). Set
  `"PAPERLESS_MCP_LOG_LEVEL": "DEBUG"` for more detail.

## Configuration

Every setting has an environment variable and a command-line flag; the flag
wins. A `.env` file in the working directory is loaded automatically (override
with `--env-file`) and never overwrites variables the MCP client already set.

| Variable | Flag | Default | Purpose |
|---|---|---|---|
| `PAPERLESS_URL` | `--url` | — (required) | Base URL of your Paperless-ngx instance |
| `PAPERLESS_TOKEN` | `--token` | — (required) | Paperless-ngx API token |
| `PAPERLESS_MCP_TRANSPORT` | `--transport` / `--stdio` / `--http` | `stdio` | `stdio` or `http` |
| `PAPERLESS_MCP_HOST` | `--host` | `127.0.0.1` | Bind address (http only) |
| `PAPERLESS_MCP_PORT` | `--port` | `8000` | Bind port (http only) |
| `PAPERLESS_MCP_AUTH_TOKEN` | `--auth-token` | _empty_ | Bearer token required on `/mcp` |
| `PAPERLESS_MCP_READONLY` | `--readonly` | `false` | If true: hide every write/delete tool |
| `PAPERLESS_MCP_ENABLE_DELETE` | `--enable-delete` | `false` | If true: expose delete tools |
| `PAPERLESS_MCP_MAX_FILE_BYTES` | `--max-file-bytes` | `25000000` | Cap for file/thumbnail payloads |
| `PAPERLESS_MCP_VERIFY_SSL` | `--no-verify-ssl` | `true` | TLS certificate verification |
| `PAPERLESS_MCP_TIMEOUT` | `--timeout` | `30` | Per-request HTTP timeout, seconds |
| `PAPERLESS_MCP_LOG_LEVEL` | `--log-level` | `INFO` | Verbosity (always logged to stderr) |

### Tool visibility matrix

| Mode | Read tools | Write tools | Delete tools |
|---|---|---|---|
| `READONLY=true` | ✅ | ❌ | ❌ |
| Default | ✅ | ✅ | ❌ |
| `ENABLE_DELETE=true` | ✅ | ✅ | ✅ |

`READONLY=true` always wins, even if `ENABLE_DELETE=true`.

## Tools

**Read**: `search_documents`, `get_document`, `get_document_content`,
`get_document_metadata`, `get_document_notes`, `get_document_history`,
`find_similar_documents`, `download_document`, `get_document_thumbnail`,
`list_tags`, `list_correspondents`, `list_document_types`,
`list_storage_paths`, `list_custom_fields`, `list_share_links`,
`list_saved_views`, `get_saved_view`, `list_trash`, `list_active_tasks`,
`list_tasks`, `get_task`, `get_statistics`, `get_paperless_info`,
`get_document_suggestions`, `get_document_ai_suggestions`.

**Write** (default-on, suppressed by `READONLY`): `upload_document`,
`update_document`, `add_document_note`, `bulk_edit_documents`,
`bulk_reprocess_documents`, `bulk_merge_documents`, `bulk_rotate_documents`,
`acknowledge_tasks`, `create_tag`, `update_tag`, `create_correspondent`,
`update_correspondent`, `create_document_type`, `update_document_type`,
`create_storage_path`, `update_storage_path`, `create_custom_field`,
`update_custom_field`, `create_share_link`, `restore_documents`.

**Delete** (requires `ENABLE_DELETE=true`): `delete_document`,
`delete_document_note`, `delete_tag`, `delete_correspondent`,
`delete_document_type`, `delete_storage_path`, `delete_custom_field`,
`delete_share_link`, `empty_trash`.

Notes on semantics:

- `search_documents` combines a Whoosh full-text `query` with Django-style
  filters, and takes `order_by` / `descending`.
- `update_document` **replaces** the tag list and accepts a `clear_fields`
  list (`correspondent`, `document_type`, `storage_path`,
  `archive_serial_number`) to unset foreign keys. Setting and clearing the
  same field in one call is rejected. Use `bulk_edit_documents` to add or
  remove individual tags.
- `upload_document` returns a task UUID; poll it with `get_task`.

## Running over HTTP / in Docker

The server also speaks Streamable HTTP at `/mcp`, guarded by a bearer token,
and the repo ships a `Dockerfile` and a `docker-compose.yml` for exactly that.
Documentation for this path is coming — start with the uv setup above; it is
the supported route for now.

## Out of scope (for now)

- **Workflows, mail accounts/rules, users, groups, config**: admin-tier
  concerns where letting an LLM make autonomous changes is rarely the right
  answer.
- **Executing saved views**: `get_saved_view` returns the filter rules so the
  model can translate them into a `search_documents` call, but there is no
  auto-execution — Paperless' filter-rule numbering is internal and mapping it
  to Django-style lookups is brittle.
- **The document chat endpoint** (`/api/documents/chat/`): it streams plain
  text, and pypaperless 6.0's `DocumentChat` model carries only the echoed
  query, so no answer ever reaches the caller. A tool for it would fail every
  time; it will come back if the library starts returning the response body.

## Development

This repo ships a VS Code devcontainer based on
`mcr.microsoft.com/devcontainers/python:3-3.13`, with `uv`, `ruff`, pytest and
the docker CLI preinstalled. Open the project in VS Code and select
**"Reopen in Container"** — the dev venv lands at
`/home/vscode/.local/dev-venv` and dependencies are synced via `uv` on
container start.

```bash
script/bootstrap             # uv sync --group dev
uv run pytest                # suite + coverage (gate: 80 %)
uv run ruff check --fix .    # lint
uv run ruff format .         # format
uv run mypy                  # strict, on the paperless_mcp package
prek run --all-files         # everything CI lints, in one go
uv run paperless-mcp --help
```

[AGENTS.md](AGENTS.md) documents the module layout and the conventions that
hold this together — the tool surface as public API, why tools never raise, why
list tools must paginate. Worth reading before adding a tool, whether you are
human or not.

## License

[MIT](LICENSE.md).
