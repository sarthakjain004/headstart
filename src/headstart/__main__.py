"""Entry point: `python -m headstart` scrapes all companies and writes the feed."""

from __future__ import annotations

from pathlib import Path

from headstart.config import load_active_companies, load_companies
from headstart.pipeline import build_feed, scrape_all, write_feed

_ROOT = Path(__file__).resolve().parents[2]
_CONFIG = _ROOT / "config" / "companies.toml"
_ACTIVE = _ROOT / "data" / "ats-tenants-merged" / "active"
_OUTPUT = _ROOT / "docs" / "jobs.json"
_JOBS_DIR = _ROOT / "data" / "jobs"


def main() -> None:
    # Prefer the liveness-validated active lists; fall back to the curated seed if they
    # haven't been generated yet.
    companies = load_active_companies(_ACTIVE) if any(_ACTIVE.glob("*.csv")) else load_companies(_CONFIG)
    result = scrape_all(companies, jobs_dir=_JOBS_DIR)
    feed = build_feed(result)
    write_feed(feed, _OUTPUT)
    print(f"wrote {feed['count']} jobs to {_OUTPUT} (+ per-ATS JSONL under {_JOBS_DIR})")
    if result.errors:
        print(f"{len(result.errors)} company(ies) failed:")
        for key, message in result.errors.items():
            print(f"  {key}: {message}")


if __name__ == "__main__":
    main()
