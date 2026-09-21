#!/usr/bin/env python3
"""Retry classes per shard — and the egress-loss signature hiding in their ratio.

`scrape_run` logs one line per shard classifying every retry it spent:

    retries: 403-wall 34, 405-wall 74, 429-ratelimit 2207, 5xx 2943, network 9286 (total 14544)

**The headline use is detecting a shard that lost its spare egress, without trusting a log
string.** Measured over runs 32261793515 and 32272854468 (30 shards) — **the 2026-08 regime, not
today's volumes; see the re-measurement below** — the two populations do not overlap:

| shard state (2026-08) | `network` retries | `429-ratelimit` retries |
| --- | --- | --- |
| WARP healthy | 5,000-19,000 | 1,100-2,900 |
| degraded to direct | **0-13** | **14,600-23,800** |

Concordance with the `degrading to direct` log line was 4/4 and 0/0 across those two runs — exact,
in both directions. The mechanism is plain once seen: requests through the WARP SOCKS5 proxy fail
as *network* errors when the tunnel wobbles, so a shard with no proxy has almost none; and having
no proxy it also has no second address, so the origin walls it and it eats 429s instead.

This ratio is the signal to prefer over grepping `degrading to direct`, for two reasons. It is
quantitative, so it shows *how badly* a shard was degraded rather than that it was. And it cannot
be broken by a logging change — which matters here, because a previous investigation was misled
when new log wording made an old phenomenon look new.

**Sample size: 4 degraded shards, all from one run (32261793515), against 26 healthy ones.** The
separation is wide — healthy shards top out at 0.37, degraded ones start at 1489 — but four is a
small n drawn from a single run, so treat `DIRECT_RATIO` as calibrated, not proven. A shard landing
in the ambiguous band between the thresholds is reported as `?` rather than forced to a verdict —
and so, since `MIN_EGRESS_RETRIES` below, is one that retried too little for the ratio to mean
anything. Both print `?`; the ratio column tells them apart, a number against a `-`.

**Retry volume has since collapsed by more than an order of magnitude, and the ratio alone became
a false-positive machine.** Re-measured 2026-09-21 over the 9 measurable of the 10 most recent
`pipeline.yml` runs — 35569584172, 35572417059, 35575571888, 35579899915, 35583945947, 35587348049,
35590192500, 35592873553, 35595828212 (35578784666 stood down), 135 shards:

| 2026-09 regime | `network` retries | `429-ratelimit` retries |
| --- | --- | --- |
| per shard | 0-1,560 | 0-473 |
| whole run, all 15 shards | 3,103-5,964 | 785-2,046 |

A whole run now spends 11,903-15,825 retries of every class, comparable to what a *single* degraded
shard used to spend on 429s alone. Against those volumes the ratio fired `DIRECT !MISMATCH` on
**6 of the 9 runs, 9 shards in total, while 0 of 135 shards logged `degrading to direct`** — and on
4 of the 6 the excess line came out *negative* (-71, -89, -78, -74), i.e. the shard flagged as
having lost its proxy spent **fewer** rate-limit retries than a healthy one. The flagged shapes say
why: `network=2, 429=230`, `network=3, 429=18`, `network=0, 429=20` (prints `inf`), `network=11,
429=473`. That is a near-zero denominator, not degradation — hence `MIN_EGRESS_RETRIES`.

**A caution the measurement earned.** `spare_egress.rotations()` counts tunnel restarts, and a
restart that returns the same address counts the same as one that does not — so rotation counts
printed here are activity, not health, regardless of how deep the address pool behind them turns
out to be. (ADR-0067 first measured that pool at "one to three" per colo, ~11-of-30 rotations
genuinely fresh; ADR-0081 corrected it on 150 shard-runs of real traffic to a deep pool with
~100% rescue rate. Neither number changes this caution — a count still isn't health.) Egress
health is the network/429 ratio and nothing else in this output.

Run: python scripts/runlog/fanout_retries.py 32272854468
     python scripts/runlog/fanout_retries.py 32261793515 32272854468   # compare
"""

