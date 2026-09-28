# ADR-0257: Jobvite walks a short Board again and gates its detail pages on the listing title

**Status:** accepted · **Date:** 2026-09-28 · **Amends:**
[ADR-0231](0231-a-jobvite-job-is-read-from-its-detail-page-or-not-at-all.md) · **Relates to:**
[ADR-0166](0166-gate-the-detail-pass-on-the-tech-filter.md) (the pre-detail tech gate),
[ADR-0121](0121-a-negligible-shortfall-is-still-an-authoritative-list.md),
[ADR-0083](0083-evict-only-on-a-second-consecutive-absence.md)

## Context

The 2026-09-28 scraper critique found two faults in Jobvite's scraper, and both reproduced live.

1. **`/search` pagination is unstable between requests.** On `fprs` the counter read `1-50 of
   1,783` on every page. Four walks a few minutes apart read 1,765, 1,735, 1,738 and 1,765
   distinct ids, and their union grew 1,765 → 1,772 → 1,777 → 1,783. A posting can fill two slots
   of one walk, and the posting it displaces is then on no page of that walk. A missed id was a
   live posting. The 2026-09-07 note read this shortfall as "one posting in two slots", counting
   the repeated slot but not the posting it pushed out.
   - One walk served as the Board, so live postings flapped: ADR-0083 evicts a posting on its
     second consecutive miss.
   - Nothing logged or marked the short walk.
2. **Every posting's detail page was fetched on every run, for about 7% tech** (registry note).
   The anchor of a classic listing row states the title:
   - On 30 random live Boards, 1,242 of 1,378 postings had a plain title, and every one of the
     1,242 equalled the page's title.
   - The department is on the page alone, and `tech_filter`'s rule 4 promotes a vague title under
     a technical department ("Estimator" under "Engineering").
   - A title-only gate would have skipped 42 of the 247 tech postings on those Boards.

## Decision

- **Walk again while short.** When the ids read fall short of the counter, the Board is walked
  again, up to four walks, while each walk still finds new ids.
  - If walks are still finding new ids at the cap, the Board goes through
    `mark_truncated_unless_negligible` (ADR-0121).
  - If a walk finds nothing new, the shortfall is stable. It is only logged, as before: nothing
    shows a posting is missing, and ADR-0053's exclusion has no drain.
  - A walk also stops on a next link back to any page it already served. The old rule, stop on a
    page with no new ids, would end every re-walk on its first page.
- **Gate on the listing title under the most-promoting department.** The tech gate asks
  `is_tech(listing title, "Software Engineering")`.
  - On the 30 Boards it skipped 515 of the 1,242 titled pages and no tech posting.
  - A row with no plain title is always fetched, because `is_tech(None, …)` keeps it under that
    department.
- **A gated posting ships as a Job carrying only its listing title and link.** ADR-0231's "from
  its detail page or not at all" still holds for every Job the gate keeps. A gated Job is non-tech
  under any department, so the tech filter drops it again downstream. Shipping it keeps the full
  set whole instead of shrinking it by the gate.

## Consequences

- A Board short on its first walk pays for up to three more. On `fprs` two walks reached the
  counter, and the listing took 33 s instead of 18 s.
- The gate is safe only while "Software Engineering" promotes at least as much as any real
  department. A test pins that on the measured departments and titles; a change to rule 4's
  vocabulary that breaks it fails there.
- Gated Jobs carry no description, location, date or department in `data/jobs/jobvite.jsonl`.
