from pathlib import Path

from headstart.config import EXCLUDED_BOARDS, PARKED_BOARDS, load_companies
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


def test_skip_list_keys_are_lowercase():
    """Both lookups lowercase the ledger's key, so an entry carrying a capital could never
    match — it would sit in the list looking effective while the Board kept being scraped."""
    assert all(key == key.lower() for key in EXCLUDED_BOARDS)
    assert all(key == key.lower() for key in PARKED_BOARDS)
