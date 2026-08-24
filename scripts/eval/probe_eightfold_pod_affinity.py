#!/usr/bin/env python3
"""Does an Eightfold app pod control which backend replica answers a search?

Companion probe to `docs/eightfold/pcsx-replica-instability.md` and
`docs/eightfold/no-client-side-fix-for-replica-instability.md`. The first doc infers a
load-balancer-plus-replicas architecture from indirect evidence (no session affinity, stable
counts vs. unstable order); this probe measures it directly. Every `/api/pcsx/search` response
carries `X-EF-IID`, the serving app pod's own identity, for free — correlating it against the
returned ordering turns "we think there are multiple backends" into "here is the pod, here is
which backend it answered from, and here is the same pod answering from a different one next
time."

Fires N requests at the same offset (default: page 0) on one board and reports:
  - how many distinct pods answered
  - how many distinct orderings were returned
  - for every pod hit more than once, whether it stayed on one ordering or flipped

FLIPS on a repeated pod is the finding: if pod identity determined the answer, a pod would
always return the same ordering. It doesn't, which means no client-side signal (cookie, header,
connection reuse, query parameter) can pin a replica, because the app tier itself doesn't get to
choose one either — see the "no-client-side-fix" doc for the full negative result (ES-shaped
`preference`/`routing` params, a fixed `seed`, session reuse — none of it works, and this probe
is why).

Run:
  .venv/bin/python -u scripts/eval/probe_eightfold_pod_affinity.py careers.qualcomm.com
  .venv/bin/python -u scripts/eval/probe_eightfold_pod_affinity.py ngc.eightfold.ai --reps 40
Exit: 0 always (this is a measurement, not a pass/fail check) — read the printed verdict.
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.parse
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import requests

from headstart.scrapers.base import USER_AGENT
from headstart.scrapers.eightfold import EightfoldScraper


def _page(
    slug: str, group_id: str, start: int, headers: dict
) -> tuple[str | None, tuple]:
    """One search request. Returns (serving pod, ids-in-order); retries transient failures."""
    params = {"domain": group_id, "query": "", "location": "", "start": start}
    url = f"https://{slug}/api/pcsx/search?{urllib.parse.urlencode(params)}"
    for attempt in range(4):
        time.sleep(0.5)
        response = requests.get(url, headers=headers, timeout=45)
        if response.status_code == 200:
            data = response.json().get("data") or {}
            ids = tuple(str(p.get("id")) for p in (data.get("positions") or []))
            return response.headers.get("X-EF-IID"), ids
        time.sleep(3 + 3 * attempt)
    return None, ()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("slug", help="board host, e.g. careers.qualcomm.com")
    parser.add_argument(
        "--start", type=int, default=0, help="offset to probe (default 0)"
    )
    parser.add_argument(
        "--reps", type=int, default=30, help="requests to fire (default 30)"
    )
    args = parser.parse_args()

    group_id = EightfoldScraper(args.slug)._group_id()
    if not group_id:
        print(f"{args.slug}: no group_id found — not an Eightfold board app")
        return 0
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Referer": f"https://{args.slug}/careers",
    }
    print(
        f"board: {args.slug}  group_id: {group_id}  offset: {args.start}  reps: {args.reps}\n"
    )

    rows: list[tuple[str, tuple]] = []
    for i in range(args.reps):
        pod, ids = _page(args.slug, group_id, args.start, headers)
        if pod:
            rows.append((pod, ids))
        print(f"  {i + 1:3d}  {pod}", flush=True)

    if not rows:
        print("\nno successful responses — cannot conclude anything")
        return 0

    pod_orderings: dict[str, set[tuple]] = defaultdict(set)
    for pod, ids in rows:
        pod_orderings[pod].add(ids)
    distinct_orderings = {ids for _pod, ids in rows}
    repeated = {
        p: o for p, o in pod_orderings.items() if sum(1 for x, _ in rows if x == p) > 1
    }
    flipping = {p: o for p, o in repeated.items() if len(o) > 1}

    print(
        f"\nresponses: {len(rows)}  distinct pods: {len(pod_orderings)}  "
        f"distinct orderings: {len(distinct_orderings)}"
    )
    print(
        f"pods hit more than once: {len(repeated)}, of which {len(flipping)} answered from "
        f"more than one ordering on their own repeat hits"
    )
    for pod in flipping:
        print(f"  FLIPS: {pod}")

    if flipping:
        print(
            "\nVERDICT: pod identity does not determine the ordering — the same pod answers "
            "from either backend interchangeably. No app-tier signal can pin a replica."
        )
    elif len(pod_orderings) > 1 and len(distinct_orderings) > 1:
        print(
            "\nVERDICT: inconclusive at this rep count — no repeated pod caught flipping yet, "
            "but multiple pods and multiple orderings were both seen. Raise --reps."
        )
    else:
        print(
            "\nVERDICT: no instability observed at this rep count — try a larger board or "
            "more reps (small boards may have too few day-ties to exhibit the effect)."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
