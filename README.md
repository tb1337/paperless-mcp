# paperless-mcp

A [Model Context Protocol][mcp] server for [Paperless-ngx][pngx], powered by
[pypaperless][pyp] 6.0. It speaks **stdio** — so Claude Desktop (or any other
MCP client) can launch it directly — and **Streamable HTTP**, so one instance
can serve a whole network from Docker.

[mcp]: https://modelcontextprotocol.io/
[pngx]: https://github.com/paperless-ngx/paperless-ngx
[pyp]: https://github.com/tb1337/pypaperless

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
- Built on **pypaperless 6.0.0rc2** — requires **Paperless-ngx 3.0+**.

## Quick start — Claude Desktop

Add this to your `claude_desktop_config.json` and restart the app:

```json
{
  "mcpServers": {
    "paperless": {
      "command": "uvx",
      "args": ["--from", "paperless-mcp", "paperless-mcp"],
      "env": {
        "PAPERLESS_URL": "https://paperless.example.com",
        "PAPERLESS_TOKEN": "your-paperless-api-token"
      }
    }
  }
}
```

Config file locations:

| OS | Path |
|---|---|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |

Ready-made files live in [`examples/`](examples/): the published package, a git
checkout via `uv run`, and a networked Docker container (see
[below](#connecting-claude-desktop-to-the-container)).

Get the API token from Paperless-ngx under **Settings → API tokens** (or
`/api/token/`). The server runs read+write by default; add
`"PAPERLESS_MCP_READONLY": "true"` to the `env` block if you want Claude to
look but not touch, or `"PAPERLESS_MCP_ENABLE_DELETE": "true"` to also allow
deletions.

### Troubleshooting

- **"Server disconnected" right after startup** — almost always a bad
  `command`. Claude Desktop does not inherit your shell `PATH`, so use an
  absolute path (`/opt/homebrew/bin/uvx`, `which uvx` to find it) if the bare
  name does not resolve.
- **Tools appear but every call returns `connection_error`** — `PAPERLESS_URL`
  is wrong or unreachable from the machine running Claude Desktop. The server
  starts anyway by design; the error text names the cause.
- **`auth_failed`** — the API token is wrong, or belongs to a deactivated user.
- **Self-signed certificate** — set `"PAPERLESS_MCP_VERIFY_SSL": "false"`.
- Logs go to stderr and end up in Claude Desktop's MCP log
  (`~/Library/Logs/Claude/mcp-server-paperless.log` on macOS). Set
  `"PAPERLESS_MCP_LOG_LEVEL": "DEBUG"` for more detail.

## Quick start — Docker (HTTP transport)

```bash
cp .env.example .env
# edit .env: PAPERLESS_URL, PAPERLESS_TOKEN, PAPERLESS_MCP_AUTH_TOKEN
docker compose up -d --build
```

The server listens on `http://<host>:8000/mcp`. Point a network-capable MCP
client at it:

```json
{
  "mcpServers": {
    "paperless": {
      "url": "http://paperless-mcp.lan:8000/mcp",
      "headers": { "Authorization": "Bearer <PAPERLESS_MCP_AUTH_TOKEN>" }
    }
  }
}
```

Running it without Docker:

```bash
uvx --from paperless-mcp paperless-mcp --http --host 0.0.0.0 --port 8000
```

### Connecting Claude Desktop to the container

Claude Desktop cannot point at a LAN address directly. Its **custom connector**
UI hands the URL to Anthropic's cloud, which then calls it — so the endpoint
would have to be reachable from the public internet, and that path currently
offers only OAuth credentials, no bearer header. Everything Claude Desktop
launches itself speaks stdio.

The bridge for this is [`mcp-remote`][mcpremote]: Claude Desktop starts it over
stdio, and it forwards to the container's HTTP endpoint. It needs Node.js on
the machine running Claude Desktop.

```json
{
  "mcpServers": {
    "paperless": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote@latest",
        "http://paperless-mcp.lan:8000/mcp",
        "--allow-http",
        "--transport", "http-only",
        "--header", "Authorization:${AUTH_HEADER}"
      ],
      "env": { "AUTH_HEADER": "Bearer your-paperless-mcp-auth-token" }
    }
  }
}
```

Also in [`examples/claude_desktop_config.docker-http.json`](examples/claude_desktop_config.docker-http.json).

Four details that are easy to get wrong:

- The token goes in `env`, not inline in `args`. Claude Desktop on Windows does
  not escape spaces inside `args`, which would split `Bearer <token>` in two —
  hence `Authorization:${AUTH_HEADER}` with no space after the colon.
- `--allow-http` is required for a plain `http://` URL; drop it once you put
  the endpoint behind TLS.
- `--transport http-only` skips a pointless SSE fallback — this server only
  implements Streamable HTTP.
- Claude Desktop reads the config once at startup. Quit it completely (not just
  close the window) and reopen.

If the token is missing or wrong, `mcp-remote` treats the 401 as an
authentication challenge, tries to discover an OAuth server, and exits with a
bare `Fatal error: ServerError` that never mentions the token. Check that
`AUTH_HEADER` matches the container's `PAPERLESS_MCP_AUTH_TOKEN` and starts
with `Bearer `. `curl http://<host>:8000/healthz` (no auth needed) tells you
whether the container itself is reachable.

[mcpremote]: https://github.com/geelen/mcp-remote

## Configuration

Every setting has an environment variable and a command-line flag; the flag
wins. A `.env` file in the working directory is loaded automatically (override
with `--env-file`) and never overwrites variables the MCP client already set.

| Variable | Flag | Default | Purpose |
|---|---|---|---|
| `PAPERLESS_URL` | `--url` | — (required) | Base URL of your Paperless-ngx instance |
| `PAPERLESS_TOKEN` | `--token` | — (required) | Paperless-ngx API token |
| `PAPERLESS_MCP_TRANSPORT` | `--transport` / `--stdio` / `--http` | `stdio` | `stdio` or `http` |
| `PAPERLESS_MCP_HOST` | `--host` | `127.0.0.1` | Bind address (http only; the image uses `0.0.0.0`) |
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

## Upgrading from 0.1.x

0.2.0 pins `pypaperless==6.0.0rc2` from PyPI (0.1.x tracked the library's git
`main`) and follows its API changes. What that means for callers:

- Task fields follow Paperless-ngx 3.0: `type` → `task_type`, `result` →
  `result_data`, `related_document` → `related_document_ids`. `task_file_name`
  is gone.
- Saved views no longer report `show_on_dashboard` / `show_in_sidebar`; they
  report `page_size`, `display_mode` and `display_fields` instead.
- List responses gained a `total` field, and `has_more` is now derived from
  the server-reported match count rather than from over-fetching.
- Taxonomy objects report `matching_algorithm` as a number plus a readable
  `matching_algorithm_name`.
- The default transport is now `stdio`, not HTTP. Pass `--http` (or set
  `PAPERLESS_MCP_TRANSPORT=http`) for the old behaviour; the Docker image does
  this for you.
- The default bind address is `127.0.0.1` instead of `0.0.0.0`. The Docker
  image sets `0.0.0.0`.
- `chat_with_documents` was removed (see above).

## Development

This repo ships a VS Code devcontainer based on
`mcr.microsoft.com/devcontainers/python:3-3.13`, with `uv`, `ruff`, pytest and
the docker CLI preinstalled. Open the project in VS Code and select
**"Reopen in Container"** — the dev venv lands at
`/home/vscode/.local/dev-venv` and dependencies are synced via `uv` on
container start.

```bash
# Manual setup (without the devcontainer):
uv sync --group dev
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run paperless-mcp --help
```

## License

MIT.
