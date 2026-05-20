# paperless-mcp

A [Model Context Protocol][mcp] server for [Paperless-ngx][pngx], powered by
[pypaperless][pyp] v6. Runs as a Docker service and exposes its tools over
HTTP (Streamable HTTP transport), so any MCP-aware client on your network can
search, read, ingest, and curate your documents through an LLM.

[mcp]: https://modelcontextprotocol.io/
[pngx]: https://github.com/paperless-ngx/paperless-ngx
[pyp]: https://github.com/tb1337/paperless-api

## Features

- **Network-exposed** MCP server via `streamable-http` (`/mcp` endpoint).
- **Bearer-token auth** for the MCP endpoint (recommended; can be disabled
  for trusted networks).
- **Tunable surface**: writes can be disabled (`PAPERLESS_MCP_READONLY=true`)
  and deletes require explicit opt-in (`PAPERLESS_MCP_ENABLE_DELETE=true`).
- **~50 tools** covering documents, taxonomy (tags, correspondents, document
  types, storage paths, custom fields), bulk operations, trash, tasks,
  statistics, saved views, share links, classifier suggestions, AI suggestions
  and the document chat endpoint.
- Built on **pypaperless v6** (main) — supports Paperless-ngx 3.0+.

## Quick start (Docker Compose)

```bash
cp .env.example .env
# edit .env: PAPERLESS_URL, PAPERLESS_TOKEN, PAPERLESS_MCP_AUTH_TOKEN
docker compose up -d --build
```

The server listens on `http://<host>:8000/mcp`. Point any MCP client at it:

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

## Configuration

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `PAPERLESS_URL` | ✅ | — | Base URL of your Paperless-ngx instance |
| `PAPERLESS_TOKEN` | ✅ | — | Paperless-ngx API token |
| `PAPERLESS_MCP_AUTH_TOKEN` | recommended | _empty_ | Bearer token required on the MCP endpoint |
| `PAPERLESS_MCP_HOST` | | `0.0.0.0` | Bind address |
| `PAPERLESS_MCP_PORT` | | `8000` | Bind port |
| `PAPERLESS_MCP_READONLY` | | `false` | If true: hide every write/delete tool |
| `PAPERLESS_MCP_ENABLE_DELETE` | | `false` | If true: expose delete tools |
| `PAPERLESS_MCP_MAX_FILE_BYTES` | | `25000000` | Max size for base64 file returns |

### Tool visibility matrix

| Mode | Read tools | Write tools | Delete tools |
|---|---|---|---|
| `READONLY=true` | ✅ | ❌ | ❌ |
| Default | ✅ | ✅ | ❌ |
| `ENABLE_DELETE=true` | ✅ | ✅ | ✅ |

`READONLY=true` always wins, even if `ENABLE_DELETE=true`.

## Tools (overview)

**Read**: `search_documents`, `get_document`, `get_document_content`,
`get_document_metadata`, `get_document_notes`, `get_document_history`,
`find_similar_documents`, `download_document`, `get_document_thumbnail`,
`list_tags`, `list_correspondents`, `list_document_types`,
`list_storage_paths`, `list_custom_fields`, `list_share_links`,
`list_saved_views`, `run_saved_view`, `list_trash`, `list_active_tasks`,
`get_task`, `get_statistics`, `get_paperless_info`,
`get_document_suggestions`, `get_document_ai_suggestions`,
`chat_with_documents`.

**Write** (default-on, suppressed by `READONLY`): `upload_document`,
`update_document`, `add_document_note`, `bulk_edit_documents`,
`bulk_reprocess_documents`, `bulk_merge_documents`, `create_tag`,
`update_tag`, `create_correspondent`, `update_correspondent`,
`create_document_type`, `update_document_type`, `create_storage_path`,
`update_storage_path`, `create_custom_field`, `update_custom_field`,
`create_share_link`, `restore_documents`.

**Delete** (requires `ENABLE_DELETE=true`): `delete_document`,
`delete_document_note`, `delete_tag`, `delete_correspondent`,
`delete_document_type`, `delete_storage_path`, `delete_custom_field`,
`delete_share_link`, `empty_trash`.

## Out of scope (for now)

Workflows, mail accounts, mail rules, users and groups are deliberately not
exposed. They're admin-tier concerns where letting an LLM make autonomous
changes is rarely the right answer.

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
uv run paperless-mcp     # needs PAPERLESS_URL / PAPERLESS_TOKEN
```

## License

MIT.
