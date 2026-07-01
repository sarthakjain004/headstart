import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from headstart.scrapers.registry import get_scraper

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCRAPED_AT = "2026-01-01T00:00:00+00:00"


def _load(name):
    with open(FIXTURES / name, encoding="utf-8") as f:
        return json.load(f)


def test_greenhouse_parse():
    jobs = get_scraper("greenhouse", "stripe", "Stripe").parse(
        _load("greenhouse_stripe.json"), SCRAPED_AT
    )
    assert len(jobs) == 3
    j = jobs[0]
    assert j.id == "greenhouse:stripe:7954688"
    assert j.ats == "greenhouse"
    assert j.company == "Stripe"
    assert j.title == "Account Executive, AI Sales (Grower)"
    assert j.location == "San Francisco, CA"
    assert j.remote is False
    assert j.url.startswith("https://")
    assert j.scraped_at == SCRAPED_AT
    # ?content=true also yields department + description in the same request
    assert j.department == "1650 AI GTM Strategy & Solutions"
    assert j.description and "</" not in j.description  # populated, HTML-stripped


def test_lever_parse():
    jobs = get_scraper("lever", "palantir", "Palantir").parse(
        _load("lever_palantir.json"), SCRAPED_AT
    )
    assert len(jobs) == 2
    j = jobs[0]
    assert j.id == "lever:palantir:0bbfd4f4-41ff-4ec6-b73f-5200efd5d4d3"
    assert j.company == "Palantir"
    assert j.title == "Administrative Business Partner - Security"
    assert j.location == "Palo Alto, CA"
    assert j.remote is False
    assert j.department == "Administrative"
    assert j.url.startswith("https://jobs.lever.co/palantir/")
    assert j.employment_type == "Full-time"  # categories.commitment
    assert j.description and "</" not in j.description  # populated, HTML-stripped


def test_ashby_parse_skips_unlisted():
    raw = _load("ashby_ramp.json")
    jobs = get_scraper("ashby", "ramp", "Ramp").parse(raw, SCRAPED_AT)
    expected = sum(1 for j in raw["jobs"] if j.get("isListed", True))
    assert len(jobs) == expected
    j = jobs[0]
    assert j.id == "ashby:ramp:34413f8d-26bf-4bbc-8ade-eb309a0e2245"
    assert j.title == "Security Engineer, Cloud"  # leading space stripped
    assert j.department == "Engineering"
    assert j.remote is True
    assert j.employment_type == "FullTime"
    assert j.description and "</" not in j.description  # populated, HTML-stripped


def test_darwinbox_parse():
    jobs = get_scraper("darwinbox", "licious", "Licious").parse(
        _load("darwinbox_licious.json"), SCRAPED_AT
    )
    assert len(jobs) == 2
    j = jobs[0]
    assert j.id == "darwinbox:licious:5ebea18409d3e"
    assert j.ats == "darwinbox"
    assert j.company == "Licious"
    assert j.title == "Dispatch Supervisor"
    # multi-location job: real cities recovered from tool_tip_locations, not "Multiple Locations"
    assert j.location == "Bengaluru, Gurgaon, Mumbai"
    assert j.remote is False
    assert j.department == "Dispatch"
    assert j.posted_at == "3-Feb-2025"
    assert (
        j.url == "https://licious.darwinbox.in/ms/candidate/careers/jobs/5ebea18409d3e"
    )
    assert j.scraped_at == SCRAPED_AT
    assert j.experience == "2 - 4 Years"
    assert j.employment_type == "Onroll"
    assert j.description and "</" not in j.description  # populated, HTML-stripped


def test_keka_parse():
    jobs = get_scraper("keka", "jupiter", "Jupiter").parse(
        _load("keka_jupiter.json"), SCRAPED_AT
    )
    assert len(jobs) == 2
    j = jobs[0]
    assert j.id == "keka:jupiter:132016"
    assert j.title == "Product Manager"
    assert j.location == "Bengaluru, KA, India"  # city, state, country joined
    assert j.department == "Product"
    assert j.url == "https://jupiter.keka.com/careers/jobdetails/132016"
    assert j.experience == "3-5"
    assert j.description and "</" not in j.description  # populated, HTML-stripped


def test_recruitee_parse():
    jobs = get_scraper("recruitee", "weekday", "Weekday").parse(
        _load("recruitee_weekday.json"), SCRAPED_AT
    )
    j = jobs[0]
    assert j.id == "recruitee:weekday:2141029"
    assert j.title == "Key Account Manager"
    assert j.remote is True  # location "Remote job"
    assert j.department == "Sales"
    assert j.url == "https://weekday.recruitee.com/o/key-account-manager"
    assert j.experience == "mid_level"
    assert j.employment_type == "fulltime_permanent"
    assert j.description and "</" not in j.description  # populated, HTML-stripped


