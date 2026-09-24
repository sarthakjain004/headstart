# ADR-0208: A failed Zoho detail keeps the held description

**Status:** accepted · **Date:** 2026-09-24 · **Relates to:**
[ADR-0050](0050-persist-descriptions-across-runs.md) (the store restores held text into the
corpus), [ADR-0089](0089-the-description-store-holds-text-not-verdicts.md),
[ADR-0207](0207-the-served-description-follows-the-posting.md) (the served description follows
changed text, so a flip would reach the table)

## Context

Zoho fetches every posting's detail page, because Salary lives only there. When a detail fetch
failed, `parse` fell back to the bare listing record. That record also carries a
`Job_Description`, but a different rendering of it: live on 2026-09-24, 34 of 40 listing/detail
pairs across 5 tenants produced different `Job.description` text.

Detail passes fail in bulk on some runs and not on others. Run 36021294272 lost the detail for
all of 143 Zoho Boards' postings (6,212), mostly as "no jobs blob on the page", and for 2,478 of
9,283 postings on 62 more Boards. Fetched one at a time by hand on the same day, 40 of 40
(24hours-group, 492/575 lost in that run) and 15 of 15 (jobberman, 526/526 lost) parsed on both
of two attempts. So a failure is a property of the run, not of the posting.

Each failure therefore replaced the stored detail text with the listing's rendering, and the next
successful fetch replaced it back. In the 7 runs of the description store read on 2026-09-24,
Zoho accounted for 4,784 of 6,115 replacements, and 1,527 of its 2,357 changed ids had gone back
to an earlier text. Each flip was written to the store, queued to re-derive and, under ADR-0207,
rewritten in the served table.

## Decision

**A Job whose description the store holds gets no description from a failed detail.** `parse`
leaves `description` as None when the detail is missing and `needs_detail` says the text is held.
`update_descriptions` then writes the held text back into the corpus, so the store, the re-derive
queue and the table see no change.

**A Job the store does not hold still falls back to the listing.** Its listing text is the best
text there is, and without it a new Job would be embedded from its title alone and re-embedded
once the detail lands. The first successful detail then replaces the listing text once, which is
a single change rather than a flip.

Outside the pipeline `have_details` is None, so every Job reads as unheld and the fallback is
unchanged for local scrapes.

## Consequences

- The Zoho flip leaves the store's replacement count. Without it, the store's replacements in
  those 7 runs were 1,331 on other ATSes plus at most 830 Zoho ids that changed without going
  back (some of them a one-time listing-to-detail upgrade), so about 190 to 310 a run.
- Other fields still fall back to the listing on a failed detail. The `salary` fact, which exists
  only on the detail page, still reads None on such a run. That is a separate flip in
  `update_meta`'s fact sync, not addressed here.
- Why whole detail passes fail from CI but not by hand is not diagnosed here.
