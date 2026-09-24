# ADR-0211: Held descriptions are re-fetched on a seven-day rotation

**Status:** accepted · **Date:** 2026-09-25 · **Amends:**
[ADR-0048](0048-skip-details-we-already-hold.md) and
[ADR-0050](0050-persist-descriptions-across-runs.md) (the skip-list no longer holds every held
Job) · **Relates to:** [ADR-0207](0207-the-served-description-follows-the-posting.md) (an edit
that is fetched reaches the table), [ADR-0089](0089-the-description-store-holds-text-not-verdicts.md),
[ADR-0168](0168-delete-the-orphaned-blobs-dont-ask-for-them-to-be-collected.md)

## Context

ADR-0207 serves an edited description once a fetch returns it. Six Scrapers never fetch it again:
ADP, Apple, Cornerstone, Eightfold, Phenom and Zwayam skip a Job's detail once the store holds its
description (ADR-0048), and their store fragments held 0 replacements across the 7 runs read on
2026-09-24. The owner asked for these to re-fetch too, on a bounded rotation, with a period and
budget that fit each ATS's measured limits, and for any ATS without a safe period to be left out.

### Measured

**Held descriptions** (HF store, 2026-09-24): ADP 5,607 on 1,704 Boards; Apple 5,056 on 1;
Cornerstone 2,174 on 201; Eightfold 45,084 on 105; Phenom 5,008 on 69; Zwayam 4,745 on 163.

**How often their Boards are scraped** (`board_freshness.csv.gz`, age of each Board's last
authoritative scrape at 2026-09-24 16:30Z):

| ATS | Boards | median age | max age | older than 1 day |
|---|---:|---:|---:|---:|
| ADP | 7,870 | 0.14 d | 0.25 d | 0% |
| Apple | 1 | 0.00 d | 0.00 d | 0% |
| Cornerstone | 415 | 0.17 d | 0.49 d | 0% |
| Eightfold | 101 | 0.00 d | 10.62 d | 1.0% |
| Phenom | 77 | 0.00 d | 0.93 d | 0% |
| Zwayam | 222 | 0.38 d | 4.99 d | 27.5% |

**A live re-fetch** (2026-09-25): the scraper run on a real Board with a sample of its held Jobs
taken off the skip-list, each fresh text compared with the held one.

| Board | re-fetched | same | different | empty |
|---|---:|---:|---:|---:|
| adp: one 63-Job Board | 15 | 15 | 0 | 0 |
| apple: jobs.apple.com | 20 | 20 | 0 | 0 |
| cornerstone: gmv | 15 | 15 | 0 | 0 |
| phenom: careers.bcg.com | 15 | 15 | 0 | 0 |
| phenom: jobs.baesystems.com | 20 | 19 | 1 (figures changed) | 0 |
| eightfold: softtek | 15 | 10 | 5 (rewritten sections) | 0 |
| eightfold: ngc | 20 | 18 | 2 (a clearance term, added tags) | 0 |
| zwayam: careers.microland.com | 15 | 9 | 6 (`’` read back as `?`, nothing else) | 0 |

**Rate limits**, from each module's docstring:

- ADP: F5 refuses the 201st request in a 60-second window across tenants, and the scraper paces
  every request at 0.4 s (150 a minute).
- Apple, Cornerstone, Phenom: no limit found (Apple to 64 wide, Cornerstone to 196 requests/s,
  Phenom over 360 requests).
- Eightfold: the edge meters per origin across tenants, losing 50% of details at width 25.
- Zwayam: an Akamai per-IP quota, 23 of 100 refused at 500 cumulative requests and all at 600.

**Detail requests today**, run 36021294272's shard logs: ADP 493, Cornerstone 57, Eightfold 47,
Phenom 133, Apple 4. The pipeline ran 27 times on 2026-09-24.

## Decision

**ADP, Apple, Cornerstone, Eightfold and Phenom re-fetch each held Job every 7 days.**
`update_descriptions` leaves the due Jobs off the skip-list it publishes, so the next scrape
fetches their details like any unheld Job's. The rotation lives in
`headstart.ingest.held_refetch`.

- **A Job is due** once the last fetch that reached it is 7 days old, by a ledger in
  `data/state/description_checked.tsv.gz` (`id`, UTC date). A Job the ledger does not know is due
  only on its own day of the cycle, `(day + crc32(id)) % 7 == 0`. The first round after this ships
  is spread over 7 days, and so is every round if the ledger is ever lost.
- **A fetch counts as a check whatever it returns.** The due set is published beside the skip-list
  (`data/state/refetch_due.txt`), so the next run knows which corpus rows were asked for. A due
  Job that came back empty keeps its held text (ADR-0050, ADR-0089) and waits another 7 days,
  so a posting whose detail always answers empty is not fetched on every scrape.
- **The ledger is narrowed to held Jobs each run**, so it never outgrows the store: about 63,000
  lines, under 1 MB gzipped.

**The per-run budget this implies**, at 27 runs a day:

| ATS | re-fetches a day | a run | today's detail requests a run |
|---|---:|---:|---:|
| ADP | 801 | ~30 | 493 |
| Apple | 722 | ~27 | 4 |
| Cornerstone | 311 | ~12 | 57 |
| Eightfold | 6,441 | ~240, across ~11 shards | 47 |
| Phenom | 715 | ~27 | 133 |

ADP's 30 a run are spread over its shards and paced at 0.4 s each, well under F5's window.
Eightfold's ~22 a shard is two orders of magnitude under the ~3,400 a shard the width-25 setting
was sized for. A Board that is not scraped for 7 days re-fetches all of its held Jobs at once:
that is the same request count as its first scrape, which the pass already absorbs, and every one
of these Boards was scraped within a day except one Eightfold Board.

**Zwayam is left out.** Three reasons, each measured: its per-IP quota refuses from about 500
cumulative requests; 27.5% of its Boards had not been scraped for more than a day, up to 5 days,
so a rotation would arrive as whole-Board bursts against that quota; and 6 of 15 re-fetched texts
differed only in `’` becoming `?`, so re-fetching would replace good text with worse text.

## Consequences

- An edit on these five reaches the store, the re-derive queue and (ADR-0207) the served table
  within 7 days of the last fetch, plus the time until its Board is next scraped.
- Zwayam's edits are still never fetched. Re-fetching it needs the `?` rendering fixed first and a
  budget under its per-IP quota.
- Two small state files join `data/state`, rewritten each run: about 1 MB together.
- The skip-list is no longer "every held Job". It is every held Job minus the due set.
- A fetch that fails transiently on a due Job counts as a check, so that Job's next attempt is 7
  days away. That is the conservative trade: no retry storm, at the cost of a slower re-check.