def test_workable_parse():
    jobs = get_scraper("workable", "apna", "Apna").parse(
        _load("workable_apna.json"), SCRAPED_AT
    )
    assert len(jobs) == 2
    j = jobs[0]
    assert j.id == "workable:apna:41CF6A5AAA"
    assert j.title == "Account Manager- Enterprise Business"
    assert j.location == "Bengaluru, Karnataka, India"
    assert j.department == "Sales & Account Management"
    assert j.url == "https://apply.workable.com/j/41CF6A5AAA/apply"
    assert j.experience == "Mid-Senior level"
    assert j.employment_type == "Full-time"
    assert j.description and "</" not in j.description  # populated, HTML-stripped


def test_smartrecruiters_parse():
    jobs = get_scraper("smartrecruiters", "freshworks", "Freshworks").parse(
        _load("smartrecruiters_freshworks.json"), SCRAPED_AT
    )
    assert len(jobs) == 2
    j = jobs[0]
    assert j.id == "smartrecruiters:freshworks:744000133057378"
    assert j.title == "Specialist - Marketing Operations (North America)"
    assert "Chennai" in j.location and "India" in j.location
    assert j.url == "https://jobs.smartrecruiters.com/freshworks/744000133057378"
    assert j.experience == "Associate"  # experienceLevel.label
    assert j.employment_type == "Full-time"  # typeOfEmployment.label
    assert (
        j.description and "</" not in j.description
    )  # detail fetch; populated, HTML-stripped


def test_sensehq_parse():
    jobs = get_scraper("sensehq", "zetwerk", "Zetwerk").parse(
        _load("sensehq_zetwerk.json"), SCRAPED_AT
    )
    assert len(jobs) == 2
    j = jobs[0]
    assert j.id == "sensehq:zetwerk:56117"
    assert j.title == "CA Industrial Trainee"
    assert j.location == "Bangalore"
    assert j.department == "Aerospace & Defence"
    assert j.posted_at == "2026-06-13T03:19:29.434000+00:00"  # epoch ms -> ISO
    assert j.url == "https://zetwerk.sensehq.com/careers/jobs/56117"
    assert j.experience == "0-1"  # experience_start-experience_end
    assert j.employment_type == "INTERN"
    assert j.description and "</" not in j.description  # populated, HTML-stripped


def test_ripplehire_parse():
    jobs = get_scraper("ripplehire", "7-eleven-gsc", "7-Eleven GSC").parse(
        _load("ripplehire_7-eleven-gsc.json"), SCRAPED_AT
    )
    assert len(jobs) == 2
    j = jobs[0]
    assert j.id == "ripplehire:7-eleven-gsc:10454"
    assert j.title == "Analyst - RO"
    assert j.location == "Bengaluru"
    assert j.url == "https://7-eleven-gsc.ripplehire.com/candidate/careers"
    assert j.experience == "3 - 5 Years"  # jobReqExp
    # this tenant leaves jobDesc/jobType empty — fields stay None, job still emitted
    assert j.description is None
    assert j.employment_type is None


def test_oracle_parse():
    slug = "fa-etqo-saasfaprod1.fa.ocs.oraclecloud.com/CX_2"
    jobs = get_scraper("oracle", slug, "Oracle CE Tenant").parse(
        _load("oracle_fa-etqo_cx2.json"), SCRAPED_AT
    )
    assert len(jobs) == 2
    j = jobs[0]
    assert j.id == "oracle:fa-etqo-saasfaprod1.fa.ocs.oraclecloud.com:NAG_002"
    assert j.company == "Oracle CE Tenant"  # LegalEmployer empty -> fallback to company
    assert j.title == "Executive - Non Voice - Nagpur"
    assert j.posted_at == "2026-03-16"
    assert j.url == (
        "https://fa-etqo-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/"
        "CandidateExperience/en/sites/CX_2/job/NAG_002"
    )
    assert (
        j.description and "</" not in j.description
    )  # short ShortDescriptionStr, HTML-stripped


def test_workday_parse():
    slug = "https://3m.wd1.myworkdayjobs.com/search"
    jobs = get_scraper("workday", slug, "3M").parse(
        _load("workday_3m.json"), SCRAPED_AT
    )
    assert len(jobs) == 2
    j = jobs[0]
    assert j.id == "workday:3m/search:R01165862"  # site-scoped id, bulletFields req id
    assert j.company == "3M"
    assert j.title == "Procurement Service Center Operational Manager"
    assert j.location == "IN, BANGALORE"
    assert j.url == (
        "https://3m.wd1.myworkdayjobs.com/search/job/IN-BANGALORE/"
        "Procurement-Operations-Manager---India_R01165862-1"
    )
    # description now comes from a per-job detail fetch (injected into the fixture as _jobDescription)
    assert j.description and "</" not in j.description  # populated, HTML-stripped
    assert j.experience is None  # list/detail give no clean experience field
    assert j.employment_type is None


