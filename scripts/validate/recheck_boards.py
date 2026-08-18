#!/usr/bin/env python3
"""Re-probe a named list of Boards against the live ATS, without sweeping a whole provider.

``check_liveness.py`` is the ledger-wide sweep: it walks every tenant of an ATS under its TTLs.
That is the wrong shape for a triage question like "these 292 Boards 404'd in every run — are they
actually dead?", where re-probing all 22,964 greenhouse+ashby tenants to learn about 292 of them is
most of a day's rate budget spent on the wrong thing.

So this takes the Boards by name and probes exactly those, through the *same* ``PROBES`` functions
the sweep uses, so a verdict here means what a verdict there means:

    LIVE     200 with a parseable job list
    DEAD     definitive: 404/410, or DNS doesn't resolve
    UNKNOWN  couldn't tell: timeout, reset, 5xx, 429 — worth a retry, not a delisting

Reads ``{ats}:{slug}`` ids, one per line (``-`` for stdin), and resolves each to the ledger row that
carries its url. **Reports only** — the ledger is not written, because deciding to delist a Board is
a separate call from measuring it, and a 429 storm mid-probe would otherwise mark live Boards dead.

    python scripts/validate/recheck_boards.py ids.txt
    python scripts/validate/recheck_boards.py - < ids.txt --out data/validate/recheck.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_liveness import PROBES  # needs the paths above first

from headstart import liveness

LEDGER = ROOT / "data" / "validate" / "liveness"


def read_ids(source: str) -> list[str]:
    lines = sys.stdin if source == "-" else Path(source).read_text().splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]


def resolve(ids: list[str]) -> tuple[list[tuple[str, str, str]], list[str]]:
    """Pair each ``{ats}:{slug}`` with the url its ledger row holds; report ids we can't place."""
    ledgers: dict[str, dict] = {}
    targets, unknown = [], []
    for jid in ids:
        ats, _, slug = jid.partition(":")
        if ats not in PROBES:
            unknown.append(f"{jid} (no probe for {ats!r})")
            continue
        if ats not in ledgers:
            ledgers[ats] = liveness.load(LEDGER / f"{ats}.csv")
        row = ledgers[ats].get(slug)
        if row is None:
            unknown.append(f"{jid} (not in the {ats} ledger)")
            continue
        targets.append((ats, slug, row.url))
    return targets, unknown


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("ids", help="file of {ats}:{slug} lines, or - for stdin")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", type=Path, help="write a CSV of the verdicts here")
    args = ap.parse_args()

    targets, unresolved = resolve(read_ids(args.ids))
    for line in unresolved:
        print(f"  skip {line}", flush=True)
    print(f"probing {len(targets)} board(s) with {args.workers} workers\n", flush=True)

    results, tally = [], {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(PROBES[ats], slug, url): (ats, slug, url)
            for ats, slug, url in targets
        }
        # as_completed, and print per board: a slow probe must not hold up the rest (repo rule)
        for done in as_completed(futures):
            ats, slug, url = futures[done]
            try:
                verdict, jobs = done.result()
            except Exception as exc:  # noqa: BLE001 - a probe blowing up is itself a verdict
                verdict, jobs = "unknown", None
                print(f"  {ats}:{slug} raised {type(exc).__name__}", flush=True)
            tally[verdict] = tally.get(verdict, 0) + 1
            results.append((ats, slug, url, verdict, jobs))
            print(
                f"  {verdict:<8} {ats}:{slug}"
                f"{'' if jobs is None else f' ({jobs} jobs)'}",
                flush=True,
            )

    print(
        f"\n{len(results)} probed: "
        + ", ".join(f"{v} {k}" for k, v in sorted(tally.items()))
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["ats", "slug", "url", "verdict", "jobs"])
            w.writerows(sorted(results))
        print(f"-> {args.out}")
    print("\nLedger not modified. Decide delisting separately from measuring.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
