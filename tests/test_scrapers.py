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


def test_greenhouse_location_strips_trailing_whitespace():
    # Real bug, location-field audit 2026-08-24: `location.name` ships un-trimmed padding on a
    # real minority of tenants ("Hybrid in Boston, MA   ", three trailing spaces — 22/178 sampled
    # jobs, 12.4%), and nothing downstream stripped it.
    raw = {"jobs": [{"id": 1, "title": "T", "location": {"name": "Washington D.C.  "}}]}
    jobs = get_scraper("greenhouse", "x", "X").parse(raw, SCRAPED_AT)
    assert jobs[0].location == "Washington D.C."


def test_greenhouse_location_missing_name_stays_none():
    raw = {"jobs": [{"id": 1, "title": "T", "location": {}}]}
    jobs = get_scraper("greenhouse", "x", "X").parse(raw, SCRAPED_AT)
    assert jobs[0].location is None


@pytest.mark.parametrize(
    ("envelope", "should_warn"),
    [
        ({"jobs": [{"id": 1}], "meta": {"total": 5}}, True),  # short and says so
        ({"jobs": [{"id": 1}], "meta": {"total": 1}}, False),  # healthy: agrees
        ({"jobs": [{"id": 1}]}, False),  # no meta at all — nothing to compare
        ({"jobs": [{"id": 1}], "meta": {}}, False),  # meta present but no total
        ({"jobs": [{"id": 1}], "meta": {"total": None}}, False),  # total not an int
        ({"jobs": [], "meta": {"total": 0}}, False),  # empty board, consistent
    ],
)
def test_greenhouse_reports_an_envelope_that_contradicts_itself(
    monkeypatch, caplog, envelope, should_warn
):
    """docs/pipeline/2026-08-23_false-board-eviction-root-cause.md §4.1: greenhouse's API can
    return a silently short list (200, valid JSON, no error) and this scraper had no way to see
    it. `meta.total` is the one self-contradiction signal the envelope offers; this logs it and
    deliberately does NOT mark the Board truncated, because the guard is still unverified for the
    short-response case. Observation only — so the assertion is on the log, not on `truncated`.
    """
    s = get_scraper("greenhouse", "acme", "Acme")
    monkeypatch.setattr(type(s), "_get", lambda self: json.dumps(envelope))
    caplog.set_level(logging.WARNING, logger="headstart.scrapers.greenhouse")

    raw = s.fetch_raw()

    assert raw == envelope, "the envelope must pass through untouched"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert bool(warnings) is should_warn
    if should_warn:
        assert (
            "meta.total=5" in warnings[0].message
            and "greenhouse:acme" in warnings[0].message
        )
    assert s.truncated is None, (
        "observation only — wiring this to mark_truncated is the unverified guard §4.1 declines "
        "to ship until a real short response is captured"
    )


def test_lever_parse():
    jobs = get_scraper("lever", "palantir", "Palantir").parse(
        _load("lever_palantir.json"), SCRAPED_AT
    )
    assert len(jobs) == 2
    j = jobs[0]
    assert j.id == "lever:palantir:0bbfd4f4-41ff-4ec6-b73f-5200efd5d4d3"
    assert j.company == "Palantir"
    assert j.title == "Administrative Business Partner - Security"
    # location-audit-2026-08-25/lever.md: the fixture's own `country` ("US") is absent from
    # "Palo Alto, CA", so the fix appends it — this pins that the append actually fires here,
    # not just in a synthetic case.
    assert j.location == "Palo Alto, CA, US"
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


def test_lever_location_joins_all_locations_and_recovers_hidden_india():
    # Real posting, captured live 2026-08-25: lever:spreetail:9fcfd96f-141e-4dfe-b670-
    # eb872164abe0 ("Business Solutions Analyst"). categories.location alone is "Manila";
    # allLocations also carries Bogota/India/Karachi. Before this fix, the India location was
    # invisible to geo.where("india") — the served string never contained "India" at all.
    raw = [
        {
            "id": "9fcfd96f-141e-4dfe-b670-eb872164abe0",
            "text": "Business Solutions Analyst",
            "categories": {
                "location": "Manila",
                "allLocations": ["Manila", "Bogota", "India", "Karachi"],
                "commitment": "Contractor",
                "team": "Transportation",
            },
            "country": "PH",
            "workplaceType": "remote",
            "hostedUrl": "https://jobs.lever.co/spreetail/9fcfd96f-141e-4dfe-b670-eb872164abe0",
            "createdAt": 1787247868191,
        }
    ]
    jobs = get_scraper("lever", "spreetail", "Spreetail").parse(raw, SCRAPED_AT)
    assert jobs[0].location == "Manila, Bogota, India, Karachi, PH"


def test_lever_location_country_already_present_is_not_duplicated():
    # Real posting, jobgether board (captured live 2026-08-25): a bare-country listing whose
    # `location` and `allLocations` are both just the ISO-2 code itself — the composed string
    # must not become "US, US".
    raw = [
        {
            "id": "abc123",
            "text": "Remote Role",
            "categories": {"location": "US", "allLocations": ["US"]},
            "country": "US",
            "workplaceType": "remote",
            "hostedUrl": "https://jobs.lever.co/jobgether/abc123",
            "createdAt": 1787247868191,
        }
    ]
    jobs = get_scraper("lever", "jobgether", "Jobgether").parse(raw, SCRAPED_AT)
    assert jobs[0].location == "US"


def test_lever_location_country_full_name_already_present_is_not_duplicated():
    # Real posting (lever:fuellabs, captured live 2026-08-25): `location` is the country's full
    # English name, not its code, so a bare substring check on "PT" would miss it and wrongly
    # append ", PT". allLocations must still be joined in full.
    raw = [
        {
            "id": "def456",
            "text": "Remote Engineer",
            "categories": {
                "location": "Portugal",
                "allLocations": [
                    "Portugal",
                    "Canada",
                    "Singapore",
                    "Switzerland",
                    "Germany",
                ],
            },
            "country": "PT",
            "workplaceType": "remote",
            "hostedUrl": "https://jobs.lever.co/fuellabs/def456",
            "createdAt": 1787247868191,
        }
    ]
    jobs = get_scraper("lever", "fuellabs", "Fuel Labs").parse(raw, SCRAPED_AT)
    assert jobs[0].location == "Portugal, Canada, Singapore, Switzerland, Germany"


def test_lever_location_country_code_is_not_matched_as_a_substring():
    # Found in review round 1: a bare substring check on the 2-letter code reads "in" inside
    # "Beijing" or "Cincinnati" as India already being named, and silently never appends it —
    # defeating the fix's own point (recovering a hidden India signal behind another city).
    raw = [
        {
            "id": "sub1",
            "text": "Remote Role",
            "categories": {
                "location": "Chennai",
                "allLocations": ["Chennai", "Beijing"],
            },
            "country": "IN",
            "workplaceType": "remote",
            "hostedUrl": "https://jobs.lever.co/acme/sub1",
            "createdAt": 1787247868191,
        }
    ]
    jobs = get_scraper("lever", "acme", "Acme").parse(raw, SCRAPED_AT)
    assert jobs[0].location == "Chennai, Beijing, IN"


def test_lever_location_country_code_is_not_matched_inside_a_city_name():
    # Same class, found live on real Boards in review round 1 (lever:zoox, lever:wealthfront):
    # "us" sits inside "Austin", so "Austin, TX" + country "US" must still get the code
    # appended rather than reading "us" as already present.
    raw = [
        {
            "id": "sub2",
            "text": "Remote Role",
            "categories": {
                "location": "Austin, TX",
                "allLocations": ["Austin, TX"],
            },
            "country": "US",
            "workplaceType": "onsite",
            "hostedUrl": "https://jobs.lever.co/acme/sub2",
            "createdAt": 1787247868191,
        }
    ]
    jobs = get_scraper("lever", "acme", "Acme").parse(raw, SCRAPED_AT)
    assert jobs[0].location == "Austin, TX, US"


def test_lever_location_falls_back_when_all_locations_missing():
    raw = [
        {
            "id": "ghi789",
            "text": "Some Role",
            "categories": {"location": "Berlin"},
            "country": "DE",
            "workplaceType": "onsite",
            "hostedUrl": "https://jobs.lever.co/acme/ghi789",
            "createdAt": 1787247868191,
        }
    ]
    jobs = get_scraper("lever", "acme", "Acme").parse(raw, SCRAPED_AT)
    assert jobs[0].location == "Berlin, DE"


def test_lever_location_country_recognizes_usa_short_form():
    # Real posting, lever:freedompay (captured live 2026-08-26, review round 2): the location
    # is the colloquial "USA" short form, not the full "United States" name the code maps to —
    # a bare name check misses it and appends a redundant ", US".
    raw = [
        {
            "id": "usa1",
            "text": "Remote Role",
            "categories": {
                "location": "Select USA Remote Locations",
                "allLocations": ["Select USA Remote Locations"],
            },
            "country": "US",
            "workplaceType": "remote",
            "hostedUrl": "https://jobs.lever.co/freedompay/usa1",
            "createdAt": 1787247868191,
        }
    ]
    jobs = get_scraper("lever", "freedompay", "FreedomPay").parse(raw, SCRAPED_AT)
    assert jobs[0].location == "Select USA Remote Locations"


def test_ashby_location_keeps_every_place_the_record_names():
    """The served location IS the filter substrate — `geo.where()` is a raw substring LIKE
    (ADR-0024) — so a place absent from this string is unfilterable however well the record
    knows it. Measured 2026-08-25 over 884 live Boards / 16,138 Jobs: 69.55% shipped no country
    at all, 79.43% omitted some populated component of their own address, and 17.5% had a
    `secondaryLocations[]` nothing ever opened.
    """
    from headstart.scrapers.ashby import _location

    # a real kafene posting: served as "Panama City", losing its country AND a Guatemala
    # secondary, so neither a Panama nor a Guatemala filter could find it
    assert (
        _location(
            {
                "location": "Panama City",
                "address": {"postalAddress": {"addressCountry": "Panama"}},
                "secondaryLocations": [
                    {
                        "location": "Guatemala City",
                        "address": {
                            "postalAddress": {
                                "addressRegion": "Guatemala ",
                                "addressCountry": "Guatemala",
                                "addressLocality": "Guatemala City",
                            }
                        },
                    }
                ],
            }
        )
        == "Panama City, Guatemala City"
    )

    # the India shape — the country must be appended, since "india" is not inside "Bengaluru"
    assert (
        _location(
            {
                "location": "Bengaluru",
                "address": {"postalAddress": {"addressCountry": "India"}},
            }
        )
        == "Bengaluru, India"
    )
    assert _location({}) is None


def test_ashby_location_is_additive_and_never_repeats_a_place():
    """Components are appended only when they aren't already named, as a whole word, in what
    has been kept so far.

    That whole-word test is the right one *because* the filter is a substring match: "Panama"
    needs no separate entry beside "Panama City", but "India" does beside "Bengaluru". It also
    keeps the employer's own wording, which is the better display text.
    """
    from headstart.scrapers.ashby import _location

    assert (
        _location(
            {
                "location": "SpotDraft HQ, Bengaluru",
                "address": {
                    "postalAddress": {
                        "addressLocality": "Bengaluru",
                        "addressCountry": "India",
                    }
                },
            }
        )
        == "SpotDraft HQ, Bengaluru, India"
    )
    # already complete — nothing to add, and no duplication
    assert (
        _location(
            {
                "location": "Berlin, Germany",
                "address": {
                    "postalAddress": {
                        "addressLocality": "Berlin",
                        "addressCountry": "Germany",
                    }
                },
            }
        )
        == "Berlin, Germany"
    )
    # a tenant shipping "Guatemala " with a trailing space must not leak it into the join
    assert (
        _location({"address": {"postalAddress": {"addressCountry": "Guatemala "}}})
        == "Guatemala"
    )
    # a bare substring check would wrongly read "CA" as already present inside "Vacaville"
    # (code review round 1) and drop the state — the whole-word test must still add it
    assert (
        _location(
            {
                "location": "Vacaville",
                "address": {
                    "postalAddress": {
                        "addressLocality": "Vacaville",
                        "addressRegion": "CA",
                        "addressCountry": "United States",
                    }
                },
            }
        )
        == "Vacaville, CA, United States"
    )


@pytest.mark.parametrize(
    ("workplace", "is_remote", "expected"),
    [
        ("Remote", True, True),
        ("OnSite", False, False),
        # the defect: ashby's own `isRemote` is `workplaceType != "OnSite"`, so Hybrid arrives
        # as True. 4,183 of 16,138 live Jobs (25.9%) were served remote=True on this shape.
        ("Hybrid", True, None),
        (None, None, None),
        # no workplaceType at all: fall back to the flag rather than invent an answer
        (None, True, True),
    ],
)
def test_ashby_hybrid_is_not_remote(workplace, is_remote, expected):
    """`Job.remote` is tri-state and hybrid is what None is for — it is neither remote nor
    on-site, and asserting either is a guess. `workday._remote_from` already answers it this
    way; this brings ashby into line rather than inventing a convention."""
    from headstart.scrapers.ashby import _remote

    assert _remote({"workplaceType": workplace, "isRemote": is_remote}) is expected


def test_ashby_parse_skips_unlisted():
    raw = _load("ashby_ramp.json")
    jobs = get_scraper("ashby", "ramp", "Ramp").parse(raw, SCRAPED_AT)
    expected = sum(1 for j in raw["jobs"] if j.get("isListed", True))
    assert len(jobs) == expected
    j = jobs[0]
    assert j.id == "ashby:ramp:34413f8d-26bf-4bbc-8ade-eb309a0e2245"
    assert j.title == "Security Engineer, Cloud"  # leading space stripped
    assert j.department == "Engineering"
    # `workplaceType: "Hybrid"` with `isRemote: true` — this assertion used to read `is True`,
    # which encoded the defect: ashby's `isRemote` is exactly `workplaceType != "OnSite"`, so it
    # calls Hybrid remote. Tri-state None is the honest answer and matches `workday._remote_from`.
    assert j.remote is None
    # a two-city posting: the headline names only the HQ, the record also carries a Miami
    # secondary and the country, none of which used to reach the served row
    assert j.location == (
        "New York, NY (HQ), New York City, USA, Miami, FL, Florida, "
        "Remote (US), United States, Remote (Canada)"
    )
    assert j.employment_type == "FullTime"
    assert j.description and "</" not in j.description  # populated, HTML-stripped
    # this fixture predates compensationTiers (only compensationTierSummary is present) — real,
    # current ashby responses always carry the key (empty or populated); _salary() correctly
    # returns None rather than falling back to the unstructured summary string (code review,
    # PR #240 — see test_ashby_salary_from_structured_compensation_tier below for the real shape).
    assert j.salary is None
    # the board URL must request compensation or the block is absent
    assert "includeCompensation=true" in get_scraper("ashby", "ramp", "Ramp").url()


@pytest.mark.parametrize(
    ("compensation", "expected"),
    [
        (
            {
                "compensationTiers": [
                    {
                        "components": [
                            {
                                "compensationType": "Salary",
                                "interval": "1 YEAR",
                                "currencyCode": "USD",
                                "minValue": 80000,
                                "maxValue": 100000,
                            }
                        ]
                    }
                ]
            },
            "80000-100000 USD 1 YEAR",
        ),
        (
            {
                "compensationTiers": [
                    {
                        "components": [
                            {
                                "compensationType": "Salary",
                                "interval": "1 HOUR",
                                "currencyCode": "USD",
                                "minValue": 25,
                                "maxValue": 30,
                            }
                        ]
                    }
                ]
            },
            "25-30 USD 1 HOUR",
        ),
        (
            {
                "compensationTiers": [
                    {
                        "components": [
                            {
                                "compensationType": "Salary",
                                "interval": "1 YEAR",
                                "currencyCode": "USD",
                                "minValue": 0,
                                "maxValue": 250000,
                            }
                        ]
                    }
                ]
            },
            "0-250000 USD 1 YEAR",
        ),
        (
            {
                "compensationTiers": [
                    {
                        "components": [
                            {
                                "compensationType": "Salary",
                                "interval": "1 YEAR",
                                "currencyCode": "USD",
                                "minValue": 0,
                                "maxValue": None,
                            }
                        ]
                    }
                ]
            },
            "0 USD 1 YEAR",
        ),
        (
            {
                "compensationTiers": [
                    {
                        "components": [
                            {
                                "compensationType": "EquityPercentage",
                                "interval": "NONE",
                                "currencyCode": None,
                                "minValue": None,
                                "maxValue": None,
                            }
                        ]
                    }
                ]
            },
            None,
        ),
        (
            {
                "compensationTiers": [
                    {
                        "components": [
                            {
                                "compensationType": "Salary",
                                "interval": "1 TIME",
                                "currencyCode": "EUR",
                                "minValue": 650,
                                "maxValue": 700,
                            }
                        ]
                    }
                ]
            },
            None,
        ),
        ({"compensationTiers": []}, None),
        (None, None),
    ],
)
def test_ashby_salary_from_structured_compensation_tier(compensation, expected):
    """Real, direct API inspection (2026-08-22, code review PR #240): ashby's compensation object
    carries a structured Salary-typed component (min/max/currency/interval) one level deeper than
    the compensationTierSummary string this scraper used to extract — 34% of jobs have it
    populated, close to 4x teamtailor's field-presence rate. A "1 TIME" interval (a one-off
    payment, not a recurring salary — real: "Compensation per finished project") is deliberately
    excluded rather than guessed at as annual. The two ``minValue=0`` cases are a real, live-
    reconfirmed Standards-review catch (Ramp's own board): a truthy check on ``lo``/``hi`` drops a
    genuine 0 and silently corrupts the disclosure, so both must format the 0 rather than treat it
    as absent."""
    from headstart.scrapers.ashby import _salary

    assert _salary(compensation) == expected


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


def test_keka_location_strips_a_dirty_city_field():
    # Real bug, location-field audit 2026-08-24: `city` carries a trailing space on some
    # tenants' data while the sibling `name` field for the same location is clean
    # ({'name': 'Ahmedabad Center', 'city': 'Ahmedabad Center '}) — `city` wins the `or` chain,
    # so the padding reached the served field with nothing downstream to strip it.
    raw = [
        {
            "id": 1,
            "title": "T",
            "jobLocations": [{"name": "Ahmedabad Center", "city": "Ahmedabad Center "}],
        }
    ]
    jobs = get_scraper("keka", "x", "X").parse(raw, SCRAPED_AT)
    assert jobs[0].location == "Ahmedabad Center"


def test_keka_location_empty_list_stays_none():
    raw = [{"id": 1, "title": "T", "jobLocations": []}]
    jobs = get_scraper("keka", "x", "X").parse(raw, SCRAPED_AT)
    assert jobs[0].location is None


def test_keka_salary_no_scientific_notation_for_large_amounts():
    # Real bug, salary-extraction pass 2026-08-22: Python's `:g` format (the previous
    # implementation) switches to scientific notation ("1e+06") for values >= 1,000,000 — neither
    # headstart.salary's _RANGE regex nor _num() can parse an exponent, so every genuine keka
    # figure at or above ₹1,000,000 was silently discarded. 27% of a 300-job sample of rejected
    # Job.salary values showed this shape, across 19 distinct companies. Fixing it recovered
    # ~1,550 jobs on re-measurement (Tier 1 coverage 15.8% -> 27.8% of the full sampled corpus).
    from headstart.scrapers.keka import _salary

    assert _salary({"minimum": 500000.0, "maximum": 1000000.0, "currency": "INR"}) == (
        "500000-1000000 INR"
    )
    assert _salary({"minimum": 1000000.0, "maximum": 1800000.0, "currency": "INR"}) == (
        "1000000-1800000 INR"
    )
    assert _salary({"minimum": 4000000.0, "maximum": 5000000.0, "currency": "INR"}) == (
        "4000000-5000000 INR"
    )
    # A bare single value at or above the same threshold (the `lo or hi` ceiling-only branch).
    assert _salary({"minimum": 0.0, "maximum": 2000000.0, "currency": "INR"}) == (
        "2000000 INR"
    )
    # Small LPA-shorthand decimals (well below the threshold) stay exactly as before.
    assert _salary({"minimum": 2.5, "maximum": 3.5, "currency": "INR"}) == "2.5-3.5 INR"
    assert _salary({"minimum": 25000.0, "maximum": 30000.0, "currency": "INR"}) == (
        "25000-30000 INR"
    )


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
    text = scraper._extract_detail(_Resp())["description"]
    assert "Build things" in text
    assert "5+ years of experience" in text  # qualifications must ride along
    assert "Perks" in text
    assert "boilerplate" not in text  # companyDescription deliberately skipped


def test_smartrecruiters_compensation_custom_field_appended_to_description():
    # Real, found via direct API inspection: some companies configure a free-text custom field
    # for pay info the standard jobAd sections never carry (salary-extraction pass, 2026-08-22).
    jobs = get_scraper("smartrecruiters", "acme", "Acme").parse(
        {
            "content": [
                {
                    "id": "1",
                    "name": "Marketing Specialist",
                    "location": {},
                    "customField": [
                        {"fieldLabel": "Country/Region", "valueLabel": "New Zealand"},
                        {
                            "fieldLabel": "Enter salary or hourly pay range (+ pay grade, if known)",
                            "valueLabel": "Grade 15. $100K - $115K",
                        },
                    ],
                }
            ]
        },
        SCRAPED_AT,
    )
    j = jobs[0]
    assert "Grade 15. $100K - $115K" in j.description
    assert (
        "New Zealand" not in j.description
    )  # unrelated custom fields are not appended
    from headstart.salary import extract

    assert extract(None, j.description, "smartrecruiters") is not None


def test_smartrecruiters_compensation_custom_field_absent_leaves_description_unchanged():
    jobs = get_scraper("smartrecruiters", "acme", "Acme").parse(
        {
            "content": [
                {
                    "id": "1",
                    "name": "Some Role",
                    "location": {},
                    "customField": [
                        {"fieldLabel": "Country/Region", "valueLabel": "New Zealand"},
                    ],
                    "_detail": {"description": "<p>Build things</p>"},
                }
            ]
        },
        SCRAPED_AT,
    )
    assert jobs[0].description == "Build things"


@pytest.mark.parametrize(
    ("compensation", "expected"),
    [
        (
            {"min": 70000, "max": 85000, "currency": "EUR", "period": "YEARLY"},
            "70000-85000 EUR 1 YEAR",
        ),
        (
            {"min": 3500, "max": 4000, "currency": "CNY", "period": "MONTHLY"},
            "3500-4000 CNY 1 MONTH",
        ),
        (
            {"min": 25, "max": 28, "currency": "NZD", "period": "WEEKLY"},
            "25-28 NZD 1 WEEK",
        ),
        (
            {"min": 160000, "max": 185000, "currency": "USD", "period": "YEARLY"},
            "160000-185000 USD 1 YEAR",
        ),
        # junk values are real (direct API inspection, 2026-08-25) and must be checked with
        # `is not None`, not truthiness — a truthy check on a real 0 would misread it as absent
        # rather than format and correctly decline it downstream in salary.py's `_bounded`.
        ({"min": 1, "max": 1, "currency": "GTQ"}, "1-1 GTQ"),
        # min/max both absent (only currency/period stated) is genuinely no figure to report.
        ({"currency": "USD", "period": "YEARLY"}, None),
        ({}, None),
        (None, None),
        # max-only ("up to $X") is declined outright, not passed through as a bare single value —
        # that path always reads as floor-only, which would silently misreport a stated ceiling as
        # an unbounded floor. `{"max": 0, ...}` used to format as "0 GBP" (still correctly declined
        # downstream by `_bounded`, since 0 is below every currency's floor) but a real nonzero
        # ceiling like the live-verified `{"max": 12150, "currency": "MXN", "period": "MONTHLY"}`
        # (2026-08-26, 1/19 populated compensation blocks across 60 boards/348 postings) clears
        # `_bounded`'s USD-fallback plausibility bounds cleanly and would ship as a confident wrong
        # number instead — so both decline the same way now.
        ({"max": 0, "currency": "GBP"}, None),
        ({"max": 12150, "currency": "MXN", "period": "MONTHLY"}, None),
        # min-only ("$X+, no stated ceiling") is a genuine floor-only disclosure, not the same
        # ambiguity as max-only — unaffected by the max-only decline above and still passed
        # through as a bare single value, which correctly reads as floor-only.
        ({"min": 65000, "currency": "USD", "period": "YEARLY"}, "65000 USD 1 YEAR"),
    ],
)
def test_smartrecruiters_salary_from_native_compensation_block(compensation, expected):
    """Real, direct API inspection (2026-08-25,
    experiment/location-audit-2026-08-25/smartrecruiters.md): the posting-detail response carries
    a native `compensation.{min,max,currency,period}` block on 10.48% of postings, previously
    never read. The adverb period SmartRecruiters itself sends ("YEARLY", "MONTHLY", ...) is
    mapped to the singular bare word ("1 YEAR", "1 MONTH", ...) salary.py's
    `_field_range_currency_interval` recognizes — the raw adverb does not match its bare-word
    regex and would silently default to the annual multiplier instead of annualizing."""
    from headstart.scrapers.smartrecruiters import _salary

    assert _salary(compensation) == expected


