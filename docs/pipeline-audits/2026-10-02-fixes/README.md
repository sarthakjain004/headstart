# Pipeline reliability fixes and verified diagnoses — 2 October 2026

## Completed fixes

**Paired embedding rewrites:** staged metadata/vectors are synced before a durable rewrite
journal is installed. Recovery finishes both replacements and their manifest count before any
append-tail reconciliation. The publication step independently recovers and checks the store;
unresolved or inconsistent files stop upload even when pruning itself was nonfatal or timed out.
Tests kill a subprocess between replacements and assert exact ID/vector correspondence after
recovery. This prevents new interrupted rewrites; it cannot detect historical corruption whose
files already have equal sizes. [Decision](../../adr/0375-a-positional-embedding-rewrite-is-recovered-before-publication.md).

**Browser shutdown:** harvest closes the transport before interpreter executor shutdown;
`atexit` is only a synchronous process/profile reap. Every worker is bound to its harvest's
lifetime before dispatch, and every origin/Page owns its browser, loop and semaphore. Late
workers cannot start another Chrome or dispatch old pages/release slots into a replacement
lifetime. Closure still runs if unfinished-cost bookkeeping fails. After a timed-out exit, cancellation
cleanup is drained on its owning loop before closure; old registry entries cannot poison a later
shutdown. Baseline process-exit tests
reproduced the executor error; real Chrome tests verify exited PIDs and deleted profiles for
explicit shutdown, process exit and shutdown/reopen with an old context still held.
[Decision](../../adr/0376-a-harvest-closes-browser-transport-before-interpreter-exit.md).

## Spare egress: what it solved

Radancy's final detail HTTP 403 loss fell from **25,938/112,891 (22.98%)** before its spare-egress
opt-in to **4/119,622 (0.00334%)** in the fresh completed control. The original audit already
contained that recovery. Board selection and other source changes confound the exact improvement
size, but existing three-replica Actions probes independently demonstrate rate-sensitive refusals
and recovery limits. Residual sitemap caps, HTTP 404s and unrecognized HTTP 200 content remain.

WP Job Openings does not opt into spare egress. Seven matched live failure-class probes showed
zero rescues; two readable direct listings became bodyless HTTP 202 on WARP, and TLS/HTML faults
remained. A blanket opt-in is unsupported. [Full controls, measurements and caveats](egress-and-avature.md).

## Flapping: the proven mechanisms and missing evidence

All **83 distinct IDs / 86 return events** went through sync eviction after prior Unconfirmed
state and authoritative Board reads. The grace period was not bypassed. Across **664 per-ID
observations**, **82 IDs** disappeared from the parsed source before tech filtering. The remaining
Jabil ID stayed parsed and tech but switched to an unrelated Spanish industrial-engineering body
under its English network-security title/URL; the English gate rejected it for two runs before
the original body returned.

The production Radancy replay proves the lifetime weakness: a sitemap-listed ID with unreadable
detail becomes no Job; a loss below 1% keeps the Board authoritative, so two such reads can evict
the ID. Current checks of all 67 affected Radancy IDs found **52 readable details**, **41 in the
sitemap**, and **nine sitemap-listed URLs returning HTTP 404**. Historical native listing rows
and individual detail responses were not retained, so the exact upstream reason for every one of
the 82 source disappearances cannot be established. They must not all be called false closures.
The corrective design is to preserve "listed but unread" identity separately from parsed fields,
not to lengthen grace indiscriminately or change the English detector.
[Every cohort, competing cause and reproduction](flapping-root-cause.md).

## Booz Allen: a tested opt-in faster path

Increasing four workers alone cannot beat the shared one-request/second pacer. The existing
HTTP 406 fallback only moved later requests; the opt-in mode also retries the initially refused
request under the route cap. `HEADSTART_AVATURE_DUAL_EGRESS=1` selects eight detail streams and
alternate IDs preferring WARP, while every actual direct/spare route keeps one request/second
across listings, details, retries, rotation waits and fallback. If both preferences end up on one
route, they share one budget. Preferred-spare outcomes are separate from wall-rescue metrics.

A matched pooled-client benchmark read **410 Jobs in 410.02s versus 205.09s**, preserving all IDs,
titles and description hashes. A new-code live control read **40/40 Jobs in each mode**, with all
nine parsed fields and all fifteen serialized Job fields equal, in **40.09s versus 23.07s**.
These are local measurements, not an Actions or full-pipeline speedup. The initial unpooled/
submission-paced experiments were inadequate controls and are identified as such in the report.

The workflow exposes the mode through the repository variable of the same name, **unset by
default**. No repository variable was set, production pipeline was dispatched, or daemon mode was
changed. A matched Actions/full-Board control is still needed before default enablement.
[Decision](../../adr/0377-avature-dual-egress-is-opt-in-and-paced-by-the-actual-route.md),
[benchmark and API checks](egress-and-avature.md).

## Validation and review

Final rebased-source validation: **9,904 passed, two skipped, two expected failures** in 184.57s;
Ruff check and format check pass across all 674 Python files; all **558 JavaScript tests** pass. Two independent review
axes checked the code. Their initial browser ownership/cleanup findings and the singular explicit
proxy pacing loophole were reproduced and fixed with regressions; no finding was silently
discarded. [Review outcomes](code-review.md). Captures and scripts remain local under
`experiment/pipeline-reliability-fixes-2026-10-02/` and `experiment/pipeline-fixes-2026-10-02/egress/`;
full private corpus artifacts and raw payloads are not added to Git.
