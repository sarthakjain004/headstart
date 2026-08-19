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
        # Walmart's wd5 tenant was retired (superseded by wd504's WalmartExternal, the real
        # board, 2000 postings). wd5 now 303-redirects every jobs query to a Workday
        # maintenance page; our client follows it to a 200 of maintenance-page HTML, so
        # response.json() throws JSONDecodeError on every run (confirmed live 2026-08-19, not
        # just historical logs). The ledger carries this one dead URL under three tenant-key
        # casings/formats; one lowercased entry here covers all three.
        "workday:https://walmart.wd5.myworkdayjobs.com/non-workdayinternal",
    }
)

# Real Boards withheld *for now* — kept apart from EXCLUDED_BOARDS above, whose every member is
# not a genuine Board at all. Each entry says what un-parks it: a park that outlives its reason
# is silent lost coverage, and this one costs a large employer.
#
# Keyed on the canonical lowercased ``board_key``, NOT on ``ats:slug`` like EXCLUDED_BOARDS. The
# ledger carries one Workday Board under several hosts — Accenture sits on both `wd3` and `wd103`
# — and `board_key` is what collapses them (ADR-0023). Keyed on one URL, the park would remove
# that row and merely promote another instance's row to be `_dedupe_boards`' survivor: the Board
# keeps being scraped while the entry looks effective.
#
# A parked Board also leaves `index_plan.live_keep_set`, so whatever rows it holds in the index
# are evicted as off-Board. Accepted either way: this Board has never finished a scrape, so it
# has little or nothing indexed — and could not keep those rows fresh if it did.
PARKED_BOARDS: frozenset[str] = frozenset(
    {
        # 48,369 jobs. Workday reports a query's total as at most 2,000, so the scraper
        # subdivides by facet (depth 3 here) and pages each leaf 20 at a time — thousands of
        # sequential requests against a Board no per-board budget bounds. It finished in none of
        # the three runs of 2026-08-13 (03:36 / 06:53 / 08:48 UTC), and because a running thread
        # cannot be cancelled, `scrape_all`'s shutdown then outlived the 6 min between the 60m
        # inner budget and the 66m step timeout — failing the whole shard, not just this Board.
        # Un-park once a per-board deadline bounds it.
        "workday:accenture/accenturecareers",
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
    are skipped, as are the vendor test Boards in :data:`EXCLUDED_BOARDS` (matched on
    ``ats:slug``) and the real-but-withheld ones in :data:`PARKED_BOARDS` (matched after the
    dedupe, on the canonical ``board_key``). This is the production source for a full scrape;
    ``config/companies.toml`` remains the small curated seed.
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
    return _drop_parked(_dedupe_boards(companies))


def board_identity(company: CompanyRef) -> str:
    """The Board's canonical key: ``board_key`` where the scraper can build one, the plain
    ``ats:slug`` where a malformed slug defeats it — never dropping the Board either way."""
    from headstart.scrapers.registry import SCRAPERS

    try:
        return SCRAPERS[company.ats](company.slug).board_key()
    except Exception:  # noqa: BLE001 - a malformed slug falls back to the plain key
        return f"{company.ats}:{company.slug}"


def _drop_parked(companies: list[CompanyRef]) -> list[CompanyRef]:
    """Drop :data:`PARKED_BOARDS`, matched on the same identity ``_dedupe_boards`` collapses on
    so the two can never disagree about which Board an entry names."""
    return [c for c in companies if board_identity(c).lower() not in PARKED_BOARDS]


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
    best: dict[str, tuple[str, CompanyRef]] = {}
    for company in companies:
        key = board_identity(company)
        canon = key.lower()
        current = best.get(canon)
        if current is None or key < current[0]:
            best[canon] = (key, company)
    return [company for _, company in best.values()]
