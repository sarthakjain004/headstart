# HeadStart launch verification — 2026-10-02

## Scope

Owner requested: fix dead application links, allow browsing before sign-in, verify service
capacity/recovery, and remove manually maintained README figures. Trends changes and other
launch items are deferred. Probes use the live public service and no real Account writes.

## Application links

Live `/search?company=Masimo&k=2&strict=1` returned Oracle jobs 4018 and 2562. Both application
URLs followed redirects to `/hcmUI/CandidateExperience/errors/404`, despite HTTP 200. This
confirms #873 still affects the product. The retired Board is parked, so the next ingest run
using this code prunes its held rows and cannot scrape them back in. Unpark only when public
applications work. A live search for the historically reported `sphinixusa` returned no rows;
that old issue count is not treated as a current count.

## Anonymous browsing

`/` renders the app without a session. Search, public Trends/Hot and browser-only résumé drafts
are accessible. Sign-in is optional through `/signin`, preserving the selected tab. Account
features and sync controls render only for a signed-in Account; all Account routes remain
protected. The browser preview used the existing test encoder/table (two fixture jobs), not
the production corpus. Google authentication was not exercised against a real account.

## Capacity: not a release pass

One wave at each concurrency 2, 4 and 8, 14 mixed public requests in total: all returned 200,
0.664–1.767 seconds. Requests included semantic Search, browse, total Facets, Trends, Hot and
session status. These warm requests can use caches.

Eight simultaneous distinct semantic queries: five returned 200, three returned 500, in
1.288–1.781 seconds. Sequential retries returned 200 for “Android engineer for medical devices”
but still returned 500 for “Frontend engineer building developer tools” and “Machine learning
engineer for document processing”. This does not establish concurrency alone as the cause.
The live generic 500 responses contain no diagnosis. Do not claim announcement traffic readiness.

These are small controlled probes, not sustained traffic or a measured upper capacity limit.
Account write capacity remains unmeasured: tests did not save records, send alerts, invoke the
résumé-reading gateway or consume real users' quotas.

## Recovery

Pipeline run `36990873982`, main `48ff7c14`, completed successfully. Its merge job
`110800321415` logged “space restarted” at 2026-10-02 10:49:42 UTC. Later public probes answered
successfully. This establishes recovery after that scheduled restart, but does not measure
its outage duration or verify recovery from a crash. The earlier measured 4m13s boot remains
historical evidence, not a new timing. No manual restart was performed just to test recovery.

## README maintenance

The searchable-job badge uses `/badges/jobs`, counted with the same `n_served()` as the site.
Shields reads its JSON endpoint; service responses allow a five-minute cache, while badge and
GitHub caches may lag further. A sleeping/restarting Space can make the badge unavailable.
Schema: https://shields.io/badges/endpoint-badge.

ADR and scraper figures and Board totals are not duplicated in prose. Links lead to the
current ADR directory, scraper registry, and tested Counting Boards glossary. Adding an ADR,
scraper or ledger row no longer requires changing a headline or coverage table in README.
Historical measurements and schema examples remain snapshots, not live statistics.

Local probe outputs and runtime logs are kept under `experiment/launch-readiness/`, gitignored.
Runtime logs must remain private. Production verification after deployment is recorded below
when observed; source changes alone do not establish that stale rows have drained.
