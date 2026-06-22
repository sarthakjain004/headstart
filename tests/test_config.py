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
    assert SCRAPERS["greenhouse"].slug_from("stripe", "https://boards.greenhouse.io/stripe") == "stripe"
    assert SCRAPERS["zoho"].slug_from("01da", "https://01da.zohorecruit.eu") == "01da.zohorecruit.eu"
    assert (SCRAPERS["workday"].slug_from("3m/x", "https://3m.wd1.myworkdayjobs.com/x/")
            == "https://3m.wd1.myworkdayjobs.com/x")


def _write_active(active, name, rows):
    active.mkdir(exist_ok=True)
    body = "ats,tenant,url,jobs\n" + "".join(f"{r}\n" for r in rows)
    (active / name).write_text(body, encoding="utf-8")


def test_load_active_companies_maps_slug_and_filters(tmp_path):
    active = tmp_path / "active"
    _write_active(active, "greenhouse.csv", [
        "greenhouse,stripe,https://boards.greenhouse.io/stripe,12",
        "greenhouse,deadco,https://boards.greenhouse.io/deadco,0",  # jobs=0 -> dropped
    ])
    _write_active(active, "zoho.csv", ["zoho,01da,https://01da.zohorecruit.eu,3"])
    _write_active(active, "workday.csv",
                  ["workday,3m/search,https://3m.wd1.myworkdayjobs.com/search,7"])
    _write_active(active, "beehive.csv", ["beehive,foo,https://foo.beehivehcm.com,5"])  # no scraper

    companies = load_active_companies(active)
    by_ats = {c.ats: c for c in companies}
    assert set(by_ats) == {"greenhouse", "zoho", "workday"}  # jobs=0 + no-scraper ATS excluded
    assert by_ats["greenhouse"].slug == "stripe"
    assert by_ats["greenhouse"].name == "stripe"
    assert by_ats["zoho"].slug == "01da.zohorecruit.eu"
    assert by_ats["workday"].slug == "https://3m.wd1.myworkdayjobs.com/search"


def test_load_active_companies_min_jobs(tmp_path):
    active = tmp_path / "active"
    _write_active(active, "greenhouse.csv", [
        "greenhouse,a,https://boards.greenhouse.io/a,1",
        "greenhouse,b,https://boards.greenhouse.io/b,5",
    ])
    assert len(load_active_companies(active, min_jobs=1)) == 2
    assert len(load_active_companies(active, min_jobs=5)) == 1
