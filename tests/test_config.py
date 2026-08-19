from pathlib import Path

from headstart import config
from headstart.config import (
    EXCLUDED_BOARDS,
    PARKED_BOARDS,
    board_identity,
    load_active_companies,
    load_companies,
)
from headstart.scrapers.registry import SCRAPERS

CONFIG = Path(__file__).resolve().parent.parent / "config" / "companies.toml"


def test_seed_companies_load():
    companies = load_companies(CONFIG)
    assert len(companies) >= 15
    pairs = {(c.ats, c.slug) for c in companies}
    assert ("greenhouse", "stripe") in pairs
    assert all(c.ats in {"greenhouse", "lever", "ashby", "zoho"} for c in companies)
    assert all(c.slug for c in companies)


def test_slug_from_default_and_overrides():
    # default: the bare tenant label; zoho: careers host; workday: full careers URL
    assert (
        SCRAPERS["greenhouse"].slug_from(
            "stripe", "https://boards.greenhouse.io/stripe"
        )
        == "stripe"
    )
    assert (
        SCRAPERS["zoho"].slug_from("01da", "https://01da.zohorecruit.eu")
        == "01da.zohorecruit.eu"
    )
    assert (
        SCRAPERS["workday"].slug_from("3m/x", "https://3m.wd1.myworkdayjobs.com/x/")
        == "https://3m.wd1.myworkdayjobs.com/x"
    )


def _write_ledger(ledger, name, rows):
    # rows: "ats,tenant,url,status,jobs,checked_at"
    ledger.mkdir(exist_ok=True)
    body = "ats,tenant,url,status,jobs,checked_at\n" + "".join(f"{r}\n" for r in rows)
    (ledger / name).write_text(body, encoding="utf-8")


def test_load_active_companies_maps_slug_and_filters(tmp_path):
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

    companies = load_active_companies(ledger)
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


def test_skip_list_keys_are_lowercase():
    """Both lookups lowercase the ledger's key, so an entry carrying a capital could never
    match — it would sit in the list looking effective while the Board kept being scraped."""
    assert all(key == key.lower() for key in EXCLUDED_BOARDS)
    assert all(key == key.lower() for key in PARKED_BOARDS)


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
    slugs = {c.slug for c in load_active_companies(ledger)}
    assert slugs == {"https://accenture.wd103.myworkdayjobs.com/avanadecareers"}


def test_parked_boards_name_a_live_board_and_are_dropped(monkeypatch):
    """Against the real ledger, not a fixture — and asserted from both sides, because either
    half passes vacuously alone. The first cut of this park passed a synthetic test while the
    real ledger defeated it (a `wd3` row for the same Board survived dedupe); conversely an
    emptiness check alone stays green when a key is typo'd and parks nothing at all, which is
    the "silent lost coverage" PARKED_BOARDS' own comment warns about."""
    ledger = Path(__file__).resolve().parents[1] / "data" / "validate" / "liveness"

    selected = {
        board_identity(c).lower() for c in load_active_companies(ledger, min_jobs=0)
    }
    assert not (PARKED_BOARDS & selected), (
        f"parked Boards still selectable: {sorted(PARKED_BOARDS & selected)}"
    )

    monkeypatch.setattr(config, "PARKED_BOARDS", frozenset())
    unparked = {
        board_identity(c).lower() for c in load_active_companies(ledger, min_jobs=0)
    }
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
    slugs = {(c.ats, c.slug) for c in load_active_companies(ledger)}
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
    assert load_active_companies(ledger) == []


def test_excluded_boards_drops_walmart_non_workday_internal():
    """Against the real ledger, not a fixture — the ledger carries this dead wd5 board under
    three tenant-key casings/formats (`non-workdayinternal`, `walmart/non-workdayinternal`,
    `walmart.wd5.myworkdayjobs.com/non-workdayinternal`), and one lowercased EXCLUDED_BOARDS
    entry must drop all three, not just the one it was copied from."""
    ledger = Path(__file__).resolve().parents[1] / "data" / "validate" / "liveness"
    slugs = {
        c.slug.lower()
        for c in load_active_companies(ledger, min_jobs=0)
        if c.ats == "workday"
    }
    assert "https://walmart.wd5.myworkdayjobs.com/non-workdayinternal" not in slugs


def test_load_active_companies_min_jobs(tmp_path):
    ledger = tmp_path / "liveness"
    _write_ledger(
        ledger,
        "greenhouse.csv",
        [
            "greenhouse,a,https://boards.greenhouse.io/a,live,1,2026-07-01",
            "greenhouse,b,https://boards.greenhouse.io/b,live,5,2026-07-01",
        ],
    )
    assert len(load_active_companies(ledger, min_jobs=1)) == 2
    assert len(load_active_companies(ledger, min_jobs=5)) == 1
