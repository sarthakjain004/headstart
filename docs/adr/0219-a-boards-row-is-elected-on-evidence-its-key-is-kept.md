# ADR-0219: A Board's row is elected on evidence, and its key is kept

**Status:** accepted · **Date:** 2026-09-25 · **Amends:**
[ADR-0023](0023-prune-stale-and-duplicate-index-rows.md) (the representative row: lex-min over the
live rows only) · **Relates to:** [ADR-0012](0012-liveness-ledger.md) (the ledger's `checked_at`),
[ADR-0177](0177-an-unknown-reprobe-keeps-a-live-verdict.md) (an `unknown` re-probe keeps `live`),
[ADR-0191](0191-one-module-answers-whether-a-board-is-scraped.md) (`scrapable_boards.load`),
[ADR-0203](0203-a-row-becomes-a-board-only-through-its-scraper.md) (the prober asks what the
scraper reads)

## Context

The liveness ledger holds several rows for one Board: casing variants (`.../External` and
`.../external`), a display slug beside a careers URL (#650), and one Workday site on two data
centres. `scrapable_boards.load` kept only the `live` rows, then kept the lexicographically
smallest identity per lowercased key (ADR-0023). Measured on the committed ledger at `a0d3b73f`,
which this change's merge of `main` leaves unchanged:

- **D2: any live row wins.** A Board whose newest row is `dead` stayed Scrapable while an older
  row said `live`. 4 groups: `novozymes/novonesis_careers` and 3 `schwaebisch/*` sites, each dead
  on 2026-07-27 over a live row from 2026-07-03. Another 45 groups have a `dead` and a `live` row
  on the *same* day, and 13 have an `unknown` row newer than their newest verified row.
- **D3: ledger order decides.** In 2,320 groups several live rows carry the same identity, and
  the first in the file wins (the file is sorted by tenant). In 581 the survivor is older than
  the group's newest live row. In 1,652, all Workday, the newest live row spells the key in
  another casing.
- **J3: the Hiring list could elect another row.** `min_jobs` filtered rows before the dedupe,
  so `load(min_jobs=1)` could pick a different row than `load(min_jobs=0)` (1 group today,
  `eppendorf/starlabcareers`, a different data centre).

Electing the newest row outright would re-key the 1,652 Workday Boards: a served id carries the
identity's casing (`workday:3m/Search:…` against `…/search`), so their rows would all be evicted
and re-embedded.

## Decision

`scrapable_boards._elect` answers three questions separately, over every row of a Board, whatever
its status.

1. **Is the Board scraped?** Only if no `dead` row is *newer* than its newest `live` row.
   `unknown` never counts: a probe that earned no verdict is no evidence (ADR-0177's reasoning).
   A same-day `dead` does not count either: `checked_at` is a date, so it cannot say which came
   first.
2. **Under which key?** The lex-min identity among its live rows, the rule ADR-0023 has always
   applied. It is the casing the index already carries.
3. **From which row?** The newest live row that carries that key. On a same-day tie, the row the
   ledger lists first, as before. Its slug is what the scraper fetches, its tenant is the
   `CompanyRef.name`, and its job count is what `min_jobs` reads, so the Scrapable and Hiring
   lists elect the same row.

A non-live row whose slug its scraper cannot parse names no Board and is skipped. Workday holds
398 dead or unknown rows with no url, and each would otherwise reach `board_identity`'s fallback
warning on every load.

## Evidence

**Re-probed live on 2026-09-25**, with `check_liveness.p_workday` at 16 concurrent: every row of
the 74 Workday groups where the rule could drop a Board or where #650's rows sit (253 rows).

| Groups | Count | Every row now |
| --- | ---: | --- |
| A `dead` row newer than the newest `live` | 4 | dead: the 4 above, every row refused on every data centre |
| A `dead` and a `live` row on the same day | 45 | at least one row live; every one of their dead rows now answers live |
| An `unknown` row newer than the newest verified | 13 | at least one row live |
| #650 groups not in the rows above | 12 | at least one row live |

So the rule drops exactly the 4 Boards the re-probe confirms dead, and keeping a Board on a
same-day tie is right in 45 of 45 cases.

#650's display-slug rows (13 `dead`, 36 `unknown` today) all re-probed. 6 dead and 23 unknown
now answer live, and each sits in a group with a live URL-form row, which the rule keeps. The
other 7 dead and 13 unknown are in groups with no live row, so they were not Scrapable before
and are not now. No display-slug row can take out or re-key a live Board.

**A/B against `origin/main`'s `load` on the committed ledger:**

| | before | after |
| --- | ---: | ---: |
| Scrapable Board | 154,037 | 154,033 (−4, the re-probed dead) |
| Hiring Board | 101,487 | 101,482 (−5: the same 4, and `bluemeridian/bmp-external`, whose newest row counts 0) |
| Unique Board | 180,541 | 180,537 |
| Boards whose key string changes | | **0** |
| Boards whose fetched slug changes | | 48, all Workday, all a data centre |
| Boards whose `CompanyRef.name` changes | | 349, all Workday; `company_name.settled` serves the same company for all 349 |

`test_no_board_in_the_committed_ledger_changes_key` keeps the 0.

## The data centre is not evidence

The critique that proposed this rule expected the newest live row to fix 141 stale Workday data
centres (155 when re-measured the same way here). It does not. Probed 2026-09-25, of the 48 Boards whose data centre changes, the new one
answers CXS on 31 and the old one on 17. Across all 344 Scrapable Workday Boards whose live rows
name more than one data centre, the elected one refuses while another answers on 119 before and
105 after.

The reason is the prober. `p_workday` asks the row's own data centre, then sweeps every other,
and writes `live` if any of them answers. It keeps the row's url as it was. So a row's
`checked_at` dates a verdict on the Board, not on the data centre its url names. In 90 of the
105 remaining cases, a row that carries the key names the answering data centre, but that row is
not the newest. In the other 15, only a row with another casing names it.

A tie-break on job count was tried and dropped. It moved 51 more Boards, and the new data centre
answered on only 11 of them.

Nothing is lost when the data centre is wrong: `WorkdayScraper._resolve_instance` sweeps the data
centres at scrape time, at the cost of a few extra POSTs. The fix that would make this evidence is
in the prober: write the data centre that answered into the row's url. It is not done here;
[#661](https://github.com/sarthakjain004/headstart/issues/661) tracks it.

## Consequences

- `min_jobs` reads the job count on the key row, not on the newest live row. A Board whose key row
  counts 0 while a newer row in another casing counts jobs would leave Hiring on stale evidence;
  the committed ledger has 0 such groups today (checked 2026-09-25).
- `index prune` evicts the 4 dropped Boards' rows, since `live_keep_set` reads `load`.
- CONTEXT.md and the README separate the two things between Live row and Unique Board: 6,632
  duplicate spellings and 4 Boards outvoted by a newer `dead` row. The README funnel gains the
  row; `tests/test_board_counts.py` checks it and both prose sites.
- The ledger's rows are unchanged. #650's display-slug rows stay; they can no longer change a
  verdict or a key.

## Alternatives considered

- **Newest row wins outright.** Re-keys 1,652 Workday Boards and about 44,666 served rows.
  Rejected.
- **Carry the newest row's data centre onto the key's casing** (a Workday hook). Of the 95
  groups where only another casing is newest, that data centre answers on 15 and the key row's
  on 80. Rejected on that measurement.
- **Treat a same-day `dead` as newer.** All 45 such groups re-probed live, so it would drop 45
  Boards that answer live.
