"""Loading the configured list of companies to scrape."""

from __future__ import annotations

import csv
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CompanyRef:
    ats: str
    slug: str
    name: str | None = None


def load_companies(path: str | Path) -> list[CompanyRef]:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return [
        CompanyRef(ats=entry["ats"], slug=entry["slug"], name=entry.get("name"))
        for entry in data.get("company", [])
    ]


def load_active_companies(active_dir: str | Path, min_jobs: int = 1) -> list[CompanyRef]:
    """Build the scrape list from the liveness-validated active lists.

    Reads every ``{active_dir}/{ats}.csv`` (ats,tenant,url,jobs) and keeps boards with
    ``jobs >= min_jobs`` (default: drop boards with no open postings). Each scraper knows how
    to turn a discovered (tenant, url) into its own slug via ``slug_from``, so no per-ATS
    logic lives here. Rows for an ATS with no scraper are skipped. This is the production
    source for a full scrape; ``config/companies.toml`` remains the small curated seed.
    """
    from headstart.scrapers.registry import SCRAPERS

    active_dir = Path(active_dir)
    companies: list[CompanyRef] = []
    for csv_path in sorted(active_dir.glob("*.csv")):
        scraper = SCRAPERS.get(csv_path.stem)
        if scraper is None:
            continue
        with csv_path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    jobs = int(row.get("jobs") or 0)
                except ValueError:
                    jobs = 0
                if jobs < min_jobs:
                    continue
                companies.append(CompanyRef(
                    ats=scraper.ats,
                    slug=scraper.slug_from(row["tenant"], row["url"]),
                    name=row["tenant"],
                ))
    return companies
