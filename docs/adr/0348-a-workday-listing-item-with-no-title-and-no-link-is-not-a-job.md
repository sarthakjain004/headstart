# ADR-0348: A Workday listing item with no title and no link is not a Job

**Status:** accepted · **Date:** 2026-09-29 · **Amends:**
[ADR-0088](0088-a-lost-detail-is-not-a-truncation.md) (its 2026-09-09 amendment's cost claim) ·
**Relates to:** [ADR-0252](0252-a-workday-department-is-read-off-the-family-slice-that-listed-it.md),
[ADR-0083](0083-evict-only-on-a-second-consecutive-absence.md)

## Context

The 2026-09-29 served-table audit (TN-05) found 48 Workday rows titled "Untitled" with a null
location and the Board root as their link: Walmart 41, Airbus 2, Hitachi 2, NVIDIA 2, AtkinsRéalis 1.
Their descriptions are real (46 of 48 carry text, 322,964 characters in all). "Untitled" appears as a
title on no other ATS and on no other row (500,167 rows; every title containing "untitled" is one of
the 48, and all 48 are the only Workday rows whose link has no `/job/`), so no legitimate job is
titled "Untitled".

**What the source serves.** Some listing items carry only `bulletFields`, a requisition id and
nothing else: no `title`, no `externalPath`, no `locationsText`. Measured live 2026-09-29 by walking
Walmart's pinned Technology, Full time slice: 860 items, 43 stubs (5%), every one with the single key
`bulletFields`, none titled, none also present as a real item. The audited Walmart ids (R-2646910,
R-2611361, R-2400946, R-2445125, R-2445762) are among them. A stub is still findable by the text of
its old description (`searchText: "Ray RAPIDS agentic"` returns the one stub, total 1), so the index
holds the posting but its display fields are gone. There is no detail to fetch and no link to give:
`docs/workday/2026-09-09_parser-shaped-detail-losses.md` measured the CXS detail addressed by req id
at 404, 406 and 404, and 0 of 27 avanade stubs in the Board's sitemap; Walmart's sitemap lists 100
URLs and none is a stub. The other seven audited ids (Airbus, Hitachi, NVIDIA, AtkinsRéalis) are not in
their Board's listing at all today (a req-id search returns total 0, where the same search returns a
real posting 5 of 5 times as a control), so their stub state was momentary.

**Why the rows exist.** `parse` fills a missing title with "Untitled" and a missing path with the Board
root. That was harmless on the premise, written down 2026-09-09 in `workday.py`, ADR-0088 and the
write-up above, that `tech_filter.classify` drops an untitled Job. The premise held while a Workday
Job had no department. ADR-0252 (2026-09-28) gives an item read inside a one-family slice that family,
so a stub on a pinned Board reads `Technology`, and `classify("Untitled", "Technology")` is tech. 39
of the 48 rows were first seen in one merge on 2026-09-29 (08:16 UTC), the day after ADR-0252 merged
(#770). Their text is not new: the description store hands a stored description back to any Job with
the same id, so a stub whose id was once a real posting comes back with that posting's text under no
title. Nine rows carry a real `posted_at`, so they were real postings first and took a stub's
metadata later; that ordering is inferred from those fields, not observed.

## Decision

`WorkdayScraper.parse` makes no Job from an item that has no `externalPath` and no title. It says so
through the shared `note_unread_rows` line, one INFO per Board: `N of M listed row(s) had no title and no externalPath`. The
stub never reaches `data/jobs/workday.jsonl`, `data/facts/`, the description store or the index, so the
tech gate is not asked to catch it. The premise comments in `workday.py` are corrected.

Only that shape is dropped. An item with a title and no path keeps its behaviour, and
`_report_detail_losses` still counts it (0 of 43 on Walmart, 0 of 38 on 22 Boards). An item with a path
and no title keeps the "Untitled" fallback, a shape never observed and so left alone rather than
guessed at.

**The 48 existing rows leave through the ordinary path.** Once the stub ids are absent from a
scrape, `plan_sync` counts a first absence as Unconfirmed and deletes on the second consecutive one
(ADR-0083). All five Boards rank 8th to 70th of 43,220 by priority score (`board_priority.csv`,
2026-09-29), so all five sit in the Head of every run's pick (CONTEXT.md §Slice), and Walmart's pinned
slice read 860 of 860 items live, so it stays in eviction scope. Nothing rewrites a row and no targeted rule exists. The delete touches 48 rows.

## Alternatives

- **Reject an empty or "Untitled" title in `tech_filter`.** One rule for every ATS, but no other ATS
  has the shape, and it would leave the stub in `data/jobs/workday.jsonl` and the raw-fact store, which
  keep every scraped Job whether tech or not. The tech gate decides which real jobs are tech; whether
  an item is a job is the scraper's call.
- **Recover the title or link.** There is no path, so no detail JSON and no JSON-LD page to read, and the
  req-id addressing and sitemap probes above found nothing. Deriving a title from the stored
  description would be a guess put in front of the reader.
- **Keep serving the row with a description.** The link is the Board root and the title is invented;
  the reader can neither name the job nor open it.

## Consequences

- Walmart's tech count drops by about 43 (859 in `last_tech_jobs` to about 816), the other four
  Boards by one or two when they show a stub. A Board that lists only stubs reads 0 Jobs.
- The description store keeps the stubs' text. If a posting comes back with a title and a path, it is a
  normal Job under the same id, and the stored text applies as it always did.
- ADR-0088's 2026-09-09 amendment said such a Job is dropped by `tech_filter`. That is no longer true
  and now reads as history; `parse` is what drops it.
- No `DERIVATIONS_VERSION` bump: nothing derived from `experience.py` or `salary.py` changes.
