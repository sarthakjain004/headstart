# ADR-0278: An Oracle Board serves only what an active career site publishes

**Status:** accepted · **Date:** 2026-09-29 · **Relates to:**
[ADR-0053](0053-scope-eviction-on-scrape-outcome.md) (Unauthoritative Boards),
[ADR-0083](0083-evict-only-on-a-second-consecutive-absence.md) (grace period),
[ADR-0200](0200-a-board-scraped-empty-is-in-the-eviction-scope.md) (an empty Board is in scope),
[ADR-0281](0281-a-lever-board-whose-hosted-pages-are-off-serves-nothing.md) (the Lever case) ·
**Issue:** #873

## Context

An Oracle tenant runs one or more Candidate Experience sites (`CX`, `CX_1`, `CX_6001`, …). The
scraper reads the host-wide requisition listing, with no `siteNumber`, because that is the union
of every site (`docs/oracle/2026-09-08_api-measurement.md` §3). Each Job links to
`/hcmUI/CandidateExperience/en/sites/CX_1/job/{id}`. The site in that link does not matter: the
careers UI redirects it to an active site that publishes the posting.

Two things were not known when the scraper was built:

- **A site can be switched off.** `recruitingCESites` states each site's `StatusCode`,
  `ORA_ACTIVE` or `ORA_INACTIVE`. When no active site publishes a posting, its link redirects to
  `/hcmUI/CandidateExperience/errors/404`.
- **The host-wide listing still carries those postings.** On `egcu.fa.us6` (Masimo) both sites
  are inactive, yet the listing held 102 requisitions (newest posted 2026-09-25). All 38 served
  links went to `/errors/404`. On `eknh.fa.em2` only `CX_6001` is active. The listing held 65
  requisitions, and `CX_6001` publishes 13 of them. Every link was fetched: the 13 opened, and
  the other 52 went to `/errors/404` (65 of 65 matched).

MEASUREMENT_PLACEHOLDER

## Decision

`OracleScraper` asks `recruitingCESites` once per scrape, then:

- **No active site: serve nothing.** It logs `no active career site` and returns `[]`. That is
  the same as ADR-0281's Lever case. The listing did answer, so the Board stays in the eviction
  scope (ADR-0200), and its rows evict through ADR-0083's two consecutive absences. The Board is
  not marked truncated, because an Unauthoritative Board keeps its rows (ADR-0053). It is not a
  gone-strike either: the API still answers, and a Board that turns a site back on is served on
  its next scrape.
- **Some sites inactive: read each active site.** The listing is walked once per active site,
  with `siteNumber`, through the same `_listing` walk, so each keeps its paging, ceiling and
  truncation rules. A posting on two sites is served once.
- **Every site active: read host-wide, as before.** No `siteNumber`, no extra page.
- **Sites unreadable: read host-wide, as before.** One attempt (`_fetch_once`). A 5xx, a 429, a
  request that raises, a body that is not the expected JSON, or an empty site list all fall back
  to the old read. A transient failure can neither empty nor narrow a Board. (The 144 tenants that
  answered an empty site list all listed no postings either.)

Job links are unchanged. The UI already sends `CX_1` to whichever active site publishes the
posting, so there is nothing to re-point.

## Rejected

- **Read the Candidate Experience root instead.** It redirects to `/errors/404` when no site is
  active, so it would catch Masimo. It cannot tell which postings a partly-off tenant still
  publishes, and `eknh` is that case.
- **Ask each Job's link.** One request per Job, where one per Board answers it.
- **Ask an inactive site's listing.** It is not a filter that can be trusted: on `edmn.fa.us2`,
  six inactive sites each answered the full host-wide 1,510, and on `egcu` an inactive site
  answered 0.
- **Park the Board or mark its ledger row dead.** Nothing would bring it back when the tenant
  turns a site on again, and the liveness probe reads the listing, which still answers.

## Consequences

CONSEQUENCES_PLACEHOLDER

- Each scrape spends one more GET per Oracle Board (`recruitingCESites`). A Board with every site
  active, or with a single active site, reads the same number of listing pages as before. A Board
  with several active sites reads one walk per site.
- `scripts/validate/eightfold_backing_boards.py` reads an Oracle backing Board through the same
  method, so a front is buried only onto what the Oracle Board serves.
- Masimo is hiring on Danaher's Workday Board (`danaher.wd1.myworkdayjobs.com/DanaherJobs`, held),
  which listed three Masimo postings on 2026-09-29, two of them also on the Oracle listing. The
  Oracle rows were dead links and would have become duplicates too.
