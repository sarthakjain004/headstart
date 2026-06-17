from pathlib import Path

from headstart.config import load_companies

CONFIG = Path(__file__).resolve().parent.parent / "config" / "companies.toml"


def test_seed_companies_load():
    companies = load_companies(CONFIG)
    assert len(companies) >= 15
    pairs = {(c.ats, c.slug) for c in companies}
    assert ("greenhouse", "stripe") in pairs
    assert all(c.ats in {"greenhouse", "lever", "ashby", "zoho"} for c in companies)
    assert all(c.slug for c in companies)
