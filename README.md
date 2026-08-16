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
> It also needs a Paperless-ngx 3.0 that is itself fresh.
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
  54 tools by default, 64 with deletes enabled.
- **Workflow prompts**: three slash commands — `triage_inbox`,
  `monthly_review`, `find_duplicates` — that chain the tools into the jobs an
  archive actually needs, with the Paperless-specific judgement calls written
  into them. They adapt to the visibility flags, so a read-only server hands
  back a proposal instead of walking the model into a write that is not there.
- **Saved views run, not just read**: `run_saved_view` executes the queries the
  user curated in the web UI — "Unpaid invoices", "Tax 2024" — by translating
  their stored filter rules into one document query server-side, in the view's
  own sort order. A rule the translation table does not know is refused rather
  than dropped, because a view answered with too many documents is worse than
  one left unanswered.
- **Names, not just IDs**: Paperless reports correspondents, tags, document
  types, storage paths and owners as bare numbers. The master data is read once
  per connection and cached, so every result carries `correspondent_name`,
  `tag_names`, `owner_name` and friends next to the IDs — including the label
  behind a `select` custom field — without a lookup call per document.
- **Server-side pagination** on every list-shaped tool: `offset`/`limit` are
  translated into Paperless page requests, so paging deep into a result set
  costs at most two HTTP calls and each response reports `total` and
  `has_more`. `limit` is capped at **100** everywhere, because the model is what
  picks the window and nothing else bounds how large a result gets: a search
  answering 100 documents already serializes to roughly 42k tokens, and 250 to
  about 105k — past what a client accepts as a single tool result, which it
  reports as a failure the model cannot read or recover from. A window over the
  cap is refused with `invalid_argument` naming the ceiling, rather than
  silently narrowed: `limit` is echoed back in every envelope and has to keep
  meaning the same thing there.
- **Results are measured, not estimated.** Three things decide what a window of
  documents costs, and all three were wrong by default. The MCP SDK builds *both*
  halves of a tool result from the same return value — a JSON text block and
  `structuredContent` — and the wire format carries both, so a response was paid
  for twice; the tools are registered as unstructured, which drops the duplicate.
  The SDK then indents the text block with two spaces, which nothing reads;
  results are serialized compact. And a list carried every field of every
  document, where `fields="compact"` now carries what a hit is judged on. A
  25-document search went from 42,124 characters to 9,338 — **−78 %**, or 10.5k
  tokens down to 2.3k — and a 100-document one from 42k tokens to 9.3k. What this
  gives up is the published `outputSchema`, which described a result the model had
  already received.
- **Structured errors**: pypaperless exceptions become results like
  `{"error": "not_found", "detail": "...", "cause": "..."}` instead of
  protocol-level failures, so the model can recover rather than give up. The `error`
  code is one of a closed set, so a client can branch on it:

  | Code | Means |
  |---|---|
  | `invalid_argument` | An argument was rejected before anything was written — a bad name, an impossible page selection, a malformed query. |
  | `not_found` | Paperless has no such object (HTTP 404), including one that was already deleted. |
  | `forbidden` | The Paperless user may not touch this resource. |
  | `auth_failed` | Paperless rejected the API token. |
  | `connection_error` | The server was unreachable, or the connection could not be initialized. |
  | `timeout` | Paperless did not answer within `PAPERLESS_MCP_TIMEOUT`. |
  | `missing_field` | A required field was not supplied when creating something. |
  | `draft_invalid` | Paperless refused the new object. |
  | `delete_failed` | Paperless refused the delete for a reason other than a missing object. |
  | `bulk_edit_failed` | Paperless rejected the bulk edit. |
  | `asn_failed` | No archive serial number could be assigned. |
  | `email_failed` | Paperless rejected the email request. |
  | `unsupported` | The operation does not exist on this Paperless, or the resource cannot be created through the API. |
  | `paperless_error` | Paperless answered with an error payload of its own; `cause` carries its message. |
  | `upstream_error` | Paperless answered with something unexpected: a non-JSON body, invalid JSON, or an unhandled status. |
  | `file_too_large` | The file exceeds `PAPERLESS_MCP_MAX_FILE_BYTES`; the result also reports `size_bytes` and `max_bytes`. |
  | `unsupported_media_type` | Paperless answered a thumbnail request with something that is not an image. |
  | `unsupported_filter_rule` | A saved view filters on rule types this server cannot translate, so running it would return more documents than the view selects. |

  An argument the JSON schema itself rejects — an enum value that does not exist — is
  the one failure that arrives as a protocol error instead, because pydantic validates
  before the tool body runs. The message names the allowed values.
