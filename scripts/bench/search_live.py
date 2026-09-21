#!/usr/bin/env python3
"""Low-load authenticated latency sampler for the production Search surface.

The session cookie is read from stdin and is never written to the artifact. Cases are rotated
between rounds so a warming or drifting Space does not always benefit the same request shape.
Output is appended after every request so an interrupted run keeps its evidence.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

import requests

CASES = (
    ("browse", "/search", {"k": 20}),
    ("semantic", "/search", {"q": "backend engineer", "k": 20}),
    (
        "semantic_remote",
        "/search",
        {"q": "backend engineer", "remote": "true", "k": 20},
    ),
    (
        "semantic_combined",
        "/search",
        {
            "q": "backend engineer",
            "ats": "greenhouse",
            "remote": "true",
            "max_years": 5,
            "seen_within": 168,
            "k": 20,
        },
    ),
    (
        "facets_combined",
        "/facets",
        {
            "ats": "greenhouse",
            "remote": "true",
            "max_years": 5,
            "seen_within": 168,
        },
    ),
    ("coverage", "/coverage", {}),
)


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, int(len(ordered) * 0.95) - 1))]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="https://imposeidon-headstart-search.hf.space")
    ap.add_argument("--rounds", type=int, default=9)
    ap.add_argument("--timeout", type=float, default=90)
    ap.add_argument(
        "--out",
        default=(
            "experiment/search-index-performance/artifacts/"
            "2026-09-21_api-live-baseline.jsonl"
        ),
    )
    args = ap.parse_args()

    cookie = sys.stdin.readline().strip()
    if not cookie:
        raise SystemExit("session cookie required on stdin")

    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.cookies.set("session", cookie)
    timings: dict[str, list[float]] = defaultdict(list)
    failures = 0

    with dest.open("a", encoding="utf-8") as out:
        for round_no in range(args.rounds):
            # Rotate, rather than randomize, so every case occupies every relative position in a
            # six-round block and the run stays exactly reproducible.
            cases = CASES[round_no % len(CASES) :] + CASES[: round_no % len(CASES)]
            for name, path, params in cases:
                url = args.base.rstrip("/") + path
                if params:
                    url += "?" + urlencode(params)
                started = time.perf_counter()
                status = None
                size = 0
                error = None
                try:
                    response = session.get(url, timeout=args.timeout)
                    status = response.status_code
                    size = len(response.content)
                    if status != 200:
                        error = f"HTTP {status}"
                except requests.RequestException as exc:
                    error = type(exc).__name__
                elapsed_ms = (time.perf_counter() - started) * 1000
                row = {
                    "measured_at": datetime.now(UTC).isoformat(),
                    "round": round_no + 1,
                    "case": name,
                    "elapsed_ms": round(elapsed_ms, 1),
                    "status": status,
                    "response_bytes": size,
                    "error": error,
                }
                out.write(json.dumps(row, sort_keys=True) + "\n")
                out.flush()
                print(
                    f"round={round_no + 1:02d} {name:<20} "
                    f"{elapsed_ms:>8.1f} ms status={status or '-'} bytes={size}",
                    flush=True,
                )
                if error:
                    failures += 1
                else:
                    timings[name].append(elapsed_ms)

    print("\nsummary", flush=True)
    for name, _, _ in CASES:
        values = timings[name]
        if not values:
            print(f"{name:<20} no successful samples", flush=True)
            continue
        print(
            f"{name:<20} n={len(values):>2} median={statistics.median(values):>8.1f} ms "
            f"p95={_p95(values):>8.1f} ms min={min(values):>8.1f} ms "
            f"max={max(values):>8.1f} ms",
            flush=True,
        )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
