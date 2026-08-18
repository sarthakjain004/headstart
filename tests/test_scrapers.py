import json
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import ClassVar

import pytest

from headstart import http
from headstart.scrapers.personio import PersonioScraper
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

        def raise_for_status(self):
            pass  # always 200 in this fixture

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


def _darwinbox_curl_wall(monkeypatch):
    """Stub `http.fetch` so `.in` answers Cloudflare's 403 and `.com` the wrong-TLD 500.

    The fakes are real ``curl_cffi`` Responses and `_alljobs` raises via `raise_for_status`,
    so the library builds the exception. Stubbing `fetch` to *raise* instead would hand the
    scraper a `urllib` error, whose ``.code`` is the HTTP status — ``curl_cffi``'s is a curl
    errno, always 0 — and the tests would pass against a predicate that never fires in
    production (the no-op-fix bug the #137 review caught).
    """
    from curl_cffi.requests import models

    import headstart.scrapers.darwinbox as db

    def _response(status, reason):
        r = models.Response()
        r.status_code, r.ok, r.reason = status, False, reason
        return r

    def _fetch(method, url, **kwargs):
        if ".darwinbox.in" in url:
            return _response(403, "Forbidden")  # the wall, on the tenant's real host
        return _response(500, "Internal Server Error")  # wrong TLD: "Invalid subdomain"

    monkeypatch.setattr(db.http, "fetch", _fetch)


class _FakeDarwinboxPage:
    """browser_http._Page's surface, answering the darwinbox API from canned pages."""

    def __init__(self, pages):
        self.pages = pages
        self.posted = []

    def post_json(self, path, body):
        self.posted.append(body)
        return {"data": self.pages[body["page"] - 1]}

    def get_json(self, path):
        return {"message": {"company": {"new_careers": True}}}


def test_darwinbox_wall_routes_to_the_browser_on_the_walled_tld(monkeypatch):
    """A persistent 403 escalates to the browser transport on the tenant's real host.

    The wall admits a genuine Chrome and nothing else (ADR-0056), so a walled board must not
    surface an error — and must navigate the TLD that 403'd (`.in` here), because the wrong
    TLD's 500 is darwinbox itself answering, not Cloudflare.
    """
    from contextlib import contextmanager

    import headstart.browser_http as bh

    _darwinbox_curl_wall(monkeypatch)
    listing = _load("darwinbox_licious.json")
    navigated = []

    @contextmanager
    def _origin(page_url):
        navigated.append(page_url)
        yield _FakeDarwinboxPage([listing])

    monkeypatch.setattr(bh, "origin", _origin)
    scraper = get_scraper("darwinbox", "licious", "Licious")
    raw = scraper.fetch_raw()

    assert navigated == ["https://licious.darwinbox.in/ms/candidate/careers"]
    jobs = scraper.parse(raw, SCRAPED_AT)  # same JSON in -> parse untouched
    assert [j.id for j in jobs] == [
        "darwinbox:licious:5ebea18409d3e",
        "darwinbox:licious:a6610fb2a780ac",
    ]
    assert jobs[0].url == (
        "https://licious.darwinbox.in/ms/candidatev2/main/careers/jobDetails/5ebea18409d3e"
    )


def test_darwinbox_browser_route_paginates_full_pages(monkeypatch):
    """A full first page keeps fetching until a short batch, exactly like the curl path."""
    from contextlib import contextmanager

    import headstart.browser_http as bh
    import headstart.scrapers.darwinbox as db

    _darwinbox_curl_wall(monkeypatch)
    full = [{"id": f"a{i}"} for i in range(db._PAGE_SIZE)]
    fake = _FakeDarwinboxPage([full, [{"id": "last"}]])

    @contextmanager
    def _origin(page_url):
        yield fake

    monkeypatch.setattr(bh, "origin", _origin)
    raw = get_scraper("darwinbox", "licious", "Licious").fetch_raw()
    assert len(raw) == db._PAGE_SIZE + 1
    assert [b["page"] for b in fake.posted] == [1, 2]


