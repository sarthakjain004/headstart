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


# Vendor test and sandbox Boards. They are live, they look like they are hiring, and their
# postings are fabricated — RippleHire's own QA/UAT tenants, a SmartRecruiters demo board,
# greenhouse boards whose company name is literally "Test". They reach users as real results
# (a "Software Engineer" at "prodtest"), so they are dropped here, which both stops the scrape
# and makes `index prune` evict the rows already indexed — the keep-set is built from this
# same function.
#
# Every entry was confirmed by READING that Board's own postings (2026-08-12), never from the
# shape of its slug. That distinction is the whole point: a slug-pattern rule would also have
# dropped `greenhouse:stage`, which is KKR's real board of 128 jobs, and `recruitee:test1234`,
# which belongs to a real Austrian education agency. Keys are lowercased ``{ats}:{slug}``, so
# one entry covers a Board that appears under several casings (smartrecruiters Dev2/dev2).
EXCLUDED_BOARDS: frozenset[str] = frozenset(
    {
        "darwinbox:training",  # company "training"; "Ali marketing Executive", "SK_Jr. Associate"
        "greenhouse:staging",  # company "Staging Site Board"; its one posting is titled "TEST"
        "greenhouse:test1",  # company "Test"
        "ripplehire:prodtest",  # 863 postings, company "prodtest"
        "ripplehire:qa1-tataaia",  # 209 postings, company "qa1-tataaia"
        "ripplehire:qa1-ust-app",  # 300 postings, company "qa1-ust-app"; "software developement"
        "ripplehire:rhsandbox",  # 649 postings; RippleHire's own sandbox tenant
        "ripplehire:uat2",  # 788 postings, company "uat2"
        "smartrecruiters:dev2",  # company "Dev"; SmartRecruiters demo tenant
        # Capgemini's test RMK host, which mirrors real postings under the company name
        # "careers-test". Safe to drop because their production board is in the ledger too
        # (`careers.capgemini.com`, 1338 postings) — this removes the duplicate, not the jobs.
        "successfactors:careers-test.capgemini.com",
        # Blackstone's own test sites; the second is named for what it serves. Workday slugs
        # ARE the careers URL, so these keys are longer than the rest.
        "workday:https://blackstone.wd1.myworkdayjobs.com/marni_test_site",
        "workday:https://blackstone.wd1.myworkdayjobs.com/marni_test_ghost_posting_site",
    }
)


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
    are skipped, as are the vendor test Boards in :data:`EXCLUDED_BOARDS`. This is the
    production source for a full scrape; ``config/companies.toml`` remains the small curated
    seed.
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
            slug = scraper.slug_from(v.tenant, v.url)
            if f"{scraper.ats}:{slug}".lower() in EXCLUDED_BOARDS:
                continue
            companies.append(CompanyRef(ats=scraper.ats, slug=slug, name=v.tenant))
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
