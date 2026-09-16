# ADR-0159: A hard cap marks the Board truncated; an approximate ceiling does not

**Status:** accepted · **Date:** 2026-09-16

## Context

`freshteam`'s widget serves at most 1,000 jobs for a tenant and takes no pagination parameter.
`zoho`'s embed serves roughly 750 records. The two look identical from the call site — a listing
that comes back sitting on a round number — and until now both did the same thing: log a line and
carry on, leaving the Board **authoritative**.

That is wrong for one of them. A Board whose list is authoritative has every id it did not return
treated as a delisting: absent from this snapshot, absent from the next, and evicted on the second
consecutive miss (ADR-0083). For a Board permanently pinned at a cap, every posting past the cap is
absent from *every* snapshot, so the eviction is guaranteed rather than probabilistic. Measured over
the five runs of 2026-09-16, three freshteam Boards sat on the cap in every run — `abnhire`,
`simera-talent` and `kalam` — and `freshteam partial` read `0` in all five.

The cost of the other choice is real and is why this was not obvious. Marking a Board truncated
puts it outside ADR-0053's eviction scope, and **ADR-0053 has no drain**: a Board short on every run
never re-enters scope, so its closed postings are served indefinitely (measured elsewhere at 105
dead rows on one Qualcomm Board, oldest 22 days).

## Decision

Split on whether the ceiling is **exact** or **approximate**.

- **Exact, documented hard cap → `mark_truncated`.** The remainder is genuinely unreachable, and
  unreachable identically on every run. `base.mark_truncated`'s own contract already said this
  ("a hard cap … calls `mark_truncated` directly however close to complete the read looks");
  freshteam simply did not follow it. It does now.
- **Approximate ceiling → no mark, log only.** Landing near it is evidence, not proof. A Board with
  exactly that many real openings is indistinguishable from one being cut off, and paying ADR-0053's
  undrained exclusion on a guess is the worse trade. `zoho` keeps its current behaviour unchanged,
  and its existing comment already states this reasoning.

## Consequences

- Three freshteam Boards leave the eviction scope. That is the deliberate price: they stop having
  live postings deleted, and they start accreting closed ones. The blast radius was measured before
  the change rather than assumed — 3 Boards, 12 occurrences across five runs.
- The split is a *property of the ceiling*, not of the ATS, so a future scraper answers one
  question: can I state the cap exactly, and is it the same every run? `trakstar`'s RSS ceiling and
  any other approximate limit stay on the zoho side.
- This does not fix ADR-0053's missing drain, which remains the reason the choice is a trade at all.
  If that drain is ever built, the approximate side becomes much cheaper and this ADR should be
  revisited.

## Alternatives considered

- **Mark both.** Simple and wrong for zoho: it converts a guess into a permanent exclusion.
- **Mark neither** (the status quo). Keeps ADR-0053 clean at the cost of deleting live postings on
  Boards we know are capped — the pipeline silently removing jobs that exist.
- **Mark, with a re-probe every N runs.** The right long-term answer, but it is really a fix to
  ADR-0053's drain and belongs there rather than in each scraper.