def test_darwinbox_no_wall_no_browser_raises_the_last_error(monkeypatch):
    """Without a 403 there is nothing to escalate: the last real error surfaces."""
    from curl_cffi.requests import models
    from curl_cffi.requests.exceptions import HTTPError

    import headstart.scrapers.darwinbox as db

    def _response(status, reason):
        r = models.Response()
        r.status_code, r.ok, r.reason = status, False, reason
        return r

    monkeypatch.setattr(
        db.http, "fetch", lambda m, u, **k: _response(500, "Internal Server Error")
    )
    with pytest.raises(HTTPError) as excinfo:
        get_scraper("darwinbox", "licious", "Licious").fetch_raw()
    assert excinfo.value.response.status_code == 500


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


def test_workday_repairs_a_rollup_location_from_the_detail():
    """`locationsText` is a rollup on multi-location postings, and we shipped it verbatim.

    Measured 2026-08-18 over 800 listing rows on 40 boards: 9.5% carry "N Locations" (23.1% on
    the eight boards holding the biggest description gaps — capitalone 13/20, nvidia 11/20). The
    detail response we already fetch for the description carries the real places, and its count
    matched the rollup in 45/45 sampled postings. Joined, not just the primary: the location
    filter is a substring LIKE (ADR-0024), so every place a posting is open in should match it.
    """
    raw = [
        {
            "title": "A",
            "locationsText": "5 Locations",
            "bulletFields": ["R1"],
            "_detail": {
                "location": "London",
                "additionalLocations": ["Dublin", "Warsaw", "Paris", "Berlin"],
            },
        },
        {
            "title": "B",
            "locationsText": "Austin, TX",
            "bulletFields": ["R2"],
            "_detail": {"location": "Somewhere Else"},
        },
    ]
    jobs = get_scraper(
        "workday", "https://acme.wd1.myworkdayjobs.com/careers", "Acme"
    ).parse(raw, SCRAPED_AT)
    assert jobs[0].location == "London; Dublin; Warsaw; Paris; Berlin"
    assert jobs[1].location == "Austin, TX", (
        "a real listing location is authoritative — the detail must not override it"
    )


def test_workday_fills_a_missing_location_from_the_detail():
    """Accenture ships `locationsText: null` on every posting sampled (60/60 across three
    offsets), so that whole board carries no location at all. The repair keys on missing *or*
    rollup — a rollup-only regex would leave the largest board in the pool unfixed."""
    raw = [
        {
            "title": "A",
            "locationsText": None,
            "bulletFields": ["R1"],
            "_detail": {"location": "Pune, PDC2C"},
        }
    ]
    jobs = get_scraper(
        "workday", "https://acme.wd1.myworkdayjobs.com/careers", "Acme"
    ).parse(raw, SCRAPED_AT)
    assert jobs[0].location == "Pune, PDC2C"


def test_workday_rollup_no_longer_asserts_a_posting_is_not_remote():
    """The knock-on that made the rollup worse than cosmetic.

    `is_remote("2 Locations")` returns False, not None — so a rollup didn't merely lose the
    place, it positively asserted the job was on-site. Listing `remoteType` covered only 10/200
    sampled postings, so the rollup decided for ~95% of them, and 4 of 45 sampled rollups hid an
    explicitly remote location.
    """
    raw = [
        {
            "title": "A",
            "locationsText": "2 Locations",
            "bulletFields": ["R1"],
            "_detail": {
                "location": "US, CA, Remote",
                "additionalLocations": ["US, Remote"],
            },
        }
    ]
    jobs = get_scraper(
        "workday", "https://acme.wd1.myworkdayjobs.com/careers", "Acme"
    ).parse(raw, SCRAPED_AT)
    assert jobs[0].remote is True


def test_workday_reads_remote_type_from_the_detail_when_the_listing_is_silent():
    """The detail carries `remoteType` on 50/200 sampled postings against 10/200 in the listing,
    and `_extract_detail` discarded it. A decisive listing value still wins."""
    raw = [
        {
            "title": "A",
            "locationsText": "Austin, TX",
            "bulletFields": ["R1"],
            "_detail": {"remoteType": "Remote"},
        },
        {
            "title": "B",
            "locationsText": "Austin, TX",
            "remoteType": "On-site",
            "bulletFields": ["R2"],
            "_detail": {"remoteType": "Remote"},
        },
    ]
    jobs = get_scraper(
        "workday", "https://acme.wd1.myworkdayjobs.com/careers", "Acme"
    ).parse(raw, SCRAPED_AT)
    assert [j.remote for j in jobs] == [True, False]


