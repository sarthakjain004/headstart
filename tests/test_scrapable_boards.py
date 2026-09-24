"""The Scrapable Board list: which live ledger rows a run may pick, and under what identity."""

from pathlib import Path

from headstart import config
from headstart.config import PARKED_BOARDS
from headstart.scrapable_boards import ScrapableBoard, is_excluded, load


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
    merely promotes another instance's row to be `_dedupe_boards`' survivor, so the Board keeps
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
    demo Board as both `Dev2` and `dev2`, and the pair survives `_dedupe_boards`."""
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
