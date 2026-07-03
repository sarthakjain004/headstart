# ADR-0016: Async multiplexed fan-out on by default, width 100

- Status: Accepted
- Date: 2026-07-03
- Supersedes the default-posture of [ADR-0015](0015-async-multiplexed-fan-out.md)

## Context

ADR-0015 shipped the HTTP/2-multiplexed detail fan-out as **opt-in** (sync default), to be adopted and
A/B-validated one scraper at a time. That validation is now done: SmartRecruiters measured 1.16×
(matched width) to 1.61× (width 100) faster with **zero** correctness cost; `join` and `rippling` were
confirmed live (0 failures); and the async path is wired into all five detail-fetchers
(SmartRecruiters, Workday, Join, Rippling, Trakstar). The evidence supports making it the default
rather than leaving the win behind an env flag.

## Decision

The async multiplexed detail pass is the **default** for every detail-fetch scraper. The switch is
centralised in `BaseScraper.async_fanout_enabled()` — on unless `HEADSTART_ASYNC_FANOUT=0`.

- **Default width 100** concurrent streams per host (`_DEFAULT_H2_STREAMS`), around the common server
  `MAX_CONCURRENT_STREAMS`. Resolved at call time so it's configurable three ways: the per-call
  `concurrency=` argument, the `HEADSTART_H2_STREAMS` env var, or `run_scrapers --streams N`.
- **Escape hatch:** `HEADSTART_ASYNC_FANOUT=0` (or `run_scrapers --sync`) falls back to the sync
  thread-pool path, which is retained unchanged.
- **Per-host politeness is preserved:** Trakstar passes an explicit `concurrency=_DETAIL_WORKERS` (4),
  so it stays gentle under DataDome regardless of the global width.

## Rejected alternatives

- **Keep it opt-in.** Leaves a measured, no-downside speedup off by default for no reason once the
  per-ATS validation ADR-0015 asked for is done.
- **Remove the sync path entirely.** The sync fallback is cheap to keep and valuable for incident
  response and for hosts that punish concurrency — deleting it trades a one-flag safety net for nothing.
- **Per-ATS default flags.** Overkill: one global default plus Trakstar's explicit width already
  covers the only host that needs special handling.

## Consequences

Production scrapes (`python -m headstart`, `run_scrapers`) now multiplex by default. Width 100 is
aggressive for a host with a low stream cap; the per-call override (Trakstar's precedent) and the
`HEADSTART_H2_STREAMS` knob handle the sensitive cases, and the whole thing reverts with one flag. The
`fan_out_async` mechanism, its contract, and the ADR-0015 measurements all stand — this ADR only flips
the default and sets the width.
