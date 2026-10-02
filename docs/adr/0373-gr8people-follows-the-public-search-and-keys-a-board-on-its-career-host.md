# ADR-0373: gr8people follows the public search and keys a Board on its career host

**Status:** accepted · **Date:** 2026-10-02 · **Relates to:** #970, ADR-0001, ADR-0111, ADR-0158, ADR-0188, [measurement](../gr8people/2026-10-02_graphql-measurement.md), [Carrier freshness comparison](../gr8people/2026-10-02_carrier-workday-freshness-comparison.md)

## Context

Teradata's public gr8people Board yields 116 tech-filter matches from 168 postings.
Discovery across gr8people.com, workgr8.com and Teradata's vanity site finds
247 candidate hosts, 57 live rows. Its public GraphQL APIs carry full descriptions.
Oversized page requests silently truncate, the native and Google search indices
can disagree, and departed public sites can retain API postings.

## Decision

- A Board is its lower-case career host. Read `/jobs` before trusting its count;
  a 404/410 is gone, and an unexplained refusal remains unknown.
- Follow the frontend's google-job-discovery flag and kill switch to choose
  searchGoogleJobDiscovery or searchJobPostings. Both are anonymous; fetch no token.
- Page 100 at a time through cursor continuation and compare unique readable ids
  with the stated total. Partial pages retain Jobs and mark truncation. No detail
  pass or source-side tech filter is needed.
- Retain all locations, native workplace type and postedOn. Salary requires a
  stated floor, currency and period; do not assume an OTE period or a posted date.
- Two same-client host pairs have identical public postings. Bury the duplicate
  hosts through the shared-reqs alias ledger, regenerate it with
  gr8people_shared_clients.py, and bump DEDUP_VERSION to 12 for this first ledger.
- Enable gr8people with Carrier parked: 102.95 MB / 384 tech-filter matches =
  0.268 MB per match, under the 2 MB bar.
  These are measured uncompressed-payload proxies, not projected HF storage growth.
- Park `gr8people:carriernoam.workgr8.com`, by the owner's decision on October 2.
  The refreshed Workday Board lists 1,054 postings against gr8people's 4,167;
  all 41 gr8people requisitions dated September 26–October 2 are already in
  Workday, while 268 recent Workday requisitions are missing from gr8people.
  All 36 gr8people-only entries dated within the last month redirect to Workday
  and return S22 without job details; three live controls work, and one missing
  page was confirmed in the browser. Revisit after a stale-feed audit, not from
  a higher raw posting count alone. Carrier's held Workday Board stays active.

## Alternatives considered

A bare vendor label cannot address a vanity host or distinguish the two vendor
domains. Sitemap-only misses nine Teradata jobs. A global native search reads two
deleted Randstad nodes; global Google search fails on Carrier. Requesting more
than 100 can lie about the next page. Per-job HTML adds requests for fields the
listing already contains. Grouping aliases by org alone merges independent ActOne
brands, and redirect-only grouping misses both measured client twins.

## Consequences

The committed ledger has 247 rows (57 live, 96 dead, 94 unknown), with two aliases.
With Carrier parked, gr8people adds 54 Scrapable Boards and 37 Hiring Boards.
Repeat the shared-client scan after refreshing the ledger. Vanity-host
discovery and the 94 unresolved candidates remain coverage limits. Classifier tech
matches are not a manual role census. Post-pipeline, re-run live-index verification
for gr8people; the current harness has zero semantic failures and a clean coverage
gate, but unrelated existing job-link failures prevent a globally clean run.
