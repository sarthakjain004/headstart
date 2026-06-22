import html
import json
from pathlib import Path

from headstart.scrapers.registry import get_scraper

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCRAPED_AT = "2026-01-01T00:00:00+00:00"


def _page(records):
    """Wrap job records the way Zoho renders them: HTML-escaped JSON in the jobs input."""
    return f'<input type="hidden" value="{html.escape(json.dumps(records))}" id="jobs">'


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


def test_zoho_skips_locked_unpublished_and_idless():
    records = [
        {"id": "1", "Posting_Title": "Remote Engineer", "Remote_Job": True,
         "State": "Karnataka", "Country": "India"},                 # kept; no City -> "State, Country"
        {"id": "2", "Posting_Title": "Locked Role", "Is_Locked": True, "City": "Pune"},   # skipped
        {"id": "3", "Posting_Title": "Draft Role", "Publish": False, "City": "Delhi"},     # skipped
        {"Posting_Title": "No Id Role", "City": "Mumbai"},          # skipped (no id)
        {"id": "5", "Job_Opening_Name": "Ops Lead", "City": "Chennai"},  # title via Job_Opening_Name
    ]
    jobs = get_scraper("zoho", "acme.zohorecruit.com").parse(_page(records), SCRAPED_AT)
    assert [j.title for j in jobs] == ["Remote Engineer", "Ops Lead"]
    assert jobs[0].remote is True
    assert jobs[0].location == "Karnataka, India"
    assert jobs[1].location == "Chennai"