def test_workday_extract_detail_carries_the_location_fields():
    """The parse-side repair is worthless if the fetch side drops the fields. One extractor
    serves both the sync and the async detail paths, so this pins both."""

    class _Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "jobPostingInfo": {
                    "jobDescription": "<p>hi</p>",
                    "location": "London",
                    "additionalLocations": ["Dublin"],
                    "remoteType": "Remote Available",
                }
            }

    from headstart.scrapers.workday import WorkdayScraper

    got = WorkdayScraper._extract_detail(_Response())
    assert got["location"] == "London"
    assert got["additionalLocations"] == ["Dublin"]
    assert got["remoteType"] == "Remote Available"


def test_workday_keeps_the_rollup_when_the_detail_never_arrived():
    """A failed detail fetch leaves `_detail` empty and the Job is still kept (module
    docstring). Better a rollup string than None — it is what the listing said."""
    raw = [{"title": "A", "locationsText": "3 Locations", "bulletFields": ["R1"]}]
    jobs = get_scraper(
        "workday", "https://acme.wd1.myworkdayjobs.com/careers", "Acme"
    ).parse(raw, SCRAPED_AT)
    assert jobs[0].location == "3 Locations"


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
    monkeypatch.setattr(s, "_post", lambda applied, offset, **_: pages[offset])
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


def test_personio_slug_from_keeps_only_the_host():
    """Discovery stored the raw Common Crawl capture for host-shaped ATSes, so 634 rows in the
    personio ledger carry a job deep link with tracking params instead of the board. A path alone
    404s honestly; a *query* is silent, because `url()` appends /xml and on `...?language=de` that
    lands inside the query string — Personio then serves the HTML job page with a 200 and the XML
    parse dies (678 ParseErrors over 19 pipeline runs)."""
    s = PersonioScraper.slug_from
    host = "falkemedia.jobs.personio.de"
    assert s("falkemedia", f"https://{host}") == host
    assert s("falkemedia", f"https://{host}/") == host
    assert s("falkemedia", f"https://{host}/job/186062") == host
    assert s("falkemedia", f"https://{host}/job/186062?language=de") == host
    assert s("falkemedia", f"https://{host}/?language=de") == host
    assert (
        s(
            "apploft",
            "https://apploft.jobs.personio.com/job/609444?utm_id=1&utm_source=x",
        )
        == "apploft.jobs.personio.com"
    )


def test_personio_slug_from_falls_back_when_the_host_is_not_personio():
    """The `personio` test must read the host, not the whole URL: a path segment naming personio
    on some other domain would otherwise be taken for a board host."""
    assert (
        PersonioScraper.slug_from("acme", "https://example.com/personio/job/1")
        == "acme.jobs.personio.de"
    )
    assert PersonioScraper.slug_from("acme", "") == "acme.jobs.personio.de"


def test_personio_url_is_the_xml_feed_on_a_normalised_slug():
    """The seam the ledger data broke: /xml has to terminate the URL, not land inside a query."""
    slug = PersonioScraper.slug_from(
        "falkemedia", "https://falkemedia.jobs.personio.de/job/186062?language=de"
    )
    assert (
        get_scraper("personio", slug, "falkemedia").url()
        == "https://falkemedia.jobs.personio.de/xml"
    )


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


