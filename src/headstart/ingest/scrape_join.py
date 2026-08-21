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
import json
from collections import Counter
from pathlib import Path

from headstart import log
from headstart.ingest import REPO_ROOT, observability, shard_speedup
from headstart.scrapers.registry import get_scraper

_log = log.get(__name__, __spec__)

_SHARDS = (
    REPO_ROOT / "data" / "scrape" / "fragments"
)  # outside data/jobs, so the joined snapshot stays clean
_OUT = REPO_ROOT / "data" / "jobs"
# Under data/state because that is what rides the corpus-state artifact to the job running
# `index sync` — the shard fragments themselves stop at this stage (ADR-0053).
_UNAUTHORITATIVE = REPO_ROOT / "data" / "state" / "unauthoritative_boards.json"
_SPEEDUP = REPO_ROOT / "data" / "state" / "shard_speedup.csv"


def _fragment_dirs(root: Path) -> list[Path]:
    """Fragment dirs under ``root`` (each holding one or more ``{ats}.jsonl``), sorted."""
    return sorted(d for d in root.iterdir() if d.is_dir() and any(d.glob("*.jsonl")))


def write_unauthoritative_boards(reports: list[dict], path: Path) -> dict[str, str]:
    """Persist the Boards whose scraped list is not authoritative, keyed the way the index keys
    Boards.

    This is the hop ADR-0046 named as the honest fix and deferred. A Board whose scrape came back
    short still emits the pages it did get, so ``index sync`` — which infers "this Board was
    scraped" from the presence of any job line — evicts everything the truncated list is missing.
    The outcome was known all along: ``harvest.scrape_all`` records it per Board and the shard
    report carries it here. Nothing carried it any further, and whole Boards flapped out of search
    for a cycle at a time.

    The shard reports key errors ``{ats}:{slug}`` — the *scrape* list's key — while eviction scope
    is keyed by ``board_key()`` (ADR-0049). Those differ wherever a slug is not the Board tail:
    Workday's slug is the whole careers URL, so ``workday:https://x.wd1.myworkdayjobs.com/Site``
    has to become ``workday:x/Site`` or the lookup silently never matches. Rows that will not
    resolve are dropped with a warning rather than written through unconverted, which would look
    like protection while providing none.

    Always writes, even with nothing to record: ``data/state`` round-trips through the HF dataset,
    so a run that skipped the write would leave the *previous* run's Boards in place and protect
    Boards that scraped cleanly this time.
    """
    unauthoritative: dict[str, str] = {}
    unresolved: list[str] = []
    for report in reports:
        # `errors` (the Board raised) and `truncated` (it returned a list it knows is short) are
        # kept apart upstream because they read differently in a log — but they mean one thing
        # here: this Board's list is not authoritative, so sync must not evict against it. The
        # truncations are the ones that actually flap; a raising Board writes no lines at all and
        # was never in the eviction scope to begin with.
        outcomes = {**(report.get("errors") or {}), **(report.get("truncated") or {})}
        for key, why in outcomes.items():
            ats, sep, slug = str(key).partition(
                ":"
            )  # partition: a Workday slug holds colons
            try:
                if (
                    not sep or not slug
                ):  # `get_scraper(ats, "")` yields a bogus `ats:` key
                    raise ValueError("not an ats:slug key")
                unauthoritative[get_scraper(ats, slug).board_key()] = str(why)
            except Exception:  # noqa: BLE001 - a malformed key must not sink the join
                unresolved.append(str(key))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(unauthoritative, indent=1, sort_keys=True), encoding="utf-8"
    )
    if unresolved:
        _log.warning(
            f"{len(unresolved)} unauthoritative Board(s) could not be resolved to a board_key and "
            f"are NOT protected from eviction this run: {sorted(unresolved)[:10]}"
        )
    _log.info(f"recorded {len(unauthoritative)} unauthoritative Board(s) -> {path}")
    return unauthoritative