def test_smartrecruiters_extract_detail_reads_description_and_compensation_from_one_response():
    """The compensation fix must cost zero extra requests: both fields come off the SAME
    posting-detail response the scraper already fetches for the description alone."""
    from headstart.scrapers.smartrecruiters import SmartRecruitersScraper

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {
                "jobAd": {
                    "sections": {"jobDescription": {"text": "<p>Build things</p>"}}
                },
                "compensation": {
                    "min": 70000,
                    "max": 85000,
                    "currency": "EUR",
                    "period": "YEARLY",
                },
            }

    detail = SmartRecruitersScraper._extract_detail(_Resp())
    assert detail == {
        "description": "<p>Build things</p>",
        "compensation": {
            "min": 70000,
            "max": 85000,
            "currency": "EUR",
            "period": "YEARLY",
        },
    }


def test_smartrecruiters_extract_detail_missing_compensation_is_none():
    from headstart.scrapers.smartrecruiters import SmartRecruitersScraper

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"jobAd": {"sections": {"jobDescription": {"text": "<p>Role</p>"}}}}

    detail = SmartRecruitersScraper._extract_detail(_Resp())
    assert detail["compensation"] is None


def test_smartrecruiters_parse_maps_native_compensation_into_job_salary():
    """End-to-end: a posting whose detail carries the native `compensation` block gets a
    populated `Job.salary`, formatted so `headstart.salary.extract` parses it as Tier 1."""
    jobs = get_scraper("smartrecruiters", "acme", "Acme").parse(
        {
            "content": [
                {
                    "id": "1",
                    "name": "Senior Analytics Engineer",
                    "location": {},
                    "_detail": {
                        "description": "<p>Build things</p>",
                        "compensation": {
                            "min": 70000,
                            "max": 85000,
                            "currency": "EUR",
                            "period": "YEARLY",
                        },
                    },
                }
            ]
        },
        SCRAPED_AT,
    )
    j = jobs[0]
    assert j.salary == "70000-85000 EUR 1 YEAR"

    from headstart.salary import SalarySpan, extract

    assert extract(j.salary, j.description, "smartrecruiters") == SalarySpan(
        70000, 85000, "EUR", "field"
    )


def test_smartrecruiters_parse_no_compensation_leaves_salary_none():
    jobs = get_scraper("smartrecruiters", "acme", "Acme").parse(
        {
            "content": [
                {
                    "id": "1",
                    "name": "Some Role",
                    "location": {},
                    "_detail": {"description": "<p>Build things</p>"},
                }
            ]
        },
        SCRAPED_AT,
    )
    assert jobs[0].salary is None


def test_smartrecruiters_location_collapses_blank_region_comma_segment():
    """Cosmetic-only fix (experiment/location-audit-2026-08-25/smartrecruiters.md §3d):
    `fullLocation` carries an empty comma segment on 10.54% of postings when `location.region`
    is blank — the same defect class already fixed on darwinbox (identical comma-split, strip,
    drop-empties, rejoin) and, differently, on keka (per-field strip only — keka's location
    arrives as discrete city/state/country fields, not one joined string to split).
    `fullLocation` itself is a 100.00%-populated ceiling and stays the primary source; only its
    formatting is cleaned up."""
    jobs = get_scraper("smartrecruiters", "acme", "Acme").parse(
        {
            "content": [
                {
                    "id": "1",
                    "name": "Some Role",
                    "location": {
                        "city": "Singapore",
                        "country": "sg",
                        "fullLocation": "Singapore, , Singapore",
                    },
                }
            ]
        },
        SCRAPED_AT,
    )
    assert jobs[0].location == "Singapore, Singapore"


def test_smartrecruiters_location_with_region_is_unaffected():
    jobs = get_scraper("smartrecruiters", "acme", "Acme").parse(
        {
            "content": [
                {
                    "id": "1",
                    "name": "Some Role",
                    "location": {
                        "city": "Irving",
                        "region": "TX",
                        "country": "us",
                        "fullLocation": "Irving, TX, United States",
                    },
                }
            ]
        },
        SCRAPED_AT,
    )
    assert jobs[0].location == "Irving, TX, United States"


def _sr_offline(monkeypatch, scraper, payload):
    """Run `fetch_raw` against `payload` with the detail pass stubbed out.

    ``payload["content"]`` is the *whole* board; the stub slices it by `offset` the way the real
    listing does, so paging past page 1 reads further postings and then runs out.
    """
    from headstart.scrapers import smartrecruiters as sr

    board = payload.get("content") or []

    def _get(url=None):
        offset = int(url.split("offset=")[1]) if url and "offset=" in url else 0
        page = board[offset : offset + sr._PAGE_SIZE]
        return json.dumps({**payload, "offset": offset, "content": page})

    monkeypatch.setattr(scraper, "_get", _get)
    monkeypatch.setattr(scraper, "fan_out", lambda items, fn, **kw: [None] * len(items))
    monkeypatch.setattr(
        scraper, "fan_out_async", lambda items, fn, **kw: [None] * len(items)
    )
    return scraper.fetch_raw()


def test_smartrecruiters_marks_truncation_when_the_board_outruns_one_page(monkeypatch):
    """`totalFound` above the postings returned means the list is knowingly short (ADR-0053).

    Unmarked, `index sync` reads every posting behind the page as a delisting. Measured live
    2026-08-20: `dominos` answers `totalFound=24556` behind a 100-posting page, and `offset=100`
    returns 100 further distinct ids — so the rest is reachable, and its absence is not a
    delisting.
    """
    scraper = get_scraper("smartrecruiters", "dominos", "Dominos")
    _sr_offline(
        monkeypatch,
        scraper,
        {
            "offset": 0,
            "limit": 100,
            "totalFound": 24556,
            "content": [{"id": str(n)} for n in range(100)],
        },
    )

    assert scraper.truncated is not None
    assert "24556" in scraper.truncated


def test_smartrecruiters_complete_board_is_not_marked_truncated(monkeypatch):
    """A Board that fits in one page is authoritative — marking it would strip it from the
    eviction scope for nothing, and its real delistings would then never be pruned."""
    scraper = get_scraper("smartrecruiters", "acme", "Acme")
    _sr_offline(
        monkeypatch,
        scraper,
        {
            "offset": 0,
            "limit": 100,
            "totalFound": 3,
            "content": [{"id": str(n)} for n in range(3)],
        },
    )

    assert scraper.truncated is None


def test_smartrecruiters_a_board_of_exactly_one_page_is_not_truncated(monkeypatch):
    """`totalFound == len(content) == limit` separates the two candidate signals.

    The rejected `len(content) == limit` heuristic would mark this board and strip it from the
    eviction scope forever; `totalFound` is exact, so it does not. No board in the liveness ledger
    sits at exactly 100 today — this pins the distinction rather than a live shape.
    """
    scraper = get_scraper("smartrecruiters", "exactly", "Exactly")
    _sr_offline(
        monkeypatch,
        scraper,
        {
            "offset": 0,
            "limit": 100,
            "totalFound": 100,
            "content": [{"id": str(n)} for n in range(100)],
        },
    )

    assert scraper.truncated is None


def test_smartrecruiters_page_cap_is_the_decided_number():
    """ADR-0076 decided 5,000 postings, and every other test here reads the constant.

    Without this the cap could be re-tuned to anything and the suite would stay green, which is
    how a number chosen from a measurement quietly becomes a number chosen to feel safe.
    """
    from headstart.scrapers import smartrecruiters as sr

    assert (sr._PAGE_SIZE, sr._MAX_PAGES) == (100, 50)


@pytest.mark.skip(
    reason="cap enforcement commented out for the initial uncapped rollout, see #227"
)
def test_smartrecruiters_marks_its_page_cap(monkeypatch):
    """Paging stops at `_MAX_PAGES` and says so, so the unread tail is still not a delisting.

    The cap is sized by cost, not by an assumption that the tail is junk (ADR-0076): measured
    live 2026-08-20, tech density does *not* fall off down the list — 14.1% at offset 500 across
    40 random boards over 500 postings, and `EndeavorITSolution` runs 62% tech at 8,478 deep.
    """
    from headstart.scrapers import smartrecruiters as sr

    scraper = get_scraper("smartrecruiters", "dominos", "Dominos")
    raw = _sr_offline(
        monkeypatch,
        scraper,
        {
            "offset": 0,
            "limit": sr._PAGE_SIZE,
            "totalFound": 24561,
            # more board than the cap can read, so stopping is the scraper's choice
            "content": [
                {"id": str(n)} for n in range(sr._PAGE_SIZE * (sr._MAX_PAGES + 1))
            ],
        },
    )

    read = raw["content"]
    assert len(read) == sr._PAGE_SIZE * sr._MAX_PAGES
    # distinct ids: the offsets really advanced rather than re-reading page 1
    assert len({p["id"] for p in read}) == sr._PAGE_SIZE * sr._MAX_PAGES
    assert scraper.truncated and f"{sr._MAX_PAGES}-page cap" in scraper.truncated
    assert "24561" in scraper.truncated


def test_smartrecruiters_a_short_last_page_is_not_blamed_on_the_cap(monkeypatch):
    """Truncated, but *not* by the cap: the last page came back short.

    `page` alone reaches `_MAX_PAGES` either way, and `totalFound` is read off page 1 — so a board
    that loses a posting mid-read lands exactly here. The reason string feeds the shard report
    (ADR-0045), and a reason that names a cap which never fired is the false premise CLAUDE.md's
    review rule exists to catch.
    """
    from headstart.scrapers import smartrecruiters as sr

    scraper = get_scraper("smartrecruiters", "shrinking", "Shrinking")
    board = sr._PAGE_SIZE * sr._MAX_PAGES - 1  # one short of a full final page
    _sr_offline(
        monkeypatch,
        scraper,
        {
            "offset": 0,
            "limit": sr._PAGE_SIZE,
            "totalFound": board + 1,  # page 1 counted the posting that has since closed
            "content": [{"id": str(n)} for n in range(board)],
        },
    )

    assert scraper.truncated and "page cap" not in scraper.truncated


def test_smartrecruiters_a_board_shorter_than_the_cap_is_read_whole(monkeypatch):
    """Paging stops on a short page, and a board it fully read stays evictable (ADR-0053)."""
    from headstart.scrapers import smartrecruiters as sr

    scraper = get_scraper("smartrecruiters", "midsize", "Midsize")
    raw = _sr_offline(
        monkeypatch,
        scraper,
        {
            "offset": 0,
            "limit": sr._PAGE_SIZE,
            "totalFound": 250,
            "content": [{"id": str(n)} for n in range(250)],
        },
    )

    assert len(raw["content"]) == 250  # page 1 alone would have read 100
    assert scraper.truncated is None


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
    monkeypatch.setenv(
        "HEADSTART_ASYNC_FANOUT", "0"
    )  # keep the detail pass on the sync path

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


def test_ripplehire_fetch_raw_attaches_full_detail_record(monkeypatch):
    """`fetch_raw` already fetches `jobVO` per job for the description — `parse` needs the rest
    of that same record (department/posted_at/employment_type/salary), so `fetch_raw` must keep
    it rather than reading only `jobDesc` back out of it and discarding the rest."""
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "0")

    class _Resp:
        def __init__(self, url="", payload=None):
            self.url = url
            self._payload = payload
            self.status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    def _fetch(method, url, **kwargs):
        if url.endswith("/candidate/careers"):
            return _Resp(url="https://x.ripplehire.com/candidate/?token=TOK123")
        if "candidatejobsearch" in url:
            return _Resp(
                payload={
                    "jobVoList": [{"jobSeq": 1, "jobTitle": "SRE", "jobDesc": None}],
                    "totalJobCount": 1,
                }
            )
        return _Resp(
            payload={
                "jobVO": {
                    "jobDesc": "<p>desc</p>",
                    "bussinessUnit": "Technology",
                    "jobPostingDate": "23-Jun-2020",
                }
            }
        )

    import headstart.scrapers.ripplehire as rh

    monkeypatch.setattr(rh.http, "fetch", _fetch)
    raw = get_scraper("ripplehire", "x", "X").fetch_raw()
    assert raw[0]["_detail"]["bussinessUnit"] == "Technology"
    assert raw[0]["_detail"]["jobPostingDate"] == "23-Jun-2020"


def test_ripplehire_location_joins_city_and_country():
    """`locations` is the city field (2,613 distinct values fleet-wide); `jobLocation` is a
    34-value country picker. The old `jobLocation or locations` served the coarser value and
    silently dropped the city on every job carrying both — 33.21% of the corpus, live-verified
    2026-08-25 (experiment/location-audit-2026-08-25/ripplehire.md). The fix joins both, so a
    `geo.where(city)` filter can still match."""
    from headstart.scrapers import ripplehire as rh

    jobs = rh.RippleHireScraper("acme").parse(
        [
            # both present, disjoint -> join, city first
            {
                "jobSeq": 1,
                "jobTitle": "A",
                "locations": "Mumbai",
                "jobLocation": "India",
            },
            # jobLocation already a substring of the composed string -> no duplicate
            {
                "jobSeq": 2,
                "jobTitle": "B",
                "locations": "Mumbai, India",
                "jobLocation": "India",
            },
            # locations absent -> jobLocation alone
            {"jobSeq": 3, "jobTitle": "C", "locations": None, "jobLocation": "USA"},
            # multi-city locations, untrimmed parts -> each stripped, country appended
            {
                "jobSeq": 4,
                "jobTitle": "D",
                "locations": "Chennai , Bengaluru ,Pune",
                "jobLocation": "India",
            },
            # both absent -> None
            {"jobSeq": 5, "jobTitle": "E", "locations": None, "jobLocation": None},
        ],
        SCRAPED_AT,
    )
    assert jobs[0].location == "Mumbai, India"
    assert jobs[1].location == "Mumbai, India"  # not "Mumbai, India, India"
    assert jobs[2].location == "USA"
    assert jobs[3].location == "Chennai, Bengaluru, Pune, India"
    assert jobs[4].location is None


def test_ripplehire_maps_department_posted_at_employment_type_salary_from_detail():
    """The search list's `bussinessUnit`/`jobPostingDate`/`jobType`/`compensationRange` are
    always empty (live-verified across all 18,659 jobs on all 55 boards, 2026-08-25) — the real
    values live in the `jobVO` detail record `fetch_raw` already downloads for every job's
    description. `parse` must read the four fields from there, not the always-empty list keys."""
    from headstart.scrapers import ripplehire as rh

    jobs = rh.RippleHireScraper("acme").parse(
        [
            {
                "jobSeq": 1,
                "jobTitle": "A",
                # list-side fields: always empty in production, present here only to prove
                # they are NOT what wins
                "bussinessUnit": None,
                "jobPostingDate": None,
                "jobType": None,
                "compensationRange": None,
                "_detail": {
                    "bussinessUnit": "Technology",
                    "jobPostingDate": "23-Jun-2020",
                    "jobType": "R",  # a requisition-type code, not an employment type
                    "jobTypeCustom3": "Full time",
                    "compensationRange": "Compensation range: $ 46,417.00 to 77,864.00 per year",
                },
            },
            # no detail record at all (e.g. the per-job fetch failed) -> falls back, stays None
            {"jobSeq": 2, "jobTitle": "B"},
            # no detail record, but list-side keys present -> the safety net must actually
            # recover them, not just fall through to None (department/posted_at/salary only;
            # employment_type has no list-side fallback, see parse()'s own comment)
            {
                "jobSeq": 3,
                "jobTitle": "C",
                "bussinessUnit": "Finance",
                "jobPostingDate": "01-Jan-2021",
                "compensationRange": "10-15 LPA",
            },
        ],
        SCRAPED_AT,
    )
    j = jobs[0]
    assert j.department == "Technology"
    assert j.posted_at == "23-Jun-2020"
    assert j.employment_type == "Full time"  # jobTypeCustom3, not the coded jobType "R"
    assert j.salary == "Compensation range: $ 46,417.00 to 77,864.00 per year"

    j2 = jobs[1]
    assert j2.department is None
    assert j2.posted_at is None
    assert j2.employment_type is None
    assert j2.salary is None

    j3 = jobs[2]
    assert j3.department == "Finance"
    assert j3.posted_at == "01-Jan-2021"
    assert j3.salary == "10-15 LPA"


def test_ripplehire_prefers_publish_details_iso_timestamp_for_posted_at():
    """`publishDetails.CAREER_SITE` is a real ISO-8601 timestamp for the same posting
    `jobPostingDate` gives as `"23-Jun-2020"` — prefer it when present (Job.posted_at's own
    contract: "ISO-8601 if the source provides it"), falling back to the non-ISO date otherwise."""
    from headstart.scrapers import ripplehire as rh

    jobs = rh.RippleHireScraper("acme").parse(
        [
            {
                "jobSeq": 1,
                "jobTitle": "A",
                "_detail": {
                    "jobPostingDate": "23-Jun-2020",
                    "publishDetails": {"CAREER_SITE": "2026-07-16T13:53:16Z"},
                },
            },
        ],
        SCRAPED_AT,
    )
    assert jobs[0].posted_at == "2026-07-16T13:53:16Z"


def test_ripplehire_does_not_carry_month_valued_exp_fields_across_the_unit_trap():
    """`jobMinExp`/`jobMaxExp` are YEARS on the search list but MONTHS on the `jobVO` detail
    record for the SAME job — confirmed live 2026-08-25 across 160 paired records, exactly x12
    (e.g. "4 - 6 Years" list-side pairs with jobMinExp=48/jobMaxExp=72 in the detail record).
    `jobReqExp` is identical text on both surfaces and is the only experience field this scraper
    reads; this pins that reading the (now-consulted) detail record for other fields does not
    let the month-valued pair leak into `experience`."""
    from headstart.scrapers import ripplehire as rh

    jobs = rh.RippleHireScraper("acme").parse(
        [
            {
                "jobSeq": 1,
                "jobTitle": "A",
                "jobReqExp": "4 - 6 Years",
                "jobMinExp": 4,  # years, list-side
                "jobMaxExp": 6,
                "_detail": {
                    "jobReqExp": "4 - 6 Years",
                    "jobMinExp": 48,  # months, detail-side — same job, x12
                    "jobMaxExp": 72,
                },
            },
        ],
        SCRAPED_AT,
    )
    assert jobs[0].experience == "4 - 6 Years"


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
                    "jobReqId": "JR00258",
                }
            }

    from headstart.scrapers.workday import WorkdayScraper

    got = WorkdayScraper._extract_detail(_Response())
    assert got["location"] == "London"
    assert got["additionalLocations"] == ["Dublin"]
    assert got["remoteType"] == "Remote Available"


def test_workday_extract_detail_carries_the_country_field():
    """`jobPostingInfo.country.descriptor` is populated on 99.06% of detail records
    (experiment/location-audit-2026-08-25/workday.md) and `_extract_detail` never copied it —
    the fetch-side half of the country fix, same shape as the location-fields test above."""

    class _Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "jobPostingInfo": {
                    "jobDescription": "<p>hi</p>",
                    "location": "Ottawa, ON",
                    "country": {"descriptor": "Canada", "id": "abc123"},
                }
            }

    from headstart.scrapers.workday import WorkdayScraper

    got = WorkdayScraper._extract_detail(_Response())
    assert got["country"] == "Canada"


def test_workday_appends_the_country_the_listing_never_named():
    """The defect: 81.45% of served locations never name the country, even though
    `jobPostingInfo.country.descriptor` sits in the same already-fetched detail response
    `_location_from` already reads for the rollup repair (measured 2026-08-25,
    experiment/location-audit-2026-08-25/workday.md). A real listing location is the common
    case — `_location_from` used to return it before ever consulting the detail's country."""
    raw = [
        {
            "title": "A",
            "locationsText": "Ottawa, ON",
            "bulletFields": ["R1"],
            "_detail": {"country": "Canada"},
        },
        {
            "title": "B",
            "locationsText": "Fairfield, IA",
            "bulletFields": ["R2"],
            "_detail": {"country": "United States of America"},
        },
    ]
    jobs = get_scraper(
        "workday", "https://acme.wd1.myworkdayjobs.com/careers", "Acme"
    ).parse(raw, SCRAPED_AT)
    assert jobs[0].location == "Ottawa, ON; Canada"
    assert jobs[1].location == "Fairfield, IA; United States of America"


def test_workday_does_not_duplicate_a_country_already_named_in_the_location():
    """Additive, not replacing: a location that already names the country (case-insensitive
    substring) must not gain a duplicate — the served filter is a raw substring LIKE
    (ADR-0024)."""
    raw = [
        {
            "title": "A",
            "locationsText": "Cork, Ireland",
            "bulletFields": ["R1"],
            "_detail": {"country": "IRELAND"},
        }
    ]
    jobs = get_scraper(
        "workday", "https://acme.wd1.myworkdayjobs.com/careers", "Acme"
    ).parse(raw, SCRAPED_AT)
    assert jobs[0].location == "Cork, Ireland"


def test_workday_country_composes_with_the_rollup_repair():
    """The country append is a final step over whatever `_location_from` already produced —
    including the detail-repaired rollup case — not a special case of the plain-listing path."""
    raw = [
        {
            "title": "A",
            "locationsText": "5 Locations",
            "bulletFields": ["R1"],
            "_detail": {
                "location": "London",
                "additionalLocations": ["Dublin", "Warsaw", "Paris", "Berlin"],
                "country": "United Kingdom",
            },
        }
    ]
    jobs = get_scraper(
        "workday", "https://acme.wd1.myworkdayjobs.com/careers", "Acme"
    ).parse(raw, SCRAPED_AT)
    assert jobs[0].location == "London; Dublin; Warsaw; Paris; Berlin; United Kingdom"


def test_workday_country_absent_leaves_location_unchanged():
    """No detail country (0.94% of records, or a failed detail fetch) must not error or alter
    the location — additive only, never a required field."""
    raw = [
        {
            "title": "A",
            "locationsText": "Austin, TX",
            "bulletFields": ["R1"],
            "_detail": {},
        }
    ]
    jobs = get_scraper(
        "workday", "https://acme.wd1.myworkdayjobs.com/careers", "Acme"
    ).parse(raw, SCRAPED_AT)
    assert jobs[0].location == "Austin, TX"


def test_workday_country_does_not_taint_an_unrepaired_rollup():
    """A rollup that survives (detail present but with no `location`/`additionalLocations` to
    repair it) must not gain a country either — `parse()`'s remote-detection guard keys on
    `_is_rollup` matching the exact "N Locations" string, and appending "; Canada" to it would
    break that match, silently flipping `Job.remote` from an honest `None` to an incorrect
    `False` (the "asserts on-site when we can't tell" failure the module's own docstring warns
    against)."""
    raw = [
        {
            "title": "A",
            "locationsText": "2 Locations",
            "bulletFields": ["R1"],
            "_detail": {"country": "Canada"},
        }
    ]
    jobs = get_scraper(
        "workday", "https://acme.wd1.myworkdayjobs.com/careers", "Acme"
    ).parse(raw, SCRAPED_AT)
    assert jobs[0].location == "2 Locations"
    assert jobs[0].remote is None


def test_workday_keeps_the_rollup_when_the_detail_never_arrived():
    """A failed detail fetch leaves `_detail` empty and the Job is still kept (module
    docstring). Better a rollup string than None — it is what the listing said."""
    raw = [{"title": "A", "locationsText": "3 Locations", "bulletFields": ["R1"]}]
    jobs = get_scraper(
        "workday", "https://acme.wd1.myworkdayjobs.com/careers", "Acme"
    ).parse(raw, SCRAPED_AT)
    assert jobs[0].location == "3 Locations"


def test_workday_detail_gap_names_what_the_failures_actually_were(monkeypatch, caplog):
    """A detail pass that loses most of a Board must say *what* it lost them to.

    Measured on `workday:ngc/Northrop_Grumman_External_Site`, runs 32942748996 and
    32936269675 (2026-08-26): 3,536/3,691 and 3,569/3,678 details missing, reported as one
    INFO count each. The two runs took 134s and 1,658s for the same ratio — a gap only the
    failure classes explain, since a retried 5xx costs a backoff ladder and a non-transient
    status costs one round trip. `_paginate` has reported exactly this for the listing pass
    since ADR-0076; the detail pass mapped every outcome onto an untyped None.
    """
    import asyncio
    from collections import Counter

    from headstart import http
    from headstart.scrapers.workday import WorkdayScraper

    outcomes = iter([429, 503, 503, 404, 200])

    async def fake_fetch_async(session, method, url, **kw):
        status = next(outcomes)

        class _R:
            status_code = status

            @staticmethod
            def json():
                return {"jobPostingInfo": {"jobDescription": "<p>d</p>"}}

        return _R()

    monkeypatch.setattr(http, "fetch_async", fake_fetch_async)
    scraper = WorkdayScraper("https://acme.wd1.myworkdayjobs.com/careers", "Acme")
    classes: Counter[str] = Counter()
    for path in ("/job/a", "/job/b", "/job/c", "/job/d", "/job/e"):
        asyncio.run(scraper._job_detail_async(None, path, classes))
    # the settled statuses are kept apart, not collapsed into one count
    assert classes == Counter({"HTTP 503": 2, "HTTP 429": 1, "HTTP 404": 1})

    # and a Board that loses most of its details says so at WARNING, naming the classes —
    # a 96%-empty detail pass previously produced no warning at all
    caplog.set_level(logging.WARNING, logger="headstart.scrapers.workday")
    scraper._report_detail_losses([None, None, None, None, {"d": 1}], classes)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "workday:acme/careers" in message
    assert "4 of 5 detail(s) failed mid-crawl" in message
    assert "HTTP 503 x2" in message
    # the tail states the ADR-0053 consequence that *doesn't* follow (ADR-0088)
    assert "not a truncation (the listing pass reports its own)" in message


