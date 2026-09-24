# ADR-0207: The served description follows the posting

**Status:** accepted · **Date:** 2026-09-24 · **Amends:**
[ADR-0104](0104-a-keyword-filter-with-a-scope-map-and-a-stored-description-column.md) (§3: the
`description` column is compared after all, against the corpus instead of the store's meta, and
`sync --backfill-descriptions` is removed) · **Relates to:**
[ADR-0021](0021-re-embed-on-content-change.md) (re-embedding an edited Job stays deferred),
[ADR-0050](0050-persist-descriptions-across-runs.md) (measures the edit churn it left unmeasured),
[ADR-0061](0061-refreshable-metadata.md), [ADR-0062](0062-drain-the-description-gap.md),
[ADR-0089](0089-the-description-store-holds-text-not-verdicts.md),
[ADR-0168](0168-delete-the-orphaned-blobs-dont-ask-for-them-to-be-collected.md),
[ADR-0208](0208-a-failed-zoho-detail-keeps-the-held-description.md) (removes the Zoho flip first)

## Context

The owner's rule: *"if the description fetch succeeded and differs from what we have in the table
then we need to replace it with the new description, companies are allowed to change description
of the job postings."*

The table did not follow that rule. A sample on 2026-09-24 found 25 of 147 live Greenhouse postings
first seen before 09-10 serving older text than the posting carries now. One example is
`greenhouse:undercontrolroboticsinc:4259025009`. The table served a revision with no pay line,
while its `min_salary_annual` of 72,800 came from the newer text ("Pay: $35–$45/hour").

Each step of a fetched description's path was traced to find where an edit was lost:

| Step | Takes an edited text? |
|---|---|
| scrape → `data/jobs/tech` | yes, whenever the scraper fetches the description |
| `update_descriptions` (ADR-0050 store) | **yes**: `held.get(id) != fresh` writes it to the run's fragment, and last write wins |
| ADR-0062 re-derive queue | **yes**: the same `learned` list is queued for every already-embedded id |
| `update_meta` | **yes**: a queued row re-runs the experience, salary and remote cascade from the store's new text |
| `index sync` → `_refresh_metadata` | **no** |

`_refresh_metadata` rewrote a row only when its store metadata differed from the table's. Under
ADR-0104 §3 the `description` column was carried across that rewrite and never compared. A
rewrite did pick up the corpus's newer text. An edit that moved no derived value never caused
one, though, so the old text stayed. An edit that did move a value landed only if that run's
corpus carried the Job. If the value changed later, in a version sweep while the Board sat out
the slice, the numbers updated and the text stayed old. That path fits the example: the store
has held its newer text since before the last compaction, and its `salary_source` is `regex`.

### Measured churn

**The store, per run.** The HF store was read on 2026-09-24. It holds 7 fragments per busy ATS
since its last compaction, one per run. Those fragments hold **6,115 entries that replace held
text**, about **874 per run**. None of them differs only by whitespace.

- **Zoho** accounts for 4,784 of them. That is not editing: 1,527 of the 2,357 changed Zoho ids
  went back to an earlier text. The text alternates between the detail page's record and the
  bare listing record whenever the detail fetch fails. The two differ in the appended `Salary:`
  line alone for 364 of the 2,357 ids, and in the rendered body too (inline CSS, spacing) for
  the other 1,993. The detail also
  supplies facts such as `salary`, the posting date and the state, so a flip often moves the
  row's metadata as well, and such a row was already being rewritten. How often was not
  measured.
- **The rest are real edits**, about 190 per run. Samples show added pay lines, rewritten
  sections and changed locations. The largest contributors are Workday (333), Greenhouse (227),
  SuccessFactors (217), Lever (81) and Ashby (72).

The latest run (36021294272) queued 844 already-embedded ids to re-derive. That is the same order.

