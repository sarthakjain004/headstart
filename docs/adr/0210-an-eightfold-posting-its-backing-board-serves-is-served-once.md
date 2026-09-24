# ADR-0210: An Eightfold posting its backing Board serves is served once, matched on the requisition

**Status:** accepted · **Date:** 2026-09-25 · **Extends:**
[ADR-0187](0187-a-workday-requisition-is-served-once-per-tenant.md) (the duplicate grouping this
widens) · **Amends:** [ADR-0188](0188-a-dedup-rule-change-is-a-trends-epoch.md) (the marker no
longer carries a dedup's removals alone), [ADR-0205](0205-an-eightfold-site-its-backing-board-already-serves-is-an-alias.md) (its candidates table becomes the committed pairs file) · **Relates to:**
[ADR-0061](0061-refreshable-metadata.md) (the facts
refresh that stamps old rows), [ADR-0083](0083-evict-only-on-a-second-consecutive-absence.md) (the grace period
re-admission waits on)

## Context

ADR-0205 buries an Eightfold career site in the alias ledger only when its backing ATS Board serves
every tech posting it lists. That removed 113 of the ~9.8k cross-ATS duplicate rows on served index
v654: one posting served only by the Eightfold copy blocks the whole Board. The user chose a
**row-level** rule instead, keyed on a new **`requisition` column** (over matching descriptions, and
over staging the two), with the backing row always winning.

Every Eightfold posting states its backing ATS's requisition, and #632 measured how it lines up
(recall 9,690 of 9,694 on postings known to be held):

| backing ATS | Eightfold field | backing field |
| --- | --- | --- |
| Workday | `atsJobId` | the native id we already serve |
| Oracle | `displayJobId` | the requisition `Id` (also the native id) |
| Greenhouse | `atsJobId` | `internal_job_id` |
| Taleo Enterprise | `atsJobId` | the listing's `contestNo` |
| SuccessFactors | `atsJobId` | `"internalId":"NNN-locale"` on every RMK job page |

## Decision

1. **`Job.requisition` and a nullable `requisition` string column on the served table.** Filled by
   six scrapers only — eightfold, workday, oracle, greenhouse, taleo_enterprise, successfactors —
   from the fields above; every other ATS writes null. Stored as the ATS states it, normalised
   just enough to compare: `models.requisition_of` makes it trimmed text (Greenhouse states a
   number), and SuccessFactors keeps only the id before the first `-` of `internalId`, so a
   requisition's `en_US` and `de_DE` pages carry one id. The column is a fact like `url`
   (`doc_prep.META_FIELDS`), so ADR-0061's facts pass stamps a row already held the next time its
   Board is scraped, and sync's metadata refresh carries it into the table. Nothing in the Space or
   the search API reads it (`search.RESULT_COLUMNS` is an explicit projection).
   **The store keeps it only on rows whose Board the pairs name** (the coordinator's decision for
   the user, 2026-09-25): every Eightfold site in the file, and every Board behind one — for
   Workday any site of the tenant. `eightfold_backing.in_scope` is the test, matched on the id's
   prefix, and `doc_prep.stored_facts` is the one place it is applied, read by both `to_meta` (a
   new Job) and `update_meta`'s facts refresh (a Job already held); every other row stores null.
   Only these rows can ever match, and a new value in the store rewrites the served row, vector
   included: stamping all six ATSes would rewrite 215,746 v654 rows on the first run (267,496 over
   a day) for no dedup, where the scoped fill — Workday counted tenant-wide, as `in_scope` matches it — rewrites
   **25,890 on the first run and 26,124 in all**
   (13,909 Eightfold, 10,879 Workday, 614 SuccessFactors, 393 Oracle, 264 Taleo Enterprise, 65
   Greenhouse). HF storage is the binding cost (ADR-0168). The scrapers still state the id on
   every row of their ATS, in the corpus. **To widen it**, add pairs to the file — a new pair's
   rows are stamped the next time its Boards are scraped — or drop the check in `stored_facts`.
2. **The Eightfold scraper picks its field by the backing ATS.** `displayJobId` when its Board's
   backing Board is Oracle, else `atsJobId`, read from the pairs file below. A posting read through
   the sitemap fallback states neither, and stays null.
3. **The pairs are a committed file, `data/validate/eightfold_backing.csv`**, read through one
   module, `headstart.eightfold_backing.load()` — `{Eightfold slug: backing Board keys}`, several
   allowed, Lumen out (the user's decision). It *is* ADR-0205's `BACKING` table, moved out of the
   script rather than written by it: the script now loads it as its candidates, and `index
   sync`/`prune` and the Eightfold scraper read the same file. One source, three readers — the
   deletion test holds, since deleting the module puts the CSV parsing back in three places — and
   no second copy that drifts until someone reruns a 90-minute network script.
4. **The grouping extends ADR-0187's, in `index_plan._placement`.** An Eightfold row whose stamp a
   row on one of its backing Boards also carries joins **that row's group**
   (`_backing_copies`). The ranking that picks a group's survivor, and the displacement sync
   applies to an incumbent, both put a backing Board before an Eightfold site (`_survivor_precedence`), ahead
   of ADR-0187's public-before-non-public key. So in sync an arriving Eightfold copy is refused
   while its backing row is served, an arriving backing row displaces a served Eightfold copy that
   prune then drops in the same run, and in prune the copy is a duplicate. A Workday backing Board
   is matched on its tenant, the group ADR-0187 already serves a requisition from, so a copy the
   tenant serves from another site still counts. A pair whose backing Board is itself an Eightfold
   site (a company's second site, ADR-0205's hand-frozen losers) is not matched: there is no other
   ATS's row to prefer. What ADR-0187 decides for Workday rows is
   unchanged: a Workday row's stamp is its native id, the group it already had, and an Eightfold
   copy that joins ranks last in it (pinned by a test).
5. **Zero loss, re-admission and the transition fall out of the grouping.** A posting only the
   Eightfold site serves has no backing row to join and stays. When the backing row leaves —
   evicted after ADR-0083's two absences, or its Board off the keep-set — the copy has no group to
   join, so the next scrape of the Eightfold Board adds it back. An unstamped row never matches, so
   until both rows carry a stamp both are served as today.
6. **`DEDUP_VERSION` 4 → 5**, a new grouping in `plan_prune` (ADR-0188).
7. **A dedup eviction ledger, `data/state/dedup_evictions.csv`** (the user's decision): rows
   `(ts, board, count, rule)`, one per run, Board and rule, counting every row prune takes out as a
   duplicate — `case-variant`, `workday-tenant` (ADR-0187), `backing-requisition` (this rule) — or
   as off-Board on a Board an alias ledger buries, `alias:{signal}` (`index_plan.aliased_boards`
   matches ledger rows exactly as `scrapable_boards.load` skips them). Ordinary closures (sync) and
   other off-Board evictions are never recorded. `ts` is `ingest.run_ts()`: the merge job exports
   `HEADSTART_RUN_TS` once, and `role_trends` stamps its ledger with the same value, so company
   Trends joins a removal to its tick and can add it back exactly. Written after the table's delete,
   to a temp file renamed over the ledger, and only when prune is given `--dedup-evictions`, which
   the merge job passes only when this run's corpus artifact (and so `data/state`) arrived: it rides
   `data/state` through the HF dataset like every other ledger, fetched by the join's
   `data/state/*` and published by merge.

## Evidence

**SuccessFactors costs no request.** The detail pass already fetches every tech job page, and the
id is on it. Live 2026-09-24 through the scraper's own fetch: 484 of 510 parsed pages across 27
Boards stated `internalId`, classic and CSB-rendered alike (10 named pair Boards and 17 random
Hiring Boards, plus four read whole: dolby 110/110, britishcouncil 144/144, cipla 63/63). All 26
misses were one tenant's (`careers.bsp.gov.ph`, 22 of 32 pages). One dolby page omitted it on three
fetches within a minute and stated it on the next twelve.

**Projection on served v654 (read-only)**, requisitions derived as #632's measurement read them:
**10,296** Eightfold rows removed — 9,211 onto Workday, 563 SuccessFactors, 275 Oracle, 247 Taleo
Enterprise — from 30 Eightfold Boards (nvidia 1,974, micron 1,924, ngc 1,680, amat 874, caci 787,
citi 762, …). **0 tech postings lost**: every removed row's duplicate group keeps a backing row. The
measurement read both of a posting's ids and the scraper stores one; only where a posting
states two could the projection match on the other, which leaves at most the 2 premierhealth
rows (Taleo, where 39 postings state two) counted that the code may not remove.
(Eight hp rows looked lost to a check keyed on the named backing site; their requisition is served
from another hp Workday site, the ADR-0187 survivor, which is why the tenant match exists.) Twilio,
vizientinc and curriculumassociates remove nothing: ADR-0205 already buries them.

**When the removals land**, from `data/state` fetched from HF on 2026-09-24 (board_priority,
board_freshness) and the run cadence of 2026-09-21..24 (113 runs, median 42 minutes apart, ~30 a
day): a Head Board (top 6,000 by score) is scraped every run; any other Scrapable Board (154,052)
is in the Tail with p = 14,000 / 148,052 = 0.095 a run, 90% after 24 runs (~19 hours). Weighted by
the 10,296 rows, **99.6% have both sides on Head Boards and are stamped in the first run after
deploy**, 99.9% within 15 runs, all within ~25 runs (under a day). The rest sit on six Tail
pairs (albemarle, britishcouncil, costar_campus, premierhealth, sephora, vialto: 41 rows). Counted
by pair instead of by row, 25 of the 31 matchable pairs are stamped on both sides on the first run,
and **90% of pairs after about 10 runs (~8 hours)**.

## Alternatives considered

- **Group every stamped row on `(Board, requisition)`**, the spec's first wording for backing rows.
  Rejected: a backing Board serves one requisition as several postings — Twilio's Greenhouse Board
  posts internal job `3399621` four times (per location), Vodafone's SuccessFactors Board 30
  requisitions twice (per locale) — and keying their groups on the requisition would drop all but
  one of each. Joining the Eightfold row to one backing row's group removes only the copy.
- **Match on descriptions.** Rejected by the user for the requisition column.
- **Have the ADR-0205 writer emit the pairs file from its `BACKING`.** Two copies of one table, the
  file refreshed only by a long network run; the committed file read by all three is one.
- **Stamp every row of the six ATSes** (the spec's first reading of "fill it only where it's
  needed"). 267,496 v654 rows rewritten, 215,746 of them in one run, for stamps nothing reads.
  Rejected for the scoped fill above.
- **Scope in each scraper.** Six scrapers would each need the pairs; one check where the fact
  enters the store is one place.
- **Land every removal on the marker tick** (hold the rule until both sides are stamped
  everywhere). Unneeded: 99.6% land on it already, and the ledger records the rest exactly.

## Consequences

- **A one-time rewrite of ~26k served rows**, 25,890 of them in the first run after deploy (on
  v654): sync's refresh rewrites each newly stamped row once, vector included, and
  `cleanup-index`'s compaction reclaims the fragments. A pair added to the file later costs its
  own rows the same, once.
- **The corpus and the store disagree on purpose.** `data/jobs/tech` carries the scraper's
  `requisition` on every row of the six ATSes; the store and the served table carry it only in
  scope. Read the column, not the corpus, for what the rule sees.
- **ADR-0188's marker no longer carries a dedup's removals alone.** This rule's removals follow the
  stamps, not the marker; measured, 99.6% land on the marker's tick and the rest within a day. The
  dedup eviction ledger is what makes that exact: it records every removal a dedup rule makes, in
  the run it makes it.
- **A stamp that goes missing for a run re-admits the copy for a run.** The facts pass overwrites a
  stamp with whatever the scrape saw, so a SuccessFactors page that omits `internalId` (the dolby
  minute above) nulls its row's stamp, the next Eightfold scrape re-adds the copy with a fresh
  `first_seen`, and the backing Board's next scrape takes it out again. Rare on the measurement;
  the ledger records the second removal.
- **`cleanup-index`'s prune records nothing.** It neither fetches nor publishes `data/state`. The
  pipeline's prune runs first after every sync, so a duplicate reaching cleanup unremoved needs a
  ledger change between the two; a dedup it does make there is logged but not in the ledger.
- **A run whose corpus artifact was lost writes no ledger rows**, since `data/state` is not
  published then; prune still runs and its removals are logged.
- **An aliased Board's row is labelled only when its id resolves to that Board.** An off-Board id
  resolves through `board_of`'s last-colon guess (ADR-0049), so a buried Board's row whose native
  id carries a colon (Workday's `REQ: 228` shape) is removed unrecorded. The same guess every
  other off-Board path makes; rare, and logged.
