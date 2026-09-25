# ADR-0228: A Detail pass may send its requests in batches from one warmed tab

**Status:** accepted · **Date:** 2026-09-25 · **Relates to:**
[ADR-0201](0201-a-scraper-states-its-detail-request-once-and-the-base-runs-the-pass.md) (the pass
this adds a third transport to),
[ADR-0139](0139-a-single-source-board-is-its-own-ats.md) (Tesla is a Single source scraper),
[ADR-0056](0056-darwinbox-browser-escalation.md) (navigate once, then in-page requests),
[ADR-0166](0166-gate-the-detail-pass-on-the-tech-filter.md),
[ADR-0048](0048-skip-details-we-already-hold.md) ·
Closes [#553](https://github.com/sarthakjain004/headstart/issues/553)

## Context

Every Tesla Job shipped `description=None` — 8,337 listings on 2026-09-25, our largest
description-less Board — because Akamai refuses any explicitly issued request and a detail was
thought to cost one browser navigation per job. `run_detail_pass` could not help: its two
transports are curl sessions, and curl is what the wall refuses.

Measured live on 2026-09-22 and 2026-09-25 (captures kept locally, not committed): a tab that
has *navigated to one job page* can then fetch other ids with page-JS `fetch()`. Batches of 10, 25
and 50 answered 200 on every id, with descriptions, in 1.5-2.0 s per batch; the same fetches from
the search page, or after ten idle seconds, answered 404. So the cost is one navigation plus a few
hundred short calls, not thousands of navigations.

**The same measurement found the wall's edge, and it is expensive.** A batch of 100 answered 403 on
69 ids and the next, of 200, on all 200. Within minutes `GET /careers/search/` itself was a hard
403 (`Reference #18.…`) from that IP, so an overshoot costs the listing, not just the details.

## Decision

`BaseScraper` gains a third detail transport: a Scraper that sets **`detail_batch_size`** and
implements **`fetch_detail_batch(requests)`** has `run_detail_pass` send its requests that many at a
time, in order, instead of fanning them out. Everything else in the pass is unchanged and still
runs for it: the ADR-0166 tech gate, ADR-0048's held-description skip, the stall bound, the loss
labels, `read_detail`, `FetchedDetails.missing`. A batch member is a response-shaped object or an
`Exception` (labelled like any transport error). `DetailBatchWalled` ends the pass: the batch and
every later one are skipped under one label (`DETAIL_WALLED`), and what landed is kept.

Tesla uses it with a batch of 25 (half the largest size measured to work, which was measured
once), a 0.5 s pause after each batch, and a walled-batch policy:

* **Any 403/429 is a wall; a 404 is not.** The un-warmed tab answers 404, and so does a closed
  posting, so a batch that is *mostly* non-200 without a wall status navigates to a job page again
  and retries once. A second mostly-non-200 answer is itself treated as a wall (404), because the
  un-warmed tab answers 404 and a wall that arrives without a wall status would otherwise cost two
  navigations per batch across the whole board.
* **A wall moves the Chrome onto the spare egress** (`spare_egress.mark_walled`, then a relaunch
  with `--proxy-server=socks5://…` — Chrome fixes its proxy at launch, so a route change is a
  browser restart), and **each further wall rotates the IP**, up to three. The state read takes the
  same path, since the listing shares the origin's fate. With no spare egress, or none left, the
  state read raises as before and the detail pass stops with what it has.

Two guards keep this from costing more than it buys. A batch that raises for any reason other than
a wall (a CDP error, a timeout, a Chrome that will not launch) is labelled per item and the pass
goes on, as the other transports isolate a failing item; the stall bound (ADR-0209) cuts a pass
that lands nothing. And a Scraper built outside the pipeline (`have_details is None`, so the tech
gate and the held skip are both off) skips the pass entirely: it would otherwise fetch every
posting, ~330 batches from one IP.

Rejected: a bespoke pass inside `tesla.py` (smaller, but it re-implements the gate, the skip and
the loss labelling that ADR-0201 exists to keep in one place); and fetching all listings with no
gate (fetches descriptions for jobs the index drops, and again every run).

## Not measured, and why it matters

* **Whether the 403 was the batch size or the cumulative count.** The isolating run was the one
  that met the block. Size 25 is a guess between "50 passed" and "100 failed", not a measured
  optimum, and a full board's worth of batches from one IP was never run.
* **How long the tab's trust lasts.** Bounded below at ~15 s and never seen to expire, so the pass
  re-navigates on evidence (a mostly-404 batch) rather than on a timer.
* **The spare egress carrying Chrome.** Nothing here ran through WARP: the laptop has none and its
  IP was already blocked. The egress policy is tested against a fake daemon and a fake Chrome only.
  The first real check is a `workflow_dispatch` run on Actions, which commits LanceDB and state
  to HF (ADR-0168), so it is a deliberate, asked-for step and not part of this change.

## Consequences

* Other Scrapers are untouched (`detail_batch_size` defaults to None).
* `has_detail_pass = True` puts Tesla in `registry.detail_pass_atses()`, which the embed planner
  (degraded-vector repair), `update_meta` and the description-gap drain read. The largest
  description gap therefore starts to be drained and its title-only vectors repaired, which is
  the point, but it is a wider effect than the scraper.
* Wall-clock, unmeasured on a full board: ~330 batches of 25 at ~2.5 s with the pause is about
  14 minutes for an ungated run; the tech gate and the held skip cut that to the new Jobs of a
  night.
* Until that Actions run, Tesla's first pipeline run is the measurement. A wall stops the detail
  pass and keeps what landed, leaving the listing intact. Only an IP refused before the state
  read fails the Board, as an unreadable Board did before this change.
* `fanout_stats` records nothing for the batch transport: there is no width to read a transport
  decision from.
