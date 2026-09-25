# ADR-0226: Zoho's throttle redirect walls its egress group

**Status:** accepted · **Date:** 2026-09-25 · **Relates to:**
[ADR-0063](0063-spare-egress-for-a-spent-origin-budget.md) (the spare egress this opts Zoho into),
[ADR-0208](0208-a-failed-zoho-detail-keeps-the-held-description.md) (what a lost Zoho detail
keeps)

## Context

Zoho lost 3,191–5,965 details per run as `no jobs blob on the page`, only in CI. Once #664 labelled
the `.com` throttle page apart, run `36055532061` showed that all of it was the throttle: the old
label disappeared, and `.com throttle shell (page currently unavailable)` counted 3,339 details on
129 Boards (`docs/zoho/2026-09-25_closed-posting-shells.md`).

After about 1,000 detail requests from one client IP, every `*.zohorecruit.com` host 302s a
detail request to `/html/portal.html`. The block lifts after about 7 minutes. Held descriptions
survive through ADR-0208, but every new posting behind the throttle lost its description, salary
and other detail-only fields for that run.

## Decision

Zoho's detail requests are sent with `allow_redirects=False`. `ZohoScraper.egress_fallback_on`
is `{302}`, and the detail request's `retry_on` is `TRANSIENT | {302}`. The first throttle
redirect therefore marks the `zoho` egress group walled, and that request's retry and every later
Zoho request in the shard leave from the spare egress, a different client IP. If the spare egress
is throttled in turn, ADR-0063's rotation moves it again. A 302 that never clears is labelled
through a new `BaseScraper.detail_status_loss` hook as the throttle, not a bare `HTTP 302`.

This is safe only because a 302 is unambiguous on this request. On 2026-09-25, 30 of 30 live
detail pages across `.com`, `.eu` and `.in` Boards, open and closed, answered 200 with no redirect.
The listing request still follows redirects and cannot wall the group.

## Options considered

- **Pace the requests.** Rejected for now: the throttle's window is unknown (about 1,000
  requests, over an unmeasured time). Too slow wastes shard time, and too fast gains nothing.
- **Skip details whose description is already held (ADR-0048).** Rejected. It contradicts the
  2026-08-24 decision to fetch every Zoho detail, because salary is on the detail page only.

## Consequences

- **The wall covers all of Zoho, not just `.com`.** The egress group is `zoho`, but the throttle
  was measured on `.com` hosts only (2,812 of 3,674, and 0 of 1,326 others). Once a shard is
  walled, its `.in`, `.eu` and other details ride the spare egress too. That is about a quarter of
  Zoho's details, roughly 1 GB a shard at ~1.7 MB a page, on a tunnel Workday already uses on
  every shard. Keying the group by data centre is the follow-up if the scrape stage slows.
- **Zoho's fan-out width does not change.** `stream_width` clamps a walled group to at most 12
  streams, and Zoho already runs 6.
- **Without a spare egress it is worse.** A shard whose spare egress did not come up retries each
  throttled request from the same IP, three times where it used to send once. If the throttle
  window is rolling, that may prolong it. The request then settles as the labelled throttle loss.
- **A throttled listing request is not handled.** `/jobs/Careers` still follows redirects, so it
  cannot wall the group. If the throttle also redirects the listing, the Board reads as empty, as it
  did before this change. No run has shown that yet.
- The yardstick is the `.com throttle shell` count in `scrape_join`'s Zoho loss-cause line: 3,339
  on the run before this change.