def test_workday_detail_gap_records_a_raised_request(monkeypatch):
    """A request that never settled is its own class, not another non-200.

    The distinction is the whole point: a spent retry ladder, a timeout and a DNS failure
    each call for a different response, and `_job_detail_async` returned the same None for
    all three plus every 4xx/5xx."""
    import asyncio
    from collections import Counter

    from headstart import http
    from headstart.scrapers.workday import WorkdayScraper

    async def fake_fetch_async(session, method, url, **kw):
        raise http.RequestsError("timed out")

    monkeypatch.setattr(http, "fetch_async", fake_fetch_async)
    scraper = WorkdayScraper("https://acme.wd1.myworkdayjobs.com/careers", "Acme")
    classes: Counter[str] = Counter()
    assert asyncio.run(scraper._job_detail_async(None, "/job/a", classes)) is None
    assert asyncio.run(scraper._job_detail_async(None, None, classes)) is None
    # curl_cffi's `RequestsError` is an alias of `RequestException`, which is the name
    # `_failure_class` reports for a request the origin never gave a status to
    assert classes == Counter({"RequestException": 1, "no externalPath": 1})


def test_workday_complete_detail_pass_warns_about_nothing(monkeypatch, caplog):
    """The counterpart guard: a Board whose details all arrived must stay silent, so the
    new WARNING keeps meaning something."""
    from collections import Counter

    from headstart.scrapers.workday import WorkdayScraper

    caplog.set_level(logging.INFO, logger="headstart.scrapers.workday")
    WorkdayScraper("https://acme.wd1.myworkdayjobs.com/careers")._report_detail_losses(
        [{"d": 1}] * 5, Counter()
    )
    assert caplog.records == []


def test_workday_detail_gap_under_the_share_stays_info(caplog):
    """Below `_MAX_LOST_DETAIL_SHARE` the same gap reports at INFO, not WARNING.

    The escalation is the point of the threshold, so both sides of it need pinning — with
    only the WARNING side covered, deleting the ternary and always warning stayed green."""
    from collections import Counter

    from headstart.scrapers.workday import WorkdayScraper

    caplog.set_level(logging.INFO, logger="headstart.scrapers.workday")
    WorkdayScraper("https://acme.wd1.myworkdayjobs.com/careers")._report_detail_losses(
        [None] + [{"d": 1}] * 9, Counter({"HTTP 404": 1})
    )
    assert [r.levelno for r in caplog.records] == [logging.INFO]
    assert "1 of 10 detail(s) failed mid-crawl (HTTP 404 x1)" in caplog.text


def test_workday_detail_classes_always_account_for_every_loss(monkeypatch, caplog):
    """The parenthesis must total `missing`, never a fraction of it presented as the reason.

    A 200 whose body isn't JSON makes `response.json()` raise; both fan-outs turn a raising
    item into None, so the posting counted as lost while naming no class. Measured on the
    unfixed code: 5 details lost, `classes` empty, and the line printed no `(...)` at all —
    a 3,536-loss Board could read `(HTTP 404 x10)` as though 404s explained it."""
    import asyncio
    from collections import Counter

    from headstart import http
    from headstart.scrapers.workday import WorkdayScraper

    async def fake_fetch_async(session, method, url, **kw):
        class _R:
            status_code = 200

            @staticmethod
            def json():
                raise ValueError("not json")

        return _R()

    monkeypatch.setattr(http, "fetch_async", fake_fetch_async)
    scraper = WorkdayScraper("https://acme.wd1.myworkdayjobs.com/careers", "Acme")
    classes: Counter[str] = Counter()
    assert asyncio.run(scraper._job_detail_async(None, "/job/a", classes)) is None
    assert classes == Counter({"unparseable": 1})

    # and whatever still escapes labelling is named rather than silently dropped, so the
    # classes shown always sum to the loss count
    caplog.set_level(logging.WARNING, logger="headstart.scrapers.workday")
    scraper._report_detail_losses([None] * 4, Counter({"HTTP 404": 1}))
    assert (
        "4 of 4 detail(s) failed mid-crawl (unclassified x3, HTTP 404 x1)"
        in caplog.text
    )


def test_workday_detail_classes_reach_the_report_through_fetch_raw(monkeypatch, caplog):
    """The wiring, not just the pieces: a real `fetch_raw` must carry `classes` to the line.

    Every other test here calls `_job_detail_async` and `_report_detail_losses` directly, so
    dropping the `classes` argument from the `fan_out_async` lambda left them all passing."""
    from headstart import http
    from headstart.scrapers.workday import WorkdayScraper

    scraper = WorkdayScraper("https://acme.wd1.myworkdayjobs.com/careers", "Acme")
    monkeypatch.setattr(scraper, "_resolve_instance", lambda: None)
    monkeypatch.setattr(
        scraper,
        "_post",
        lambda applied, offset=0, raise_gone=False: {
            "total": 2,
            "jobPostings": [{"externalPath": "/job/a"}, {"externalPath": "/job/b"}],
        },
    )

    async def fake_fetch_async(session, method, url, **kw):
        class _R:
            status_code = 404

            @staticmethod
            def json():
                return {}

        return _R()

    monkeypatch.setattr(http, "fetch_async", fake_fetch_async)
    caplog.set_level(logging.WARNING, logger="headstart.scrapers.workday")
    scraper.fetch_raw()
    assert "2 of 2 detail(s) failed mid-crawl (HTTP 404 x2)" in caplog.text


def test_freshteam_parse():
    jobs = get_scraper("freshteam", "12min", "12min").parse(
        _load("freshteam_12min.json"), SCRAPED_AT
    )
    assert len(jobs) == 4  # the deleted=true job is dropped

    marketing, backend, sre, platform = jobs
    assert marketing.id == "freshteam:12min:1000070208"  # numeric id, not unique_id
    assert marketing.company == "12min"
    assert (
        marketing.title == "Email Marketing & Lifecycle Automation Specialist (Remote)"
    )
    # preferred_remote_job_locations replaces the branch join (Brazil) for a remote job whose
    # real hiring geography is elsewhere — the branch is the tenant's registered office, not
    # where the work is.
    assert marketing.location == "Remote, United States of America"
    assert marketing.remote is True  # native remote flag
    assert marketing.department == "Marketing"  # job_role_id join
    assert marketing.url.startswith("https://12min.freshteam.com/jobs/")
    assert marketing.posted_at == "2025-02-06T19:22:55.000Z"
    assert marketing.description and "</" not in marketing.description  # HTML stripped
    assert marketing.employment_type == "Contract"  # job_type 1

    # native remote=false, physical branch, no preferred_remote_job_locations -> untouched
    assert backend.location == "Bengaluru, India" and backend.remote is False
    assert backend.employment_type == "Full Time"  # job_type 2
    # native remote=false but the branch location literally says "Remote" -> both-family recovers it
    assert sre.location == "Remote - India" and sre.remote is True
    assert sre.employment_type is None  # job_type absent from the payload

    # branch is Singapore, but preferred_remote_job_locations names India + Vietnam: the wrong
    # branch country must NOT ride along next to the real ones (that's the false-positive/
    # false-negative bug), and multiple places join with "; " like workday's multi-location strings.
    assert platform.location == "India, India; Vietnam, Viet Nam"
    assert "Singapore" not in platform.location
    assert platform.remote is True
    assert platform.employment_type == "Fixed Term Contract"  # job_type 8


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
    # a mid-crawl 404 (None from _post_async) skips that page but keeps the rest, and one
    # WARNING reports the gap — the tripwire for a partial board
    from headstart.scrapers.workday import WorkdayScraper

    s = WorkdayScraper("https://acme.wd1.myworkdayjobs.com/ext")
    pages = {
        20: {"jobPostings": [{"bulletFields": ["R20"]}]},
        40: None,  # this page 404ed mid-crawl
        60: {"jobPostings": [{"bulletFields": ["R60"]}]},
        80: {"jobPostings": [{"bulletFields": ["R80"]}]},
    }

    async def fake_post_async(session, applied, offset):
        return pages[offset]

    monkeypatch.setattr(s, "_post_async", fake_post_async)
    absorbed = []
    caplog.set_level(logging.WARNING, logger="headstart.scrapers.workday")
    s._paginate({}, 100, absorbed.extend)
    # the surviving pages are all absorbed — fanned out concurrently now, so not guaranteed to
    # land in offset order (the postings they build are deduplicated/looked up by id, never by
    # position, so this doesn't need to assert order to prove the pagination is correct)
    assert sorted(p["bulletFields"][0] for p in absorbed) == ["R20", "R60", "R80"]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert warnings[0].name == "headstart.scrapers.workday"
    # 1 of 5, not 1 of 4: the first page `_exhaust` already holds counts too
    assert "1 of 5 page(s) failed" in warnings[0].getMessage()
    assert "workday:acme/ext" in warnings[0].getMessage()  # the board key
    assert s.truncated is not None  # and it travels with the Jobs (ADR-0053)


def test_workday_paginate_fans_out_bounded_by_page_streams(monkeypatch):
    """`_paginate` no longer walks offsets one at a time — it fans them out concurrently,
    bounded to `_PAGE_STREAMS` in flight at once (mirrors `fan_out_async`'s bounded-semaphore
    shape). Prove both halves of that: genuinely concurrent (more than one in flight at a time)
    and genuinely bounded (never past the width)."""
    import asyncio

    from headstart.scrapers import workday as workday_mod
    from headstart.scrapers.workday import WorkdayScraper

    s = WorkdayScraper("https://acme.wd1.myworkdayjobs.com/ext")
    in_flight = 0
    max_in_flight = 0

    async def fake_post_async(session, applied, offset):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        # yield so sibling pages can overlap before this one finishes
        await asyncio.sleep(0)
        in_flight -= 1
        return {"jobPostings": [{"bulletFields": [f"R{offset}"]}]}

    monkeypatch.setattr(s, "_post_async", fake_post_async)
    # comfortably more pages than the stream width, so the bound actually gets exercised
    total = workday_mod._PAGE_LIMIT * (workday_mod._PAGE_STREAMS + 11)
    absorbed = []
    s._paginate({}, total, absorbed.extend)

    assert len(absorbed) == len(
        range(workday_mod._PAGE_LIMIT, total, workday_mod._PAGE_LIMIT)
    )
    assert 1 < max_in_flight <= workday_mod._PAGE_STREAMS


def test_workday_paginate_narrows_its_fan_out_once_the_origin_has_walled(monkeypatch):
    """#195's second call site. `_paginate_async` builds its own semaphore rather than calling
    `fan_out_async` — its exception contract is the one `_paginate` needs (ADR-0076) — so the
    width policy has to reach it separately or the larger half of Workday's traffic keeps paging
    at full width against an origin that has already said no.

    Paired against its own control, because a `<=` assertion alone passes on a fan-out that
    simply never saturated: the same fake, same offsets, walled and not."""
    import asyncio

    from headstart import spare_egress
    from headstart.scrapers import workday as workday_mod
    from headstart.scrapers.workday import WorkdayScraper

    def widest() -> int:
        s = WorkdayScraper("https://acme.wd1.myworkdayjobs.com/ext")
        in_flight = 0
        peak = 0

        async def fake_post_async(session, applied, offset):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0)
            in_flight -= 1
            return {"jobPostings": []}

        monkeypatch.setattr(s, "_post_async", fake_post_async)
        s._paginate(
            {},
            workday_mod._PAGE_LIMIT * (workday_mod._PAGE_STREAMS + 11),
            lambda b: None,
        )
        return peak

    spare_egress.reset()
    try:
        assert widest() == workday_mod._PAGE_STREAMS
        spare_egress.mark_walled("workday", 429)
        assert widest() == spare_egress._WALLED_STREAM_WIDTH
    finally:
        spare_egress.reset()


def test_workday_paginate_absorbs_a_retry_exhausted_page_mid_crawl(monkeypatch, caplog):
    """A page that spends `fetch_async`'s retry ladder on a persisting 429 is one page of a live
    board, exactly like a mid-crawl 404 — it must not discard the pages that did arrive (#194:
    the bigger the board, the more page requests, so raising here killed the boards worth most).
    Same shape as the 404 test above, with the failing page raising instead of returning None."""
    from headstart import http
    from headstart.scrapers.workday import WorkdayScraper

    s = WorkdayScraper("https://acme.wd1.myworkdayjobs.com/ext")

    async def fake_post_async(session, applied, offset):
        if offset == 40:  # retries spent — `_post_async`'s raise_for_status
            raise http.RequestsError("HTTP Error 429: Too Many Requests")
        return {"jobPostings": [{"bulletFields": [f"R{offset}"]}]}

    monkeypatch.setattr(s, "_post_async", fake_post_async)
    absorbed = []
    caplog.set_level(logging.WARNING, logger="headstart.scrapers.workday")
    s._paginate({}, 100, absorbed.extend)

    assert sorted(p["bulletFields"][0] for p in absorbed) == ["R20", "R60", "R80"]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "1 of 5 page(s) failed" in warnings[0].getMessage()
    # and the gap travels with the Jobs, or `index sync` reads it as delistings (ADR-0053)
    assert s.truncated is not None


def test_workday_paginate_raises_when_most_pages_fail_mid_crawl(monkeypatch):
    """The other end of the same line. One failed page in five is a truncation worth keeping (the
    test above); a crawl that loses more than `_MAX_LOST_PAGE_SHARE` of its pages has kept too
    little to read as those postings, and marking *that* truncated would tell `index sync` to
    preserve rows for a query we barely read — so it still fails outright, as every mid-crawl
    error did before #194. The premise this test used to carry — that a *single* non-404 error
    fails the crawl — is what #194 changed."""
    from headstart import http
    from headstart.scrapers.workday import WorkdayScraper

    s = WorkdayScraper("https://acme.wd1.myworkdayjobs.com/ext")

    async def fake_post_async(session, applied, offset):
        if offset == 20:
            return {"jobPostings": [{"bulletFields": ["R20"]}]}
        raise http.RequestsError("HTTP Error 500: Internal Server Error")

    monkeypatch.setattr(s, "_post_async", fake_post_async)
    # 3 of 4 pages gone. The origin's own words are re-raised, not an error of our own making:
    # `board_failures._GONE` reads the status out of that text to tell gone from throttled.
    with pytest.raises(http.RequestsError, match="500"):
        s._paginate({}, 100, lambda batch: None)
    # an error, not a truncation — matches `_post`'s own contract
    assert s.truncated is None


def test_workday_paginate_raises_without_reading_a_404_majority_as_gone(monkeypatch):
    """The threshold counts pages that came back short, whatever made them — so a crawl that
    404s most of its pages fails too, and there is no exception to re-raise. The one it
    synthesises must not read as a *gone* verdict: `board_failures` ages a Board toward
    quarantine on 404/410 text, and a mid-crawl 404 is explicitly one page of a live board."""
    from headstart.ingest.board_failures import is_gone
    from headstart.scrapers.workday import WorkdayScraper

    s = WorkdayScraper("https://acme.wd1.myworkdayjobs.com/ext")

    async def fake_post_async(session, applied, offset):
        if offset == 20:
            return {"jobPostings": [{"bulletFields": ["R20"]}]}
        return None  # 404ed mid-crawl

    monkeypatch.setattr(s, "_post_async", fake_post_async)
    with pytest.raises(RuntimeError, match="3 of 5") as caught:
        s._paginate({}, 100, lambda batch: None)
    assert not is_gone(f"{type(caught.value).__name__}: {caught.value}")
    assert s.truncated is None


def test_workday_paginate_sync_absorbs_a_retry_exhausted_page_mid_crawl(monkeypatch):
    """The kill switch (ADR-0016) may change how the pages are fetched; it must not change how
    much of a struggling board survives."""
    from headstart import http
    from headstart.scrapers.workday import WorkdayScraper

    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "0")
    s = WorkdayScraper("https://acme.wd1.myworkdayjobs.com/ext")

    def fake_post(applied, offset, **_):
        if offset == 40:
            raise http.RequestsError("HTTP Error 429: Too Many Requests")
        return {"jobPostings": [{"bulletFields": [f"R{offset}"]}]}

    monkeypatch.setattr(s, "_post", fake_post)
    absorbed = []
    s._paginate({}, 100, absorbed.extend)

    assert [p["bulletFields"][0] for p in absorbed] == ["R20", "R60", "R80"]
    assert s.truncated is not None


def test_workday_paginate_sync_raises_when_most_pages_fail_mid_crawl(monkeypatch):
    from headstart import http
    from headstart.scrapers.workday import WorkdayScraper

    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "0")
    s = WorkdayScraper("https://acme.wd1.myworkdayjobs.com/ext")

    def fake_post(applied, offset, **_):
        if offset == 20:
            return {"jobPostings": [{"bulletFields": ["R20"]}]}
        raise http.RequestsError("HTTP Error 500: Internal Server Error")

    monkeypatch.setattr(s, "_post", fake_post)
    with pytest.raises(http.RequestsError, match="500"):
        s._paginate({}, 100, lambda batch: None)
    assert s.truncated is None


def test_workday_paginate_falls_back_to_sync_when_async_fanout_is_off(monkeypatch):
    """HEADSTART_ASYNC_FANOUT=0 is this codebase's one incident-response kill switch for async
    traffic against an ATS (ADR-0016) — the detail pass already obeys it, and pagination must
    too, or "stop all async requests to Workday" is only half true."""
    from headstart.scrapers.workday import WorkdayScraper

    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "0")
    s = WorkdayScraper("https://acme.wd1.myworkdayjobs.com/ext")
    seen_offsets: list[int] = []

    def fake_post(applied, offset, **_):
        seen_offsets.append(offset)
        return {"jobPostings": [{"bulletFields": [f"R{offset}"]}]}

    def boom_post_async(*a, **k):
        raise AssertionError("must not touch the async path when fanout is off")

    monkeypatch.setattr(s, "_post", fake_post)
    monkeypatch.setattr(s, "_post_async", boom_post_async)
    absorbed = []
    s._paginate({}, 100, absorbed.extend)

    assert seen_offsets == [20, 40, 60, 80]
    assert [p["bulletFields"][0] for p in absorbed] == ["R20", "R40", "R60", "R80"]


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


class _TrakstarDetailResp:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


def test_trakstar_extract_posting_prefers_jsonld_when_present():
    from headstart.scrapers.trakstar import TrakstarScraper

    page = """<html><head>
    <script type="application/ld+json">
    {"@context": "http://schema.org", "@type": "JobPosting",
     "title": "Backend Engineer", "datePosted": "2026-03-01",
     "description": "&lt;p&gt;Build the platform.&lt;/p&gt;"}
    </script></head><body>
    <div class="jobdesciption"><p>Ignored -- JSON-LD wins when both are present.</p></div>
    </body></html>"""
    posting = TrakstarScraper._extract_posting(_TrakstarDetailResp(text=page))
    assert posting["datePosted"] == "2026-03-01"
    assert posting["description"] == "&lt;p&gt;Build the platform.&lt;/p&gt;"


def test_trakstar_extract_posting_falls_back_to_html_when_jsonld_absent():
    """Some tenant boards (m800, managementapps, rivian, cityflo, dripcapital -- #179) never
    emit the JSON-LD block at all; the description still renders into the page's own
    `.jobdesciption` container (that's the tenant template's own spelling). Shaped like the
    real cityflo/dripcapital markup, live-fetched 2026-08-19, whose body is wrapped in a
    nested <div> that a naive non-greedy regex truncates (verified: it loses >1000 chars on
    the real dripcapital page)."""
    from headstart.models import html_to_text
    from headstart.scrapers.trakstar import TrakstarScraper

    page = """<html><body>
    <div class="jobdesciption">
        <div class="s-vgBottom2 u-fontSize14 u-colorGray4">
    <p>Build the payments platform end to end.</p>
    <p>Requirements: 3+ years of Python experience.</p>
    </div>
        </div>
    <section class="bottomspace-double">apply here</section>
    </body></html>"""
    posting = TrakstarScraper._extract_posting(_TrakstarDetailResp(text=page))
    assert posting is not None
    assert "datePosted" not in posting  # not present anywhere on these pages
    text = html_to_text(posting["description"])
    assert text == (
        "Build the payments platform end to end. Requirements: 3+ years of Python experience."
    )
    assert (
        "apply here" not in text
    )  # stops at the container's own close, not a later one


def test_trakstar_extract_posting_none_when_neither_jsonld_nor_html_present():
    from headstart.scrapers.trakstar import TrakstarScraper

    page = "<html><body><p>No JSON-LD and no .jobdesciption div here.</p></body></html>"
    assert TrakstarScraper._extract_posting(_TrakstarDetailResp(text=page)) is None


