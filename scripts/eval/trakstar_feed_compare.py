#!/usr/bin/env python3
"""Compare trakstar's two fetch paths on real live boards before any cutover decision.

``trakstar.py``'s production path (``fetch_raw()`` + ``parse()``) reads the careers-page HTML,
which silently caps at 25 rendered job cards — confirmed live and measured at ~10% of boards
hitting it (see the scraper module's own docstring, "Known gap" note, 2026-08-22). The
alternate ``fetch_via_feed()`` path reads the tenant's RSS feed instead, which carries every
job with no such cap — but it isn't available for every tenant (~2.5% 404).

This script runs BOTH paths against the same sample of real boards and reports, per board:
recovered jobs (in the feed but not the HTML page — the truncation this exists to measure),
missing jobs (in the HTML page but not the feed — would be a real regression if ever cut over),
and field-level differences on the jobs both paths agree exist (location, department,
employment_type, posted_at, description length). It does not decide whether to cut over — it
produces the evidence for that decision.

Output (all local; experiment/ is gitignored):
  experiment/trakstar-feed-compare/artifacts/report-<n>-seed<seed>.json   machine report
  experiment/trakstar-feed-compare/artifacts/boards/<slug>.json           per-board raw comparison

Run (needs network):
  .venv/bin/python -u scripts/eval/trakstar_feed_compare.py --n 200
  .venv/bin/python -u scripts/eval/trakstar_feed_compare.py --n 50 --seed 11 --workers 8
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from headstart.config import load_active_companies
from headstart.scrapers.registry import get_scraper

ROOT = Path(__file__).resolve().parents[2]
LEDGER_DIR = ROOT / "data" / "validate" / "liveness"
ARTIFACTS_ROOT = ROOT / "experiment" / "trakstar-feed-compare" / "artifacts"

_DEFAULT_WORKERS = 8  # each board also triggers fetch_raw()'s own internal 4-way detail
# fan-out, so board-level concurrency compounds — kept modest relative to salary_sample.py's
# 32-worker default, which has no such per-board internal fan-out on top of it.


@dataclass
class BoardComparison:
    slug: str
    error: str | None = None
    html_job_count: int = 0
    feed_available: bool = False
    feed_job_count: int = 0
    recovered_codes: list[str] | None = (
        None  # in feed only — the truncation this measures
    )
    missing_codes: list[str] | None = (
        None  # in HTML page only — a real regression if it exists
    )
    field_diffs: list[dict] | None = (
        None  # per shared code: which fields disagree, and how
    )


def _compare_one(company) -> BoardComparison:
    scraper = get_scraper(company.ats, company.slug, company.name)
    scraped_at = datetime.now(UTC).isoformat()
    try:
        html_jobs = scraper.parse(scraper.fetch_raw(), scraped_at)
    except Exception as exc:  # noqa: BLE001 - a board-level failure must not kill the batch
        return BoardComparison(slug=company.slug, error=f"html path: {exc!r}")

    try:
        feed_jobs = scraper.fetch_via_feed(scraped_at)
    except Exception as exc:  # noqa: BLE001
        return BoardComparison(slug=company.slug, error=f"feed path: {exc!r}")

    if feed_jobs is None:
        return BoardComparison(
            slug=company.slug, html_job_count=len(html_jobs), feed_available=False
        )

    html_by_code = {j.id.split(":", 2)[2]: j for j in html_jobs}
    feed_by_code = {j.id.split(":", 2)[2]: j for j in feed_jobs}
    recovered = sorted(set(feed_by_code) - set(html_by_code))
    missing = sorted(set(html_by_code) - set(feed_by_code))

    field_diffs = []
    for code in sorted(set(html_by_code) & set(feed_by_code)):
        h, f = html_by_code[code], feed_by_code[code]
        diff = {}
        if (h.location or None) != (f.location or None):
            diff["location"] = {"html": h.location, "feed": f.location}
        if (h.department or None) != (f.department or None):
            diff["department"] = {"html": h.department, "feed": f.department}
        if (h.employment_type or None) != (f.employment_type or None):
            diff["employment_type"] = {
                "html": h.employment_type,
                "feed": f.employment_type,
            }
        if (h.posted_at or None) != (f.posted_at or None):
            diff["posted_at"] = {"html": h.posted_at, "feed": f.posted_at}
        h_len, f_len = len(h.description or ""), len(f.description or "")
        # descriptions differ in incidental whitespace/entity handling even when substantively
        # the same (see the scraper module's own "one extra location line" note) — only flag a
        # length gap wide enough to suggest a real content difference, not noise.
        if abs(h_len - f_len) > max(h_len, f_len) * 0.15 and max(h_len, f_len) > 0:
            diff["description_length"] = {"html": h_len, "feed": f_len}
        if diff:
            field_diffs.append({"code": code, **diff})

    return BoardComparison(
        slug=company.slug,
        html_job_count=len(html_jobs),
        feed_available=True,
        feed_job_count=len(feed_jobs),
        recovered_codes=recovered,
        missing_codes=missing,
        field_diffs=field_diffs,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=200)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--workers", type=int, default=_DEFAULT_WORKERS)
    args = parser.parse_args()

    companies = [c for c in load_active_companies(LEDGER_DIR) if c.ats == "trakstar"]
    random.seed(args.seed)
    sample = random.sample(companies, min(args.n, len(companies)))

    boards_dir = ARTIFACTS_ROOT / "boards"
    boards_dir.mkdir(parents=True, exist_ok=True)

    results: list[BoardComparison] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_compare_one, c): c for c in sample}
        for i, future in enumerate(as_completed(futures), 1):
            c = futures[future]
            result = future.result()
            results.append(result)
            (boards_dir / f"{c.slug}.json").write_text(
                json.dumps(asdict(result), indent=2), encoding="utf-8"
            )
            if result.error:
                print(
                    f"  [{i}/{len(sample)}] {c.slug}: ERROR {result.error}", flush=True
                )
            elif not result.feed_available:
                print(
                    f"  [{i}/{len(sample)}] {c.slug}: {result.html_job_count} html jobs, "
                    f"feed unavailable",
                    flush=True,
                )
            else:
                print(
                    f"  [{i}/{len(sample)}] {c.slug}: {result.html_job_count} html jobs, "
                    f"{result.feed_job_count} feed jobs, "
                    f"+{len(result.recovered_codes)} recovered, "
                    f"-{len(result.missing_codes)} missing, "
                    f"{len(result.field_diffs)} field diffs",
                    flush=True,
                )

    ok = [r for r in results if r.error is None]
    errored = [r for r in results if r.error is not None]
    feed_ok = [r for r in ok if r.feed_available]
    feed_unavailable = [r for r in ok if not r.feed_available]
    hit_cap = [r for r in ok if r.html_job_count == 25]
    total_recovered = sum(len(r.recovered_codes or []) for r in feed_ok)
    total_missing = sum(len(r.missing_codes or []) for r in feed_ok)
    boards_with_missing = [r for r in feed_ok if r.missing_codes]
    boards_with_field_diffs = [r for r in feed_ok if r.field_diffs]

    print()
    print("===== trakstar feed-compare summary =====")
    print(f"boards sampled: {len(sample)}  ({len(errored)} errored, {len(ok)} ok)")
    print(
        f"feed available: {len(feed_ok)}/{len(ok)} ({len(feed_ok) / max(len(ok), 1) * 100:.1f}%)"
    )
    print(f"feed unavailable (fell back conceptually): {len(feed_unavailable)}")
    print(f"boards hitting the 25-card HTML cap: {len(hit_cap)}/{len(ok)}")
    print(f"total jobs recovered by the feed (missed by HTML page): {total_recovered}")
    print(
        f"total jobs missing from the feed (present in HTML page only): {total_missing}"
    )
    print(
        f"boards where the feed is MISSING a job the HTML page has: {len(boards_with_missing)}"
    )
    print(
        f"boards with a field-level difference on a shared job: {len(boards_with_field_diffs)}"
    )

    summary_path = ARTIFACTS_ROOT / f"report-{len(sample)}-seed{args.seed}.json"
    summary_path.write_text(
        json.dumps(
            {
                "sampled": len(sample),
                "seed": args.seed,
                "errored": len(errored),
                "ok": len(ok),
                "feed_available": len(feed_ok),
                "feed_unavailable": len(feed_unavailable),
                "hit_25_cap": len(hit_cap),
                "total_recovered": total_recovered,
                "total_missing": total_missing,
                "boards_with_missing": [r.slug for r in boards_with_missing],
                "boards_with_field_diffs": [r.slug for r in boards_with_field_diffs],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nwrote {summary_path.relative_to(ROOT)}")
    return 1 if not ok else 0


if __name__ == "__main__":
    sys.exit(main())
