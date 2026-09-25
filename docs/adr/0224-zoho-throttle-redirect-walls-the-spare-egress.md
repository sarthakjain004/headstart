# ADR-0224: Zoho's throttle redirect walls its egress group

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

- A shard's Zoho traffic moves onto the spare egress once throttled. Zoho detail pages run to
  about 1.7 MB, so this adds bandwidth to a route Workday already uses on every shard. The
  per-group `stream_width` clamp also narrows Zoho's fan-out once walled. Watch Zoho's `concurrency
  zoho details` line and the scrape stage's wall-clock time on the runs after merge.
- A shard whose spare egress failed to come up retries a throttled request from the same IP. That
  costs a few seconds of backoff per request and then settles as the labelled throttle loss.
- The yardstick is the `.com throttle shell` count in `scrape_join`'s Zoho loss-cause line: 3,339
  on the run before this change.
