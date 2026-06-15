"""Entry point: `python -m headstart` scrapes all companies and writes the feed."""

from __future__ import annotations

from pathlib import Path

from headstart.config import load_companies
from headstart.pipeline import build_feed, scrape_all, write_feed

_ROOT = Path(__file__).resolve().parents[2]
_CONFIG = _ROOT / "config" / "companies.toml"
_OUTPUT = _ROOT / "docs" / "jobs.json"


def main() -> None:
    companies = load_companies(_CONFIG)
    result = scrape_all(companies)
    feed = build_feed(result)
    write_feed(feed, _OUTPUT)
    print(f"wrote {feed['count']} jobs to {_OUTPUT}")
    if result.errors:
        print(f"{len(result.errors)} company(ies) failed:")
        for key, message in result.errors.items():
            print(f"  {key}: {message}")


if __name__ == "__main__":
    main()
