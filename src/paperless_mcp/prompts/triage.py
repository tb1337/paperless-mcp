"""The inbox triage workflow.

The blocks holding a literal ``{`` — the ones quoting an error result — are
never passed through :meth:`str.format`, so they need no brace escaping.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from ..config import Settings
from ._helpers import capability_note, sections

_INTRO = """\
Work through the Paperless-ngx inbox and file what is in it. Oldest arrival first, at most
{limit} documents in this pass, and finish one document before starting the next."""

_SETUP = """\
Step 0 — set up once, before touching a document.

- `list_tags(limit=200)`: find the tag whose `is_inbox_tag` is true and remember its ID. Removing
  that tag is what marks a document as processed — Paperless has no other "done" flag.
- `list_correspondents(limit=200)`, `list_document_types(limit=200)`,
  `list_storage_paths(limit=200)`: the vocabulary you may assign. Prefer an existing entry over a
  new one — near-duplicate master data ("Stadtwerke" next to "Stadtwerke München") is the main way
  an archive rots, and it stays invisible until someone searches for the wrong one.
- `search_documents(is_in_inbox=true, order_by="added", limit={limit})`: your work list. If it
  comes back empty, the inbox is clear — say so and stop."""

_EVIDENCE = """\
Step 1 — per document, collect evidence before deciding anything.

- `get_document(id)`: the fields plus a 500-character preview of the OCR text. Usually enough.
  Reach for `get_document_content` only when the preview does not reveal the sender, the date or
  what the document actually is — a full scan is mostly boilerplate you pay for in context.
- `get_document_suggestions(id)`: the classifier Paperless trained on your own archive. Cheap, and
  structurally unable to invent anything — it can only propose objects that already exist.
- `get_document_ai_suggestions(id)`: the LLM suggestions of the Paperless instance itself. Richer,
  and the only source that can propose a *new* correspondent or tag, in the `suggested_*` lists.
  Optional: when it answers `{"error": ...}` because AI is not enabled server-side, drop it and
  carry on with the classifier — do not retry it on every following document.
- `find_similar_documents(id, limit=5)`: the strongest of the three, and the one no classifier can
  give you. If last year's letter from the same sender is already in the archive, the way *it* is
  filed is the answer. Consistency with what is already there beats a marginally better label."""

_DECIDE = """\
Step 2 — decide, per document: correspondent, document type, storage path, tags, title, date.

- Where the sources agree, that is the answer. Do not second-guess it.
- Where they disagree, the order of trust is: an existing sibling from `find_similar_documents`,
  then the classifier, then the AI suggestion, then your own reading of the text.
- A title is a filing label, not the heading the OCR happened to pick up. "<sender> — <what it is>
  <period>" reads well in a list and sorts sensibly: "Stadtwerke München — Jahresabrechnung 2025".
- `created` is the date printed *on* the document, not the day it was scanned. Correct it when the
  two clearly differ; every date-ranged search afterwards depends on it.
- Tags are what you will search for later — a topic, a project, a tax year. Do not mirror the
  document type as a tag; that information is already on the document.
- When the evidence genuinely does not identify a document, leave it in the inbox and list it as
  unresolved together with the one question that would settle it. A confident wrong filing costs
  far more than an unfiled document, because nobody ever goes looking for it again."""

_APPLY = """\
Step 3 — apply the decisions.

- Group first: everything that gets the same correspondent, type or storage path goes into one
  `bulk_edit_documents(document_ids=[...], ...)` call. Keep `update_document` for what is
  genuinely per-document — the title and the date.
- Individual tags are added and removed with
  `bulk_edit_documents(add_tag_names=[...], remove_tag_names=[...])`.
  `update_document(tag_names=...)` **replaces** the whole list, so it silently drops every tag you
  did not repeat.
- Remove the inbox tag last, in its own call, and only from the documents you actually resolved.
  Doing it earlier means a failure halfway through leaves a half-filed document that no longer
  shows up in the inbox for anyone to notice.
- Do not create master data as a side effect of triage. When a correspondent or a tag is missing,
  say so in the report and ask — `create_tag` and `create_correspondent` exist, but a vocabulary
  that grows one document at a time is how an archive ends up with four spellings of one sender.
- Tools answer with a result, never an exception. If one comes back `{"error": ...}`, read it, fix
  the argument and retry that one document rather than abandoning the pass."""

_PROPOSE = """\
Step 3 — report instead of applying.

This deployment registers no write tools, so the outcome of the pass is a proposal a human
executes. Give, per document, the ID, the current title and the proposed correspondent, document
type, storage path, tags, title and date — precise enough that applying it is mechanical."""

_REPORT = """\
Step 4 — close the pass. One line per document (ID, new title, correspondent, type, tags, what
changed), then the unresolved ones with the question that would settle each, then how many
documents are still waiting in the inbox."""


def register(mcp: MCPServer, settings: Settings) -> None:
    """Register the inbox triage prompt."""

    @mcp.prompt(title="Triage the inbox")
    async def triage_inbox(limit: int = 10) -> str:
        """Work through the Paperless inbox and file what is in it.

        Reads each waiting document, cross-checks the trained classifier against
        the AI suggestions and against the documents that are already filed like
        it, then settles on correspondent, type, storage path, tags, title and
        date. ``limit`` caps how many documents one pass handles.
        """
        return sections(
            _INTRO.format(limit=limit),
            capability_note(settings),
            _SETUP.format(limit=limit),
            _EVIDENCE,
            _DECIDE,
            _APPLY if settings.expose_writes else _PROPOSE,
            _REPORT,
        )