_TRAKSTAR_FEED = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel xmlns:job="https://recruiterbox.com/rss/job/">
<title>Jobs at Acme</title>
<item><title>Backend Engineer</title>
<link>http://acme.hire.trakstar.com/jobs/fk0abc1</link>
<description>&lt;h2 id="job_meta"&gt;&lt;p&gt;Location: Austin, Texas, United States&lt;/p&gt;&lt;/h2&gt;&lt;div id="job_description"&gt;&lt;p&gt;Build the platform.&lt;/p&gt;&lt;/div&gt;&lt;div id="how_to_apply"&gt;&lt;a href="#"&gt;Apply&lt;/a&gt;&lt;/div&gt;</description>
<pubDate>Fri, 21 Aug 2026 00:00:00 +0530</pubDate>
<guid>http://acme.hire.trakstar.com/jobs/fk0abc1</guid>
<job:locationCity>Austin</job:locationCity><job:locationState>Texas</job:locationState><job:locationCountry>United States</job:locationCountry>
<job:positionType>full_time</job:positionType><job:team>Engineering</job:team></item>
<item><title>Store Associate</title>
<link>http://acme.hire.trakstar.com/jobs/fk0xyz2/</link>
<description>&lt;div id="job_description"&gt;&lt;p&gt;Help customers.&lt;/p&gt;&lt;/div&gt;</description>
<pubDate></pubDate>
<guid>http://acme.hire.trakstar.com/jobs/fk0xyz2</guid>
<job:locationCity></job:locationCity><job:locationState></job:locationState><job:locationCountry></job:locationCountry>
<job:positionType>part_time</job:positionType><job:team></job:team></item>
</channel></rss>"""


def test_trakstar_feed_items_parses_real_shape():
    from headstart.scrapers.trakstar import _feed_items

    items = _feed_items(_TRAKSTAR_FEED)
    assert len(items) == 2
    first = items[0]
    assert first["code"] == "fk0abc1"  # trailing slash absent
    assert first["title"] == "Backend Engineer"
    assert first["location"] == "Austin, Texas, United States"
    assert (
        first["description"] == "<p>Build the platform.</p>"
    )  # job_meta/how_to_apply excluded
    assert first["posted_at"] == "2026-08-21"
    assert first["department"] == "Engineering"
    assert first["employment_type"] == "Full-time"


def test_trakstar_feed_items_code_from_trailing_slash_link():
    from headstart.scrapers.trakstar import _feed_items

    items = _feed_items(_TRAKSTAR_FEED)
    assert items[1]["code"] == "fk0xyz2"  # trailing slash present on this item's <link>


def test_trakstar_feed_items_handles_missing_optional_fields():
    from headstart.scrapers.trakstar import _feed_items

    items = _feed_items(_TRAKSTAR_FEED)
    second = items[1]
    assert second["location"] is None  # all three location parts blank
    assert second["posted_at"] is None  # blank pubDate
    assert second["department"] is None  # blank job:team
    assert second["employment_type"] == "Part-time"


def test_trakstar_feed_items_none_on_malformed_xml():
    from headstart.scrapers.trakstar import _feed_items

    assert _feed_items("not xml at all <<<") is None


def test_trakstar_feed_items_empty_channel_is_empty_list_not_none():
    # a real, distinct case from malformed XML or a 404 — a tenant whose feed works but
    # currently has zero open postings (confirmed live: grassrootsvoter, knowingtechnologies)
    from headstart.scrapers.trakstar import _feed_items

    xml = '<?xml version="1.0"?><rss version="2.0"><channel><title>Jobs at Acme</title>\n</channel></rss>'
    assert _feed_items(xml) == []


def test_trakstar_feed_items_skips_item_with_unparseable_link():
    from headstart.scrapers.trakstar import _feed_items

    xml = """<rss><channel><item><title>Bad Link</title><link>not-a-jobs-url</link>
    <description></description></item></channel></rss>"""
    assert _feed_items(xml) == []


def test_trakstar_feed_posted_at_unparseable_returns_none():
    from headstart.scrapers.trakstar import _feed_posted_at

    assert _feed_posted_at("not a date") is None
    assert _feed_posted_at(None) is None


def test_trakstar_jobs_from_feed_builds_job_objects():
    from headstart.scrapers.trakstar import _feed_items, _jobs_from_feed

    items = _feed_items(_TRAKSTAR_FEED)
    jobs = _jobs_from_feed("trakstar", "acme", "Acme", items, SCRAPED_AT)
    assert len(jobs) == 2
    j = jobs[0]
    assert j.id == "trakstar:acme:fk0abc1"
    assert j.ats == "trakstar"
    assert j.company == "Acme"
    assert j.url == "https://acme.hire.trakstar.com/jobs/fk0abc1/"
    assert j.description == "Build the platform."
    assert j.scraped_at == SCRAPED_AT
    assert j.remote is False


def test_trakstar_fetch_via_feed_returns_none_when_feed_unavailable(monkeypatch):
    import headstart.scrapers.trakstar as trakstar_module

    monkeypatch.setattr(trakstar_module, "_fetch_feed", lambda slug: None)
    s = get_scraper("trakstar", "acme", "Acme")
    assert s.fetch_via_feed(SCRAPED_AT) is None


def test_trakstar_fetch_via_feed_returns_empty_list_when_feed_has_zero_jobs(
    monkeypatch,
):
    # a working feed reporting zero current openings must be distinguishable from "no feed at
    # all" — a caller checking `is None` sees the difference; one that checks truthiness doesn't
    import headstart.scrapers.trakstar as trakstar_module

    empty_feed = '<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>'
    monkeypatch.setattr(trakstar_module, "_fetch_feed", lambda slug: empty_feed)
    s = get_scraper("trakstar", "acme", "Acme")
    result = s.fetch_via_feed(SCRAPED_AT)
    assert result == []
    assert result is not None


def test_trakstar_fetch_via_feed_returns_jobs_when_available(monkeypatch):
    import headstart.scrapers.trakstar as trakstar_module

    monkeypatch.setattr(trakstar_module, "_fetch_feed", lambda slug: _TRAKSTAR_FEED)
    s = get_scraper("trakstar", "acme", "Acme")
    jobs = s.fetch_via_feed(SCRAPED_AT)
    assert len(jobs) == 2
    assert jobs[0].id == "trakstar:acme:fk0abc1"


def _trakstar_cards_page(n_cards, total=None):
    """A minimal careers-page HTML with ``n_cards`` job cards and, when ``total`` is given, the
    page's own "View N Openings" button (real markup shape, live-fetched 2026-08-25:
    ``<a class="js-show-openings ..." href="#content">View 634 Openings</a>``)."""
    button = (
        f'<a class="js-show-openings btn" href="#content">View {total} Openings</a>'
        if total is not None
        else ""
    )
    cards = "".join(
        f'<div class="js-careers-page-job-list-item" data-href="/jobs/code{i}/">'
        f'<h3 class="js-job-list-opening-name" title="Job {i}">Job {i}</h3>'
        f'<div class="js-job-list-opening-loc" title="Remote">Remote</div>'
        f"</div>"
        for i in range(n_cards)
    )
    return f"<html><body>{button}{cards}</body></html>"


def test_trakstar_is_capped_true_when_total_exceeds_cards():
    from headstart.scrapers.trakstar import _is_capped

    html = _trakstar_cards_page(25, total=40)
    assert _is_capped(html, 25) is True


def test_trakstar_is_capped_false_when_total_matches_cards_at_the_render_cap():
    # A Board can genuinely have exactly 25 real postings (confirmed live 2026-08-25:
    # interglobalhomes, 2workonline1, dataentrydirect) -- the card count alone can't tell that
    # apart from a truncated one, but the page's own total can, and must not trigger a wasted
    # RSS fetch.
    from headstart.scrapers.trakstar import _is_capped

    html = _trakstar_cards_page(25, total=25)
    assert _is_capped(html, 25) is False


def test_trakstar_is_capped_falls_back_to_card_count_without_a_total():
    from headstart.scrapers.trakstar import _is_capped

    assert _is_capped(_trakstar_cards_page(25), 25) is True
    assert _is_capped(_trakstar_cards_page(24), 24) is False


def test_trakstar_fetch_raw_uses_feed_when_capped_and_skips_the_detail_pass(
    monkeypatch,
):
    """A capped Board (sleekr/colcare-shaped: 25 cards, a higher total) whose feed answers must
    return the feed's jobs -- and must never fetch a single per-job detail page for the cards
    it's about to discard (those pages sit behind DataDome; the feed already has the full
    description inline)."""
    import headstart.scrapers.trakstar as trakstar_module

    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "0")
    s = get_scraper("trakstar", "acme", "Acme")
    monkeypatch.setattr(s, "_get", lambda url=None: _trakstar_cards_page(25, total=40))
    monkeypatch.setattr(trakstar_module, "_fetch_feed", lambda slug: _TRAKSTAR_FEED)
    detail_calls = []
    monkeypatch.setattr(s, "_job_posting", lambda code: detail_calls.append(code))

    raw = s.fetch_raw()

    assert detail_calls == []  # the capped cards' detail pages were never fetched
    assert raw == {"feed_items": trakstar_module._feed_items(_TRAKSTAR_FEED)}
    jobs = s.parse(raw, SCRAPED_AT)
    assert len(jobs) == 2
    assert jobs[0].id == "trakstar:acme:fk0abc1"
    assert s.truncated is None  # the feed answered in full -- this Board is not short


def test_trakstar_fetch_raw_skips_feed_when_not_capped(monkeypatch):
    """The 92%+ of Boards under the render cap must cost exactly the one careers-page request
    they always did -- no RSS fetch, since there's nothing the cards are missing."""
    import headstart.scrapers.trakstar as trakstar_module

    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "0")
    s = get_scraper("trakstar", "acme", "Acme")
    monkeypatch.setattr(s, "_get", lambda url=None: _trakstar_cards_page(3, total=3))

    def boom_feed(slug):
        raise AssertionError("must not fetch the RSS feed when the Board isn't capped")

    monkeypatch.setattr(trakstar_module, "_fetch_feed", boom_feed)
    monkeypatch.setattr(s, "_job_posting", lambda code: None)

    raw = s.fetch_raw()

    assert "feed_items" not in raw
    assert len(raw["postings"]) == 3
    assert s.truncated is None


def test_trakstar_fetch_raw_keeps_html_when_feed_unreachable(monkeypatch):
    """sleekr-shaped live case: capped (25 cards, real total higher) but the feed 404s. The
    capped HTML list must still come back -- not an empty Board -- and the Board must be marked
    truncated now that the page's own total makes the shortfall provable, not just suspected."""
    import headstart.scrapers.trakstar as trakstar_module

    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "0")
    s = get_scraper("trakstar", "acme", "Acme")
    monkeypatch.setattr(s, "_get", lambda url=None: _trakstar_cards_page(25, total=77))
    monkeypatch.setattr(trakstar_module, "_fetch_feed", lambda slug: None)
    monkeypatch.setattr(s, "_job_posting", lambda code: None)

    raw = s.fetch_raw()

    assert "feed_items" not in raw
    assert len(raw["postings"]) == 25
    jobs = s.parse(raw, SCRAPED_AT)
    assert len(jobs) == 25
    assert s.truncated is not None
    assert "unreachable" in s.truncated


def test_trakstar_fetch_raw_does_not_mark_truncated_for_card_count_heuristic_alone(
    monkeypatch,
):
    """A Board with no "View N Openings" total on the page (_is_capped falls back to the bare
    card-count heuristic) that also lands on the cap and has an unreachable feed must NOT be
    marked truncated -- this is the same ambiguous "reached the cap" signal the pre-fix code
    deliberately declined to mark_truncated for; only the page's own total turns that into
    proof, and this Board never had one."""
    import headstart.scrapers.trakstar as trakstar_module

    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "0")
    s = get_scraper("trakstar", "acme", "Acme")
    monkeypatch.setattr(
        s, "_get", lambda url=None: _trakstar_cards_page(25)
    )  # no total button
    monkeypatch.setattr(trakstar_module, "_fetch_feed", lambda slug: None)
    monkeypatch.setattr(s, "_job_posting", lambda code: None)

    raw = s.fetch_raw()

    assert "feed_items" not in raw
    assert len(raw["postings"]) == 25
    assert s.truncated is None


def test_trakstar_feed_location_strips_each_part():
    # real values, live-fetched 2026-08-25 (americandirectlogistic): a bare field routinely
    # carries a stray space that an unstripped join turns into 'fort worth , tx , usa '
    from headstart.scrapers.trakstar import _feed_location

    assert _feed_location("fort worth ", "tx ", "usa ") == "fort worth, tx, usa"


def test_trakstar_feed_location_drops_whitespace_only_part():
    # real values, live-fetched 2026-08-25 (ihjez): a blank part can be a lone space, not "",
    # which the old `if part` truthy check let through as a dangling comma
    from headstart.scrapers.trakstar import _feed_location

    assert _feed_location("Amman", " ", "Jordan") == "Amman, Jordan"


def test_trakstar_feed_location_drops_state_that_repeats_city():
    # real values, live-fetched 2026-08-25 (anduin), re-confirmed live 2026-08-26 -- see
    # docs/location-audit/2026-08-26_trakstar-cap-verification.md for how common this is
    from headstart.scrapers.trakstar import _feed_location

    assert _feed_location("Hamburg", "Hamburg", "Deutschland") == "Hamburg, Deutschland"
    assert (
        _feed_location("Ho Chi Minh City", "Ho Chi Minh City", "Vietnam")
        == "Ho Chi Minh City, Vietnam"
    )


def test_trakstar_feed_location_all_blank_is_none():
    from headstart.scrapers.trakstar import _feed_location

    assert _feed_location("", "", "") is None


def test_trakstar_feed_items_cleans_dirty_location_end_to_end():
    from headstart.scrapers.trakstar import _feed_items

    xml = """<rss><channel xmlns:job="https://recruiterbox.com/rss/job/">
    <item><title>Ops</title><link>http://acme.hire.trakstar.com/jobs/fk0aaa1/</link>
    <description></description>
    <job:locationCity>Hamburg</job:locationCity><job:locationState>Hamburg</job:locationState>
    <job:locationCountry> Deutschland </job:locationCountry></item>
    </channel></rss>"""
    items = _feed_items(xml)
    assert items[0]["location"] == "Hamburg, Deutschland"


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


def _recruitee_offer(**fields):
    """One offer through the real parse path. See `_is_remote_sentinel` for why these matter."""
    offer = {"id": 1, "title": "T", **fields}
    return get_scraper("recruitee", "weekday", "Weekday").parse(
        {"offers": [offer]}, SCRAPED_AT
    )[0]


@pytest.mark.parametrize(
    "marker",
    [
        "Remote job",
        "Poste a distance",
        "Homeoffice",
        "Werken op afstand",
        "Trabajo a distancia",
        "Praca zdalna",
        "Trabalho remoto",
        "Lavoro da remoto",
    ],
)
def test_recruitee_localized_remote_marker_does_not_swallow_the_city(marker):
    """Every locale's marker loses to the structured city/country, not just the English one.

    The list is illustrative, not exhaustive — independent live samples keep surfacing locales
    the previous one missed, which is exactly why `_is_remote_sentinel` is structural.
    """
    j = _recruitee_offer(
        location=marker, city="Bangalore", country="India", remote=True
    )
    assert j.location == "Bangalore, India"
    assert j.remote is True  # remoteness survives the rewrite, via Recruitee's own flag


def test_recruitee_city_spelled_differently_cannot_forge_remote():
    """The detector's known false positive must cost a spelling, never the `remote` verdict.

    "Bengaluru, India" does not contain `city="Bangalore"`, so the detector fires on a real
    on-site location. The fallback swaps one spelling of the place for another — acceptable —
    but `remote` must stay False, which is why `parse` does not let the detector decide it.
    """
    j = _recruitee_offer(location="Bengaluru, India", city="Bangalore", country="India")
    assert j.location == "Bangalore, India"  # a place, either way
    assert j.remote is False  # NOT forged by the detector


def test_recruitee_remote_marker_with_no_city_is_left_alone():
    """Nothing to fall back to, so the marker stays rather than becoming an unexplained blank."""
    j = _recruitee_offer(location="Remote job", remote=True)
    assert j.location == "Remote job"
    assert j.remote is True


def test_recruitee_real_location_is_passed_through_untouched():
    j = _recruitee_offer(location="Berlin, Germany", city="Berlin")
    assert j.location == "Berlin, Germany"
    assert j.remote is False


def test_recruitee_location_naming_its_own_city_is_never_a_marker():
    """A place that merely decorates the city ("Remote - Bangalore") is still a place."""
    j = _recruitee_offer(
        location="Remote - Bangalore", city="Bangalore", country="India"
    )
    assert j.location == "Remote - Bangalore"


def test_recruitee_location_falls_back_to_city_country_when_absent():
    j = _recruitee_offer(city="Remote", country="Anywhere")
    assert j.location == "Remote, Anywhere"
    assert j.remote is True  # is_remote() still reads the fallback it built


def test_recruitee_salary_formatting():
    from headstart.scrapers.recruitee import _salary

    assert _salary(None) is None
    assert _salary({"min": None, "max": None}) is None  # blank -> None, job still kept
    assert _salary(
        {"min": 50000, "max": 70000, "currency": "EUR", "period": "year"}
    ) == ("50000-70000 EUR year")
    assert _salary({"min": 80000, "currency": "USD"}) == "80000 USD"  # one-sided range


def _teamtailor_pages(monkeypatch, scraper, pages):
    """Serve `pages` (a list of item-id lists) from jobs.json, recording each URL requested."""
    asked: list[str] = []

    def _get(self, url=None):
        asked.append(url or "")
        index = 0
        if url and "page=" in url:
            index = int(url.rsplit("page=", 1)[1]) - 1
        items = pages[index] if index < len(pages) else []
        return json.dumps(
            {"title": "Co", "items": [{"id": i, "title": f"J{i}"} for i in items]}
        )

    monkeypatch.setattr(type(scraper), "_get", _get)
    return asked


def test_teamtailor_walks_every_page_not_just_the_first(monkeypatch):
    """`jobs.json` serves at most 100 items and `?page=N` walks the rest.

    Measured 2026-08-25 over 766 live Boards: 27 sat at exactly 100 and paging them out found
    4,046 Jobs — 26.4% of that sample's corpus — never scraped. A Job never fetched cannot be
    repaired downstream; it is simply absent, and `sync` sees a Board that shrank.
    """
    from headstart.scrapers import teamtailor as tt

    s = get_scraper("teamtailor", "big", "Big")
    full = list(range(tt._PAGE_SIZE))
    asked = _teamtailor_pages(monkeypatch, s, [full, [900, 901]])

    jobs = s.parse(s.fetch_raw(), SCRAPED_AT)
    assert len(jobs) == tt._PAGE_SIZE + 2
    assert len({j.id for j in jobs}) == len(jobs)  # no page overlap
    assert "page=2" in asked[1] and len(asked) == 2  # stopped on the short page


def test_teamtailor_single_page_board_costs_one_request(monkeypatch):
    """The common case must not pay for pagination — 748 of 766 Boards are one page."""
    s = get_scraper("teamtailor", "small", "Small")
    asked = _teamtailor_pages(monkeypatch, s, [[1, 2, 3]])

    assert len(s.parse(s.fetch_raw(), SCRAPED_AT)) == 3
    assert len(asked) == 1


def test_teamtailor_stops_if_the_feed_ignores_the_page_parameter(monkeypatch):
    """A feed that serves page 1 forever would otherwise loop forever — there is no page-count
    ceiling to fall back on, so this is the walk's only protection.

    Item count alone cannot tell "ran off the end" from "looping" — both keep returning a full
    page — so the walk also stops when a page adds no new ids, and marks the Board truncated
    (ADR-0053): unlike a genuinely short last page, this isn't proof the Board is exhausted.
    """
    from headstart.scrapers import teamtailor as tt

    s = get_scraper("teamtailor", "stuck", "Stuck")
    full = list(range(tt._PAGE_SIZE))
    asked = _teamtailor_pages(monkeypatch, s, [full, full, full])

    jobs = s.parse(s.fetch_raw(), SCRAPED_AT)
    assert len(jobs) == tt._PAGE_SIZE  # the repeat contributed nothing
    assert len(asked) == 2  # it stopped rather than walking forever
    assert s.truncated and "no new ids" in s.truncated


def test_teamtailor_walks_past_the_old_page_cap_when_the_board_is_genuinely_that_big(
    monkeypatch,
):
    """Pagination has no page-count ceiling — a Board with hundreds of full, all-fresh pages
    must be walked in full, not cut off, since a Job never fetched can't be repaired downstream.
    """
    from headstart.scrapers import teamtailor as tt

    s = get_scraper("teamtailor", "huge", "Huge")
    n_pages = 210  # past the old 200-page bound this scraper used to stop at
    pages = [
        list(range(page * tt._PAGE_SIZE, (page + 1) * tt._PAGE_SIZE))
        for page in range(n_pages)
    ]
    pages.append([])  # the genuine last, short page
    asked = _teamtailor_pages(monkeypatch, s, pages)

    raw = s.fetch_raw()
    assert len(raw["items"]) == tt._PAGE_SIZE * n_pages
    assert len(asked) == n_pages + 1
    assert s.truncated is None  # a real short last page — nothing was left unread


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


@pytest.mark.parametrize(
    ("position_xml", "expected"),
    [
        (
            """<position><salaryInformation><min>3200.00</min><max>4600.00</max>
            <currencyCode>EUR</currencyCode><currencySymbol>€</currencySymbol>
            <type>monthly</type></salaryInformation></position>""",
            "3200.00-4600.00 EUR monthly",
        ),
        (
            """<position><salaryInformation><min>48000.00</min>
            <currencyCode>EUR</currencyCode><currencySymbol>€</currencySymbol>
            <type>yearly</type></salaryInformation></position>""",
            "48000.00 EUR yearly",
        ),
        (
            """<position><salaryInformation><min>25.00</min>
            <currencyCode>GBP</currencyCode><type>hourly</type></salaryInformation></position>""",
            "25.00 GBP hourly",
        ),
        ("<position><salaryInformation></salaryInformation></position>", None),
        ("<position></position>", None),
    ],
)
def test_personio_salary_from_structured_salary_information(position_xml, expected):
    """Real, direct API inspection (2026-08-22, PR #243): personio's <salaryInformation> element
    is structured (min/max/currencyCode/type), one level deeper than the direct element text
    _text() used to read — always empty for this shape, so this was a real Tier-1 dead end, not
    genuinely-absent data. 13.4% of positions carry it in a live 80-board sample. <type> passes
    through unmapped: an earlier version mapped "yearly"/"monthly"/"hourly" to the bare words
    _period_multiplier_structured recognizes, on the assumption the "-ly" suffix broke that
    function's word-boundary check — code review found this was speculative (3 of 5 map entries
    provably redundant, since _period_multiplier's own hardcoded checks and annual default
    already handle every real value correctly) and it was removed."""
    from headstart.scrapers.personio import _salary

    pos = ET.fromstring(position_xml)
    assert _salary(pos) == expected


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


def test_personio_additional_offices_are_joined_into_location():
    """`<additionalOffices>` is a sibling of `<office>` inside the same `<position>` that nothing
    previously read: 13.28% of positions in a live 147-Board sample (2026-08-25, a separate draw
    from the scraper docstring's 149-Board/24.89% figure — sample variance, same real defect)
    carry it. Both are read and joined so the extra offices become filterable instead of silently
    dropped."""
    from headstart.scrapers.personio import _location

    pos = ET.fromstring(
        "<position><office>Zürich</office>"
        "<additionalOffices><office>Berlin</office><office>Hamburg</office></additionalOffices>"
        "</position>"
    )
    assert _location(pos) == "Zürich, Berlin, Hamburg"


def test_personio_placeless_office_marker_recovers_the_dropped_city():
    """Real, live 2026-08-25: `interlead.jobs.personio.de` serves one position with
    `<office>Home Office</office>` and `<additionalOffices><office>Bremen</office></additionalOffices>`
    — today `location` is just "Home Office" and the real city is silently dropped. Joining
    (rather than enumerating "Home Office"/"Mobil"/"Hybrid"/... as marker strings, which is always
    one locale behind) recovers it without needing to classify `<office>` at all."""
    from headstart.scrapers.personio import _location

    pos = ET.fromstring(
        "<position><office>Home Office</office>"
        "<additionalOffices><office>Bremen</office></additionalOffices></position>"
    )
    assert _location(pos) == "Home Office, Bremen"


def test_personio_additional_offices_deduplicated_case_insensitively():
    """A duplicate spelling of the primary office must not repeat itself in the served string."""
    from headstart.scrapers.personio import _location

    pos = ET.fromstring(
        "<position><office>Berlin</office>"
        "<additionalOffices><office>berlin</office><office>Munich</office></additionalOffices>"
        "</position>"
    )
    assert _location(pos) == "Berlin, Munich"


def test_personio_no_additional_offices_leaves_location_as_the_bare_office():
    """No sibling element -> unchanged behaviour (the pre-fix case, still exercised)."""
    from headstart.scrapers.personio import _location

    assert (
        _location(ET.fromstring("<position><office>Munich</office></position>"))
        == "Munich"
    )
    assert _location(ET.fromstring("<position></position>")) is None


def test_personio_parse_reflects_the_joined_location():
    pos_xml = (
        "<position><id>1</id><office>Home Office</office>"
        "<additionalOffices><office>Bremen</office></additionalOffices>"
        "<name>Engineer</name></position>"
    )
    raw = ET.fromstring(f"<workzag-jobs>{pos_xml}</workzag-jobs>")
    jobs = get_scraper("personio", "acme.jobs.personio.de", "Acme").parse(
        raw, SCRAPED_AT
    )
    assert jobs[0].location == "Home Office, Bremen"


def test_personio_experience_prefers_the_native_years_range_over_seniority():
    """Real, live 2026-08-25: personio's own `<seniority>` is a coarse 4-value enum populated on
    99%+ of positions, so `seniority or yearsOfExperience` wins the `or` chain almost every time
    and discards a real numeric range. `yearsOfExperience` must win whenever it actually parses."""
    from headstart.scrapers.personio import _experience

    pos = ET.fromstring(
        "<position><seniority>experienced</seniority>"
        "<yearsOfExperience>1-2</yearsOfExperience></position>"
    )
    assert _experience(pos) == "1-2"


def test_personio_experience_falls_back_to_seniority_when_the_range_cannot_parse():
    """personio's own open-ended spellings ("lt-1", "gt-15") do not match `from_field`'s regex
    (it requires a leading digit). A naive swap would lose these ~1,000 positions to `None`; the
    fallback must keep serving the seniority-based floor instead."""
    from headstart.scrapers.personio import _experience

    pos = ET.fromstring(
        "<position><seniority>entry-level</seniority>"
        "<yearsOfExperience>lt-1</yearsOfExperience></position>"
    )
    assert _experience(pos) == "entry-level"


def test_personio_experience_falls_back_when_years_field_is_absent():
    from headstart.scrapers.personio import _experience

    assert _experience(
        ET.fromstring("<position><seniority>student</seniority></position>")
    ) == ("student")
    assert _experience(ET.fromstring("<position></position>")) is None


def test_personio_parse_reflects_the_years_range_preference():
    pos_xml = (
        "<position><id>1</id><office>Berlin</office><name>Engineer</name>"
        "<seniority>experienced</seniority><yearsOfExperience>1-2</yearsOfExperience></position>"
    )
    raw = ET.fromstring(f"<workzag-jobs>{pos_xml}</workzag-jobs>")
    jobs = get_scraper("personio", "acme.jobs.personio.de", "Acme").parse(
        raw, SCRAPED_AT
    )
    # Through the real cascade: "experienced" alone floors at 5; the native "1-2" range must win.
    from headstart.experience import extract

    span = extract(jobs[0].experience, jobs[0].description, jobs[0].title)
    assert span.min_years == 1
    assert span.max_years == 2
    assert span.source == "field"


def _personio_feed(*positions: str) -> str:
    return "<workzag-jobs>" + "".join(positions) + "</workzag-jobs>"


def _personio_position(jid: str, description: str | None) -> str:
    """One <position>; `description` None renders the empty <jobDescriptions /> personio serves
    for a posting that has no translation in the requested language."""
    block = (
        "<jobDescriptions />"
        if description is None
        else (
            "<jobDescriptions><jobDescription>"
            f"<value>{description}</value></jobDescription></jobDescriptions>"
        )
    )
    return (
        f"<position><id>{jid}</id><office>Berlin</office>"
        f"<name>Engineer {jid}</name>{block}</position>"
    )


def _personio_stub(
    monkeypatch, scraper, feeds: dict[str | None, str]
) -> list[str | None]:
    """Serve `feeds` keyed by the `?language=` code (None = the bare feed) and record the order
    the scraper asked in, so a test can assert on the request cost as well as the result.

    The two halves go out by different routes on purpose, and the stub mirrors that: since #313
    the **bare** feed is a direct `http.fetch` that refuses redirects (so an off-host Location can
    be read as gone), while the language variants still ride `_get`. Stubbing only `_get` would
    leave the bare fetch live."""
    asked: list[str | None] = []

    class _Feed:
        status_code = 200
        headers: ClassVar[dict] = {}

        def __init__(self, text: str):
            self.text = text

        @staticmethod
        def raise_for_status():
            return None

    def _fetch(method, url, **kw):
        asked.append(None)
        if None not in feeds:
            raise AssertionError("unexpected bare-feed fetch")
        return _Feed(feeds[None])

    def _get(url=None):
        lang = url.split("?language=")[1] if url and "?language=" in url else None
        asked.append(lang)
        if lang not in feeds:
            raise AssertionError(f"unexpected language fetch: {lang}")
        return feeds[lang]

    monkeypatch.setattr(http, "fetch", _fetch)
    monkeypatch.setattr(scraper, "_get", _get)
    return asked


def _personio_crossed_feeds() -> dict[str | None, str]:
    """The two-feed scenario both halves of the safety property need: each feed describes exactly
    the position the other leaves empty. Position 1 is the gridx case (empty bare, English on
    `?language=en`), position 2 the interlead one (German bare, emptied by `?language=en`). One
    fixture, so "fills the gap" and "does not overwrite" are asserted against the same input.
    """
    return {
        None: _personio_feed(
            _personio_position("1", None), _personio_position("2", "German text")
        ),
        "en": _personio_feed(
            _personio_position("1", "English text"), _personio_position("2", None)
        ),
    }