def test_eightfold_resweeps_an_unstable_list_to_completeness():
    """The index flap's dominant cause, pinned at the scraper (#142).

    PCSX serves ``/api/pcsx/search`` from replicas whose orderings disagree — ``postedTs`` has
    day resolution, so hundreds of postings tie and each replica breaks the ties its own way. One
    offset crawl then returns some jobs twice and others never, and because duplicate rows counted
    toward ``data.count`` the crawl believed itself complete: no truncation mark, Board stays in
    the eviction scope, and sync evicts the missed jobs as delistings. Next run misses a
    *different* subset, so they come back as adds. Measured on ngc.eightfold.ai: 3,685 rows
    fetched, 3,460 unique — 225 jobs missed per crawl, and 93% of all eightfold evictions were
    re-added within the audit window.

    A crawl must judge completeness on *distinct* postings and re-sweep to pick up what the next
    replica deals to different offsets.
    """
    from headstart.scrapers.eightfold import EightfoldScraper

    class _Resp:
        def __init__(self, ids, count=30):
            self.status_code = 200
            self._body = {
                "data": {"positions": [{"id": n} for n in ids], "count": count}
            }

        def json(self):
            return self._body

    pages = [
        _Resp(range(10)),  # sweep 1: pages disagree on ordering —
        _Resp(range(10, 20)),
        _Resp([5, 6, 7, 8, 9, 20, 21, 22, 23, 24]),  # 5-9 again; 25-29 never dealt
        _Resp([25, 26, 27, 28, 29, 0, 1, 2, 3, 4]),  # sweep 2 finds the missed five
    ]
    scraper = EightfoldScraper("acme.eightfold.ai")
    scraper._get = lambda *a, **k: pages.pop(0)

    got = scraper._api_search("acme")

    ids = [str(p["id"]) for p in got]
    assert sorted(ids, key=int) == [str(n) for n in range(30)], (
        "every distinct posting must be present exactly once"
    )
    assert len(ids) == len(set(ids)), "duplicate rows must not be returned"
    assert scraper.truncated is None, (
        "a re-sweep that completed the list is not a truncation"
    )


def test_eightfold_marks_a_persistently_short_list_truncated():
    """When re-sweeps stop finding new postings the gap is real: report it, so sync keeps the
    Board out of the eviction scope instead of reading the never-dealt jobs as delistings."""
    from headstart.scrapers.eightfold import EightfoldScraper

    class _Resp:
        def __init__(self, ids):
            self.status_code = 200
            self._body = {"data": {"positions": [{"id": n} for n in ids], "count": 30}}

        def json(self):
            return self._body

    # Every sweep deals the same 25 postings; 5 of the advertised 30 never appear.
    sweep = [range(10), range(10, 20), [5, 6, 7, 8, 9, 20, 21, 22, 23, 24]]
    pages = [_Resp(ids) for _ in range(3) for ids in sweep]
    scraper = EightfoldScraper("acme.eightfold.ai")
    scraper._get = lambda *a, **k: pages.pop(0)

    got = scraper._api_search("acme")

    assert len({str(p["id"]) for p in got}) == 25
    assert scraper.truncated, "a list still short after re-sweeps must say so"
    assert "25 of 30" in scraper.truncated


def test_mark_truncated_keeps_the_first_reason():
    """A crawl that gave up once tends to give up again, and the reasons that follow are
    consequences of the first — so the first one is the one worth reporting (ADR-0053)."""
    scraper = get_scraper("greenhouse", "acme")
    scraper.mark_truncated("HTTP 429 on page 2 — got 10 of 300 postings")
    scraper.mark_truncated("empty page 3 — got 10 of 300 postings")
    assert scraper.truncated == "HTTP 429 on page 2 — got 10 of 300 postings"


def _successfactors_board(monkeypatch, *, search, rss, sitemap=("rss", "", None)):
    """A SuccessFactors scraper whose three listing surfaces are stubbed. Each returns what the
    real one does — its list plus why-it-came-up-short: ``sitemap`` as ``(kind, text, cut_short)``
    (defaulting to an RSS classification, so the whole fallback chain runs), ``search`` as
    ``(pairs, cut_short)`` from the ``/search/`` walk, ``rss`` as ``(pairs, cut_short)`` from the
    patient stream."""
    from headstart.scrapers.successfactors import SuccessFactorsScraper

    monkeypatch.setenv(
        "HEADSTART_ASYNC_FANOUT", "0"
    )  # keep the detail pass on the sync path
    scraper = SuccessFactorsScraper("careers.voith.com")
    monkeypatch.setattr(scraper, "_fetch_sitemap", lambda: sitemap)
    monkeypatch.setattr(scraper, "_search_job_urls", lambda: search)
    monkeypatch.setattr(scraper, "_rss_job_urls", lambda: rss)
    monkeypatch.setattr(scraper, "_job_fields", lambda url: {"title": "Engineer"})
    return scraper


class _SearchPage:
    def __init__(self, status, text=""):
        self.status_code = status
        self.text = text


