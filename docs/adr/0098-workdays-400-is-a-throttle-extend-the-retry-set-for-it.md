# ADR-0098: Workday's 400 is a throttle — extend the retry set for it, don't widen it

- Status: Accepted
- Date: 2026-08-31
- Follows [ADR-0097](0097-a-postings-id-comes-from-the-listing-never-the-detail.md), which closed
  the *consequence* of these lost details (a lost detail renamed a Job) and explicitly left this,
  the cause, open.
- Relates to [ADR-0063](0063-spare-egress-for-a-spent-origin-budget.md) (the egress fallback
  this deliberately does **not** extend), [ADR-0088](0088-a-lost-detail-is-not-a-truncation.md)
  (a detail gap is classified, never scope-excluded), [ADR-0047](0047-pace-against-the-origin.md)
  (403/405 as bot-wall blips, the precedent for retrying a status that usually means something else)

## Context

`http._TRANSIENT` — 403, 405, 429, 5xx — is what earns a second attempt. **400 was in it nowhere,
and in `workday.egress_fallback_on` nowhere either**, so a Workday 400 was retried never and
rotated the egress never: it settled on the first attempt as a lost detail, silently.

That is not a rare path. In run `33288099045`, 217 Workday Boards logged detail losses and 58 of
them lost details to 400 — `dxctechnology` 837/860 (97%), `caci` 96%, `micron` 89%, `roche`
827/1,210 (68%). Five more Boards lost *listing pages* the same way. The neighbouring run
`33286160766` is worse still: `kohls` 2,147/2,170 (**99%**), `thermofisher` 93%, and `roche` at
918/1,210 — so this is a standing condition, not one bad night.

**It is a throttle, not a malformed request.** Four measurements, each able to have falsified it:

