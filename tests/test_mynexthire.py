"""Tests for `headstart.scrapers.mynexthire`.

Fixtures are real `/employer/careers/reqlist/get` responses captured 2026-09-30:
`mynexthire_microlise_reqlist.json` (all 11 postings, a tech-heavy Board),
`mynexthire_sharechat_reqlist.json` (all 5, contract and monthly-currency rows) and
`mynexthire_azentio_reqlist_one_posting.json` (one of 12, from the tenant whose `careerStream` is
"NA" on every posting). `mynexthire_sharechat_client_trimmed.json` is the
`/employer/jobboard/details_by_shortname/get/{slug}/` record cut to the three keys it names the
company with. Every number cited is in `docs/mynexthire/2026-09-30_reqlist-measurement.md`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fake_fetcher import FakeFetcher, FakeResponse

from headstart.network import http
from headstart.scrapers.base import BoardUnreadable
from headstart.scrapers.mynexthire import MyNextHireScraper
from headstart.scrapers.registry import get_scraper

FIXTURES = Path(__file__).parent / "fixtures"
SCRAPED_AT = "2026-09-30T00:00:00+00:00"


def _fixture(name: str) -> dict:
    with open(FIXTURES / name, encoding="utf-8") as fh:
        return json.load(fh)


def _jobs(slug: str, fixture: str) -> dict:
    scraper = MyNextHireScraper(slug)
    return {
        j.id.rsplit(":", 1)[1]: j for j in scraper.parse(_fixture(fixture), SCRAPED_AT)
    }


def test_the_scraper_is_registered_under_its_ats_name():
    assert isinstance(get_scraper("mynexthire", "swiggy"), MyNextHireScraper)


def test_a_posting_parses_every_field_the_listing_states():
    job = _jobs("microlise", "mynexthire_microlise_reqlist.json")["187"]
    assert job.id == "mynexthire:microlise:187"
    assert job.ats == "mynexthire"
    assert job.title == "Lead Engineer"
    assert job.location == "Pune"
    assert job.department == "Engineering"
    assert job.posted_at == "2026-09-07T05:46:36.697000+00:00"
    assert job.scraped_at == SCRAPED_AT
    assert job.experience == "9-11 years"
    assert job.employment_type == "Full-time"
    assert job.salary is None
    assert job.description.startswith("Job title: Lead Engineer")
    assert "Angular 15+" in job.description


def test_the_job_url_is_the_careers_page_opened_on_the_posting():
    """The link the careers page itself builds (`encoder.js` `getEncodedJobboardLink`): `p` is
    base64 of the page context `getQStringObject` makes, with `pageType` "jd". It rendered the
    posting in a real browser on swiggy, sharechat and microlise, and a closed id renders "Oops!
    Something went wrong!" (2026-09-30)."""
    url = MyNextHireScraper("microlise").job_url("187")
    assert url == (
        "https://microlise.mynexthire.com/employer/jobs/careers?src=careers&p="
        "eyJwYWdlVHlwZSI6ImpkIiwiY3ZTb3VyY2UiOiJjYXJlZXJzIiwicmVxSWQiOjE4NywicmVxdWVzdGVyIjp7"
        "ImlkIjoiIiwiY29kZSI6IiIsIm5hbWUiOiIifSwicGFnZSI6ImNhcmVlcnMiLCJidWZpbHRlciI6LTF9"
    )
    assert _jobs("microlise", "mynexthire_microlise_reqlist.json")["187"].url == url


def test_url_shape_matches_every_job_url_this_scraper_produces():
    for slug, fixture in (
        ("microlise", "mynexthire_microlise_reqlist.json"),
        ("sharechat", "mynexthire_sharechat_reqlist.json"),
    ):
        for job in _jobs(slug, fixture).values():
            assert re.fullmatch(MyNextHireScraper.url_shape, job.url), job.url


