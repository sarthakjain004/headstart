# zwayam's 403 wall — what it is, and why the full sweep could not settle it

**Date:** 2026-09-17. **Why:** zwayam fails 47–71% of its Boards in every pipeline run and is
55–73% of all board errors (see `docs/pipeline/2026-09-17_five-run-log-review.md` §4).

## The wall is a cumulative per-IP quota, not a dead Board and not a concurrency limit

| probe | result |
| --- | --- |
| 14 ledger-live Boards, real scraper, ~34 requests in | **12 of 14 HTTP 403** |
| 10 Boards the fast sweep called `unknown`, serial, 3s apart | **10 of 10 live** |
| 60 distinct Boards, 16-wide concurrency, 60 requests | **200 on all 60** |
| 6 Boards, direct vs WARP, after cooldown | **200 on both routes** |

16-wide concurrency does not trip it; ~3,000 cumulative requests does. So it meters **requests per
IP over a window**, and it clears on cooldown. Slowing down does not buy quota back — a different
address does, which is exactly what the spare egress is for.

## The full sweep is INVALID — do not load it into the ledger

`artifacts/2026-09-17_full-sweep-8workers-WALLED.csv` — all 3,239 rows, 8 workers, via
`scripts/validate/recheck_boards.py` (which reports only and never writes the ledger, by design).

Headline: `303 dead, 120 live, 2816 unknown`. The order effect shows why that is not a measurement:

| quartile (probe order) | dead | live | unknown |
| ---: | ---: | ---: | ---: |
| 1 | 303 | 120 | 386 |
| 2 | 0 | 0 | **809** |
| 3 | 0 | 0 | **809** |
| 4 | 0 | 0 | **809** |

Every settled verdict came from quartile 1; quartiles 2–4 are 809/809 `unknown` each. The probe was
walled partway through the first quartile and never recovered. Transitions confirm it: 2,159
previously-`dead` and 657 previously-`live` rows both collapsed to `unknown`.

## Why spare-egress rotation did not save it

Rotation never fired. Two independent reasons, both deliberate:

- `check_liveness._EGRESS_ON` is `frozenset()` **on purpose** — a request naming an egress group
  *rides* the spare egress once that group is walled, but no single response may *mark* it walled.
  The marking decision lives in the ladder (`_ban_or_rotate`), where Retry-After, challenge markers
  and the redirect chain are all in view.
- `_ban_or_rotate` is reached only from `_on_429`, or from a challenge that `_is_challenge`
  recognises — `cf-mitigated: challenge`, or one of `Vercel Security Checkpoint` /
  `Just a moment...` / `cf-browser-verification` in the body. A bare Akamai 403 is none of these.

So zwayam's 403 falls straight through to `UNKNOWN` with no gate trip and no rotation. `p_zwayam`'s
own docstring still calls the 403 "rare and transient", measured 2026-08-27; the pipeline data and
this capture both falsify that.

WARP is available locally (`warp-cli` connected, `spare_egress.proxy_url()` →
`socks5h://127.0.0.1:40000`), so the missing piece is only the trigger.

## Wired, and the sweep re-run: 2,816 unknown -> 3

`check_liveness` now treats a bare 403 from a `_QUOTA_403` host as a wall and calls
`_ban_or_rotate` (branch `fix/zwayam-403-rotates-egress`). The rung is separate from the challenge
path on purpose: `_is_challenge` still returns False for a bare 403, so an ordinary *forbidden*
from any other host does not start rotating.

The same 3,239-board sweep, re-run with it — `artifacts/2026-09-17_full-sweep-with-rotation.csv`:

| | first sweep (walled) | with rotation |
| --- | ---: | ---: |
| dead | 303 | **2,382** |
| live | 120 | **854** |
| unknown | **2,816** | **3** |

Rotation fired twice, and the log says so:

    [gate] public.zwayam.com on a fresh egress address (#1, was 403, per-IP quota spent)
           — ban cleared, back to 10.0 req/s

Verdicts now settle evenly across all four quartiles (637/617/636/492 dead, 169/192/173/317 live)
instead of stopping dead after the first — the cutoff is gone. All 3 remaining `unknown` are in
quartile 1, before the first rotation.

## What the sweep actually found: the ledger was right

| ledger 2026-08-27 -> sweep 2026-09-17 | boards |
| --- | ---: |
| dead -> dead | 2,381 |
| live -> live | **756** |
| dead -> **live** | **98** |
| dead -> unknown | 3 |
| live -> dead | **1** |

**756 of 757 previously-live Boards re-confirm live**, and 98 Boards the ledger calls dead are
live. So the pipeline's 47-71% per-run zwayam failure rate is the quota wall and nothing else —
these Boards are not gone, and gone-striking them would have quarantined ~100 live Boards per run.

Totals now: 854 live, 225 hiring, 23,972 jobs (ledger holds 757 live / 224 hiring).

## Still open

- **The scrape path does not rotate either.** `BaseScraper.egress_fallback_on` defaults to
  `frozenset()` and `ZwayamScraper` never sets it, so the pipeline's own zwayam requests carry
  `egress_group="zwayam"` with an empty `egress_on` — routed but never marking the wall. That is
  the production half of this fix and it is a one-line opt-in, since `base._fetch` already applies
  `**self._egress()`.
- **The ledger is still unwritten.** `recheck_boards.py` reports only, by design: delisting is a
  separate call from measuring. Applying this sweep would revive 98 Boards and retire 1.
