"""A/B the pre-detail tech gate (ADR-0017) against a real Board, on the runner that pays for it.

The gate skips a detail fetch for a posting `filter_tech` will drop anyway. Whether that is worth
anything is a wall-clock question about a live origin, so this measures it rather than modelling
it: the same scraper, the same Board, the same process, differing only in `HEADSTART_TECH_GATE`.

**A/B/B/A, and the arm order alternates.** Two consecutive scrapes of one Board are not two draws
from one distribution — origin latency drifts, caches warm, and a single A/B hands the second arm
whatever the first arm's warming left behind. Interleaving and reporting each repeat separately is
what lets a reader tell a real saving from drift (`scalar_index_bench_abab.py` established the
shape).

Interleaving *reps* is not enough on its own, which this harness learned the hard way. It ran
`for gate in (False, True)` inside each rep, so the gated arm was always second and always
followed the control's full detail pass against the same origin — a systematic, one-directional
confound. On `jll.wd1.myworkdayjobs.com` that showed up as `desc_lost` of 2, 47, 7 and 59 across
four pairs: never zero, always the same direction, which is what an order effect looks like and
not what symmetric flakiness looks like. Odd reps now run the gated arm **first**, so any
remaining one-directional result is the mechanism rather than the running order.

**The safety number is the point, not the speed-up.** A gate that is fast and drops real tech jobs
is a defect, so every arm's Jobs are compared field-by-field over the *tech* subset — the only one
that reaches the index. `tech_lost` must be 0. A non-zero value means the gate's accessors do not
read what `parse` reads, and no speed-up redeems it.

Run:  python -u scripts/bench/tech_gate_bench.py workday:https://... smartrecruiters:Foo
      python -u scripts/bench/tech_gate_bench.py --repeats 2 --out results.json <board> ...
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from headstart.scrapers.registry import get_scraper
from headstart.tech_filter import is_tech


class _CountingFetcher:
    """Wraps a scraper's own fetcher to tally requests. The per-origin budget is the cost the
    gate exists to spend less of, so a request count is the measurement that transfers between
    machines — unlike seconds, which are this runner's."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.n = 0

    def fetch(self, method: str, url: str, **kwargs: Any) -> Any:
        self.n += 1
        return self._inner.fetch(method, url, **kwargs)

    async def fetch_async(
        self, session: Any, method: str, url: str, **kwargs: Any
    ) -> Any:
        self.n += 1
        return await self._inner.fetch_async(session, method, url, **kwargs)


def arm_order(rep: int) -> tuple[bool, bool]:
    """Which arm runs first in repeat ``rep``, as ``(first gate flag, second gate flag)``.

    Counterbalanced, because interleaving repeats does not control the order *within* one. The
    first version of this harness ran control-then-gated in every repeat, so the gated arm always
    inherited the control's warmed connections and freshly-spent origin budget — and on
    `jll.wd1.myworkdayjobs.com` that produced a `desc_lost` of 2, 47, 7 and 59 across four pairs:
    never zero, always the same direction, which is an order effect's signature and not
    flakiness'. Named and tested rather than inline for exactly that reason.
    """
    return (False, True) if rep % 2 == 0 else (True, False)


def ran_first(rep: int, gate: bool) -> bool:
    """Whether the arm carrying this ``gate`` flag was the first of its repeat."""
    return gate == arm_order(rep)[0]


def _run(board: str, gate: bool) -> dict[str, Any]:
    """One arm: a fresh scraper, one full fetch_raw + parse, timed."""
    ats, slug = board.split(":", 1)
    os.environ["HEADSTART_TECH_GATE"] = "1" if gate else "0"
    # `have_details` is the pipeline signal every gate is conditional on (ADR-0048). An *empty*
    # container is the honest value here: it says "the pipeline is running and holds no detail
    # for this Board", which is the first-run state and the one that makes the gate the only
    # thing skipping anything — so the arms differ by the gate alone, not by the skip-list.
    scraper = get_scraper(ats, slug, have_details=frozenset())
    counter = _CountingFetcher(scraper._fetcher)
    scraper._fetcher = counter
    t0 = time.monotonic()
    raw = scraper.fetch_raw()
    jobs = scraper.parse(raw, "2026-09-17T00:00:00Z")
    seconds = time.monotonic() - t0
    tech = {j.id: j for j in jobs if is_tech(j.title, j.department)}
    return {
        "listed_ids": {j.id for j in jobs},
        "seconds": round(seconds, 2),
        "requests": counter.n,
        "jobs": len(jobs),
        "tech_jobs": len(tech),
        "gated_out": int(scraper.telemetry.get("tech_gated_details") or 0),
        "with_description": sum(1 for j in tech.values() if j.description),
        "truncated": scraper.truncated,
        "_tech": tech,
    }