**Without the Zoho flip.** ADR-0208 (#639, merged first) stops a failed Zoho detail from
replacing held text. What is left is the 1,331 replacements on other ATSes plus at most 830 Zoho
ids that changed without going back, some of them a one-time listing-to-detail upgrade: about
**190 to 310 per run**.

**The table's backlog.** The served table as of run 35998606646 (520,566 rows) was compared with
the store's current text:

- **25,690 rows (4.9%)** serve a description other than the store's current one. The top ATSes
  are Greenhouse 6,942 (14.0%), Ashby 4,652 (18.3%), Workday 4,462 and iCIMS 2,366.
- None of the Greenhouse, Ashby or Zoho differences is whitespace-only; samples read as edits.
- Only 2 rows are null while the store holds text, because `backfill-from-store` has run.

## Decision

**`_refresh_metadata` compares the row's description against this run's corpus.** A row is
rewritten when its store metadata moved (unchanged) **or** when the corpus carries non-empty text
that differs from the row's. A null row that gains text counts as a difference too. The rewrite
serves the corpus's text, keeps `first_seen`, and takes the vector from the store as before.

- **An empty fetch is never a reason.** `_corpus_texts` yields only rows with non-empty stripped
  text, so a corpus row with none leaves the row exactly as it was. A completed fetch is not
  proof that a posting has no description (ADR-0050, ADR-0089).
- **Text restored from the store counts.** Suppose a run skips or fails a Job's fetch.
  `update_descriptions` then writes the store's text into the corpus row. That text is the last
  successful fetch, and the store is never behind the table, because every fresh text reaches the
  store in the join before the merge reads it. So a row whose table text is older than the store's
  is brought up to date whenever its Board is in the slice. This covers ATSes whose detail pass
  skips held Jobs too. It is how the 25,690-row backlog drains.
- **The store and the derived fields needed no change.** They already took the edit, as the table
  above shows. The fix is the one missing step.
- **One corpus pass, no second copy of the corpus.** The table scan already held every row's
  description. The corpus pass keeps the text only for rows being rewritten.
- **`sync --backfill-descriptions` is removed.** It rewrote a null row whose corpus carried text.
  That is now a special case of the rule above, so the flag had nothing left to do. No workflow
  passed it. `backfill-from-store` stays: it reaches rows whose Board never enters a slice.
- **The vector is not re-embedded.** This is ADR-0021's deferral, kept on purpose because it
  costs embedding budget. A replaced description can sit beside a vector built from an older
  revision. The Keyword filter follows the edit; semantic ranking does not. To re-embed on edit
  would cost about 190 to 310 embeds a run once the Zoho flip is gone (ADR-0208).
- **The log says why.** The rewrite line adds `N for an edited description and M filled where it
  had none (ADR-0207)`, so the churn can be read per run.
- **`update_descriptions` counts the churn at its source.** Every run logs, per ATS and zero
  included, `{ats}: replaced N held description(s) with different text, R of them back to the
  text held before`, and the step summary carries the totals. A replacement back to the text held
  before is a flip, not an edit, so the two stay apart in the log.
- **A per-Job change count is kept in `data/state/description_changes.tsv.gz`.** Each line is
  `id`, the number of times a fetch replaced the Job's held text, and a 16-hex SHA-1 of the text
  held before the last replacement, which is what tells a flip from a new revision. Only Jobs that
  have changed at least once are listed. It is a state ledger rather than a field on the store's
  records, because every reader of the store expects `{id, description}` and the store is
  append-only. It grows with distinct changed Jobs: about 6,000 ids in the week measured, most of
  them Zoho's, so on the order of 300 KB gzipped, rewritten each run beside the rest of
  `data/state`. Nothing prunes it yet; a Job that leaves the index keeps its line.

## Cost

HF storage is the binding cost (ADR-0168), so each extra row rewritten was costed. The served
table's data files hold **5.65 KB per physical row**: 3.12 GB over 552,618 rows, measured on the
same snapshot. That covers the vector, the description and the metadata.

- **Steady state: about 190 to 310 extra rows a run, at most 1.75 MB of new Lance data**, once
  ADR-0208 has removed the Zoho flip. Without it the bound was 874 rows, 4.9 MB. Today a run writes about
  4,100 rows (2,342 rewritten and 1,757 added in run 36021294272), about 23 MB. The embedding
  store rewrites 3.31 GB a run (ADR-0168), which dwarfs both.
- **Once: about 25,700 rows, or about 145 MB.** Most of it lands on the first run. The last run's
  corpus covered 454,398 Jobs, most of the table. Each 2,048-row batch is a delete-then-add, so
  it also leaves deletion files. The merge already asks for a compaction when `_deletions` fills.
  After that, the old fragments go through the reclaim path like any others.
- **Time:** the run above spent about 26 s on the whole refresh for 2,342 rows. The one-time
  25,700 rows is minutes inside the merge job's budget.

## Consequences

- The Keyword filter searches the posting's current text wherever a recent run fetched it. The
  table's text now agrees with the salary, experience and remote values derived from that text.
- **Not covered: an edit on an ATS that skips a held Job's detail fetch.** The detail pass never
  runs again for a Job the store holds, so the edit is never fetched. The owner's rule covers
  only fetches that succeeded. The ATSes are those whose pass is gated on `needs_detail`:
  **ADP, Apple, Cornerstone, Eightfold, Phenom and Zwayam**. Their store fragments hold 0
  replacements across the 7 runs read. Covering them means re-fetching held details on some
  cadence, which is a request-budget decision this ADR does not make.
- Zoho's alternating text would have reached the served description on every flip. ADR-0208
  fixed the flip at its source first, so it does not.
- An edited description is still embedded as the revision it was first built from (ADR-0021).
