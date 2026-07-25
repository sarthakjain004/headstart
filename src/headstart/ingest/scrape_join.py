#!/usr/bin/env python3
"""Join scrape-shard outputs into one snapshot — the union step of ADR-0026 (ADR-0025 Phase 2).

Each scrape shard wrote its boards to its own ``{ats}.jsonl`` under a fragment dir. Eviction is
scoped to the Boards present in ``data/jobs/`` (ADR-0014), so before tech-filter/sync the shards
**must** be unioned into one snapshot — otherwise a Board scraped by shard 3 wouldn't have its
closed postings evicted. Boards are shard-disjoint (the planner partitions them), so the union is a
per-ATS concatenation; a duplicate line from an intra-board resume is deduped downstream by id
(``corpus.iter_jobs``), exactly as with the monolith's single-file output.

Streams line-by-line (never buffering a whole ATS), and a shard that timed out mid-scrape simply
contributes the boards it did finish — partial-harvest safety survives per shard.

Run: python -m headstart.ingest.scrape_join [--shards DIR] [--out DIR]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from headstart.ingest import REPO_ROOT

_SHARDS = (
    REPO_ROOT / "data" / "scrape" / "fragments"
)  # outside data/jobs, so the joined snapshot stays clean
_OUT = REPO_ROOT / "data" / "jobs"


def _fragment_dirs(root: Path) -> list[Path]:
    """Fragment dirs under ``root`` (each holding one or more ``{ats}.jsonl``), sorted."""
    return sorted(d for d in root.iterdir() if d.is_dir() and any(d.glob("*.jsonl")))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--shards",
        default=str(_SHARDS),
        help="dir of scrape fragment dirs (default: data/jobs/fragments)",
    )
    ap.add_argument(
        "--out", default=str(_OUT), help="unioned snapshot dir (default: data/jobs)"
    )
    args = ap.parse_args()

    shards_root = Path(args.shards)
    frags = _fragment_dirs(shards_root) if shards_root.exists() else []
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # group source files by ATS filename ({ats}.jsonl), across all shards
    per_ats: dict[str, list[Path]] = {}
    for frag in frags:
        for f in sorted(frag.glob("*.jsonl")):
            per_ats.setdefault(f.name, []).append(f)
    print(
        f"[join] {len(frags)} shard(s), {len(per_ats)} ATS file(s)",
        file=sys.stderr,
        flush=True,
    )

    total = 0
    for ats_file, sources in sorted(per_ats.items()):
        n = 0
        with (out / ats_file).open("w", encoding="utf-8") as dst:
            for src in sources:
                with src.open(encoding="utf-8") as s:
                    for line in s:
                        if line.strip():
                            dst.write(line if line.endswith("\n") else line + "\n")
                            n += 1
        total += n
        print(
            f"[join] {ats_file}: {n} lines from {len(sources)} shard(s)",
            file=sys.stderr,
            flush=True,
        )

    print(
        f"[join] wrote {total} lines across {len(per_ats)} ATS files -> {out}",
        file=sys.stderr,
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
