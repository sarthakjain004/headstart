from pathlib import Path

from headstart.scrapers.registry import get_scraper

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCRAPED_AT = "2026-01-01T00:00:00+00:00"


def test_zoho_parse():
    html_text = (FIXTURES / "zoho_pnbcsl.html").read_text(encoding="utf-8")
    jobs = get_scraper("zoho", "pnbcsl.zohorecruit.in", "Fallback Co").parse(
        html_text, SCRAPED_AT
    )
    assert len(jobs) == 2
    j = jobs[0]
    assert j.id == "zoho:pnbcsl.zohorecruit.in:91994000000294203"
    assert j.ats == "zoho"
    assert j.company == "PNB Cards & Services Limited"  # from embedded org_info, not fallback
    assert j.title == "Sales Manager (Vehicle Loan)"
    assert j.remote is False
    assert j.department == "Sales - Marketing"
    assert "Kolkata" in j.location
    assert j.posted_at == "2026-01-16"
    assert j.url == (
        "https://pnbcsl.zohorecruit.in/jobs/Careers/"
        "91994000000294203/Sales-Manager-Vehicle-Loan-?source=CareerSite"
    )
    assert j.scraped_at == SCRAPED_AT


def test_zoho_no_jobs_input_returns_empty():
    jobs = get_scraper("zoho", "x.zohorecruit.in").parse("<html>nothing</html>", SCRAPED_AT)
    assert jobs == []