1. **The same postings recover.** Of the 77 roche postings evicted in `33288099045` for a
   failed detail, **75 (97%)** were re-added by `33289938377` — 48 minutes later, the same
   postings. (827 details 400'd that run; 77 is the subset that had also been missed the previous
   scrape and so crossed ADR-0083's grace period.) The 400 is not a property of the request.
2. **The same load from a residential IP never 400s.** A full 1,208-detail pass at production
   concurrency returned `{200: 1124, 429: 84}` on 2026-08-30 — 93% success, and the failures in
   the *retryable* class. A 1,500-detail repeat the next day returned zero 400s **and** zero 429s,
   so the 429 share is the host's mood; the reproducible half of this is the absence of 400s.
3. **It is not concurrency, and not the proxy.** 500 details through the same Cloudflare WARP
   SOCKS5 proxy `spare_egress` uses, on the port it uses: `{200: 500}`. And at raised
   concurrency: `{200: 400}` — though note `curl_cffi.AsyncSession` defaults `max_clients=10` and
   this call site does not raise it, so that first arm actually ran at 10, not the 80 asked for;
   a deliberate re-run at a true 80 and 200 was also all 200s. Not reproducible off-CI by any
   route tried.
4. **It is not board size.** 400-share by size band: 29.6% (0–100 details), 9.9%, 20.1%, 11.0%,
   8.4% (1500+). Small Boards are hit *hardest*, so it is a shard-wide episode catching whatever
   is in flight, not a per-Board rate ceiling.

What survives all four is IP reputation — Cloudflare fronts all of `myworkdayjobs.com` and serves
400 to the Azure ranges Actions runs on.

## Decision

**Add a `retry_on` parameter to `http.fetch`/`fetch_async`, defaulting to the now-public
`http.TRANSIENT`, and have Workday pass `TRANSIENT | {400}`.** `_TRANSIENT` becomes `TRANSIENT`;
`egress_on`'s documented invariant becomes "a subset of `retry_on`".

**Extend per caller; do not widen the shared set.** 400 genuinely means *malformed request* on the
other twenty active ATSes, and none of them has shown this behaviour. Widening `TRANSIENT` would spend
two extra attempts, everywhere, against hosts that will keep saying no. This is the same shape as
ADR-0047's 403/405: a status that means one thing in general and another on one origin.

**The four crawling call sites opt in** — `_post`, `_post_async`, `_job_detail`,
`_job_detail_async` — **and so does the first of `_resolve_instance`'s probes, but not its sweep.**

A wrong data centre answers **422**, never 400: measured 54/54 across five tenants, and
`_resolve_instance`'s own docstring said so all along (*"its host 500s, the CXS API 422s"*). So a
400 on the **hinted** instance is a throttle rejecting the centre that was right, and since
`serves()` accepts only a 200 it would send that Board on a full sweep for nothing. Three attempts
are worth spending to avoid that.

**The sweep behind it stays unretried, and that is a cost decision.** It runs serially over all 18
`INSTANCES`; simulated with every probe returning 400, retrying there costs **54 requests and
~81 s of backoff** before the Board fetches a single job — and still resolves nothing, because a
throttle rejecting one centre rejects them all. An earlier draft of this ADR opted the whole
function in and did not account for that. The residue is a Board that has *both* migrated and been
throttled: it resolves nowhere and reads empty. Narrow, and the ADR-0046 collapse guard already
holds its rows against exactly that.

### This change measures itself, because one thing could not be measured before shipping

**Unknown at merge time: whether an *immediate* retry recovers a CI 400.** The 97% recovery
evidence above is *next-run* — hours later — and the 400 could not be reproduced from outside CI
by any route tried, so retry efficacy could not be established locally. If a throttle episode
outlasts the ladder — 3 attempts, and for a bare 400 that is about 4.5 s of backoff, not the
30 s `_MAX_RETRY_AFTER` ceiling a `Retry-After` header could buy — then retries will not
recover it and only a different
egress would.

Rather than guess, the change is instrumented to answer it. A retried 400 is counted under its own
class, **`400-throttle`**, in the `http.retry_stats()` totals `scrape_run` already prints per
shard. Read it against the settled 400s in the same shard's `N of M detail(s) failed mid-crawl
(HTTP 400 xS)` lines:

- every 400 that *stays* failed burns exactly 2 retries (`_ATTEMPTS = 3`), so it contributes `2S`
  to the counter — unless a rotation extended that request's budget, which cannot happen on a 400
  alone (400 is not in `egress_fallback_on`, so `_rotate_for` never fires for one) but can on a
  request that met a 429 first;
- **`400-throttle` ≈ 2S means nothing recovered — revert this;**
- **`400-throttle` >> 2S means retries are recovering details.** The gap is not a clean count of
  them: a recovered 400 cost 1 retry if the second attempt worked and 2 if the third did, so the
  gap over-states recoveries by up to 2x. Direction is reliable; magnitude is a bound.
- Two known biases, both toward *keep*, so treat a marginal result as a revert: listing-pass 400s
  are counted in `400-throttle` but their settled counterparts are not in the detail lines `S`
  reads, and `_report_detail_losses` prints only its four largest classes, so a small `HTTP 400 xS`
  can be elided entirely.

Both numbers come from the same run, so the ratio measures the retry rather than the ATS's mood
that night — the flaw that makes a bare "rescued" rate uninterpretable.

## Why not the alternatives

1. **Add 400 to `TRANSIENT` globally.** One line instead of a parameter. Rejected: it changes
   behaviour for twenty other active ATSes on evidence from one, and the failure mode is silent — two wasted
   attempts per genuinely-bad request, everywhere, forever.
2. **Rotate the spare egress on 400 too** (add it to `egress_fallback_on`). Tempting, and it is
   what one would try for an IP-reputation problem. Rejected because **both routes get 400s**, so
   changing route has no reason to help.

   The evidence, and its limits. In run `33288099045`, one shard's first 400 line lands ~10 s
   *before* `spare egress: connected`, so those 400s were served to the direct Actions IP. The
   other 14 land after it, two of them by more than three minutes — long enough that the traffic
   was certainly proxied. **The close ones prove less than they look:** the line is a per-Board
   aggregate printed when that Board's detail pass *ends*, so a pass finishing 6 s after connect
   may have done its 400ing before it. Three shards are inside that window. What survives is the
   pair of unambiguous cases at each end — direct 400s in one shard, proxied 400s in two — and
   that pair is enough for "both routes", which is the claim being made.

   An earlier draft said 15 of 15 and concluded "already on the spare egress". It was measured
   against the `walled — rotating` line rather than `connected`, which is not the same event, and
   it drew a stronger conclusion from weaker data. Rotation also spends a bounded IP supply. If
   the counter above shows retries alone are not enough, this is still the next thing to try.
3. **`mark_truncated` on a heavy 400 rate.** ADR-0088 refused this for detail gaps and its
   reasoning is undiminished: the listing is complete, so the Board is not Unauthoritative, and
   ADR-0053's exclusion has no bound and no drain.
4. **Lower the concurrency.** `_DETAIL_STREAMS = 25` is already a measured number, and 400 details
   at 80 streams from a laptop returned zero 400s — concurrency is not what distinguishes the
   environments, so cutting it would pay throughput for nothing.
5. **Give 400 a shorter ladder than other statuses.** Bounds the downside if retries do not work,
   at the cost of a second retry-budget concept in `fetch`. The counter answers the same question
   in one run without the complexity; revisit only if the extra load proves to matter.

## Consequences

- **More requests when Workday is throttling, by construction.** A Board losing 827 details to
  400 will now issue up to ~1,654 extra attempts, spread over the standard backoff. That is the
  cost of finding out; it is bounded by the existing ladder, and the current alternative is losing
  68–97% of the Board's descriptions outright.
- **Slower shards in the worst case.** Backoff on a heavily-throttled Board adds real seconds to
  the scrape's critical path — bounded, because the one place the cost would have compounded (the
  serial 18-instance sweep) is deliberately left unretried; see the Decision. Watch the `scrape` stage's max against `scrape_plan`'s predicted
  makespan; if the two diverge after this, this is the first suspect.
- **`retry_stats()` gains a `400-throttle` line** in every shard summary — the evidence for
  keeping or reverting this, per the reading above.
- **A lost detail still costs ADR-0021 null fields and an ADR-0050 gap entry** when the retry
  does not save it. It no longer costs identity — ADR-0097 saw to that independently, which is
  why this change can be judged on data quality alone.
- **No other caller's *requests* change.** Everyone else passes no `retry_on` and retries exactly
  what they retried before; `TRANSIENT`'s membership is unchanged from `_TRANSIENT`'s.

- **Every caller's retry *telemetry* does change, deliberately.** `_retry_reason` keyed on the log
  text until this change, and libcurl writes numbers into its errors, so a transport failure could
  be filed as a status: replaying the old classifier, `Operation timed out after 30405
  milliseconds` counted as a `405-wall` and `...30502...` as a `5xx`. It is keyed on the status
  now. The correction is small against `network`'s own volume (run `33288099045`: 108,762 network
  retries against 215 `405-wall`) but it is the same order as the `405-wall` bucket itself, so a
  before/after comparison of *those* series across this commit is not like-for-like. It had to be
  fixed here regardless: a change that asks to be judged by a counter cannot ship with the counter
  mis-attributing.