class _StreamedBody:
    """A streamed ``requests`` response standing in for one of the two sitemap.xml reads."""

    def __init__(self, chunks, status=200):
        self._chunks = chunks
        self.status_code = status

    def iter_content(self, *args, **kwargs):
        yield from self._chunks

    def close(self):
        pass


def _stub_stream(monkeypatch, sf, response):
    monkeypatch.setattr(
        sf.http,
        "session",
        lambda: type("_S", (), {"request": lambda *a, **k: response})(),
    )


def test_successfactors_sitemap_read_reports_hitting_the_size_cap(monkeypatch):
    """The urlset read stops at ``_SITEMAP_CAP``, and everything past it is simply never read —
    unlisted, not absent. Without the reason travelling out of here, `index sync` reads those
    postings as delistings and evicts them (ADR-0053)."""
    from headstart.scrapers import successfactors as sf

    monkeypatch.setattr(sf, "_SITEMAP_CAP", 2 * 1024 * 1024)
    scraper = sf.SuccessFactorsScraper("jobs.example.com")
    oversized = b"<urlset><loc>https://jobs.example.com/job/x/1/</loc>" + b" " * (
        2 * 1024 * 1024
    )
    _stub_stream(monkeypatch, sf, _StreamedBody([oversized]))

    kind, _text, cut_short = scraper._fetch_sitemap()

    assert kind == "urlset"
    assert cut_short and "2 MB read cap" in cut_short
    assert scraper.truncated is None  # reported to fetch_raw, not recorded here

    # ...while a sitemap that fits is read whole and reports nothing.
    _stub_stream(monkeypatch, sf, _StreamedBody([b"<urlset></urlset>"]))
    assert scraper._fetch_sitemap()[2] is None


def test_successfactors_rss_stream_reports_a_feed_that_aborted_mid_read(monkeypatch):
    """Voith's generator dies ~2 MB into the feed. Keeping the links that arrived is right —
    partial beats none — but the list is knowingly short and must say so (ADR-0053)."""
    from headstart.scrapers import successfactors as sf

    def torn():
        yield b"<loc>https://jobs.example.com/job/x/7/</loc>"
        raise sf.http.RequestsError("connection reset by peer")

    scraper = sf.SuccessFactorsScraper("jobs.example.com")
    _stub_stream(monkeypatch, sf, _StreamedBody(torn()))

    found, cut_short = scraper._rss_job_urls()

    assert [job_id for _url, job_id in found] == ["7"]  # what arrived is still scraped
    assert cut_short and "aborted" in cut_short
    assert scraper.truncated is None  # reported to fetch_raw, not recorded here

    # ...a feed still streaming at `_SITEMAP_CAP` is the same kind of short list.
    monkeypatch.setattr(sf, "_SITEMAP_CAP", 2 * 1024 * 1024)
    _stub_stream(
        monkeypatch,
        sf,
        _StreamedBody(
            [b"<loc>https://jobs.example.com/job/x/7/</loc>" + b" " * (2 * 1024 * 1024)]
        ),
    )
    assert "2 MB read cap" in scraper._rss_job_urls()[1]

    # ...while a feed that streams to its end reports nothing.
    _stub_stream(
        monkeypatch,
        sf,
        _StreamedBody([b"<loc>https://jobs.example.com/job/x/7/</loc>"]),
    )
    assert scraper._rss_job_urls()[1] is None


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
        rss=([("https://careers.voith.com/job/x/1/", "1")], None),
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
        rss=([("https://careers.voith.com/job/x/1/", "1")], None),
    )

    scraper.fetch_raw()

    assert scraper.truncated == (
        "HTTP 503 at startrow 25 — 1 postings read before the walk stopped"
    )


def test_successfactors_reports_an_rss_stream_that_ended_early(monkeypatch):
    """The RSS stream is the last resort, and it keeps whatever arrived when the tenant's own
    generator aborts mid-feed (Voith's dies ~2 MB in) — a list the scraper knows is short, which
    ADR-0053 requires it say so about. Nothing follows this surface, so when it lists anything
    it *is* the Board's answer and its truncation is the Board's."""
    scraper = _successfactors_board(
        monkeypatch,
        search=([], None),
        rss=(
            [("https://careers.voith.com/job/x/1/", "1")],
            (
                "the tenant's RSS feed aborted 2,097,152 bytes in — postings past that point "
                "were not listed"
            ),
        ),
    )

    raw = scraper.fetch_raw()

    assert [item["id"] for item in raw] == ["1"]  # partial still beats none
    assert scraper.truncated == (
        "the tenant's RSS feed aborted 2,097,152 bytes in — postings past that point "
        "were not listed"
    )


