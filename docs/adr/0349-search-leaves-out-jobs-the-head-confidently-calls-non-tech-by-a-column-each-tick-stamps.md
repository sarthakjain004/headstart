# ADR-0349: Search leaves out the Jobs the role-family head confidently calls non-tech, by a column each Trends tick stamps

**Status:** accepted · **Date:** 2026-09-29 · **Amends:**
[ADR-0057](0057-record-family-assignments-and-report-reassignment.md) (its rejection of "a
`role_family` column on the served table", for one bit of the verdict, not the family) ·
**Relates to:** [ADR-0017](0017-tech-role-filter.md) (the recall bias this pays for),
[ADR-0173](0173-rebuild-the-search-indexes-with-the-table.md) and
[ADR-0193](0193-one-module-per-materialized-search-filter.md) (materialized Search filters),
[ADR-0220](0220-a-trained-title-classifier-decides-a-role-family.md) and
[ADR-0224](0224-a-rows-description-vector-joins-its-title-in-deciding-its-role-family.md) (the head),
[ADR-0322](0322-a-category-spans-the-index-and-an-agent-filters-by-age-experience-and-employer.md)
(family tables), [ADR-0341](0341-a-job-that-states-no-employment-type-is-full-time-to-the-filter.md)
(the same rewrite path)

## Context

The 2026-09-29 served-table audit (REL-01) found that about one Job in four is not a tech role.
The head, recomputed over every row exactly as `role_trends` does, calls 122,022 of 500,167 rows
(24.4%) non-tech; reading 200 of them found 82% clearly non-tech, 14% borderline and 4% tech. No
Search filter, Browse path or count used the verdict, so a reader saw those rows: the newest 1,000
Browse rows were 17% non-tech, the "Entry level" bucket 36%, and the exact top 20 of 40 generic
queries held a mean 10.6 head-non-tech results ("graduate engineer" 20 of 20, "engineering intern"
19, while "software engineer" and "developer" held none). ADR-0017 makes the Tech filter recall-biased
on purpose, so this is its creep, not a defect in it.

The head's top probability is what separates the safe part of that quarter. On the audited table
(rows the head calls non-tech, by the probability it needs):

| at least | rows |
| --- | ---: |
| 0.5 | 115,075 |
| 0.7 | 95,100 |
| 0.8 | 81,212 |
| 0.9 | **59,523** (11.9% of rows) |
| 0.95 | 39,441 |
| 0.99 | 11,100 |

## Decision

**Search, Browse and every count leave out a Job the head calls `non-tech` with a top probability
of at least 0.9, unless the request sends `include_non_tech`.** The owner chose this on 2026-09-29:
hidden by default, on the website and in the MCP `search_jobs` tool, with a visible switch to show
everything.

- **The threshold is one constant**, `confident_non_tech_filter.PROBABILITY = 0.9`, beside
  `MEASURED_ON_HEAD_VERSION = 3`. A test compares that version to the head's manifest, so retraining
  the head fails it until the threshold has been measured again on the new head's probabilities.
- **The verdict is a column, `is_confident_non_tech`**, the next materialized Search filter under
  ADR-0193: `search_filters/confident_non_tech_filter.py` holds the column, the verdict, the SQL an
  old table is migrated with (`false`), the clause and the capability check. It is bitmap-indexed
  and never null: a new row is `false`.
- **The Trends tick writes it.** `role_trends` already decides every served row's family, so once
  the head, the family list and the title cache have decided every row it stamps the confident ones
  through `ingest/confident_non_tech_stamp.py`, before `refresh-indexes` and `index_publish` in the
  same merge job. Its decision is recomputed whole each tick.
- **`decide_rows` is `decide_rows_scored`.** The head's probability had to reach the tick, so
  `role_family_classifier.decide_rows_scored` returns each row's family with it, and `decide_rows`,
  which only `role_trends` called, is gone.
- **The clause is `(is_confident_non_tech IS NULL OR is_confident_non_tech = false)`**: a row with
  no verdict is visible, so nothing is hidden on no evidence. A table from before the column has
  `has_confident_non_tech_flag` false, and Search then compiles no clause and does not error.
- **The switch is `include_non_tech`**, `SearchFilters.include_non_tech` (default False, the one
  field whose unset value compiles a clause), read from `include_non_tech=true` or `=1`. The site
  shows it as "Include non-tech roles", off by default, sent with the other filters, kept by a Saved
  Set and its email digest, and shown as a removable pill. The MCP argument has the same name and
  description in `search_jobs`, and the Space's agent contract is now version 18 (17 is ADR-0352's `per_company`). `role_requirements`
  takes it too, with the same schema; `company_profile` takes none and reads the default.