def test_personio_fetch_raw_fills_an_empty_description_from_a_language_feed(
    monkeypatch,
):
    """Live-measured 2026-08-26 over 296 real Boards / 2,029 positions: personio's `/xml` serves
    each description only in the *requested* language, and the bare feed serves the tenant's
    default one. A posting authored in another language comes back as a self-closing
    `<jobDescriptions />` — real text, simply not in the language asked for. 9.41% of all
    positions and 22.41% of tech ones were empty this way, and every one of the 191 had a present
    `<jobDescriptions>` with zero children, so nothing was being mis-parsed.
    """
    s = get_scraper("personio", "gridx.jobs.personio.com", "gridX")
    asked = _personio_stub(monkeypatch, s, _personio_crossed_feeds())
    jobs = s.parse(s.fetch_raw(), SCRAPED_AT)
    by_id = {j.id.rsplit(":", 1)[1]: j for j in jobs}
    assert by_id["1"].description == "English text"  # recovered from ?language=en
    assert asked == [None, "en"]


def test_personio_language_feed_never_overwrites_a_description_the_bare_feed_had(
    monkeypatch,
):
    """The guard the measurement demands. Over the same 249-Board sample, switching wholesale to
    `?language=en` RECOVERED 133 descriptions but LOST 1,159 (101 tech) — the tenant default is
    German far more often than not, and asking for English empties those. So the language feeds
    may only *fill* what the bare feed left empty, never replace what it carried.
    """
    s = get_scraper("personio", "acme.jobs.personio.de", "Acme")
    _personio_stub(monkeypatch, s, _personio_crossed_feeds())
    jobs = s.parse(s.fetch_raw(), SCRAPED_AT)
    by_id = {j.id.rsplit(":", 1)[1]: j for j in jobs}
    assert by_id["2"].description == "German text"  # NOT emptied by the en feed


def test_personio_complete_bare_feed_costs_no_extra_request(monkeypatch):
    """Cost guard: ~80% of live Boards have no empty position at all (58 of 296 held one), and those
    must keep paying exactly one request."""
    s = get_scraper("personio", "acme.jobs.personio.de", "Acme")
    asked = _personio_stub(
        monkeypatch, s, {None: _personio_feed(_personio_position("1", "text"))}
    )
    s.fetch_raw()
    assert asked == [None]


def test_personio_language_sweep_stops_as_soon_as_every_position_is_filled(monkeypatch):
    """Early exit, so a board whose gap the first fallback closes does not pay for the rest."""
    s = get_scraper("personio", "acme.jobs.personio.de", "Acme")
    asked = _personio_stub(
        monkeypatch,
        s,
        {
            None: _personio_feed(_personio_position("1", None)),
            "en": _personio_feed(_personio_position("1", "English text")),
        },
    )
    s.fetch_raw()
    assert asked == [None, "en"]


def test_personio_language_sweep_is_bounded_when_no_variant_closes_the_gap(monkeypatch):
    """The worst case, and it must stay bounded. 4 of the 191 empty positions in the 296-Board
    sweep are empty in *every* language variant — their text is only on the HTML job page's
    JSON-LD — so those Boards pay the whole list and recover nothing. The seed-31337 sample turned
    up the same shape live (2026-08-26): `albaberlin.jobs.personio.com`, 1 of 7 positions empty in
    the bare feed and in all of en/es/nl/fr, 5 requests total. The cost ceiling is the length of
    `_DESCRIPTION_LANGUAGES`; a position that is never filled must not make the scraper ask again,
    or retry, or give up on the descriptions the bare feed did carry.
    """
    from headstart.scrapers.personio import _DESCRIPTION_LANGUAGES

    s = get_scraper("personio", "albaberlin.jobs.personio.com", "Alba")
    stubborn = _personio_feed(
        _personio_position("1", None), _personio_position("2", "German text")
    )
    asked = _personio_stub(
        monkeypatch, s, dict.fromkeys([None, *_DESCRIPTION_LANGUAGES], stubborn)
    )
    jobs = s.parse(s.fetch_raw(), SCRAPED_AT)
    assert asked == [None, *_DESCRIPTION_LANGUAGES]  # every code tried, exactly once
    assert (
        len(_DESCRIPTION_LANGUAGES) <= 4
    )  # ceiling: at most four extra requests, ever
    by_id = {j.id.rsplit(":", 1)[1]: j for j in jobs}
    assert by_id["1"].description is None  # unrecoverable, and reported as such
    assert by_id["2"].description == "German text"  # the rest of the Board is untouched


def test_personio_language_sweep_survives_a_failing_variant(monkeypatch):
    """A language variant that errors must not lose the bare feed. Live, `?language=` with an
    unknown code answers 200 with every description emptied, so a failure here is the network's,
    and the positions the bare feed did carry are still worth returning."""
    s = get_scraper("personio", "acme.jobs.personio.de", "Acme")

    # The bare feed is a direct `http.fetch` since #313; stubbing only `_get` would leave it live.
    class _Feed:
        status_code = 200
        headers: ClassVar[dict] = {}
        text = _personio_feed(_personio_position("1", None))

        @staticmethod
        def raise_for_status():
            return None

    def _get(url=None):
        if url and "?language=en" in url:
            raise RuntimeError("boom")
        if url and "?language=" in url:
            return _personio_feed(_personio_position("1", "Spanish text"))
        raise AssertionError("the bare feed must not go through _get")

    monkeypatch.setattr(http, "fetch", lambda method, url, **kw: _Feed())
    monkeypatch.setattr(s, "_get", _get)
    jobs = s.parse(s.fetch_raw(), SCRAPED_AT)
    assert jobs[0].description == "Spanish text"


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
    assert j.employment_type  # employmentType.label
    assert j.posted_at  # createdOn from the detail fetch
    assert j.description and "</" not in j.description  # populated, HTML-stripped


def test_rippling_employment_type_reads_label_not_id():
    """employmentType.label is a clean 6-value enum (SALARIED_FT, HOURLY_FT, ...);
    .id is tenant free text (347 distinct spellings measured live, 130 of them
    singletons — docs/salary-extraction/rippling.md). Falls back to .id when .label
    is null (where genuinely non-enum values like "Seasonal" live) rather than
    losing the field entirely."""
    raw = [
        {
            "uuid": "a1",
            "name": "Engineer",
            "url": "https://ats.rippling.com/acme/jobs/a1",
            "_detail": {
                "employmentType": {
                    "label": "SALARIED_FT",
                    "id": "Salaried, Full-Time (US)",
                },
            },
        },
        {
            "uuid": "a2",
            "name": "Seasonal Associate",
            "url": "https://ats.rippling.com/acme/jobs/a2",
            "_detail": {
                "employmentType": {"label": None, "id": "Seasonal"},
            },
        },
    ]
    jobs = get_scraper("rippling", "acme", "Acme").parse(raw, SCRAPED_AT)
    assert jobs[0].employment_type == "SALARIED_FT"
    assert jobs[1].employment_type == "Seasonal"  # label null -> falls back to id


def test_rippling_pay_range_unions_all_entries():
    """payRangeDetails can carry more than one band (per-level/per-region); entry [0]
    alone understates the true span when a later entry carries a wider range — e.g.
    cat5-resources-llc serves '25-27 USD HOUR' from entry [0] while the real span
    across all entries (Level 1-4) is 25-40 (live measurement,
    docs/salary-extraction/rippling.md)."""
    from headstart.scrapers.rippling import _pay_range

    ranges = [
        {
            "rangeStart": 25.0,
            "rangeEnd": 27.0,
            "currency": "USD",
            "frequency": "HOUR",
            "location": "Level 1",
        },
        {
            "rangeStart": 30.0,
            "rangeEnd": 35.0,
            "currency": "USD",
            "frequency": "HOUR",
            "location": "Level 2",
        },
        {
            "rangeStart": 35.0,
            "rangeEnd": 40.0,
            "currency": "USD",
            "frequency": "HOUR",
            "location": "Level 4",
        },
    ]
    assert _pay_range(ranges) == "25-40 USD HOUR"
    # entry [0] alone would report "25-27 USD HOUR" — confirm the fix reads the true
    # min/max across the whole array, not just the first entry.
    assert _pay_range(ranges[:1]) == "25-27 USD HOUR"


def test_rippling_pay_range_does_not_blend_mismatched_currency():
    """Found in review, live: a real job (journaltech) carries three USD/YEAR entries
    alongside one CAD/YEAR entry. Pooling raw numbers across all entries regardless of unit
    mislabeled the CAD figure as USD — '155000-200000 USD YEAR' instead of the true USD-only
    span. Entries outside the majority (currency, frequency) must be excluded, not blended."""
    from headstart.scrapers.rippling import _pay_range

    ranges = [
        {
            "rangeStart": 160000,
            "rangeEnd": 180000,
            "currency": "USD",
            "frequency": "YEAR",
        },
        {
            "rangeStart": 180000,
            "rangeEnd": 200000,
            "currency": "USD",
            "frequency": "YEAR",
        },
        {
            "rangeStart": 160000,
            "rangeEnd": 190000,
            "currency": "USD",
            "frequency": "YEAR",
        },
        {
            "rangeStart": 155000,
            "rangeEnd": 190000,
            "currency": "CAD",
            "frequency": "YEAR",
        },
    ]
    assert _pay_range(ranges) == "160000-200000 USD YEAR"


def test_rippling_pay_range_keeps_a_zero_floor():
    """rangeStart/rangeEnd must be checked with `is not None`, not truthiness — the same class
    of bug ashby's `_salary` docstring documents (a real Ramp job with minValue=0)."""
    from headstart.scrapers.rippling import _pay_range

    ranges = [
        {"rangeStart": 0, "rangeEnd": 50000, "currency": "USD", "frequency": "HOUR"}
    ]
    assert _pay_range(ranges) == "0-50000 USD HOUR"


def test_rippling_pay_range_majority_unit_wins_regardless_of_position():
    """The (currency, frequency) group anchored is whichever the MOST entries share, not
    positionally entry [0]'s unit — so a minority-currency entry the API happens to list first
    can't narrow the reported range to just that outlier. Same journaltech-shaped mix as
    test_rippling_pay_range_does_not_blend_mismatched_currency, but with the lone CAD entry
    moved to position 0: entry-[0]-anchored code would report "155000-190000 CAD YEAR"."""
    from headstart.scrapers.rippling import _pay_range

    ranges = [
        {
            "rangeStart": 155000,
            "rangeEnd": 190000,
            "currency": "CAD",
            "frequency": "YEAR",
        },
        {
            "rangeStart": 160000,
            "rangeEnd": 180000,
            "currency": "USD",
            "frequency": "YEAR",
        },
        {
            "rangeStart": 180000,
            "rangeEnd": 200000,
            "currency": "USD",
            "frequency": "YEAR",
        },
        {
            "rangeStart": 160000,
            "rangeEnd": 190000,
            "currency": "USD",
            "frequency": "YEAR",
        },
    ]
    assert _pay_range(ranges) == "160000-200000 USD YEAR"


def test_rippling_employment_type_empty_label_does_not_fall_back():
    """`.label` is checked with `is not None`, not truthiness — the same class of bug
    `_pay_range` fixes for rangeStart/rangeEnd. A present-but-empty label (never observed
    live, but not ruled out by the API) must be kept, not silently replaced by `.id`."""
    raw = [
        {
            "uuid": "a3",
            "name": "Contractor",
            "url": "https://ats.rippling.com/acme/jobs/a3",
            "_detail": {
                "employmentType": {"label": "", "id": "Contractor (1099)"},
            },
        },
    ]
    jobs = get_scraper("rippling", "acme", "Acme").parse(raw, SCRAPED_AT)
    assert jobs[0].employment_type == ""


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


def test_darwinbox_salary_range_not_double_suffixed():
    # Real bug, salary-extraction pass 2026-08-22: `salary_range` already carries its own
    # "(Annual)"/"(Monthly)" suffix whenever one exists (confirmed: 1,874/1,874 real suffixed
    # values in a 290-board sample), so appending `salary_timeframe` on top only ever duplicated
    # it ("INR 3 - 5 (Annual) (Annual)", ADR-0019's own documented example) — never added info.
    raw = [
        {
            "id": "abc123",
            "title": "Test Role",
            "salary_range": "INR 600000 - 1000000 (Annual)",
            "salary_timeframe": "Annual",
        }
    ]
    jobs = get_scraper("darwinbox", "yesforyou", "Yes For You").parse(raw, SCRAPED_AT)
    assert jobs[0].salary == "INR 600000 - 1000000 (Annual)"


def test_darwinbox_salary_range_without_timeframe_unaffected():
    # salary_timeframe is null whenever salary_range has no suffix baked in (confirmed: real,
    # zero counterexamples) — the field carries nothing the string doesn't already have.
    raw = [
        {
            "id": "abc124",
            "title": "Test Role",
            "salary_range": "INR 250000 - 400000",
            "salary_timeframe": None,
        }
    ]
    jobs = get_scraper("darwinbox", "disha", "Disha").parse(raw, SCRAPED_AT)
    assert jobs[0].salary == "INR 250000 - 400000"


def test_darwinbox_single_location_strips_an_embedded_carriage_return():
    # Real bug found in a location-field audit, 2026-08-24: the raw `locations` string ships a
    # literal \r right before its comma on some tenants ("Maharashtra\r, India" — 31/67 sampled
    # jobs on one board, 47.45% across the full live population). The multi-location branch
    # already strips each `tool_tip_locations` part; the single-location branch took the raw
    # string with zero cleaning.
    raw = [
        {
            "id": "abc125",
            "title": "Test Role",
            "locations": "Jhagadia, Gujarat\r, India",
            "tool_tip_locations": [],
        }
    ]
    jobs = get_scraper("darwinbox", "aartiindustries", "Aarti").parse(raw, SCRAPED_AT)
    assert jobs[0].location == "Jhagadia, Gujarat, India"


def test_darwinbox_single_location_drops_empty_comma_segments():
    # Same fix, a second real shape found in the same full-population verification: some raw
    # strings carry a leading or doubled comma ("` , Makati, ...`", "`Serrano Ave,, San Juan...`")
    # from an empty office/building field upstream — the comma-split-and-filter approach that
    # fixes the \r also drops these for free, since an empty segment is just as falsy as one that
    # was only whitespace.
    raw = [
        {"id": "l1", "title": "T", "locations": " , Makati, Metro Manila, Philippines"},
        {"id": "l2", "title": "T", "locations": "Serrano Ave,, San Juan, Metro Manila"},
    ]
    jobs = get_scraper("darwinbox", "synergymarinegroup", "Synergy").parse(
        raw, SCRAPED_AT
    )
    assert jobs[0].location == "Makati, Metro Manila, Philippines"
    assert jobs[1].location == "Serrano Ave, San Juan, Metro Manila"


def test_darwinbox_single_location_none_stays_none():
    raw = [{"id": "abc126", "title": "Test Role", "locations": None}]
    jobs = get_scraper("darwinbox", "someco", "SomeCo").parse(raw, SCRAPED_AT)
    assert jobs[0].location is None


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


def test_successfactors_location_from_slug_recovers_the_prefix():
    # Real gap found in a location-field audit, 2026-08-24: some CSB tenants' job pages carry
    # no location markup anywhere — not JSON-LD, not itemprop, not a joblayouttoken label — yet
    # the URL SuccessFactors itself generated still has it: careers.gallo.com's real live page
    # for this exact title.
    from headstart.scrapers.successfactors import _location_from_slug

    url = "https://careers.gallo.com/job/Charlotte-Account-Manager-Customer-Development-NC-28277/1407690100/"
    assert (
        _location_from_slug("Account Manager - Customer Development", url)
        == "Charlotte"
    )


@pytest.mark.parametrize(
    ("title", "slug", "expected"),
    [
        # "(m/w/d)" -> "(mwd)": three title words glued into one slug token.
        (
            "Finanzierungsberater Baufinanzierungen (m/w/d)",
            "Berlin-Finanzierungsberater-Baufinanzierungen-%28mwd%29-BE",
            "Berlin",
        ),
        # "Projektcontroller/Finance" -> "ProjektcontrollerFinance".
        (
            "Projektcontroller/Finance Partner",
            "Berlin-ProjektcontrollerFinance-Partner-BE-10117",
            "Berlin",
        ),
        # "Werkstudent*in" -> "Werkstudentin".
        (
            "Werkstudent*in IT Compliance",
            "Berlin-Werkstudentin-IT-Compliance-BE-10117",
            "Berlin",
        ),
        # A two-word place survives the same path.
        (
            "Immobilienmakler (m/w/d)",
            "Gera-Immobilienmakler-%28mwd%29-TH-07545",
            "Gera",
        ),
    ],
)
def test_successfactors_location_from_slug_spans_the_encoders_glued_words(
    title, slug, expected
):
    """SuccessFactors's slug encoder drops punctuation without a separator, gluing two title
    words into one slug token. A token-sequence match can never span that, so these postings
    yielded no location at all despite the slug plainly carrying one — 13 of 13 nulls on
    jobs.dkb.de, measured 2026-08-25.
    """
    from headstart.scrapers.successfactors import _location_from_slug

    assert _location_from_slug(title, f"https://jobs.dkb.de/job/{slug}/1234567/") == (
        expected
    )


def test_successfactors_location_from_slug_does_not_truncate_on_a_prefix_token():
    """`str.find` takes the FIRST occurrence, so a title whose concatenation is a prefix of a
    longer slug token would match there instead of at its real position and cut the location
    short — "Sales Rep" against `Berlin-Salesrepublic-Sales-Rep` giving "Berlin" when the whole
    prefix is "Berlin Salesrepublic". Found in review; anchoring the match's END to a token
    boundary as well as its start is what refuses it.
    """
    from headstart.scrapers.successfactors import _location_from_slug

    url = "https://x/job/Berlin-Salesrepublic-Sales-Rep/1234567/"
    assert _location_from_slug("Sales Rep", url) == "Berlin Salesrepublic"


def test_successfactors_location_from_slug_needs_a_whole_token_boundary():
    """Matching on concatenated words must not let a title start mid-token.

    "Onsite" contains "site", so a substring match alone would split the token and report a
    location of "Berlin On" — a fabricated place. The run has to begin where a slug token does.
    """
    from headstart.scrapers.successfactors import _location_from_slug

    url = "https://x/job/Berlin-Onsite-Engineer-BE/1234567/"
    assert _location_from_slug("site Engineer", url) is None


def test_successfactors_location_falls_back_to_country_when_nothing_else_exists():
    """Last tier, country grain: some tenants render no location markup and put none in the URL
    either, leaving `streetAddress` as the only geography on the page (careers.theredsea.sa —
    51 of 70 residual nulls in a 14-board sample). Coarse, but a real place that filters."""
    from headstart.scrapers.successfactors import _page_fields

    page = (
        '<html><span data-careersite-propertyid="title">Divemaster</span>'
        '<meta itemprop="streetAddress" content="SA">2</html>'
    )
    url = "https://careers.theredsea.sa/job/Divemaster/857326923/"
    assert _page_fields(page, url)["location"] == "SA"


def test_successfactors_location_from_street_address_drops_a_leaked_url():
    """A tenant's own data can leak a URL into `streetAddress` — careers.wataniaind.com serves
    `content="SA, https://ma"` (its job titles carry the same fragment), 1 of 12 non-empty
    values in a 22-tenant sample. Drop the bad segment, keep the real place."""
    from headstart.scrapers.successfactors import _location_from_street_address

    assert (
        _location_from_street_address(
            '<meta itemprop="streetAddress" content="SA, https://ma">'
        )
        == "SA"
    )
    # A place at whatever grain the tenant configured survives intact.
    assert (
        _location_from_street_address(
            '<meta itemprop="streetAddress" content="Kuala Lumpur, MY, 50450">'
        )
        == "Kuala Lumpur, MY, 50450"
    )
    # Nothing left once the junk is gone is None, not an empty string.
    assert _location_from_street_address(
        '<meta itemprop="streetAddress" content="https://x">'
    ) is (None)
    assert _location_from_street_address("<html>no meta</html>") is None


def test_successfactors_country_tier_never_outranks_a_real_place():
    """The country meta is last for a reason — a finer tier must always win."""
    from headstart.scrapers.successfactors import _page_fields

    page = (
        '<html><span data-careersite-propertyid="title">Analyst</span>'
        '<span data-careersite-propertyid="location">Guadalajara, Jalisco</span>'
        '<meta itemprop="streetAddress" content="MX">2</html>'
    )
    assert _page_fields(page, "https://x/job/Analyst/1/")["location"] == (
        "Guadalajara, Jalisco"
    )


def test_successfactors_location_from_slug_ignores_a_trailing_req_id():
    # The dangerous direction: a title-only slug leaves a bare requisition number after it
    # ("Foshan-City-Sr-Technician-528513" for a title of just "Sr Technician"). Appending it as
    # part of the location would fabricate "Gaoming District Foshan City 528513" — a real live
    # example (careers.gallo.com sibling tenant). Only the prefix is ever trusted.
    from headstart.scrapers.successfactors import _location_from_slug

    url = (
        "https://x/job/Gaoming-District%2C-Foshan-City-Sr-Technician-528513/1368205300/"
    )
    assert _location_from_slug("Sr Technician", url) == "Gaoming District Foshan City"


def test_successfactors_location_from_slug_none_when_slug_is_the_title_verbatim():
    # careers.ijm.com: real live tenant whose job URLs are the bare title with no location
    # component at all. Must return None, not a guess built from stray title punctuation.
    from headstart.scrapers.successfactors import _location_from_slug

    url = "https://careers.ijm.com/job/ENGINEER,-PLANNING/945286110/"
    assert _location_from_slug("ENGINEER, PLANNING", url) is None


def test_successfactors_location_from_slug_title_with_an_encoded_slash_fails_safe():
    # A title containing a literal "/" (percent-encoded %2F in the real URL) decodes BEFORE the
    # path is split on "/", so it fragments the slug into an extra path segment and the id/slug
    # segments no longer line up as expected. Found in code review, round 1 — confirmed here to
    # degrade to a safe None rather than a wrong location: the token match then fails against a
    # misaligned segment, which is the same fail-safe path a punctuation mismatch takes.
    from headstart.scrapers.successfactors import _location_from_slug

    url = "https://x/job/Charlotte-IT%2FOT-Engineer-NC-28277/1234567/"
    assert _location_from_slug("IT/OT Engineer", url) is None


def test_successfactors_location_from_slug_repeated_place_name_in_title():
    # tuyendung.vietcombank.com.vn: the location text ("Bình Dương") appears a SECOND time
    # embedded inside the title's own bracketed code. Exact contiguous-match on the title's full
    # token sequence still isolates the true prefix correctly rather than getting confused by
    # the repeat.
    from headstart.scrapers.successfactors import _location_from_slug

    title = "[II.2026_Nam Bình Dương] CV khách hàng (kinh nghiệm)"
    url = (
        "https://tuyendung.vietcombank.com.vn/job/"
        "B%C3%ACnh-D%C6%B0%C6%A1ng-II_2026_Nam-B%C3%ACnh-D%C6%B0%C6%A1ng-"
        "CV-kh%C3%A1ch-h%C3%A0ng-(kinh-nghi%E1%BB%87m)/55551544/"
    )
    assert _location_from_slug(title, url) == "Bình Dương"


def test_successfactors_page_fields_uses_slug_only_as_the_last_resort():
    # Integration: the new tier must never fire when JSON-LD or CSB markup already answered —
    # gated behind `not fields.get("location")` in _page_fields, checked here rather than only
    # read off the source, since a gating bug would silently overwrite a page's real answer with
    # a slug guess on every tenant that has proper markup.
    from headstart.scrapers.successfactors import _page_fields

    page = """<html><head><script type="application/ld+json">
    {"@context": "http://schema.org", "@type": "JobPosting", "title": "Engineer",
    "jobLocation": {"address": {"addressLocality": "Berlin"}}}
    </script></head><body></body></html>"""
    url = "https://x/job/Munich-Engineer/1/"  # slug disagrees with the real JSON-LD answer
    fields = _page_fields(page, url)
    assert fields["location"] == "Berlin"


def test_successfactors_page_fields_falls_through_to_slug_when_page_has_no_markup():
    from headstart.scrapers.successfactors import _page_fields

    # og:title-only page, no JSON-LD, no CSB microdata/labels — the Southco shape (measured live,
    # 2026-08-24: location appears nowhere in the page body, only in the URL the platform built).
    page = (
        "<html><head><title>Manager I - Engineering Program Job Details | Southco</title>"
        '<meta property="og:title" content="Manager I - Engineering Program" /></head>'
        "<body></body></html>"
    )
    url = "https://x/job/Concordville-Manager-I-Engineering-Program-PA-19331-0116/1384635700/"
    fields = _page_fields(page, url)
    assert fields["title"] == "Manager I - Engineering Program"
    assert fields["location"] == "Concordville"


def test_successfactors_page_fields_no_url_skips_the_slug_tier():
    # Every existing caller of _page_fields (three above) has no URL and must keep working
    # unchanged — the parameter is optional precisely so they don't need touching.
    from headstart.scrapers.successfactors import _page_fields

    page = (
        "<html><head><title>Manager I - Engineering Program Job Details | Southco</title>"
        "</head><body></body></html>"
    )
    fields = _page_fields(page)
    assert fields["location"] is None


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


