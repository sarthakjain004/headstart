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
    assert (
        j.company == "PNB Cards & Services Limited"
    )  # from embedded org_info, not fallback
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
    jobs = get_scraper("zoho", "x.zohorecruit.in").parse(
        "<html>nothing</html>", SCRAPED_AT
    )
    assert jobs == []


def test_zoho_skips_locked_unpublished_and_idless():
    records = [
        {
            "id": "1",
            "Posting_Title": "Remote Engineer",
            "Remote_Job": True,
            "State": "Karnataka",
            "Country": "India",
        },  # kept; no City -> "State, Country"
        {
            "id": "2",
            "Posting_Title": "Locked Role",
            "Is_Locked": True,
            "City": "Pune",
        },  # skipped
        {
            "id": "3",
            "Posting_Title": "Draft Role",
            "Publish": False,
            "City": "Delhi",
        },  # skipped
        {"Posting_Title": "No Id Role", "City": "Mumbai"},  # skipped (no id)
        {
            "id": "5",
            "Job_Opening_Name": "Ops Lead",
            "City": "Chennai",
        },  # title via Job_Opening_Name
    ]
    jobs = get_scraper("zoho", "acme.zohorecruit.com").parse(_page(records), SCRAPED_AT)
    assert [j.title for j in jobs] == ["Remote Engineer", "Ops Lead"]
    assert jobs[0].remote is True
    assert jobs[0].location == "Karnataka, India"
    assert jobs[1].location == "Chennai"


# --- location: join City, State, Country instead of the old `City or (State, Country)` -----------
# The old code discarded a real Country on 85.69% of jobs whenever City was present (audit:
# experiment/location-audit-2026-08-25/zoho.md).


def test_zoho_location_joins_city_state_and_country():
    records = [
        {
            "id": "1",
            "Posting_Title": "Backend Engineer",
            "City": "Tampa",
            "State": "Florida",
            "Country": "United States",
        }
    ]
    jobs = get_scraper("zoho", "acme.zohorecruit.com").parse(_page(records), SCRAPED_AT)
    assert jobs[0].location == "Tampa, Florida, United States"


def test_zoho_location_dedupes_city_equal_state_and_state_equal_country():
    records = [
        {"id": "1", "Posting_Title": "A", "City": "Riyadh", "State": "Riyadh"},
        {"id": "2", "Posting_Title": "B", "State": "Singapore", "Country": "Singapore"},
    ]
    jobs = get_scraper("zoho", "acme.zohorecruit.com").parse(_page(records), SCRAPED_AT)
    assert jobs[0].location == "Riyadh"
    assert jobs[1].location == "Singapore"


def test_zoho_location_filters_junk_state():
    records = [
        {
            "id": "1",
            "Posting_Title": "A",
            "City": "Casablanca",
            "State": ".",
            "Country": "Morocco",
        }
    ]
    jobs = get_scraper("zoho", "acme.zohorecruit.com").parse(_page(records), SCRAPED_AT)
    assert jobs[0].location == "Casablanca, Morocco"


def test_zoho_location_keeps_placeless_city_marker_and_appends_country():
    # H1: a placeless City marker used to win the old truthy `or` and discard a real Country.
    # Remote_Job is False here on purpose — the audit found 0/68 such jobs have it set, so the
    # fix can't key off that flag; it has to fall out of the plain join.
    records = [
        {
            "id": "1",
            "Posting_Title": "A",
            "City": "Remote",
            "Country": "Luxembourg",
            "Remote_Job": False,
        }
    ]
    jobs = get_scraper("zoho", "acme.zohorecruit.com").parse(_page(records), SCRAPED_AT)
    assert jobs[0].location == "Remote, Luxembourg"


def test_zoho_location_none_when_every_part_blank_or_junk():
    records = [{"id": "1", "Posting_Title": "A", "State": "-"}]
    jobs = get_scraper("zoho", "acme.zohorecruit.com").parse(_page(records), SCRAPED_AT)
    assert jobs[0].location is None


def test_zoho_parse_fills_description_from_details():
    # tenants that omit the Job_Description column get it from the detail pass
    records = [
        {"id": "1", "Posting_Title": "Backend Engineer"},
        {"id": "2", "Posting_Title": "Data Engineer", "Job_Description": "<p>Own</p>"},
    ]
    raw = {
        "page": _page(records),
        "details": {"1": {"id": "1", "Job_Description": "<p>5+ years of Python</p>"}},
    }
    jobs = get_scraper("zoho", "acme.zohorecruit.com").parse(raw, SCRAPED_AT)
    assert jobs[0].description == "5+ years of Python"
    assert (
        jobs[1].description == "Own"
    )  # listing value wins when the detail fetch found nothing