def _compare(control: dict[str, Any], treatment: dict[str, Any]) -> dict[str, Any]:
    """What the gate changed about the Jobs that reach the index.

    Only the tech subset is compared: a non-tech Job's description is discarded by `filter_tech`
    before anything reads it, so losing it is the saving, not a regression.

    **Churn is subtracted before anything is called a loss.** The arms are separate live reads
    minutes apart, and a Board that opens or closes a posting between them puts an id in one arm
    and not the other — which a plain set difference reports as the gate deleting a tech job.
    Not hypothetical: on `thehartford` 2026-09-17 one arm listed "IND Lead Associate / Engineer"
    and the other did not, and the gate *keeps* that title (`is_tech` -> generic-tech-token), so
    it cannot have been the cause. Only ids **both** arms listed can testify about the gate; the
    rest are counted as `churned` and reported separately."""
    c, t = control["_tech"], treatment["_tech"]
    both_listed = control["listed_ids"] & treatment["listed_ids"]
    churned = len(control["listed_ids"] ^ treatment["listed_ids"])
    lost = sorted((set(c) - set(t)) & both_listed)
    gained = sorted((set(t) - set(c)) & both_listed)
    changed = [
        jid
        for jid in set(c) & set(t)
        if (c[jid].title, c[jid].department, c[jid].location, c[jid].url)
        != (t[jid].title, t[jid].department, t[jid].location, t[jid].url)
    ]
    # **Both directions, because one direction cannot tell a regression from flakiness.** A
    # detail fetch fails transiently on a busy origin, in whichever arm happens to draw it, so
    # counting only control-had/treatment-lacks reports half of a symmetric process as if it
    # were one-sided damage. Measured on `jll.wd1.myworkdayjobs.com`, where the gate is exact
    # and `tech_lost` is 0: 47 lost one way on 2026-09-17 — and the reverse column is what says
    # whether that is the gate or the origin.
    desc_lost = [
        jid for jid in set(c) & set(t) if c[jid].description and not t[jid].description
    ]
    desc_gained = [
        jid for jid in set(c) & set(t) if t[jid].description and not c[jid].description
    ]
    return {
        "churned": churned,
        "tech_lost": len(lost),
        "tech_gained": len(gained),
        "tech_fields_changed": len(changed),
        "tech_description_lost": len(desc_lost),
        "tech_description_gained": len(desc_gained),
        "examples_lost": [f"{c[j].title} | {c[j].department}" for j in lost[:5]],
        "examples_desc_lost": [f"{c[j].title}" for j in desc_lost[:5]],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("boards", nargs="+")
    ap.add_argument("--repeats", type=int, default=2, help="A/B pairs per board")
    ap.add_argument("--out", default="tech_gate_bench.json")
    args = ap.parse_args()

    if args.repeats < 2:
        print(
            "NOTE: --repeats 1 runs control-then-gated only, so arm order is not counterbalanced "
            "and a one-directional desc-lost/gained cannot be told from an order effect.",
            flush=True,
        )
    results: list[dict[str, Any]] = []
    out = pathlib.Path(args.out)
    for board in args.boards:
        print(f"\n=== {board}", flush=True)
        arms: list[dict[str, Any]] = []
        for rep in range(args.repeats):
            for gate in arm_order(rep):
                label = "gate-ON " if gate else "gate-OFF"
                try:
                    arm = _run(board, gate)
                except Exception as exc:  # noqa: BLE001 - one dead Board must not end the sweep
                    print(
                        f"  rep{rep} {label}: FAILED {type(exc).__name__}: {exc}",
                        flush=True,
                    )
                    arm = {"error": f"{type(exc).__name__}: {exc}", "_tech": {}}
                else:
                    print(
                        f"  rep{rep} {label}: {arm['seconds']:>7.1f}s  "
                        f"{arm['requests']:>6} reqs  {arm['jobs']:>6} jobs  "
                        f"{arm['tech_jobs']:>5} tech  {arm['gated_out']:>6} gated-out",
                        flush=True,
                    )
                arm.update(
                    board=board, rep=rep, gate=gate, ran_first=ran_first(rep, gate)
                )
                arms.append(arm)
        ok = [a for a in arms if "error" not in a]
        pairs = []
        for rep in range(args.repeats):
            c = next((a for a in ok if a["rep"] == rep and not a["gate"]), None)
            t = next((a for a in ok if a["rep"] == rep and a["gate"]), None)
            if c and t:
                cmp = _compare(c, t)
                cmp.update(
                    rep=rep,
                    control_seconds=c["seconds"],
                    treatment_seconds=t["seconds"],
                    control_requests=c["requests"],
                    treatment_requests=t["requests"],
                    speedup=round(c["seconds"] / t["seconds"], 2)
                    if t["seconds"]
                    else None,
                    requests_saved_pct=round(
                        100 * (c["requests"] - t["requests"]) / c["requests"], 1
                    )
                    if c["requests"]
                    else None,
                )
                pairs.append(cmp)
                verdict = "OK" if cmp["tech_lost"] == 0 else "!! TECH JOBS LOST !!"
                print(
                    f"  rep{rep} -> {cmp['speedup']}x faster, "
                    f"{cmp['requests_saved_pct']}% fewer requests, "
                    f"tech_lost={cmp['tech_lost']} "
                    f"desc-lost/gained={cmp['tech_description_lost']}/"
                    f"{cmp['tech_description_gained']} "
                    f"churned={cmp['churned']} "
                    f"{verdict}",
                    flush=True,
                )
                if cmp["examples_lost"]:
                    for e in cmp["examples_lost"]:
                        print(f"        LOST: {e}", flush=True)
        for a in arms:
            a.pop("_tech", None)
            a.pop("listed_ids", None)
        results.append({"board": board, "arms": arms, "pairs": pairs})
        out.write_text(
            json.dumps(results, indent=2)
        )  # progressive, never only at the end
    print(f"\nwrote {out}", flush=True)


if __name__ == "__main__":
    main()