from __future__ import annotations

import re
from typing import NamedTuple

from run_logs import Run, common_args, runs_from, skip_if_stood_down

RETRIES = re.compile(r"\[scrape_run\] retries: ([^(]+)\(total (\d+)\)")
# `http._retry_reason` emits these five for the statuses anyone currently retries, with `network`
# as its fallback for a transport failure. It is no longer strictly closed: a caller that extends
# its own `retry_on` past `http.TRANSIENT` gets an `http-{status}` line, which shows up in the NB
# below rather than being folded into one of these. (A `400-throttle` column lived here until
# ADR-0103: workday used to retry a 400 as a throttle, until the 400 turned out to be a stale
# session cookie the scraper now clears in-pass rather than retrying — see its `cookie-reset
# (recovered)` detail-loss line, not a retry class.)
# Naming them here rather than deriving columns from the rows is what lets this table print
# its header up front and stream each shard as it lands, per the repo's streaming rule.
CLASSES = ("network", "429-ratelimit", "5xx", "403-wall", "405-wall")
DEGRADED = "degrading to direct"
ROTATED = re.compile(r"spare egress: rotated to a fresh egress IP")
WALLED = re.compile(r"spare egress: (\S+) walled the current IP")
SPENT = re.compile(
    r"\[spare_egress\] (\w+): origin returned (\d+) — spending this shard"
)

# Above DIRECT_RATIO a shard's retries are dominated by rate-limiting rather than proxy
# flakiness — the fingerprint of running direct. Below WARP_RATIO it looks proxied. The measured
# gap is enormous (healthy max 0.37, degraded min 1489), so the band between them is empty in the
# sample and a shard landing there is reported as unknown rather than forced to a verdict.
DIRECT_RATIO = 5.0
WARP_RATIO = 1.0

# A ratio needs a denominator. `429/network` on a shard that spent 2 network and 17 rate-limit
# retries reads 8.50 and prints DIRECT — and that, not lost egress, is what produced every one of
# the 9 `!MISMATCH` rows in the 2026-09 window above. So classify only above a floor on the two
# classes together. 1,000 is **calibrated, not proven**: 2.1x the largest false positive measured
# (484 = 11 network + 473 429; the other eight are all under 250), 6.1x below the smallest healthy
# shard the ratios were drawn from (6,100 = 5,000 + 1,100), and 14.6x below the degraded
# population's own 429 floor (14,600) — so it abstains on 125 of the 135 shards in that window,
# while still clearing a degradation an order of magnitude smaller than 2026-08's. Zero of
# both classes, the case this guard originally special-cased, is simply the bottom of that range.
MIN_EGRESS_RETRIES = 1000


class Row(NamedTuple):
    shard: int | None
    counts: dict[str, int]
    total: int
    logged_degraded: bool
    rotations: int
    walls: int
    spent: int


def direct_ratio(counts: dict[str, int]) -> float | None:
    """429-per-network, the egress fingerprint. `None` — and `None` only — when the two classes
    together fall below `MIN_EGRESS_RETRIES`: the shard is then quiet, not direct, and calling it
    direct was a real false positive this guard exists to stop. Above the floor, a shard with zero
    network reads `inf`/DIRECT, deliberately: no proxy failures at all alongside that much walling
    is the degraded shape."""
    net, lim = counts.get("network", 0), counts.get("429-ratelimit", 0)
    if net + lim < MIN_EGRESS_RETRIES:
        return None
    return float("inf") if net == 0 else lim / net


def verdict_of(ratio: float | None) -> str:
    if ratio is None:
        return "?"
    if ratio > DIRECT_RATIO:
        return "DIRECT"
    return "warp" if ratio < WARP_RATIO else "?"


