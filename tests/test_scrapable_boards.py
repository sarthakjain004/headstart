"""The Scrapable Board list: which live ledger rows a run may pick, and under what identity."""

from pathlib import Path

from headstart import config, liveness
from headstart.config import PARKED_BOARDS
from headstart.scrapable_boards import ScrapableBoard, is_excluded, load
from headstart.scrapers.registry import SCRAPERS, company_from_row


def _write_ledger(ledger, name, rows):
    # rows: "ats,tenant,url,status,jobs,checked_at"
    ledger.mkdir(exist_ok=True)
    body = "ats,tenant,url,status,jobs,checked_at\n" + "".join(f"{r}\n" for r in rows)
    (ledger / name).write_text(body, encoding="utf-8")


def test_load_maps_slug_and_filters(tmp_path):
    ledger = tmp_path / "liveness"
    _write_ledger(
        ledger,
        "greenhouse.csv",
        [
            "greenhouse,stripe,https://boards.greenhouse.io/stripe,live,12,2026-07-01",
            "greenhouse,emptyco,https://boards.greenhouse.io/emptyco,live,0,2026-07-01",  # jobs=0 -> dropped
            "greenhouse,deadco,https://boards.greenhouse.io/deadco,dead,,2026-06-01",  # dead -> dropped
        ],
    )
    _write_ledger(
        ledger, "zoho.csv", ["zoho,01da,https://01da.zohorecruit.eu,live,3,2026-07-01"]
    )
    _write_ledger(
        ledger,
        "workday.csv",
        ["workday,3m/search,https://3m.wd1.myworkdayjobs.com/search,live,7,2026-07-01"],
    )
    _write_ledger(
        ledger,
        "beehive.csv",
        ["beehive,foo,https://foo.beehivehcm.com,live,5,2026-07-01"],
    )  # no scraper

    companies = load(ledger)
    by_ats = {c.ats: c for c in companies}
    assert set(by_ats) == {
        "greenhouse",
        "zoho",
        "workday",
    }  # jobs=0 + dead + no-scraper ATS excluded
    assert by_ats["greenhouse"].slug == "stripe"
    assert by_ats["greenhouse"].name == "stripe"
    assert by_ats["zoho"].slug == "01da.zohorecruit.eu"
    assert by_ats["workday"].slug == "https://3m.wd1.myworkdayjobs.com/search"


def test_parked_board_is_dropped_across_hosts_and_casings(tmp_path):
    """A park must survive every form the ledger carries the same Board in. Accenture sits on
    BOTH `wd3` and `wd103` and in two casings; keyed on one URL the park removes that row and
    merely promotes another instance's row to be `_elect`'s survivor, so the Board keeps
    being scraped while the entry looks effective. `board_key` is what collapses them."""
    ledger = tmp_path / "liveness"
    _write_ledger(
        ledger,
        "workday.csv",
        [
            "workday,a1,https://accenture.wd103.myworkdayjobs.com/AccentureCareers,live,2000,2026-08-13",
            "workday,a2,https://accenture.wd103.myworkdayjobs.com/accenturecareers,live,2000,2026-08-13",
            "workday,a3,https://accenture.wd3.myworkdayjobs.com/accenturecareers,live,2000,2026-08-13",
            "workday,a4,https://accenture.wd103.myworkdayjobs.com/avanadecareers,live,900,2026-08-13",
        ],
    )
    slugs = {c.slug for c in load(ledger)}
    assert slugs == {"https://accenture.wd103.myworkdayjobs.com/avanadecareers"}


def test_parked_boards_name_a_live_board_and_are_dropped(monkeypatch):
    """Against the real ledger, not a fixture — and asserted from both sides, because either
    half passes vacuously alone. The first cut of this park passed a synthetic test while the
    real ledger defeated it (a `wd3` row for the same Board survived dedupe); conversely an
    emptiness check alone stays green when a key is typo'd and parks nothing at all, which is
    the "silent lost coverage" PARKED_BOARDS' own comment warns about."""
    ledger = Path(__file__).resolve().parents[1] / "data" / "validate" / "liveness"

    selected = {c.lowercase_identity for c in load(ledger, min_jobs=0)}
    assert not (PARKED_BOARDS & selected), (
        f"parked Boards still selectable: {sorted(PARKED_BOARDS & selected)}"
    )

    monkeypatch.setattr(config, "PARKED_BOARDS", frozenset())
    unparked = {c.lowercase_identity for c in load(ledger, min_jobs=0)}
    assert PARKED_BOARDS <= unparked, (
        f"parked keys naming no live Board: {sorted(PARKED_BOARDS - unparked)}"
    )