- **Constrained arguments are published as enums**, not as bare strings: the
  allowed values for `order_by`, `data_type`, `file_version`, `object_type`,
  `matching_algorithm` and the task filters are in the tool's JSON schema, so a
  model reads them instead of guessing and paying a round trip. `matching_algorithm`
  takes names — `none`, `any`, `all`, `literal`, `regex`, `fuzzy`, `auto` — rather
  than the 0–6 the REST API uses. Every argument publishes its type and its values
  *inline* and with a single scalar `type`: no `$ref` into `$defs`, no `anyOf` around
  an optional one, no list of types. All three are valid JSON Schema that a client may
  render as an empty `{}`, which leaves the model guessing at an argument the server
  documented. Two consequences for callers: pass an optional argument by **omitting**
  it rather than sending `null`, and where a tool accepts more than one shape — only
  `custom_field_query` does — the schema advertises the first and the docstring
  describes the rest.
- **Behaviour hints on every tool**: each of the 64 tools ships MCP tool
  annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`,
  `openWorldHint`) plus a display title, so a client can wave a search through
  and stop to ask before a rotate, a merge or an `empty_trash`.
- **Survives an unreachable Paperless**: the connection is established lazily
  and retried per call, so the MCP handshake never fails just because the
  server was briefly down.
- **Thumbnails as real images**, viewable inline in the client.
- Built on **pypaperless 6.0.0** and the **MCP Python SDK 2.0** — requires
  **Paperless-ngx 3.0+**.

## Requirements

- **[uv](https://docs.astral.sh/uv/)** on the machine that runs the MCP client.
  It manages the virtualenv *and* the Python toolchain — no system Python 3.13
  needed, `uv` fetches one if it has to.
- **Paperless-ngx 3.0+** with an API token
  (**Settings → API tokens**, or `/api/token/`).

## Installation — uv

[`paperless-mcp` is on PyPI](https://pypi.org/project/paperless-mcp/), so `uv`
can install it without a clone. Every dependency is a stable release, so no
resolver flags are needed.

### Install it as a tool (recommended)

```bash
uv tool install paperless-mcp
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
uvx --from paperless-mcp paperless-mcp --help
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

Or let the client drive `uvx`, with the invocation moved into `args`:

```json
{
  "mcpServers": {
    "paperless": {
      "command": "/absolute/path/to/uvx",
      "args": ["--from", "paperless-mcp", "paperless-mcp"],
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

- **`module 'httpx' has no attribute 'AsyncClient'`** — installed with
  `--prerelease=allow`, which lets uv pick an httpx dev build. No prerelease
  flag is needed any more; drop it. Releases after 0.0.1 cap httpx below 1.0
  themselves.
- **"Server disconnected" right after startup** — almost always the `command`.
  Use an absolute path; see above.
- **"No such file or directory" / the server starts but nothing works** — for
  the clone setup, the `--directory` path does not point at the clone (it needs
  the directory containing `pyproject.toml`).
- **Tools appear but every call returns `connection_error`** — `PAPERLESS_URL`
  is wrong or unreachable from the machine running the client. The server
  starts anyway by design; the error text names the cause.
- **`auth_failed`** — the API token is wrong, or belongs to a deactivated user.
- **`cannot start on <host>:<port>: [Errno 98] Address already in use`** — with
  `--transport http`, something already listens there. Pick another `--port`, or
  stop the other process. Exit code 1; a configuration error is exit code 2.
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
| `PAPERLESS_MCP_NAME_CACHE_TTL` | `--name-cache-ttl` | `300` | Lifetime of the ID→name snapshot, seconds (`0`: forever) |
| `PAPERLESS_MCP_LOG_LEVEL` | `--log-level` | `INFO` | Verbosity (always logged to stderr) |

`get_paperless_info` reports the ones that change what a call does —
`readonly`, `deletes_enabled`, `max_file_bytes`, `name_cache_ttl` and
`request_timeout` — so a client can read the deployment's limits instead of
discovering them by hitting one.

### Tool visibility matrix

| Mode | Read tools | Write tools | Delete tools |
|---|---|---|---|
| `READONLY=true` | ✅ | ❌ | ❌ |
| Default | ✅ | ✅ | ❌ |
| `ENABLE_DELETE=true` | ✅ | ✅ | ✅ |

`READONLY=true` always wins, even if `ENABLE_DELETE=true`.

## Tools

**Read**: `search_documents`, `search_everywhere`, `get_document`, `get_document_content`,
`get_document_metadata`, `get_document_notes`, `get_document_history`,
`find_similar_documents`, `download_document`, `get_document_thumbnail`,
`get_next_asn`,
`list_tags`, `list_correspondents`, `list_document_types`,
`list_storage_paths`, `list_custom_fields`, `list_share_links`,
`list_saved_views`, `get_saved_view`, `run_saved_view`, `list_trash`,
`list_active_tasks`, `list_tasks`, `get_task`, `get_statistics`,
`get_system_status`, `get_paperless_info`, `search_autocomplete`,
`get_document_suggestions`,
`get_document_ai_suggestions`.

**Write** (default-on, suppressed by `READONLY`): `upload_document`,
`update_document`, `add_document_note`, `bulk_edit_documents`,
`bulk_reprocess_documents`, `bulk_merge_documents`, `bulk_rotate_documents`,
`split_document`, `delete_document_pages`,
`acknowledge_tasks`, `create_tag`, `update_tag`, `create_correspondent`,
`update_correspondent`, `create_document_type`, `update_document_type`,
`create_storage_path`, `update_storage_path`, `create_custom_field`,
`update_custom_field`, `set_document_custom_field`,
`remove_document_custom_field`, `create_share_link`, `restore_documents`.

**Delete** (requires `ENABLE_DELETE=true`): `delete_document`,
`delete_document_note`, `delete_tag`, `delete_correspondent`,
`delete_document_type`, `delete_storage_path`, `delete_custom_field`,
`delete_share_link`, `bulk_delete_objects`, `empty_trash`.

Notes on semantics:

- **Relations take a name or an ID.** Correspondent, document type, storage
  path and tags can be given as `document_type_name: "Kündigung"` instead of
  `document_type_id: 11`, and as `tag_names` instead of `tag_ids`, on
  `search_documents`, `update_document`, `upload_document`,
  `bulk_edit_documents` and `bulk_delete_objects`. A tag's parent likewise:
  `create_tag` and `update_tag` take `parent_name` beside `parent_id`, which is
  the name `format_tag` reports back. Names are what every result
  already reports back, and
  what a human reading the call along can veto — a wrong ID hits another valid
  object and relabels a document without an error anywhere. Matching is exact,
  then case-insensitive, never fuzzy: an archive holding `MR-ST 1337` next to
  `MR-ST 1337_2` leaves no room to guess, and an ambiguous name is refused with
  both candidates. An unknown name is an error listing the near misses, never a
  newly created tag — but it costs one snapshot reload first, so master data
  added elsewhere a moment ago still resolves. Passing both halves is allowed
  and they must agree; a mismatch is rejected rather than silently resolved.
- `search_documents` combines a Whoosh full-text `query` with Django-style
  filters, and takes `order_by` / `descending`.
- Document lists — `search_documents`, `find_similar_documents`,
  `run_saved_view`, `list_trash` — take `fields`. The default `compact` carries
  the ID, title, correspondent, document type, tags, the dates and the page
  count; `fields="full"` adds the storage path, the owner, both file names, the
  MIME type and `modified`, and costs roughly three times as much per document.
  `get_document` answers the full set for the one hit that turns out to matter,
  which is usually the cheaper route.
- `search_everywhere` is the other half: one global-search call that answers
  "what is this called in Paperless?" across documents, tags, correspondents,
  document types, storage paths, custom fields and saved views, so a name
  becomes an ID without paging three `list_*` tools. It cannot filter, sort or
  page — `limit` caps each category separately and `truncated` says whether it
  bit. Users, groups, mail rules and workflows are left out on purpose; they
  are admin-tier resources this server does not carry.
- `search_autocomplete` completes a partial word against the full-text index.
  It answers with vocabulary that actually occurs in the scans — whether an
  archive spells it "Rechnung" or "Rechnungen" — not with field names or query
  syntax, which live in the `search_documents` description.
- `split_document` requires `page_groups` to cover every page exactly once.
  Paperless keeps only the pages it is handed and discards the rest silently,
  so a gap is refused rather than acted on; use `delete_document_pages` to drop
  pages on purpose. The results inherit the source metadata unchanged, without
  a "(split 1)" title suffix.
- `delete_document_pages` is neither idempotent nor atomic. The survivors are
  renumbered, so repeating the same call removes different sheets; and the
  pages to keep are computed from a page count read in an earlier request, so a
  document that gains a version in between loses the wrong ones. Both tools
  take an optional `page_count` that skips that read — `get_document` reports
  it — though passing it does not close the window.
- `get_next_asn` reports the next free archive serial number, the one written
  on the paper original before filing. It is only free until something claims
  it: fetch it immediately before the upload or update that uses it. Two calls
  in a row return the same number, not two.
- `get_system_status` needs the `view_system_monitoring` permission or a staff
  account, and answers `{"error": "forbidden"}` without it. `health` rolls the
  six subsystems (database, Redis, Celery, index, classifier, sanity check) up
  into one verdict and `problems` lists only those that are not OK.
- `search_documents` also takes `custom_field_query`, the only filter that
  reaches custom field *values*. It is Paperless' JSON expression — an atom
  `[field, operator, value]` where `field` is a custom field's name or ID, and
  `["AND", [expr, ...]]` / `["OR", [expr, ...]]` / `["NOT", expr]` around
  others — accepted either as JSON text or as the structure itself:

  ```json
  ["AND", [["Due", "range", ["2024-08-01", "2024-08-31"]], ["Paid", "exact", false]]]
  ```

  Which operators an atom may use depends on the field's `data_type`: `exact`,
  `in`, `isnull` and `exists` on any type; `icontains` / `istartswith` /
  `iendswith` on `string`, `longtext`, `url` and `monetary`; `gt` / `gte` /
  `lt` / `lte` / `range` on `date`, `integer`, `float` and `monetary`;
  `contains` on `documentlink`. A `date` field also takes a component in front
  of the operator, so `["Due", "month__exact", 8]` matches every August. The
  expression is checked against the cached field definitions before the
  request goes out, because Paperless answers a bad one with a validation
  payload keyed by position rather than by name.
- `update_document` **replaces** the tag list and accepts a `clear_fields`
  list (`correspondent`, `document_type`, `storage_path`,
  `archive_serial_number`) to unset foreign keys. Setting and clearing the
  same field in one call is rejected — including when the value came in as a
  name. Use `bulk_edit_documents` to add or remove individual tags.
- `upload_document` queues a file for consumption and returns a task UUID to
  poll with `get_task`. Pass `poll=true` to have the server do the waiting
  instead: the call then blocks until the consumer is done and answers with the
  new `document_id`, the terminal `status` and the full `task` record — one
  call instead of an upload plus a polling loop the model has to drive itself.
  The wait ends after `poll_timeout_seconds` (default 30, maximum 300; raise it
  for OCR-heavy scans) and running out is not an error: the result carries
  `timed_out: true` alongside the `task_uuid` to keep polling with. A `failure`
  status most often means Paperless rejected the file as a duplicate, and
  `task.result_data` says so. Keep the timeout under the MCP client's own
  request timeout — a call the client gives up on still consumes the file, it
  just leaves nobody to read the result.
- `create_tag` and its four siblings answer with the object as Paperless stored
  it — the same projection `list_tags` returns, under the resource's name. Two ids
  and a name could not confirm that a colour, a path or a matching algorithm
  arrived as intended, so confirming meant a second call; the create makes that
  call itself, once.
- `download_document` echoes `requested_version` (`archive` or `original`).
  Paperless serves the original when a document has no archive version and says
  so nowhere in the response, so without the echo two downloads of the same
  document are indistinguishable — which is precisely what comparing checksums
  needs to know.
- `set_document_custom_field` **upserts** one field value on one document: a
  field the document does not carry yet is added, an existing one is replaced.
  The value is checked against the field's `data_type` first, so `1` is not
  quietly stored as `true` and `1.0` is rejected instead of rounded. Setting
  the value a field already holds writes nothing and reports `changed: false`.
  Two things to know before a `documentlink` write: the list of IDs **replaces**
  the stored one (to add a link, read the current list from `get_document` and
  send it back with the new ID appended), and Paperless maintains the reverse
  link itself, so linking A to B makes B show A — never set both directions.
- `bulk_delete_objects` deletes tags, correspondents, document types or storage
  paths in one call, and takes its selection **either** as `object_ids` /
  `object_names` **or** as a filter — `name_contains`, `name_startswith`,
  `name_endswith`, `name_exact`, plus `is_root` for tags and `path_contains` for
  storage paths. The filter form is what API v10 added `all` and `filters` for:
  clearing out 400 stale correspondents travels as one lookup instead of 400 IDs
  that would have to be paged out of `list_correspondents` first. The two forms
  cannot be combined, because Paperless stops looking at `objects` as soon as
  `all` is set — a call meaning to intersect them would silently delete by
  filter alone. A filter no FilterSet behind the endpoint knows is refused for
  the same reason: django-filter drops an unrecognized lookup, and a dropped
  lookup widens the selection to everything. `deleted` reports how many objects
  the selection covered and `object_ids` lists them — for a filtered call too,
  read back immediately before the delete, since the endpoint itself answers with
  a bare `OK` and leaves no trash to inspect afterwards; for `tags` both
  understate, as Paperless also removes the matched tags' descendants. The
  endpoint has no branch for custom fields — `delete_custom_field` stays
  one-at-a-time — and its other operation, `set_permissions`, is not exposed:
  this server carries no users or groups to name in one.
- `remove_document_custom_field` clears the value on one document; the field
  definition and its values elsewhere are untouched, which is what
  `delete_custom_field` would destroy instead. A field that is not set is not
  an error: the call reports `removed: false` and changes nothing.
- Both write the document's custom fields as one array, because that is the
  only thing the API accepts — a value another client stored between the read
  and the write is lost.

### Tool annotations

Every tool carries MCP annotations, so a client can decide how much ceremony a
call deserves without parsing the description:

- `readOnlyHint` is true for all 30 read tools, and only for those.
  `destructiveHint` / `idempotentHint` are left unset there — the spec only
  gives them meaning once a tool can write.
- `destructiveHint` is true for 24 tools — every `update_*`, all ten delete
  tools (`delete_*`, `bulk_delete_objects` and `empty_trash`), the six that
  rewrite a document or its pages (`bulk_edit_documents`,
  `bulk_reprocess_documents`, `bulk_merge_documents`, `bulk_rotate_documents`,
  `split_document`, `delete_document_pages`), and the two
  custom field value tools (`set_document_custom_field` replaces a value,
  `remove_document_custom_field` clears one). It is false for the additive ones
  (`upload_document`, `create_*`, `add_document_note`, `restore_documents`,
  `acknowledge_tasks`).
- `idempotentHint` is true only where repeating the identical call converges on
  the same state. It is false wherever a call accumulates:
  `bulk_rotate_documents` (twice by 90° is 180°), `bulk_merge_documents` and
  `upload_document` (each call mints another document),
  `bulk_reprocess_documents` (each call queues another task), and every
  `create_*` plus `add_document_note` (each call adds another row).
- `openWorldHint` is false everywhere: the tools reach exactly one configured
  Paperless instance, not an open-ended set of external entities.

The vocabulary has no axis for *reversible* versus *final*, so tools that differ
a lot end up with identical hints: `delete_document` moves a document to the
trash and `restore_documents` brings it back, while `empty_trash` cannot be
undone and purges the entire trash when called without arguments, and
`bulk_delete_objects` can retire a whole branch of the taxonomy from a single
filter. A client that wants to hold the final ones back harder has to go by the
name or the description.

They are hints, not a permission system — the actual gate is
`PAPERLESS_MCP_READONLY` / `PAPERLESS_MCP_ENABLE_DELETE`, which decide whether a
tool is advertised at all.

## Prompts

Tools are verbs; a job is a sequence of them plus the judgement about what to do
when they disagree. That sequence is what the prompts hold. In Claude Desktop
they show up as slash commands, and they are the user's to start — the model
cannot invoke one on its own.

| Prompt | Arguments | What it does |
|---|---|---|
| `triage_inbox` | `limit` (default 10) | Works through the inbox document by document: reads it, then cross-checks `get_document_suggestions` (the trained classifier) against `get_document_ai_suggestions` (the LLM, when the instance has AI enabled) against `find_similar_documents` (how the archive already files this sender), and settles on correspondent, type, storage path, tags, title and date. |
| `monthly_review` | `month` (`YYYY-MM`, defaults to last month) | Closes out a month: what was *dated* in the window versus what *arrived* in it, then the things that slipped — still in the inbox, untagged, no correspondent, consumption failed — and the recurring senders that went silent compared with the month before. |
| `find_duplicates` | `query`, `limit` (default 25) | Hunts the duplicates that survive a Paperless import: not byte-identical re-uploads (those are refused at the door) but the same paper scanned twice. Runs candidates through the similarity index, confirms each pair against `get_document_metadata` checksums and sizes, and rules out the look-alikes — last month's statement, last year's form. |

Two things they do that a hand-written prompt would not:

- **The date arithmetic is done before the model sees it.** `monthly_review`
  renders with the window already resolved — `2024-02-01` to `2024-02-29`,
  plus the preceding month to compare against — so leap days and year
  boundaries are settled facts rather than something to reason about.
- **They respect the visibility flags.** With `PAPERLESS_MCP_READONLY=true`,
  `triage_inbox` asks for a filing proposal instead of describing
  `bulk_edit_documents`; with deletes off, `find_duplicates` tags the losing
  copy for a human rather than reaching for `delete_document`. A plan that ends
  in a tool the server never advertised is a plan that gets followed until it
  fails.

Rendering a prompt never touches Paperless: it is a plan, built from its
arguments and the server's configuration, so a slash command cannot fail
because the archive was briefly unreachable.

## Running over HTTP / in Docker

The server also speaks Streamable HTTP at `/mcp`, guarded by a bearer token,
and the repo ships a `Dockerfile` and a `docker-compose.yml` for exactly that.
Documentation for this path is coming — start with the uv setup above; it is
the supported route for now.

## Out of scope (for now)

- **Workflows, mail accounts/rules, users, groups, config**: admin-tier
  concerns where letting an LLM make autonomous changes is rarely the right
  answer.
- **The document chat endpoint** (`/api/documents/chat/`): it streams plain
  text, and pypaperless 6.0's `DocumentChat` model carries only the echoed
  query, so no answer ever reaches the caller. A tool for it would fail every
  time; it will come back if the library starts returning the response body.

## Development

This repo ships a VS Code devcontainer based on
`mcr.microsoft.com/devcontainers/python:3-3.13`, with `uv`, `ruff` and pytest
preinstalled. Open the project in VS Code and select **"Reopen in Container"**
— the dev venv lands at `/home/vscode/.local/dev-venv` and dependencies are
synced via `uv` on container start. Nothing else runs inside it: no Docker
daemon, no Paperless. Point `PAPERLESS_URL` in your `.env` at an instance
running elsewhere — from inside the container the host is `172.17.0.1`.

```bash
script/bootstrap             # uv sync --group dev
uv run pytest                # suite + coverage (gate: 80 %)
uv run ruff check --fix .    # lint
uv run ruff format .         # format
uv run mypy                  # strict, on the paperless_mcp package
prek run --all-files         # everything CI lints, in one go
uv run paperless-mcp --help
```

The same commands are wired up as VS Code tasks (`test: pytest`,
`lint: ruff check`, `validate: full suite` as the default build task), and
three debug configurations are ready in the Run view:

- **paperless-mcp debug script** runs [`run/debug.py`](run/debug.py), a scratch
  file that builds the real server, opens one session and calls tools directly
  against the Paperless in your `.env`. This is the one to reach for when you
  want a breakpoint inside a tool without an MCP client in the loop.
- **paperless-mcp (http)** serves Streamable HTTP on port 8000 (forwarded out
  of the container), for driving the server from a real client or MCP Inspector.
- **paperless-mcp (stdio)** starts the transport a client would spawn — useful
  for checking startup and configuration errors, less so for interactive work.

[AGENTS.md](AGENTS.md) documents the module layout and the conventions that
hold this together — the tool surface as public API, why tools never raise, why
list tools must paginate. Worth reading before adding a tool, whether you are
human or not.

## License

[MIT](LICENSE.md).