def test_successfactors_reports_a_sitemap_cut_at_the_read_cap(monkeypatch):
    """The urlset surface answers most tenants, and `_fetch_sitemap` stops reading at
    `_SITEMAP_CAP`. Past that the document is simply not read, so the job URLs it holds are
    unlisted, not absent (ADR-0053)."""
    scraper = _successfactors_board(
        monkeypatch,
        search=([], None),
        rss=([], None),
        sitemap=(
            "urlset",
            "<loc>https://careers.voith.com/job/x/1/</loc>",
            "the sitemap hit the 30 MB read cap — postings past it were not listed",
        ),
    )

    raw = scraper.fetch_raw()

    assert [item["id"] for item in raw] == ["1"]
    assert scraper.truncated == (
        "the sitemap hit the 30 MB read cap — postings past it were not listed"
    )


def test_successfactors_keeps_a_capped_sitemap_that_listed_nothing_off_the_board(
    monkeypatch,
):
    """The round-3 lesson applied to the sitemap: a read that hit the cap before a single job URL
    appeared lists nothing, so the search walk answers — and it can answer with the whole board,
    which must not inherit the sitemap's truncation."""
    scraper = _successfactors_board(
        monkeypatch,
        search=([("https://careers.voith.com/job/x/9/", "9")], None),
        rss=([], None),
        sitemap=(
            "urlset",
            "<urlset></urlset>",
            "the sitemap hit the 30 MB read cap — postings past it were not listed",
        ),
    )

    raw = scraper.fetch_raw()

    assert [item["id"] for item in raw] == ["9"]  # the search walk answered, and whole
    assert scraper.truncated is None


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

    def post(applied, offset, *, raise_gone=False):
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


def test_workday_raises_when_the_whole_site_is_gone(monkeypatch):
    """A 404 on the *unfiltered* first page means the site is gone, and it must RAISE.

    It used to return None and read as "no jobs": the Board wrote no lines, so nothing flagged
    it and nothing could act on it — a dead Workday board looked exactly like a live empty one
    for as long as it stayed in the ledger. ADR-0058 counts only a *raised* 404/410 as a
    gone-verdict, so swallowing it here is what would keep such a board un-quarantinable
    forever. A truncation flag is still not the answer (there are no partial rows to protect) —
    the error is.
    """
    scraper = _workday_scraper()

    class _Gone:
        status_code = 404

        def raise_for_status(self):
            raise http.RequestsError("HTTP Error 404: Not Found")

    monkeypatch.setattr(http, "fetch", lambda *a, **k: _Gone())

    with pytest.raises(http.RequestsError, match="404"):
        scraper._exhaust({}, lambda batch: None, depth=0)
    assert scraper.truncated is None  # an error, not a truncation


def test_workday_keeps_the_none_path_for_a_subdivided_slice(monkeypatch):
    """Only depth 0 raises. A subdivided slice that 404s is one slice of a live board, so it
    stays a reported truncation and its siblings' postings still ship — raising there would
    throw away a whole board over one vanished facet."""
    scraper = _workday_scraper()
    seen: list[bool] = []

    def post(applied, offset, *, raise_gone=False):
        seen.append(raise_gone)
        return None if applied else {"total": 1, "jobPostings": [], "facets": []}

    monkeypatch.setattr(scraper, "_post", post)
    scraper._exhaust({"jobFamilyGroup": ["Eng"]}, lambda batch: None, depth=1)

    assert seen == [False], "a subdivided slice must not raise on 404"
    assert scraper.truncated and "jobFamilyGroup=Eng" in scraper.truncated