def test_excluded_boards_are_dropped_but_look_alikes_are_kept(tmp_path):
    """The deny-list drops vendor test Boards without touching real ones that merely read
    like tests — `greenhouse:stage` is KKR's board, and dropping it would cost 128 real jobs."""
    ledger = tmp_path / "liveness"
    _write_ledger(
        ledger,
        "greenhouse.csv",
        [
            "greenhouse,staging,https://boards.greenhouse.io/staging,live,1,2026-08-12",
            "greenhouse,test1,https://boards.greenhouse.io/test1,live,1,2026-08-12",
            "greenhouse,stage,https://boards.greenhouse.io/stage,live,128,2026-08-12",
        ],
    )
    _write_ledger(
        ledger,
        "ripplehire.csv",
        [
            "ripplehire,prodtest,https://prodtest.ripplehire.com,live,863,2026-08-12",
            "ripplehire,paytm,https://paytm.ripplehire.com,live,10,2026-08-12",
        ],
    )
    slugs = {(c.ats, c.slug) for c in load(ledger)}
    assert slugs == {("greenhouse", "stage"), ("ripplehire", "paytm")}


def test_excluded_boards_match_regardless_of_slug_casing(tmp_path):
    """One entry must cover every casing the ledger carries — smartrecruiters lists the same
    demo Board as both `Dev2` and `dev2`, and the pair survives `_elect`."""
    ledger = tmp_path / "liveness"
    _write_ledger(
        ledger,
        "smartrecruiters.csv",
        [
            "smartrecruiters,Dev2,https://api.smartrecruiters.com/v1/companies/Dev2,live,9456,2026-08-12",
            "smartrecruiters,dev2,https://api.smartrecruiters.com/v1/companies/dev2,live,9456,2026-08-12",
        ],
    )
    assert load(ledger) == []


def test_excluded_boards_drops_walmart_non_workday_internal(monkeypatch):
    """Against the real ledger, not a fixture — and asserted from both sides, like
    ``test_parked_boards_name_a_live_board_and_are_dropped`` above, because a one-sided check
    passes vacuously if the ledger row's status ever drifts off ``live`` on its own. The ledger
    carries this dead board under three duplicate rows (`non-workdayinternal`,
    `walmart/non-workdayinternal`, `walmart.wd5.myworkdayjobs.com/non-workdayinternal`) that
    all resolve, via `slug_from`'s URL column, to the same lowercased key — one entry must drop
    all three."""
    ledger = Path(__file__).resolve().parents[1] / "data" / "validate" / "liveness"
    key = "https://walmart.wd5.myworkdayjobs.com/non-workdayinternal"

    slugs = {c.slug.lower() for c in load(ledger, min_jobs=0) if c.ats == "workday"}
    assert key not in slugs

    monkeypatch.setattr(config, "EXCLUDED_BOARDS", frozenset())
    unexcluded_slugs = {
        c.slug.lower() for c in load(ledger, min_jobs=0) if c.ats == "workday"
    }
    assert key in unexcluded_slugs, "ledger no longer names this Board at all"


def test_excluded_boards_drop_oracles_taleo_demo_tenant(monkeypatch):
    """Against the real ledger and from both sides, like the Walmart test above: a Taleo
    Enterprise slug is the whole canonical section URL, so a key spelt any other way would
    silently exclude nothing."""
    ledger = Path(__file__).resolve().parents[1] / "data" / "validate" / "liveness"

    def pmg_sections():
        return {
            c.slug
            for c in load(ledger, min_jobs=0)
            if c.slug.startswith("https://pmg.taleo.net/")
        }

    assert pmg_sections() == set()
    monkeypatch.setattr(config, "EXCLUDED_BOARDS", frozenset())
    assert len(pmg_sections()) == 2, (
        "ledger no longer holds pmg's two sections on live rows"
    )


def test_load_min_jobs(tmp_path):
    ledger = tmp_path / "liveness"
    _write_ledger(
        ledger,
        "greenhouse.csv",
        [
            "greenhouse,a,https://boards.greenhouse.io/a,live,1,2026-07-01",
            "greenhouse,b,https://boards.greenhouse.io/b,live,5,2026-07-01",
        ],
    )
    assert len(load(ledger, min_jobs=1)) == 2
    assert len(load(ledger, min_jobs=5)) == 1


