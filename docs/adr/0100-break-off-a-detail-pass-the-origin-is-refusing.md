# ADR-0100: Break off a detail pass the origin is refusing

- Status: Accepted
- Date: 2026-09-01
- Follows [ADR-0098](0098-workdays-400-is-a-throttle-extend-the-retry-set-for-it.md) (the 400
  face of the same defense; its retry-recovery counter is still being measured, and this ADR is
  deliberately scoped to leave that measurement untouched) and
  [ADR-0099](0099-a-404d-workday-detail-falls-back-to-the-public-pages-json-ld.md) (the 404
  face; a page-recovered detail resets this ADR's streak, since it proves the origin answers).
- Relates to [ADR-0050](0050-a-description-store-so-a-fetched-description-is-never-refetched.md)
  (the healing path this ADR leans on) and
  [ADR-0088](0088-a-lost-detail-is-not-a-truncation.md) (unchanged: a broken-off pass still
  never marks the Board truncated).

## Context

The 12-run error sweep (2026-08-31→09-01) counted 24,601 Workday detail fetches settling at 5xx
after the full 3-attempt ladder. Diagnosis (`experiment/workday-detail-500/LOG.md`, measured not
inferred):

- **Board-episodic**: 24 episodes ≥200 losses carry ~20.6k of the 24.6k; a board loses 93–96%
  of its details in one run and is clean the next.
- **IP-independent**: massgeneralbrigham's episode (run 33448251066) persisted across 10+ fresh
  egress IPs and 14 walls in a 1,153s pass — rotation does not cure it.
- **Not our request and not a dead board**: a wrong instance answers 422 (3/3), a bad path 404;
  a full real-concurrency pass on the worst pure-500 board a day later returned 567/567 with
  zero 5xx.
- **Status-promiscuous**: the same board was hit as listing-400s, ConnectionErrors, and
  detail-500s on different days — the defense wears interchangeable numbers (ADR-0098's lesson
  generalised).
- **Cost**: 172 measured minutes of in-pass grinding across the window, on the same boards the
  wall-clock analysis names as shard floors (pwc 1,845s, MGB 1,153s), spending thousands of
  requests to salvage a few percent.

Nothing recovers in-run. The store (ADR-0050) plus the next scrape recover everything — every
probed episode board served the same details cleanly the next day.

## Decision

After **30 consecutive** detail responses settle at 5xx (`_DETAIL_BREAK_STREAK`), the pass
breaks off: no further detail request is *started* for that Board this run (requests already in
flight on the 25-stream fan-out complete, retry ladders included), the unfetched tail is
counted under its own loss class (`skipped after the 5xx break-off`), and one WARNING names the
break-off. Any successfully obtained detail — parsed CXS or ADR-0099 page recovery — resets the
streak, so only a run of settled 5xx uninterrupted by any successful detail trips it (a raised
network error neither advances nor resets — the diagnosis saw episodes wear ConnectionErrors
too); the steady background 5xx that recovers inside the retry ladder never settles and never
counts.

**5xx only.** A pure 400-storm never trips the breaker: ADR-0098 chose to retry 400s to recover
them and instrumented itself to be judged; an early break-off would truncate exactly the passes
that counter needs to observe. Revisit widening only after that measurement is read.

## Alternatives considered

- **Rotate egress on 500** (add it to `egress_fallback_on`) — falsified before building: the
  MGB episode outlived 10+ fresh IPs.
- **Extend ADR-0099's page fallback to settled 500s** — during an episode this doubles the
  request volume against an origin already refusing, the opposite of what the evidence asks;
  and unlike the 404 class there is no proof the public page works mid-episode.
- **Do nothing** — defensible (the data self-heals), but the grind is a measured straggler
  cause (172 min/window on shard-floor boards) and sustained hammering of a refusing origin
  plausibly deepens the walls ADR-0063/0098 fight.

## Consequences

- An episode board's tail ships title-only for that run — which it did anyway (93–96% were
  dying); the delta is the few percent the grind would have salvaged, healed on the next scrape
  and re-embedded by the ADR-0050 upgrade path.
- The break-off line plus the `skipped after the 5xx break-off x N` class make the mechanism's
  firing rate readable in run logs; if it never fires again, it cost nothing.
- On the threaded `fan_out` fallback the streak increments race (same documented caveat as the
  loss classes) — the trip point is approximate there, exact on the async path.