def main() -> int:
    log.setup()
    observability.context("join")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--shards",
        default=str(_SHARDS),
        help="dir of scrape fragment dirs (default: data/scrape/fragments)",
    )
    ap.add_argument(
        "--out", default=str(_OUT), help="unioned snapshot dir (default: data/jobs)"
    )
    ap.add_argument(
        "--unauthoritative-boards",
        default=str(_UNAUTHORITATIVE),
        help="where to record the Boards whose scraped list is not authoritative, for `index "
        "sync` to exclude from the eviction scope "
        "(default: data/state/unauthoritative_boards.json)",
    )
    ap.add_argument(
        "--speedup-ledger",
        default=str(_SPEEDUP),
        help="shard_speedup.csv to blend this run's measured fan-out speedup into (ADR-0054)",
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
    reports = observability.read_shards(shards_root)
    # Written unconditionally, before the summary: an empty file is the honest record of "every
    # Board's list is authoritative", and the summary below is telemetry that must never gate the
    # eviction signal.
    write_unauthoritative_boards(reports, Path(args.unauthoritative_boards))
    _update_speedup(reports, Path(args.speedup_ledger))
    _report_shards(reports, total, len(per_ats))
    return 0


def _update_speedup(reports: list[dict], path: Path) -> None:
    """Blend this run's measured fan-out speedup into the ledger the next plan divides by.

    Written here rather than in ``update_ledgers`` because the shard reports are read here and
    nowhere else. Never fatal: a missing speedup costs one run of prediction accuracy, and the
    join is un-bankable — losing the run's scrape to a telemetry write would be a far worse
    trade (the same reasoning as the ``.get(...)`` discipline in :func:`_report_shards`).
    """
    try:
        ratios = shard_speedup.ratios_from_reports(reports)
        if not ratios:
            _log.info(
                "no usable shard timings — leaving the speedup ledger as it stands"
            )
            return
        stored = shard_speedup.load(path)
        blended = shard_speedup.blend(stored.ratio, ratios)
        shard_speedup.save(path, blended, len(ratios))
        _log.info(
            f"fan-out speedup: {blended:.2f}x "
            f"(was {stored.ratio:.2f}x, {len(ratios)} shard(s) this run)"
        )
    except Exception as exc:  # noqa: BLE001 - telemetry must never sink the join
        _log.warning(f"could not update the speedup ledger: {exc}")


def _report_shards(reports: list[dict], lines: int, ats_files: int) -> None:
    """The whole fan-out's story in one place — the view no single shard job can give.

    A shard's numbers only ever existed in its own runner's log, so questions like "did any
    shard run out of time, and how much work did it defer?" needed fifteen job logs opened by
    hand. The reports ride the fragment artifacts here, so this is the first point where they
    can be added up; ``main`` reads them once and hands them to both consumers.
    """
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
    # The cross-shard view a single shard cannot have: pool depth (ADR-0067 first measured 30 jobs
    # sharing just 11 WARP IPs; ADR-0081 corrected that to 11,007 distinct IPs across 150
    # shard-runs of real traffic) is a fact no shard's own count of its rotations can show.
    egress_ips: Counter[str] = Counter()
    for r in reports:
        egress_ips.update(r.get("egress_ips") or {})
    distinct_ips = sorted(k[3:] for k in egress_ips if k.startswith("ip:"))
    distinct_colos = sorted(k[5:] for k in egress_ips if k.startswith("colo:"))
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
        # Which Boards, across the whole fan-out. The shard names its own, but the run page is
        # where a Board that keeps being deferred becomes visible as a pattern rather than as
        # one shard's bad luck — and a name is what turns "a shard was killed" into a fix.
        lost = [b for r in killed for b in (r.get("deferred") or [])]
        if lost:
            _log.warning("deferred boards: " + observability.named_sample(lost))
    if errors:
        _log.warning(f"{errors} board errors across {len(reports)} shards")
    _log.info(
        f"fan-out: {len(reports)} shards | slowest {slowest_seconds / 60:.1f} min "
        f"| worst single board {worst_board:.0f}s | retries {sum(retries.values())}"
        + (f" | actual/predicted {ratio_span}" if ratio_span else "")
    )
    if distinct_ips:
        _log.info(
            f"egress: {len(distinct_ips)} distinct address(es) across {len(reports)} shards"
            f" ({', '.join(distinct_ips)}), colos {', '.join(distinct_colos) or '?'}"
        )
    observability.summary(
        "Scrape fan-out",
        [
            f"- {lines:,} job lines across {ats_files} ATS files",
            (
                f"- {len(reports)} shards, slowest **{slowest_seconds / 60:.1f} min**, "
                f"worst single board **{worst_board:.0f}s**"
            ),
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
