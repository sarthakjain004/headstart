# Spare egress and Avature throughput: verified follow-up (2026-10-02)

## Scope and controls

Read-only Actions logs, an existing three-replica runner diagnostic, and paired local requests through an **already connected** WARP proxy. No daemon mode, registration, network setting, pipeline dispatch or production state changed. Scripts and captures: `experiment/pipeline-fixes-2026-10-02/egress/`. Local Python explicitly imports this worktree's `src` via `PYTHONPATH`.

Control run [36570947232](https://github.com/sarthakjain004/headstart/actions/runs/36570947232) began September 29 12:51 UTC at `2f6866d0b33de41412a8c50215565ac22f283343`, before Radancy's fallback commit `73a9f399` (September 29 15:11 UTC). Current completed run [36990873982](https://github.com/sarthakjain004/headstart/actions/runs/36990873982) began October 2 09:37 UTC at `48ff7c144b212b876e7e7316abf0cfe6d9e05a75`. This is a before/after observation, not a randomized experiment: Board selection and corpus differ, and Radancy also changed employment-type parsing in this source range. Its measured transport change is the 403 spare-egress opt-in. The original audited runs already contained the fallback.

## Radancy: the old 403 loss is substantially recovered; residual partial Boards remain

| Metric | Before fallback | Current completed run |
|---|---:|---:|
| Detail attempts | 112,891 | 119,622 |
| All detail loss events | 26,155 (23.17%) | 214 (0.179%) |
| Final HTTP 403 events | 25,938 (22.98%) | 4 (0.00334%) |
| Final HTTP 404 events | 213 | 190 |
| HTTP 200 with missing recognized detail markup | 4 | 20 |
| Boards attempted / failed / partial | 165 / 4 / 58 | 173 / 0 / 19 |
| Radancy-triggered rotation log lines | 0 | 149 |

The original eight-execution audit had 2,857 detail losses / 950,759 attempts (0.3005%), including just 60 final HTTP 403s (0.00631%). Thus its 164 partial Boards / 1,381 attempts are **remaining problems after spare egress**, not the earlier 23% HTTP refusal loss. A partial Board is a different unit from a lost detail request: sitemap caps and count mismatches also mark partials. Rotations are retry-path evidence, not proof that every rotation obtains a new IP or every initially refused request is recovered.

Independently downloaded existing diagnostic [36605443835](https://github.com/sarthakjain004/headstart/actions/runs/36605443835), three replicas on September 29, provides stronger mechanism evidence than comparing totals:

- All six sequential direct walks (Jabil and Sysco, three replicas) read 300 pages at 1 request/second without refusal.
- Jabil's direct 16-stream bursts first refused at request 260, 260 and 266. Its WARP bursts first refused at 237 and 257 on two replicas; the third read 300.
- All three rotations recorded different hashed WARP addresses. Post-rotation bursts were refused again near 258–263 on two replicas. The first replica's shorter 161/179-page walks passed; shorter samples do not establish sustained capacity.
- Three client variants and delayed retries gave mixed outcomes on the same refused URLs; control Petco remained readable. This establishes rate-sensitive, front-specific behavior and refutes treating WARP as unlimited capacity. Different clients occasionally pass on the same address shortly after a scraper refusal, so a purely address-only rule is also unproven. The probe does not identify Akamai's exact internal rule.

## WP Job Openings: spare egress has not solved its failures

The scraper has **no** `egress_fallback_on`. Other providers' tunnel activity does not route these requests through it. Current run: 131 failed Boards / 1,946 attempted (6.73%); 53 Timeout, 52 BoardUnreadable, 17 HTTPError, 6 CertificateVerifyError and three other transport failures. Before-control failures were 71/2,115 (3.36%); these differently selected populations are not a causal trend measurement.

The current run's 343/10,641 detail loss events comprise 291 recognized-markup failures on HTTP 200, 25 HTTP 404, 13 HTTP 202, six JSON-LD parsing failures, four timeouts and four server errors. This distribution does not establish an address wall.

Bounded paired requests on October 2 use exactly the scraper's `_page_url(1)` and Chrome-impersonated client with `headstart/0.1` User-Agent. Seven Boards were selected from prior failure classes, not sampled to estimate all Boards:

| Board | Direct | Existing WARP proxy |
|---|---|---|
| m-forte.co.ug | 200, non-JSON HTML | 200, non-JSON HTML |
| t2fitness.co.uk | 200 JSON list | 200 JSON list |
| ikwezimining.com | TLS certificate verification error | Same verification error |
| addon.global | 200 JSON list | 200 JSON list |
| www.manageratempo.com | 200 JSON list | 202, non-JSON |
| systech-inc.net | 200 JSON list | 202, non-JSON |
| www.revantha.co.in | 200 JSON list | 200 JSON list |

Two sampled sites become unreadable through spare egress, and none is rescued in this sample. Some historical timeouts no longer reproduce on either path. Do not infer their origin-side cause or label them dead. A provider-wide opt-in is unsupported by this evidence and can worsen readability.

## Booz Allen: workers cannot outrun the process-wide pacer

Current source has four detail workers/streams, process-wide `Pacer(1.0)`, and **already** opts into spare egress on HTTP 406. Current run reads 1,347 Jobs in 1,356 seconds. The prior audit repeatedly measured approximately one request/second at four streams. Increasing workers alone preserves that ceiling.

The September 26 source measurement observed a ~350-request burst refilling at ~1.2 requests/second, with a sustained 2/second stream refused on request 893 after 446 seconds. A 40- or even 300-request faster burst can pass and still fail at production volume. Raising the single-IP rate from a short sample is not justified.

An actual route split must pace **the concrete route after fallback**: direct requests that hit 406 move to WARP and must share the same WARP budget as requests preferring that route. An unavailable spare lane must share the direct budget. Independent lane pacers would double-spend one IP after either collapse. Current Avature pacing wraps the high-level fetch; internal HTTP retry attempts occur inside that wrapper, so a safe route-aware extension belongs before each actual send in the shared HTTP sync/async drivers. It must retain tunnel admission and rotation/drain handling.

The measured local route controls below establish readability and throughput for this machine, not Actions' address reputation or a verified production optimization. The default scraper pacing and concurrency remain unchanged. After a faithful pooled control passed, an explicit opt-in implementation was added below.

### Local paired route controls

Three Radancy fronts (Jabil, Sysco, Staples) each returned a readable sitemap on both routes; three matched detail URLs per front returned HTTP 200 and parsed titles/descriptions on both routes (18 details total). This is local readability evidence only. Historical Actions diagnostic results above remain the evidence about runner refusals.

Booz Allen's first 40 matched URLs at four workers with one-second request spacing yielded 39 readable HTTP 200s plus one timeout direct, and 40 readable HTTP 200s through WARP. Direct took 45.10 seconds, WARP 41.12 seconds. This is not a above-burst sustainability test.

The actual Booz Allen JobDetail URLs use `careers.boozallen.com`, a vanity CNAME to `portalsboozallen.avature.net`, then `boozallen.avature.net` / `iatsapp-prod-en12.avature.net`. A current AAAA lookup returns that CNAME chain without IPv6 addresses. Consequently the deep IPv6 pool measured for Radancy is not evidence of equivalent Avature rotation capacity. The local trace endpoint confirms WARP `on`; an IPv4-only trace to `https://1.1.1.1/cdn-cgi/trace` shows a distinct IPv4 route, recorded locally in `ipv4-traces.json`. An IP observed by Cloudflare's trace is not independent proof of the exact IP the employer sees.

### First 410-ID two-route experiment and pacing correction

The first sustained local experiment returned readable titles/descriptions for all 410 WARP requests and 369/410 direct requests; 41 direct requests timed out at the diagnostic's 20-second deadline. The 369 jointly readable pages had identical parsed title and description hash. Total experiment duration was approximately 412 seconds. No HTTP 406 was observed.

However, inspection of recorded request-start timestamps found an important harness limitation: pacing **submission** to four worker threads can still bunch actual starts when previous timeouts create a queue. Therefore this experiment does **not** demonstrate strict one-request/second actual-send pacing and is not sufficient evidence for enabling a two-route production configuration. The corrected harness `avature-dual-paced.py` calls the repository's shared route-specific `Pacer.wait()` **inside each worker immediately before the request**. It repeats the same 410-ID shape at the same 20-second timeout; results are kept separately so the failed control is not overwritten. Source listing changes between controls are recorded by their actual URLs/IDs; do not assume two independently obtained listing prefixes are identical without comparing them.

The corrected unpooled control returned WARP 410/410 readable HTTP 200 pages and direct 380/410, with 30 direct 20-second timeouts; durations were 410.53 and 449.05 seconds. Recorded minimum start gaps were 0.9988 and 0.9951 seconds respectively (scheduler timing jitter). These remain fresh-session diagnostic results, whereas production multiplexes a pooled `AsyncSession`; they establish neither production benefit nor harm. The faithful pooled control uses the same frozen 410 URLs/IDs in baseline and split-route arms, production Chrome impersonation, cookie discard, disabled redirects and 60-second deadline, and records each actual start and parsed title/description hash.


## Valid pooled control and tested opt-in implementation

The production-shaped control froze **the exact same 410 URLs/IDs** and used one Chrome-impersonated `AsyncSession` per arm, `discard_cookies=True`, `allow_redirects=False`, a 60-second deadline and actual-send pacing inside each semaphore slot. It did not use the module-level fresh-session client. Neither arm retried a request; all settled successfully.

| Arm | Streams | Actual route budget | Readable / attempted | Wall time |
|---|---:|---|---:|---:|
| Baseline | 4 | Direct 1 request/s | 410 / 410 | 410.0169 s |
| Split routes | 8 | Direct 1 request/s + WARP 1 request/s | 410 / 410 | 205.0860 s |

All 410 IDs, parsed titles and description SHA-256 values match. The split arm sent 205 requests per route; its elapsed time was **1.9992 times faster locally**. Actual start spacing differed from one second by at most approximately 6 ms of scheduler jitter. Before/after IPv4-only trace readings stayed distinct and stable, WARP `on`. The direct fresh-session timeout symptom did **not** reproduce in this pooled baseline.

This is a bounded local sample, **not** proof of the 1,347-page Actions run's throughput or route reputation. Each split lane sent only 205 requests; the separate 410-per-route unpooled controls establish local readability at that volume but are not pooled production controls. The source measurement's sustainable per-IP rate is retained. IPv4 pool diversity and runner behavior remain unverified.

The implementation is **disabled by default**. `HEADSTART_AVATURE_DUAL_EGRESS=1` enables eight Avature detail streams and directs odd numeric posting IDs to prefer spare egress. Even IDs begin on the direct route. All Avature requests share one `http.RoutePacer(1.0)` per process: routes are resolved after every wait, including HTTP retries, and only an actually due start claims a slot. A direct wall joins the existing spare budget; an unavailable spare joins the direct budget. A changed proxy address does not create another allowance. Preferred requests wait on the existing rotation gate and retain tunnel in-flight accounting. Proactively proxied requests get separate counters instead of inflating the wall-rescue rate.

The default path preserves four streams and its original process-wide `Pacer(1.0)`. A source/test discovery adds an important caveat to the older documentation: the existing `{406}` opt-in marks a wall, but HTTP's default transient set omits 406, so the current 406 response fails and only subsequent requests move. **The new enabled path explicitly adds 406 to its retry set**; that retry also draws from the actual-route budget. No other provider's retry set changes.

Validation: the new regression command initially failed four tests, then the fixed code passed **257 focused tests** across `test_avature_dual_egress.py`, `test_network_http.py`, `test_network_spare_egress.py` and `test_avature.py`; Ruff and `git diff --check` passed. Tests exercise both drivers, first-406 recovery, route convergence, unavailable WARP, cross-Board shared budgets, route changes during pacing, rotation-gate waiting and rescue-counter separation. `avature-pooled.json` retains the matched control; `opt-in-red.log` and `opt-in-tests.log` retain test outcomes.

Before enabling the option on pipeline runners, run an isolated read-only diagnostic with the same frozen URL population and actual route/IP measurements. A full pipeline is a mutating state publication and was not dispatched for this measurement.

The actual new-API live smoke used the already connected proxy through an attached in-memory daemon adapter (so it issued **no** daemon/configuration commands). First, 40/40 enabled requests matched prior titles and description hashes. A subsequent matched **default versus enabled** 40-ID control compared every `page_fields` value and every serialized `Job` field, including location, department, employment type, requisition, posted time and remote state. Both arms parsed 40/40 HTTP 200 responses; **zero field differences** were found. `scraped_at` was deliberately pinned to the same value in both arms, so the comparison does not mistake fetch time for content drift. Captures: `avature-opt-in-live.json` and `avature-opt-in-fields.json`. The latter control took 40.09 seconds default and 23.07 enabled; ID parity splits this small sample unevenly, unlike the exactly balanced 410-ID prototype. This smoke verifies the new code path, not runner behavior or full-population field equivalence.
