# ATS throughput and recovery follow measured public behavior

**Status:** accepted · **Date:** 2026-10-03 · **Relates to:** 0026, 0158, 0204, 0382, 0383, 0384

## Context

The first three adapters used deliberately low untested start rates, and their archive and
cross-provider comparison passes were incomplete. The owner asked for higher measured
concurrency, spare egress, completed archive discovery and a full employer/title comparison.

## Decision

Recruiterflow's 2,048 listing requests all succeeded in one-Board and 24-Board ramps through
128 concurrent requests on direct and spare routes. A separate detail ramp succeeded through
16. Use eight detail workers and sixteen shared request starts per second. PageUp's direct
throughput flattened between 64 and 128 (many-Board p95 rose from 2.9 to 7.4 seconds); use eight
shared starts per second. Its spare burst timed out at eight, so spare is recovery rather than
its normal throughput route.

Manatal's short ramps also completed 2,048 requests, but a sustained sixteen-starts/second
census received 429 after 1,221 requests and about 79 seconds. Stop on the refusal and retain
the demonstrated sustained two-starts/second API budget. Burst success is not a sustained quota.
Use the public frontend's supported `ordering=-is_pinned_in_career_page,-last_published_at`.
Unstable offsets can still omit IDs: every measured deficit marks the Board incomplete, even
below the shared one-percent tolerance, so missing postings cannot be mistaken for closures.

The shared Fetcher opt-in retries one exhausted connect/timeout/reset failure on an available
spare route, for GET/HEAD only. It does not switch on HTTP responses, DNS/certificate errors,
explicit direct/proxy requests or POSTs; it does not rotate or recursively retry. Existing
scrapers stay unchanged unless opted in. Both transports have regression tests; controlled
primary failures followed by real spare requests returned and parsed public Recruiterflow and
PageUp responses on sync and async paths.

Archive recovery completed all 330 target/crawl entries (33 Common Crawl collections) and all
262 intended Wayback namespace pages. Historical URLs remain candidates until probed. The
comparison covers 363,472 retained new-provider postings against 4,345,946 existing records
and each other, with native IDs/URLs retained for every review candidate. Matching titles are
not aliases: independently checked Avomind, Smartworks, ScaleOps, SPAR and BioCatch sets are
partial, and removing a whole Board would discard unmatched postings.

Content-confirmed PageUp demo Boards are excluded. An immediate HTML meta-refresh is now
included in migration checks: G8 Education and The Star returned HTTP 200 with full/stale
posting data but immediately navigated to generic searches. A single closed sampled Job,
unknown external route, delayed refresh or fragment-addressed destination does not prove a
Board gone.

## Consequences

Full raw measurements, archive manifests, comparison tables and source evidence stay in the
project's ignored experiment folders. The comparison explicitly accounts for 29 IDs below
stable Manatal totals, a changing-count Board, unread advanced surfaces and unrepaired existing
titles; those are not represented as proved absence or as completed reads. No served-data
rewrite, workflow dispatch or title-only deduplication is part of this change.