def _one(**fields) -> object:
    """A real sharechat posting with ``fields`` replaced, parsed."""
    row = {
        **_fixture("mynexthire_sharechat_reqlist.json")["reqDetailsBOList"][0],
        **fields,
    }
    [job] = MyNextHireScraper("sharechat").parse(
        {"reqDetailsBOList": [row]}, SCRAPED_AT
    )
    return job


@pytest.mark.parametrize(
    ("stated", "served"),
    [
        ("full_time", "Full-time"),
        ("full-time", "Full-time"),
        ("permanent", "Permanent"),
        ("onroll", "Full-time"),
        ("contract", "Contract"),
        ("fixed-term-contract", "Contract"),
        ("consultant", "Contract"),
        ("third_party_consultant", "Contract"),
        ("intern", "Internship"),
        ("internship", "Internship"),
        ("conversion", None),
        ("azentio_group", None),
    ],
)
def test_employment_type_maps_every_value_measured(stated, served):
    """All twelve values seen in two reads of the live Boards (2026-09-30).
    `third_party_consultant` would read part-time raw (the filter finds "part" in
    "third_party"); `azentio_group` (a tenant's own label, 12 postings) and `conversion` (2, no
    stated meaning) name no type."""
    assert _one(employmentType=stated).employment_type == served


@pytest.mark.parametrize(
    ("low", "high", "served"),
    [
        (0.0, 0.0, "0-0 years"),  # a fresher posting (6 of 345)
        (2.0, 4.0, "2-4 years"),
        # The three fractional pairs measured (aziro); `experience.from_field` reads "6.5-7.5
        # years" as 6 with no ceiling, so the bounds widen to whole years instead.
        (0.6, 2.0, "0-2 years"),
        (6.5, 7.5, "6-8 years"),
        (5.5, 7.5, "5-8 years"),
    ],
)
def test_experience_is_the_stated_bounds_in_whole_years(low, high, served):
    assert _one(expMin=low, expMax=high).experience == served


def test_experience_is_none_when_a_bound_is_missing():
    assert _one(expMin=None).experience is None


def test_department_is_the_career_stream():
    """`careerStream` over `buName`: the tech gate keeps 202 of 345 postings with it and 164
    with `buName`, and none that `buName` keeps is lost (docs, §tech gate)."""
    assert (
        _jobs("sharechat", "mynexthire_sharechat_reqlist.json")["2443"].department
        == "BD"
    )


def test_a_career_stream_of_na_falls_back_to_the_business_unit():
    """azentio states `careerStream` "NA" on all 12 postings; its `buName` is the department."""
    job = _jobs("azentio", "mynexthire_azentio_reqlist_one_posting.json")["1226"]
    assert job.department == "Product Engineering"
    assert job.title == "Senior Software Engineer - Oracle Forms & Reports"
    assert job.location == "Chennai - India"


def test_location_joins_every_office_the_posting_names():
    """One office on all 345 postings measured, but `locationList` is a list, and a posting cut
    to its first place fails the location filter everywhere else (#561)."""
    offices = [
        {"office": "Pune", "address": "Pune"},
        {"office": "Bangalore", "address": "Bangalore"},
    ]
    assert _one(locationList=offices).location == "Pune; Bangalore"


def test_location_falls_back_to_the_location_field_with_no_list():
    assert _one(locationList=None, location="Mumbai").location == "Mumbai"


def test_remote_is_read_off_the_location():
    """No field states it: none of 345 locations names remote work."""
    assert _one(locationList=[{"office": "Remote", "address": ""}]).remote is True
    assert _one().remote is not True


def _served(reqlist: FakeResponse, client: FakeResponse | None = None) -> FakeFetcher:
    """The listing POST answered by ``reqlist``; the client record GET by ``client``."""

    def route(method, url, kwargs):
        if url.endswith("/employer/careers/reqlist/get"):
            return reqlist
        if "/employer/jobboard/details_by_shortname/get/" in url and client is not None:
            return client
        return AssertionError(f"unexpected {method} {url}")

    return FakeFetcher(route)