def test_eightfold_remote_from_covers_the_live_vocabulary():
    """Live vocabulary measured 2026-08-25 across 44,215 jobs/62 boards is exactly these four
    values — remote_local/remote_global previously matched nothing in `_REMOTE_OPTION`, so 999
    jobs (2.26%) the API explicitly flags remote were served `remote=False`."""
    from headstart.scrapers.eightfold import _remote_from

    assert _remote_from("onsite") is False
    assert _remote_from("hybrid") is None  # tri-state: neither remote nor onsite
    assert _remote_from("remote_local") is True
    assert _remote_from("remote_global") is True


def test_eightfold_first_location_skips_a_blank_leading_entry():
    """ascendion.eightfold.ai ships `locations[0] == ""` with real cities after it — the fix
    takes the first *non-empty* entry rather than always index 0."""
    from headstart.scrapers.eightfold import _first_location

    assert _first_location(["", "bangalore", "hyderabad", "pune"]) == "bangalore"


def test_eightfold_first_location_repairs_a_site_code():
    """`US-CA-Fremont (1003)` is an internal site code, not a place name — repaired from the
    index-matched `standardizedLocations` entry (measured live on lamresearch)."""
    from headstart.scrapers.eightfold import _first_location

    assert (
        _first_location(["US-CA-Fremont (1003)"], ["Fremont, CA, US"])
        == "Fremont, CA, US"
    )


def test_eightfold_first_location_repairs_an_empty_comma_segment():
    """astrazeneca.eightfold.ai's `"Riyadh, , Saudi Arabia"` shape — same defect class
    darwinbox was fixed for on 2026-08-24 (keka's fix that day was the neighboring
    dirty-whitespace shape, not an empty segment)."""
    from headstart.scrapers.eightfold import _first_location

    assert (
        _first_location(["Riyadh, , Saudi Arabia"], ["Riyadh, Riyadh Province, SA"])
        == "Riyadh, Riyadh Province, SA"
    )


def test_eightfold_first_location_is_a_repair_tier_not_a_wholesale_swap():
    """A clean `locations[0]` is left exactly as it is, even when `standardizedLocations` differs
    — this is the central distinction from a blanket swap, which the audit measured costs India
    matches on some boards and collapses 3.91% of jobs to a bare country code."""
    from headstart.scrapers.eightfold import _first_location

    assert (
        _first_location(["Bengaluru, Karnataka, India"], ["Bengaluru, KA, IN"])
        == "Bengaluru, Karnataka, India"
    )


def test_eightfold_first_location_repair_rejects_a_bare_country_code():
    """`'SG-Singapore (3301)'` -> `'SG'` measured live on lamresearch: the repair would collapse
    a city-state's only place name to its bare country code — a real information loss, so the
    dirty original is kept instead."""
    from headstart.scrapers.eightfold import _first_location

    assert _first_location(["SG-Singapore (3301)"], ["SG"]) == "SG-Singapore (3301)"


def test_eightfold_first_location_repair_rejects_a_still_site_code_shaped_value():
    """lamresearch's `standardizedLocations` sometimes just lowercases the same site code instead
    of translating it (`'KR-Yongin-02 (3821)'` -> `'kr-yongin-02 (3821)'`) — not a real repair."""
    from headstart.scrapers.eightfold import _first_location

    assert (
        _first_location(["KR-Yongin-02 (3821)"], ["kr-yongin-02 (3821)"])
        == "KR-Yongin-02 (3821)"
    )


def test_eightfold_first_location_repair_rejects_a_country_mismatch():
    """Measured live: every `'MY-LMM KM [3620] (3832)'` posting on lamresearch carries
    `standardizedLocations: ['Lancaster, VIC, AU']` — a bad tenant-side site mapping that would
    swap Malaysia for Australia. The site code's own 2-letter prefix disagreeing with the
    repair's country is the tell."""
    from headstart.scrapers.eightfold import _first_location

    assert (
        _first_location(["MY-LMM KM [3620] (3832)"], ["Lancaster, VIC, AU"])
        == "MY-LMM KM [3620] (3832)"
    )


def test_eightfold_first_location_repair_uses_the_index_matched_standardized_entry():
    """`locations`/`standardizedLocations` are parallel arrays (measured live: same length on
    10,694/10,694 jobs where both are present) — a dirty entry at index 1 must repair from
    `standardizedLocations[1]`, not `[0]`."""
    from headstart.scrapers.eightfold import _first_location

    assert (
        _first_location(["", "US-CA-Fremont (1003)"], ["", "Fremont, CA, US"])
        == "Fremont, CA, US"
    )


def test_eightfold_api_records_wires_the_remote_and_location_fixes(monkeypatch):
    """Integration: the fixes reach `_api_records`'s built fields, not just the pure helpers."""
    from headstart.scrapers.registry import get_scraper

    scraper = get_scraper("eightfold", "acme.eightfold.ai", "Acme")
    monkeypatch.setattr(
        scraper, "fan_out_async", lambda items, fn, **kw: [None] * len(items)
    )
    positions = [
        {
            "id": "1",
            "name": "Remote Engineer",
            "workLocationOption": "remote_local",
            "locations": ["Bangalore, Karnataka, India"],
        },
        {
            "id": "2",
            "name": "Onsite Engineer",
            "workLocationOption": "onsite",
            "locations": ["US-CA-Fremont (1003)"],
            "standardizedLocations": ["Fremont, CA, US"],
        },
    ]
    records = scraper._api_records("acme.com", positions)
    by_id = {r["id"]: r["fields"] for r in records}
    assert (
        by_id["1"]["remote"] is True
    )  # remote_local now resolves, was False before the fix
    assert by_id["2"]["location"] == "Fremont, CA, US"  # site code repaired


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
    # startrow 1, not 25: the walk steps by the page it got (one posting here). It used to step
    # by the larger of that and a 25-row floor, which is the overshoot that silently skipped rows
    # on every tenant paging under the floor — the offset reported here moved with that fix.
    assert why and "503" in why and "startrow 1" in why
    assert scraper.truncated is None  # the caller decides, not the walk


def test_successfactors_search_walk_reports_its_page_ceiling(monkeypatch):
    """Exhausting _MAX_SEARCH_PAGES is a knowingly short list, and must say so (ADR-0053).

    It used to fall out of the loop returning ``cut_short=None`` — indistinguishable from a walk
    that reached the end — so a capped Board read as complete and `index sync` evicted whatever
    sat past the ceiling. Eightfold and Workday both mark their equivalent caps.
    """
    from headstart.scrapers import successfactors as sf

    monkeypatch.setattr(sf, "_MAX_SEARCH_PAGES", 3)
    scraper = sf.SuccessFactorsScraper("jobs.example.com")
    n = iter(range(100))
    # every page yields a fresh posting, so the walk never reaches its natural end
    monkeypatch.setattr(
        sf.http,
        "fetch",
        lambda *a, **k: _SearchPage(200, f'<a href="/job/x/{next(n)}/">a</a>'),
    )

    found, why = scraper._search_job_urls()

    assert len(found) == 3
    assert why and "ceiling" in why
    assert (
        scraper.truncated is None
    )  # the caller decides which surface answers, not the walk


def test_successfactors_search_walk_reads_every_row_of_a_small_page(monkeypatch):
    """The walk must step by the page it actually got, not by a floor that overshoots it.

    Stepping by `max(len(found), 25)` skips rows whenever a tenant's page holds
    fewer than the floor. Measured live: `jobs.chartindustries.com` serves 10 rows a page and
    advertises 219 postings, and the walk returned 90 — rows 0-9, 25-34, 50-59 ... with the 15
    rows between each window never read. `jobs.bayer.com` (also 10/page) returned 241 of 601.
    Every sampled board with a page under 25 was short and every board at or above it was whole,
    which is the floor and nothing else. Nothing marked it: the walk runs off the end of the
    board, sees no fresh ids, and exits by the natural-end path with `cut_short=None`, so a
    Board missing 59% of its postings reads as complete and `index sync` evicts the difference.
    """
    from headstart.scrapers import successfactors as sf

    page, total = (
        10,
        25,
    )  # a page smaller than the old 25-row floor, as chartindustries is

    def _serve(method, url, **kw):
        startrow = int(url.rsplit("startrow=", 1)[1])
        rows = range(startrow, min(startrow + page, total))
        return _SearchPage(200, "".join(f'<a href="/job/x/{i}/">a</a>' for i in rows))

    monkeypatch.setattr(sf.http, "fetch", _serve)
    scraper = sf.SuccessFactorsScraper("jobs.example.com")

    found, why = scraper._search_job_urls()

    assert {i for _u, i in found} == {str(i) for i in range(total)}, (
        f"read {len(found)} of {total} postings — the step overshot the page and skipped rows"
    )
    assert why is None, "it did reach the end, so there is nothing to report"


def _labelled_search_page(rows, total, label="Results", connector="of", extras=()):
    """A /search/ page carrying the pagination label the walk reads its yardstick from.

    ``extras`` are job links that are *not* results of this page — the shape jobs.kaufland.com
    renders, where 4 recurring links sit alongside the 15 rows the label counts."""
    rows = list(rows)
    body = "".join(f'<a href="/job/x/{i}/">a</a>' for i in [*rows, *extras])
    last = len(rows)
    return (
        f'<span class="paginationLabel">{label} <b>1 \u2013 {last}</b> '
        f"{connector} <b>{total}</b></span>{body}"
    )


def test_successfactors_reports_reading_fewer_than_the_board_advertises(monkeypatch):
    """Reaching the natural end is not proof the walk read everything — the stride bug exited by
    exactly that path. The board states its own total, so a shortfall must be reported through
    the ADR-0053 channel rather than presented as the whole Board."""
    from headstart.scrapers import successfactors as sf

    # the board says 40, but only ever serves the first 10 and then nothing
    def _serve(method, url, **kw):
        startrow = int(url.rsplit("startrow=", 1)[1])
        rows = range(startrow, min(startrow + 10, 10))
        return _SearchPage(200, _labelled_search_page(rows, 40))

    monkeypatch.setattr(sf.http, "fetch", _serve)

    found, why = sf.SuccessFactorsScraper("jobs.example.com")._search_job_urls()

    assert len(found) == 10
    assert why and "10 of the 40" in why


def test_successfactors_makes_no_completeness_claim_without_a_label(monkeypatch):
    """An unknown total must never become a zero: a wrongly-inferred shortfall marks the Board
    unauthoritative and removes it from the eviction scope entirely, so closed postings would be
    served indefinitely. A board that states no total is simply not checked."""
    from headstart.scrapers import successfactors as sf

    def _serve(method, url, **kw):
        startrow = int(url.rsplit("startrow=", 1)[1])
        rows = range(startrow, min(startrow + 10, 10))
        return _SearchPage(200, "".join(f'<a href="/job/x/{i}/">a</a>' for i in rows))

    monkeypatch.setattr(sf.http, "fetch", _serve)

    found, why = sf.SuccessFactorsScraper("jobs.example.com")._search_job_urls()

    assert len(found) == 10
    assert why is None, "no total advertised, so nothing to compare against"


@pytest.mark.parametrize(
    ("label", "connector"),
    [("Results", "of"), ("Ergebnisse", "von"), ("Resultados", "de")],
)
def test_successfactors_reads_the_total_in_any_locale(label, connector):
    """The label's wording is localised per tenant — measured live on career.deutz.com (German)
    and canaldeempleo.es (Spanish). Matching on the English "of" read every other board as having
    no total, silently disabling the check exactly where boards are largest."""
    from headstart.scrapers.successfactors import _advertised_paging

    page = _labelled_search_page(range(10), 219, label, connector)
    assert _advertised_paging(page) == (10, 219)


def test_successfactors_walks_a_board_that_renders_more_links_than_it_lists(
    monkeypatch,
):
    """jobs.kaufland.com labels 15 results and renders 19 job links — 4 recurring extras that are
    not rows of this page. Stepping by the link count skips 4 rows of every window, the stride bug
    wearing a different disguise, so the board's own stated page size wins wherever it gives one.

    Driven through the walk rather than the parser: a parser-level assertion passes just as well
    with the stride reverted to `len(found)`, which is the bug. Here rows 15-18 go missing if it
    is."""
    from headstart.scrapers import successfactors as sf

    board, page, extras = 45, 15, [9001, 9002, 9003, 9004]

    def _serve(method, url, **kw):
        startrow = int(url.rsplit("startrow=", 1)[1])
        rows = range(startrow, min(startrow + page, board))
        return _SearchPage(200, _labelled_search_page(rows, board, extras=extras))

    monkeypatch.setattr(sf.http, "fetch", _serve)

    found, why = sf.SuccessFactorsScraper("jobs.example.com")._search_job_urls()
    ids = {i for _u, i in found}

    assert {str(i) for i in range(board)} <= ids, "a window's worth of rows went unread"
    assert why is None, "the whole board was read, so nothing was cut short"


def test_successfactors_ignores_numbers_outside_the_pagination_label():
    """An unrecognised label must yield nothing rather than a number scavenged from elsewhere on
    the page. A too-high total reads as a shortfall, which marks the Board unauthoritative and
    drops it from the eviction scope entirely (ADR-0053) — closed postings then served forever.
    So the figures are matched only within the label element."""
    from headstart.scrapers.successfactors import _advertised_paging

    stray = (
        '<span class="paginationLabel">Results <b>1-10</b></span>'
        "<div>see <b>note</b> and <b>12</b></div>"
    )
    assert _advertised_paging(stray) is None


def test_successfactors_rejects_a_label_whose_figures_do_not_order_sanely():
    """A range running past the grand total is not a label this parser understands; guessing
    from it risks the same false-shortfall direction as scavenging."""
    from headstart.scrapers.successfactors import _advertised_paging

    assert (
        _advertised_paging(
            '<span class="paginationLabel">Results <b>1 \u2013 90</b> of <b>40</b></span>'
        )
        is None
    )


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


def test_workday_keeps_the_sibling_slices_when_one_slice_fails(monkeypatch):
    """The #194 fix has to reach a *capped* board, because that is where the biggest ones are:
    past the 2,000 cap every page after the first is fetched inside a subdivided slice, so a
    slice that fails outright must cost its own postings and not its siblings'. nvidia is
    exactly this shape — total 2,000, fifteen `jobFamilyGroup` slices, three of them a single
    paginated page (live-checked 2026-08-20), and it is the board the issue leads with."""
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "0")
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
            raise http.RequestsError("HTTP Error 429: Too Many Requests")
        return {"total": 1, "jobPostings": [{"bulletFields": ["R2"]}]}

    monkeypatch.setattr(scraper, "_post", post)
    absorbed: list[dict] = []
    scraper._exhaust({}, absorbed.extend, depth=0)

    assert [p["bulletFields"][0] for p in absorbed] == [
        "R1",
        "R2",
    ]  # siblings still land
    assert scraper.truncated and "jobFamilyGroup=Eng" in scraper.truncated
    assert "429" in scraper.truncated  # *why* the slice went, not only that it did


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


async def _fake_post_async(session, applied, offset):
    """A page beyond the first that just says "nothing here" — enough for tests that only
    care whether `_paginate`'s concurrent fan-out runs, not what it finds."""
    return {"jobPostings": []}


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
    # total > _PAGE_LIMIT reaches `_paginate`'s concurrent fan-out, which pages via `_post_async`.
    monkeypatch.setattr(scraper, "_post_async", _fake_post_async)

    scraper._exhaust({}, lambda batch: None, depth=0)

    assert scraper.truncated and "2000" in scraper.truncated

    # ...while a board whose total is under the cap paginates to the end and says nothing.
    whole = _workday_scraper()
    monkeypatch.setattr(
        whole,
        "_post",
        lambda applied, offset, **_: {"total": 40, "jobPostings": [], "facets": []},
    )
    monkeypatch.setattr(whole, "_post_async", _fake_post_async)
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


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        (
            '{"id": "1", "Job_Description": "Great role.", "Salary": "5-10 Lakhs", "Currency": "INR"}',
            "Great role. Salary: 5-10 Lakhs Currency: INR",
        ),
        (
            '{"id": "1", "Job_Description": "Great role.", "Salary": "5-10 Lakhs"}',
            "Great role. Salary: 5-10 Lakhs",
        ),
        (
            '{"id": "1", "Salary": "5-10 Lakhs", "Currency": "INR"}',
            "Salary: 5-10 Lakhs Currency: INR",
        ),
        (
            '{"id": "1", "Job_Description": "Great role.", "Currency": "INR"}',
            "Great role. Currency: INR",
        ),
        (
            '{"id": "1", "Currency": "INR"}',
            "Currency: INR",
        ),
        (
            '{"id": "1", "Job_Description": "Great role."}',
            "Great role.",
        ),
        (
            '{"id": "1"}',
            None,
        ),
    ],
)
def test_zoho_detail_description_appends_salary_and_currency(record, expected):
    """Salary/Currency only live on the detail page, never the listing (found via a
    code-review-triggered re-probe on PR #238) — free-text per-tenant strings, so they ride
    along in the description for Tier-2 mining rather than a bespoke Tier-1 parser, matching
    smartrecruiters' customField compensation treatment."""
    from headstart.scrapers.zoho import ZohoScraper, _description_text

    page = f"var jobs = JSON.parse('[{record}]')"
    assert _description_text(ZohoScraper._detail_record_of(page) or {}) == expected


def _zoho_listing(records):
    import html as _html

    return (
        f'<input type="hidden" value="{_html.escape(json.dumps(records))}" id="jobs">'
    )


def test_zoho_fetches_every_job_detail_not_just_empty_descriptions(monkeypatch):
    """Salary/Currency live ONLY on the detail page, never the listing — gating the detail
    fetch on a missing listing description meant the majority of jobs (whose listing already
    carries a description) never had their detail page fetched at all, so Salary was invisible
    for them regardless of any extraction fix. User decision 2026-08-24: fetch every job's
    detail page, accepting the bandwidth cost, for full Salary coverage."""
    records = [
        {
            "id": "1",
            "Job_Description": "Has a description already.",
            "Is_Locked": False,
        },
        {"id": "2", "Is_Locked": False},  # the old gate's only trigger case
        {
            "id": "3",
            "Job_Description": "Also has one.",
            "Is_Locked": True,
        },  # excluded: locked
        {
            "id": "4",
            "Job_Description": "Also has one.",
            "Publish": False,
        },  # excluded: unpublished
    ]
    s = get_scraper("zoho", "acme.zohorecruit.in", "Acme")
    monkeypatch.setattr(s, "_get", lambda url=None: _zoho_listing(records))
    monkeypatch.setattr(s, "async_fanout_enabled", lambda: False)
    fetched_ids = []

    def fake_fan_out(items, fn, workers=None):
        fetched_ids.extend(items)
        return [None] * len(items)

    monkeypatch.setattr(s, "fan_out", fake_fan_out)
    s.fetch_raw()

    assert sorted(fetched_ids) == ["1", "2"]  # not "3" (locked) or "4" (unpublished)


def test_zoho_parse_prefers_the_salary_enriched_detail_description(monkeypatch):
    """The detail record is a strict superset of the listing's bare Job_Description — it carries
    the same text PLUS Salary/Currency. Preferring the listing (the old precedence) would
    silently discard the Salary a detail fetch just paid bandwidth to collect, both from the
    description text and from the new `Job.salary` field."""
    records = [
        {"id": "1", "Job_Description": "Plain listing text.", "Is_Locked": False}
    ]
    s = get_scraper("zoho", "acme.zohorecruit.in", "Acme")
    monkeypatch.setattr(s, "_get", lambda url=None: _zoho_listing(records))
    monkeypatch.setattr(s, "async_fanout_enabled", lambda: False)
    monkeypatch.setattr(
        s,
        "fan_out",
        lambda items, fn, workers=None: [
            {
                "id": "1",
                "Job_Description": "Plain listing text.",
                "Salary": "10-12",
                "Currency": "LPA",
            }
        ],
    )

    raw = s.fetch_raw()
    jobs = s.parse(raw, SCRAPED_AT)

    assert jobs[0].description == "Plain listing text. Salary: 10-12 Currency: LPA"
    assert jobs[0].salary == "10-12 LPA"


def test_zoho_parse_falls_back_to_the_listing_if_the_detail_fetch_failed(monkeypatch):
    records = [
        {"id": "1", "Job_Description": "Plain listing text.", "Is_Locked": False}
    ]
    s = get_scraper("zoho", "acme.zohorecruit.in", "Acme")
    monkeypatch.setattr(type(s), "_get", lambda self, url=None: _zoho_listing(records))
    monkeypatch.setattr(type(s), "async_fanout_enabled", lambda self: False)
    monkeypatch.setattr(
        type(s), "fan_out", staticmethod(lambda items, fn, workers=None: [None])
    )

    raw = s.fetch_raw()
    jobs = s.parse(raw, SCRAPED_AT)

    assert jobs[0].description == "Plain listing text."


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


def test_personio_stays_on_its_direct_route_on_429(monkeypatch):
    """#312's spare-egress opt-in is reverted: personio's 429 is not an origin budget.

    Measured live 2026-08-26 (ADR-0063's own amendment): every one of the 22 Boards that failed
    terminally with `HTTP Error 429` across runs 32936269675/32942748996 is a tenant that has left
    personio, whose `/xml` 307s off the Board host to the marketing site — and the 429 is Vercel's
    bot mitigation there, keyed on the request rather than the client IP. The real scraper driven
    against those Boards rotated through three verified-distinct WARP addresses and got 429 on
    every one, so routing this ATS through a second egress cannot ever clear it.

    Asserted through an actual 429, not just off the constant: what has to hold is that the
    fetch carries **no** egress kwargs, since `http.fetch` walls a group only when it is handed
    an `egress_group`. A bare `egress_fallback_on == frozenset()` would still pass if some later
    change started passing a group explicitly.
    """
    from headstart.scrapers.personio import PersonioScraper

    assert PersonioScraper.egress_fallback_on == frozenset()

    seen: list[dict] = []

    class _Walled:
        status_code = 429
        headers: ClassVar[dict] = {"x-vercel-mitigated": "challenge"}
        text = ""

        @staticmethod
        def raise_for_status():
            raise http.RequestsError("HTTP Error 429: Too Many Requests")

    monkeypatch.setattr(
        http, "fetch", lambda method, url, **kw: (seen.append(kw), _Walled())[1]
    )
    with pytest.raises(http.RequestsError):
        PersonioScraper("acme.jobs.personio.de", "Acme").fetch_raw()

    assert "egress_group" not in seen[-1], (
        "a 429 must not route personio off its own IP"
    )
    assert "egress_on" not in seen[-1], "a 429 must not mark personio walled"


@pytest.mark.parametrize(
    "location",
    [
        "https://personio.com",  # what personio actually sends, 19 of 19 observed
        "//personio.com/",  # protocol-relative: names a host, so it is still off-host
    ],
)
def test_personio_a_tenant_that_redirects_off_the_board_host_reads_as_gone(
    monkeypatch, location
):
    """A departed personio tenant need not 404 — `/xml` 307s to personio's marketing site.

    Following that redirect is what produced every terminal `HTTP Error 429` on this ATS: the
    marketing host is behind Vercel bot mitigation, which answers 429 to our User-Agent (measured
    live 2026-08-26: same IP, same second, 200 under a Chrome UA and 429 under ours). That 429
    also marked the whole ATS walled, dragging every *healthy* personio Board onto the spare
    egress for the rest of the shard.

    So the redirect is not followed, and it is reported in the shape `board_failures.is_gone`
    already recognises (ADR-0058), the way lever reports a slug that is on no Lever board. A 429
    deliberately never ages a Board, which is why these tenants had been failing every run
    indefinitely; read as gone, the existing quarantine retires them after five agreeing runs.
    """
    from headstart.ingest.board_failures import is_gone
    from headstart.scrapers.personio import PersonioScraper

    seen: list[dict] = []

    class _Redirect:
        status_code = 307
        headers: ClassVar[dict] = {"location": location}
        text = ""

        @staticmethod
        def raise_for_status():
            return None

    monkeypatch.setattr(
        http, "fetch", lambda method, url, **kw: (seen.append(kw), _Redirect())[1]
    )
    scraper = PersonioScraper("zellerfeld.jobs.personio.com", "Zellerfeld")
    with pytest.raises(http.RequestsError) as excinfo:
        scraper.fetch_raw()

    assert seen[-1]["allow_redirects"] is False, "the redirect must not be followed"
    assert is_gone(f"{type(excinfo.value).__name__}: {excinfo.value}"), (
        "a departed tenant must age the Board's ADR-0058 gone-streak"
    )
    assert "zellerfeld.jobs.personio.com" in str(excinfo.value)


