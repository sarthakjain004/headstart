"""Loading the configured list of companies to scrape."""

from __future__ import annotations

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


def load_active_companies(
    ledger_dir: str | Path, min_jobs: int = 1
) -> list[CompanyRef]:
    """Build the scrape list from the liveness ledger (ADR-0012).

    Reads every ``{ledger_dir}/{ats}.csv`` (the per-ATS liveness ledger) and keeps the boards
    whose last verdict is ``live`` with ``jobs >= min_jobs`` (default: drop boards with no open
    postings — the "currently hiring" subset). Each scraper turns a ``(tenant, url)`` into its
    own slug via ``slug_from``, so no per-ATS logic lives here. Rows for an ATS with no scraper
    are skipped. This is the production source for a full scrape; ``config/companies.toml``
    remains the small curated seed.
    """
    from headstart import liveness
    from headstart.scrapers.registry import DISABLED_ATS, SCRAPERS

    ledger_dir = Path(ledger_dir)
    companies: list[CompanyRef] = []
    for csv_path in sorted(ledger_dir.glob("*.csv")):
        scraper = SCRAPERS.get(csv_path.stem)
        if scraper is None or scraper.ats in DISABLED_ATS:
            continue
        for v in liveness.load(csv_path).values():
            if v.status != liveness.LIVE or (v.jobs or 0) < min_jobs:
                continue
            companies.append(
                CompanyRef(
                    ats=scraper.ats,
                    slug=scraper.slug_from(v.tenant, v.url),
                    name=v.tenant,
                )
            )
    return _dedupe_boards(companies)


def _dedupe_boards(companies: list[CompanyRef]) -> list[CompanyRef]:
    """Collapse Boards that map to the same canonical key to one entry (ADR-0023).

    The ledger holds duplicate rows for one Board — differing only by slug casing (Workday sites
    ``.../External`` vs ``.../external``) or by an equivalent tenant/url form that resolves to the
    same ``board_key``. Left in, each variant is scraped and indexed separately, so one job lands in
    the index two or three times. Keep the lexicographically-smallest ``board_key`` per canonical
    (lowercased) key — this picks the Board that is actually scraped, and ``index_plan.plan_prune``
    keeps the index row carrying *that* casing, so scrape and index agree. (Until 2026-08-11 the
    prune instead kept the lex-min casing *present in the index*, which is a different population —
    it includes casings that left the ledger — and the two disagreed permanently: ADR-0023's
    amendment.)"""
    from headstart.scrapers.registry import SCRAPERS

    best: dict[str, tuple[str, CompanyRef]] = {}
    for company in companies:
        try:
            key = SCRAPERS[company.ats](company.slug).board_key()
        except Exception:  # noqa: BLE001 - a malformed slug falls back to the plain key, never drops the Board
            key = f"{company.ats}:{company.slug}"
        canon = key.lower()
        current = best.get(canon)
        if current is None or key < current[0]:
            best[canon] = (key, company)
    return [company for _, company in best.values()]