def test_a_scrapable_board_carries_its_identity_in_both_casings():
    """The identity is the scraper's own `board_key` casing; the lowercased copy is what the
    dedupe and the park compare on. Both are computed from the reference, never passed in."""
    board = ScrapableBoard(
        "workday", "https://Acme.wd1.myworkdayjobs.com/External", "Acme"
    )
    assert isinstance(board, config.CompanyRef)
    assert board.identity == "workday:Acme/External"
    assert board.lowercase_identity == "workday:acme/external"


def test_a_slug_that_will_not_parse_keeps_the_plain_ats_slug_identity():
    """ADR-0155's lenient form: a malformed slug names its Board rather than dropping it."""
    board = ScrapableBoard("workday", "not-a-url")
    assert board.identity == "workday:not-a-url"


def test_is_excluded_matches_any_casing_and_nothing_else():
    assert is_excluded("smartrecruiters", "Dev2")
    assert is_excluded("smartrecruiters", "dev2")
    assert not is_excluded("greenhouse", "stage")  # KKR's real Board, not a test tenant


# --- electing a Board's representative row (ADR-0219) ---
# Several ledger rows can name one Board. The verdict is the newest verified row's, the key keeps
# today's lex-min casing, and the slug comes from the newest live row that carries that key.


def test_a_newer_dead_row_takes_the_board_out(tmp_path):
    """novozymes/novonesis_careers: live on 2026-07-03, dead under another spelling on 07-27, and
    dead under both when re-probed 2026-09-25. Any-live-row-wins kept scraping it."""
    ledger = tmp_path / "liveness"
    _write_ledger(
        ledger,
        "workday.csv",
        [
            "workday,a,https://novozymes.wd103.myworkdayjobs.com/Novonesis_Careers,live,80,2026-07-03",
            "workday,b,https://novozymes.wd103.myworkdayjobs.com/novonesis_careers,dead,,2026-07-27",
        ],
    )
    assert load(ledger, min_jobs=0) == []


def test_a_dead_row_from_the_same_day_as_a_live_one_keeps_the_board(tmp_path):
    """The ledger dates a probe to the day, so a same-day `dead` is not newer. Re-probed
    2026-09-25, all 45 such groups answered live (ADR-0219)."""
    ledger = tmp_path / "liveness"
    _write_ledger(
        ledger,
        "workday.csv",
        [
            "workday,a,https://lego.wd103.myworkdayjobs.com/LEGO_External,live,391,2026-07-03",
            "workday,b,https://lego.wd3.myworkdayjobs.com/LEGO_External,dead,,2026-07-03",
        ],
    )
    assert [b.slug for b in load(ledger, min_jobs=0)] == [
        "https://lego.wd103.myworkdayjobs.com/LEGO_External"
    ]


def test_a_newer_unknown_row_never_overrides_a_live_one(tmp_path):
    """A probe that earned no verdict is no evidence (#650's `*-wdN-*` display rows)."""
    ledger = tmp_path / "liveness"
    _write_ledger(
        ledger,
        "workday.csv",
        [
            "workday,a,https://target.wd5.myworkdayjobs.com/targetcareers,live,900,2026-07-03",
            "workday,target-wd5-targetcareers,https://target.wd5.myworkdayjobs.com/targetcareers,unknown,,2026-08-28",
        ],
    )
    assert len(load(ledger, min_jobs=0)) == 1


def test_the_key_keeps_its_casing_when_a_newer_row_spells_it_otherwise(tmp_path):
    """The key is the lex-min identity among live rows, the casing every served id carries.
    Electing the newest row outright would re-key 1,652 Workday Boards (ADR-0219)."""
    ledger = tmp_path / "liveness"
    _write_ledger(
        ledger,
        "workday.csv",
        [
            "workday,a,https://3m.wd1.myworkdayjobs.com/Search,live,500,2026-07-03",
            "workday,b,https://3m.wd1.myworkdayjobs.com/search,live,510,2026-09-20",
        ],
    )
    (board,) = load(ledger, min_jobs=0)
    assert board.identity == "workday:3m/Search"
    assert board.slug == "https://3m.wd1.myworkdayjobs.com/Search"


def test_the_slug_comes_from_the_newest_live_row_carrying_the_key(tmp_path):
    """One site on two data centres: both rows carry the same key, and the newer one's pod is the
    one fetched, not whichever the ledger lists first."""
    ledger = tmp_path / "liveness"
    _write_ledger(
        ledger,
        "workday.csv",
        [
            "workday,a,https://acronis.wd3.myworkdayjobs.com/acronis_careers,live,17,2026-07-03",
            "workday,b,https://acronis.wd502.myworkdayjobs.com/acronis_careers,live,17,2026-08-14",
        ],
    )
    (board,) = load(ledger, min_jobs=0)
    assert board.slug == "https://acronis.wd502.myworkdayjobs.com/acronis_careers"
    assert board.name == "b"