def test_trakstar_parse():
    # raw is {html: listing, descriptions: {code: detail-page JSON-LD description}}
    jobs = get_scraper("trakstar", "exotel", "Exotel").parse(
        _load("trakstar_exotel.json"), SCRAPED_AT
    )
    assert len(jobs) == 2
    j = jobs[0]
    assert j.id == "trakstar:exotel:fk0zvv1"
    assert j.title == "Application Security Engineer - L4"
    assert j.location == "Bengaluru/Gurugram"
    assert j.department == "Security"  # now read from the card's rb-text-4 div
    assert j.employment_type == "Full-time"  # from the opening-meta span
    assert j.url == "https://exotel.hire.trakstar.com/jobs/fk0zvv1/"
    assert j.description and "</" not in j.description  # from the detail page JSON-LD


def test_recruitee_salary_formatting():
    from headstart.scrapers.recruitee import _salary

    assert _salary(None) is None
    assert _salary({"min": None, "max": None}) is None  # blank -> None, job still kept
    assert _salary(
        {"min": 50000, "max": 70000, "currency": "EUR", "period": "year"}
    ) == ("50000-70000 EUR year")
    assert _salary({"min": 80000, "currency": "USD"}) == "80000 USD"  # one-sided range


def test_teamtailor_parse():
    jobs = get_scraper("teamtailor", "1komma5", "1KOMMA5").parse(
        _load("teamtailor_1komma5.json"), SCRAPED_AT
    )
    assert len(jobs) == 2
    j = jobs[0]
    assert j.id == "teamtailor:1komma5:d47f17ae-f550-4511-b046-594892a59734"
    assert j.ats == "teamtailor"
    assert "1KOMMA5" in j.company
    assert j.title  # non-empty
    assert j.location.startswith("Stockholm") and j.location.endswith("SE")
    assert j.url.startswith("https://1komma5.teamtailor.com/jobs/")
    assert j.posted_at.startswith("2026-")
    assert j.description and "</" not in j.description  # populated, HTML-stripped


def test_personio_parse():
    raw = ET.fromstring((FIXTURES / "personio_avian.xml").read_bytes())
    jobs = get_scraper("personio", "avian.jobs.personio.com", "Avian").parse(
        raw, SCRAPED_AT
    )
    assert len(jobs) == 2
    j = jobs[0]
    assert j.id == "personio:avian:2642824"
    assert j.ats == "personio"
    assert j.title == "Business Development Summer Intern"
    assert j.location  # office present
    assert j.department == "Operations"
    assert j.url == "https://avian.jobs.personio.com/job/2642824"
    assert j.posted_at  # createdAt
    assert j.description and "</" not in j.description  # populated, HTML-stripped


def test_join_parse():
    jobs = get_scraper("join", "indie-solutions", "indie").parse(
        _load("join_indie-solutions.json"), SCRAPED_AT
    )
    assert len(jobs) == 12
    j = jobs[0]
    assert j.id == "join:indie-solutions:16244456"
    assert j.ats == "join"
    assert j.title  # non-empty
    assert j.location  # present
    assert j.department == "Electrical Engineering"
    assert j.employment_type == "Employee"
    assert j.url.startswith("https://join.com/companies/indie-solutions/")
    assert j.posted_at
    assert j.description and "</" not in j.description  # populated, HTML-stripped
    assert (
        sum(1 for x in jobs if x.description) == 12
    )  # the bounded detail-fetch filled all 12


def test_rippling_parse():
    jobs = get_scraper("rippling", "acrn", "Acrn").parse(
        _load("rippling_acrn.json"), SCRAPED_AT
    )
    assert len(jobs) == 2
    j = jobs[0]
    assert j.id == "rippling:acrn:26708222-0b57-42df-8f52-b6b927351d18"
    assert j.ats == "rippling"
    assert j.title == "Clinical Operations Manager"
    assert j.location  # workLocation present
    assert (
        j.url
        == "https://ats.rippling.com/acrn/jobs/26708222-0b57-42df-8f52-b6b927351d18"
    )
    assert j.employment_type  # employmentType.id
    assert j.posted_at  # createdOn from the detail fetch
    assert j.description and "</" not in j.description  # populated, HTML-stripped


def test_unknown_ats_raises():
    with pytest.raises(ValueError):
        get_scraper("nonexistent", "foo")