def test_workday_reports_a_capped_query_it_cannot_subdivide(monkeypatch):
    """``total`` stuck at exactly 2,000 means the real total is higher; with no facet left to
    split there is no second query to reach the rest, so the crawl paginates 2,000 of a
    knowingly larger board — eightfold's page ceiling in Workday form (ADR-0053)."""
    scraper = _workday_scraper()
    monkeypatch.setattr(
        scraper,
        "_post",
        lambda applied, offset, **_: {"total": 2000, "jobPostings": [], "facets": []},
    )

    scraper._exhaust({}, lambda batch: None, depth=0)

    assert scraper.truncated and "2000" in scraper.truncated

    # ...while a board whose total is under the cap paginates to the end and says nothing.
    whole = _workday_scraper()
    monkeypatch.setattr(
        whole,
        "_post",
        lambda applied, offset, **_: {"total": 40, "jobPostings": [], "facets": []},
    )
    whole._exhaust({}, lambda batch: None, depth=0)
    assert whole.truncated is None


# --- listing-level errors must raise, never read as an empty board (ADR-0058) -----------------
#
# A scraper that maps a dead listing endpoint to `[]` presents a gone board as alive-and-empty:
# it writes no lines, so `index sync` never reaches it, no error reaches the shard report, and the
# consecutive-gone quarantine — which counts only a *raised* 404/410 — can never fire. Each case
# below reverts to that shape if the guard is removed.


class _Status:
    """A minimal response whose raise_for_status behaves like curl_cffi's."""

    def __init__(self, status_code=200, text="", payload=None, url=""):
        self.status_code = status_code
        self.text = text
        self._payload = payload
        self.url = url

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not (200 <= self.status_code < 400):
            raise http.RequestsError(f"HTTP Error {self.status_code}: Not Found")


@pytest.mark.parametrize(
    ("ats", "slug"),
    [
        ("lever", "gone-co"),
        ("rippling", "gone-co"),
        ("join", "gone-co"),
        ("ripplehire", "gone-co"),
    ],
)
def test_a_dead_listing_endpoint_raises_instead_of_reading_as_empty(
    monkeypatch, ats, slug
):
    monkeypatch.setattr(http, "fetch", lambda *a, **k: _Status(status_code=404))
    with pytest.raises(http.RequestsError, match="404"):
        get_scraper(ats, slug).fetch_raw()


def test_eightfold_sitemap_surface_raises_when_it_is_the_last_surface(monkeypatch):
    """The sitemap is only reached once the careers page and the API have failed, so a non-200
    there means the board went entirely unread — not that it is empty."""
    monkeypatch.setattr(http, "fetch", lambda *a, **k: _Status(status_code=404))
    with pytest.raises(http.RequestsError, match="404"):
        get_scraper("eightfold", "gone.eightfold.ai").fetch_raw()


def test_zoho_raises_when_the_page_shape_changes(monkeypatch):
    """A careers page carrying the jobs input whose JSON will not parse is Zoho changing shape
    under us. Swallowing it would empty every zoho board at once and sync would evict them all."""
    from headstart.scrapers.zoho import ZohoScraper

    with pytest.raises(json.JSONDecodeError):
        ZohoScraper._records('<input value="{not json" id="jobs" />')
    # ...while a page with no jobs input at all is simply an empty board.
    assert ZohoScraper._records("<html>no jobs here</html>") == []


def test_zoho_slug_from_keeps_only_the_host():
    """Same shape as the personio bug: `url()` appends `/jobs/Careers`, so a stored job deep link
    would put that suffix inside the path or query and fetch something that is not the board.
    Latent rather than active — zoho's 44 pathy / 19 query ledger rows are all dead today."""
    from headstart.scrapers.zoho import ZohoScraper

    host = "acme.zohorecruit.in"
    assert ZohoScraper.slug_from("acme", f"https://{host}") == host
    assert ZohoScraper.slug_from("acme", f"https://{host}/") == host
    assert ZohoScraper.slug_from("acme", f"https://{host}/jobs/Careers/123") == host
    assert ZohoScraper.slug_from("acme", f"https://{host}/jobs?utm_source=x") == host
    assert get_scraper("zoho", host).url() == f"https://{host}/jobs/Careers"