def parse_retries(text: str) -> tuple[dict[str, int], int] | None:
    m = RETRIES.search(text)
    if not m:
        return None
    counts = {}
    for part in m.group(1).strip().rstrip(",").split(", "):
        why, n = part.rsplit(" ", 1)
        counts[why.strip()] = int(n)
    return counts, int(m.group(2))


def report(run: Run) -> None:
    print(
        "".join(
            [f"{'sh':>4}"]
            + [f"{k:>16}" for k in CLASSES]
            + [f"{'total':>9}{'429/net':>9}{'egress':>10}  (arrival order)"]
        ),
        flush=True,
    )
    rows: list[Row] = []
    for shard, _job, text in run.stage("scrape"):
        parsed = parse_retries(text)
        if not parsed:
            continue
        counts, total = parsed
        r = Row(
            shard=shard,
            counts=counts,
            total=total,
            logged_degraded=DEGRADED in text,
            rotations=len(ROTATED.findall(text)),
            walls=len(WALLED.findall(text)),
            spent=len(SPENT.findall(text)),
        )
        rows.append(r)
        ratio = direct_ratio(r.counts)
        verdict = verdict_of(ratio)
        # An abstention cannot contradict the log line, but a shard the log called degraded while
        # the ratio abstains is still worth surfacing — that is the calibration's blind spot.
        if verdict == "?":
            flag = " ?vs-log" if r.logged_degraded else ""
        else:
            flag = "" if (verdict == "DIRECT") == r.logged_degraded else " !MISMATCH"
        shown = (
            "-"
            if ratio is None
            else ("inf" if ratio == float("inf") else f"{ratio:.2f}")
        )
        print(
            "".join([f"{r.shard:>4}"] + [f"{r.counts.get(k, 0):>16}" for k in CLASSES])
            + f"{r.total:>9}{shown:>9}{verdict:>10}{flag}",
            flush=True,
        )
    if not rows:
        print("  no retry lines found", flush=True)
        return

    unknown = sorted({k for r in rows for k in r.counts} - set(CLASSES))
    if unknown:
        print(
            f"  NB: retry classes outside CLASSES, omitted above: {unknown}", flush=True
        )
    agg = {k: sum(r.counts.get(k, 0) for r in rows) for k in CLASSES}
    print(
        "".join([f"{'Σ':>4}"] + [f"{agg[k]:>16}" for k in CLASSES])
        + f"{sum(r.total for r in rows):>9}",
        flush=True,
    )

    direct = [r for r in rows if verdict_of(direct_ratio(r.counts)) == "DIRECT"]
    logged = [r for r in rows if r.logged_degraded]
    quiet = [r for r in rows if direct_ratio(r.counts) is None]
    print(
        f"  egress: {len(direct)}/{len(rows)} shards look DIRECT by retry ratio; "
        f"{len(logged)}/{len(rows)} logged '{DEGRADED}'; "
        f"{len(quiet)}/{len(rows)} spent under {MIN_EGRESS_RETRIES:,} network+429 retries, too few "
        f"to read a ratio from (shown '-')",
        flush=True,
    )
    if direct:
        cost = sum(r.counts.get("429-ratelimit", 0) for r in direct)
        rest = sum(r.counts.get("429-ratelimit", 0) for r in rows if r not in direct)
        per = rest / max(1, len(rows) - len(direct))
        print(
            f"  those shards spent {cost:,} rate-limit retries against ~{per:,.0f} "
            f"on a healthy shard — {cost - per * len(direct):,.0f} excess",
            flush=True,
        )
    print(
        f"  rotation activity (NOT health, ADR-0067/0081): {sum(r.rotations for r in rows)} rotations, "
        f"{sum(r.walls for r in rows)} wall events, {sum(r.spent for r in rows)} budgets spent",
        flush=True,
    )


def main() -> None:
    args = common_args(__doc__.split("\n")[0]).parse_args()
    for run in runs_from(args):
        if skip_if_stood_down(run):
            continue
        print(f"\n===== run {run.id} head={run.head} — retries =====", flush=True)
        report(run)


if __name__ == "__main__":
    main()