@pytest.mark.parametrize(
    "location",
    [
        "https://zellerfeld.jobs.personio.com/xml/",  # same host, exactly
        "https://Zellerfeld.Jobs.Personio.com/xml",  # hosts are case-insensitive
        "https://zellerfeld.jobs.personio.com:443/xml",  # the default port is still this host
        "https://zellerfeld.jobs.personio.com./xml",  # the FQDN root dot is still this host
        "/xml/",  # relative: no host of its own
        "",  # a 3xx with no Location at all
    ],
)
def test_personio_a_same_host_redirect_does_not_read_as_gone(monkeypatch, location):
    """Only an **off-host** target may age a Board. The gone verdict is keyed on the redirect's
    destination, not on the bare fact of a 3xx.

    No live Board redirects on-host today — 0 of 600 live and 0 of 200 dead Boards sampled
    2026-08-26 go anywhere but the marketing site — so this is about which way the check fails
    when personio changes. A same-host normalisation, or a 3xx with no `Location` at all, is not
    the origin saying the Board is gone; reading it as gone would retire a *live* Board after
    five agreeing runs (ADR-0058) on evidence that was never given. It fails the fetch instead,
    which costs one run and self-corrects.
    """
    from headstart.ingest.board_failures import is_gone
    from headstart.scrapers.personio import PersonioScraper

    class _Redirect:
        status_code = 301
        headers: ClassVar[dict] = {"location": location}
        text = ""

        @staticmethod
        def raise_for_status():
            return None

    monkeypatch.setattr(http, "fetch", lambda method, url, **kw: _Redirect())
    scraper = PersonioScraper("zellerfeld.jobs.personio.com", "Zellerfeld")
    with pytest.raises(http.RequestsError) as excinfo:
        scraper.fetch_raw()

    assert not is_gone(f"{type(excinfo.value).__name__}: {excinfo.value}"), (
        "a same-host redirect must not age the Board toward quarantine"
    )


def test_personio_a_live_board_still_parses_its_feed(monkeypatch):
    """The other side of what the redirect check keys on: a 200 must be read exactly as before."""
    from headstart.scrapers.personio import PersonioScraper

    class _Feed:
        status_code = 200
        headers: ClassVar[dict] = {}
        text = '<?xml version="1.0"?><workzag-jobs><position><id>1</id></position></workzag-jobs>'

        @staticmethod
        def raise_for_status():
            return None

    monkeypatch.setattr(http, "fetch", lambda method, url, **kw: _Feed())
    root = PersonioScraper("acme.jobs.personio.de", "Acme").fetch_raw()
    assert root.tag == "workzag-jobs"
    assert len(root.findall("position")) == 1


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


# _posting_key: docs/pipeline/2026-08-23_false-board-eviction-root-cause.md found bulletFields[0]
# is never actually the req id on any of 25 live-checked boards — a location, a relative
# posted-date, a closing-date label, an employment-type tag, a store name, or a subsidiary name,
# all of which change value across scrapes for the same live posting. These fixtures are the real
# shapes found on those boards (values only, not full postings) plus the tenant that motivated the
# fallback-index case (solenis: req id at index 2, not 1) and the two (tutorperini, nkg) that
# motivated ranking externalPath above bulletFields[0]: bulletFields[0] is the SAME literal string
# on every posting for those tenants (an employer/subsidiary name), so trusting it as a last
# resort collided hundreds of distinct real postings onto a handful of ids.


def _wd_key(bullet_fields, detail=None, external_path="/job/x/Some-Title_FALLBACK-999"):
    from headstart.scrapers.workday import _posting_key

    item = {"bulletFields": bullet_fields, "externalPath": external_path}
    if detail is not None:
        item["_detail"] = detail
    return _posting_key(item)


@pytest.mark.parametrize(
    ("bullet_fields", "expected"),
    [
        (["Posted 30+ Days Ago", "JR00258"], "JR00258"),  # astro — date first
        (["John C Lincoln - 250 E Dunlap Ave Phoenix, AZ 85020", "JR11133"], "JR11133"),
        (["TN - Memphis - 1100 Ridgeway Loop Rd", "R-0012714"], "R-0012714"),
        (
            ["Duba, Saudi Arabia", "JR-2026-21904", "ICS Network Engineer"],
            "JR-2026-21904",
        ),
        (["Data Scientist", "JR2025005486"], "JR2025005486"),  # title first
        (  # solenis — req id at index 2, not 1
            [
                "Florence, Kentucky, United States of America",
                "Florence, Kentucky, United States of America",
                "R0030539",
            ],
            "R0030539",
        ),
        (["Closing Date:", "Closing Date: 25/08/2026", "JR55512"], "JR55512"),
        (["PT-JR042569"], "PT-JR042569"),  # multi-letter-prefix shape
        (["REQ2026 - 9929"], "REQ2026-9929"),  # spaced shape, whitespace stripped
        (["2409195-R"], "2409195-R"),  # digits-then-letter-suffix shape
        (["26027605"], "26027605"),  # bare numeric, long enough to trust
    ],
)
def test_workday_posting_key_finds_the_req_id_wherever_it_falls(
    bullet_fields, expected
):
    assert _wd_key(bullet_fields) == expected


@pytest.mark.parametrize(
    "bullet_fields",
    [
        ["0018 - Shaler - Supermarket"],  # store name, no req id
        ["Casual"],  # employment-type tag, no req id
        ["Apogee Services Inc."],  # subsidiary name, no req id
        ["5 Locations"],  # location rollup, no req id
        ["0", "1"],  # short bare numbers — a stray flag/count seen live, not a req id
    ],
)
def test_workday_posting_key_falls_to_external_path_when_no_candidate_found(
    bullet_fields,
):
    """externalPath outranks bulletFields[0] once no field in bulletFields looks like a req id —
    see the module comment above `_posting_key` for why (tutorperini/nkg collision evidence)."""
    assert _wd_key(bullet_fields) == "Some-Title_FALLBACK-999"


def test_workday_posting_key_avoids_the_measured_collision():
    """The live-measured failure mode this ordering fixes: two DIFFERENT postings sharing the
    exact same non-req-id bulletFields[0] (tutorperini's is literally identical across all of a
    tenant's postings) must not collapse onto the same id — their distinct externalPath does."""
    shared_bullet = ["Tutor Perini Corporation"]
    key_a = _wd_key(
        shared_bullet, external_path="/job/White-Plains/Superintendent_JR102942"
    )
    key_b = _wd_key(
        shared_bullet, external_path="/job/Newark-NJ/Project-Accountant_JR102927"
    )
    assert key_a != key_b


def test_workday_posting_key_falls_to_bullet_fields_zero_as_the_last_resort():
    """bulletFields[0] is only reached when externalPath is ALSO empty — the one situation left
    where it's the sole available signal."""
    assert _wd_key(["Casual"], external_path="") == "Casual"
    assert _wd_key(["Casual"], external_path=None) == "Casual"


def test_workday_posting_key_rejects_an_iso_date():
    """theirc's bulletFields carries a real req id alongside a plain ISO date — the date must
    never be picked over it, in either position."""
    assert _wd_key(["JR00004545", "2026-09-22"]) == "JR00004545"
    assert _wd_key(["2026-09-22", "JR00004545"]) == "JR00004545"


def test_workday_posting_key_falls_back_to_external_path_tail_without_bullet_fields():
    assert _wd_key([]) == "Some-Title_FALLBACK-999"
    assert _wd_key(None) == "Some-Title_FALLBACK-999"


@pytest.mark.parametrize(
    ("bullet_fields", "external_path", "req_id"),
    [
        # roche — `_looks_like_req_id` rejected NNNNNN-NNNNNN, so a lost detail renamed
        # roche's own two measured runs, 827/1210 and 918/1210 of details lost: 58% of all
        # index flapping across 12 runs
        # (docs/pipeline/2026-08-30_posting-key-detail-dependence-flapping.md).
        (
            ["202607-119609"],
            "/job/Hyderabad/ERP-Solution-Consultant---EHS_202607-119609",
            "202607-119609",
        ),
        # pwc/crm — digits-then-letters.
        (
            ["726071WD"],
            "/job/AC-Manila/Cybersecurity-Platform-Associate_726071WD",
            "726071WD",
        ),
        # autodesk — digits, letters, digits.
        (
            ["26WD100347"],
            "/job/Toronto/Full-Stack-Software-Development-Engineer_26WD100347",
            "26WD100347",
        ),
        # saabgroup — the listing carries NO bulletFields at all, so no regex can help;
        # the externalPath tail has to hold the identity on both sides.
        (
            None,
            "/job/Huskvarna/Deputy-Head-of-Airworthiness-Office_REQ_44663",
            "REQ_44663",
        ),
        # wisconsin/UW_Milwaukee — bulletFields is a closing-date label and the real req id is
        # nowhere in the listing. Load-bearing alongside saabgroup: on the three cases above the
        # widening makes both tiers agree, so re-introducing the detail tier would NOT turn them
        # red. These last two are the cases that actually catch that regression.
        (
            ["Application Deadline: 09/13/2026"],
            "/job/Milwaukee/Research-Associate_JR10014519",
            "JR10014519",
        ),
    ],
)
def test_workday_posting_key_is_stable_when_the_detail_is_lost(
    bullet_fields, external_path, req_id
):
    """A posting's id must not depend on whether an OPTIONAL network fetch succeeded.

    It used to: `_posting_key` preferred `_detail["jobReqId"]`, which only exists after the
    per-job detail pass. A failed detail therefore did not make a posting *missing* — it
    *renamed* it, so the old id went Unconfirmed (ADR-0083), evicted on the second consecutive
    absence, and was re-added the moment the detail pass recovered. Measured: 75 of the 77 roche
    postings evicted in run 33288099045 were re-added by 33289938377, the same postings.

    ADR-0088 named this defect and deferred it here: "a defect in `_posting_key`'s
    detail-dependence — to be fixed there".
    """
    got_with = _wd_key(
        bullet_fields, detail={"jobReqId": req_id}, external_path=external_path
    )
    got_without = _wd_key(bullet_fields, detail={}, external_path=external_path)
    assert got_with == got_without, (
        f"a lost detail renamed the posting: {got_with!r} -> {got_without!r}"
    )


def test_workday_posting_key_keeps_the_req_id_shapes_the_detail_used_to_supply():
    """Widening `_looks_like_req_id` is what makes the tiers *agree* rather than merely stop
    disagreeing, so dropping the detail tier renames nothing on these boards. Measured live
    across roche/pwc/autodesk: 3,796 rows would have been renamed without the widening, 452
    with it (saab alone, which has no bulletFields to widen to)."""
    assert _wd_key(["202607-119609"]) == "202607-119609"  # roche
    assert _wd_key(["726071WD"]) == "726071WD"  # pwc/crm
    assert _wd_key(["26WD100347"]) == "26WD100347"  # autodesk


def test_workday_widened_req_id_shapes_still_avoid_the_measured_collision():
    """The widening must not re-open what the externalPath ranking closed. Verified live against
    both boards the module comment names: tutorperini (235 postings) and nkg (48) keep every id
    distinct, because their shared bulletFields[0] is a company name the widened shapes reject."""
    for shared in (["Tutor Perini Corporation"], ["NKG Stockler LTDA"]):
        assert _wd_key(
            shared, external_path="/job/White-Plains/Superintendent_JR102942"
        ) != _wd_key(shared, external_path="/job/Newark-NJ/Project-Accountant_JR102927")


@pytest.mark.parametrize(
    ("bullet_fields", "external_path", "expected"),
    [
        # roche — the URL vouches for the field, so no req-id shape is needed at all.
        (
            ["202607-119609"],
            "/job/Hyderabad/ERP-Solution-Consultant---EHS_202607-119609",
            "202607-119609",
        ),
        # cree — the req id is at index 1 behind an employment-type tag, and `26-167` is far
        # below any shape floor. The tail is what tells them apart.
        (["Regular", "26-167"], "/job/Durham/Senior-Tax-Analyst_26-167", "26-167"),
        # cree again — Workday's `-N` re-post suffix sits after the req id in the tail.
        (
            ["Regular", "26-695"],
            "/job/Durham/Program-Manager--Defense_26-695-1",
            "26-695",
        ),
        # cooley — the served value carries a space the URL does not; both sides normalise.
        (["Req 5047"], "/job/London/Accounts-Payable-Coordinator_Req5047", "Req5047"),
    ],
)
def test_workday_posting_key_trusts_a_field_the_url_vouches_for(
    bullet_fields, external_path, expected
):
    """The listing's own URL is the arbiter of which `bulletFields` entry is the req id.

    Shape-matching cannot be made complete — this whole defect began with `_looks_like_req_id`
    not knowing roche's `202607-119609` — so the first tier asks a question with a definite
    answer instead: does the posting's own `externalPath` end with this field? Measured live at
    25/25 agreement with the detail's `jobReqId` on roche, usbank, mercyhealth, montagehealth and
    aafp, and 0/25 on wisconsin, tutorperini and nkg, whose fields are a date label and two
    company names."""
    assert _wd_key(bullet_fields, external_path=external_path) == expected


@pytest.mark.parametrize(
    ("bullet_fields", "external_path"),
    [
        # A bare word that merely ENDS the title. Without the `_` boundary this returns
        # "Engineer" for every engineering posting on the board — the exact collision
        # `tutorperini` and `nkg` motivated guarding against.
        (["Engineer"], "/job/Austin/Software-Engineer"),
        (["Analyst"], "/job/Austin/Senior-Tax-Analyst"),
        # wisconsin — a closing-date label; the real req id is nowhere in the listing.
        (
            ["Application Deadline: 09/13/2026"],
            "/job/Milwaukee/Research-Associate_JR10014519",
        ),
        # tutorperini / nkg — a company name, identical across every posting.
        (["Tutor Perini Corporation"], "/job/White-Plains/Superintendent_JR102942"),
        (["NKG Stockler LTDA"], "/job/Sao-Paulo/Trader_JR55"),
    ],
)
def test_workday_posting_key_needs_an_underscore_boundary_not_a_bare_suffix(
    bullet_fields, external_path
):
    """The field must sit where Workday puts the req id — after the title's `_` — not merely at
    the end of the string. `Software-Engineer` ends with `Engineer`; that is a title, not an id,
    and keying on it would collapse a whole board onto one row."""
    assert _wd_key(bullet_fields, external_path=external_path) != bullet_fields[0]


def test_workday_posting_key_rejects_a_bare_zip_plus_four():
    """`^\\d{6,}[-_]\\d{3,}$` takes SIX leading digits, not five, so a bare US ZIP+4 cannot be
    mistaken for a req id — bulletFields carries addresses (module comment above `_posting_key`).
    Pinned because loosening it to five keeps every other test green."""
    assert _wd_key(["12345-6789"]) == "Some-Title_FALLBACK-999"
    assert _wd_key(["90210-1234"]) == "Some-Title_FALLBACK-999"
    assert _wd_key(["202607-119609"]) == "202607-119609"  # six digits: still a req id


def test_workday_posting_key_rejects_a_ddmmmyyyy_closing_date():
    """The `26WD100347` shape must not stretch to `10JAN2026`. bulletFields carries closing-date
    labels (module comment above `_posting_key`), and a date is the SAME string across a tenant's
    postings — precisely the collision the externalPath ranking exists to prevent."""
    assert _wd_key(["10JAN2026"]) == "Some-Title_FALLBACK-999"
    assert _wd_key(["31DEC2026"]) == "Some-Title_FALLBACK-999"
    assert _wd_key(["10JAN2026", "JR00004545"]) == "JR00004545"


def test_workday_posting_key_rejects_a_year_month_as_a_req_id():
    """The widened digits-hyphen-digits shape must not swallow a bare year-month, which
    `_ISO_DATE` (three groups) does not cover — the closing-date labels the module comment
    records living in bulletFields make this a live risk, not a hypothetical."""
    assert _wd_key(["2026-07", "JR00004545"]) == "JR00004545"
    assert _wd_key(["2026-07"]) == "Some-Title_FALLBACK-999"


def test_workday_detail_passes_opt_into_the_spare_egress(monkeypatch):
    """The detail pass is the traffic that spends the Origin budget — workday.py's own header
    documents 3.02M 429-retries and 51.7% of descriptions lost to it — yet only the *listing*
    calls carried the egress opt-in. A wall the listing marks must route the detail fetches,
    sync and async both, or ADR-0063 protects the cheap requests and abandons the expensive ones.
    """
    import asyncio

    from headstart import http
    from headstart.scrapers.workday import WorkdayScraper

    seen: list[tuple[str, str | None]] = []

    def fake_fetch(method, url, *, egress_group=None, egress_on=frozenset(), **kw):
        seen.append(("sync", egress_group))

        class _R:
            status_code = 200

            @staticmethod
            def json():
                return {"jobPostingInfo": {}}

        return _R()

    async def fake_fetch_async(
        session, method, url, *, egress_group=None, egress_on=frozenset(), **kw
    ):
        seen.append(("async", egress_group))

        class _R:
            status_code = 200

            @staticmethod
            def json():
                return {"jobPostingInfo": {}}

        return _R()

    monkeypatch.setattr(http, "fetch", fake_fetch)
    monkeypatch.setattr(http, "fetch_async", fake_fetch_async)
    s = WorkdayScraper("https://acme.wd1.myworkdayjobs.com/careers", "Acme")
    s._job_detail("/job/x")
    asyncio.run(s._job_detail_async(None, "/job/x"))

    assert seen == [("sync", "workday"), ("async", "workday")]


def test_eightfold_async_surfaces_opt_into_the_spare_egress(monkeypatch):
    import asyncio

    from headstart import http
    from headstart.scrapers.eightfold import EightfoldScraper

    seen: list[str | None] = []

    async def fake_fetch_async(
        session, method, url, *, egress_group=None, egress_on=frozenset(), **kw
    ):
        seen.append(egress_group)

        class _R:
            status_code = 200
            text = ""

            @staticmethod
            def json():
                return {}

        return _R()

    monkeypatch.setattr(http, "fetch_async", fake_fetch_async)
    s = EightfoldScraper("jobs.example.com", "Example")
    asyncio.run(s._description_async(None, "g", "1"))
    asyncio.run(s._jsonld_async(None, "https://jobs.example.com/careers/job/1"))

    assert seen == ["eightfold", "eightfold"]


def test_successfactors_listing_surfaces_go_through_the_retry_seam(monkeypatch):
    """ADR-0047: retry and Retry-After live in `http.fetch`, not the raw pooled session.

    Both listing surfaces called `http.session().request(...)` directly, so a 429 settled on the
    first try — and `_fetch_sitemap` maps a non-200 to ("other", "", None), so a throttled read
    presented as an empty Board and `index sync` evicted its rows. Pinned by making the raw
    session unusable: anything still bypassing the seam raises.
    """
    from headstart.scrapers import successfactors as sf

    def _no_raw_session():
        raise AssertionError("bypassed http.fetch — the retry seam (ADR-0047)")

    monkeypatch.setattr(sf.http, "session", _no_raw_session)
    monkeypatch.setattr(
        sf.http, "fetch", lambda *a, **k: _StreamedBody([b"<urlset></urlset>"])
    )
    scraper = sf.SuccessFactorsScraper("jobs.example.com")

    kind, _text, cut_short = scraper._fetch_sitemap()

    assert kind and cut_short is None
    monkeypatch.setattr(
        sf.http, "fetch", lambda *a, **k: _StreamedBody([b"<rss></rss>"])
    )
    scraper._rss_job_urls()  # must not touch the raw session either


def test_successfactors_marks_truncation_when_detail_pages_are_lost(monkeypatch):
    """ADR-0053: a Board whose returned list is knowingly short must say so.

    Every SuccessFactors field comes from the job page, so `parse` drops a Job whose page did
    not arrive. `report_detail_gaps` counted those losses into a log line and stopped there —
    nothing reached `truncated`, so `index sync` saw a shorter list and evicted the difference
    as delistings. The Jobs were still posted; only their detail fetch had failed.
    """
    from headstart.scrapers import successfactors as sf

    scraper = sf.SuccessFactorsScraper("jobs.example.com")
    monkeypatch.setattr(scraper, "_fetch_sitemap", lambda: ("urlset", "", None))
    monkeypatch.setattr(
        scraper,
        "_search_job_urls",
        lambda: (
            [(f"https://jobs.example.com/job/x/{i}/", str(i)) for i in (1, 2, 3)],
            None,
        ),
    )
    # the middle page 404s; the other two read fine
    monkeypatch.setattr(
        scraper,
        "_job_fields",
        lambda url: None if url.endswith("/2/") else {"title": "Engineer"},
    )
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "0")

    raw = scraper.fetch_raw()

    assert len(scraper.parse(raw, "2026-01-01")) == 2, "the lost page's Job is dropped"
    assert scraper.truncated and "unreadable" in scraper.truncated


def test_successfactors_marks_truncation_when_a_page_loads_but_has_no_title(
    monkeypatch,
):
    """docs/pipeline/2026-08-23_false-board-eviction-root-cause.md §4: a live-verified real-world
    gap the sibling test above (`_job_fields` returning `None`) doesn't cover. `_job_fields` only
    returns None on a hard fetch failure (non-200, or an exception isolated by `fan_out`) —
    `_page_fields` itself always returns a dict (`_jsonld_fields(page) or {}`), never None, even
    when the page loaded (200 OK) but its content didn't yield a parseable title (a temporary
    placeholder, an anti-bot interstitial served with 200, or any page shape the parser doesn't
    recognize).

    `parse()` correctly drops the Job either way — there's nothing to keep it by — but before the
    fix, `report_detail_gaps` only counted `None` results, so this loss was invisible to it and
    `mark_truncated` never fired: `index sync` read the board as fully, authoritatively scraped
    and evicted the Job as a delisting, though its detail page never told the scraper anything was
    wrong.
    """
    from headstart.scrapers import successfactors as sf

    scraper = sf.SuccessFactorsScraper("jobs.example.com")
    monkeypatch.setattr(scraper, "_fetch_sitemap", lambda: ("urlset", "", None))
    monkeypatch.setattr(
        scraper,
        "_search_job_urls",
        lambda: (
            [(f"https://jobs.example.com/job/x/{i}/", str(i)) for i in (1, 2, 3)],
            None,
        ),
    )

    class _Response:
        def __init__(self, text):
            self.status_code = 200
            self.text = text

    good_page = """<html><head><script type="application/ld+json">
    {"@context": "http://schema.org", "@type": "JobPosting", "title": "Engineer"}
    </script></head><body></body></html>"""

    def fake_fetch(method, url, **kw):
        # every page returns 200; the middle one's body has no title in any shape the parser
        # recognizes (a placeholder page) — exercises the real _job_fields -> _titled_fields
        # path, not a mock that bypasses it
        if url.endswith("/2/"):
            return _Response("<html><body>Temporarily unavailable.</body></html>")
        return _Response(good_page)

    monkeypatch.setattr(sf.http, "fetch", fake_fetch)
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "0")

    raw = scraper.fetch_raw()

    assert len(scraper.parse(raw, "2026-01-01")) == 2, (
        "the title-less page's Job is dropped"
    )
    assert scraper.truncated and "unreadable" in scraper.truncated


def test_eightfold_sitemap_fallback_marks_truncation_when_pages_are_lost(monkeypatch):
    """Same ADR-0053 hole on the surface eightfold takes whenever the API 403s."""
    from headstart.scrapers import eightfold as ef

    scraper = ef.EightfoldScraper("acme")
    urls = [f"https://acme.eightfold.ai/careers/job/{i}" for i in (1, 2, 3)]
    monkeypatch.setattr(scraper, "_job_urls", lambda: urls)
    monkeypatch.setattr(
        scraper,
        "_jsonld",
        lambda u: None if u.endswith("/2") else {"title": "Engineer"},
    )
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "0")

    records = scraper._sitemap_records()

    assert sum(1 for r in records if r["fields"] is None) == 1
    assert scraper.truncated and "unreadable" in scraper.truncated


def test_sensehq_marks_its_page_cap_but_not_a_board_that_ended(monkeypatch):
    """ADR-0053: stopping at a cap is not the same as reaching the end.

    Both directions matter. Unmarked, a capped board reads as complete and `index sync` evicts
    everything past the cap; marked wrongly, a healthy board is exempt from eviction forever and
    its closed postings are served indefinitely.
    """
    from headstart.scrapers import sensehq as sh

    # a board that ends naturally: one short page
    ended = sh.SenseHQScraper("acme")
    monkeypatch.setattr(
        type(ended),
        "_get",
        lambda self: json.dumps({"data": {"rows": [{"id": 1}], "count": 1}}),
    )
    ended.fetch_raw()
    assert ended.truncated is None, "a board that ended must stay evictable"

    # a board that never ends: every page full, count always out of reach
    capped = sh.SenseHQScraper("acme")
    monkeypatch.setattr(
        type(capped),
        "_get",
        lambda self: json.dumps(
            {"data": {"rows": [{"id": i} for i in range(10)], "count": 10_000}}
        ),
    )
    capped.fetch_raw()
    assert capped.truncated and "100-page cap" in capped.truncated


def test_darwinbox_marks_its_page_cap(monkeypatch):
    """The same cap exists on darwinbox's curl and browser paths (ADR-0053)."""
    from headstart.scrapers import darwinbox as db

    s = db.DarwinboxScraper("acme")
    full = [{"id": i} for i in range(db._PAGE_SIZE)]
    monkeypatch.setattr(s, "_alljobs", lambda host, page: full)
    monkeypatch.setattr(s, "_portal_is_v2", lambda host: True)
    jobs = s.fetch_raw()

    assert len(jobs) == db._PAGE_SIZE * 99
    assert s.truncated and "99-page cap" in s.truncated


def test_successfactors_does_not_mark_a_board_whose_pages_all_arrived(monkeypatch):
    """The other direction of ADR-0053: a Board wrongly marked truncated is exempt from
    eviction indefinitely, so its closed postings are served forever."""
    from headstart.scrapers import successfactors as sf

    scraper = sf.SuccessFactorsScraper("jobs.example.com")
    monkeypatch.setattr(scraper, "_fetch_sitemap", lambda: ("urlset", "", None))
    monkeypatch.setattr(
        scraper,
        "_search_job_urls",
        lambda: (
            [(f"https://jobs.example.com/job/x/{i}/", str(i)) for i in (1, 2)],
            None,
        ),
    )
    monkeypatch.setattr(scraper, "_job_fields", lambda url: {"title": "Engineer"})
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "0")

    scraper.fetch_raw()

    assert scraper.truncated is None


