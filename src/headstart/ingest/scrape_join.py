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
from collections import Counter
from pathlib import Path

from headstart import log
from headstart.ingest import REPO_ROOT, observability

_log = log.get(__name__, __spec__)

_SHARDS = (
    REPO_ROOT / "data" / "scrape" / "fragments"
)  # outside data/jobs, so the joined snapshot stays clean
_OUT = REPO_ROOT / "data" / "jobs"


def _fragment_dirs(root: Path) -> list[Path]:
    """Fragment dirs under ``root`` (each holding one or more ``{ats}.jsonl``), sorted."""
    return sorted(d for d in root.iterdir() if d.is_dir() and any(d.glob("*.jsonl")))


def main() -> int:
    log.setup()
    observability.context("join")
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
    _log.info(f"{len(frags)} shard(s), {len(per_ats)} ATS file(s)")

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
        _log.info(f"{ats_file}: {n} lines from {len(sources)} shard(s)")

    _log.info(f"wrote {total} lines across {len(per_ats)} ATS files -> {out}")
    _report_shards(shards_root, total, len(per_ats))
    return 0


def _report_shards(shards_root: Path, lines: int, ats_files: int) -> None:
    """The whole fan-out's story in one place — the view no single shard job can give.

    A shard's numbers only ever existed in its own runner's log, so questions like "did any
    shard run out of time, and how much work did it defer?" needed fifteen job logs opened by
    hand. The reports ride the fragment artifacts here, so this is the first point where they
    can be added up.
    """
    reports = observability.read_shards(shards_root)
    if not reports:
        _log.info(
            "no shard reports — nothing to aggregate (older shards, or a local run)"
        )
        return

    killed = [r for r in reports if r.get("killed_by_budget")]
    deferred = sum(int(r.get("undone") or 0) for r in reports)
    errors = sum(len(r.get("errors") or {}) for r in reports)
    retries: Counter[str] = Counter()
    for r in reports:
        retries.update(r.get("retries") or {})
    # `.get(... ) or 0` throughout, never direct indexing: a truncated report must not raise
    # here. The join's real job is unioning the run's job data, and losing that to a broken
    # telemetry file would be a far worse trade than losing one shard's numbers.
    slowest_seconds = max(float(r.get("seconds") or 0) for r in reports)
    worst_board = max(
        (float((r.get("board_seconds") or {}).get("max") or 0) for r in reports),
        default=0.0,
    )
    ratios = [
        float(r["seconds"]) / 60 / float(r["predicted_minutes"])
        for r in reports
        if r.get("predicted_minutes") and r.get("seconds")
    ]
    ratio_span = f"{min(ratios):.2f}-{max(ratios):.2f}x" if ratios else ""

    if killed:
        # An annotation, not an info line: a shard that ran out of time silently deferred work,
        # and that is the single fact most worth seeing on the run page.
        _log.warning(
            f"{len(killed)} shard(s) hit the time budget, deferring {deferred} boards: "
            + ", ".join(str(r.get("shard", "?")) for r in killed)
        )
    if errors:
        _log.warning(f"{errors} board errors across {len(reports)} shards")
    _log.info(
        f"fan-out: {len(reports)} shards | slowest {slowest_seconds / 60:.1f} min "
        f"| worst single board {worst_board:.0f}s | retries {sum(retries.values())}"
        + (f" | actual/predicted {ratio_span}" if ratio_span else "")
    )
    observability.summary(
        "Scrape fan-out",
        [
            f"- {lines:,} job lines across {ats_files} ATS files",
            f"- {len(reports)} shards, slowest **{slowest_seconds / 60:.1f} min**, "
            f"worst single board **{worst_board:.0f}s**",
            f"- {errors} board errors, {sum(retries.values())} retries"
            + (
                f" ({', '.join(f'{k} {v}' for k, v in sorted(retries.items()))})"
                if retries
                else ""
            ),
            f"- **{len(killed)} shard(s) hit the time budget**, deferring {deferred} boards"
            if killed
            else "- no shard hit its time budget",
        ]
        + ([f"- actual/predicted **{ratio_span}**"] if ratio_span else []),
    )


if __name__ == "__main__":
    raise SystemExit(main())