def _ok(fixture: str) -> FakeResponse:
    return FakeResponse(200, (FIXTURES / fixture).read_text(encoding="utf-8"))


def test_fetch_posts_the_one_mandatory_field_and_names_the_company():
    """`{"source": "careers"}` answered byte-for-byte what the upstream's three-field body did
    on swiggy (420,476 bytes both); an empty body answers 417 "Source is mandatory.". The name
    is the client record's `clientName` (ShareChat, Swiggy, Aziro, 3 of 3)."""
    fetcher = _served(
        _ok("mynexthire_sharechat_reqlist.json"),
        _ok("mynexthire_sharechat_client_trimmed.json"),
    )
    scraper = get_scraper("mynexthire", "sharechat", "sharechat", fetcher=fetcher)
    jobs = scraper.fetch()
    assert len(jobs) == 5
    assert {j.company for j in jobs} == {"ShareChat"}
    listing, client = fetcher.requests
    assert listing.method == "POST"
    assert (
        listing.url == "https://sharechat.mynexthire.com/employer/careers/reqlist/get"
    )
    assert listing.kwargs["json"] == {"source": "careers"}
    assert client.method == "GET"
    assert client.url == (
        "https://sharechat.mynexthire.com/employer/jobboard/details_by_shortname/get/sharechat/"
    )


def test_an_empty_board_is_a_null_list_and_asks_for_no_company_name():
    """meesho, whose careers page renders "Current Openings [0]", answered exactly this."""
    body = '{"requesterTitle":"","reqDetailsBOList":null,"hrXmlModel":null}'
    fetcher = _served(FakeResponse(200, body))
    assert get_scraper("mynexthire", "meesho", fetcher=fetcher).fetch() == []
    assert len(fetcher.requests) == 1


def test_a_failed_company_lookup_keeps_the_board():
    fetcher = _served(
        _ok("mynexthire_microlise_reqlist.json"), FakeResponse(503, "unavailable")
    )
    jobs = get_scraper("mynexthire", "microlise", "microlise", fetcher=fetcher).fetch()
    assert len(jobs) == 11


@pytest.mark.parametrize(
    ("status", "body"),
    [
        # An unknown label (3 of 3: an invented one, `app`, and `msystechnologies`, Aziro's
        # old label).
        (
            417,
            '{"errorMessage":"41703001:Invalid company short name: zzqxnotatenant12"}',
        ),
        # A lapsed customer (jupitermoney and sirion, 2 of 2).
        (
            402,
            (
                '{"errorMessage":"MyNextHire account subscription for client SIRION '
                'has expired."}'
            ),
        ),
    ],
)
def test_a_departed_tenant_is_gone(status, body):
    fetcher = _served(FakeResponse(status, body))
    with pytest.raises(http.RequestsError, match="410"):
        get_scraper("mynexthire", "sirion", fetcher=fetcher).fetch()


def test_any_other_refusal_is_a_plain_failure_not_gone():
    fetcher = _served(FakeResponse(417, '{"errorMessage":"Invalid source"}'))
    with pytest.raises(http.RequestsError) as caught:
        get_scraper("mynexthire", "swiggy", fetcher=fetcher).fetch()
    assert "410" not in str(caught.value)


def test_a_body_without_the_list_is_unreadable_not_empty():
    fetcher = _served(FakeResponse(200, '{"errorMessage":"something else"}'))
    with pytest.raises(BoardUnreadable):
        get_scraper("mynexthire", "swiggy", fetcher=fetcher).fetch()


def test_a_blank_description_is_none():
    """5 of 630 postings, all tenants' test requisitions, state "" (indevia's "Test")."""
    assert _one(jdDisplay="").description is None


def test_contract_postings_read_contract():
    jobs = _jobs("sharechat", "mynexthire_sharechat_reqlist.json")
    assert jobs["2451"].employment_type == "Contract"
    assert jobs["2443"].employment_type == "Full-time"
