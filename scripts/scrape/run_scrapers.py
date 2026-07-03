#!/usr/bin/env python3
"""Run the ATS scrapers over a chosen tenant list and stream jobs to ``data/jobs/{ats}.jsonl``.

A controllable launcher for the same engine ``python -m headstart`` uses (``scrape_all``): pick the
source list, the pick order, and a per-ATS cap on how many boards to attempt. Built for experiments —
it fixes a repeatable selection and prints throughput (jobs/s, boards/s) plus a breakdown of the
boards that failed, so you can measure a change to scrape speed and see what's being missed.

Source (``--source``):
  merged  -> ``data/ats-tenants-merged/{ats}.csv``         (everything discovered; not liveness-checked)
  active  -> ``data/ats-tenants-merged/active/{ats}.csv``   (the active list; carries a job count)

Order (``--order``):
  ranked  -> busiest board first, by the active list's job count. The merged list has no count, so it
             falls back to the CSV's file order. (default)
  random  -> shuffle; pass ``--seed`` for a reproducible pick.

Limit (``--limits FILE``): a TOML of per-ATS caps with an optional default, e.g.::

      default = 20
      greenhouse = 100
      lever = 50

  An ATS with no entry and no ``default`` is uncapped. Omit ``--limits`` entirely for no cap.

Only the ATSes that have a scraper are run; source files for the rest are skipped. Each run truncates
the ``{ats}.jsonl`` it writes; pass ``--resume`` to append and skip boards already done. Use
``--dry-run`` to print the selection without scraping.

Examples:
  .venv/bin/python scripts/scrape/run_scrapers.py --source active --limits scripts/scrape/scrape-limits.example.toml
  .venv/bin/python scripts/scrape/run_scrapers.py --source merged --order random --seed 1 --ats greenhouse,lever --dry-run
"""

from __future__ import annotations

import argparse
import collections
import csv
import random
import sys
import time
import tomllib
from pathlib import Path

from headstart.config import CompanyRef
from headstart.pipeline import scrape_all
from headstart.scrapers.registry import SCRAPERS

ROOT = Path(__file__).resolve().parents[2]
MERGED = ROOT / "data" / "ats-tenants-merged"
JOBS_DIR = ROOT / "data" / "jobs"

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))  # tenant lists can be large


def _source_dir(source: str) -> Path:
    return MERGED if source == "merged" else MERGED / "active"


def _load_rows(csv_path: Path) -> list[tuple[CompanyRef, int]]:
    """``(CompanyRef, job_count)`` for one ATS csv; ``job_count`` is 0 when the source has no count.

    A row whose slug can't be derived is skipped rather than sinking the whole selection.
    """
    scraper = SCRAPERS[csv_path.stem]  # caller guarantees the ATS has a scraper
    out: list[tuple[CompanyRef, int]] = []
    try:
        with csv_path.open(newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                tenant, url = r.get("tenant", ""), r.get("url", "")
                try:
                    slug = scraper.slug_from(tenant, url)
                except Exception:  # noqa: BLE001 - a malformed row shouldn't crash the batch
                    continue
                jobs = (r.get("jobs") or "").strip()
                out.append(
                    (
                        CompanyRef(scraper.ats, slug, tenant),
                        int(jobs) if jobs.isdigit() else 0,
                    )
                )
    except (
        OSError,
        UnicodeDecodeError,
        csv.Error,
    ) as exc:  # one bad file shouldn't sink the run
        print(f"warning: skipping {csv_path.name}: {exc}", file=sys.stderr)
    return out


def select_companies(
    rows: list[tuple[CompanyRef, int]],
    order: str,
    limit: int | None,
    rng: random.Random,
) -> list[CompanyRef]:
    """Order the rows (ranked busiest-first, or random) and take the first ``limit`` (None = all)."""
    if order == "random":
        rows = rows[:]
        rng.shuffle(rows)
    else:  # ranked: job count desc; stable sort, so a countless (merged) list keeps file order
        rows = sorted(rows, key=lambda rc: rc[1], reverse=True)
    if limit is not None:
        rows = rows[:limit]
    return [ref for ref, _ in rows]


def _load_limits(path: Path | None) -> dict:
    """Load + validate the caps TOML: values must be non-negative ints; unknown-ATS keys warn."""
    if path is None:
        return {}
    if not path.exists():
        raise SystemExit(f"no limits file at {path}")
    try:
        with path.open("rb") as f:
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"invalid TOML in {path}: {exc}") from None
    for key, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SystemExit(
                f"limit for {key!r} in {path} must be a non-negative integer, got {value!r}"
            )
        if key != "default" and key not in SCRAPERS:
            print(
                f"warning: limits key {key!r} is not a known ATS — ignored",
                file=sys.stderr,
            )
    return raw


