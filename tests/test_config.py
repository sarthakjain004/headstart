from pathlib import Path

from headstart.config import load_active_companies, load_companies
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