def _detail_page(record):
    """A detail page the way Zoho renders it: JS-escaped JSON inside JSON.parse('…')."""
    payload = json.dumps([record])
    escaped = payload.replace("\\", "\\\\").replace('"', "\\x22").replace("/", "\\/")
    return f"<script>var jobs = JSON.parse('{escaped}');</script>"


def test_zoho_fetch_raw_detail_pass(monkeypatch):
    monkeypatch.setenv(
        "HEADSTART_ASYNC_FANOUT", "0"
    )  # keep the detail pass on the sync path
    # Every published, non-locked record gets a detail fetch, not just description-less ones —
    # Salary/Currency live ONLY on the detail page, so gating on a listing-level description
    # meant most jobs never had it fetched at all (user decision 2026-08-24).
    records = [
        {"id": "1", "Posting_Title": "Backend Engineer"},
        {"id": "2", "Posting_Title": "Filled", "Job_Description": "<p>x</p>"},
        {"id": "3", "Posting_Title": "Locked", "Is_Locked": True},
    ]
    page = _page(records)
    fetched = []

    def _get(self, url=None):
        if url is None:
            return page
        fetched.append(url)
        return _detail_page({"id": "1", "Job_Description": "<div>4+ years of Go</div>"})

    s = get_scraper("zoho", "acme.zohorecruit.com")
    monkeypatch.setattr(type(s), "_get", _get)
    raw = s.fetch_raw()
    assert fetched == [
        "https://acme.zohorecruit.com/jobs/Careers/1",
        "https://acme.zohorecruit.com/jobs/Careers/2",
    ]  # not "3" — locked
    assert raw["details"] == {
        "1": {"id": "1", "Job_Description": "<div>4+ years of Go</div>"},
        "2": {"id": "1", "Job_Description": "<div>4+ years of Go</div>"},
    }
    jobs = s.parse(raw, SCRAPED_AT)
    # The detail record wins over the listing's own Job_Description for both jobs — it is a
    # measured strict superset (experiment/location-audit-2026-08-25/zoho.md).
    assert jobs[0].description == "4+ years of Go"
    assert jobs[1].description == "4+ years of Go"


# --- detail record: build the Job from it, falling back to the listing -------------------------
# The detail page is already fetched for every published job (`fetch_raw`), and was measured a
# strict superset of the listing across 205 paired tenants: Date_Opened, Work_Experience, State,
# Industry and Salary/Currency all show up there at meaningfully higher coverage (audit:
# experiment/location-audit-2026-08-25/zoho.md).


def test_zoho_detail_record_enriches_posted_at_experience_department_state_and_salary(
    monkeypatch,
):
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "0")
    records = [
        {
            "id": "1",
            "Posting_Title": "Backend Engineer",
            "City": "Reyrieux",
            "Country": "France",
        }
    ]
    page = _page(records)
    detail = _detail_page(
        {
            "id": "1",
            "City": "Reyrieux",
            "State": "Auvergne-Rhone-Alpes",
            "Country": "France",
            "Date_Opened": "2025-09-25",
            "Work_Experience": "+3 ans",
            "Industry": "Industrie",
            "Salary": "30-32",
            "Currency": "EUR",
        }
    )

    def _get(self, url=None):
        return page if url is None else detail

    s = get_scraper("zoho", "acme.zohorecruit.com")
    monkeypatch.setattr(type(s), "_get", _get)
    jobs = s.parse(s.fetch_raw(), SCRAPED_AT)
    j = jobs[0]
    assert j.posted_at == "2025-09-25"  # listing had none
    assert j.experience == "+3 ans"  # listing had none
    assert j.department == "Industrie"  # listing had none
    assert (
        j.location == "Reyrieux, Auvergne-Rhone-Alpes, France"
    )  # State only on detail
    assert j.salary == "30-32 EUR"  # never read from the listing at all


def test_zoho_falls_back_to_listing_when_detail_fetch_missing():
    records = [
        {
            "id": "1",
            "Posting_Title": "Backend Engineer",
            "City": "Chennai",
            "Date_Opened": "2026-01-01",
            "Work_Experience": "1-3 years",
            "Industry": "Technology",
        }
    ]
    raw = {"page": _page(records), "details": {}}
    jobs = get_scraper("zoho", "acme.zohorecruit.com").parse(raw, SCRAPED_AT)
    j = jobs[0]
    assert j.posted_at == "2026-01-01"
    assert j.experience == "1-3 years"
    assert j.department == "Technology"
    assert j.salary is None  # never in the listing to begin with
