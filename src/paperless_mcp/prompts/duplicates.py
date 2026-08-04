"""The duplicate-hunting workflow."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from ..config import Settings
from ._helpers import capability_note, sections

_INTRO = """\
Hunt for duplicate documents in the Paperless-ngx archive.

Paperless already refuses a byte-identical re-upload, so the duplicates that survive in a real
archive are re-scans: the same piece of paper consumed twice, with a different checksum, slightly
different OCR and usually a different title. That is why searching for identical titles finds
nothing, and why this has to go through the similarity index."""

_CANDIDATES_QUERY = """\
Candidates: `search_documents(query="{query}", limit={limit})`. Whoosh syntax, so narrow it
further with `AND`/`OR` if the first pass is too broad."""

_CANDIDATES_RECENT = """\
Candidates: `search_documents(order_by="added", descending=true, limit={limit})` — the newest
arrivals, where an accidental second scan is most likely. To hunt somewhere else instead, re-run
this prompt with a `query`, or start from one suspicious correspondent."""

_COMPARE = """\
For each candidate C, in this order:

1. `find_similar_documents(C, limit=5)` — Paperless' "more like this" over the full-text index.
   Near-identical text ranks first. Skip C itself in the result.
2. Triage each neighbour N on what the result already carries, before spending another call: the
   same `correspondent`, the same `created` date and the same `page_count` is already a strong
   case; a different correspondent, or a year between them, almost never is.
3. Confirm before claiming it. `get_document_metadata` on both:
   - equal `original_checksum` → the same file, conclusively. Rare, but it happens after a restore
     or an import that bypassed the consumer.
   - different checksums but the same `original_size` and page count → very likely the same paper
     scanned twice.
   - still unsure → `get_document_content` on both. A re-scan differs only in OCR noise; two
     genuinely different documents differ in the numbers.
4. Rule out the look-alikes, because they are the majority: consecutive monthly statements from
   one sender, the same form for a different year, a contract and its amendment, a document and
   the cover letter that came with it. A differing invoice number, period, amount or meter reading
   means it is not a duplicate — however similar the rest of the text is."""

_KEEP = """\
When a pair is confirmed, pick the copy that stays, in this order: more pages, then the one that
has an archived (OCR'd) version, then the one holding the archive serial number, then the one
carrying notes or custom fields, then the earlier `added` date. The ASN outranks almost
everything — it is the link to a physical file, and Paperless will not let the same number be
assigned twice."""

_TRASH = """\
Then remove the other copy with `delete_document(id)`. That moves it to the trash, where
`list_trash` still shows it and `restore_documents` brings it back; it is gone only once the trash
is emptied, which is the one reason this step is safe to take unattended. Never delete a pair you
did not confirm in step 3."""

_TAG_INSTEAD = """\
Do not try to remove the other copy — this deployment registers no delete tools. Tag it instead:
`create_tag(name="duplicate")` once, then `bulk_edit_documents(document_ids=[...],
add_tag_names=["duplicate"])` for every confirmed duplicate. The pairs then survive this
conversation and a human can clear them out in one filtered view."""

_READONLY = """\
This deployment is read-only, so the finding *is* the deliverable. Make it precise enough to act
on without anyone having to repeat the investigation."""

_REPORT = """\
Report one row per confirmed pair: keep-ID, duplicate-ID, the evidence that settled it (equal
checksum / equal size and page count / identical content) and your confidence. List the pairs you
examined and rejected separately, one line each on why — that is what keeps the next run from
walking the same false positives all over again."""


def _cleanup(settings: Settings) -> str:
    """Return the removal step this deployment can actually carry out."""
    if settings.expose_deletes:
        return _TRASH
    return _READONLY if settings.readonly else _TAG_INSTEAD


def register(mcp: MCPServer, settings: Settings) -> None:
    """Register the duplicate-hunting prompt."""

    @mcp.prompt(title="Find duplicate documents")
    async def find_duplicates(query: str | None = None, limit: int = 25) -> str:
        """Find documents that are the same paper filed twice.

        Walks candidates through the similarity index, then confirms each pair
        against the file metadata before calling it a duplicate — and rules out
        the look-alikes (monthly statements, last year's form) that make a naive
        text match useless. ``query`` narrows the hunt; without one it starts
        from the most recent arrivals.
        """
        candidates = (
            _CANDIDATES_QUERY.format(query=query, limit=limit)
            if query
            else _CANDIDATES_RECENT.format(limit=limit)
        )
        return sections(
            _INTRO,
            capability_note(settings),
            candidates,
            _COMPARE,
            _KEEP,
            _cleanup(settings),
            _REPORT,
        )
