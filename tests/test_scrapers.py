import json
import logging
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
    # the lists sections (Requirements etc.) and additional must ride along —
    # descriptionPlain alone is just the intro
    assert "Core Responsibilities" in j.description
    assert "Salary" in j.description  # from `additional`
    assert j.salary == "80000-110000 USD per-year-salary"


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
    assert j.salary == "$150K – $200K • Offers Equity"  # compensationTierSummary
    # the board URL must request compensation or the block is absent
    assert "includeCompensation=true" in get_scraper("ashby", "ramp", "Ramp").url()


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
    assert (
        j.posted_at == "2025-02-03"
    )  # '3-Feb-2025' normalized to ISO for recency filters
    # v2-portal jobDetails route (browser-verified); parse defaults to new_careers=True
    assert j.url == (
        "https://licious.darwinbox.in/ms/candidatev2/main/careers/jobDetails/5ebea18409d3e"
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


def _keka_stub_get(portal, page="", jobs="[]"):
    """Stub BaseScraper._get, dispatching on the requested URL (careerportalinfo / careers page /
    embedjobs) so KekaScraper.fetch_raw can be exercised without network."""

    def _get(self, url=None):
        target = url or self.url()
        if "careerportalinfo" in target:
            return portal
        if "embedjobs" in target:
            return jobs
        if target.endswith("/careers"):
            return page
        raise AssertionError(f"unexpected GET {target}")

    return _get


def test_keka_uuid_from_portal_background(monkeypatch):
    # the common case: the UUID rides in careersBackgroundPath
    portal = '{"careersBackgroundPath":"/ats/documents/7e2f830e-7500-440f-992f-5013e438f8b4/bg.png"}'
    s = get_scraper("keka", "acme", "Acme")
    monkeypatch.setattr(
        type(s), "_get", _keka_stub_get(portal, jobs='[{"id":1,"title":"Eng"}]')
    )
    raw = s.fetch_raw()
    assert [j["id"] for j in raw] == [1]
    assert s._tenant == "7e2f830e-7500-440f-992f-5013e438f8b4"


def test_keka_uuid_falls_back_to_careers_page(monkeypatch):
    # background-less portal: no UUID in careerportalinfo, but the /careers page carries it
    portal = '{"careersBackgroundPath":"","name":"Aggne"}'
    page = "<html>...96d9c896-b9c8-40c0-bdf3-1b764db423a4...</html>"
    s = get_scraper("keka", "aggne", "Aggne")
    monkeypatch.setattr(
        type(s),
        "_get",
        _keka_stub_get(portal, page=page, jobs='[{"id":2,"title":"Dev"}]'),
    )
    raw = s.fetch_raw()
    assert [j["id"] for j in raw] == [2]
    assert s._tenant == "96d9c896-b9c8-40c0-bdf3-1b764db423a4"


def test_keka_invalid_tenant_yields_no_jobs(monkeypatch):
    # soft-404: an unknown slug renders "Invalid Tenant" HTML at HTTP 200
    s = get_scraper("keka", "nope", "Nope")
    monkeypatch.setattr(
        type(s), "_get", _keka_stub_get("<html><title>Invalid Tenant</title></html>")
    )
    assert s.fetch_raw() == []


def test_keka_no_uuid_anywhere_yields_no_jobs(monkeypatch):
    # background-less portal whose /careers page also omits the UUID (JS-loaded) -> unreadable
    s = get_scraper("keka", "anblicks", "Anblicks")
    monkeypatch.setattr(
        type(s),
        "_get",
        _keka_stub_get('{"careersBackgroundPath":""}', page="<html>no id</html>"),
    )
    assert s.fetch_raw() == []


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
    assert (
        "Requirements" in j.description
    )  # the separate requirements field rides along


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


def test_smartrecruiters_description_joins_requirement_sections():
    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {
                "jobAd": {
                    "sections": {
                        "companyDescription": {"text": "<p>About us boilerplate</p>"},
                        "jobDescription": {"text": "<p>Build things</p>"},
                        "qualifications": {"text": "<p>5+ years of experience</p>"},
                        "additionalInformation": {"text": "<p>Perks</p>"},
                    }
                }
            }

    scraper = get_scraper("smartrecruiters", "acme", "Acme")
    text = scraper._extract_description(_Resp())
    assert "Build things" in text
    assert "5+ years of experience" in text  # qualifications must ride along
    assert "Perks" in text
    assert "boilerplate" not in text  # companyDescription deliberately skipped


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


def test_ripplehire_fetch_raw_fills_jobdesc_from_detail(monkeypatch):
    # the search list always carries jobDesc: null — the detail JSON must fill it
    class _Resp:
        def __init__(self, url="", payload=None):
            self.url = url
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

    calls = []

    def _fetch(method, url, **kwargs):
        calls.append(url)
        if url.endswith("/candidate/careers"):
            return _Resp(url="https://x.ripplehire.com/candidate/?token=TOK123")
        if "candidatejobsearch" in url:
            return _Resp(
                payload={
                    "jobVoList": [
                        {"jobSeq": 1, "jobTitle": "SRE", "jobDesc": None},
                        {"jobSeq": 2, "jobTitle": "Filled", "jobDesc": "<p>have</p>"},
                    ],
                    "totalJobCount": 2,
                }
            )
        assert "candidatejobdetail" in url and "token=TOK123" in url
        return _Resp(payload={"jobVO": {"jobDesc": "<p>3+ years of Kubernetes</p>"}})

    import headstart.scrapers.ripplehire as rh

    monkeypatch.setattr(rh.http, "fetch", _fetch)
    raw = get_scraper("ripplehire", "x", "X").fetch_raw()
    assert [j["jobDesc"] for j in raw] == [
        "<p>3+ years of Kubernetes</p>",
        "<p>have</p>",
    ]
    # only the description-less job triggered a detail call
    assert sum("candidatejobdetail" in u for u in calls) == 1


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
    # description/date/type come from the per-job detail fetch (fixture's _detail block)
    assert j.description and "</" not in j.description  # populated, HTML-stripped
    assert j.experience is None  # list/detail give no clean experience field
    assert (
        j.posted_at == "2026-01-10"
    )  # detail startDate, not the list's "30+ Days Ago"
    assert j.employment_type == "Full time"  # timeType


def test_workday_remote_falls_back_to_location():
    # remoteType is absent on ~99% of Workday listings (remote-audit LOG); the location
    # string then decides. A decisive remoteType still wins over the location string.
    raw = [
        {"title": "A", "locationsText": "Remote - Colombia", "bulletFields": ["R1"]},
        {"title": "B", "locationsText": "Austin, TX", "bulletFields": ["R2"]},
        {
            "title": "C",
            "locationsText": "Remote-MO",
            "remoteType": "On-site",
            "bulletFields": ["R3"],
        },
    ]
    jobs = get_scraper(
        "workday", "https://acme.wd1.myworkdayjobs.com/careers", "Acme"
    ).parse(raw, SCRAPED_AT)
    assert [j.remote for j in jobs] == [True, False, False]


def test_freshteam_parse():
    jobs = get_scraper("freshteam", "12min", "12min").parse(
        _load("freshteam_12min.json"), SCRAPED_AT
    )
    assert len(jobs) == 3  # the deleted=true job is dropped

    marketing, backend, sre = jobs
    assert marketing.id == "freshteam:12min:1000070208"  # numeric id, not unique_id
    assert marketing.company == "12min"
    assert (
        marketing.title == "Email Marketing & Lifecycle Automation Specialist (Remote)"
    )
    assert marketing.location == "Belo Horizonte, Brazil"  # branch_id join
    assert marketing.remote is True  # native remote flag
    assert marketing.department == "Marketing"  # job_role_id join
    assert marketing.url.startswith("https://12min.freshteam.com/jobs/")
    assert marketing.posted_at == "2025-02-06T19:22:55.000Z"
    assert marketing.description and "</" not in marketing.description  # HTML stripped
    assert marketing.employment_type is None  # job_type enum left unmapped

    # native remote=false, physical branch -> not remote
    assert backend.location == "Bengaluru, India" and backend.remote is False
    # native remote=false but the branch location literally says "Remote" -> both-family recovers it
    assert sre.location == "Remote - India" and sre.remote is True


def test_freshteam_dead_tenant_is_empty():
    # an unknown slug soft-errors at HTTP 200 with an HTML 404 page (not JSON)
    assert get_scraper("freshteam", "nope").parse({}, SCRAPED_AT) == []


class _FakeResp:
    def __init__(self, status):
        self.status_code = status

    def json(self):
        return {"total": 1, "jobPostings": []}


def _workday_fetch_stub(live_instance):
    """Stub headstart.http.fetch: 200 only for the CXS URL on `live_instance`, else 422."""

    def fetch(method, url, **kwargs):
        return _FakeResp(200 if f".{live_instance}." in url else 422)

    return fetch


def test_workday_keeps_instance_when_hinted_serves(monkeypatch):
    monkeypatch.setattr("headstart.http.fetch", _workday_fetch_stub("wd3"))
    s = get_scraper("workday", "https://acme.wd3.myworkdayjobs.com/careers", "Acme")
    s._resolve_instance()
    assert s._instance is None  # hinted instance served it -> no sweep, URL unchanged
    assert ".wd3." in s.url()


def test_workday_follows_migrated_instance(monkeypatch):
    # tenant migrated wd3 -> wd103; hinted 422s, sweep finds wd103
    monkeypatch.setattr("headstart.http.fetch", _workday_fetch_stub("wd103"))
    s = get_scraper("workday", "https://acme.wd3.myworkdayjobs.com/careers", "Acme")
    s._resolve_instance()
    assert s._instance == "wd103"
    assert ".wd103." in s.url() and "/wday/cxs/acme/careers/jobs" in s.url()


def test_workday_leaves_instance_when_none_serves(monkeypatch):
    # gone everywhere (422 on all DCs) -> keep hinted; crawl yields nothing
    monkeypatch.setattr("headstart.http.fetch", _workday_fetch_stub("nowhere"))
    s = get_scraper("workday", "https://gone.wd3.myworkdayjobs.com/careers", "Gone")
    s._resolve_instance()
    assert s._instance is None
    assert ".wd3." in s.url()


def test_workday_paginate_warns_once_on_missing_pages(monkeypatch, caplog):
    # a mid-crawl 404 (None from _post) skips that page but keeps the rest, and one
    # WARNING reports the gap — the tripwire for a partial board
    from headstart.scrapers.workday import WorkdayScraper

    s = WorkdayScraper("https://acme.wd1.myworkdayjobs.com/ext")
    pages = {
        20: {"jobPostings": [{"bulletFields": ["R20"]}]},
        40: None,  # this page 404ed mid-crawl
        60: {"jobPostings": [{"bulletFields": ["R60"]}]},
        80: {"jobPostings": [{"bulletFields": ["R80"]}]},
    }
    monkeypatch.setattr(s, "_post", lambda applied, offset: pages[offset])
    absorbed = []
    caplog.set_level(logging.WARNING, logger="headstart.scrapers.workday")
    s._paginate({}, 100, absorbed.extend)
    # the surviving pages are absorbed, in offset order
    assert [p["bulletFields"][0] for p in absorbed] == ["R20", "R60", "R80"]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert warnings[0].name == "headstart.scrapers.workday"
    assert "1 page(s) 404ed" in warnings[0].getMessage()
    assert "workday:acme/ext" in warnings[0].getMessage()  # the board key


def test_trakstar_parse():
    # raw is {html: listing, postings: {code: detail-page JSON-LD JobPosting}}
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
    assert j.posted_at == "2026-02-01"  # JSON-LD datePosted; the listing card has none


def test_recruitee_url_ignores_the_customers_vanity_domain():
    """The API's `careers_url` is whatever domain the customer configured, and a third of
    those do not serve the board (transperfect.com/o/… 404s while the job is open). Build the
    link on the tenant's own host instead, which always resolves."""
    from headstart.scrapers.recruitee import _offer_url

    offer = {
        "slug": "software-engineer-net-c-1",
        "careers_url": "https://transperfect.com/o/software-engineer-net-c-1",
        "careers_apply_url": "https://transperfect.com/o/software-engineer-net-c-1/c/new",
    }
    assert (
        _offer_url("transperfect", offer)
        == "https://transperfect.recruitee.com/o/software-engineer-net-c-1"
    )
    # nothing to build from -> the API's own links, rather than a fabricated URL
    assert _offer_url("transperfect", {"careers_url": "https://x.test/o/a"}) == (
        "https://x.test/o/a"
    )
    assert _offer_url("transperfect", {}) == ""


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


def test_darwinbox_legacy_portal_url():
    # a tenant with companyinfo.new_careers=false keeps the old app's careers/:id route
    s = get_scraper("darwinbox", "licious", "Licious")
    s._new_careers = False
    jobs = s.parse(_load("darwinbox_licious.json"), SCRAPED_AT)
    assert jobs[0].url == (
        "https://licious.darwinbox.in/ms/candidate/careers/5ebea18409d3e"
    )


def test_darwinbox_iso_date():
    from headstart.scrapers.darwinbox import _iso_date

    assert _iso_date("3-Feb-2025") == "2025-02-03"
    assert _iso_date("21-Apr-2026") == "2026-04-21"
    assert _iso_date(None) is None
    assert _iso_date("sometime soon") == "sometime soon"  # unparseable passes through
    # some tenants (orangehealth) send an epoch int, not the string — must not crash the board
    assert _iso_date(1706918400000) == "2024-02-03"  # epoch ms
    assert _iso_date(1706918400) == "2024-02-03"  # epoch seconds
    assert _iso_date(0) is None  # falsy -> unknown, not 1970


def test_successfactors_parse():
    jobs = get_scraper("successfactors", "jobs.sap.com", "SAP").parse(
        _load("successfactors_pages.json"), SCRAPED_AT
    )
    assert len(jobs) == 2  # the fields=None item (failed detail fetch) is dropped
    ml = jobs[0]
    assert ml.id == "successfactors:jobs.sap.com:1392118733"
    assert ml.ats == "successfactors"
    assert ml.company == "SAP"
    assert ml.title == "Machine Learning Engineer Expert"
    assert ml.location == "Bangalore, KA, IN"
    assert ml.remote is False  # no TELECOMMUTE, location not remote
    assert ml.url.startswith("https://jobs.sap.com/job/")
    assert ml.posted_at == "2026-07-01"
    assert ml.employment_type == "FULL_TIME"
    assert ml.description == "Build and ship ML systems for SAP Labs India."
    assert jobs[1].remote is True  # JSON-LD TELECOMMUTE wins over the null location


def test_successfactors_company_derived_from_host():
    # the ledger only knows the vanity host; the display name derives from it
    assert get_scraper("successfactors", "jobs.sap.com").company == "sap"
    assert get_scraper("successfactors", "jobsearch.alstom.com").company == "alstom"
    assert get_scraper("successfactors", "jobdetails.nestle.com").company == "nestle"
    assert get_scraper("successfactors", "careers.payu.in").company == "payu"
    # an explicit name always wins
    assert get_scraper("successfactors", "jobs.sap.com", "SAP").company == "SAP"


def test_successfactors_job_urls_from():
    from headstart.scrapers.successfactors import _job_urls_from

    text = """
    <loc>https://jobs.birlasoft.com/job/Pune-Data-Architect-%28Snowflake-&amp;-Databricks%29-INDI/57210344/</loc>
    <a class="jobTitle-link" href="/job/Pune-OTM-Consultant-INDI/57254244/">OTM Consultant</a>
    <a href="/job/Pune-OTM-Consultant-INDI/57254244/">dupe of the same posting</a>
    """
    pairs = _job_urls_from(text, "jobs.birlasoft.com")
    assert pairs == [
        (
            "https://jobs.birlasoft.com/job/Pune-Data-Architect-%28Snowflake-&-Databricks%29-INDI/57210344/",
            "57210344",
        ),
        (
            "https://jobs.birlasoft.com/job/Pune-OTM-Consultant-INDI/57254244/",
            "57254244",
        ),
    ]


def test_successfactors_page_fields_jsonld():
    from headstart.scrapers.successfactors import _page_fields

    page = """<html><head><script type="application/ld+json">
    {"@context": "http://schema.org", "@type": "JobPosting",
     "title": "Senior Software Engineer",
     "datePosted": "2026-07-10",
     "employmentType": "FULL_TIME",
     "description": "<p>Ship backend services.</p>",
     "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress",
       "addressLocality": "Pune", "addressRegion": "MH", "addressCountry": "IN"}}}
    </script></head><body></body></html>"""
    fields = _page_fields(page)
    assert fields["title"] == "Senior Software Engineer"
    assert fields["location"] == "Pune, MH, IN"
    assert fields["posted_at"] == "2026-07-10"
    assert fields["employment_type"] == "FULL_TIME"
    assert fields["description"] == "<p>Ship backend services.</p>"


def test_successfactors_page_fields_csb():
    from headstart.scrapers.successfactors import _page_fields

    # the CSB-rendered shape (Wipro/Voith): no JSON-LD; microdata + joblayouttoken labels
    page = """<html><head><title>Lead Data Scientist Job Details | Wipro Limited</title>
    <meta property="og:title" content="Lead Data Scientist" /></head><body>
    <span class="joblayouttoken-label" role="heading">City: </span>
    <span xml:lang="en-US" class="rtltextaligneligible">Mississauga </span>
    <span class="joblayouttoken-label" role="heading">State/Province: </span>
    <span xml:lang="en-US" class="rtltextaligneligible">Ontario </span>
    <span class="joblayouttoken-label" role="heading">Posting Start Date: </span>
    <span xml:lang="en-US" class="rtltextaligneligible">6/29/26 </span>
    <span xml:lang="en-US" lang="en-US" itemprop="title" class="rtltextaligneligible">Lead Data Scientist </span>
    <span itemprop="description" class="rtltextaligneligible">short teaser</span>
    <span itemprop="description" class="rtltextaligneligible"><div><p><strong>Role:</strong>
    ML with <span>Python</span> and SQL.</p></div></span>
    </body></html>"""
    fields = _page_fields(page)
    assert fields["title"] == "Lead Data Scientist"
    assert fields["location"] == "Mississauga, Ontario"
    assert fields["posted_at"] == "2026-06-29"
    # the longest itemprop=description block wins (teaser vs full description), and the
    # tag-matching walk keeps the nested span inside it
    assert "Python" in fields["description"] and "teaser" not in fields["description"]


def test_successfactors_page_fields_csb_meta_microdata():
    from headstart.scrapers.successfactors import _page_fields

    # the LTIMindtree-style shape: no JSON-LD, no location/date labels — the JobPosting
    # schema lives in <meta itemprop> microdata (Java Date.toString for datePosted)
    page = """<html><head><title>Senior Software Engineer Job Details | LTM</title></head>
    <body><span itemprop="jobLocation" itemscope><span itemprop="address" itemscope>
    <meta itemprop="addressLocality" content="Brazil"><meta itemprop="addressRegion" content="SP">
    <meta itemprop="addressCountry" content="BR"></span></span>
    <meta itemprop="datePosted" content="Tue Jul 21 02:00:00 UTC 2026">
    <span itemprop="title">Senior Software Engineer</span>
    <span itemprop="description"><p>Build things.</p></span></body></html>"""
    fields = _page_fields(page)
    assert fields["title"] == "Senior Software Engineer"
    assert fields["location"] == "Brazil, SP, BR"
    assert fields["posted_at"] == "2026-07-21"
    # "25 Jun 2026"-style label dates parse too
    from headstart.scrapers.successfactors import _csb_posted_at

    label_page = (
        '<span class="joblayouttoken-label">Posting Date: </span>'
        "<span>25 Jun 2026 </span>"
    )
    assert _csb_posted_at(label_page) == "2026-06-25"


def test_successfactors_location_from_careersite_property():
    from headstart.scrapers.successfactors import _csb_location

    # the location value wrapped in a nested <p> (Novo Nordisk / SKF shape): the label-value
    # regex captures only whitespace, so the data-careersite-propertyid="location" text wins
    page = (
        '<span class="joblayouttoken-label">Location: </span>'
        '<span data-careersite-propertyid="location" class="rtltextaligneligible">'
        '<p id="job-location" class="jobLocation">Durham, NC, US</p></span>'
    )
    assert _csb_location(page) == "Durham, NC, US"


def test_eightfold_parse():
    # the normalized record shape both the PCSX-API and sitemap paths feed into parse()
    jobs = get_scraper("eightfold", "jobs.nvidia.com", "NVIDIA").parse(
        _load("eightfold_pages.json"), SCRAPED_AT
    )
    assert len(jobs) == 2  # the fields=None item (unreadable) is dropped
    j = jobs[0]
    assert j.id == "eightfold:jobs.nvidia.com:893395145771"
    assert j.ats == "eightfold"
    assert j.company == "NVIDIA"
    assert j.title == "Senior Memory Mask Design Engineer"
    assert j.location == "Bengaluru, Karnataka, India"
    assert j.remote is False
    assert j.department == "Silicon Engineering"  # the PCSX API supplies department
    assert j.url == "https://jobs.nvidia.com/careers/job/893395145771"
    assert j.posted_at == "2026-05-15"
    assert j.description == "Design memory masks for next-gen GPUs."
    assert (
        jobs[1].remote is True
    )  # remote flag survives a null location + missing description


def test_eightfold_api_field_helpers():
    from headstart.scrapers.eightfold import (
        _first_location,
        _remote_from,
        _ts_to_iso,
    )

    assert _ts_to_iso("1784592000") == "2026-07-21"  # unix seconds (string) -> ISO date
    assert _ts_to_iso(1784592000) == "2026-07-21"  # or int
    assert (
        _ts_to_iso(0) is None and _ts_to_iso(None) is None and _ts_to_iso("x") is None
    )
    assert _remote_from("onsite") is False
    assert _remote_from("Remote") is True
    assert _remote_from("hybrid") is None  # neither -> defer to the location signal
    assert _first_location(["Bangalore, India", "Pune, India"]) == "Bangalore, India"
    assert _first_location([]) is None and _first_location(None) is None


def test_eightfold_jobposting_fallback():
    # the sitemap-fallback path parses the job page's JSON-LD
    from headstart.scrapers.eightfold import _jobposting, _sitemap_position_id

    page = """<html><head><script type="application/ld+json">
    {"@context": "http://schema.org", "@type": "JobPosting",
     "title": "Senior ASIC Design Verification Engineer",
     "datePosted": "2026-06-02T00:00:00", "employmentType": "FULL_TIME",
     "description": "<p>Verify ASICs.</p>",
     "jobLocation": [{"@type": "Place", "address": {"@type": "PostalAddress",
       "addressLocality": "Hyderabad", "addressRegion": "Telangana,IN",
       "addressCountry": {"@type": "Country", "name": "IN"}}}]}
    </script></head></html>"""
    f = _jobposting(page)
    assert f["title"] == "Senior ASIC Design Verification Engineer"
    # region already carries the country ("Telangana,IN"); duplicate country part deduped
    assert f["location"] == "Hyderabad, Telangana,IN"
    assert f["posted_at"] == "2026-06-02T00:00:00"
    assert f["employment_type"] == "FULL_TIME"
    assert f["department"] is None  # not in the JSON-LD (only the API path has it)
    assert (
        _sitemap_position_id(
            "https://x/careers/job/41979677-senior-asic-verification-hyderabad?domain=micron.com"
        )
        == "41979677"
    )
    assert _jobposting("<html>no ld</html>") is None


def test_eightfold_sitemap_index_and_job_urls():
    from headstart.scrapers.eightfold import _CHILD_SITEMAP, _JOB_LOC, _dedupe

    index = """<sitemapindex><sitemap><loc>https://h/careers/sitemap1.xml</loc></sitemap>
    <sitemap><loc>https://h/careers/sitemap_cat.xml</loc></sitemap></sitemapindex>"""
    children = [c for c in _CHILD_SITEMAP.findall(index) if "index" not in c.lower()]
    assert children == [
        "https://h/careers/sitemap1.xml",
        "https://h/careers/sitemap_cat.xml",
    ]
    body = """<urlset>
    <url><loc>https://h/careers/job/1-a-pune-india?domain=x.com</loc></url>
    <url><loc>https://h/careers/job/2-b-remote?domain=x.com</loc></url>
    <url><loc>https://h/careers/job/1-a-pune-india?domain=x.com</loc></url></urlset>"""
    assert _dedupe(_JOB_LOC.findall(body)) == [
        "https://h/careers/job/1-a-pune-india?domain=x.com",
        "https://h/careers/job/2-b-remote?domain=x.com",
    ]


def test_eightfold_skips_details_it_already_holds(monkeypatch):
    """ADR-0048: a Job already covered gets no detail fetch, and the rest stay aligned.

    Alignment is the trap — the fan-out now covers a *subset* of the positions, so pairing its
    results back by index instead of by id would hang each description on the wrong Job.
    """
    from headstart.scrapers.registry import get_scraper

    scraper = get_scraper("eightfold", "acme.eightfold.ai", "Acme")
    scraper.have_details = {"eightfold:acme.eightfold.ai:1"}
    positions = [
        {"id": "1", "name": "Held Engineer"},
        {"id": "2", "name": "Fresh Engineer"},
    ]
    fetched: list[str] = []

    def fake_fan_out_async(items, fn, **kwargs):
        fetched.extend(items)
        return [f"desc-{i}" for i in items]

    monkeypatch.setattr(scraper, "fan_out_async", fake_fan_out_async)
    records = scraper._api_records("acme.com", positions)

    assert fetched == ["2"]  # the held Job was never fetched
    by_id = {r["id"]: r["fields"]["description"] for r in records}
    assert by_id["2"] == "desc-2"  # the fetched description landed on the right Job
    assert (
        by_id["1"] is None
    )  # and the held one carries no description, not someone else's


def test_every_detail_is_needed_without_a_skip_list():
    """The default for every caller outside the pipeline: no list means fetch everything, even
    for a Job whose composite key another run would have covered."""
    from headstart.scrapers.registry import get_scraper

    scraper = get_scraper("eightfold", "acme.eightfold.ai", "Acme")
    assert scraper.have_details is None
    assert scraper.needs_detail("1") is True


def test_needs_detail_uses_board_key_not_the_bare_slug():
    """personio and workday override board_key, so composing ``{ats}:{slug}:{id}`` at a call site
    would miss every entry on those Boards — silently, as "fetch everything" (ADR-0048)."""
    from headstart.scrapers.registry import get_scraper

    scraper = get_scraper(
        "workday", "https://acme.wd3.myworkdayjobs.com/External", "Acme"
    )
    covered = f"{scraper.board_key()}:R-1"
    assert covered != f"workday:{scraper.slug}:R-1"  # the override really does differ
    scraper.have_details = {covered}
    assert scraper.needs_detail("R-1") is False
    assert scraper.needs_detail("R-2") is True


def test_eightfold_reports_a_rate_limited_page_as_a_truncated_board():
    """The index flap's root cause, pinned at the scraper (ADR-0053).

    `_api_search` gives up mid-pagination on a non-200 and returns what it has. That is the right
    call — the postings it did fetch are real — but until this signal existed the Board looked
    fully scraped to `harvest`, so `index sync` read the unread postings as delistings and evicted
    them. Whole NVIDIA/Qualcomm Boards left search for a cycle at a time on exactly this path.
    """
    from headstart.scrapers.eightfold import EightfoldScraper

    class _Resp:
        def __init__(self, status, positions=(), count=0):
            self.status_code = status
            self._body = {"data": {"positions": list(positions), "count": count}}

        def json(self):
            return self._body

    pages = [
        _Resp(200, [{"id": n} for n in range(10)], count=30),  # page 1 of 3
        _Resp(429),  # rate-limited partway
    ]
    scraper = EightfoldScraper("nvidia.eightfold.ai")
    scraper._get = lambda *a, **k: pages.pop(0)

    got = scraper._api_search("nvidia")

    assert len(got) == 10, "the postings it did fetch must still be returned"
    assert scraper.truncated, (
        "a Board cut short must say so, or sync evicts the rest as closed"
    )
    assert "429" in scraper.truncated and "30" in scraper.truncated


def test_eightfold_leaves_truncated_unset_on_a_complete_crawl():
    """The other half: a Board that really did list everything must NOT be protected, or eviction
    stops working and closed postings are served forever."""
    from headstart.scrapers.eightfold import EightfoldScraper

    class _Resp:
        def __init__(self, positions, count):
            self.status_code = 200
            self._body = {"data": {"positions": list(positions), "count": count}}

        def json(self):
            return self._body

    scraper = EightfoldScraper("acme.eightfold.ai")
    scraper._get = lambda *a, **k: _Resp([{"id": 1}], count=1)

    scraper._api_search("acme")
    assert scraper.truncated is None


def test_mark_truncated_keeps_the_first_reason():
    """A crawl that gave up once tends to give up again, and the reasons that follow are
    consequences of the first — so the first one is the one worth reporting (ADR-0053)."""
    scraper = get_scraper("greenhouse", "acme")
    scraper.mark_truncated("HTTP 429 on page 2 — got 10 of 300 postings")
    scraper.mark_truncated("empty page 3 — got 10 of 300 postings")
    assert scraper.truncated == "HTTP 429 on page 2 — got 10 of 300 postings"


def _successfactors_board(monkeypatch, *, search, rss):
    """A SuccessFactors scraper whose three listing surfaces are stubbed: the sitemap classifies
    as RSS (so the fallback chain runs in full), ``search`` is what the ``/search/`` walk returns
    as ``(pairs, why-it-stopped)``, and ``rss`` is what the patient stream lists."""
    from headstart.scrapers.successfactors import SuccessFactorsScraper

    monkeypatch.setenv(
        "HEADSTART_ASYNC_FANOUT", "0"
    )  # keep the detail pass on the sync path
    scraper = SuccessFactorsScraper("careers.voith.com")
    monkeypatch.setattr(scraper, "_fetch_sitemap", lambda: ("rss", ""))
    monkeypatch.setattr(scraper, "_search_job_urls", lambda: search)
    monkeypatch.setattr(scraper, "_rss_job_urls", lambda: rss)
    monkeypatch.setattr(scraper, "_job_fields", lambda url: {"title": "Engineer"})
    return scraper


class _SearchPage:
    def __init__(self, status, text=""):
        self.status_code = status
        self.text = text


def test_successfactors_search_walk_reports_where_it_stopped_without_claiming_the_board(
    monkeypatch,
):
    """The walk hands its truncation back rather than recording it, because whether the Board's
    list is short depends on which surface ends up answering — and that is decided in
    ``fetch_raw``, not here (ADR-0053)."""
    from headstart.scrapers import successfactors as sf

    scraper = sf.SuccessFactorsScraper("jobs.example.com")
    pages = [_SearchPage(200, '<a href="/job/x/11/">a</a>'), _SearchPage(503)]
    monkeypatch.setattr(sf.http, "fetch", lambda *a, **k: pages.pop(0))

    found, why = scraper._search_job_urls()

    assert [job_id for _url, job_id in found] == ["11"]
    assert why and "503" in why and "startrow 25" in why
    assert scraper.truncated is None  # the caller decides, not the walk


def test_successfactors_keeps_a_whole_rss_board_off_the_truncated_list(monkeypatch):
    """The bug this pins: the ``/search/`` walk 503s on its *first* page, so it lists nothing and
    the RSS stream answers with the complete board. Carrying the walk's truncation onto that
    complete list would exempt a healthy Board from eviction permanently — its closed postings
    would then be served forever."""
    scraper = _successfactors_board(
        monkeypatch,
        search=([], "HTTP 503 at startrow 0 — 0 postings read before the walk stopped"),
        rss=[("https://careers.voith.com/job/x/1/", "1")],
    )

    raw = scraper.fetch_raw()

    assert [item["id"] for item in raw] == [
        "1"
    ]  # the RSS stream answered, and answered whole
    assert scraper.truncated is None


def test_successfactors_reports_a_short_search_walk_when_it_is_the_answer(monkeypatch):
    """The other direction: when the walk *does* list the Board, its truncation is the Board's."""
    scraper = _successfactors_board(
        monkeypatch,
        search=(
            [("https://careers.voith.com/job/x/1/", "1")],
            "HTTP 503 at startrow 25 — 1 postings read before the walk stopped",
        ),
        rss=[("https://careers.voith.com/job/x/1/", "1")],
    )

    scraper.fetch_raw()

    assert scraper.truncated == (
        "HTTP 503 at startrow 25 — 1 postings read before the walk stopped"
    )


def _workday_scraper():
    from headstart.scrapers.workday import WorkdayScraper

    return WorkdayScraper("https://acme.wd1.myworkdayjobs.com/ext")


def test_workday_reports_a_subdivided_slice_that_lost_its_first_page(monkeypatch):
    """A 404 on a subdivided facet's first page drops that whole slice, silently.

    The Board is not empty afterwards — its sibling slices still land, so it emits lines, stays
    in the eviction scope, and ``index sync`` reads the vanished slice as delistings (ADR-0053).
    """
    scraper = _workday_scraper()
    root = {
        "total": 2000,  # the reported cap: subdivide
        "jobPostings": [{"bulletFields": ["R1"]}],
        "facets": [
            {
                "facetParameter": "jobFamilyGroup",
                "values": [{"id": "Eng", "count": 1500}, {"id": "Ops", "count": 600}],
            }
        ],
    }

    def post(applied, offset):
        if not applied:
            return root
        if applied.get("jobFamilyGroup") == ["Eng"]:
            return None  # 404 mid-crawl — this slice vanishes whole
        return {"total": 1, "jobPostings": [{"bulletFields": ["R2"]}]}

    monkeypatch.setattr(scraper, "_post", post)
    absorbed: list[dict] = []
    scraper._exhaust({}, absorbed.extend, depth=0)

    assert [p["bulletFields"][0] for p in absorbed] == [
        "R1",
        "R2",
    ]  # siblings still land
    assert scraper.truncated and "jobFamilyGroup=Eng" in scraper.truncated


def test_workday_leaves_a_board_that_answered_nothing_unflagged(monkeypatch):
    """A 404 on the *unfiltered* first page yields no postings at all, so the Board writes no
    lines and eviction never reaches it. Flagging it would report a truncation nothing can act
    on — and every dead Workday board would carry one."""
    scraper = _workday_scraper()
    monkeypatch.setattr(scraper, "_post", lambda applied, offset: None)
    absorbed: list[dict] = []

    scraper._exhaust({}, absorbed.extend, depth=0)

    assert absorbed == []
    assert scraper.truncated is None


def test_workday_reports_a_capped_query_it_cannot_subdivide(monkeypatch):
    """``total`` stuck at exactly 2,000 means the real total is higher; with no facet left to
    split there is no second query to reach the rest, so the crawl paginates 2,000 of a
    knowingly larger board — eightfold's page ceiling in Workday form (ADR-0053)."""
    scraper = _workday_scraper()
    monkeypatch.setattr(
        scraper,
        "_post",
        lambda applied, offset: {"total": 2000, "jobPostings": [], "facets": []},
    )

    scraper._exhaust({}, lambda batch: None, depth=0)

    assert scraper.truncated and "2000" in scraper.truncated

    # ...while a board whose total is under the cap paginates to the end and says nothing.
    whole = _workday_scraper()
    monkeypatch.setattr(
        whole,
        "_post",
        lambda applied, offset: {"total": 40, "jobPostings": [], "facets": []},
    )
    whole._exhaust({}, lambda batch: None, depth=0)
    assert whole.truncated is None
