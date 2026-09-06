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
        # Zwayam's own demo/QA tenants, confirmed by reading their board content on 2026-08-27
        # rather than inferred from the slug — the same bar the darwinbox entries below were held
        # to. `testcompany.cluster3` is the worst of them and the reason this entry exists: it is
        # `live` with 77 postings titled "TEDT", "tesdt", "xyz_zbc", "dsf", "14 dec", whose
        # locations are "fd", "sd", "c", "dfs", "H". `zhirematetest` posts "Test Job", "Hi",
        # "Hello Test", "Testing job".
        #
        # Deliberately NOT excluded, though their slugs invite it: `ssttest` ("Software Engineer",
        # "Implementation Engineer") and `hirematetest1` ("Senior Java Developer") carry ordinary
        # titles and real Indian locations, so the content does not confirm the slug's hint. Two
        # further vendor-shaped hosts, `ratestcompany` (0 postings) and `wisseninfotechhiremate`
        # (1), are left for the same reason.
        "zwayam:testcompany.cluster3.openings.co",
        "zwayam:zhirematetest.openings.co",
        "darwinbox:training",  # company "training"; "Ali marketing Executive", "SK_Jr. Associate"
        # More of Darwinbox's own demo/QA/training tenants (found during darwinbox's salary-
        # extraction pass, 2026-08-22, reading real board content — not from the slug alone).
        # Confirmed by content: `minion` ("abc1", "Testing", "Animator USA" repeated, nonsensical
        # salary_range values like "INR 100-150"); `southuat` ("Test Pre Offer", "Gulf Dummy",
        # "Demo_Unicommerce"); `darwinboxdemo` (title/description MISMATCH — the "Customer Success
        # Manager" posting's own description is for a "Chemistry Teacher" role, plus literal
        # "Please enter job description" placeholders elsewhere — Darwinbox's own literal demo
        # tenant); `spoc` ("SUPERADMINSUPERADMINSUPERADMIN SUPERADMIN" x3, "Sai_Test341", "VP HR
        # Test Test", "Test 123", "Job created for 7.6 on all servers"); `treebotest` ("Tera Soft
        # Recruitment Testing", 61% empty/placeholder descriptions); `homecredituat` and
        # `partnerdemodeloitte` (unfilled template merge-fields verbatim in the description,
        # "#*Group Company*# Designation: #*Designation*#..."); `training14` (same "training"
        # family as the entry above — a numeric template ID contaminates BOTH the title AND
        # location fields identically and repeatedly: "3891_Manager" @ "3891_Singapore, Singapore,
        # Singapore, Singapore", "DB156_Manager" @ "DB156_Hyderabad, ...", "00002_Manager" @
        # "00002_Los Angeles, ...", "DB163 MANAGER" @ "DB163 Los Angeles, ..." — plus a literal
        # "Darwinbox Sample" title and gibberish ("sasasa")); `training2` (same family, weaker
        # signal — "COE senior manager472"/"Sr.Manager_KK" carry the same stray-numeric-suffix
        # shape, title-only on its 8 postings, none of `training14`'s title/location contamination
        # since there's no location field affected in this smaller sample). Checked
        # and deliberately kept: `banyanhcmuat` (a "uat"-shaped slug, same risk class as
        # `homecredituat`/`southuat`, but its content is genuinely realistic hospitality-role
        # postings — real titles like "Chief Steward", "Sushi Chef", even a Chinese-language
        # "预订经理" (Reservations Manager) — with no test/dummy signal anywhere and no separate
        # "banyantree" tenant to suggest this is a redundant staging copy; excluding it would be
        # exactly the slug-pattern reasoning this list's own rule warns against).
        "darwinbox:darwinboxdemo",  # 17 postings
        "darwinbox:homecredituat",  # 34 postings
        "darwinbox:minion",  # 135 postings
        "darwinbox:partnerdemodeloitte",  # 3 postings
        "darwinbox:southuat",  # 59 postings
        "darwinbox:spoc",  # 88 postings
        "darwinbox:training14",  # 30 postings
        "darwinbox:training2",  # 8 postings
        "darwinbox:treebotest",  # 142 postings
        "greenhouse:staging",  # company "Staging Site Board"; its one posting is titled "TEST"
        "greenhouse:test1",  # company "Test"
        # Keka's own demo/QA tenants (found during keka's salary-extraction pass, 2026-08-22,
        # reading real board content — not from the slug alone). Confirmed by content: `csdemo`'s
        # organization name is literally "keka cs" (Keka's own Customer Success team), with job
        # titles including "ABC", "Bacancy - Demo", "Keka Test Engineer", and several employees'
        # own personal test postings ("Chaitanya test", "Demo Sneh"); `salesdemo`'s organization
        # name is the nonsensical "Out comes Operating" and its own LinkedIn link points to Keka's
        # own company page, not an independent client. Checked and deliberately kept:
        # `keka:lambdatest` and `keka:testsigma` (real companies whose own brand names happen to
        # contain "test"), `keka:vtest` (a single real-looking job posting, not enough evidence
        # either way to exclude) — exactly the false-positive risk this list's own rule warns
        # against.
        "keka:csdemo",  # 681 postings
        "keka:salesdemo",  # 153 postings
        # Lever's own demo/sandbox/QA tenants (found during lever's salary-extraction pass,
        # 2026-08-22, reading real board content — not from the slug alone, per this list's own
        # rule). 1,769 fabricated postings total. Confirmed by content: template/placeholder
        # titles ("[TEMPLATE] Customer Experience Specialist", "***POSTING TEMPLATE - ENGINEERING",
        # "Account Executive (copy)", "Draft External Job", "Ice cream eater", "# Test Job 123",
        # "[JEN TEST] WHITELISTED POSTING FOR RESUME REQ OVERRIDE") with no real company name
        # attached, unlike `lever:sandboxvr` (Sandbox VR, a real VR entertainment company; kept).
        "lever:leverdemo",  # 383 postings
        "lever:leverdemo-8",  # 429 postings
        "lever:leverdemo193",  # 16 postings
        "lever:leverdemo50000",  # 7 postings
        "lever:leverdemo93321",  # 4 postings
        "lever:leverdemo956",  # 15 postings
        "lever:levertest",  # 894 postings
        "lever:salesdemo-jr",  # 21 postings
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
        # LTIMindtree's retired vanity host. Its TLS cert is SAP's own unconfigured-vanity
        # placeholder (CN=certificate-not-found.jobs2web.com, no SAN for this hostname) —
        # verified live 2026-08-19, failing the same way in all 6 recent pipeline runs. Its
        # CNAME chain (-> larsenturbo.jobs2web.com -> rmk12.jobs2web.com) is identical to
        # `careers.ltm.com`, and requesting that jobs2web host with `Host: careers.ltimindtree.com`
        # 301s to `careers.ltm.com/xml/sitemap.xml` — same SuccessFactors tenant, now served
        # under its current vanity domain, which is already live in the ledger with a valid
        # cert (`careers.ltm.com`, 49 postings, comparable to this host's last-good 55). Safe to
        # drop: it removes the permanently broken duplicate, not LTIMindtree's jobs.
        "successfactors:careers.ltimindtree.com",
        # Three more SuccessFactors redirect-aliases used to sit here by hand (CONA, HCLTech,
        # Bombardier — #212, #218). They now come from `data/validate/aliases/successfactors.csv`,
        # regenerated live by `dedupe_boards.py`, so the fact has one home instead of two
        # (ADR-0111). The two above stay hand-listed: Capgemini's is a *test* board rather than a
        # duplicate, and LTIMindtree's TLS cert is permanently broken, so the scan can never reach
        # it to prove what its redirect says.
        # Trakstar Hire's own demo/QA tenants (found during trakstar's salary-extraction pass,
        # 2026-08-22, reading real board content — not from the slug alone, per this list's own
        # rule). Confirmed by content: `bbtest`'s sole posting is titled "Bug Buster"; `smoketest`
        # (66 postings, confirmed via its own `jobfeeds` RSS feed — the careers-page HTML this
        # scraper reads renders only the first 25 of them, a separate, real truncation bug under
        # its own investigation, unrelated to this exclusion) is unmistakably a vendor
        # feature-testing sandbox — "Custom Fields - No Fields", "Custom Fields - With all 9
        # Fields", "Django Upgrade Final Test", "Example Logo", "filter test" (x2), "google account
        # 2", "Google Smoke Testing", "IT Coordinator job description" — scattered across many
        # countries (Tijuana x18, Chennai x9, Bengaluru x8, and 20+ singletons), not city-clustered
        # like the other two; `testbass`'s sole posting ("Ruby on Rails Developer") reads plausibly
        # on its own but shares `bbtest`'s same Bangalore, India location and single-generic-
        # posting shape, with no independent company signal anywhere. Checked and deliberately
        # kept: `zutest` (a "System Administrator – Computing Services Department" posting in Abu
        # Dhabi, UAE — detailed, professionally formatted, no test/dummy signal in the content
        # itself) — exactly the slug-pattern-alone reasoning this list's own rule warns against.
        "trakstar:bbtest",  # 1 posting
        "trakstar:smoketest",  # 66 postings (jobfeeds RSS count)
        "trakstar:testbass",  # 1 posting
        # SenseHQ's own dev/test tenant (found during sensehq's salary-extraction pass,
        # 2026-08-23, reading real board content). Confirmed by content: 204 postings, the
        # large majority QA/testing-tool placeholder titles — "Cypress 1" (41), "QA test" (13),
        # "TESTING" (19), "Cypress test" (3), "QA testTest Lead" (3), template stand-ins
        # "Job template"/"Crm template"/"Crm job" (6+3+3), "sdaa" (9) — plus real-looking
        # titles duplicated with " copy" appended ("Sales development Representative" /
        # "Sales development Representative copy"). A feature-testing sandbox, not a real
        # employer. The "-dev" slug matches, but per this list's own rule that alone would
        # not have been enough.
        "sensehq:trm-dev",  # 204 postings
        # Blackstone's own test sites; the second is named for what it serves. Workday slugs
        # ARE the careers URL, so these keys are longer than the rest.
        "workday:https://blackstone.wd1.myworkdayjobs.com/marni_test_site",
        "workday:https://blackstone.wd1.myworkdayjobs.com/marni_test_ghost_posting_site",
        # Walmart's wd5 tenant was retired (superseded by wd504's WalmartExternal, the real
        # board, 2000 postings). wd5 now 303-redirects every jobs query to a Workday
        # maintenance page; our client follows it to a 200 of maintenance-page HTML, so
        # response.json() throws JSONDecodeError on every run (confirmed live 2026-08-19, not
        # just historical logs). The ledger carries this dead URL under three duplicate rows
        # (the match is on `slug_from`'s URL, not the `tenant` column, so only the URL's two
        # casings matter) — the lowercased key here covers both.
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
        # 1,162 postings, real and un-fabricated — unlike Accenture above this board finishes
        # every run, it's just consistently the worst floor-bound shard once it does: the single
        # most expensive board across 10+ consecutive pipeline runs (~19-37 min each,
        # docs/pipeline/2026-08-20_cadence-settle-in-and-critical-path.md §3), now the run-owning
        # straggler after the six Workday retail boards were narrowed instead of parked (§6).
        # No per-category narrowing exists for this scraper the way Workday's
        # `_FIXED_FACETS_BY_SLUG` does — SuccessFactors' listing surfaces (sitemap/search/RSS)
        # carry no facet mechanism to fetch only a tech-labeled subset. Un-park once one exists,
        # or a per-board timeout bounds the cost instead.
        "successfactors:careers.ey.com",
        # 23,806 postings for **136 tech jobs** — the worst cost-to-yield Board in the corpus, and
        # the run-owning straggler in all 7 of the runs 33065892407..33151091246. Measured from
        # those runs' own `scrape_run` lines: 1,466 s, against a shard total of 1,471 s. It is not
        # merely the slowest board in its shard, it *is* its shard — everything else had finished
        # 5 s earlier, and `board seconds` for that shard reads p50 0.4, p99 118, max 1,466.
        # Scrape is 42% of the run's wall clock and straggler-bound, so this one Board costs ~10
        # min of critical path per run to contribute 0.04% of the index (136 of 330,487 rows,
        # `data/state/board_priority.csv` 2026-08-28) — 10.8 s of makespan per tech job.
        # Un-park if its tech yield ever justifies the floor, or once a per-board deadline bounds
        # it — the same condition that would un-park Accenture above.
        "smartrecruiters:adeebaeservicespvtltd",
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
    ``ats:slug``), the Boards buried as duplicates in the alias ledger (ADR-0111, matched on the
    slug) and the real-but-withheld ones in :data:`PARKED_BOARDS` (matched after the
    dedupe, on the canonical ``board_key``). This is the production source for a full scrape;
    ``config/companies.toml`` remains the small curated seed.

    Two dedupes run here and they catch different things: the alias ledger is *semantic* (one
    company, two hostnames, no shared key to collapse on) and ``_dedupe_boards`` below is
    *syntactic* (one hostname, two spellings). Neither subsumes the other.
    """
    from headstart import board_aliases, liveness
    from headstart.scrapers.registry import DISABLED_ATS, SCRAPERS

    ledger_dir = Path(ledger_dir)
    companies: list[CompanyRef] = []
    for csv_path in sorted(ledger_dir.glob("*.csv")):
        scraper = SCRAPERS.get(csv_path.stem)
        if scraper is None or scraper.ats in DISABLED_ATS:
            continue
        # Boards this ATS publishes twice, buried in favour of their canonical (ADR-0111). Dropped
        # here beside EXCLUDED_BOARDS because both are keyed on the slug; the *syntactic* dedupe
        # below cannot do it, since two different hostnames share no `board_key` to collapse on.
        aliases = board_aliases.load(board_aliases.path_for(ledger_dir, scraper.ats))
        for v in liveness.load(csv_path).values():
            if v.status != liveness.LIVE or (v.jobs or 0) < min_jobs:
                continue
            slug = scraper.slug_from(v.tenant, v.url)
            if f"{scraper.ats}:{slug}".lower() in EXCLUDED_BOARDS or slug in aliases:
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