def test_eightfold_api_probe_routes_over_the_spare_egress_but_never_marks(monkeypatch):
    """The first `/api/pcsx/search` page asks "does this tenant expose the API at all?", and ~40%
    of tenants answer a steady 403 there followed by a healthy 200 on the sitemap.

    So it must not *mark* the ATS walled (that would dial the spare egress on nearly every shard,
    on the normal path) — but it must still *route* over it once something else has, or on exactly
    the walled shard the probe 403s against the spent IP and every remaining Board falls through to
    the per-job sitemap path, thousands of fetches inside a 60-minute budget (ADR-0063).
    """
    from headstart.scrapers.eightfold import EightfoldScraper

    seen: list[tuple[str, dict]] = []

    class _Resp:
        status_code = 403
        headers: ClassVar[dict] = {}
        text = ""

    monkeypatch.setattr(
        http, "fetch", lambda method, url, **kw: (seen.append((url, kw)), _Resp())[1]
    )
    scraper = EightfoldScraper("symetra.eightfold.ai")

    scraper._get(scraper._search_url("symetra.com", 0), marks_wall=False)
    probe = seen[-1][1]
    assert probe["egress_group"] == "eightfold"  # still routed once the ATS is walled
    assert probe["egress_on"] == frozenset()  # but can never be what walls it

    scraper._get("https://symetra.eightfold.ai/careers/sitemap.xml")
    wall_surface = seen[-1][1]
    assert wall_surface["egress_group"] == "eightfold"
    assert wall_surface["egress_on"] == frozenset({403, 405})


def test_workday_opts_into_the_spare_egress_on_429(monkeypatch):
    """Provisional experiment (ADR-0063, amended): Workday's metering was measured to be per
    (source IP x instance host), so a second egress is a second allocation rather than a way of
    ignoring a rate limit. Only the sync listing POST can lose a Board — a detail failure returns
    None — so that is the call that must carry the opt-in.
    """
    from headstart.scrapers.workday import WorkdayScraper

    assert WorkdayScraper.egress_fallback_on == frozenset({429})

    seen: list[dict] = []

    class _Resp:
        status_code = 200
        headers: ClassVar[dict] = {}

        @staticmethod
        def json():
            return {"jobPostings": [], "total": 0}

        @staticmethod
        def raise_for_status():
            return None

    monkeypatch.setattr(
        http, "fetch", lambda method, url, **kw: (seen.append(kw), _Resp())[1]
    )
    scraper = WorkdayScraper("https://micron.wd1.myworkdayjobs.com/External")
    scraper._post({}, 0)
    assert seen[-1]["egress_group"] == "workday"
    assert seen[-1]["egress_on"] == frozenset({429})


def test_workday_429_does_not_leak_the_opt_in_to_other_scrapers():
    """The opt-in is per scraper; an ATS that never walled us must stay on its direct route."""
    from headstart.scrapers.greenhouse import GreenhouseScraper

    assert GreenhouseScraper.egress_fallback_on == frozenset()
    assert GreenhouseScraper("acme")._egress() == {}


def test_workday_a_surviving_rollup_leaves_remote_unknown():
    """The half of the repair that a detail fetch failure would otherwise skip.

    When no detail arrives the rollup string stays — better than None — but it must not decide
    remoteness: `is_remote("3 Locations")` returns False, asserting on-site when the honest
    answer is that we cannot tell. Getting this wrong locks in the exact harm the repair exists
    to remove, on the one path where nothing else can correct it.
    """
    raw = [{"title": "A", "locationsText": "3 Locations", "bulletFields": ["R1"]}]
    jobs = get_scraper(
        "workday", "https://acme.wd1.myworkdayjobs.com/careers", "Acme"
    ).parse(raw, SCRAPED_AT)
    assert jobs[0].location == "3 Locations"
    assert jobs[0].remote is None


def test_workday_a_malformed_additional_locations_does_not_explode_into_letters():
    """`or []` over a bare string iterates it character by character. Live data is list-of-str
    in every posting sampled, so this guards the shape rather than a seen failure."""
    raw = [
        {
            "title": "A",
            "locationsText": "2 Locations",
            "bulletFields": ["R1"],
            "_detail": {"location": "London", "additionalLocations": "Dublin"},
        }
    ]
    jobs = get_scraper(
        "workday", "https://acme.wd1.myworkdayjobs.com/careers", "Acme"
    ).parse(raw, SCRAPED_AT)
    assert jobs[0].location == "London"