def test_the_hiring_list_elects_the_same_row_as_the_scrapable_list(tmp_path):
    """`min_jobs` reads the elected row's count, so it cannot promote another row: before,
    filtering rows first made the curated feed fetch a different pod than the scrape."""
    ledger = tmp_path / "liveness"
    _write_ledger(
        ledger,
        "workday.csv",
        [
            "workday,a,https://eppendorf.wd3.myworkdayjobs.com/starlabcareers,live,4,2026-07-03",
            "workday,b,https://eppendorf.wd502.myworkdayjobs.com/starlabcareers,live,0,2026-08-14",
        ],
    )
    (scrapable,) = load(ledger, min_jobs=0)
    assert scrapable.slug == "https://eppendorf.wd502.myworkdayjobs.com/starlabcareers"
    assert load(ledger, min_jobs=1) == []


def test_no_board_in_the_committed_ledger_changes_key():
    """Against the real ledger: every Scrapable Board carries the lex-min identity among its live
    rows, the key ADR-0023 elected and the index already uses. A rule that elected the newest row
    outright re-keys 1,652 Workday Boards, and this is what would catch it."""
    ledger = Path(__file__).resolve().parents[1] / "data" / "validate" / "liveness"
    lex_min: dict[str, str] = {}
    for csv_path in sorted(ledger.glob("*.csv")):
        if csv_path.stem not in SCRAPERS:
            continue
        for v in liveness.load(csv_path).values():
            if v.status == liveness.LIVE:
                c = company_from_row(csv_path.stem, v.tenant, v.url)
                board = ScrapableBoard(ats=c.ats, slug=c.slug, name=c.name)
                key = board.lowercase_identity
                lex_min[key] = min(lex_min.get(key, board.identity), board.identity)
    changed = {
        b.identity: lex_min[b.lowercase_identity]
        for b in load(ledger, min_jobs=0)
        if b.identity != lex_min[b.lowercase_identity]
    }
    assert not changed, (
        f"{len(changed)} Boards re-keyed, e.g. {list(changed.items())[:3]}"
    )


def test_a_same_day_tie_keeps_the_row_the_ledger_lists_first(tmp_path):
    """A probe date is the only evidence of recency, and a tie carries none. Re-probed 2026-09-25,
    breaking ties on job count instead moved 51 Boards and picked the pod that answered on 11."""
    ledger = tmp_path / "liveness"
    _write_ledger(
        ledger,
        "workday.csv",
        [
            "workday,a,https://amadeus.wd502.myworkdayjobs.com/jobs,live,124,2026-07-03",
            "workday,b,https://amadeus.wd3.myworkdayjobs.com/jobs,live,130,2026-07-03",
        ],
    )
    (board,) = load(ledger, min_jobs=0)
    assert board.slug == "https://amadeus.wd502.myworkdayjobs.com/jobs"


def test_load_reports_why_each_row_did_not_become_a_board(tmp_path, caplog):
    """One line per load that accounts for every row, so a shrunken list names the rule."""
    ledger = tmp_path / "liveness"
    _write_ledger(
        ledger,
        "greenhouse.csv",
        [
            "greenhouse,stripe,https://boards.greenhouse.io/stripe,live,12,2026-07-01",
            "greenhouse,Stripe,https://boards.greenhouse.io/Stripe,live,12,2026-07-01",  # collapsed
            "greenhouse,emptyco,https://boards.greenhouse.io/emptyco,live,0,2026-07-01",  # min_jobs
            "greenhouse,deadco,https://boards.greenhouse.io/deadco,dead,,2026-06-01",  # no live
        ],
    )
    _write_ledger(
        ledger,
        "workday.csv",
        ["workday,x,not-a-url,dead,,2026-06-01"],  # unparseable non-live
    )
    with caplog.at_level("INFO", logger="headstart.scrapable_boards"):
        assert len(load(ledger)) == 1
    (line,) = [r.message for r in caplog.records if "scrapable boards:" in r.message]
    assert line == (
        "scrapable boards: 1 from 5 ledger rows — excluded 0, alias-buried 0, "
        "unparseable non-live 1, collapsed 1, no live or newer dead 1, "
        "under min_jobs=1 1, parked 0"
    )