def _limit_for(limits: dict, ats: str) -> int | None:
    """Cap for one ATS: its entry, else ``default``, else None (uncapped). Values are pre-validated."""
    return limits.get(ats, limits.get("default"))


def _error_kind(message: str) -> str:
    """Bucket a board failure by its leading exception type (what scrape_all recorded).

    ``"HTTPError: 404 ..."`` -> ``"HTTPError"``; groups the misses so a run shows *why* boards
    failed (DNS vs bot-wall vs parse) rather than a flat list.
    """
    return message.split(":", 1)[0].strip() or "unknown"


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--source", choices=("merged", "active"), required=True)
    ap.add_argument("--order", choices=("ranked", "random"), default="ranked")
    ap.add_argument(
        "--limits", type=Path, help="TOML of per-ATS caps (default + per-ATS)"
    )
    ap.add_argument(
        "--ats", help="comma-separated subset of ATSes to run (default: all)"
    )
    ap.add_argument(
        "--seed", type=int, help="seed for --order random (reproducible pick)"
    )
    ap.add_argument(
        "--resume",
        action="store_true",
        help="append + skip already-done boards (default: overwrite fresh)",
    )
    ap.add_argument(
        "--workers",
        type=int,
        help="scrape concurrency (default: HEADSTART_WORKERS or cores*4)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="print the selection and exit; don't scrape",
    )
    args = ap.parse_args()

    src = _source_dir(args.source)
    if not src.is_dir():
        raise SystemExit(f"no source dir at {src}")
    limits = _load_limits(args.limits)
    only = None
    if args.ats:
        only = {a.strip() for a in args.ats.split(",") if a.strip()}
        unknown = only - set(SCRAPERS)
        if unknown:
            raise SystemExit(
                f"unknown ATS(es): {', '.join(sorted(unknown))}; "
                f"known: {', '.join(sorted(SCRAPERS))}"
            )
    rng = random.Random(args.seed)

    companies: list[CompanyRef] = []
    print(
        f"{'ATS':<16}{'available':>10}{'selected':>10}  (source={args.source}, order={args.order})"
    )
    for csv_path in sorted(src.glob("*.csv")):
        ats = csv_path.stem
        if ats not in SCRAPERS or (only and ats not in only):
            continue
        rows = _load_rows(csv_path)
        chosen = select_companies(rows, args.order, _limit_for(limits, ats), rng)
        companies.extend(chosen)
        print(f"{ats:<16}{len(rows):>10}{len(chosen):>10}", flush=True)

    n_ats = len({c.ats for c in companies})
    print(f"\nselected {len(companies)} boards across {n_ats} ATSes")
    if args.dry_run:
        print("dry run — not scraping")
        return 0
    if not companies:
        raise SystemExit("nothing selected")

    start = time.monotonic()
    result = scrape_all(
        companies,
        jobs_dir=JOBS_DIR,
        progress_every=200,
        resume=args.resume,
        max_workers=args.workers,
    )
    elapsed = time.monotonic() - start

    # --- performance: what did we get, how fast (the numbers to compare across experiments) ---
    ok = result.boards - len(result.errors)
    print("\n=== run summary ===", flush=True)
    print(
        f"selection : source={args.source} order={args.order} workers={args.workers or 'auto'}"
    )
    print(
        f"boards    : {result.boards} attempted, {ok} ok, {len(result.errors)} failed"
    )
    print(f"jobs      : {result.unique} unique -> {JOBS_DIR}/{{ats}}.jsonl")
    print(
        f"time      : {elapsed:.1f}s  |  {result.boards / elapsed:.1f} boards/s"
        f"  |  {result.unique / elapsed:.0f} jobs/s"
        if elapsed
        else "time      : 0s"
    )

    # --- misses: why boards failed, bucketed by kind (DNS vs bot-wall vs parse ...) ---
    if result.errors:
        kinds = collections.Counter(_error_kind(m) for m in result.errors.values())
        print("misses    : failed boards by kind")
        for kind, n in kinds.most_common():
            print(f"    {kind:<22}{n}")
        print("    examples:")
        for key, msg in list(result.errors.items())[:5]:
            print(f"      {key}: {msg[:80]}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        # scrape_all flushes each board as it finishes, so partial output is already on disk
        print(
            "\ninterrupted — partial output is in data/jobs/{ats}.jsonl",
            file=sys.stderr,
        )
        raise SystemExit(130) from None