### What the stamp leaves alone, and what it changes

Hidden by default, because they compile through `build_filter` or scope by Board with the same
clause: `/search` (ranked, browsed, `like=`), `/facets` and every option count, the Blocking filter's
recounts, `/requirements`, `/companies/locations`, `/companies/levels` (so `company_profile`'s
places, levels and openings hide the same rows `search_jobs` does; how a place is spelled is a separate
matter, ADR-0344), the door's and the header's job counts (`JobSearch.n_served`, the visible set, which
the door's new-jobs count already was), and a Subscription's digest (its filters go to `/search`).

Not hidden, and why:

- **`/job` and `get_job`**: a Job read by id opens whatever it is. `get_job`'s "does the index serve
  this Board at all" check sends `include_non_tech` too, so a Board of only such roles is still held.
- **Saved and `indexed()`**: a Job the user saved must not vanish when a tick stamps it.
- **Trends, Hiring now and the role-assignment snapshot**: they count non-tech apart already, and
  the family tables (ADR-0322) hold tech families only.

`/facets` and `/requirements` say what a request left out as `non_tech_left_out` (the count with the
switch on, less the count), present only where the table has the column and the request did not include
them, so an answer never claims a hiding that did not happen. `/requirements` counts it as it counts
its sample, within the category and under `operators=`. The site prints it under the switch and in the
empty state, and the MCP tools print it in a search's scope line, its nothing-matched line, a
requirements answer's scope line and `company_profile`.

### It fails safe, in the direction of showing

- Missing head, missing family list, an unusable taxonomy, or a title cache still warming up: the
  tick returns before any family is decided and **the column is untouched**. Rows the last healthy tick
  stamped stay stamped and nothing new is hidden.
- A title no run has encoded yet has no probability, so a new non-tech Job whose title is not cached
  is visible until a later tick encodes it.
- A stamp that would hide more than 30% of the table (`MAX_STAMPED_SHARE`) is **refused** and the column left as it was: a
  healthy head calls 24.4% non-tech at any probability, so a share above 30% means the head or its
  inputs are broken.
- The column is replaced by dropping it and adding it back (see below). A write that fails between the
  two puts the column back with every row visible, and `role_trends` logs it and carries on: a failed
  or refused stamp never sinks a Trends tick.

### How a hidden Job comes back

The tick names the whole set every time, so a Job the head stops calling confidently non-tech is simply
not named and is visible again after the next tick. A retitled posting takes that path: `sync` rewrites
its row with the new title, and the next tick decides it from the new title (a title no run has yet
encoded is unclassified, so visible at once). The stamp is not a one-way mark.

### Surviving every rewrite path

`_refresh_metadata`'s delete-then-add carries the stamp across like `first_seen` and leaves it out of
its staleness comparison (else every stamped row would read stale on every run); `sync` and `compact`
migrate a table without the column; a re-embedded or new row is added `false` and stamped by the tick of
the same run; `prune` only deletes; `backfill-from-store` and `compact` copy every column, the ADR-0173
rebuild builds the bitmap through `_search_index_specs`, and `refresh-indexes` requires it.

## Measurement

Audited table, v18, 500,167 rows.

- **Rows.** 59,523 stamped (11.9%). By ATS, share of its rows: Taleo Enterprise at least 30%, ADP 28.7%,
  iCIMS 22.6%, Oracle 22.2%, SmartRecruiters 18.1%, SuccessFactors 15.8%, Workday 12.7% (12,869 rows),
  Zoho 9.1%, Lever 6.0%, Greenhouse 5.0%.
- **Precision.** The audit read 101 stamped rows: 94 clearly non-tech, 0 tech, 7 borderline. A second
  read of 60 stamped rows drawn uniformly (seed 20260929) with the description in front: 53 clearly
  non-tech, 7 borderline (a plant-model simulation engineer, a PLC controls engineer, a crash-simulation
  CAE engineer, a director of client value at a technology-services group, an applications engineer, a
  cleared engineering technician, an AES development engineer), 0 tech. Together 147 of 161 clearly
  non-tech, none tech.
- **Tech-looking titles.** Of the 49,221 rows titled software engineer or software developer, none is
  stamped. A broad title regex (software, developer, ML, data, devops, front/back-end...) matches 2,209
  stamped titles (3.7%), 1,355 of them Albertsons' "Front End Entry Level" cashier ad and most of the
  rest store "Front End" clerks and "Job Developer"; the narrower regex over software, web, DevOps, SRE,
  ML, data scientist and data engineer titles matches 30, the largest group "Back-end" store and process
  roles, and about five name software as a tool of a controls or simulation job.
- **The 40 generic queries** (exact top 20 over the served vectors, the query alone encoded): a mean of
  6.6 of the top 20 were stamped before (24 of 40 queries had at least one; "process engineer" and
  "commissioning engineer" 20 of 20). After hiding, 0 stamped remain, and the mean head-non-tech count
  falls from 10.62 to 7.35, the rest being rows the head calls non-tech below 0.9. "software engineer",
  "developer", "analyst", "ML engineer" and "data analyst" are unchanged.
- **The write.** On a clone of the served table, stamping the 59,523 rows by `table.update` took 4.1 s and
  wrote 339.7 MB in 120 files; `merge_insert` on `(id, flag)` 8.6 s and 337.9 MB. Dropping the column and
  adding `id IN (…)` in its place took 0.21 s and wrote 0.08 MB, on lancedb 0.33 and 0.39, and five ticks
  in a row left no data files behind; a second tick after `delete` and `add` on the table gave the same
  set. The column's bitmap builds in 0.05 s. HF storage is the binding cost (ADR-0168), and the row rewrite
  was the price ADR-0341 accepted for a similar backfill; this needs none of it.
- **End to end** (the branch's own code on a clone of the table, lancedb 0.33, no encoder): `sync`'s
  migration 4 ms; the stamp of 59,523 ids 178 ms, 0.09 MB in 8 files; a second identical tick 43 ms and no
  write; the bitmap 43 ms. The default total is 440,644 and `non_tech_left_out` 59,523, together the 500,167
  rows; the whole facet strip takes 31 ms; twenty default browse pages hold 400 rows and none stamped;
  `jobs_by_id` and `indexed` still hold a stamped Job; the door's "new in 7 days" falls from 162,538 to
  141,804. The first read of a description keyword slows from 0.9 to 1.3 s locally (`kubernetes`; `python`
  1.0 to 1.3 s), the second where-clause the left-out count compiles.
- **The clause.** Counting the table with the bitmap column takes 0.5 ms and a browse page 1.6 ms; naming
  the 59,523 ids in `NOT id IN (…)` takes 173 ms and 220 ms (lancedb 0.39, local).

## Alternatives

- **A `role_family` column (ADR-0057's rejected option).** Only one bit is needed, and the family stays
  a diagnostic, so the schema grows by one bool and not by 25 labels.
- **Filtering in Python or by naming the ids.** ADR-0322 measured a 69,456-id clause at 1.8 s a statement
  on the Space, and 173 ms here against 0.5 ms for the column.
- **Rewriting rows** (`update`, `merge_insert`): 340 MB a rewrite, above.
- **A lower threshold.** 0.5 would hide 115,075 rows, twice as many, on a head whose non-tech precision is
  known only at the top; 0.99 hides 11,100 and leaves most of the quarter. 0.9 is the owner's choice and
  the measured point.
- **Hiding by the tech filter instead** (a stricter gate at ingestion): ADR-0017's recall-first rule
  stands; a hidden Job is still in the table, in Trends and behind `get_job`, and reversible by the
  switch.

## Consequences

- Search, Browse and their counts show about 12% fewer rows by default (11.9% on the audited table), the
  Space counts and the MCP totals fall by the same, and the site says how many it left out.
- A Job the head is wrong about at 0.9 or above is hidden until the switch is on or the next tick
  decides otherwise: 0 of 161 read were tech, 14 borderline.
- The Space needs a deploy for `include_non_tech`; an MCP server at version 18 stops on an older Space
  (the agent contract check).
- A new head is a re-measure (the version test) and the stamp is recomputed from its probabilities on
  its first counting tick.
- `refresh-indexes` requires the column like every other Search column, so on an old table a run whose
  `sync` was skipped aborts its publication until a run has migrated it, as for each earlier column.
- The site keeps the switch where it keeps its other filters: in the request, a Saved Set and a digest,
  not in the address bar.
- The first tick after this lands adds the column at `false` through `sync`'s migration, then stamps
  59,523 rows at 0.08 MB, and the next `refresh-indexes` builds its bitmap.
