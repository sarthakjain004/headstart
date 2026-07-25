#!/usr/bin/env python3
"""Reconcile Wellfound's company -> ATS hints against the liveness ledger.

Input is data/discover/wellfound_ats_source.csv (see scripts/discover/wellfound_ats_source.py).
For each company Wellfound says is on ATS X, look for a tenant in data/validate/liveness/{X}.csv.
An exact name/slug hit means we already track that board and there is nothing to do; those rows are
dropped. What is written out is the remainder — the companies whose board we know exists, on a known
provider, but whose tenant slug the name never matched.

That remainder is the "non-derivable slug" class from CLAUDE.md, so each row carries the closest
ledger tenants as `candidates` (scored by string similarity, best first). Candidates are LEADS, not
resolutions: short generic names collide hard across tenants (`Check` pulls `checkly` and
`checkatrade`, different companies entirely), so every one needs verifying before it is pinned. A row
with no candidates is a board the ledger appears to be missing outright.

Usage:  python scripts/resolve/wellfound_ledger_gap.py
"""

import csv
import re
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SRC = ROOT / "data" / "discover" / "wellfound_ats_source.csv"
LEDGER = ROOT / "data" / "validate" / "liveness"
OUT = ROOT / "data" / "resolve" / "wellfound_ledger_gap.csv"

MAX_CANDIDATES = 5
MIN_KEY_LEN = 5  # shorter keys match everything; the noise buries the signal
COLS = ["company", "wellfound_slug", "ats", "jobs", "verdict", "candidates"]


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def destub(s: str) -> str:
    """Wellfound appends a disambiguator to colliding slugs: `sardine-1`, `seal-10`."""
    return re.sub(r"-\d+$", "", s or "")


def main() -> int:
    rows = list(csv.DictReader(SRC.open(encoding="utf-8")))

    # ats -> {norm(tenant): (tenant, status)}
    ledger: dict[str, dict[str, tuple[str, str]]] = defaultdict(dict)
    for path in sorted(LEDGER.glob("*.csv")):
        for r in csv.DictReader(path.open(encoding="utf-8")):
            ledger[path.stem][norm(r["tenant"])] = (r["tenant"], r["status"])

    OUT.parent.mkdir(parents=True, exist_ok=True)
    f = OUT.open("w", newline="", encoding="utf-8")
    writer = csv.DictWriter(f, fieldnames=COLS, lineterminator="\n")
    writer.writeheader()

    tracked = n_lead = n_absent = 0
    for r in rows:
        ats, board = r["ats"], ledger.get(r["ats"], {})
        keys = {norm(r["company"]), norm(destub(r["wellfound_slug"]))}
        if any(k in board for k in keys):
            tracked += 1
            continue

        probes = {k for k in keys if len(k) >= MIN_KEY_LEN}
        scored = []
        for key, (tenant, status) in board.items():
            if len(key) < MIN_KEY_LEN or not any(
                p in key or key in p for p in probes
            ):
                continue
            best = max(SequenceMatcher(None, p, key).ratio() for p in probes)
            scored.append((best, tenant, status))
        scored.sort(reverse=True)
        top = scored[:MAX_CANDIDATES]

        # No ledger at all for this ATS (e.g. dover) is a provider gap, not a slug gap.
        verdict = (
            "no-ledger" if not board else ("slug-variant?" if top else "absent")
        )
        n_lead += bool(top)
        n_absent += not top
        row = {
            **{c: r.get(c, "") for c in ("company", "wellfound_slug", "ats", "jobs")},
            "verdict": verdict,
            "candidates": "; ".join(f"{t}({s},{sc:.2f})" for sc, t, s in top),
        }
        writer.writerow(row)
        f.flush()
        print(
            f"{verdict:14s} {ats:11s} {row['company'][:26]:26s} {row['candidates']}",
            flush=True,
        )

    f.close()
    print(
        f"\n{len(rows)} hints: {tracked} already tracked, {n_lead} slug-variant leads,"
        f" {n_absent} absent -> {OUT.relative_to(ROOT)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