def test_eightfold_sitemap_fallback_does_not_mark_a_complete_board(monkeypatch):
    """Same negative direction on eightfold's fallback surface."""
    from headstart.scrapers import eightfold as ef

    scraper = ef.EightfoldScraper("acme")
    monkeypatch.setattr(
        scraper, "_job_urls", lambda: ["https://acme.eightfold.ai/careers/job/1"]
    )
    monkeypatch.setattr(scraper, "_jsonld", lambda u: {"title": "Engineer"})
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "0")

    scraper._sitemap_records()

    assert scraper.truncated is None


class _RippleResp:
    """One RippleHire response, standing in for both the token GET and the search POST."""

    def __init__(self, page, total):
        self.url = "https://acme.ripplehire.com/candidate/?token=TOK"
        self.text = "token=TOK"
        self.status_code = 200
        self._page, self._total = page, total

    def raise_for_status(self):
        return None

    def json(self):
        return {"jobVoList": self._page, "totalJobCount": self._total}


def test_ripplehire_marks_its_page_cap_but_not_a_board_that_ended(monkeypatch):
    """ADR-0053, both directions. ripplehire's natural exit is the tenant's own job count;
    exhausting the page cap means the board did not end, we stopped reading it."""
    from headstart.scrapers import ripplehire as rh

    def _board(total, per_page):
        # jobDesc is already set, so the detail pass has nothing to fetch
        page = [{"jobSeq": i, "jobDesc": "x"} for i in range(per_page)]
        monkeypatch.setattr(rh.http, "fetch", lambda *a, **k: _RippleResp(page, total))
        return rh.RippleHireScraper("acme")

    ended = _board(total=1, per_page=1)
    ended.fetch_raw()
    assert ended.truncated is None, "a board that reached its count must stay evictable"

    capped = _board(total=10**9, per_page=rh._PAGE_SIZE)
    capped.fetch_raw()
    assert capped.truncated and f"{rh._MAX_PAGES}-page cap" in capped.truncated


# --- oracle: the requisition list is paged, not one shot -------------------------------------
#
# Measured live 2026-08-24 on fa-etvl-saasfaprod1: TotalJobsCount 299 against a 200-row first
# page. The scraper read only that page and reported it as the whole board, so a third of it was
# dropped every run with no error and no truncation marker.


def _oracle_page(reqs: list[dict], total: int) -> str:
    return json.dumps(
        {"items": [{"requisitionList": reqs, "TotalJobsCount": total, "Limit": 200}]}
    )


def _oracle_reqs(start: int, n: int) -> list[dict]:
    return [{"Id": str(start + i), "Title": f"Engineer {start + i}"} for i in range(n)]


def test_oracle_pages_past_the_first_200(monkeypatch):
    """The live shape: 299 across a full page and a short one. Both must arrive."""
    pages = [
        _oracle_page(_oracle_reqs(0, 200), 299),
        _oracle_page(_oracle_reqs(200, 99), 299),
    ]
    seen: list[int] = []
    s = get_scraper("oracle", "acme.fa.ocs.oraclecloud.com", "Acme")

    def _get(self, url=None):
        seen.append(self._offset)
        return pages[len(seen) - 1]

    monkeypatch.setattr(type(s), "_get", _get)
    jobs = s.parse(s.fetch_raw(), SCRAPED_AT)

    assert seen == [0, 200]  # the offset really advanced
    assert len(jobs) == 299
    assert len({j.id for j in jobs}) == 299


def test_oracle_stops_on_a_short_page_when_no_total_is_given(monkeypatch):
    """A missing TotalJobsCount must fall back to the short-page end, never to `>= 0`.

    Guards the exact shape a review found latent elsewhere: `len(reqs) >= total` with `total`
    defaulting to 0 is always true, which stops after one page while looking like a natural end.
    """
    pages = [
        json.dumps({"items": [{"requisitionList": _oracle_reqs(0, 200)}]}),
        json.dumps({"items": [{"requisitionList": _oracle_reqs(200, 5)}]}),
    ]
    seen: list[int] = []
    s = get_scraper("oracle", "acme.fa.ocs.oraclecloud.com", "Acme")

    def _get(self, url=None):
        seen.append(self._offset)
        return pages[len(seen) - 1]

    monkeypatch.setattr(type(s), "_get", _get)
    jobs = s.parse(s.fetch_raw(), SCRAPED_AT)

    assert seen == [0, 200]  # it did NOT stop after page 1
    assert len(jobs) == 205


def test_zwayam_parse():
    raw = _load("zwayam_tavant.json")
    jobs = get_scraper("zwayam", "careers.tavant.com", "Tavant").parse(raw, SCRAPED_AT)
    assert len(jobs) == 3
    j = jobs[0]
    assert j.ats == "zwayam"
    assert j.company == "Tavant"
    assert j.id == f"zwayam:careers.tavant.com:{raw['rows'][0]['id']}"
    assert j.scraped_at == SCRAPED_AT
    # Structured record wins over the shouted flat `location` field.
    assert j.location == "Bengaluru, Karnataka, India"
    assert j.remote is False
    # The deep link carries the SPA's own `<base href>` prefix, not a guessed one.
    assert j.url.startswith("https://careers.tavant.com/tavant/jobview/")
    assert j.description and "</" not in j.description
    assert j.posted_at and j.posted_at.startswith("20")


def test_zwayam_experience_falls_back_only_when_the_numbers_are_blank():
    """The regression test 4e59dfa's fix never had.

    The fixture's row 0 states both forms and they agree, so it cannot tell the two orderings
    apart — which is why the old name (`...prefers_the_tenants_own_phrasing`) outlived the
    behaviour it described. These assertions can: `extract("Upto 4 years")` returns None while the
    numeric pair (0, 4) parses to 0-4, so preferring the prose silently loses a stated range.
    """
    from headstart.experience import extract
    from headstart.scrapers.zwayam import _experience

    assert (
        extract("Upto 4 years", None, None) is None
    )  # the premise, asserted not assumed
    assert (
        _experience(
            {
                "minYearOfExperience": 0,
                "maxYearOfExperience": 4,
                "experienceUIField": "Upto 4 years",
            }
        )
        == "0-4 years"
    )
    assert (
        _experience(
            {
                "minYearOfExperience": 0,
                "maxYearOfExperience": 0,
                "experienceUIField": "Fresher",
            }
        )
        == "Fresher"
    )


def test_zwayam_experience_prefers_the_structured_numeric_pair():
    """The fixture's row 0 states both forms and they agree, so it cannot tell the orderings
    apart on its own — the disagreeing copy below is what makes this test discriminate (the same
    trap that let the old `..._prefers_the_tenants_own_phrasing` name outlive its behaviour)."""
    raw = _load("zwayam_tavant.json")
    assert raw["rows"][0]["experienceUIField"] == "5-8 years"  # the premise, asserted
    raw["rows"][0]["experienceUIField"] = "prose the numbers disagree with"
    jobs = get_scraper("zwayam", "careers.tavant.com").parse(raw, SCRAPED_AT)
    assert jobs[0].experience == "5-8 years"  # the (5, 8) pair, not the prose


def test_zwayam_zero_to_zero_years_is_an_unfilled_form_not_a_range():
    """min=max=0 is what an untouched form submits, so it must not become "0-0 years"."""
    jobs = get_scraper("zwayam", "careers.tavant.com").parse(
        _load("zwayam_tavant.json"), SCRAPED_AT
    )
    assert jobs[2].experience is None


def test_zwayam_publishes_amounts_regardless_of_the_show_toggle():
    """`showSal` is off on 19 of 23 rows that carry amounts, and Zwayam has no Tier-2 fallback
    (description mining recovers 0 of 52), so honouring the toggle emptied the column entirely."""
    raw = _load("zwayam_tavant.json")
    assert raw["rows"][1]["showSal"] is False  # toggle off...
    off = get_scraper("zwayam", "careers.tavant.com").parse(raw, SCRAPED_AT)[1]
    assert off.salary == "100000-200000 INR"  # ...and the figure is published anyway

    raw["rows"][1]["showSal"] = True
    on = get_scraper("zwayam", "careers.tavant.com").parse(raw, SCRAPED_AT)[1]
    assert on.salary == "100000-200000 INR"  # same either way


def test_zwayam_no_amounts_still_yields_no_salary():
    raw = _load("zwayam_tavant.json")
    raw["rows"][1]["minJobSalary"] = raw["rows"][1]["maxJobSalary"] = ""
    assert (
        get_scraper("zwayam", "careers.tavant.com").parse(raw, SCRAPED_AT)[1].salary
        is None
    )


def test_zwayam_slug_is_the_board_host():
    """The API keys on the hostname, so a ledger row carrying a full URL must normalise to it."""
    from headstart.scrapers.zwayam import ZwayamScraper

    assert ZwayamScraper.slug_from(
        "careers.persistent.com", "https://careers.persistent.com/"
    ) == ("careers.persistent.com")
    assert ZwayamScraper.slug_from(
        "impetus", "https://impetus.openings.co/impetus/"
    ) == ("impetus.openings.co")


class _ZwayamNullBody:
    """The 200-with-`data: null` a non-Board hostname answers with."""

    status_code = 200
    text = ""

    def raise_for_status(self):
        return None

    def json(self):
        return {"code": 200, "data": None}


def test_zwayam_unregistered_host_yields_no_jobs(monkeypatch):
    """A hostname that is not a Board answers 200 with data: null — not an error, and not jobs."""
    from headstart.scrapers import zwayam as mod

    monkeypatch.setattr(mod.http, "fetch", lambda *a, **k: _ZwayamNullBody())
    scraper = get_scraper("zwayam", "careers.not-a-board.example")
    assert scraper.parse(scraper.fetch_raw(), SCRAPED_AT) == []


class _ZwayamHomepage:
    status_code = 200

    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


def test_zwayam_link_base_tells_the_three_frontend_generations_apart(monkeypatch):
    """One API, three careers frontends, three job routes (live-classified across all 224 hiring
    Boards): Angular's `<base href>` + `jobview/`, Next.js's root `/job-view/` (where `jobview`
    hard-404s, 10/10 Boards), and the old Angular 1 shell's hash route `/#!/job-view/`."""
    from headstart.scrapers import zwayam as mod

    cases = [
        ('<base href="/tavant/"><app-root>', "https://h.example/tavant/jobview/"),
        ('<script src="/_next/static/x.js">', "https://h.example/job-view/"),
        ('<div ng-view="" id="ng-view">', "https://h.example/#!/job-view/"),
    ]
    for html, expected in cases:
        monkeypatch.setattr(
            mod.http, "fetch", lambda *a, _h=html, **k: _ZwayamHomepage(_h)
        )
        assert get_scraper("zwayam", "h.example")._link_base() == expected


def test_zwayam_unreadable_homepage_falls_back_on_the_hostname_prior(monkeypatch):
    """When the homepage GET fails the shape comes from the measured prior: `openings.co` hosts
    are the Next generation 102:12, custom domains Angular 92:0. A wrong guess costs a dead link,
    not a lost Job — so the Board must still return its rows."""
    from headstart.scrapers import zwayam as mod

    def _boom(*a, **k):
        raise OSError("refused")

    monkeypatch.setattr(mod.http, "fetch", _boom)
    assert (
        get_scraper("zwayam", "x.openings.co")._link_base()
        == "https://x.openings.co/job-view/"
    )
    assert (
        get_scraper("zwayam", "careers.x.com")._link_base()
        == "https://careers.x.com/jobview/"
    )


def test_zwayam_reports_a_short_read_as_truncated():
    """A Board whose pages stop before totalCount must NOT look complete to `harvest`, or
    `index sync` reads the unread postings as delisted (ADR-0053)."""

    scraper = get_scraper("zwayam", "careers.short.example")
    page = {
        "data": {
            "totalCount": 50,
            "hasMoreData": False,  # server says "no more" while 40 postings are unread
            "data": [
                {
                    "_source": {
                        "id": i,
                        "jobTitle": f"Dev {i}",
                        "jobUrl": f"d-{i}",
                        "mediumDescriptionWithoutHtml": "text",
                    }
                }
                for i in range(10)
            ],
        }
    }
    scraper._page = lambda start: page
    scraper._link_base = lambda: "https://careers.short.example/x/jobview/"
    scraper._company_id = lambda: None  # keeps the detail pass off the network
    raw = scraper.fetch_raw()
    assert len(raw["rows"]) == 10
    assert scraper.truncated == "read 10 of 50 postings"


def test_zwayam_truncation_keeps_the_first_reason(monkeypatch):
    """`mark_truncated` is the base-class seam; the page cap must win over the shortfall."""
    from headstart.scrapers import zwayam as mod

    scraper = get_scraper("zwayam", "careers.runaway.example")
    monkey_cap = 3
    monkeypatch.setattr(mod, "_MAX_PAGES", monkey_cap)
    page = {
        "data": {
            "totalCount": 10_000,
            "hasMoreData": True,
            "data": [
                {
                    "_source": {
                        "id": i,
                        "jobTitle": "Dev",
                        "jobUrl": "d",
                        "mediumDescriptionWithoutHtml": "text",
                    }
                }
                for i in range(10)
            ],
        }
    }
    scraper._page = lambda start: page
    scraper._link_base = lambda: "https://careers.runaway.example/x/jobview/"
    scraper._company_id = lambda: None  # keeps the detail pass off the network
    scraper.fetch_raw()
    assert scraper.truncated.startswith(f"stopped at the {monkey_cap}-page cap")


def test_zwayam_multipart_encodes_every_field():
    from headstart.scrapers.zwayam import _BOUNDARY, _multipart

    body = _multipart({"a": "1", "b": "two"}).decode()
    assert body.count(f"--{_BOUNDARY}\r\n") == 2
    assert body.endswith(f"--{_BOUNDARY}--\r\n")
    assert 'name="a"\r\n\r\n1\r\n' in body
    assert 'name="b"\r\n\r\ntwo\r\n' in body


def test_zwayam_absolute_base_href_does_not_corrupt_the_link(monkeypatch):
    """An absolute <base href> is legal HTML; pasting it onto the Board host would build
    https://host/https://cdn.../jobview/… — unresolvable."""
    from headstart.scrapers import zwayam as mod

    html = '<html><base href="https://cdn.example.com/x/"><app-root></app-root></html>'
    monkeypatch.setattr(mod.http, "fetch", lambda *a, **k: _ZwayamHomepage(html))
    assert (
        get_scraper("zwayam", "careers.abs.example")._link_base()
        == "https://careers.abs.example/jobview/"
    )


def test_zwayam_row_without_a_joburl_is_skipped_not_linked_to_the_board_root():
    """Unobserved (0 of 16,427 rows), but a Board-root link would be a URL no shape can match."""
    raw = {
        "link_base": "https://careers.nolink.example/x/jobview/",
        "rows": [{"id": 1, "jobTitle": "Dev", "jobUrl": ""}],
    }
    assert get_scraper("zwayam", "careers.nolink.example").parse(raw, SCRAPED_AT) == []


def test_zwayam_bare_amounts_default_to_rupees():
    """Most rows carrying amounts state no `currencyType`, and `salary.extract`'s plausibility
    guard falls back to USD bounds for an unknown currency — so a real 17-20 lakh range reads as
    $1.7M and is dropped, while small placeholder ranges survive. Defaulting to INR is what makes
    the large, genuine figures reach the index."""
    from headstart.salary import extract
    from headstart.scrapers.zwayam import _salary

    assert (
        _salary({"minJobSalary": "1700000", "maxJobSalary": "2000000"})
        == "1700000-2000000 INR"
    )
    span = extract(
        _salary({"minJobSalary": "1700000", "maxJobSalary": "2000000"}), None, "zwayam"
    )
    assert span and span.currency == "INR" and span.min_annual == 1700000

    # a stated currency always wins over the default
    assert (
        _salary(
            {"minJobSalary": "9000", "maxJobSalary": "15000", "currencyType": "QAR"}
        )
        == "9000-15000 QAR"
    )


def test_zwayam_fixture_row_without_a_currency_gets_the_default():
    """The fixture's salaried rows all state INR, so the shipped fixture never exercised the
    default that most real rows depend on."""
    raw = _load("zwayam_tavant.json")
    raw["rows"][1]["currencyType"] = None
    job = get_scraper("zwayam", "careers.tavant.com").parse(raw, SCRAPED_AT)[1]
    assert job.salary.endswith(" INR")


def test_zwayam_a_zero_bound_is_an_unfilled_form_half():
    """`1000000-0` makes `salary.extract` reject the whole row, losing a real floor that parses
    fine alone — 17 of 5,079 amount rows carried a floor with a zero ceiling."""
    from headstart.salary import extract
    from headstart.scrapers.zwayam import _salary

    assert _salary({"minJobSalary": "1000000", "maxJobSalary": "0"}) == "1000000 INR"
    assert extract("1000000 INR", None, "zwayam").min_annual == 1000000
    assert _salary({"minJobSalary": "0", "maxJobSalary": "0"}) is None


def test_zwayam_a_ceiling_without_a_floor_is_shown_but_never_read_as_a_floor():
    """A lone figure parses as a *floor*, so a bare ceiling would serve a job capped at 200k as
    one paying at least that (10 of 5,079 amount rows). "Upto" keeps the display column honest —
    `Job.salary` is "raw, for display" — while parsing to nothing, so no derived column inverts.
    """
    from headstart.salary import extract
    from headstart.scrapers.zwayam import _salary

    # the premise, asserted not assumed
    assert (
        extract("200000 INR", None, "zwayam").min_annual == 200000
    )  # reads as a FLOOR
    assert _salary({"minJobSalary": "", "maxJobSalary": "200000"}) == "Upto 200000 INR"
    assert _salary({"minJobSalary": "0", "maxJobSalary": "200000"}) == "Upto 200000 INR"
    assert extract("Upto 200000 INR", None, "zwayam") is None  # shown, never inverted


def test_zwayam_job_url_is_percent_encoded():
    """Real jobUrl values carry spaces, commas and slashes; pasted raw they make a malformed URL
    (and the Next.js generation hard-404s a raw slash while routing its %2F encoding)."""
    raw = {
        "link_base": "https://careers.enc.example/x/jobview/",
        "rows": [{"id": 7, "jobTitle": "SDET", "jobUrl": "sdet-pune-gen ai, py/sql"}],
    }
    url = get_scraper("zwayam", "careers.enc.example").parse(raw, SCRAPED_AT)[0].url
    assert " " not in url and "," not in url
    assert url.endswith("/jobview/sdet-pune-gen%20ai%2C%20py%2Fsql")


def test_zwayam_above_n_years_is_an_open_floor_not_an_inverted_range():
    """59 of 60 lo>hi pairs walked are "Above N years" rows — max left at the form's 0. Emitting
    "3.5-0 years" ships an inverted range; "3.5+ years" is what `experience.extract` reads as an
    open floor."""
    from headstart.experience import extract
    from headstart.scrapers.zwayam import _experience

    source = {
        "minYearOfExperience": 3.5,
        "maxYearOfExperience": 0,
        "experienceUIField": "Above 3.5 years",
    }
    assert _experience(source) == "3.5+ years"
    span = extract(_experience(source), None, None)
    assert span and span.min_years == 3 and span.max_years is None


def test_zwayam_department_survives_the_lowercase_key_being_null():
    """`departmentName` is null while `DepartmentName` carries the value on 1,399 of 16,427
    walked rows; the two agree everywhere both are set, so the fallback only recovers."""
    raw = {
        "link_base": "https://h.example/jobview/",
        "rows": [
            {
                "id": 1,
                "jobTitle": "Dev",
                "jobUrl": "d",
                "departmentName": None,
                "DepartmentName": "Engineering",
            }
        ],
    }
    job = get_scraper("zwayam", "h.example").parse(raw, SCRAPED_AT)[0]
    assert job.department == "Engineering"


def test_zwayam_a_body_error_code_raises_rather_than_reading_as_an_empty_board(
    monkeypatch,
):
    """The endpoint reports its own failures as HTTP 200 with body `code: 500` and `data: null`
    (measured) — byte-identical to a dead Board except for the code. Reading it as "no jobs"
    marks every posting Unconfirmed, and a second one evicts them all (ADR-0083)."""
    import pytest

    from headstart.scrapers import zwayam as mod

    class _ErrorBody:
        status_code = 200
        text = ""

        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 500, "data": None, "message": "Internal Server Error"}

    monkeypatch.setattr(mod.http, "fetch", lambda *a, **k: _ErrorBody())
    scraper = get_scraper("zwayam", "careers.err.example")
    with pytest.raises(RuntimeError, match="body code 500"):
        scraper.fetch_raw()


def test_zwayam_detail_text_wins_and_the_skip_list_prunes_the_fetch():
    """The listing's text can be silently truncated with no way to tell (632 chars listed vs
    909 of stripped detail text, measured), so the detail is fetched for every row not on the
    ADR-0050 skip-list and its text wins over whatever the listing carried."""
    page = {
        "data": {
            "totalCount": 3,
            "hasMoreData": False,
            "data": [
                {"_source": {"id": 1, "jobTitle": "A", "jobUrl": "a"}},
                {
                    "_source": {
                        "id": 2,
                        "jobTitle": "B",
                        "jobUrl": "b",
                        "mediumDescriptionWithoutHtml": "possibly truncated listing",
                    }
                },
                {
                    "_source": {
                        "id": 3,
                        "jobTitle": "C",
                        "jobUrl": "c",
                        # The row MUST carry listing text: without it this test passes whether
                        # or not a skip-listed row falls through to the listing, which is the
                        # exact regression it exists to catch.
                        "mediumDescriptionWithoutHtml": "possibly truncated listing",
                    }
                },
            ],
        }
    }
    scraper = get_scraper("zwayam", "h.example")
    scraper._page = lambda start: page
    scraper._link_base = lambda: "https://h.example/jobview/"
    scraper._company_id = lambda: 4242
    fetched = []

    def _detail(company_id, job_url):
        fetched.append((company_id, job_url))
        return f"detail text for {job_url}"

    scraper._job_detail = _detail
    # id 3 is on the skip-list: the store already holds its (detail-derived) text, so the row
    # must ship None and let the store supply it. Shipping the listing text instead would be
    # worse than a no-op — `update_descriptions` treats fresh corpus text as authoritative
    # ("Fresh text always wins"), so run 2 would *overwrite* the stored full text with the
    # truncated listing, and every later run would re-confirm it.
    scraper.have_details = {"zwayam:h.example:3"}
    jobs = scraper.parse(scraper.fetch_raw(), SCRAPED_AT)
    assert fetched == [(4242, "a"), (4242, "b")]
    by_id = {j.id.rsplit(":", 1)[1]: j.description for j in jobs}
    assert by_id == {"1": "detail text for a", "2": "detail text for b", "3": None}


def _zwayam_two_row_board(scraper):
    """A Board of two rows — one carrying listing text, one carrying none."""
    page = {
        "data": {
            "totalCount": 2,
            "hasMoreData": False,
            "data": [
                {
                    "_source": {
                        "id": 1,
                        "jobTitle": "A",
                        "jobUrl": "a",
                        "mediumDescriptionWithoutHtml": "possibly truncated listing",
                    }
                },
                {"_source": {"id": 2, "jobTitle": "B", "jobUrl": "b"}},
            ],
        }
    }
    scraper._page = lambda start: page
    scraper._link_base = lambda: "https://h.example/jobview/"
    return scraper


def test_zwayam_a_failed_detail_ships_nothing_so_the_next_run_retries():
    """A transient failure must NOT fall back to the listing text: the store persists whatever
    the scrape emits and membership in it is the skip-list, so one bad fetch would freeze
    possibly-truncated text forever. Emitting nothing leaves `needs_detail` true."""
    scraper = _zwayam_two_row_board(get_scraper("zwayam", "h.example"))
    scraper._company_id = lambda: 4242

    def _boom(company_id, job_url):
        raise OSError("detail refused")

    scraper._job_detail = _boom
    jobs = scraper.parse(scraper.fetch_raw(), SCRAPED_AT)
    assert {j.id.rsplit(":", 1)[1]: j.description for j in jobs} == {
        "1": None,
        "2": None,
    }


def test_zwayam_a_failed_config_call_ships_no_descriptions_not_stale_ones():
    """The config call is per-Board, so its failure fails every detail on the Board — and must
    behave like any other failed detail rather than freezing the whole Board's listing text."""
    scraper = _zwayam_two_row_board(get_scraper("zwayam", "h.example"))
    scraper._company_id = lambda: None
    jobs = scraper.parse(scraper.fetch_raw(), SCRAPED_AT)
    assert len(jobs) == 2  # the Jobs themselves still ship
    assert {j.description for j in jobs} == {None}


def test_zwayam_a_detail_that_answers_empty_keeps_the_listing_text():
    """An answered-but-bodyless detail is the posting's final word, so the listing text is the
    best that will ever exist for it — kept, unlike the failed-fetch case above."""
    scraper = _zwayam_two_row_board(get_scraper("zwayam", "h.example"))
    scraper._company_id = lambda: 4242
    scraper._job_detail = lambda company_id, job_url: ""
    jobs = scraper.parse(scraper.fetch_raw(), SCRAPED_AT)
    assert {j.id.rsplit(":", 1)[1]: j.description for j in jobs} == {
        "1": "possibly truncated listing",
        "2": None,
    }
