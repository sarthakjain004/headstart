# ADR-0074: An empty Query browses; every result set paginates

- Status: Accepted
- Date: 2026-08-20
- Relates to: [ADR-0042](0042-signed-in-ui-saved-sets.md) (`search.py`'s own citation for
  `JobSearch` as the one serving path both UIs run — this decision lives entirely inside it),
  [ADR-0031](0031-first-seen-index-stamp.md) (the `first_seen` field this reuses as the browse
  ranking), ADR-0005 (the embedding/ranking conventions this does not change for a real Query)

## Context

Opening the Search tab with nothing typed showed a static "describe the role" message —
`JobSearch.run` returned `[]` for an empty `q` before ever touching the encoder or the table
(`search.py`), and the frontend's `showIntro()` rendered the same placeholder both on page
load and on every empty query. A user had to already know what to type before seeing a single
Job. Separately, a search returned one shot of up to `k` rows (default 20, hard-capped at
`max_k=100`) with no way to see anything past that cut — "best 20" or "best 50," never more.

Two changes were asked for together: browse the newest Jobs when there is no Query, and let
both a browse and a real search page through up to 20 pages of 20 results (400 addressable
Jobs) instead of one capped shot.

## Decision

**One seam, not two.** `JobSearch.run` stays the single method both the production Space and
the local dev server call (ADR-0042's whole point). An empty `q` does not become a second
public entry point — it is a branch inside `run`: rank by `first_seen` instead of encoding a
query and ranking by cosine similarity. Every Search filter and the new pagination apply
identically either way; only the ranking criterion changes. A caller (a route, a test, the
frontend) never has to decide which method to call — the module decides internally.

**Pagination lives inside `JobSearch`, not a wrapper around it.** There are two real callers
of this module already (the Space route, the local dev route) — genuinely duplicating "how do
you page a brute-force LanceDB scan with no ANN index" across both would be exactly the
Shotgun Surgery this module exists to avoid. A new `page` parameter (1-indexed, default 1)
clamps into `[1, max_page]` the same way `k` already clamps into `[1, max_k]`; the query
becomes `.limit(k).offset((page-1)*k)`.

**No response-envelope change.** `/search` still returns a bare JSON array. Checked who else
calls it: `headstart.alerts.space_query` (the email/Telegram Digest generator) always requests
`k=100` and never sends `page`, expecting a plain list back (`json.load()`, typed
`list[dict]`). Defaulting `page=1` when absent makes that caller's behavior identical to
before this ADR — verified, not assumed. "Is there a next page" is inferred by the frontend
from a short page (fewer than `k` rows came back) rather than a total-count field, so no
second `count_rows` query is needed either.

**The tiebreak that made this correct, not just fast** (found by testing against a real
table, not by reasoning about the schema): `first_seen` is stamped once per sync batch
(ADR-0031), so thousands of rows tie on the exact same timestamp. Ordering by `first_seen
DESC` alone and paginating with `offset` **silently duplicates rows across pages and drops
others** — measured against `data/lancedb/jobs.lance` on 2026-08-20: page 1 and page 2 of a
5-row browse shared 2 rows with no tiebreak, shared zero with `id ASC` added as a second sort
key. `id` is unique per row, so it makes the ordering — and therefore the pagination —
deterministic.

**The same tiebreak must never be added to a real search.** Also measured directly: passing
an explicit `order_by` alongside a vector query does not merely fail to help, it overrides
ranking by similarity entirely — a page of "results" sorted by `id`, `_distance` ignored. The
two branches in `run` are asymmetric on purpose: browse always sets an explicit order, search
never does, and that difference is a correctness requirement, not a style choice.

**Plain dicts, not `lancedb.query.ColumnOrdering` instances.** LanceDB's pydantic layer
coerces `[{"column_name": ..., "ascending": ..., "nulls_first": ...}]` the same as it does the
real class (verified 2026-08-20). Using dicts means `search.py` never imports `lancedb` at
all, keeping it importable without the package installed — the CI quality job's `.[dev]`
extra omits it; only `.[embed]` carries it — the same property `load_encoder`'s lazy
`torch`/`sentence-transformers` imports already protect.

## Rejected alternatives

- **A separate `browse()` method (or a separate module) for the no-query case.** Grows the
  public interface for a distinction callers shouldn't have to make — every present and future
  adapter would need its own `if not q: browse() else: run()` branch, duplicating the decision
  at every call site instead of making it once, inside the module.
- **A response envelope (`{items, total, page, total_pages}`).** Would have required updating
  every `/search` caller, including the alerts module, for a total-count nobody asked for and
  the frontend does not need — a short page already tells it there's nothing more.
- **A second LanceDB query to compute total count for a real "N pages" indicator.** Rejected
  for the same reason as the envelope: nothing downstream needs the exact total, and it would
  cost a full predicate-only scan on every request.
- **Sorting by `posted_at` for "latest jobs."** It's the employer's own field — inconsistent
  format across ATSes, null on a real fraction of rows, and not what "did HeadStart fetch this
  recently" means. `first_seen` (ADR-0031) is HeadStart's own write-once ingestion stamp.

## Consequences

**The Search tab is never empty.** Landing on it, or clearing the query box, shows the newest
Jobs instead of a placeholder — `docs/agents/domain.md`'s **Query** is now optional
(CONTEXT.md updated: **Browse**, **Page**).

**`k`'s existing contract is untouched.** Every caller that predates this ADR — the alerts
module's `k=100`, any hand-built `/search?q=...&k=...` URL — behaves exactly as before, since
omitting `page` is indistinguishable from `page=1`.

**The frontend's old "Show 10/20/50" control is gone.** Page size is fixed at 20 client-side
(`PAGE_SIZE` in `app.js`), replaced with Prev/Next pagination capped at page 20 — 400
addressable Jobs per query, matching what was asked for. The server's own `max_k=100` is
unchanged, since other callers still rely on it.

**A row with no computed score is real, not a bug.** A browsed row's `score` is `None`, not
`0` — the frontend omits the match-strength ring entirely rather than showing a misleading
"0%" for a row that was never ranked by similarity in the first place.
