# ADR-0301: A Recruitee label that redirects to another is an alias

**Status:** accepted · **Date:** 2026-09-29 · **Relates to:** [ADR-0111](0111-duplicate-boards-resolve-the-board-surface.md) (the redirect signal and the alias ledger), [ADR-0222](0222-an-icims-portal-that-redirects-to-another-is-an-alias.md) (the same decision for iCIMS, followed here), [ADR-0188](0188-a-dedup-rule-change-is-a-trends-epoch.md) (the Trends epoch)

## Context

A Recruitee Board is one label, `{label}.recruitee.com`, read through
`https://{label}.recruitee.com/api/offers/`. A company that renames its account keeps the old
label, and Recruitee answers the old label's offers API with a 302 to the new label's:
`thesjefgroup` → `elockers`, `hetzner` → `xneelo`, `billwerk` → `frisbii`, `citizenlab` →
`govocal` (measured 2026-09-29). The scraper follows the redirect, reads the new label's offers
and serves them again under the old label's `board_key`. When both labels are on Live rows of the
ledger, every posting is served twice (#699).

`dedupe_boards.py` could not find these. The default `alias_key` returns the host a Board lands
on, `elockers.recruitee.com`, but a Recruitee slug is the bare label `elockers`. No key matched a
live slug, so every redirect was reported as `migrated`. ADR-0111's docstring warns about exactly
this failure.

## Decision

**A Recruitee Board on a Live row whose offers API redirects to another Board on a Live row is
buried onto it**, in `data/validate/aliases/recruitee.csv` with signal `redirect`. This is
ADR-0222 applied to Recruitee, with two changes:

- **`RecruiteeScraper.alias_key_of_landing` returns the label.** A landing on
  `{label}.recruitee.com` keys as `{label}`, which is the ledger's own slug. A landing off
  Recruitee keeps its whole host. No slug matches that, so such a Board is reported and never
  buried.
- **The scan runs with `--workers 4`, a new `dedupe_boards.py` flag.** Recruitee rate-limits per
  client address. 24 concurrent requests drew 37 × 429 out of 48 (2026-09-29). A probe that ends
  on a 429 settles on the label's own host, which reads as "no redirect". So a wide `--apply`
  would silently drop the rows of every duplicate it failed to reach.

The writer is `dedupe_boards.py --ats recruitee --workers 4 --apply`. Every row comes from the
redirect scan, so `--apply` loses nothing. It runs by hand after every refresh of
`data/validate/liveness/recruitee.csv`, and CLAUDE.md's landing rules carry the step. Nothing
scrapes a buried label, so the scan is the only thing that notices one that stops redirecting.

## Evidence

Three scans of the 4,010 Live rows of the committed ledger ran on 2026-09-29 and agreed pair for
pair. The first was a `dedupe_boards.py` dry run with 4 workers. The second was an independent
first-hop `HEAD` scan that retried each of its 255 × 429 until it got a verdict. The third was
the `--apply` run.

- **418 labels redirect**, and every one lands on another `*.recruitee.com` label. None of the
  targets redirects again.
- **357 land on a label on a Live row**, in 322 clusters: 294 survivors with one duplicate, 21
  with two and 7 with three. These are buried.
- **61 land on a label on no Live row**: 57 are absent from the ledger and 4 are on `dead` rows.
  They are reported as `migrated` and left alone (ADR-0111).
- The other labels answered 200 (3,557), 404 (33) or 403 (2) and resolve to themselves.

Nine redirects were also read by hand with `curl`, including the five #699 names. Each answered
302 to the offers API of the label the scans named.

Projected onto the served table (`jobs.lance` v298, 498,848 rows, read 2026-09-29):

| | |
| ---: | --- |
| 4,084 | Recruitee rows served, on 751 Boards |
| 85 | buried Boards with served rows |
| 645 | served rows the next `index prune` evicts |
| 0 | of those rows whose offer id the survivor does not also serve |
| 3,439 → 3,439 | distinct (survivor, offer id) tech postings served |
| 464 → 1 | offer ids served on two or more Recruitee Boards |

No posting loses its only served copy.

## What this does not catch

**Several labels that redirect to one label on no Live row.** These are reported as `migrated`,
yet they duplicate each other. There are 5 such groups covering 12 labels:

- `cnid`, `groupefl` and `mdaconsulting` → `mda`
- `bleeve`, `greenhome` and `regionaalenergieloket` → `rel1`
- `orangevalley` and `werkenbijorangevalley` → `werkenbijfollo`
- `paylogic` and `seetickets` → `eventim`
- `baltic` and `balticexteriorsinc` → `balticroofing`

Only `paylogic` and `seetickets` serve rows today, one copy each of one posting. The fix is to
land the target label on a Live row. That is a liveness change, so it is not made here. Once the
target is landed, the next scan buries the group onto it.

## Consequences

- **Scrapable Board** and **Hiring Board** fall by the buried labels (README and CONTEXT.md,
  recomputed with this change). `index prune` evicts their rows through its existing off-Board
  path, booked as `alias:redirect` in `dedup_evictions.csv`.
- **`DEDUP_VERSION` goes to 10.** This is the `redirect` signal's first ledger for Recruitee, the
  case ADR-0222 made a bump.
- **The gap is ADR-0222's.** A burial is only as fresh as the last scan. If a buried label stops
  redirecting and starts posting on its own, nothing scrapes it until the scan runs again.
