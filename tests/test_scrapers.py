import json
import logging
import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest
from fake_fetcher import FakeFetcher, FakeResponse

from headstart import fanout_stats, http
from headstart.scrapers.personio import PersonioScraper
from headstart.scrapers.registry import get_scraper
from headstart.scrapers.rippling import RipplingScraper

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


def test_greenhouse_salary_prefers_currency_range_over_currency_band():
    # Real live sample (doordashusa, "Account Manager, CPG", 2026-09-15): a currency_range "Pay
    # Transparency Range" entry beats two single-point currency "Band Midpoint"/"Minimum" entries
    # on the same job.
    metadata = [
        {
            "name": "US1 - Base Salary Band Midpoint",
            "value": {"unit": "USD", "amount": "168500.0"},
            "value_type": "currency",
        },
        {
            "name": "US4 - Base Salary Band Minimum",
            "value": {"unit": "USD", "amount": "114600.0"},
            "value_type": "currency",
        },
        {
            "name": "USA: Pay Transparency Range",
            "value": {"unit": "USD", "min_value": "114600.0", "max_value": "168500.0"},
            "value_type": "currency_range",
        },
    ]
    raw = {"jobs": [{"id": 1, "title": "T", "metadata": metadata}]}
    jobs = get_scraper("greenhouse", "x", "X").parse(raw, SCRAPED_AT)
    assert jobs[0].salary == "114600-168500 USD"


def test_greenhouse_salary_ties_keep_first_array_order():
    # Real live shape (doordashusa, 2026-09-15): two equally-scored currency_range entries for
    # different countries on the same job — the first in metadata order wins, deterministically.
    metadata = [
        {
            "name": "USA: Pay Transparency Range",
            "value": {"unit": "USD", "min_value": "114600.0", "max_value": "168500.0"},
            "value_type": "currency_range",
        },
        {
            "name": "Canada: Pay Transparency Range",
            "value": {"unit": "CAD", "min_value": "99000.0", "max_value": "124000.0"},
            "value_type": "currency_range",
        },
    ]
    raw = {"jobs": [{"id": 1, "title": "T", "metadata": metadata}]}
    jobs = get_scraper("greenhouse", "x", "X").parse(raw, SCRAPED_AT)
    assert jobs[0].salary == "114600-168500 USD"


def test_greenhouse_salary_ignores_placeholder_zero_values():
    # Real live shape (mongodb, "Job Post Range (United States/Canada)", 2026-09-15): an
    # inapplicable market's field is populated with a 0.0/no-unit placeholder instead of omitted.
    metadata = [
        {
            "name": "Job Post Range (United States)",
            "value": {"unit": "USD", "min_value": "0.0", "max_value": "0.0"},
            "value_type": "currency_range",
        },
        {
            "name": "Job Post Range (Canada)",
            "value": {"unit": None, "min_value": "0.0", "max_value": "0.0"},
            "value_type": "currency_range",
        },
    ]
    raw = {"jobs": [{"id": 1, "title": "T", "metadata": metadata}]}
    jobs = get_scraper("greenhouse", "x", "X").parse(raw, SCRAPED_AT)
    assert jobs[0].salary is None


def test_greenhouse_salary_never_reports_equity_as_salary():
    # Real risk, not hypothetical: doordashusa's "US1 - Equity Band Midpoint" is
    # value_type="currency" exactly like a real salary field, and non-zero (a real RSU grant
    # value) on 146/455 sampled jobs — must never be picked even when it's the only populated
    # compensation-shaped field on the job.
    metadata = [
        {
            "name": "US1 - Equity Band Midpoint",
            "value": {"unit": "USD", "amount": "100000.0"},
            "value_type": "currency",
        },
    ]
    raw = {"jobs": [{"id": 1, "title": "T", "metadata": metadata}]}
    jobs = get_scraper("greenhouse", "x", "X").parse(raw, SCRAPED_AT)
    assert jobs[0].salary is None


def test_greenhouse_salary_absent_when_no_metadata():
    raw = {"jobs": [{"id": 1, "title": "T"}]}
    jobs = get_scraper("greenhouse", "x", "X").parse(raw, SCRAPED_AT)
    assert jobs[0].salary is None


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
    # INFO, not WARNING: this can fire once per Board and WARNING is a run-level annotation
    # quota under Actions (ADR-0039's 2026-09-08 amendment, pinned by
    # tests/test_log_levels.py).
    caplog.set_level(logging.INFO, logger="headstart.scrapers.greenhouse")

    raw = s.fetch_raw()

    assert raw == envelope, "the envelope must pass through untouched"
    reported = [r for r in caplog.records if r.levelno == logging.INFO]
    assert bool(reported) is should_warn
    if should_warn:
        assert (
            "meta.total=5" in reported[0].message
            and "greenhouse:acme" in reported[0].message
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
    """The served location IS the filter substrate — `geo.where()` matches substrings of it
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
    # current ashby responses always carry the key (empty or populated); _salary_field() correctly
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
    assert get_scraper("ashby", "ramp")._salary_field(compensation) == expected


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


def test_darwinbox_salary_field_prefers_structured_over_salary_range():
    # Real live sample (advikhris, 2026-09-15): structured fields build a clean string even
    # though salary_range is also populated with the identical (locale-formatted) figure.
    salary_field = get_scraper("darwinbox", "acme")._salary_field

    j = {
        "salary_range": "INR 5,46,000 - 6,82,000 (Annual)",
        "salary_min": "546000",
        "salary_max": "682000",
        "salary_currency": "INR",
        "salary_timeframe": "Annual",
    }
    assert salary_field(j) == "INR 546000-682000 (Annual)"


def test_darwinbox_salary_field_recovers_non_inr_currency():
    # The real gap this pass fixes: salary_range never carried anything but "INR" text for a
    # non-INR tenant to matter, but the structured fields state the real currency directly —
    # real, live (transcarent, 2026-09-15).
    salary_field = get_scraper("darwinbox", "acme")._salary_field

    j = {
        "salary_range": "USD 20.00 - 20 (Hourly)",
        "salary_min": "20.00",
        "salary_max": "20",
        "salary_currency": "USD",
        "salary_timeframe": "Hourly",
    }
    assert salary_field(j) == "USD 20.00-20 (Hourly)"


def test_darwinbox_salary_field_falls_back_when_structured_fields_are_empty():
    # Real live placeholder shape (airtel, 2026-09-15): salary_min/salary_max are empty strings
    # even though salary_range/salary_currency are populated ("INR 0+ (Annual)") — falls back to
    # salary_range, which _field_darwinbox already declines as implausible.
    salary_field = get_scraper("darwinbox", "acme")._salary_field

    j = {
        "salary_range": "INR 0+ (Annual)",
        "salary_min": "",
        "salary_max": "",
        "salary_currency": "INR",
        "salary_timeframe": "Annual",
    }
    assert salary_field(j) == "INR 0+ (Annual)"


def test_darwinbox_salary_field_no_data_anywhere_is_none():
    salary_field = get_scraper("darwinbox", "acme")._salary_field

    assert salary_field({}) is None
    assert salary_field({"salary_range": ""}) is None


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


def test_keka_location_joins_every_entry_not_just_the_first():
    """Measured live 2026-09-22: 57 of 929 jobs on kpgroup.keka.com carry more than one
    location; taking `[0]` silently dropped the rest."""
    raw = [
        {
            "id": 1,
            "title": "T",
            "jobLocations": [
                {"city": "Bhavnagar", "state": "GJ", "countryName": "India"},
                {"city": "Bharuch", "state": "GJ", "countryName": "India"},
            ],
        }
    ]
    jobs = get_scraper("keka", "x", "X").parse(raw, SCRAPED_AT)
    assert jobs[0].location == "Bhavnagar, GJ, India; Bharuch, GJ, India"


def test_keka_location_join_drops_an_entry_with_nothing_to_say():
    raw = [
        {
            "id": 1,
            "title": "T",
            "jobLocations": [{}, {"city": "Pune", "countryName": "India"}],
        }
    ]
    jobs = get_scraper("keka", "x", "X").parse(raw, SCRAPED_AT)
    assert jobs[0].location == "Pune, India"


def test_keka_employment_type_maps_the_two_confirmed_jobtype_values():
    raw = [
        {"id": 1, "title": "T", "jobType": 2},
        {"id": 2, "title": "T", "jobType": 1},
        {
            "id": 3,
            "title": "T",
            "jobType": 0,
        },  # deliberately unmapped, see module docstring
        {"id": 4, "title": "T"},
    ]
    jobs = get_scraper("keka", "x", "X").parse(raw, SCRAPED_AT)
    assert [j.employment_type for j in jobs] == ["Full-time", "Part-time", None, None]


def test_keka_salary_no_scientific_notation_for_large_amounts():
    # Real bug, salary-extraction pass 2026-08-22: Python's `:g` format (the previous
    # implementation) switches to scientific notation ("1e+06") for values >= 1,000,000 — neither
    # headstart.salary's _RANGE regex nor _num() can parse an exponent, so every genuine keka
    # figure at or above ₹1,000,000 was silently discarded. 27% of a 300-job sample of rejected
    # Job.salary values showed this shape, across 19 distinct companies. Fixing it recovered
    # ~1,550 jobs on re-measurement (Tier 1 coverage 15.8% -> 27.8% of the full sampled corpus).
    _salary = get_scraper("keka", "acme")._salary_field

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


def _keka_stub_get(body):
    """Stub BaseScraper._get for the one-request listing path."""

    def _get(self, url=None):
        target = url or self.url()
        assert target.endswith("/careers/api/jobs/default/active"), (
            f"unexpected GET {target}"
        )
        return body

    return _get


def test_keka_reads_the_jobs_array_in_one_request(monkeypatch):
    s = get_scraper("keka", "acme", "Acme")
    assert s.url() == "https://acme.keka.com/careers/api/jobs/default/active"
    calls: list[str] = []

    def _get(self, url=None):
        calls.append(url or self.url())
        return '[{"id":1,"title":"Eng"}]'

    monkeypatch.setattr(type(s), "_get", _get)
    assert [j["id"] for j in s.fetch_raw()] == [1]
    assert len(calls) == 1, "the listing must not need a second lookup"


def test_keka_board_without_an_org_uuid_still_reads(monkeypatch):
    """The regression this endpoint exists for.

    23 of 150 sampled Hiring Boards (2026-09-22) are background-less portals whose `/careers`
    page is a shell with no uuid in it, so the old careerportalinfo -> /careers -> embedjobs path
    found no uuid and returned an empty list for a Board that was serving jobs.

    The stub deliberately answers **all three** URLs, exactly as such a Board does: an empty
    `careersBackgroundPath`, a uuid-less `/careers` shell, and a populated active-jobs array. The
    old two-step path walks the first two, finds no uuid and drops the Board; this asserts the
    jobs arrive anyway. A stub that served only the new endpoint would fail against the old code
    on an unexpected-URL assertion instead of on the drop, and so would pin nothing.
    """

    def _get(self, url=None):
        # Most specific first: the active-jobs URL also contains "/careers".
        target = url or self.url()
        if target.endswith("/careers/api/jobs/default/active"):
            return '[{"id":7,"title":"SDE"},{"id":8,"title":"QA"}]'
        if target.endswith("careerportalinfo"):
            return '{"careersBackgroundPath":"","name":"Inoptra"}'
        if target.endswith("/careers"):
            return "<html><body><div id='app'></div></body></html>"
        raise AssertionError(f"unexpected GET {target}")

    s = get_scraper("keka", "inoptra", "Inoptra")
    monkeypatch.setattr(type(s), "_get", _get)
    assert [j["id"] for j in s.fetch_raw()] == [7, 8]


def test_keka_invalid_tenant_yields_no_jobs(monkeypatch):
    # soft-404: an unknown slug renders "Invalid Tenant" HTML at HTTP 200
    s = get_scraper("keka", "nope", "Nope")
    monkeypatch.setattr(
        type(s), "_get", _keka_stub_get("<html><title>Invalid Tenant</title></html>")
    )
    assert s.fetch_raw() == []


def test_keka_forbidden_access_yields_no_jobs(monkeypatch):
    # a disabled portal renders "Forbidden Access" HTML, also at HTTP 200
    s = get_scraper("keka", "off", "Off")
    monkeypatch.setattr(
        type(s), "_get", _keka_stub_get("<html><title>Forbidden Access</title></html>")
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


def test_workable_multi_location_rows_collapse_into_one_job():
    """Real zyte shape (shortcode 6DCFF04CD6, measured live 2026-09-22): a multi-location
    posting is 3 listing rows sharing one shortcode — identical url/description, differing only
    in location. Without grouping, the harvest's within-board first-wins dedupe kept only the
    first row's location and silently dropped the other two."""
    raw = {
        "name": "Zyte",
        "jobs": [
            {
                "shortcode": "6DCFF04CD6",
                "title": "Backend Engineer",
                "city": "Sao Paulo",
                "state": "Sao Paulo",
                "country": "Brazil",
                "url": "https://apply.workable.com/zyte/j/6DCFF04CD6/",
                "description": "Build things.",
                "department": "Engineering",
            },
            {
                "shortcode": "6DCFF04CD6",
                "title": "Backend Engineer",
                "city": "Montevideo",
                "state": "Montevideo Department",
                "country": "Uruguay",
                "url": "https://apply.workable.com/zyte/j/6DCFF04CD6/",
                "description": "Build things.",
                "department": "Engineering",
            },
            {
                "shortcode": "6DCFF04CD6",
                "title": "Backend Engineer",
                "city": "Buenos Aires",
                "state": "Buenos Aires",
                "country": "Argentina",
                "url": "https://apply.workable.com/zyte/j/6DCFF04CD6/",
                "description": "Build things.",
                "department": "Engineering",
            },
            {
                "shortcode": "OTHERJOB01",
                "title": "Support Engineer",
                "city": "Remote",
                "state": "",
                "country": "",
                "url": "https://apply.workable.com/zyte/j/OTHERJOB01/",
                "description": "Help customers.",
                "department": "Support",
            },
        ],
    }
    jobs = get_scraper("workable", "zyte", "Zyte").parse(raw, SCRAPED_AT)
    assert len(jobs) == 2  # 4 rows, one shortcode grouped -> one Job
    multi = next(j for j in jobs if j.id == "workable:zyte:6DCFF04CD6")
    assert multi.location == (
        "Sao Paulo, Sao Paulo, Brazil; "
        "Montevideo, Montevideo Department, Uruguay; "
        "Buenos Aires, Buenos Aires, Argentina"
    )
    single = next(j for j in jobs if j.id == "workable:zyte:OTHERJOB01")
    assert single.location == "Remote"


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


def test_smartrecruiters_department_falls_back_to_function_label():
    """`department.label` null on 54.7% of a live 8-board sample; `function.label` present on
    100% of those (module docstring). No `team` field — `filter_tech` only reads `department`."""
    from headstart.scrapers.smartrecruiters import _department_of

    assert _department_of({"department": {"label": "Engineering"}}) == "Engineering"
    assert _department_of({"function": {"label": "Information Technology"}}) == (
        "Information Technology"
    )
    # department present wins over function, even when both are stated
    assert (
        _department_of(
            {"department": {"label": "Engineering"}, "function": {"label": "Sales"}}
        )
        == "Engineering"
    )
    assert _department_of({}) is None


def test_smartrecruiters_parse_uses_function_when_department_is_null():
    scraper = get_scraper("smartrecruiters", "acme", "Acme")
    raw = {
        "content": [
            {
                "id": "1",
                "name": "IT Support Specialist",
                "function": {"label": "Information Technology"},
            }
        ]
    }
    (job,) = scraper.parse(raw, SCRAPED_AT)
    assert job.department == "Information Technology"


def test_smartrecruiters_tech_gate_reads_function_when_department_is_null():
    """The gate and `parse` must reach the same verdict — both go through `_department_of`."""
    from headstart.scrapers.smartrecruiters import SmartRecruitersScraper

    postings = [
        # vague title, no department, tech function -> rule 4 promotes it (gate must fetch it)
        {"id": "1", "name": "Associate", "function": {"label": "Engineering"}},
        # vague title, no department, non-tech function -> stays gated out
        {"id": "2", "name": "Associate", "function": {"label": "Retail"}},
    ]

    def route(method, url, kwargs):
        if "/postings?" in url:
            return FakeResponse(text=json.dumps({"content": postings, "totalFound": 2}))
        return FakeResponse(text=json.dumps({"jobAd": {}}))

    fetcher = FakeFetcher(route)
    scraper = SmartRecruitersScraper("acme", fetcher=fetcher)
    scraper.have_details = frozenset()

    scraper.fetch_raw()

    assert [url.rsplit("/", 1)[1] for url in fetcher.urls()[1:]] == ["1"]


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
    text = scraper.read_detail({"id": "1"}, _Resp())["description"]
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
    assert (
        get_scraper("smartrecruiters", "acme")._salary_field(compensation) == expected
    )


def test_smartrecruiters_read_detail_reads_description_and_compensation_from_one_response():
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

    detail = SmartRecruitersScraper("acme").read_detail({"id": "1"}, _Resp())
    assert detail == {
        "description": "<p>Build things</p>",
        "compensation": {
            "min": 70000,
            "max": 85000,
            "currency": "EUR",
            "period": "YEARLY",
        },
    }


def test_smartrecruiters_read_detail_missing_compensation_is_none():
    from headstart.scrapers.smartrecruiters import SmartRecruitersScraper

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"jobAd": {"sections": {"jobDescription": {"text": "<p>Role</p>"}}}}

    detail = SmartRecruitersScraper("acme").read_detail({"id": "1"}, _Resp())
    assert detail["compensation"] is None


def test_detail_without_a_native_id_is_not_counted_as_attempted():
    scraper = get_scraper("smartrecruiters", "acme")

    assert scraper.fetch_detail({"id": None}) is None
    scraper.report_detail_gaps([None], "details")

    assert scraper.telemetry["detail_jobs"] == 1
    assert scraper.telemetry["detail_attempted"] == 0
    assert scraper.telemetry["detail_losses"] == 1
    assert scraper.telemetry["detail_loss_causes"] == {"no posting id": 1}


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


@pytest.mark.parametrize(
    ("read", "total", "authoritative"),
    [
        (6378, 6379, True),  # accorhotel, 2026-09-24
        (4809, 4810, True),  # boschgroup, 2026-09-24
        (4700, 4810, False),  # 97.7%: below MIN_AUTHORITATIVE_SHARE
    ],
)
def test_smartrecruiters_tolerates_only_a_negligible_shortfall(
    monkeypatch, read, total, authoritative
):
    """`totalFound` is exact (ADR-0070) and no cap is enforced (#227), so a short read is a
    *measured* shortfall and goes through ADR-0121's tolerance. Unconditional, one posting
    closing mid-crawl cost `accorhotel` and `boschgroup` their whole eviction scope."""
    scraper = get_scraper("smartrecruiters", "acme", "Acme")
    _sr_offline(
        monkeypatch,
        scraper,
        {
            "offset": 0,
            "limit": 100,
            "totalFound": total,
            "content": [{"id": str(n)} for n in range(read)],
        },
    )

    if authoritative:
        assert scraper.truncated is None
    else:
        assert scraper.truncated == f"read {read} of {total} postings — the rest unread"


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
    that loses postings mid-read lands exactly here. The reason string feeds the shard report
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
            # page 1 counted 100 postings that have since closed: past ADR-0121's tolerance
            # (4999/5099 = 98.0%), since a single one would now stay authoritative
            "totalFound": board + 100,
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


def _ripplehire_board(listing_rows, detail_for):
    """A RippleHire Board whose token GET, search POST and per-job detail GETs a FakeFetcher
    answers: ``detail_for(job_seq)`` gives each detail's response."""
    from urllib.parse import parse_qs, urlsplit

    from headstart.scrapers.ripplehire import RippleHireScraper

    def route(method, url, kwargs):
        if url.endswith("/candidate/careers"):
            return FakeResponse(url="https://x.ripplehire.com/candidate/?token=TOK123")
        if "candidatejobsearch" in url:
            listing = {"jobVoList": listing_rows, "totalJobCount": len(listing_rows)}
            return FakeResponse(text=json.dumps(listing))
        return detail_for(parse_qs(urlsplit(url).query)["jobSeq"][0])

    fetcher = FakeFetcher(route)
    return RippleHireScraper("x", "X", fetcher=fetcher), fetcher


def _ripplehire_detail_response(record: dict) -> FakeResponse:
    return FakeResponse(text=json.dumps({"jobVO": record}))


@pytest.mark.parametrize("async_fanout", ["1", "0"])
def test_ripplehire_fetch_raw_fills_jobdesc_from_detail(monkeypatch, async_fanout):
    """The search list always carries jobDesc: null — the detail JSON must fill it, and only a
    description-less row costs a detail call. Either transport sends the same request."""
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", async_fanout)
    scraper, fetcher = _ripplehire_board(
        [
            {"jobSeq": 1, "jobTitle": "SRE", "jobDesc": None},
            {"jobSeq": 2, "jobTitle": "Filled", "jobDesc": "<p>have</p>"},
        ],
        lambda job_seq: _ripplehire_detail_response(
            {"jobDesc": "<p>3+ years of Kubernetes</p>"}
        ),
    )

    raw = scraper.fetch_raw()

    assert [row["jobDesc"] for row in raw] == [
        "<p>3+ years of Kubernetes</p>",
        "<p>have</p>",
    ]
    (detail_request,) = [
        request for request in fetcher.requests if "candidatejobdetail" in request.url
    ]
    assert "token=TOK123" in detail_request.url and "jobSeq=1" in detail_request.url
    assert detail_request.kwargs["headers"]["Accept"] == "application/json"


def test_ripplehire_fetch_raw_attaches_full_detail_record():
    """`fetch_raw` already fetches `jobVO` per job for the description — `parse` needs the rest
    of that same record (department/posted_at/employment_type/salary), so `fetch_raw` must keep
    it rather than reading only `jobDesc` back out of it and discarding the rest."""
    scraper, _fetcher = _ripplehire_board(
        [{"jobSeq": 1, "jobTitle": "SRE", "jobDesc": None}],
        lambda job_seq: _ripplehire_detail_response(
            {
                "jobDesc": "<p>desc</p>",
                "bussinessUnit": "Technology",
                "jobPostingDate": "23-Jun-2020",
            }
        ),
    )

    raw = scraper.fetch_raw()

    assert raw[0]["_detail"]["bussinessUnit"] == "Technology"
    assert raw[0]["_detail"]["jobPostingDate"] == "23-Jun-2020"


@pytest.mark.parametrize("async_fanout", ["1", "0"])
def test_ripplehire_a_record_without_jobdesc_keeps_its_fields_and_is_a_labelled_gap(
    caplog, monkeypatch, async_fanout
):
    """A record that arrives with no text is a description gap, labelled apart from a fetch that
    never landed — but its other fields are real, so the Job still ships its department."""
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", async_fanout)
    scraper, _fetcher = _ripplehire_board(
        [{"jobSeq": 1, "jobTitle": "SRE", "jobDesc": None}],
        lambda job_seq: _ripplehire_detail_response({"bussinessUnit": "Technology"}),
    )

    with caplog.at_level("INFO"):
        raw = scraper.fetch_raw()

    assert raw[0]["jobDesc"] is None
    (job,) = scraper.parse(raw, SCRAPED_AT)
    assert job.department == "Technology"
    assert scraper.telemetry["detail_losses"] == 1
    assert "1/1 descriptions missing (no jobDesc on the record x1)" in caplog.text


@pytest.mark.parametrize("async_fanout", ["1", "0"])
def test_ripplehire_a_lost_detail_is_labelled_and_the_job_still_ships(
    monkeypatch, async_fanout
):
    """A 200 without a ``jobVO`` and a refused request are both losses, each named; neither
    costs the Job, which ships on its listing fields."""
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", async_fanout)
    answers = {
        "1": FakeResponse(text=json.dumps({"status": "error"})),
        "2": FakeResponse(500, "down"),
    }
    scraper, _fetcher = _ripplehire_board(
        [
            {"jobSeq": 1, "jobTitle": "SRE", "jobDesc": None},
            {"jobSeq": 2, "jobTitle": "QA", "jobDesc": None},
        ],
        answers.__getitem__,
    )

    raw = scraper.fetch_raw()

    assert [row["_detail"] for row in raw] == [{}, {}]
    assert len(scraper.parse(raw, SCRAPED_AT)) == 2
    assert scraper.detail_losses == {"no jobVO on a 200": 1, "HTTP 500": 1}


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

    def _response(status, reason):
        r = models.Response()
        r.status_code, r.ok, r.reason = status, False, reason
        return r

    def _fetch(method, url, **kwargs):
        if ".darwinbox.in" in url:
            return _response(403, "Forbidden")  # the wall, on the tenant's real host
        return _response(500, "Internal Server Error")  # wrong TLD: "Invalid subdomain"

    monkeypatch.setattr(http, "fetch", _fetch)


class _FakeDarwinboxPage:
    """browser_http._Page's surface, answering the darwinbox API from canned pages."""

    def __init__(self, pages, job_counts=None):
        self.pages = pages
        self.posted = []
        self.job_counts = job_counts

    def post_json(self, path, body):
        self.posted.append(body)
        envelope = {"data": self.pages[body["page"] - 1]}
        if self.job_counts is not None:
            envelope["job_counts"] = self.job_counts
        return envelope

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


def test_darwinbox_browser_route_marks_a_measured_shortfall(monkeypatch):
    """The walled path checks `job_counts` too — a walled Board never reaches the curl loop's
    own check at all, so it needs its own (issue #549)."""
    from contextlib import contextmanager

    import headstart.browser_http as bh

    _darwinbox_curl_wall(monkeypatch)
    fake = _FakeDarwinboxPage([[{"id": "only"}]], job_counts=10)

    @contextmanager
    def _origin(page_url):
        yield fake

    monkeypatch.setattr(bh, "origin", _origin)
    scraper = get_scraper("darwinbox", "licious", "Licious")
    raw = scraper.fetch_raw()

    assert len(raw) == 1
    assert scraper.truncated and "job_counts=10" in scraper.truncated


def test_darwinbox_no_wall_no_browser_raises_the_last_error(monkeypatch):
    """Without a 403 there is nothing to escalate: the last real error surfaces."""
    from curl_cffi.requests import models
    from curl_cffi.requests.exceptions import HTTPError

    def _response(status, reason):
        r = models.Response()
        r.status_code, r.ok, r.reason = status, False, reason
        return r

    monkeypatch.setattr(
        http, "fetch", lambda m, u, **k: _response(500, "Internal Server Error")
    )
    with pytest.raises(HTTPError) as excinfo:
        get_scraper("darwinbox", "licious", "Licious").fetch_raw()
    assert excinfo.value.response.status_code == 500


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


def test_workday_job_url_drops_a_query_string_the_slug_carries():
    """The live gatesfoundation ledger slug carries `?source=...`, which `_parts()` already
    ignores. Appending the posting's path after it put the path inside the query string — a URL
    that loads the board root with no JobPosting (checked live 2/2)."""
    scraper = get_scraper(
        "workday",
        "https://gatesfoundation.wd1.myworkdayjobs.com/Gates?source=gatesfoundation.org",
        "Gates Foundation",
    )

    assert scraper.job_url("/job/Nairobi-Kenya/Officer_B021772-1") == (
        "https://gatesfoundation.wd1.myworkdayjobs.com/Gates/job/Nairobi-Kenya/"
        "Officer_B021772-1"
    )


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

    # The 404 detail now costs a second fetch — its public-page fallback (ADR-0099). That page
    # answers 200 with no JSON-LD here, so the 404 still settles as the loss this test names.
    outcomes = iter([429, 503, 503, 404, 200, 200])

    async def fake_fetch_async(session, method, url, **kw):
        status = next(outcomes)

        class _R:
            status_code = status
            text = "<html>no structured data</html>"

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
    #
    # Only the FIRST past-threshold Board in a process warns (ADR-0088's 2026-09-08 amendment),
    # and the `log.FirstOnly` that bounds it is module-level, so every test asserting a
    # WARNING replaces it rather than depending on which test ran first.
    from headstart import log
    from headstart.scrapers import workday

    monkeypatch.setattr(workday, "_DETAIL_LOSS_OVER_SHARE", log.FirstOnly(workday._log))
    caplog.set_level(logging.WARNING, logger="headstart.scrapers.workday")
    scraper._report_detail_losses([None, None, None, None, {"d": 1}], classes, 0)
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
    # `classify_exception` reports for a request the origin never gave a status to
    assert classes == Counter({"RequestException": 1, "no externalPath": 1})


def test_workday_complete_detail_pass_warns_about_nothing(monkeypatch, caplog):
    """The counterpart guard: a Board whose details all arrived must stay silent, so the
    new WARNING keeps meaning something."""
    from collections import Counter

    from headstart.scrapers.workday import WorkdayScraper

    caplog.set_level(logging.INFO, logger="headstart.scrapers.workday")
    WorkdayScraper("https://acme.wd1.myworkdayjobs.com/careers")._report_detail_losses(
        [{"d": 1}] * 5, Counter(), 0
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
        [None] + [{"d": 1}] * 9, Counter({"HTTP 404": 1}), 0
    )
    assert [r.levelno for r in caplog.records] == [logging.INFO]
    assert "1 of 10 detail(s) failed mid-crawl (HTTP 404 x1)" in caplog.text


def test_workday_no_external_path_reports_separately_from_fetch_failures(caplog):
    """A posting the listing gave no `externalPath` is not a *fetch* failure and must not be
    tallied as one.

    No request is made for it, so neither the retry ladder nor the spare-egress fallback was ever
    involved — and counting it inside "detail(s) failed mid-crawl" is what sent a 2026-09-09
    investigation hunting a network fix for a listing-side artefact (measured that day: 335 such
    postings over 125 Boards, 111 of them on `accenture/avanadecareers` alone). It gets its own
    line, and the mid-crawl tally reports only the one real fetch loss."""
    from collections import Counter

    from headstart.scrapers.workday import WorkdayScraper

    caplog.set_level(logging.INFO, logger="headstart.scrapers.workday")
    WorkdayScraper("https://acme.wd1.myworkdayjobs.com/careers")._report_detail_losses(
        [None] * 4 + [{"d": 1}] * 6,
        Counter({"no externalPath": 3, "HTTP 404": 1}),
        0,
    )
    assert "3 posting(s) carried no externalPath" in caplog.text
    # The tally counts the fetch failure only — not 4 of 10, and with no `unclassified` remainder
    # standing in for the three that were popped.
    assert "1 of 10 detail(s) failed mid-crawl (HTTP 404 x1)" in caplog.text
    assert "unclassified" not in caplog.text


def test_workday_all_postings_lacking_external_path_logs_no_failure_line(caplog):
    """A Board whose every loss is a no-URL stub reports the stubs and nothing else.

    The regression this pins is the WARNING escalation: `missing / len(details)` crosses
    `_MAX_LOST_DETAIL_SHARE` on such a Board, so before the split it raised an Actions annotation
    — against a run-level quota — for a Board where no request had failed at all."""
    from collections import Counter

    from headstart.scrapers.workday import WorkdayScraper

    caplog.set_level(logging.INFO, logger="headstart.scrapers.workday")
    WorkdayScraper("https://acme.wd1.myworkdayjobs.com/careers")._report_detail_losses(
        [None] * 5, Counter({"no externalPath": 5}), 0
    )
    assert "5 posting(s) carried no externalPath" in caplog.text
    assert "failed mid-crawl" not in caplog.text
    assert [r.levelno for r in caplog.records] == [logging.INFO]


def test_workday_titled_stub_warns_because_it_would_serve_a_dead_link(caplog):
    """A no-`externalPath` posting that *has* a title is the case the carve-out is unsafe for.

    The quiet no-URL line is justified by such a posting being dropped at the tech gate — which
    holds only because a stub has no title to classify on (measured: 0 of 38 stubs over 31,028
    postings on 22 boards, every one `bulletFields`-only). A titled one would pass the gate and
    ship with the board root as its url, so it warns instead of riding the same quiet line. This
    is the tripwire for the assumption, not a restatement of it."""
    from collections import Counter

    from headstart.scrapers.workday import WorkdayScraper

    caplog.set_level(logging.INFO, logger="headstart.scrapers.workday")
    WorkdayScraper("https://acme.wd1.myworkdayjobs.com/careers")._report_detail_losses(
        [None] * 2, Counter({"no externalPath": 2}), 1
    )
    assert "1 posting(s) had a title but no externalPath" in caplog.text
    assert [r.levelno for r in caplog.records] == [logging.INFO, logging.INFO]


def test_workday_stub_posting_parses_to_a_job_the_tech_gate_drops():
    """The real shape of a no-`externalPath` item, and why the loss costs nothing downstream.

    Captured live 2026-09-09 from `accenture/avanadecareers` (27 of 605 postings): the listing
    serves `bulletFields` and *nothing else* — no title, no path, no location. So `parse` yields
    an "Untitled" Job whose url falls back to the board root, and `tech_filter.classify` drops it
    before the description store or the index can see it. `_posting_key` still reads the req id
    off `bulletFields`, so the id is stable rather than churning (ADR-0097)."""
    from headstart.scrapers.workday import WorkdayScraper
    from headstart.tech_filter import classify

    scraper = WorkdayScraper("https://accenture.wd103.myworkdayjobs.com/avanadecareers")
    (job,) = scraper.parse([{"bulletFields": ["R00322521"]}], "2026-09-09T00:00:00Z")
    assert job.id.endswith(":R00322521")
    assert job.title == "Untitled"
    # The board root, not a job link — `parse`'s fallback when `external_path` is empty. Harmless
    # only because the title is "Untitled" and the tech gate drops it; `_report_detail_losses`
    # warns if a titled stub ever appears, which is when this url would really ship.
    assert job.url == "https://accenture.wd103.myworkdayjobs.com/avanadecareers"
    assert job.description is None
    assert not classify(job.title, job.department).is_tech


def test_only_the_first_board_past_the_share_spends_an_annotation(monkeypatch, caplog):
    """The threshold decides what a Board's gap *is*; it must not decide how loud a shard gets.

    Under Actions a WARNING is a workflow annotation and the budget is 10 per step / 50 per job,
    and the outage class this line exists to catch is not per-Board at all: ADR-0115's User-Agent
    denylist emptied the detail pass of 102 Boards in one run, which unbounded would burn the
    quota on the first ten of them. So the first past-threshold Board in the process warns and
    every later one states the identical line at INFO — the counts in it still say which side of
    the threshold each Board fell on (ADR-0088's 2026-09-08 amendment).
    """
    from collections import Counter

    from headstart import log
    from headstart.scrapers import workday
    from headstart.scrapers.workday import WorkdayScraper

    monkeypatch.setattr(workday, "_DETAIL_LOSS_OVER_SHARE", log.FirstOnly(workday._log))
    caplog.set_level(logging.INFO, logger="headstart.scrapers.workday")
    for site in ("first", "second", "third"):
        WorkdayScraper(
            f"https://acme.wd1.myworkdayjobs.com/{site}"
        )._report_detail_losses([None] * 4, Counter({"HTTP 403": 4}), 0)

    assert [r.levelno for r in caplog.records] == [
        logging.WARNING,
        logging.INFO,
        logging.INFO,
    ]
    # and the demoted ones are the same line, not a quieter summary of it
    assert caplog.text.count("4 of 4 detail(s) failed mid-crawl (HTTP 403 x4)") == 3


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
    # classes shown always sum to the loss count. `unlabelled` is `base.loss_breakdown`'s
    # spelling — workday's own copy of that formatter said `unclassified` for the same fact
    # until the two were merged.
    from headstart import log
    from headstart.scrapers import workday

    monkeypatch.setattr(workday, "_DETAIL_LOSS_OVER_SHARE", log.FirstOnly(workday._log))
    caplog.set_level(logging.WARNING, logger="headstart.scrapers.workday")
    scraper._report_detail_losses([None] * 4, Counter({"HTTP 404": 1}), 0)
    assert (
        "4 of 4 detail(s) failed mid-crawl (unlabelled x3, HTTP 404 x1)" in caplog.text
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
    from headstart import log
    from headstart.scrapers import workday

    monkeypatch.setattr(workday, "_DETAIL_LOSS_OVER_SHARE", log.FirstOnly(workday._log))
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


def test_workday_job_url_follows_the_resolved_instance(monkeypatch):
    """The served link follows the pod that serves the Board (ADR-0157's 2026-09-23
    amendment): on netflix's stale wd1 a job page 500s while wd108 serves it, so a link pinned
    to the slug's own pod was dead for every posting on the Board."""
    monkeypatch.setattr("headstart.http.fetch", _workday_fetch_stub("wd108"))
    s = get_scraper(
        "workday", "https://netflix.wd1.myworkdayjobs.com/netflix", "Netflix"
    )
    path = "/job/Los-Gatos-California/Software-Engineer_JR32657"

    assert s.job_url(path) == f"https://netflix.wd1.myworkdayjobs.com/netflix{path}"
    s._resolve_instance()
    assert s.job_url(path) == f"https://netflix.wd108.myworkdayjobs.com/netflix{path}"


def test_workday_leaves_instance_when_none_serves(monkeypatch):
    # gone everywhere (422 on all DCs) -> keep hinted; crawl yields nothing
    monkeypatch.setattr("headstart.http.fetch", _workday_fetch_stub("nowhere"))
    s = get_scraper("workday", "https://gone.wd3.myworkdayjobs.com/careers", "Gone")
    s._resolve_instance()
    assert s._instance is None
    assert ".wd3." in s.url()


class _AliasResp:
    """A settled `http.fetch` response, for `alias_key` — needs `.url` (where it landed) and
    `.close()` (the real method streams and discards the body unread)."""

    def __init__(self, url):
        self.url = url

    def close(self):
        pass


def test_workday_alias_key_fetches_the_public_page_not_the_cxs_api(monkeypatch):
    """The base default's mistake for this ATS, corrected: `url()` is the JSON endpoint, which
    nothing ever redirects a person to. `alias_key` must hit the marketing page instead."""
    seen = {}

    def fetch(method, url, **kwargs):
        seen["url"] = url
        return _AliasResp(url)

    monkeypatch.setattr("headstart.http.fetch", fetch)
    get_scraper(
        "workday", "https://acme.wd3.myworkdayjobs.com/careers", "Acme"
    ).alias_key()
    assert (
        seen["url"] == "https://acme.wd3.myworkdayjobs.com/careers"
    )  # not /wday/cxs/.../jobs


def test_workday_alias_key_resolves_to_itself_when_nothing_redirects(monkeypatch):
    # the measured shape for 384 of 400 sampled Boards (2026-09-11): no redirect at all
    monkeypatch.setattr(
        "headstart.http.fetch", lambda method, url, **kw: _AliasResp(url)
    )
    s = get_scraper("workday", "https://acme.wd3.myworkdayjobs.com/careers", "Acme")
    assert s.alias_key() == "https://acme.wd3.myworkdayjobs.com/careers"


def test_workday_alias_key_follows_a_real_redirect(monkeypatch):
    monkeypatch.setattr(
        "headstart.http.fetch",
        lambda method, url, **kw: _AliasResp(
            "https://acme.wd3.myworkdayjobs.com/NewCareers"
        ),
    )
    s = get_scraper("workday", "https://acme.wd3.myworkdayjobs.com/OldCareers", "Acme")
    assert s.alias_key() == "https://acme.wd3.myworkdayjobs.com/NewCareers"


def test_workday_alias_key_strips_the_tombstone_query_string(monkeypatch):
    """Workday's own outage page appends a per-request `?d=&s=&e=&o=` tail (measured on 2 of 400
    sampled Boards) that would otherwise make every tombstone visit compare as a different key —
    and the stripped result must be the exact string `alias_vendor_hosts` names, or the tombstone
    label silently never fires."""
    from headstart.scrapers.workday import WorkdayScraper

    tombstone = "https://community.workday.com/maintenance-page?d=3&s=1&e=1&o="
    monkeypatch.setattr(
        "headstart.http.fetch", lambda method, url, **kw: _AliasResp(tombstone)
    )
    s = get_scraper("workday", "https://gone.wd3.myworkdayjobs.com/careers", "Gone")
    key = s.alias_key()
    assert key == "https://community.workday.com/maintenance-page"
    assert key in WorkdayScraper.alias_vendor_hosts


def test_workday_alias_key_is_none_when_unreachable(monkeypatch):
    # conservative direction (base default's own docstring): no verdict, never a false duplicate
    def fetch(method, url, **kw):
        raise TimeoutError("no route")

    monkeypatch.setattr("headstart.http.fetch", fetch)
    s = get_scraper("workday", "https://acme.wd3.myworkdayjobs.com/careers", "Acme")
    assert s.alias_key() is None


def test_workday_alias_key_is_none_on_a_malformed_slug_not_a_crash(monkeypatch):
    """`_parts()` raises `ValueError` on a slug `CAREERS_URL_PATTERN` cannot parse, and the real
    caller, `dedupe_boards.py`'s `probe_all`, reads this method's result from an unguarded
    `future.result()` inside a `ThreadPoolExecutor` -- one malformed slug anywhere in a
    12,844-Board scan would abort the whole run on whichever Board happened to raise, not just
    mark that one unreachable. `alias_key` must never let that escape."""

    def fetch(method, url, **kw):
        raise AssertionError("must not be reached: _parts() should have failed first")

    monkeypatch.setattr("headstart.http.fetch", fetch)
    s = get_scraper("workday", "not-a-careers-url", "Acme")
    assert s.alias_key() is None


def test_workday_paginate_logs_once_on_missing_pages(monkeypatch, caplog):
    # a mid-crawl 404 (None from _post_async) skips that page but keeps the rest, and one
    # INFO line reports the gap — the tripwire for a partial board. INFO rather than WARNING
    # because it fires once per Board and WARNING is a run-level annotation quota under
    # Actions (ADR-0039's 2026-09-08 amendment; tests/test_log_levels.py pins it)
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
    caplog.set_level(logging.INFO, logger="headstart.scrapers.workday")
    s._paginate({}, 100, absorbed.extend)
    # the surviving pages are all absorbed — fanned out concurrently now, so not guaranteed to
    # land in offset order (the postings they build are deduplicated/looked up by id, never by
    # position, so this doesn't need to assert order to prove the pagination is correct)
    assert sorted(p["bulletFields"][0] for p in absorbed) == ["R20", "R60", "R80"]
    reported = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(reported) == 1
    assert reported[0].name == "headstart.scrapers.workday"
    # 1 of 5, not 1 of 4: the first page `_exhaust` already holds counts too
    assert "1 of 5 page(s) failed" in reported[0].getMessage()
    assert "workday:acme/ext" in reported[0].getMessage()  # the board key
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
    caplog.set_level(logging.INFO, logger="headstart.scrapers.workday")
    s._paginate({}, 100, absorbed.extend)

    assert sorted(p["bulletFields"][0] for p in absorbed) == ["R20", "R60", "R80"]
    reported = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(reported) == 1
    assert "1 of 5 page(s) failed" in reported[0].getMessage()
    # and the gap travels with the Jobs, or `index sync` reads it as delistings (ADR-0053)
    assert s.truncated is not None


def test_workday_paginate_shows_every_cause_with_no_cap(monkeypatch, caplog):
    """`_paginate`'s own mid-crawl summary used to hand-roll a `most_common(4)` cap with no
    residual accounting at all — an independent copy of `loss_breakdown`'s old shape, and worse:
    a 5th+ cause just vanished, with not even a sized tail to say so. It now formats through
    `loss_breakdown` directly, so every cause shows and the two formatters can't drift apart
    again the way `_failure_class` already had to be unified out of existence once."""
    from headstart import http
    from headstart.scrapers import workday as workday_mod
    from headstart.scrapers.workday import WorkdayScraper

    def _err(status):
        exc = http.RequestsError(f"HTTP Error {status}")
        exc.response = type("FakeResponse", (), {"status_code": status})()
        return exc

    failing = {20: 429, 40: 500, 60: 403, 80: 400}

    async def fake_post_async(session, applied, offset):
        if offset in failing:
            raise _err(failing[offset])
        if offset == 100:
            return None  # 404ed mid-crawl
        return {"jobPostings": []}

    s = WorkdayScraper("https://acme.wd1.myworkdayjobs.com/ext")
    monkeypatch.setattr(s, "_post_async", fake_post_async)
    caplog.set_level(logging.INFO, logger="headstart.scrapers.workday")
    s._paginate({}, workday_mod._PAGE_LIMIT * 20, lambda batch: None)

    reported = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(reported) == 1
    message = reported[0].getMessage()
    assert "5 of 20 page(s) failed mid-crawl" in message
    for label in (
        "HTTP 429 x1",
        "HTTP 500 x1",
        "HTTP 403 x1",
        "HTTP 400 x1",
        "404 mid-crawl x1",
    ):
        assert label in message


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


# --- jsapi.recruiterbox.com — the primary listing surface (module docstring) ------------------


def test_trakstar_api_location_joins_city_state_country():
    from headstart.scrapers.trakstar import _api_location

    assert (
        _api_location(
            {
                "city": "Bengaluru",
                "state": "Karnataka",
                "country": "India",
                "zipcode": "1",
            }
        )
        == "Bengaluru, Karnataka, India"
    )
    assert _api_location({"city": None, "state": None, "country": None}) is None
    assert _api_location(None) is None
    assert _api_location("not a dict") is None


def test_trakstar_jobs_from_api_maps_every_field():
    from headstart.scrapers.trakstar import _jobs_from_api

    items = [
        {
            "id": "fk0z83d",
            "title": "Backend Engineer",
            "description": "<p>Build things.</p>",
            "location": {"city": "Bengaluru", "state": "Karnataka", "country": "India"},
            "allows_remote": False,
            "position_type": "full_time",
            "team": "Tech & Product",
            "hosted_url": "https://exotel.hire.trakstar.com/jobs/fk0z83d/",
        }
    ]
    (job,) = _jobs_from_api("trakstar", "exotel", "Exotel", items, SCRAPED_AT)
    assert job.id == "trakstar:exotel:fk0z83d"
    assert job.url == "https://exotel.hire.trakstar.com/jobs/fk0z83d/"
    assert job.location == "Bengaluru, Karnataka, India"
    assert job.remote is False
    assert job.department == "Tech & Product"
    assert job.employment_type == "Full-time"
    assert job.description == "Build things."
    assert (
        job.posted_at is None
    )  # this surface states no posting date (module docstring)


def test_trakstar_jobs_from_api_skips_an_item_with_no_id_or_no_title():
    from headstart.scrapers.trakstar import _jobs_from_api

    items = [
        {"id": "", "title": "Ghost"},
        {"id": "x1", "title": "  "},
        {"id": "x2", "title": "Kept"},
    ]
    jobs = _jobs_from_api("trakstar", "acme", "Acme", items, SCRAPED_AT)
    assert [j.title for j in jobs] == ["Kept"]


def test_trakstar_jobs_from_api_maps_unset_position_type_to_none():
    """Measured live: `position_type` is `""`, not absent, when a tenant hasn't set it."""
    from headstart.scrapers.trakstar import _jobs_from_api

    items = [{"id": "x1", "title": "T", "position_type": ""}]
    (job,) = _jobs_from_api("trakstar", "acme", "Acme", items, SCRAPED_AT)
    assert job.employment_type is None


def test_trakstar_api_page_returns_none_on_a_non_200(monkeypatch):
    from headstart.scrapers.trakstar import TrakstarScraper

    class _Resp:
        status_code = 400
        text = '{"client_name": "Invalid client name"}'

    scraper = TrakstarScraper("nonexistent")
    monkeypatch.setattr(scraper, "_fetch", lambda *a, **k: _Resp())
    assert scraper._api_page(0) is None


def test_trakstar_api_page_returns_none_on_unparseable_json(monkeypatch):
    from headstart.scrapers.trakstar import TrakstarScraper

    class _Resp:
        status_code = 200
        text = "not json"

    scraper = TrakstarScraper("acme")
    monkeypatch.setattr(scraper, "_fetch", lambda *a, **k: _Resp())
    assert scraper._api_page(0) is None


def test_trakstar_api_listing_returns_none_when_the_first_page_is_unreachable(
    monkeypatch,
):
    """This is the signal `fetch_raw` uses to fall back to the HTML+RSS+detail path — a tenant
    with no jsapi surface, not merely a short one."""
    from headstart.scrapers.trakstar import TrakstarScraper

    scraper = TrakstarScraper("nonexistent")
    monkeypatch.setattr(scraper, "_api_page", lambda offset: None)
    assert scraper._api_listing() is None
    assert scraper.truncated is None  # unreachable is not the same claim as short


def test_trakstar_api_listing_walks_by_the_page_size_actually_returned(monkeypatch):
    """Measured live 2026-09-22: the server clamps `limit` to 250 regardless of a higher ask, so
    the walk steps by `len(batch)` rather than trusting `_API_LIMIT` to stay accurate."""
    from headstart.scrapers.trakstar import TrakstarScraper

    pages = {
        0: {"meta": {"total": 5}, "objects": [{"id": str(i)} for i in range(3)]},
        3: {"meta": {"total": 5}, "objects": [{"id": str(i)} for i in range(3, 5)]},
    }
    scraper = TrakstarScraper("acme")
    monkeypatch.setattr(scraper, "_api_page", lambda offset: pages[offset])

    items = scraper._api_listing()

    assert [i["id"] for i in items] == ["0", "1", "2", "3", "4"]
    assert scraper.truncated is None


def test_trakstar_api_listing_marks_truncated_when_a_later_page_fails(monkeypatch):
    from headstart.scrapers.trakstar import TrakstarScraper

    pages = {
        0: {"meta": {"total": 5}, "objects": [{"id": str(i)} for i in range(3)]},
    }
    scraper = TrakstarScraper("acme")
    monkeypatch.setattr(scraper, "_api_page", lambda offset: pages.get(offset))

    items = scraper._api_listing()

    assert len(items) == 3  # what arrived is still kept
    assert scraper.truncated and "offset 3" in scraper.truncated


def test_trakstar_api_listing_reports_a_shortfall_against_the_stated_total(monkeypatch):
    """`meta.total` is exactly the stated total `mark_truncated_unless_negligible` wants
    (ADR-0121) — a walk that ends (a short final page) but under-reads the total must still be
    measured against it, not treated as a clean natural end."""
    from headstart.scrapers.trakstar import TrakstarScraper

    scraper = TrakstarScraper("acme")
    monkeypatch.setattr(
        scraper,
        "_api_page",
        lambda offset: (
            {"meta": {"total": 100}, "objects": [{"id": "1"}]}
            if offset == 0
            else {"meta": {"total": 100}, "objects": []}
        ),
    )

    items = scraper._api_listing()

    assert len(items) == 1
    assert scraper.truncated and "1 of 100" in scraper.truncated


def test_trakstar_fetch_raw_prefers_the_api_and_never_touches_the_careers_page(
    monkeypatch,
):
    scraper = get_scraper("trakstar", "acme", "Acme")
    monkeypatch.setattr(
        scraper, "_api_listing", lambda: [{"id": "1", "title": "Engineer"}]
    )

    def boom_get(url=None):
        raise AssertionError("must not fetch the careers page when the API answered")

    monkeypatch.setattr(scraper, "_get", boom_get)

    raw = scraper.fetch_raw()

    assert raw == {"api_items": [{"id": "1", "title": "Engineer"}]}
    jobs = scraper.parse(raw, SCRAPED_AT)
    assert jobs[0].id == "trakstar:acme:1"


def _trakstar_board(careers_page, *, feed=None, job_pages=None):
    """A Trakstar scraper behind a fake fetcher: no jsapi board (its 400), ``careers_page`` as the
    careers page, ``feed`` as the RSS feed (a 404 when None) and each job page from ``job_pages``
    by code (a 404 when absent). Returns the scraper and the fake, which records every request."""
    from headstart.scrapers.trakstar import TrakstarScraper

    job_pages = job_pages or {}

    def route(method, url, kwargs):
        if url.startswith("https://jsapi.recruiterbox.com/"):
            return FakeResponse(400)
        if "/jobfeeds/" in url:
            return FakeResponse(404) if feed is None else FakeResponse(text=feed)
        if "/jobs/" in url:
            code = url.rstrip("/").rsplit("/", 1)[1]
            page = job_pages.get(code)
            return FakeResponse(404) if page is None else FakeResponse(text=page)
        return FakeResponse(text=careers_page)

    fetcher = FakeFetcher(route)
    return TrakstarScraper("acme", "Acme", fetcher=fetcher), fetcher


def _trakstar_job_pages_requested(fetcher):
    return [url for url in fetcher.urls() if "/jobs/" in url]


def test_trakstar_fetch_raw_falls_back_to_the_careers_page_when_the_api_is_unreachable():
    scraper, _fetcher = _trakstar_board(_trakstar_cards_page(1, total=1))

    raw = scraper.fetch_raw()

    assert "html" in raw  # the pre-existing path answered instead


#: A job card as `read_detail` receives it — `(block, code)`; the reader looks at neither.
_TRAKSTAR_CARD = ("<card/>", "code0")


def test_trakstar_read_detail_prefers_jsonld_when_present():
    from headstart.scrapers.trakstar import TrakstarScraper

    page = """<html><head>
    <script type="application/ld+json">
    {"@context": "http://schema.org", "@type": "JobPosting",
     "title": "Backend Engineer", "datePosted": "2026-03-01",
     "description": "&lt;p&gt;Build the platform.&lt;/p&gt;"}
    </script></head><body>
    <div class="jobdesciption"><p>Ignored -- JSON-LD wins when both are present.</p></div>
    </body></html>"""
    posting = TrakstarScraper("acme").read_detail(
        _TRAKSTAR_CARD, FakeResponse(text=page)
    )
    assert posting["datePosted"] == "2026-03-01"
    assert posting["description"] == "&lt;p&gt;Build the platform.&lt;/p&gt;"


def test_trakstar_read_detail_falls_back_to_html_when_jsonld_absent():
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
    posting = TrakstarScraper("acme").read_detail(
        _TRAKSTAR_CARD, FakeResponse(text=page)
    )
    assert "datePosted" not in posting  # not present anywhere on these pages
    text = html_to_text(posting["description"])
    assert text == (
        "Build the payments platform end to end. Requirements: 3+ years of Python experience."
    )
    assert (
        "apply here" not in text
    )  # stops at the container's own close, not a later one


def test_trakstar_read_detail_names_the_loss_when_neither_jsonld_nor_html_present():
    from headstart.scrapers.base import DetailLost
    from headstart.scrapers.trakstar import TrakstarScraper

    page = "<html><body><p>No JSON-LD and no .jobdesciption div here.</p></body></html>"
    with pytest.raises(DetailLost) as lost:
        TrakstarScraper("acme").read_detail(_TRAKSTAR_CARD, FakeResponse(text=page))
    assert lost.value.cause == "no JSON-LD and no description on a 200"


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


_TRAKSTAR_FEED_URL = "https://acme.hire.trakstar.com/jobfeeds/acme"


def _trakstar_feed_fetcher(status: int, feed: str = ""):
    """The shared fake, answering acme's feed request with ``status``/``feed``."""
    from fake_fetcher import FakeFetcher, FakeResponse

    return FakeFetcher(lambda _method, _url, _kwargs: FakeResponse(status, feed))


def test_trakstar_fetch_via_feed_returns_none_when_feed_unavailable():
    fake = _trakstar_feed_fetcher(404)
    scraper = get_scraper("trakstar", "acme", "Acme", fetcher=fake)
    assert scraper.fetch_via_feed(SCRAPED_AT) is None
    assert fake.urls() == [_TRAKSTAR_FEED_URL]


def test_trakstar_fetch_via_feed_returns_empty_list_when_feed_has_zero_jobs():
    # a working feed reporting zero current openings must be distinguishable from "no feed at
    # all" — a caller checking `is None` sees the difference; one that checks truthiness doesn't
    empty_feed = '<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>'
    fake = _trakstar_feed_fetcher(200, empty_feed)
    scraper = get_scraper("trakstar", "acme", "Acme", fetcher=fake)
    result = scraper.fetch_via_feed(SCRAPED_AT)
    assert result == []
    assert result is not None


def test_trakstar_fetch_via_feed_returns_jobs_when_available():
    fake = _trakstar_feed_fetcher(200, _TRAKSTAR_FEED)
    scraper = get_scraper("trakstar", "acme", "Acme", fetcher=fake)
    jobs = scraper.fetch_via_feed(SCRAPED_AT)
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


def test_trakstar_fetch_raw_uses_feed_when_capped_and_skips_the_detail_pass():
    """A capped Board (sleekr/colcare-shaped: 25 cards, a higher total) whose feed answers must
    return the feed's jobs -- and must never fetch a single per-job detail page for the cards
    it's about to discard (those pages sit behind DataDome; the feed already has the full
    description inline)."""
    import headstart.scrapers.trakstar as trakstar_module

    scraper, fetcher = _trakstar_board(
        _trakstar_cards_page(25, total=40), feed=_TRAKSTAR_FEED
    )

    raw = scraper.fetch_raw()

    # the capped cards' detail pages were never fetched
    assert _trakstar_job_pages_requested(fetcher) == []
    assert raw == {"feed_items": trakstar_module._feed_items(_TRAKSTAR_FEED)}
    jobs = scraper.parse(raw, SCRAPED_AT)
    assert len(jobs) == 2
    assert jobs[0].id == "trakstar:acme:fk0abc1"
    # the feed answered in full -- this Board is not short
    assert scraper.truncated is None


@pytest.mark.parametrize("async_fanout", ["1", "0"])
def test_trakstar_uncapped_board_skips_the_feed_and_reads_every_page_four_wide(
    monkeypatch, async_fanout
):
    """The 92%+ of Boards under the render cap must cost exactly the one careers-page request
    they always did -- no RSS fetch, since there's nothing the cards are missing -- and the
    detail pass then reads every card's page, on either transport, pinned to its DataDome
    width of 4 threads or streams."""
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", async_fanout)
    posting = (
        '<script type="application/ld+json">{"@type": "JobPosting", '
        '"datePosted": "2026-03-01", "description": "Build it."}</script>'
    )
    scraper, fetcher = _trakstar_board(
        _trakstar_cards_page(3, total=3),
        feed=_TRAKSTAR_FEED,
        job_pages={"code0": posting, "code1": posting, "code2": posting},
    )
    monkeypatch.setenv("HEADSTART_H2_STREAMS", "100")  # the operator cannot widen it
    fanout_stats.reset()

    raw = scraper.fetch_raw()

    assert "feed_items" not in raw
    assert not any("/jobfeeds/" in url for url in fetcher.urls())
    assert set(raw["postings"]) == {"code0", "code1", "code2"}
    assert [job.posted_at for job in scraper.parse(raw, SCRAPED_AT)] == [
        "2026-03-01"
    ] * 3
    assert set(fanout_stats.stats()) == {("trakstar details", 4)}
    assert scraper.truncated is None


def test_trakstar_fetch_raw_keeps_html_when_feed_unreachable():
    """sleekr-shaped live case: capped (25 cards, real total higher) but the feed 404s. The
    capped HTML list must still come back -- not an empty Board -- and the Board must be marked
    truncated now that the page's own total makes the shortfall provable, not just suspected."""
    scraper, fetcher = _trakstar_board(_trakstar_cards_page(25, total=77))

    raw = scraper.fetch_raw()

    assert "feed_items" not in raw
    assert len(_trakstar_job_pages_requested(fetcher)) == 25
    jobs = scraper.parse(raw, SCRAPED_AT)
    assert len(jobs) == 25
    assert scraper.truncated is not None
    assert "unreachable" in scraper.truncated


def test_trakstar_fetch_raw_does_not_mark_truncated_for_card_count_heuristic_alone():
    """A Board with no "View N Openings" total on the page (_is_capped falls back to the bare
    card-count heuristic) that also lands on the cap and has an unreachable feed must NOT be
    marked truncated -- this is the same ambiguous "reached the cap" signal the pre-fix code
    deliberately declined to mark_truncated for; only the page's own total turns that into
    proof, and this Board never had one."""
    scraper, fetcher = _trakstar_board(_trakstar_cards_page(25))  # no total button

    raw = scraper.fetch_raw()

    assert "feed_items" not in raw
    assert len(_trakstar_job_pages_requested(fetcher)) == 25
    assert scraper.truncated is None


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
    _salary = get_scraper("recruitee", "acme")._salary_field

    assert _salary(None) is None
    assert _salary({"min": None, "max": None}) is None  # blank -> None, job still kept
    assert _salary(
        {"min": 50000, "max": 70000, "currency": "EUR", "period": "year"}
    ) == ("50000-70000 EUR year")
    assert _salary({"min": 80000, "currency": "USD"}) == "80000 USD"  # one-sided range


def test_recruitee_ceiling_only_salary_is_refused():
    """A lone figure reads as a floor (`salary.extract` has no spelling for a ceiling), so
    oralcare's "up to EUR 5,339 a month" would serve as a EUR 64k/yr floor."""
    _salary = get_scraper("recruitee", "acme")._salary_field
    assert (
        _salary({"min": None, "max": "5339", "currency": "EUR", "period": "month"})
        is None
    )


def _teamtailor_pages(monkeypatch, scraper, pages):
    """Serve `pages` (a list of item-id lists) from jobs.json, recording each URL requested.

    `fetch_raw` also makes one `jobs.rss` request for the department/remote enrichment join —
    served here as an empty, well-formed feed, so callers that only care about the jobs.json
    pagination walk don't need their own RSS fixture. `asked` records that request too (it is a
    real request against the Board), so an assertion on its length must count it.
    """
    asked: list[str] = []

    def _get(self, url=None):
        asked.append(url or "")
        if url and url.endswith("jobs.rss"):
            return '<rss version="2.0"><channel></channel></rss>'
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
    assert "page=2" in asked[1]
    assert (
        len(asked) == 3
    )  # 2 listing pages, stopped on the short one, + 1 rss enrichment call


def test_teamtailor_single_page_board_costs_one_request(monkeypatch):
    """The common case must not pay for pagination — 748 of 766 Boards are one page. It does pay
    one further request for the jobs.rss enrichment join (+1 request per Board, module
    docstring), so this Board costs two requests total, not one."""
    s = get_scraper("teamtailor", "small", "Small")
    asked = _teamtailor_pages(monkeypatch, s, [[1, 2, 3]])

    assert len(s.parse(s.fetch_raw(), SCRAPED_AT)) == 3
    assert len(asked) == 2


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
    assert len(asked) == 3  # 2 listing pages before it stopped, + 1 rss enrichment call
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
    assert (
        len(asked) == n_pages + 2
    )  # every listing page + the short last one + 1 rss call
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


def test_teamtailor_parse_with_no_rss_enrichment_falls_back_to_the_location_guess():
    """`parse` is also called directly on a raw dict with no `_rss_enrichment` key (e.g. this
    fixture, or any caller that built `raw` by hand) — must not crash, and must fall back to the
    pre-existing `is_remote(location)` guess rather than serving `department=None`/`remote=None`
    as if the join ran and found nothing."""
    jobs = get_scraper("teamtailor", "1komma5", "1KOMMA5").parse(
        _load("teamtailor_1komma5.json"), SCRAPED_AT
    )
    j = jobs[0]
    assert j.department is None  # nothing to join against
    from headstart.models import is_remote

    assert j.remote == is_remote(j.location)


def test_teamtailor_rss_enrichment_fills_department_and_remote(monkeypatch):
    """The join itself: `jobs.rss`'s `<guid>` keys onto the `jobs.json` item `id`."""

    s = get_scraper("teamtailor", "acme", "Acme")
    rss = """<rss version="2.0" xmlns:tt="https://teamtailor.com/locations">
    <channel>
      <item>
        <guid>abc-1</guid>
        <remoteStatus>fully</remoteStatus>
        <tt:department>Engineering</tt:department>
      </item>
    </channel></rss>"""
    monkeypatch.setattr(s, "_get", lambda url=None: rss)

    enrichment = s._rss_enrichment()
    assert enrichment == {"abc-1": {"department": "Engineering", "remote": True}}

    raw = {
        "items": [{"id": "abc-1", "title": "X", "url": "u"}],
        "_rss_enrichment": enrichment,
    }
    (job,) = s.parse(raw, SCRAPED_AT)
    assert job.department == "Engineering"
    assert job.remote is True


def test_teamtailor_remote_status_vocabulary(monkeypatch):
    """The live vocabulary is fully/hybrid/none/onsite (module docstring), not upstream's
    fully/none-only enum — mapped the same way ashby/workday/bamboohr resolve an explicit
    "hybrid": True/False on the unambiguous ends, None on the middle."""
    s = get_scraper("teamtailor", "acme", "Acme")
    enrichment = {
        "fully-1": {"department": None, "remote": True},
        "hybrid-1": {"department": None, "remote": None},
        "none-1": {"department": None, "remote": False},
        "onsite-1": {"department": None, "remote": False},
    }
    raw = {
        "items": [{"id": k, "title": k, "url": "u"} for k in enrichment],
        "_rss_enrichment": enrichment,
    }
    jobs = {j.id.rsplit(":", 1)[1]: j for j in s.parse(raw, SCRAPED_AT)}
    assert jobs["fully-1"].remote is True
    assert jobs["hybrid-1"].remote is None
    assert jobs["none-1"].remote is False
    assert jobs["onsite-1"].remote is False


def test_teamtailor_rss_department_is_tenant_optional(monkeypatch):
    """Measured live: two boards state `tt:department` on zero of 109 combined postings — a
    guid the RSS covers but with no department tag still fills `remote`, not a parse failure."""
    s = get_scraper("teamtailor", "acme", "Acme")
    rss = """<rss version="2.0" xmlns:tt="https://teamtailor.com/locations">
    <channel><item><guid>x-1</guid><remoteStatus>onsite</remoteStatus></item></channel></rss>"""
    monkeypatch.setattr(s, "_get", lambda url=None: rss)

    enrichment = s._rss_enrichment()
    assert enrichment == {"x-1": {"department": None, "remote": False}}


def test_teamtailor_rss_enrichment_is_additive_on_fetch_failure(monkeypatch):
    """A `jobs.rss` fetch failure must not cost the Board its listing — this is an enrichment
    join, not a dependency, and today's every-Board behaviour (no department, guessed remote)
    is the correct degrade."""
    s = get_scraper("teamtailor", "acme", "Acme")

    def _get(url=None):
        if url and url.endswith("jobs.rss"):
            raise http.RequestsError("boom")
        return json.dumps({"title": "Co", "items": [{"id": "j1", "title": "X"}]})

    monkeypatch.setattr(s, "_get", _get)
    raw = s.fetch_raw()
    assert raw["_rss_enrichment"] == {}
    (job,) = s.parse(raw, SCRAPED_AT)
    assert job.department is None
    assert s.truncated is None  # an enrichment failure is not a listing truncation


def test_teamtailor_rss_enrichment_ignores_malformed_xml(monkeypatch):
    s = get_scraper("teamtailor", "acme", "Acme")
    monkeypatch.setattr(s, "_get", lambda url=None: "<rss><not closed")
    assert s._rss_enrichment() == {}


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
    pos = ET.fromstring(position_xml)
    assert get_scraper("personio", "acme")._salary_field(pos) == expected


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
    assert j.location == "Berlin, Germany"
    assert j.department == "Electrical Engineering"
    assert j.employment_type == "Employee"
    assert j.url.startswith("https://join.com/companies/indie-solutions/")
    assert j.posted_at
    assert j.description and "</" not in j.description  # populated, HTML-stripped
    assert (
        sum(1 for x in jobs if x.description) == 12
    )  # the bounded detail-fetch filled all 12
    # salaryAmountFrom/salaryAmountTo/salaryFrequency are on the LISTING item itself (real fixture
    # job 16244456: 7,500,000/9,000,000 minor-unit EUR, PER_YEAR) — divided by 100 into major units.
    assert j.salary == "75000-90000 EUR"


@pytest.mark.parametrize("async_fanout_switch", ["1", "0"])
def test_join_fetch_raw_keys_each_description_by_its_posting_id(
    monkeypatch, async_fanout_switch
):
    """Each description arrives keyed by its posting's id, whichever transport carries it; a
    body-less 200 and a listing row with no id are both named losses, and a body-less posting
    still ships as a Job."""
    from headstart.scrapers.join import JoinScraper

    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", async_fanout_switch)
    careers_page = (
        '<script id="__NEXT_DATA__" type="application/json">'
        + json.dumps({"props": {"pageProps": {"initialState": {"company": {"id": 7}}}}})
        + "</script>"
    )
    listed = [
        {"id": 11, "idParam": "11-a", "title": "Engineer"},
        {"id": 12, "idParam": "12-b", "title": "Analyst"},
        {"idParam": "no-id", "title": "No id"},
    ]
    detail_by_id = {
        "11": {"description": "<p>Build.</p>"},
        "12": {"intro": None, "tasks": None},
    }

    def route(method, url, kwargs):
        if url == "https://join.com/companies/acme":
            return FakeResponse(text=careers_page)
        if url.startswith("https://join.com/api/public/companies/7/jobs"):
            page = {"items": listed, "pagination": {"pageCount": 1}}
            return FakeResponse(text=json.dumps(page))
        posting_id = url.removeprefix("https://join.com/api/public/jobs/").split("?")[0]
        return FakeResponse(text=json.dumps(detail_by_id[posting_id]))

    fetcher = FakeFetcher(route)
    scraper = JoinScraper("acme", fetcher=fetcher)
    raw = scraper.fetch_raw()

    assert raw["descriptions"] == {"11": "<p>Build.</p>"}
    assert scraper.detail_losses == {"no description on a 200": 1, "no job id": 1}
    detail_requests = [
        request for request in fetcher.requests if "/api/public/jobs/" in request.url
    ]
    assert sorted(request.url for request in detail_requests) == [
        "https://join.com/api/public/jobs/11?locale=en",
        "https://join.com/api/public/jobs/12?locale=en",
    ]
    assert {request.kwargs["headers"]["Accept"] for request in detail_requests} == {
        "application/json"
    }
    raw_with_ids = {**raw, "items": raw["items"][:2]}  # `parse` needs an id for a Job
    jobs = {job.title: job for job in scraper.parse(raw_with_ids, SCRAPED_AT)}
    assert jobs["Engineer"].description == "Build."
    assert jobs["Analyst"].description is None


def test_join_location_uses_the_city_objects_city_and_country_names():
    raw = deepcopy(_load("join_indie-solutions.json"))
    raw["items"][0]["city"]["countryName"] = "City-country"
    raw["items"][0]["country"]["name"] = "Wrong top-level country"
    job = get_scraper("join", "indie-solutions", "indie").parse(raw, SCRAPED_AT)[0]
    assert job.location == "Berlin, City-country"


def test_join_salary_field_present_and_populated():
    salary_field = get_scraper("join", "acme")._salary_field

    it = {
        "salaryAmountFrom": {"amount": 9000000, "currency": "EUR"},
        "salaryAmountTo": {"amount": 14000000, "currency": "EUR"},
        "salaryFrequency": "PER_YEAR",
    }
    assert salary_field(it) == "90000-140000 EUR"


def test_join_salary_field_hourly_rate():
    salary_field = get_scraper("join", "acme")._salary_field

    it = {
        "salaryAmountFrom": {"amount": 1600, "currency": "EUR"},
        "salaryAmountTo": {"amount": 2200, "currency": "EUR"},
        "salaryFrequency": "PER_HOUR",
    }
    assert salary_field(it) == "16-22 EUR per hour"


def test_join_salary_field_absent_not_zero():
    # Real live shape (24hassistance, 2026-09-15): amounts are absent as keys entirely on a
    # posting where the employer never entered a number — salaryFrequency still defaults to
    # "PER_YEAR" even then, so its presence alone must not be read as a signal.
    salary_field = get_scraper("join", "acme")._salary_field

    it = {"salaryFrequency": "PER_YEAR"}
    assert salary_field(it) is None


def test_join_salary_field_unrecognized_frequency_declines():
    # PER_WEEK/PER_DAY are documented platform values (headstart.scrapers.join's own module
    # docstring) but salary.py's _field_generic has no phrase to annualize them correctly, so
    # the field is declined rather than guessed.
    salary_field = get_scraper("join", "acme")._salary_field

    it = {
        "salaryAmountFrom": {"amount": 50000, "currency": "EUR"},
        "salaryAmountTo": {"amount": 70000, "currency": "EUR"},
        "salaryFrequency": "PER_WEEK",
    }
    assert salary_field(it) is None


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
    _pay_range = get_scraper("rippling", "acme")._salary_field

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
    _pay_range = get_scraper("rippling", "acme")._salary_field

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
    of bug ashby's `_salary_field` docstring documents (a real Ramp job with minValue=0)."""
    _pay_range = get_scraper("rippling", "acme")._salary_field

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
    _pay_range = get_scraper("rippling", "acme")._salary_field

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


def test_rippling_pay_range_at_or_above_a_million_is_not_scientific():
    """`:g` wrote 2,000,000 as "2e+06", which `salary.extract` cannot parse — live on
    heymarvin's INR Product Designer band (2026-09-22)."""
    _pay_range = get_scraper("rippling", "acme")._salary_field
    band = {"rangeStart": 2000000, "rangeEnd": 2800000.5, "currency": "INR"}
    assert _pay_range([{**band, "frequency": "YEAR"}]) == "2000000-2800000.5 INR YEAR"


def test_rippling_ceiling_only_pay_range_is_refused():
    """A lone figure reads as a floor (`salary.extract` has no spelling for a ceiling), the
    same shape recruitee refuses; a floor alone is still kept."""
    _pay_range = get_scraper("rippling", "acme")._salary_field
    unit = {"currency": "USD", "frequency": "YEAR"}
    assert _pay_range([{"rangeStart": None, "rangeEnd": 120000, **unit}]) is None
    assert _pay_range([{"rangeStart": 90000, "rangeEnd": None, **unit}]) == (
        "90000 USD YEAR"
    )


def test_rippling_employment_type_empty_label_does_not_fall_back():
    """`.label` is checked with `is not None`, not truthiness — the same class of bug
    `_salary_field` fixes for rangeStart/rangeEnd. A present-but-empty label (never observed
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


def _rippling_multi_location_rows() -> list[dict]:
    """One posting listed once per work location, as the live `rippling` Board lists
    94486f41 (2026-09-22): same `uuid`, differing only in `workLocation`."""
    u = "94486f41-6474-446a-b67a-c164e11354ea"
    return [
        {"uuid": u, "name": "Account Executive", "workLocation": {"label": label}}
        for label in ("Pittsburgh, PA", "Cleveland, OH", "Pittsburgh, PA")
    ]


def test_rippling_merges_a_postings_location_rows_into_one_job():
    rows = _rippling_multi_location_rows()
    rows[0]["_detail"] = {}  # this row's detail was not fetched; the next row's was
    rows[1]["_detail"] = {"description": {"role": "<p>Sell.</p>"}}
    jobs = get_scraper("rippling", "acme", "Acme").parse(rows, SCRAPED_AT)
    assert [(j.id, j.location) for j in jobs] == [
        (
            "rippling:acme:94486f41-6474-446a-b67a-c164e11354ea",
            "Pittsburgh, PA; Cleveland, OH",
        )
    ]
    assert jobs[0].description == "Sell."


_RIPPLING_ACME_LISTING = "https://api.rippling.com/platform/api/ats/v1/board/acme/jobs"


def _rippling_board(
    listed: list[dict], record_by_uuid: dict[str, dict]
) -> tuple[RipplingScraper, FakeFetcher]:
    """The `acme` Board answering ``listed`` as its listing and each posting's detail GET from
    ``record_by_uuid`` (a 404 for any other uuid)."""

    def route(method: str, url: str, kwargs: dict) -> FakeResponse:
        if url == _RIPPLING_ACME_LISTING:
            return FakeResponse(text=json.dumps(listed))
        uuid = url.removeprefix(f"{_RIPPLING_ACME_LISTING}/")
        if uuid not in record_by_uuid:
            return FakeResponse(404)
        return FakeResponse(text=json.dumps(record_by_uuid[uuid]))

    fetcher = FakeFetcher(route)
    return RipplingScraper("acme", fetcher=fetcher), fetcher


@pytest.mark.parametrize("async_fanout_switch", ["1", "0"])
def test_rippling_fetches_one_detail_per_posting_not_per_location_row(
    monkeypatch, async_fanout_switch
):
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", async_fanout_switch)
    uuid = "94486f41-6474-446a-b67a-c164e11354ea"
    scraper, fetcher = _rippling_board(
        _rippling_multi_location_rows(), {uuid: {"createdOn": "x"}}
    )
    jobs = scraper.parse(scraper.fetch_raw(), SCRAPED_AT)
    assert fetcher.urls() == [
        _RIPPLING_ACME_LISTING,
        f"{_RIPPLING_ACME_LISTING}/{uuid}",
    ]
    assert fetcher.requests[1].kwargs["headers"]["Accept"] == "application/json"
    assert [j.posted_at for j in jobs] == ["x"]


def test_rippling_labels_an_empty_record_and_a_row_with_no_uuid():
    """A 200 carrying an empty record and a listing row with no uuid are both named losses; the
    row with no uuid is never requested."""
    listed = [{"uuid": "a1", "name": "Engineer"}, {"name": "No uuid"}]
    scraper, fetcher = _rippling_board(listed, {"a1": {}})
    raw = scraper.fetch_raw()
    assert fetcher.urls() == [_RIPPLING_ACME_LISTING, f"{_RIPPLING_ACME_LISTING}/a1"]
    assert scraper.detail_losses == {"empty record on a 200": 1, "no job uuid": 1}
    assert scraper.telemetry["detail_attempted"] == 1
    assert [row["_detail"] for row in raw] == [{}, {}]


def test_rippling_gates_each_location_row_before_merging_a_posting():
    """The gate asks every location row, and the posting is fetched if any row passes — so a
    posting whose last row alone would be gated out is still described. The detail hangs on the
    row the merge kept, and `tech_gated_details` counts rows."""
    uuid = "b2"
    listed = [
        {"uuid": uuid, "name": "Backend Engineer", "workLocation": {"label": "Pune"}},
        {"uuid": uuid, "name": "Receptionist", "workLocation": {"label": "Delhi"}},
    ]
    scraper, fetcher = _rippling_board(listed, {uuid: {"createdOn": "x"}})
    scraper.have_details = frozenset()  # arms the gate
    raw = scraper.fetch_raw()
    assert fetcher.urls() == [
        _RIPPLING_ACME_LISTING,
        f"{_RIPPLING_ACME_LISTING}/{uuid}",
    ]
    assert [row["_detail"] for row in raw] == [{"createdOn": "x"}, {}]
    assert scraper.telemetry["tech_gated_details"] == 1


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


def test_darwinbox_iso_date_reads_an_epoch_as_the_posters_local_midnight():
    """A live `posted_on` int is the posting's date at 00:00 in the poster's own zone, so
    an IST posting's UTC reading lands on the previous calendar day."""
    from headstart.scrapers.darwinbox import _iso_date

    assert _iso_date(1789497000) == "2026-09-16"  # 2026-09-15T18:30Z, IST midnight
    assert _iso_date(1789488000) == "2026-09-16"  # 16:00Z, UTC+8 midnight
    assert _iso_date(1789509600) == "2026-09-16"  # 22:00Z, CEST midnight
    assert _iso_date(1789531200) == "2026-09-16"  # 2026-09-16T04:00Z, EDT midnight
    assert _iso_date(1789497000000) == "2026-09-16"  # same, in ms
    # a legacy value that is a real instant (== created_on), not a midnight: its UTC date
    assert _iso_date(1582293124) == "2020-02-21"  # 2020-02-21T13:52:04Z


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


def test_successfactors_sitemal_items_reads_title_description_location():
    """`/sitemal.xml`'s shape, keyed on the same numeric id `/sitemap.xml`'s `/job/.../{id}/`
    path carries (module docstring) — a small fixture matching the real basf.jobs/ace1950
    structure rather than a synthetic one, description CDATA-wrapped and entity-escaped once,
    title carrying a trailing "(location)" this function must strip back off."""
    from headstart.scrapers.successfactors import _sitemal_items

    text = """<rss version="2.0" xmlns:g="http://base.google.com/ns/1.0"><channel>
    <item>
      <title>Senior Engineer (m/w/d) (Ludwigshafen am Rhein, DE)</title>
      <description><![CDATA[&lt;p&gt;Build things.&lt;/p&gt;]]></description>
      <link>https://basf.jobs/dark_blue_EMEA/job/Ludwigshafen-am-Rhein-Senior-Engineer-mwd/987746301/</link>
      <guid isPermaLink="false">987746301</guid>
      <g:id>987746301</g:id>
      <g:employer>BASF SE</g:employer>
      <g:location>Ludwigshafen am Rhein, DE</g:location>
    </item>
    </channel></rss>"""

    fields = _sitemal_items(text)

    assert fields == {
        "987746301": {
            "title": "Senior Engineer (m/w/d)",  # trailing "(location)" stripped
            "description": "Build things.",
            "location": "Ludwigshafen am Rhein, DE",
        }
    }


def test_successfactors_sitemal_items_skips_an_item_with_no_link_or_no_title():
    from headstart.scrapers.successfactors import _sitemal_items

    text = """<rss><channel>
    <item><title>No link</title><description>x</description></item>
    <item><title></title><link>https://basf.jobs/job/x/1/</link></item>
    <item><title>Kept</title><link>https://basf.jobs/job/x/2/</link></item>
    </channel></rss>"""

    assert list(_sitemal_items(text)) == ["2"]


def test_successfactors_sitemal_items_has_no_date_or_employment_type():
    """Measured live 2026-09-22 across three tenants (module docstring): this feed states
    neither field on any item sampled, so a job filled from it must not silently invent one."""
    from headstart.scrapers.successfactors import _sitemal_items

    text = """<rss><channel><item>
    <title>Engineer</title>
    <link>https://basf.jobs/job/x/1/</link>
    </item></channel></rss>"""

    fields = _sitemal_items(text)["1"]
    assert "posted_at" not in fields
    assert "employment_type" not in fields
    assert "remote" not in fields


def test_successfactors_strip_location_suffix():
    from headstart.scrapers.successfactors import _strip_location_suffix

    assert (
        _strip_location_suffix("Engineer (m/w/d) (Berlin, DE)", "Berlin, DE")
        == "Engineer (m/w/d)"
    )
    # no location to strip against: title kept exactly as-is
    assert _strip_location_suffix("Engineer", None) == "Engineer"
    # a parenthetical that ISN'T the location must survive
    assert (
        _strip_location_suffix("Engineer (m/w/d)", "Berlin, DE") == "Engineer (m/w/d)"
    )


def test_successfactors_strip_cdata_unwraps_and_passes_through():
    from headstart.scrapers.successfactors import _strip_cdata

    assert _strip_cdata("<![CDATA[&lt;p&gt;hi&lt;/p&gt;]]>") == "&lt;p&gt;hi&lt;/p&gt;"
    assert _strip_cdata("plain text, no CDATA") == "plain text, no CDATA"


def test_successfactors_fetch_raw_reads_every_page_even_where_sitemal_covers_it(
    monkeypatch,
):
    """The job page is the authority: `/sitemal.xml` states no posting date, and the page does
    (CSB pages too, via `_csb_posted_at` — 8 of 9 tenants measured 2026-09-22). Skipping a page
    the feed covered shipped `posted_at=None`, which `update_meta` then wrote over the stored
    date. And with every page read, the feed is not fetched at all."""
    fetched: list[str] = []

    def dated_page(url):
        fetched.append(url)
        return FakeResponse(
            text=_successfactors_job_page("T", posted="Tue Aug 25 00:00:00 UTC 2026")
        )

    scraper = _successfactors_board(
        monkeypatch,
        sitemap=(
            "urlset",
            (
                "<loc>https://careers.voith.com/job/Engineer/1/</loc>"
                "<loc>https://careers.voith.com/job/Analyst/2/</loc>"
            ),
            None,
        ),
        search=([], None, None),
        rss=([], {}, None),
        job_page=dated_page,
    )
    sitemal_reads: list[int] = []
    monkeypatch.setattr(
        scraper,
        "_sitemal_fields",
        lambda: sitemal_reads.append(1) or {"1": {"title": "Engineer"}},
    )

    raw = scraper.fetch_raw()

    assert sorted(fetched) == [
        "https://careers.voith.com/job/Analyst/2/",
        "https://careers.voith.com/job/Engineer/1/",
    ]
    assert [item["fields"]["posted_at"] for item in raw] == ["2026-08-25"] * 2
    assert sitemal_reads == []


def test_successfactors_fetch_raw_rescues_only_an_unreadable_page_from_sitemal(
    monkeypatch,
):
    """`/sitemal.xml` fills a Job only where its page yielded nothing, and a rescued Job is not
    lost: only the page neither source could read counts against the Board — out of every tech
    id, since every one was fetched (ADR-0121)."""
    scraper = _successfactors_board(
        monkeypatch,
        sitemap=(
            "urlset",
            "".join(
                f"<loc>https://careers.voith.com/job/Engineer/{i}/</loc>"
                for i in (1, 2, 3)
            ),
            None,
        ),
        search=([], None, None),
        rss=([], {}, None),
        sitemal={
            "1": {"title": "Feed title", "description": "feed"},
            "2": {"title": "Engineer", "description": "feed", "location": "Berlin"},
        },
        job_page=lambda url: (
            FakeResponse(text=_successfactors_job_page("Page title"))
            if url.endswith("/1/")
            else FakeResponse(404)
        ),
    )

    raw = scraper.fetch_raw()

    by_id = {item["id"]: item["fields"] for item in raw}
    assert by_id["1"]["title"] == "Page title"  # the page wins where it read
    assert by_id["2"]["description"] == "feed"  # the feed rescued the unreadable page
    assert by_id["3"] is None  # neither source had it
    assert (
        scraper.truncated
        == "1/3 job pages unreadable — those Jobs are listed but unbuilt"
    )


def test_successfactors_fetch_raw_drops_a_page_that_says_the_posting_is_unavailable(
    monkeypatch,
):
    """A listed id whose page is RMK's "You can't view this job" shell is a closed posting, not
    an unreadable page (careers.hcltech.com: 15 of 60 sampled, 2026-09-25). It is not emitted,
    not filled from `/sitemal.xml` (which still lists it), and not counted as a loss — so it can
    never push the Board out of eviction scope. A title-less page *without* the shell stays on
    the loss + sitemal path."""
    scraper = _successfactors_board(
        monkeypatch,
        sitemap=(
            "urlset",
            "".join(
                f"<loc>https://careers.voith.com/job/Engineer/{i}/</loc>"
                for i in (1, 2, 3)
            ),
            None,
        ),
        search=([], None, None),
        rss=([], {}, None),
        sitemal={
            "2": {"title": "Feed title for a closed posting"},
            "3": {"title": "Feed title", "description": "feed"},
        },
        job_page=lambda url: FakeResponse(
            text={
                "/1/": _successfactors_job_page("Page title"),
                "/2/": _successfactors_unavailable_page(),
                "/3/": "<html><body>Please try again later.</body></html>",
            }[url[-3:]]
        ),
    )

    raw = scraper.fetch_raw()

    by_id = {item["id"]: item["fields"] for item in raw}
    assert sorted(by_id) == ["1", "3"]  # the closed posting is gone, not rescued
    assert by_id["3"]["description"] == "feed"  # an unparseable page is still rescued
    assert scraper.truncated is None
    assert scraper.detail_losses == {"200 without a parseable title": 1}


def test_successfactors_unavailable_page_alone_does_not_fetch_sitemal(monkeypatch):
    """Nothing unread, nothing to rescue: the feed is not fetched for a closed posting, and a
    Board whose only failures are closures reads as whole."""
    scraper = _successfactors_board(
        monkeypatch,
        sitemap=(
            "urlset",
            (
                "<loc>https://careers.voith.com/job/Engineer/1/</loc>"
                "<loc>https://careers.voith.com/job/Engineer/2/</loc>"
            ),
            None,
        ),
        search=([], None, None),
        rss=([], {}, None),
        job_page=lambda url: FakeResponse(
            text=_successfactors_unavailable_page()
            if url.endswith("/2/")
            else _successfactors_job_page()
        ),
    )
    sitemal_reads: list[int] = []
    monkeypatch.setattr(
        scraper, "_sitemal_fields", lambda: sitemal_reads.append(1) or {}
    )

    raw = scraper.fetch_raw()

    assert [item["id"] for item in raw] == ["1"]
    assert sitemal_reads == []
    assert scraper.truncated is None
    assert len(scraper.parse(raw, "2026-01-01")) == 1


def test_successfactors_unavailable_marker_on_a_titled_page_keeps_the_job(
    monkeypatch,
):
    """The shell is recognised only on a page that yields no title: every one measured does, and
    a page that still states a posting is kept rather than dropped on a class name alone."""
    page = _successfactors_job_page("Engineer").replace(
        "</body>", '<p class="jobErrMsg">Applications are paused.</p></body>'
    )
    scraper = _successfactors_board(
        monkeypatch,
        sitemap=(
            "urlset",
            "<loc>https://careers.voith.com/job/Engineer/1/</loc>",
            None,
        ),
        search=([], None, None),
        rss=([], {}, None),
        job_page=lambda url: FakeResponse(text=page),
    )

    raw = scraper.fetch_raw()

    assert [item["fields"]["title"] for item in raw] == ["Engineer"]


def test_successfactors_fetch_raw_falls_back_whole_when_sitemal_is_unavailable(
    monkeypatch,
):
    """Most tenants don't have `/sitemal.xml` at all (module docstring: jobs.thyssenkrupp.com
    404s) — `_sitemal_fields` returning `{}` must leave every id on the existing detail path,
    unchanged from before this surface existed."""
    scraper = _successfactors_board(
        monkeypatch,
        sitemap=(
            "urlset",
            "<loc>https://careers.voith.com/job/Engineer/1/</loc>",
            None,
        ),
        search=([], None, None),
        rss=([], {}, None),
        sitemal={},
    )

    raw = scraper.fetch_raw()

    assert [item["id"] for item in raw] == ["1"]
    assert raw[0]["fields"]["title"] == "Engineer"  # from the job page


def test_successfactors_job_functions_from_reads_the_rss_feed_department():
    from headstart.scrapers.successfactors import _job_functions_from

    text = """<rss xmlns:g="http://base.google.com/ns/1.0"><channel>
    <item><g:id>1</g:id><g:job_function>Sales &amp; Marketing</g:job_function>
      <link>https://jobs.sap.com/job/x/1/</link></item>
    <item><g:id>2</g:id><g:job_function></g:job_function></item>
    </channel></rss>"""
    assert _job_functions_from(text) == {"1": "Sales & Marketing"}


def test_successfactors_job_functions_from_rejects_ats_internal_tokens():
    # Real basf.jobs junk (measured live 2026-09-22): a subset of tenants state an ATS
    # configuration token here instead of a department. Feeding that to the tech gate would
    # classify on the literal string "ATS_WCMS_WEBFORM" — worse than no department at all.
    from headstart.scrapers.successfactors import _job_functions_from

    text = """<rss><channel>
    <item><g:id>1</g:id><g:job_function>ATS_WCMS_WEBFORM</g:job_function></item>
    <item><g:id>2</g:id><g:job_function>ATS_TALEO_APAC</g:job_function></item>
    <item><g:id>3</g:id><g:job_function>Engineering</g:job_function></item>
    </channel></rss>"""
    assert _job_functions_from(text) == {"3": "Engineering"}


def test_successfactors_rss_stream_fills_department_end_to_end(monkeypatch):
    # The bug this pins: `department` was hardcoded `None` in `parse()` regardless of what the
    # listing surface knew, so the ADR-0017 tech gate ran title-only on this whole ATS. This
    # exercises the real path — `fetch_raw`'s rss-stream branch reading `g:job_function` off the
    # same feed it's already downloading for URLs, through to `parse()`'s served `Job`.
    from headstart.scrapers import successfactors as sf

    rss_text = """<rss xmlns:g="http://base.google.com/ns/1.0"><channel>
    <item><g:id>1</g:id><g:job_function>Engineering</g:job_function>
      <link>https://careers.voith.com/job/Engineer/1/</link></item>
    </channel></rss>"""
    scraper = _successfactors_board(
        monkeypatch,
        sitemap=("rss", "", None),
        search=([], None, None),
        rss=(
            [("https://careers.voith.com/job/Engineer/1/", "1")],
            sf._job_functions_from(rss_text),
            None,
        ),
        job_page=lambda url: FakeResponse(
            text=_successfactors_job_page("Software Engineer")
        ),
    )

    raw = scraper.fetch_raw()
    jobs = scraper.parse(raw, "2026-09-22T00:00:00Z")

    assert len(jobs) == 1
    assert jobs[0].department == "Engineering"


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


def test_eightfold_api_records_wires_the_remote_and_location_fixes():
    """Integration: the fixes reach `_api_records`'s built fields, not just the pure helpers."""
    scraper, _fetcher = _eightfold_board()
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


def _eightfold_board(*, have_details=None, job_pages=None):
    """An Eightfold scraper behind a fake fetcher: every ``position_details`` answers
    ``desc-{position_id}``, a job page in ``job_pages`` (url -> html) answers as given and any
    other URL is a 404. Returns the scraper and the fake, which records every request."""
    from headstart.scrapers.eightfold import EightfoldScraper

    job_pages = job_pages or {}

    def route(method, url, kwargs):
        position_id = _eightfold_position_id_asked_for(url)
        if position_id is not None:
            description = f"desc-{position_id}"
            return FakeResponse(
                text=json.dumps({"data": {"jobDescription": description}})
            )
        page = job_pages.get(url)
        return FakeResponse(404) if page is None else FakeResponse(text=page)

    fetcher = FakeFetcher(route)
    scraper = EightfoldScraper("acme.eightfold.ai", "Acme", fetcher=fetcher)
    scraper.have_details = have_details
    return scraper, fetcher


def _eightfold_position_id_asked_for(url):
    """The ``position_id`` a ``position_details`` URL asks for; None for any other URL."""
    import urllib.parse

    if "/api/pcsx/position_details?" not in url:
        return None
    return urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["position_id"][0]


def _eightfold_position_ids_requested(fetcher):
    return sorted(
        position_id
        for url in fetcher.urls()
        if (position_id := _eightfold_position_id_asked_for(url)) is not None
    )


@pytest.mark.parametrize("async_fanout", ["1", "0"])
def test_eightfold_skips_details_it_already_holds(monkeypatch, async_fanout):
    """ADR-0048: a Job already covered gets no detail fetch, and the rest stay aligned.

    Alignment is the trap — the fan-out now covers a *subset* of the positions, so pairing its
    results back by index instead of by id would hang each description on the wrong Job.
    """
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", async_fanout)
    scraper, fetcher = _eightfold_board(have_details={"eightfold:acme.eightfold.ai:1"})
    positions = [
        {"id": "1", "name": "Held Engineer"},
        {"id": "2", "name": "Fresh Engineer"},
    ]

    records = scraper._api_records("acme.com", positions)

    # the held Job was never fetched
    assert _eightfold_position_ids_requested(fetcher) == ["2"]
    by_id = {r["id"]: r["fields"]["description"] for r in records}
    assert by_id["2"] == "desc-2"  # the fetched description landed on the right Job
    assert (
        by_id["1"] is None
    )  # and the held one carries no description, not someone else's


def test_eightfold_counts_the_held_details_against_what_the_tech_gate_let_through(
    caplog,
):
    """The held skip keeps its own line, and a posting the gate dropped is not "already held"."""
    caplog.set_level(logging.INFO, logger="headstart.scrapers.eightfold")
    scraper, _fetcher = _eightfold_board(have_details={"eightfold:acme.eightfold.ai:1"})

    scraper._api_records(
        "acme.com",
        [
            {"id": "1", "name": "Backend Engineer"},
            {"id": "2", "name": "Data Engineer"},
            {"id": "3", "name": "Warehouse Associate", "department": "Logistics"},
        ],
    )

    assert "fetched 1/2 descriptions (1 already held)" in caplog.text


def test_eightfold_skips_details_for_postings_the_tech_filter_will_drop():
    """A posting the tech gate discards never gets a detail fetch — no store entry can save it.

    The ADR-0048 skip-list is built from `data/jobs/tech`, so a non-tech posting is never in it
    and was re-fetched every run, forever: ~34,700 of eightfold's ~34,900 detail fetches per run
    (five runs of 2026-09-16), spent on text no reader downstream ever opens.

    `tech_filter.classify` reads `title` + `department`, and the PCSX listing carries both
    (verified live: 142/142 and 157/157 positions on twilio/vialto), so the detail body is never
    needed to make the call.

    An empty `have_details` — not the `None` default — because the gate rides ADR-0048's
    pipeline signal; `test_every_detail_is_fetched_outside_the_pipeline` pins the other arm.
    """
    scraper, fetcher = _eightfold_board(have_details=set())
    positions = [
        {"id": "1", "name": "Backend Engineer", "department": "Engineering"},
        {"id": "2", "name": "Warehouse Associate", "department": "Logistics"},
        # Rule 4: a vague title under a technical department is tech, so its detail is still
        # fetched — a gate reading the title alone would wrongly drop this one.
        {"id": "3", "name": "Analyst", "department": "Data Platform"},
    ]

    records = scraper._api_records("acme.com", positions)

    assert _eightfold_position_ids_requested(fetcher) == ["1", "3"]
    by_id = {r["id"]: r["fields"]["description"] for r in records}
    assert by_id["1"] == "desc-1"
    assert by_id["3"] == "desc-3"
    # The non-tech posting is still emitted — the scrape writes the full set to
    # `data/jobs/{ats}.jsonl` and `filter_tech` is what drops it, not this.
    assert by_id["2"] is None


def test_every_detail_is_fetched_outside_the_pipeline():
    """No skip-list means fetch everything — including the postings the tech gate would drop.

    ADR-0048's documented default, and the tech gate honours it rather than overriding it. Eight
    scripts build scrapers directly, and three read the hole as a defect — `verify_scraper.py`
    reports "jobs-with-description" as its health metric, `audit_remote.py` triangulates `remote`
    against the description text, `salary_sample.py` measures `salary.extract` recall off it — so
    skipping unconditionally would hand all three `description=None` on ~59% of eightfold's
    postings and have them report a quality collapse that is not real. Production loses nothing:
    every sharded run ships a list.
    """
    scraper, fetcher = _eightfold_board()
    assert scraper.have_details is None
    positions = [
        {"id": "1", "name": "Backend Engineer", "department": "Engineering"},
        {"id": "2", "name": "Warehouse Associate", "department": "Logistics"},
    ]

    records = scraper._api_records("acme.com", positions)

    assert _eightfold_position_ids_requested(fetcher) == ["1", "2"]
    assert {r["id"]: r["fields"]["description"] for r in records} == {
        "1": "desc-1",
        "2": "desc-2",
    }


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


def test_a_negligible_shortfall_leaves_the_list_authoritative():
    """ADR-0121, with `successfactors:careers.te.com`'s real shape: one unreadable page in 2,130.

    ADR-0053 on its own excluded that whole Board from the eviction scope — 95 rows on run
    34327339789 — and it did so on every run, with no drain, to protect a single id. At 99.953%
    read the list *is* this Board's set of openings; the one missing id is ADR-0083's job, held
    back for a scrape and evicted only on a second consecutive absence.
    """
    scraper = get_scraper("greenhouse", "acme")
    scraper.mark_truncated_unless_negligible(
        2129, 2130, "1/2130 job pages unreadable — those Jobs are listed but unbuilt"
    )
    assert scraper.truncated is None


def test_a_shortfall_too_large_to_absorb_marks_the_board_unauthoritative():
    """The other side, with `successfactors:careers.hcltech.com`'s real shape from run
    34332773221: 1,022 unreadable of 11,302, 90.957% read.

    The tolerance deliberately does not reach it. One Job in eleven missing is not a list that
    can be read as the Board's openings, and releasing it would hand 1,022 ids to eviction. That
    Board is a detail-fetch defect, not a gate-calibration one. The reason string passes through
    verbatim, so `unauthoritative_boards.json` reads as it always did.
    """
    scraper = get_scraper("greenhouse", "acme")
    scraper.mark_truncated_unless_negligible(
        10280,
        11302,
        "1022/11302 job pages unreadable — those Jobs are listed but unbuilt",
    )
    assert (
        scraper.truncated
        == "1022/11302 job pages unreadable — those Jobs are listed but unbuilt"
    )


def test_a_shortfall_with_no_total_fails_closed():
    """`expected` of 0 is *no total*, which is the shape the tolerance explicitly excludes.

    It must not divide by it, and it must not tolerate it: a shortfall nobody can measure is
    Unauthoritative, the same direction ADR-0053 chose for an unresolvable key. This method is
    public and its docstring invites new callers, so the contract is pinned rather than left to
    the fact that today's six call sites all happen to guard it.
    """
    scraper = get_scraper("greenhouse", "acme")
    scraper.mark_truncated_unless_negligible(0, 0, "the feed gave no total")
    assert scraper.truncated == "the feed gave no total"


def test_a_tolerated_shortfall_stays_quiet_once_a_hard_cap_has_spoken(caplog):
    """`mark_truncated` keeps the first reason, so a Board a cap already condemned is
    Unauthoritative whatever a later measured shortfall decides — and must not then log that its
    list "stays authoritative". Reachable: Oracle's page cap precedes its shortfall branch, and
    SuccessFactors' listing cut-short precedes its detail pass.

    The assertion is on the *log*, deliberately. Checking only that `truncated` kept the cap's
    reason passes without the guard this test exists for — `mark_truncated` has kept the first
    reason since ADR-0053 — so it would have been a test of nothing.
    """
    scraper = get_scraper("greenhouse", "acme")
    scraper.mark_truncated("hit the 100-page cap — the rest unread")
    with caplog.at_level(logging.INFO):
        scraper.mark_truncated_unless_negligible(
            999, 1000, "1/1000 job pages unreadable"
        )

    assert scraper.truncated == "hit the 100-page cap — the rest unread", (
        "the cap's reason must survive"
    )
    assert "stays authoritative" not in caplog.text, (
        "a Board a cap already condemned must never be logged as authoritative"
    )


def test_the_tolerance_draws_the_line_at_the_stated_share():
    """Both sides of the one threshold, so moving `MIN_AUTHORITATIVE_SHARE` cannot pass silently.

    The constant is asserted too: it is the whole policy, it is deliberately not per-scraper, and
    the measurement behind 0.99 (loosening to 0.95 buys 55 rows of 3,055; tightening to 0.995
    costs 128) lives in its docstring rather than in anyone's memory.
    """
    from headstart.scrapers.base import MIN_AUTHORITATIVE_SHARE

    assert MIN_AUTHORITATIVE_SHARE == 0.99

    at_the_line = get_scraper("greenhouse", "acme")
    at_the_line.mark_truncated_unless_negligible(990, 1000, "990 of 1000")
    assert at_the_line.truncated is None, "exactly at the share is still authoritative"

    under = get_scraper("greenhouse", "acme")
    under.mark_truncated_unless_negligible(989, 1000, "989 of 1000")
    assert under.truncated == "989 of 1000"


def test_successfactors_tolerates_one_unreadable_page_but_still_drops_its_job(
    monkeypatch,
):
    """The shape that put 2,130-page Boards permanently out of eviction scope (ADR-0121).

    Both halves matter. The Board stays authoritative, so its *other* closed postings can drain
    — and the unreadable page's Job is still absent from the returned list, which is exactly what
    hands that one id to ADR-0083 rather than to nothing at all.
    """
    scraper = _successfactors_board(
        monkeypatch,
        slug="jobs.example.com",
        sitemap=("urlset", "", None),
        search=(
            [
                (f"https://jobs.example.com/job/Engineer/{i}/", str(i))
                for i in range(200)
            ],
            None,
            None,
        ),
        rss=([], {}, None),
        job_page=lambda url: (
            FakeResponse(404)
            if url.endswith("/7/")
            else FakeResponse(text=_successfactors_job_page())
        ),
    )

    raw = scraper.fetch_raw()

    assert scraper.truncated is None, (
        "199 of 200 read is still this Board's set of openings"
    )
    assert len(scraper.parse(raw, "2026-01-01")) == 199, (
        "the unreadable page's Job is still dropped — that absence is ADR-0083's input"
    )


def test_successfactors_truncates_on_a_surface_that_states_no_total(monkeypatch):
    """The class no share can rescue: a shortfall with nothing to measure it against.

    An RSS stream the tenant aborts mid-flight says how far it got and never how far it had to
    go, so a read ratio would be fabricated. Those surfaces call `mark_truncated` directly and
    stay unconditional — here every one of the 200 details reads perfectly and the Board is
    still Unauthoritative, because the *listing* was never complete.
    """
    aborted = (
        "the tenant's RSS feed aborted 31,457,280 bytes in — "
        "postings past that point were not listed"
    )
    scraper = _successfactors_board(
        monkeypatch,
        slug="jobs.example.com",
        sitemap=("rss", "", None),
        search=([], None, None),
        rss=(
            [(f"https://jobs.example.com/job/x/{i}/", str(i)) for i in range(200)],
            {},
            aborted,
        ),
    )

    scraper.fetch_raw()

    assert scraper.truncated == aborted


def test_eightfold_tolerates_a_replica_short_by_one_posting():
    """`eightfold:appliedmaterials.eightfold.ai` got 1,931 of 1,932 (99.948%) and left the
    eviction scope for it — run `34321068300`'s merge log, which is where that figure lives.
    It is *not* in `unauthoritative_boards.json`, because that file is a snapshot of the latest
    run only and the latest run did not find this Board short; ADR-0121's Context note has the
    distinction. A replica that never deals a posting is the transient miss ADR-0083 exists for."""
    from headstart.scrapers.eightfold import EightfoldScraper

    class _Resp:
        def __init__(self, ids):
            self.status_code = 200
            self._body = {"data": {"positions": [{"id": n} for n in ids], "count": 200}}

        def json(self):
            return self._body

    # 20 pages of 10, except the last, which is one posting short of the stated 200. Every sweep
    # deals the same 199, so the crawl gives up on sweep 2 — 99.5% read.
    sweep = [range(i * 10, i * 10 + 10) for i in range(19)] + [range(190, 199)]
    pages = [_Resp(ids) for _ in range(2) for ids in sweep]
    scraper = EightfoldScraper("appliedmaterials.eightfold.ai")
    scraper._get = lambda *a, **k: pages.pop(0)

    got = scraper._api_search("appliedmaterials")

    assert len({str(p["id"]) for p in got}) == 199
    assert scraper.truncated is None


def test_eightfold_pcsx_disabled_detects_the_message_in_any_locale():
    """The discriminator between "try SmartApply" and "fall straight to the sitemap" is the 403
    body's `message` naming PCSX — not the English phrase, since the Spanish-locale variant
    ("PCSX no está habilitado para este usuario.") was measured live on 2 of 23 tenants
    (coca-colafemsa, oxxo) and would be missed by an English-only match."""
    from headstart.scrapers.eightfold import _pcsx_disabled

    class _Resp:
        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    assert _pcsx_disabled(_Resp({"message": "PCSX is not enabled for this user."}))
    assert _pcsx_disabled(
        _Resp({"message": "PCSX no está habilitado para este usuario."})
    )
    assert not _pcsx_disabled(_Resp({"message": "Forbidden"}))
    assert not _pcsx_disabled(_Resp({}))

    class _Unparseable:
        def json(self):
            raise ValueError("not json")

    assert not _pcsx_disabled(_Unparseable())


def test_eightfold_generic_403_does_not_try_smartapply():
    """A 403 whose body doesn't name PCSX (a generic WAF wall) must fall straight through to the
    sitemap, exactly as before — SmartApply is only tried for the specific "PCSX is not enabled"
    signal, not every 403."""
    from headstart.scrapers.eightfold import EightfoldScraper

    class _Resp:
        status_code = 403

        def json(self):
            return {"message": "Forbidden"}

    scraper = EightfoldScraper("acme.eightfold.ai")
    # A single-response queue: a second `_get` call (i.e. SmartApply being tried) raises IndexError
    # and fails the test.
    pages = [_Resp()]
    scraper._get = lambda *a, **k: pages.pop(0)

    got = scraper._api_search("acme")

    assert got is None
    assert scraper._fallback_reason == "the PCSX API returned 403"


def test_eightfold_switches_to_smartapply_on_a_pcsx_disabled_403():
    """The core new behavior: a "PCSX is not enabled" 403 tries `/api/apply/v2/jobs` instead of
    giving up immediately, recovering the Board instead of losing it to the weaker sitemap
    fallback. Verified live on 23/23 such tenants 2026-09-11 (docs/eightfold/
    smartapply-fallback.md); this pins the shape with a fixture."""
    from headstart.scrapers.eightfold import EightfoldScraper

    class _PcsxDisabled:
        status_code = 403

        def json(self):
            return {"message": "PCSX is not enabled for this user."}

    class _SmartApplyResp:
        def __init__(self, positions, count):
            self.status_code = 200
            self._body = {"positions": positions, "count": count}

        def json(self):
            return self._body

    positions = [
        {
            "id": 1,
            "name": "Backend Engineer",
            "locations": ["Austin, TX, USA"],
            "department": "Engineering",
            "work_location_option": "hybrid",
            "t_create": 1700000000,
            "t_update": 1700100000,
            "job_description": "",  # always empty on this surface — detail fetch supplies it
        }
    ]
    pages = [_PcsxDisabled(), _SmartApplyResp(positions, count=1)]
    scraper = EightfoldScraper("acme.eightfold.ai")
    scraper._get = lambda *a, **k: pages.pop(0)

    got = scraper._api_search("acme")

    assert got == [
        {
            "id": 1,
            "name": "Backend Engineer",
            "locations": ["Austin, TX, USA"],
            "department": "Engineering",
            "postedTs": 1700000000,
            "workLocationOption": "hybrid",
            "atsJobId": None,
            "displayJobId": None,
        }
    ]
    assert scraper.truncated is None


def test_eightfold_smartapply_failure_falls_through_to_sitemap():
    """SmartApply itself can fail (non-200, or an unparseable body) — that must still fall through
    to the sitemap, the same as a PCSX API failure always has."""
    from headstart.scrapers.eightfold import EightfoldScraper

    class _PcsxDisabled:
        status_code = 403

        def json(self):
            return {"message": "PCSX is not enabled for this user."}

    class _SmartApplyDown:
        status_code = 500

    pages = [_PcsxDisabled(), _SmartApplyDown()]
    scraper = EightfoldScraper("acme.eightfold.ai")
    scraper._get = lambda *a, **k: pages.pop(0)

    got = scraper._api_search("acme")

    assert got is None
    assert scraper._fallback_reason == "the SmartApply API returned 500"


def test_eightfold_smartapply_paginates_and_dedupes():
    """SmartApply pages the same way as the primary search (10/page, `start` increments), and —
    though no replica disagreement was measured live — the dedupe stays as a cheap safety net."""
    from headstart.scrapers.eightfold import EightfoldScraper

    class _Resp:
        def __init__(self, ids, count):
            self.status_code = 200
            self._body = {"positions": [{"id": n} for n in ids], "count": count}

        def json(self):
            return self._body

    pages = [
        _Resp(range(10), count=15),
        _Resp(range(10, 15), count=15),
    ]
    scraper = EightfoldScraper("acme.eightfold.ai")
    scraper._get = lambda *a, **k: pages.pop(0)

    got = scraper._smartapply_search("acme")

    assert sorted(int(p["id"]) for p in got) == list(range(15))
    assert scraper.truncated is None


def test_eightfold_smartapply_to_pcsx_shape_maps_fields():
    """Field-shape mapping verified live 2026-09-11 (docs/eightfold/smartapply-fallback.md):
    same-named keys pass through, `work_location_option`/`t_create` are renamed onto the primary
    search's `workLocationOption`/`postedTs`, and a list-shaped `department` (measured on
    fluor.eightfold.ai — every other sampled tenant returns a plain string) is joined instead of
    crashing `_api_records`'s `.strip()`."""
    from headstart.scrapers.eightfold import _smartapply_to_pcsx_shape

    plain = _smartapply_to_pcsx_shape(
        {
            "id": 42,
            "name": "Data Engineer",
            "locations": ["Remote"],
            "department": "Data & Analytics",
            "work_location_option": "remote_global",
            "t_create": 1700000000,
            "t_update": 1700999999,
            "canonicalPositionUrl": "https://elsewhere.example.com/careers/job/42",
        }
    )
    assert plain == {
        "id": 42,
        "name": "Data Engineer",
        "locations": ["Remote"],
        "department": "Data & Analytics",
        "postedTs": 1700000000,
        "workLocationOption": "remote_global",
        "atsJobId": None,
        "displayJobId": None,
    }
    assert "positionUrl" not in plain, (
        "canonicalPositionUrl is deliberately not carried through — it can point at a different "
        "vanity host than self.slug, while the existing /careers/job/{id} fallback was confirmed "
        "live to resolve on every tenant checked"
    )

    list_department = _smartapply_to_pcsx_shape(
        {"id": 7, "name": "SQS Coordinator", "department": ["Quality"]}
    )
    assert list_department["department"] == "Quality"


def test_eightfold_child_sitemap_cap_truncates_even_when_every_detail_reads(
    monkeypatch,
):
    """The one route by which a hard cap could reach the tolerance (ADR-0121).

    `_MAX_INDEX_CHILDREN` bounds how much of a sitemap index is followed, and what it drops never
    reaches `listed` — the denominator the detail pass measures against. So a Board whose index is
    twice the cap can lose half its postings and still report every listed detail as read. The cap
    must therefore say so itself: here all 50 followed children parse and every detail is fine,
    and the Board is still Unauthoritative.
    """
    from headstart.scrapers import eightfold as ef

    # The child URLs must carry "sitemap" and avoid "index" — that is what `_CHILD_SITEMAP`
    # matches and what `_job_urls` filters on.
    index = "".join(
        f"<sitemap><loc>https://acme.eightfold.ai/sitemap-c{i}.xml</loc></sitemap>"
        for i in range(ef._MAX_INDEX_CHILDREN * 2)
    )

    class _Resp:
        def __init__(self, text):
            self.status_code = 200
            self.text = text

        def raise_for_status(self):
            return None

    def fake_get(self, url=None, accept=None):
        if url and "sitemap-c" in url:
            n = url.rsplit("sitemap-c", 1)[1].split(".")[0]
            return _Resp(
                f"<urlset><url><loc>https://acme.eightfold.ai/careers/job/{n}</loc></url></urlset>"
            )
        return _Resp(f"<sitemapindex>{index}</sitemapindex>")

    scraper = ef.EightfoldScraper("acme.eightfold.ai")
    monkeypatch.setattr(type(scraper), "_get", fake_get)

    urls = scraper._job_urls()

    assert len(urls) == ef._MAX_INDEX_CHILDREN, "only the followed children are listed"
    assert scraper.truncated and "child sitemaps" in scraper.truncated, (
        "the cap must mark the Board itself — nothing downstream can see what it dropped"
    )


def test_oracle_remote_maps_known_codes():
    from headstart.scrapers.oracle import _remote

    assert _remote({"WorkplaceTypeCode": "ORA_REMOTE"}, {}, "Austin, TX") is True
    assert _remote({"WorkplaceTypeCode": "ORA_ON_SITE"}, {}, "Remote") is False


def test_oracle_remote_maps_hybrid_and_unknown_codes_to_none_not_false():
    # The bug this pins: `code == _REMOTE_CODE` used to read ORA_HYBRID (and any other stated
    # code) as False, conflating "explicitly hybrid" with "explicitly on-site" — against this
    # repo's own hybrid-to-None convention (phenom.py, taleo_be.py). A stated-but-unrecognized
    # code must not fall back to a location guess either, since the tenant DID state something.
    from headstart.scrapers.oracle import _remote

    assert _remote({"WorkplaceTypeCode": "ORA_HYBRID"}, {}, "Remote") is None
    assert (
        _remote({"WorkplaceTypeCode": "ORA_FULL_TIME_REMOTE"}, {}, "Austin, TX") is None
    )


def test_oracle_remote_falls_back_to_location_only_when_no_code_at_all():
    from headstart.scrapers.oracle import _remote

    assert _remote({}, {}, "Remote - United States") is True
    assert _remote({}, {}, "Austin, TX") is False


def test_oracle_remote_reads_detail_when_listing_omits_the_code():
    from headstart.scrapers.oracle import _remote

    assert _remote({}, {"WorkplaceTypeCode": "ORA_REMOTE"}, "Austin, TX") is True


def test_oracle_offset_ceiling_truncates_however_complete_the_read_looks(monkeypatch):
    """A hard cap is never tolerated, whatever share it leaves (ADR-0121).

    The API serves no offset past 10,000, so a Board stating 10,050 reads exactly 10,000 —
    99.5%, inside the tolerance — and the 50 it cannot reach are unreachable on *every* run, not
    a transient miss. `oracle:ejwl.fa.us2.oraclecloud.com` is the live case, 187 shielded rows on
    run 34327339789. Releasing this class would evict those rows for good.
    """
    full = _oracle_page(_oracle_reqs(0, 200), 10_050)
    blank = _oracle_page([], 10_050)
    s = get_scraper("oracle", "ejwl.fa.us2.oraclecloud.com", "Acme")
    calls = {"n": 0}

    def _get(self, url=None):
        calls["n"] += 1
        # 50 full pages take the walk to exactly the ceiling; past it the envelope goes blank
        # rather than erroring, which is what ends the walk short of the Board's own total.
        return full if calls["n"] <= 50 else blank

    monkeypatch.setattr(type(s), "_get", _get)

    reqs = s._listing()

    assert len(reqs) == 10_000
    assert s.truncated and "no offset past" in s.truncated


def _successfactors_job_page(title="Engineer", posted=None):
    """A CSB-rendered job page: the microdata title `_csb_title` reads and, when given, the
    ``datePosted`` microdata `_csb_posted_at` reads, in the Java ``Date.toString`` form the pages
    write it in."""
    date = f'<meta itemprop="datePosted" content="{posted}">' if posted else ""
    return f'<html><body><span itemprop="title">{title}</span>{date}</body></html>'


def _successfactors_unavailable_page():
    """The shell RMK serves, with a 200, for a listed id it will no longer show — trimmed from
    careers.hcltech.com 2026-09-25 (the same markup on careers.wipro.com,
    lockheed.jobs.hr.cloud.sap and jobs.danfoss.com). Its ``<title>`` is the template's with an
    empty job title, so no title parses."""
    return (
        "<html><head><title> Job Details | HCLTech</title>"
        '<meta content="" property="og:title" /></head><body>'
        '<div class="jobDisplay"><div class="content"><div class="job">'
        "<p class=\"jobErrMsg\"><strong>You can't view this job because it's not "
        "available at this time.</strong></p></div></div></div></body></html>"
    )


def _successfactors_board(
    monkeypatch,
    *,
    search,
    rss,
    sitemap=("rss", "", None),
    sitemal=None,
    job_page=None,
    slug="careers.voith.com",
):
    """A SuccessFactors scraper whose three listing surfaces are stubbed. Each returns what the
    real one does — its list plus why-it-came-up-short: ``sitemap`` as ``(kind, text, cut_short)``
    (defaulting to an RSS classification, so the whole fallback chain runs), ``search`` as
    ``(pairs, cut_short, total)`` from the ``/search/`` walk, ``rss`` as ``(pairs, job_functions,
    cut_short)`` from the patient stream. ``sitemal`` is the ``/sitemal.xml`` field cache
    (``{job_id: fields}``, default ``{}``) — stubbed too, so these tests exercise the
    pre-existing surface fallback without a real request to that fourth surface.

    Job pages are real requests, through a fake fetcher: ``job_page(url)`` answers each one
    (default: a page titled "Engineer")."""
    from headstart.scrapers.successfactors import SuccessFactorsScraper

    def answer_every_page_with_a_title(url):
        return FakeResponse(text=_successfactors_job_page())

    answer = job_page or answer_every_page_with_a_title
    scraper = SuccessFactorsScraper(
        slug, fetcher=FakeFetcher(lambda method, url, kwargs: answer(url))
    )
    monkeypatch.setattr(scraper, "_fetch_sitemap", lambda: sitemap)
    monkeypatch.setattr(scraper, "_search_job_urls", lambda: search)
    monkeypatch.setattr(scraper, "_rss_job_urls", lambda: rss)
    monkeypatch.setattr(scraper, "_sitemal_fields", lambda: sitemal or {})
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

    found, job_functions, cut_short = scraper._rss_job_urls()

    assert [job_id for _url, job_id in found] == ["7"]  # what arrived is still scraped
    assert job_functions == {}  # no `g:job_function` anywhere in this feed
    assert cut_short and "aborted" in cut_short
    assert scraper.truncated is None  # reported to fetch_raw, not recorded here

    # ...a feed still streaming at `_RSS_CAP` is the same kind of short list.
    monkeypatch.setattr(sf, "_RSS_CAP", 2 * 1024 * 1024)
    _stub_stream(
        monkeypatch,
        sf,
        _StreamedBody(
            [b"<loc>https://jobs.example.com/job/x/7/</loc>" + b" " * (2 * 1024 * 1024)]
        ),
    )
    assert "2 MB read cap" in scraper._rss_job_urls()[2]

    # ...while a feed that streams to its end reports nothing.
    _stub_stream(
        monkeypatch,
        sf,
        _StreamedBody([b"<loc>https://jobs.example.com/job/x/7/</loc>"]),
    )
    assert scraper._rss_job_urls()[2] is None


def test_successfactors_rss_stream_reads_a_feed_past_the_urlset_cap(monkeypatch):
    """jobs.crh.com's whole feed is 32.8 MB — a full description per item — and its `/search/`
    lists nothing, so this stream is its only surface. The urlset guard's 30 MB cut it 86 postings
    short of 1,905 and left the Board Unauthoritative every run."""
    from headstart.scrapers import successfactors as sf

    scraper = sf.SuccessFactorsScraper("jobs.example.com")
    _stub_stream(
        monkeypatch,
        sf,
        _StreamedBody(
            [b" " * (33 * 1024 * 1024), b"<loc>https://jobs.example.com/job/x/7/</loc>"]
        ),
    )

    found, _job_functions, cut_short = scraper._rss_job_urls()

    assert [job_id for _url, job_id in found] == ["7"]
    assert cut_short is None


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

    found, why, _total = scraper._search_job_urls()

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

    found, why, _total = scraper._search_job_urls()

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

    found, why, _total = scraper._search_job_urls()

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

    found, why, _total = sf.SuccessFactorsScraper("jobs.example.com")._search_job_urls()

    assert len(found) == 10
    assert why and "10 of the 40" in why


@pytest.mark.parametrize(("served", "truncated"), [(199, False), (197, True)])
def test_successfactors_search_shortfall_takes_the_adr_0121_tolerance(
    monkeypatch, served, truncated
):
    """A walk measured against the Board's own stated total is exactly the shortfall ADR-0121
    tolerates: jobs.xpo.com read 524 of 526 (99.6%) and still left the eviction scope. Below
    the tolerance it is Unauthoritative as before."""
    from headstart.scrapers import successfactors as sf

    def serve_search_and_job_pages(method, url, kwargs):
        if "startrow=" not in url:
            return FakeResponse(text=_successfactors_job_page())
        startrow = int(url.rsplit("startrow=", 1)[1])
        return FakeResponse(
            text=_labelled_search_page(
                range(startrow, min(startrow + 10, served)), 200
            ),
        )

    scraper = sf.SuccessFactorsScraper(
        "jobs.example.com", fetcher=FakeFetcher(serve_search_and_job_pages)
    )
    monkeypatch.setattr(scraper, "_fetch_sitemap", lambda: ("rss", "", None))

    raw = scraper.fetch_raw()

    assert len(raw) == served
    assert (scraper.truncated is not None) is truncated
    if truncated:
        assert f"read {served} of the 200 postings" in scraper.truncated


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

    found, why, _total = sf.SuccessFactorsScraper("jobs.example.com")._search_job_urls()

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

    found, why, _total = sf.SuccessFactorsScraper("jobs.example.com")._search_job_urls()
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
        search=(
            [],
            "HTTP 503 at startrow 0 — 0 postings read before the walk stopped",
            None,
        ),
        rss=([("https://careers.voith.com/job/Engineer/1/", "1")], {}, None),
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
            None,
        ),
        rss=([("https://careers.voith.com/job/x/1/", "1")], {}, None),
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
        search=([], None, None),
        rss=(
            [("https://careers.voith.com/job/Engineer/1/", "1")],
            {},
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
        search=([], None, None),
        rss=([], {}, None),
        sitemap=(
            "urlset",
            "<loc>https://careers.voith.com/job/Engineer/1/</loc>",
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
        search=([("https://careers.voith.com/job/Engineer/9/", "9")], None, None),
        rss=([], {}, None),
        sitemap=(
            "urlset",
            "<urlset></urlset>",
            "the sitemap hit the 30 MB read cap — postings past it were not listed",
        ),
    )

    raw = scraper.fetch_raw()

    assert [item["id"] for item in raw] == ["9"]  # the search walk answered, and whole
    assert scraper.truncated is None


def test_successfactors_title_from_slug_recovers_a_clean_title():
    """The pre-detail tech gate's only signal. Covers both slug shapes this scraper's tenants
    use — bare ``{title}/{id}/`` and ``{location}-{title}-{state}-{zip}/{id}/`` — plus the
    URL-encoding and HTML-entity noise real slugs carry (measured live on careers.hcltech.com
    and jobs.sap.com, 2026-09-16)."""
    from headstart.scrapers.successfactors import _title_from_slug

    assert (
        _title_from_slug(
            "https://careers.hcltech.com/job/Technical-Specialist/1357856755/"
        )
        == "Technical Specialist"
    )
    assert (
        _title_from_slug(
            "https://careers.hcltech.com/job/SME-Red-Hat-Enterprise-Linux%2C-Red-Hat-Satellite/1/"
        )
        == "SME Red Hat Enterprise Linux, Red Hat Satellite"
    )
    assert (
        _title_from_slug(
            "https://jobs.sap.com/job/Burlington-Account-Executive-MA-01803/1420009533/"
        )
        == "Burlington Account Executive MA 01803"
    )
    assert (
        _title_from_slug(
            "https://x.example.com/job/Track-Manager-%28Tools-%26-Automation%29/1/"
        )
        == "Track Manager (Tools & Automation)"
    )


def test_successfactors_skips_the_detail_fetch_for_a_non_tech_slug(monkeypatch):
    """The gate itself: a non-tech slug never reaches `_job_fields` at all, and its skip does
    not count against the Board's truncation ratio — only genuine fetch failures among the
    postings actually attempted should ever do that (ADR-0053/ADR-0121). Getting this wrong
    the other way — counting every gated-out posting as "lost" — would mark nearly every
    SuccessFactors Board's listing Unauthoritative on every run, since most SuccessFactors
    Boards are not majority-tech (measured: 21.3% on careers.hcltech.com, 28.7% on
    jobs.sap.com, both 2026-09-16 samples)."""
    fetched: list[str] = []

    def any_titled_page(url):
        fetched.append(url)
        return FakeResponse(text=_successfactors_job_page("whatever the page says"))

    scraper = _successfactors_board(
        monkeypatch,
        slug="jobs.example.com",
        sitemap=("urlset", "", None),
        search=(
            [
                ("https://jobs.example.com/job/Software-Engineer/1/", "1"),
                ("https://jobs.example.com/job/Housekeeper/2/", "2"),
                ("https://jobs.example.com/job/Data-Engineer/3/", "3"),
            ],
            None,
            None,
        ),
        rss=([], {}, None),
        job_page=any_titled_page,
    )
    # The gate is conditional on `have_details`, the pipeline's own signal — an empty container
    # says "the pipeline is running and holds no detail for this Board", which is the first-run
    # state. Without it this scraper is a direct caller and keeps the whole Board; that is
    # asserted separately below.
    scraper.have_details = frozenset()

    raw = scraper.fetch_raw()

    assert [j for j in fetched if "/2/" in j] == [], (
        "the non-tech slug's detail page was never requested"
    )
    assert {item["id"] for item in raw} == {"1", "3"}
    assert scraper.truncated is None, (
        "skipping a non-tech posting is not a loss against the tech-only denominator"
    )


def test_successfactors_gate_is_off_for_a_caller_outside_the_pipeline(monkeypatch):
    """A directly-constructed scraper keeps the whole Board.

    This gate shipped unconditional in #503, unlike eightfold's. The cost was measurable: run
    35193130454's `filter_tech` reported `successfactors 32,891/33,035 = 99.6% tech`, because a
    non-tech posting never reached the corpus, so the ATS's real tech share had stopped being
    readable from the pipeline's own data. `verify_scraper.py` and the enrichment samplers build
    scrapers this way and need the Board whole."""
    scraper = _successfactors_board(
        monkeypatch,
        slug="jobs.example.com",
        sitemap=("urlset", "", None),
        search=(
            [
                ("https://jobs.example.com/job/Software-Engineer/1/", "1"),
                ("https://jobs.example.com/job/Housekeeper/2/", "2"),
            ],
            None,
            None,
        ),
        rss=([], {}, None),
    )

    assert scraper.have_details is None, "the default for a direct caller"
    assert {item["id"] for item in scraper.fetch_raw()} == {"1", "2"}


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


class _NonJsonListing(_Status):
    def __init__(self, text, *, status_code=200, content_type="text/html", url=""):
        super().__init__(status_code=status_code, text=text, url=url)
        self.content = text.encode()
        self.headers = {"content-type": content_type}

    def json(self):
        raise json.JSONDecodeError("Expecting value", self.text, 0)


def test_workday_pick_subdivision_facet_descends_into_a_nested_location_group():
    """``locationMainGroup`` wraps ``primaryLocation``/``locationCountry`` rather than
    holding leaf values itself — live-verified on bridgestone/external, 2026-09-16:
    applying ``locationMainGroup`` directly answers HTTP 400, and its own entries carry
    no id/count. The real, filterable values are one level deeper, under each child's
    own ``facetParameter``. A flat reading treats the wrapper as an empty facet and
    reports the board unsplittable when a genuine, usable dimension is sitting right
    there.
    """
    from headstart.scrapers import workday as workday_mod

    facets = [
        {"facetParameter": "jobFamily", "values": [{"id": "PSR", "count": 2386}]},
        {
            "facetParameter": "locationMainGroup",
            "values": [
                {
                    "facetParameter": "primaryLocation",
                    "descriptor": "Primary Location",
                    "values": [
                        {"descriptor": "Manchester", "id": "loc-1", "count": 2},
                        {"descriptor": "Talleyville", "id": "loc-2", "count": 3},
                    ],
                },
                {
                    "facetParameter": "locationCountry",
                    "descriptor": "Location Country",
                    "values": [{"descriptor": "US", "id": "US", "count": 5}],
                },
            ],
        },
    ]

    picked = workday_mod._pick_subdivision_facet(facets, already_applied={"jobFamily"})

    assert picked is not None
    param, items = picked
    assert param == "primaryLocation"  # the child's own key, not the wrapper's
    assert set(items) == {("loc-1", 2), ("loc-2", 3)}
    # locationCountry has only one value here, so it never becomes a candidate.


def test_workday_subdivides_through_a_nested_location_group_when_flat_facets_are_exhausted(
    monkeypatch,
):
    """The residual case ADR-0053 named — a query still capped after every flat facet is
    applied — is not a dead end when the API's location facet is nested, the way
    bridgestone/external's is (live-verified 2026-09-16): the real leaves sit one level
    inside ``locationMainGroup``, and the crawl must descend into them instead of
    reporting nothing left to split.
    """
    scraper = _workday_scraper()

    def post(applied, offset, *, raise_gone=False):
        if not applied:
            return {
                "total": 2000,
                "jobPostings": [{"bulletFields": ["root"]}],
                "facets": [
                    {
                        "facetParameter": "jobFamily",
                        "values": [
                            {"id": "PSR", "count": 2000},
                            {"id": "Other", "count": 5},
                        ],
                    }
                ],
            }
        if applied == {"jobFamily": ["Other"]}:
            return {
                "total": 5,
                "jobPostings": [{"bulletFields": ["other"]}],
                "facets": [],
            }
        if applied == {"jobFamily": ["PSR"]}:
            return {
                "total": 2000,
                "jobPostings": [{"bulletFields": ["psr"]}],
                "facets": [
                    {
                        "facetParameter": "locationMainGroup",
                        "values": [
                            {
                                "facetParameter": "primaryLocation",
                                "values": [
                                    {"id": "store-1", "count": 1000},
                                    {"id": "store-2", "count": 1000},
                                ],
                            },
                            {
                                "facetParameter": "locationCountry",
                                "values": [{"id": "US", "count": 2000}],
                            },
                        ],
                    }
                ],
            }
        store = applied["primaryLocation"][
            0
        ]  # a resolved store-level slice: small, final
        return {"total": 1, "jobPostings": [{"bulletFields": [store]}], "facets": []}

    monkeypatch.setattr(scraper, "_post", post)
    absorbed: list[dict] = []
    scraper._exhaust({}, absorbed.extend, depth=0)

    assert {p["bulletFields"][0] for p in absorbed} == {
        "root",
        "psr",
        "other",
        "store-1",
        "store-2",
    }
    assert scraper.truncated is None  # fully read via the nested facet, not given up on


def test_detail_exception_telemetry_keeps_a_settled_http_status():
    scraper = _workday_scraper()
    exc = http.RequestsError("service unavailable")
    exc.response = SimpleNamespace(status_code=503)

    scraper.note_detail_exception(exc)

    assert scraper.detail_losses == {"HTTP 503": 1}


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
    assert (
        _description_text(ZohoScraper("jobs.acme.com")._detail_record_of(page))
        == expected
    )


def _zoho_listing(records):
    import html as _html

    return (
        f'<input type="hidden" value="{_html.escape(json.dumps(records))}" id="jobs">'
    )


def _zoho_detail_page(record: dict) -> str:
    """A detail page embedding ``record`` the way Zoho does: JSON inside ``JSON.parse('…')``."""
    return f"var jobs = JSON.parse('{json.dumps([record])}')"


def _zoho_served(records, detail_for):
    """A zoho Board whose careers page lists ``records`` and whose detail pages
    ``detail_for(job_id)`` answers, both through a FakeFetcher."""
    from headstart.scrapers.zoho import ZohoScraper

    board = "https://acme.zohorecruit.in/jobs/Careers"

    def route(method, url, kwargs):
        if url == board:
            return FakeResponse(text=_zoho_listing(records))
        return detail_for(url.rsplit("/", 1)[1])

    fetcher = FakeFetcher(route)
    return ZohoScraper("acme.zohorecruit.in", "Acme", fetcher=fetcher), fetcher


def test_zoho_fetches_every_job_detail_not_just_empty_descriptions():
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
    scraper, fetcher = _zoho_served(records, lambda job_id: FakeResponse(404, "gone"))
    scraper.fetch_raw()

    fetched_ids = [url.rsplit("/", 1)[1] for url in fetcher.urls()[1:]]
    assert sorted(fetched_ids) == ["1", "2"]  # not "3" (locked) or "4" (unpublished)


def test_zoho_parse_prefers_the_salary_enriched_detail_description():
    """The detail record is a strict superset of the listing's bare Job_Description — it carries
    the same text PLUS Salary/Currency. Preferring the listing (the old precedence) would
    silently discard the Salary a detail fetch just paid bandwidth to collect, both from the
    description text and from the new `Job.salary` field."""
    records = [
        {"id": "1", "Job_Description": "Plain listing text.", "Is_Locked": False}
    ]
    detail = {
        "id": "1",
        "Job_Description": "Plain listing text.",
        "Salary": "10-12",
        "Currency": "LPA",
    }
    scraper, _fetcher = _zoho_served(
        records, lambda job_id: FakeResponse(text=_zoho_detail_page(detail))
    )

    raw = scraper.fetch_raw()
    jobs = scraper.parse(raw, SCRAPED_AT)

    assert jobs[0].description == "Plain listing text. Salary: 10-12 Currency: LPA"
    assert jobs[0].salary == "10-12 LPA"


@pytest.mark.parametrize("async_fanout", ["1", "0"])
def test_zoho_parse_falls_back_to_the_listing_if_the_detail_fetch_failed(
    monkeypatch, async_fanout
):
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", async_fanout)
    records = [
        {"id": "1", "Job_Description": "Plain listing text.", "Is_Locked": False}
    ]
    scraper, _fetcher = _zoho_served(records, lambda job_id: FakeResponse(503, "down"))

    raw = scraper.fetch_raw()
    jobs = scraper.parse(raw, SCRAPED_AT)

    assert jobs[0].description == "Plain listing text."
    assert scraper.detail_losses == {"HTTP 503": 1}


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
    assert wall_surface["egress_on"] == frozenset({403, 405, 429})


def test_workday_opts_into_the_spare_egress_on_429(monkeypatch):
    """Provisional experiment (ADR-0063, amended): Workday's metering was measured to be per
    (source IP x instance host), so a second egress is a second allocation rather than a way of
    ignoring a rate limit. Only the sync listing POST can lose a Board — a detail failure returns
    None — so that is the call that must carry the opt-in.

    429 only, as of ADR-0103 reverting ADR-0102: the 400 ADR-0102 added is a stale session cookie,
    which a route change neither causes nor cures, so it is cleared in-pass rather than rerouted.
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


def test_workday_400_does_not_wall_the_group(monkeypatch):
    """ADR-0103 reverts ADR-0102: a settled 400 no longer opens the spare egress.

    The 400 is a stale session cookie, and the rerouted probe arm showed a route change does
    nothing for it, so rotating on it would spend the escape hatch for no gain while the genuine
    429 in the same walk still needs it. Driven through `http.fetch` with Workday's own opt-in
    set rather than by asserting the constant, so it fails if the wiring stops matching.
    """
    from headstart import spare_egress
    from headstart.scrapers.workday import WorkdayScraper

    monkeypatch.setattr(spare_egress, "proxy_url", lambda: "socks5://127.0.0.1:40000")
    calls: list[dict] = []

    class _Session:
        def request(self, method, url, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(status_code=400, headers={})

    monkeypatch.setattr(http, "session", lambda: _Session())
    monkeypatch.setattr(http.time, "sleep", lambda *a: None)

    spare_egress.reset()
    try:
        response = http.fetch(
            "GET",
            "detail",
            egress_group="workday",
            egress_on=WorkdayScraper.egress_fallback_on,  # 429 only now
        )
        assert response.status_code == 400
        assert "workday" not in spare_egress.walled_groups()  # a 400 does not wall
        assert len(calls) == 1  # not retried, not rerouted
    finally:
        spare_egress.reset()


def test_workday_detail_400_clears_the_session_cookie_and_recovers(monkeypatch):
    """ADR-0103: a detail 400 is Workday's own 'session cookie is invalid'. The scraper clears the
    shared session's jar and refetches once — where the old retry re-sent the dead cookie and 400d
    again (measured). The recovered detail is served and counted `_COOKIE_RECOVERED`, not lost.
    Runs the real async path (where the 400s land) with a fake session whose jar, once cleared,
    makes the origin answer 200 — the measured shape.
    """
    import asyncio
    from collections import Counter

    from headstart.scrapers.workday import _COOKIE_RECOVERED, WorkdayScraper

    detail = {
        "jobPostingInfo": {"id": "1", "title": "Eng", "jobDescription": "<p>d</p>"}
    }

    class _Cookies:
        def __init__(self):
            self.cleared = 0

        def clear(self):
            self.cleared += 1

    class _Session:
        def __init__(self):
            self.cookies = _Cookies()

    session = _Session()

    async def fake_fetch_async(sess, method, url, **kw):
        # poisoned until the jar is cleared, then 200 — exactly the recovery arm's 3/3 result
        if sess.cookies.cleared == 0:
            return SimpleNamespace(status_code=400, headers={}, text="", json=dict)
        return SimpleNamespace(
            status_code=200, headers={}, text="{}", json=lambda: detail
        )

    monkeypatch.setattr(http, "fetch_async", fake_fetch_async)

    scraper = WorkdayScraper("https://x.wd1.myworkdayjobs.com/ext")
    scraper._instance = "wd1"
    classes: Counter[str] = Counter()
    out = asyncio.run(scraper._job_detail_async(session, "/job/x/Eng_JR1", classes))

    assert out is not None  # the detail was recovered, not dropped
    assert session.cookies.cleared == 1  # the jar was cleared exactly once
    assert classes[_COOKIE_RECOVERED] == 1  # counted as a recovery
    assert "HTTP 400" not in classes  # and not as a loss


def test_workday_detail_400_that_survives_the_cookie_reset_is_a_loss(monkeypatch):
    """The reset is one refetch, not a loop. A 400 that persists after clearing the jar settles as
    an ordinary `HTTP 400` loss, with no recovery label — the invariant `_report_detail_losses`
    depends on (a recovered detail is non-None; a loss is None)."""
    import asyncio
    from collections import Counter

    from headstart.scrapers.workday import _COOKIE_RECOVERED, WorkdayScraper

    class _Cookies:
        def __init__(self):
            self.cleared = 0

        def clear(self):
            self.cleared += 1

    class _Session:
        def __init__(self):
            self.cookies = _Cookies()

    session = _Session()

    async def always_400(sess, method, url, **kw):
        return SimpleNamespace(status_code=400, headers={}, text="", json=dict)

    monkeypatch.setattr(http, "fetch_async", always_400)

    scraper = WorkdayScraper("https://x.wd1.myworkdayjobs.com/ext")
    scraper._instance = "wd1"
    classes: Counter[str] = Counter()
    out = asyncio.run(scraper._job_detail_async(session, "/job/x/Eng_JR1", classes))

    assert out is None  # a genuine loss
    assert session.cookies.cleared == 1  # cleared once — not a loop
    assert classes["HTTP 400"] == 1
    assert _COOKIE_RECOVERED not in classes


class _CookieJar:
    """A fake session jar that only counts `clear()` calls — all the ADR-0103 tests need."""

    def __init__(self):
        self.cleared = 0

    def clear(self):
        self.cleared += 1


def _poisoned_until_cleared(jar, good_payload):
    """A listing POST that answers 400 while `jar` is un-cleared and 200 with `good_payload`
    after — the stale-cookie shape ADR-0103 recovers from. Used by both the sync and async
    listing tests, whose only difference is which jar the reset clears."""

    def respond():
        if jar.cleared == 0:
            return _Status(400, payload={})
        return _Status(200, payload=good_payload)

    return respond


def test_workday_listing_400_clears_the_session_cookie_and_recovers(monkeypatch):
    """ADR-0103 extended to the listing pass: a long pagination outlives its session's cookie and
    every page after 400s, marking the whole Board unauthoritative (measured 188 such 400s in the
    first post-fix run, on ghr/verisure/umiami/...). `_post`/`_post_async` clear the jar and
    refetch once, exactly as the detail pass does. Runs the async mid-crawl path."""
    import asyncio

    from headstart.scrapers.workday import WorkdayScraper

    page = {"jobPostings": [{"externalPath": "/job/x/A_1"}], "total": 20}
    session = SimpleNamespace(cookies=_CookieJar())
    respond = _poisoned_until_cleared(session.cookies, page)

    async def fake_fetch_async(sess, method, url, **kw):
        return respond()

    monkeypatch.setattr(http, "fetch_async", fake_fetch_async)

    scraper = WorkdayScraper("https://x.wd1.myworkdayjobs.com/ext")
    scraper._instance = "wd1"
    out = asyncio.run(scraper._post_async(session, {}, 20))

    assert out == page  # the page was recovered, not raised as a board error
    assert session.cookies.cleared == 1  # cleared once


def test_workday_unknown_listing_body_raises_with_bounded_diagnostics_without_retry(
    monkeypatch,
):
    """A shape we cannot classify must remain a Board failure, with enough bounded evidence to
    diagnose it after the runner is gone. It must not be retried merely because JSON parsing
    failed: that would blindly retry parser defects and permanent templates too."""
    from headstart.scrapers.workday import (
        UnexpectedListingResponse,
        WorkdayScraper,
    )

    calls = []
    response = _NonJsonListing(
        "unknown payload api_key=do-not-log " + "x" * 1000,
        content_type="text/plain; charset=utf-8",
        url="https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/ext/jobs?secret=drop",
    )

    def fetch(*args, **kwargs):
        calls.append((args, kwargs))
        return response

    monkeypatch.setattr(http, "fetch", fetch)
    scraper = WorkdayScraper("https://acme.wd1.myworkdayjobs.com/ext")
    scraper._instance = "wd1"

    with pytest.raises(UnexpectedListingResponse) as raised:
        scraper._post({}, 0, raise_gone=True)

    message = str(raised.value)
    assert "classification=unexpected-body" in message
    assert "instance=wd1" in message
    assert "status=200" in message
    assert "content_type=text/plain; charset=utf-8" in message
    assert (
        "final_url=https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/ext/jobs" in message
    )
    assert "bytes=1035" in message
    assert "sha256=" in message
    assert "body_prefix='unknown payload " in message
    assert "secret=drop" not in message
    assert "do-not-log" not in message
    assert "api_key=[redacted]" in message
    assert len(message) < 900
    assert len(calls) == 1
    assert scraper.telemetry["listing_pages"] == 1
    assert scraper.telemetry["listing_fetch_calls"] == 1
    assert scraper.telemetry["listing_page_losses"] == 1


def test_workday_listing_diagnostic_names_the_instance_actually_requested(monkeypatch):
    """ADR-0140's diagnostic records the Workday instance. `_instance` is only the migration
    override — None on every tenant that never moved — so reading it logged `instance=None` on
    all 25 production recurrences; the instance requested is the one `_parts()` resolves."""
    from headstart.scrapers.workday import UnexpectedListingResponse, WorkdayScraper

    monkeypatch.setattr(http, "fetch", lambda *a, **k: _NonJsonListing("unknown"))
    scraper = WorkdayScraper("https://acme.wd5.myworkdayjobs.com/ext")

    with pytest.raises(UnexpectedListingResponse, match="instance=wd5 "):
        scraper._post({}, 0, raise_gone=True)


def test_workday_listing_diagnostic_redacts_a_bearer_credential():
    from headstart.scrapers.workday import _listing_diagnostic

    response = _NonJsonListing("authorization=Bearer bearer-secret trailing-text")
    diagnostic, _ = _listing_diagnostic(response, "wd1")

    assert "authorization=[redacted] trailing-text" in diagnostic
    assert "bearer-secret" not in diagnostic


def test_workday_error_page_retries_once_and_recovers(monkeypatch, caplog):
    """The live 2026-09-12 probe saw this Workday-branded HTML error shape settle and the exact
    request immediately recover. This one positively identified transient class earns one retry."""
    from headstart.scrapers.workday import WorkdayScraper

    page = {"jobPostings": [{"externalPath": "/job/x/A_1"}], "total": 1}
    outcomes = [
        _NonJsonListing(
            "<html><div class='graphicsContainer'><img class='wdayLogo'></div></html>",
            status_code=520,
            url="https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/ext/jobs",
        ),
        _Status(payload=page),
    ]
    calls = []

    def fetch(*args, **kwargs):
        calls.append((args, kwargs))
        return outcomes.pop(0)

    monkeypatch.setattr(http, "fetch", fetch)
    caplog.set_level(logging.INFO, logger="headstart.scrapers.workday")
    scraper = WorkdayScraper("https://acme.wd1.myworkdayjobs.com/ext")
    scraper._instance = "wd1"

    assert scraper._post({}, 0, raise_gone=True) == page
    assert len(calls) == 2
    assert calls[0][1]["egress_group"] == "workday"
    assert "egress_group" not in calls[1][1], (
        "a classified HTML challenge must escape the WARP route that the runner probe showed "
        "causes it"
    )
    assert "classification=workday-error-page" in caplog.text
    assert "retrying once" in caplog.text
    assert scraper.telemetry["listing_pages"] == 1
    assert scraper.telemetry["listing_fetch_calls"] == 2
    assert scraper.telemetry["listing_status_failures"] == 1
    assert scraper.telemetry.get("listing_page_losses", 0) == 0
    assert scraper.telemetry["listing_transient_recovered"] == 1


def test_workday_persistent_transient_listing_body_still_raises(monkeypatch):
    """One retry is a bound, not a loop; the second bad response remains an observation failure."""
    from headstart.scrapers.workday import UnexpectedListingResponse, WorkdayScraper

    calls = []

    def fetch(*args, **kwargs):
        calls.append((args, kwargs))
        return _NonJsonListing(
            "<html><title>Maintenance</title><p>Temporarily unavailable</p></html>"
        )

    monkeypatch.setattr(http, "fetch", fetch)
    scraper = WorkdayScraper("https://acme.wd1.myworkdayjobs.com/ext")
    scraper._instance = "wd1"

    with pytest.raises(UnexpectedListingResponse, match="classification=maintenance"):
        scraper._post({}, 0, raise_gone=True)
    assert len(calls) == 2


def test_workday_structured_http_error_after_transient_retry_is_not_recovered(
    monkeypatch,
):
    from headstart.scrapers.workday import WorkdayScraper

    outcomes = [
        _NonJsonListing("<html><title>Maintenance</title></html>"),
        _Status(status_code=403, payload={"message": "denied"}),
    ]
    monkeypatch.setattr(http, "fetch", lambda *args, **kwargs: outcomes.pop(0))
    scraper = WorkdayScraper("https://acme.wd1.myworkdayjobs.com/ext")
    scraper._instance = "wd1"

    with pytest.raises(http.RequestsError, match="403"):
        scraper._post({}, 0, raise_gone=True)

    assert scraper.telemetry.get("listing_transient_recovered", 0) == 0
    assert scraper.telemetry["listing_page_losses"] == 1


def test_workday_async_structured_http_error_after_retry_is_not_recovered(monkeypatch):
    import asyncio

    from headstart.scrapers.workday import WorkdayScraper

    outcomes = [
        _NonJsonListing("<html><title>Maintenance</title></html>"),
        _Status(status_code=403, payload={"message": "denied"}),
    ]

    async def fetch_async(*args, **kwargs):
        return outcomes.pop(0)

    monkeypatch.setattr(http, "fetch_async", fetch_async)
    scraper = WorkdayScraper("https://acme.wd1.myworkdayjobs.com/ext")
    scraper._instance = "wd1"

    with pytest.raises(http.RequestsError, match="403"):
        asyncio.run(scraper._post_async(SimpleNamespace(cookies=_CookieJar()), {}, 20))

    assert scraper.telemetry.get("listing_transient_recovered", 0) == 0
    assert scraper.telemetry["listing_page_losses"] == 1


# Workday's own 455-byte Tomcat page, what 20 of the 25 production listing failures in runs
# 35737647801/35733082366 settled on (status=500 content_type=text/html).
_TOMCAT_500 = (
    "<!doctype html><html><head><title>HTTP Status 500 – Internal Server Error</title>"
    "</head><body><h1>HTTP Status 500 – Internal Server Error</h1></body></html>"
)


def test_workday_html_500_mid_crawl_is_one_lost_page_not_a_failed_board(monkeypatch):
    """ADR-0140 made an unrecognised *2xx* body a Board failure; it did not repeal ADR-0076 for
    an error status that happens to carry an HTML page. Parsing before the status turned one
    settled 500 page into `UnexpectedListingResponse` — not a `RequestsError`, so `_paginate`
    let it escape and `fetch_raw` discarded every posting already read. Drives the real
    `_exhaust` -> `_paginate_async` -> `_post_async`, faking only the transport."""
    from headstart.scrapers.workday import WorkdayScraper

    def page(offset):
        return _Status(
            payload={
                "total": 100,
                "jobPostings": [{"bulletFields": [f"R{offset}"]}],
                "facets": [],
            }
        )

    async def fetch_async(session, method, url, **kw):
        offset = kw["json"]["offset"]
        if offset == 40:
            return _NonJsonListing(_TOMCAT_500, status_code=500)
        return page(offset)

    def fetch(
        method, url, **kw
    ):  # the second pass (ADR-0076 amendment) fails the page again
        offset = kw["json"]["offset"]
        if offset == 40:
            return _NonJsonListing(_TOMCAT_500, status_code=500)
        return page(offset)

    monkeypatch.setattr(http, "fetch", fetch)
    monkeypatch.setattr(http, "fetch_async", fetch_async)
    scraper = WorkdayScraper("https://acme.wd1.myworkdayjobs.com/ext")
    absorbed = []
    scraper._exhaust({}, absorbed.extend, depth=0)

    assert sorted(p["bulletFields"][0] for p in absorbed) == [
        "R0",
        "R20",
        "R60",
        "R80",
    ]
    assert "1 of 5 page(s) failed mid-crawl" in scraper.truncated
    assert scraper.telemetry["listing_loss_causes"] == {"HTTP 500": 2}


def _second_pass_scraper(monkeypatch, answer):
    from headstart.scrapers.workday import WorkdayScraper

    asked = []
    scraper = WorkdayScraper("https://acme.wd1.myworkdayjobs.com/ext")

    def post(applied, offset, **_):
        asked.append(offset)
        return answer

    monkeypatch.setattr(scraper, "_post", post)
    return scraper, asked


def test_workday_second_pass_stops_at_its_per_board_budget(monkeypatch):
    """Five lost pages are asked again; six are an origin failing and none are. The budget is
    the Board's, and a slice needing more than is left is skipped whole, not asked in part."""
    from collections import Counter

    from headstart.scrapers.workday import _SECOND_PASS_MAX

    assert _SECOND_PASS_MAX == 5
    page = {"jobPostings": [{"bulletFields": ["R"]}]}

    scraper, asked = _second_pass_scraper(monkeypatch, page)
    six = {offset: "HTTP 500" for offset in range(20, 140, 20)}
    assert scraper._second_pass({}, six, [].extend, Counter({"HTTP 500": 6})) == 0
    assert asked == []

    scraper, asked = _second_pass_scraper(monkeypatch, page)
    five = dict(list(six.items())[:5])
    assert scraper._second_pass({}, five, [].extend, Counter({"HTTP 500": 5})) == 5
    assert asked == list(five)

    scraper, asked = _second_pass_scraper(monkeypatch, page)
    three = {20: "HTTP 500", 40: "HTTP 500", 60: "HTTP 500"}
    assert scraper._second_pass({}, three, [].extend, Counter({"HTTP 500": 3})) == 3
    # a second slice losing three more is past what is left of the Board's five
    assert scraper._second_pass({}, three, [].extend, Counter({"HTTP 500": 3})) == 0
    assert asked == [20, 40, 60]


def test_workday_a_page_that_404s_on_the_second_pass_is_labelled_a_404(monkeypatch):
    """Still lost, and the truncation reason names why it is lost now, not why it was first."""
    from collections import Counter

    scraper, _ = _second_pass_scraper(monkeypatch, None)
    classes = Counter({"HTTP 500": 1})
    assert scraper._second_pass({}, {40: "HTTP 500"}, [].extend, classes) == 0
    assert classes == Counter({"404 mid-crawl": 1})


def test_workday_a_page_lost_mid_crawl_is_tried_once_more_after_the_fan_out(
    monkeypatch,
):
    """Pipeline runs 35971969417..35998606646: 71 of 132 scope exclusions were Workday Boards that
    lost 1-2 pages to a ConnectionError or HTTP 500 and were never asked again, so the whole
    Board left eviction scope. A page that answers on the second pass is read, and the Board
    stays authoritative."""
    from headstart.scrapers.workday import WorkdayScraper

    def page(offset):
        return _Status(
            payload={
                "total": 100,
                "jobPostings": [{"bulletFields": [f"R{offset}"]}],
                "facets": [],
            }
        )

    async def fetch_async(session, method, url, **kw):
        offset = kw["json"]["offset"]
        if offset == 40:
            return _NonJsonListing(_TOMCAT_500, status_code=500)
        return page(offset)

    monkeypatch.setattr(
        http, "fetch", lambda method, url, **kw: page(kw["json"]["offset"])
    )
    monkeypatch.setattr(http, "fetch_async", fetch_async)
    scraper = WorkdayScraper("https://acme.wd1.myworkdayjobs.com/ext")
    absorbed = []
    scraper._exhaust({}, absorbed.extend, depth=0)

    assert sorted(p["bulletFields"][0] for p in absorbed) == [
        "R0",
        "R20",
        "R40",
        "R60",
        "R80",
    ]
    assert scraper.truncated is None
    assert scraper.telemetry["listing_second_pass_recovered"] == 1


@pytest.mark.parametrize(
    "responses",
    [
        pytest.param(lambda: [_NonJsonListing(_TOMCAT_500, status_code=500)], id="500"),
        pytest.param(
            lambda: [
                _NonJsonListing(
                    "<html><title>Just a moment...</title></html>", status_code=429
                )
                for _ in range(2)
            ],
            id="429-challenge-twice",
        ),
    ],
)
def test_workday_html_error_status_raises_as_an_http_error(monkeypatch, responses):
    """The sync `_post` too — every slice's first page, which `_exhaust`'s slice loop drops as a
    truncation only if it is a `RequestsError`. A challenge still earns its one direct retry
    (ADR-0140); what the persisting error status raises afterwards is the status."""
    from headstart.scrapers.workday import WorkdayScraper

    outcomes = responses()
    calls = len(outcomes)
    monkeypatch.setattr(http, "fetch", lambda *a, **k: outcomes.pop(0))
    scraper = WorkdayScraper("https://acme.wd1.myworkdayjobs.com/ext")

    with pytest.raises(http.RequestsError):
        scraper._post({"jobFamilyGroup": ["x"]}, 0, raise_gone=True)
    assert outcomes == []
    assert scraper.telemetry["listing_fetch_calls"] == calls
    assert scraper.telemetry["listing_page_losses"] == 1


def test_workday_async_challenge_retries_once_and_recovers(monkeypatch):
    """Concurrent pagination uses the same classification and bounded retry contract."""
    import asyncio

    from headstart.scrapers.workday import WorkdayScraper

    page = {"jobPostings": [{"externalPath": "/job/x/A_1"}], "total": 20}
    challenge = _NonJsonListing("<html><title>Just a moment...</title></html>")
    challenge.headers["cf-mitigated"] = "challenge"
    outcomes = [challenge, _Status(payload=page)]
    calls = []

    async def fetch_async(*args, **kwargs):
        calls.append((args, kwargs))
        return outcomes.pop(0)

    monkeypatch.setattr(http, "fetch_async", fetch_async)
    scraper = WorkdayScraper("https://acme.wd1.myworkdayjobs.com/ext")
    scraper._instance = "wd1"

    assert (
        asyncio.run(scraper._post_async(SimpleNamespace(cookies=_CookieJar()), {}, 20))
        == page
    )
    assert outcomes == []
    assert calls[0][1]["egress_group"] == "workday"
    assert "egress_group" not in calls[1][1]


def test_workday_transient_retry_to_404_preserves_sync_call_site_contract(monkeypatch):
    """A retry does not bypass the established 404 split: mid-crawl returns None while the
    whole Board's first page raises. Either path records one logical page loss, not two HTTP
    responses as two lost pages."""
    from headstart.scrapers.workday import WorkdayScraper

    def drive(*, raise_gone):
        outcomes = [
            _NonJsonListing("<html><title>Maintenance</title></html>"),
            _Status(status_code=404),
        ]
        monkeypatch.setattr(http, "fetch", lambda *a, **k: outcomes.pop(0))
        scraper = WorkdayScraper("https://acme.wd1.myworkdayjobs.com/ext")
        scraper._instance = "wd1"
        if raise_gone:
            with pytest.raises(http.RequestsError, match="404"):
                scraper._post({}, 0, raise_gone=True)
        else:
            assert scraper._post({}, 20) is None
        assert scraper.telemetry["listing_page_losses"] == 1
        assert scraper.telemetry["listing_fetch_calls"] == 2
        assert scraper.telemetry["listing_status_failures"] == 1

    drive(raise_gone=False)
    drive(raise_gone=True)


def test_workday_transient_retry_to_404_preserves_async_midcrawl_contract(monkeypatch):
    import asyncio

    from headstart.scrapers.workday import WorkdayScraper

    outcomes = [
        _NonJsonListing("<html><title>Maintenance</title></html>"),
        _Status(status_code=404),
    ]

    async def fetch_async(*args, **kwargs):
        return outcomes.pop(0)

    monkeypatch.setattr(http, "fetch_async", fetch_async)
    scraper = WorkdayScraper("https://acme.wd1.myworkdayjobs.com/ext")
    scraper._instance = "wd1"

    assert (
        asyncio.run(scraper._post_async(SimpleNamespace(cookies=_CookieJar()), {}, 20))
        is None
    )
    assert scraper.telemetry["listing_page_losses"] == 1
    assert scraper.telemetry["listing_fetch_calls"] == 2
    assert scraper.telemetry["listing_status_failures"] == 1


def test_workday_listing_400_that_persists_still_raises(monkeypatch):
    """One refetch, not a loop: a listing 400 that survives the cookie clear raises like any other
    page error (the caller records it, `_paginate` marks the Board unauthoritative) — the reset
    only ever adds one attempt."""
    import asyncio

    from headstart.scrapers.workday import WorkdayScraper

    session = SimpleNamespace(cookies=_CookieJar())

    async def always_400(sess, method, url, **kw):
        return _Status(400, payload={})

    monkeypatch.setattr(http, "fetch_async", always_400)

    scraper = WorkdayScraper("https://x.wd1.myworkdayjobs.com/ext")
    scraper._instance = "wd1"
    with pytest.raises(http.RequestsError):
        asyncio.run(scraper._post_async(session, {}, 20))
    assert session.cookies.cleared == 1  # cleared once — not a loop


def test_workday_listing_400_recovers_on_the_sync_first_page(monkeypatch):
    """The sync `_post` — the first page of every slice, via `_exhaust` — rides the pooled
    thread-local session, so its jar is `http.session().cookies`, not an `AsyncSession`'s. Same
    contract as the async path: a 400 clears that jar and refetches once, and the page comes
    back instead of raising as a board error."""
    from headstart.scrapers.workday import WorkdayScraper

    page = {"jobPostings": [{"externalPath": "/job/x/A_1"}], "total": 20}
    pooled = SimpleNamespace(cookies=_CookieJar())
    respond = _poisoned_until_cleared(pooled.cookies, page)
    monkeypatch.setattr(http, "session", lambda: pooled)
    monkeypatch.setattr(http, "fetch", lambda method, url, **kw: respond())

    scraper = WorkdayScraper("https://x.wd1.myworkdayjobs.com/ext")
    scraper._instance = "wd1"
    assert scraper._post({}, 0) == page
    assert pooled.cookies.cleared == 1


def test_workday_listing_400_reset_leaves_raise_gone_intact(monkeypatch):
    """The reset sits *before* the 404 branch and must not alter its contract: a 400 that clears
    into a 404 is still "one page of a live board" (None) mid-crawl, and still the raised
    gone-verdict the ADR-0058 quarantine needs on the very first page (`raise_gone=True`)."""
    from headstart.scrapers.workday import WorkdayScraper

    pooled = SimpleNamespace(cookies=_CookieJar())
    monkeypatch.setattr(http, "session", lambda: pooled)

    def four_hundred_then_gone(method, url, **kw):
        return _Status(400, payload={}) if pooled.cookies.cleared == 0 else _Status(404)

    monkeypatch.setattr(http, "fetch", four_hundred_then_gone)

    scraper = WorkdayScraper("https://x.wd1.myworkdayjobs.com/ext")
    scraper._instance = "wd1"

    assert scraper._post({}, 20) is None  # mid-crawl: the caller reports the gap
    pooled.cookies.cleared = 0  # re-poison for the first-page case
    with pytest.raises(http.RequestsError):
        scraper._post(
            {}, 0, raise_gone=True
        )  # first page: the gone-verdict still raises


def test_workday_429_does_not_leak_the_opt_in_to_other_scrapers():
    """The opt-in is per scraper; an ATS that never walled us must stay on its direct route.
    Only board attribution (no routing effect) rides along regardless."""
    from headstart.scrapers.greenhouse import GreenhouseScraper

    assert GreenhouseScraper.egress_fallback_on == frozenset()
    assert GreenhouseScraper("acme").board_fetcher.egress_binding() == {
        "egress_board": "greenhouse:acme"
    }


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


def test_workable_429_walls_the_origin_and_moves_to_the_spare_egress(monkeypatch):
    """workable's 429 IS a per-IP origin budget, so it opts in where personio must not.

    Measured live 2026-09-03 against the wall run 33725210468 hit, which cost one shard 149 of
    its 241 workable Boards in 126s while the other 14 shards lost none: the same board answers
    429 on the walled address and 200 over WARP in the same second, twice each way; five other
    tenants answer 429 from that address while three answer 200 over WARP; and all 149 lost
    boards serve 200 from a rested address, so none of them is a departed tenant. That is the
    per-client-IP evidence ADR-0063 demands, and the discriminator both reverted opt-ins lacked.

    Asserted through an actual 429 rather than off the constant, for the reason the personio test
    gives in reverse: what has to hold is that the fetch **carries** the group, since
    `http.fetch` walls a group only when it is handed an `egress_group`. Reading the constant
    alone would still pass if the Board fetcher stopped threading its binding onto the request.
    """
    from headstart.scrapers.workable import WorkableScraper

    assert WorkableScraper.egress_fallback_on == frozenset({429})

    seen: list[dict] = []

    class _Walled:
        status_code = 429
        headers: ClassVar[dict] = {"cf-mitigated": "challenge"}
        text = ""

        @staticmethod
        def raise_for_status():
            raise http.RequestsError("HTTP Error 429: Too Many Requests")

    monkeypatch.setattr(
        http, "fetch", lambda method, url, **kw: (seen.append(kw), _Walled())[1]
    )
    with pytest.raises(http.RequestsError):
        WorkableScraper("acme", "Acme").fetch_raw()

    assert seen[-1]["egress_group"] == "workable", (
        "a 429 must route workable off the spent IP"
    )
    assert 429 in seen[-1]["egress_on"], "a 429 must mark workable walled"
    assert seen[-1]["egress_board"] == "workable:acme"


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


def test_workday_posting_key_keeps_the_ids_the_detail_used_to_supply():
    """The three Boards this defect was found on keep byte-identical ids, because their own URL
    vouches for the `bulletFields` value the detail used to supply. Verified live: roche, pwc/crm
    and autodesk each 400/400 postings unchanged, so none of them migrates.

    Note the real `externalPath` is load-bearing here. An earlier version of this test passed a
    synthetic path and credited a widened `_REQ_ID_SHAPE` for the result; measured across 2,161
    live postings that shape tier never fired once, and the widening was reverted (ADR-0097 §5)."""
    assert (
        _wd_key(
            ["202607-119609"],
            external_path="/job/Hyderabad/ERP-Solution-Consultant---EHS_202607-119609",
        )
        == "202607-119609"
    )
    assert (
        _wd_key(["741671WD"], external_path="/job/AC-Manila/Cyber-Associate_741671WD")
        == "741671WD"
    )
    assert (
        _wd_key(["26WD97184"], external_path="/job/Toronto/ML-Engineer_26WD97184-2")
        == "26WD97184"
    )


def test_workday_posting_key_prefers_the_longest_field_the_url_vouches_for():
    """`-N` tolerance plus array order would otherwise let a field that is only a PREFIX of the
    real req id win: `2026` and `2026-02608` both match `Nurse_2026-02608`, and `2026` is the
    same string on every posting the board has. Longest-first settles it."""
    assert (
        _wd_key(["2026", "2026-02608"], external_path="/job/Rockford/Nurse_2026-02608")
        == "2026-02608"
    )


def test_workday_posting_key_still_avoids_the_measured_collision():
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


def test_workday_posting_key_rejects_values_no_url_vouches_for():
    """A `bulletFields` value the posting's URL does not end with never becomes the id, whatever
    it looks like — a bare US ZIP+4 and a `DDMMMYYYY` closing-date label both being real shapes
    the module comment records living there. This is the property that lets the shape tier stay
    narrow instead of growing an alternative per tenant."""
    assert _wd_key(["12345-6789"]) == "Some-Title_FALLBACK-999"
    assert _wd_key(["10JAN2026"]) == "Some-Title_FALLBACK-999"
    assert _wd_key(["202607-119609"]) == "Some-Title_FALLBACK-999"


def test_workday_posting_key_rejects_a_ddmmmyyyy_closing_date():
    """A date is the SAME string across many of a tenant's postings, so keying on one collapses
    them onto a single row — the collision `tutorperini` and `nkg` already cost us once. The
    shape tier must not admit `10JAN2026`, and a real req id beside it must still win."""
    assert _wd_key(["10JAN2026"]) == "Some-Title_FALLBACK-999"
    assert _wd_key(["31DEC2026"]) == "Some-Title_FALLBACK-999"
    assert _wd_key(["10JAN2026", "JR00004545"]) == "JR00004545"


def test_workday_posting_key_rejects_a_year_month_as_a_req_id():
    """The widened digits-hyphen-digits shape must not swallow a bare year-month, which
    `_ISO_DATE` (three groups) does not cover — the closing-date labels the module comment
    records living in bulletFields make this a live risk, not a hypothetical."""
    assert _wd_key(["2026-07", "JR00004545"]) == "JR00004545"
    assert _wd_key(["2026-07"]) == "Some-Title_FALLBACK-999"


def test_workday_no_longer_retries_a_400_anywhere(monkeypatch):
    """ADR-0103 reverts ADR-0098: a 400 leaves the retry ladder everywhere. Retrying it re-sends
    the stale session cookie that caused it, so no Workday call opts a 400 into `retry_on` — every
    fetching call takes the shared `http.TRANSIENT`, `_resolve_instance`'s data-centre probe
    included (its `patient` arm, which existed only to retry a 400, is gone; a wrong data centre
    answers 422, which `TRANSIENT` already excludes, so the sweep still fails fast)."""
    import asyncio

    from headstart import http
    from headstart.scrapers.workday import WorkdayScraper

    seen: list[tuple[str, frozenset]] = []

    class _R:
        status_code = 200

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"jobPostingInfo": {}, "total": 0, "jobPostings": []}

    def fake_fetch(method, url, *, retry_on=http.TRANSIENT, **kw):
        seen.append((method, retry_on))
        return _R()

    async def fake_fetch_async(session, method, url, *, retry_on=http.TRANSIENT, **kw):
        seen.append((method, retry_on))
        return _R()

    monkeypatch.setattr(http, "fetch", fake_fetch)
    monkeypatch.setattr(http, "fetch_async", fake_fetch_async)

    scraper = WorkdayScraper("https://x.wd1.myworkdayjobs.com/ext")
    scraper._instance = "wd1"
    scraper._job_detail("/job/x/Some-Title_JR1")
    asyncio.run(scraper._job_detail_async(object(), "/job/x/Some-Title_JR1"))
    scraper._post({}, 0)
    asyncio.run(scraper._post_async(object(), {}, 0))
    WorkdayScraper("https://x.wd1.myworkdayjobs.com/ext")._resolve_instance()

    assert seen, "no fetch was made"
    for method, retry_on in seen:
        assert 400 not in retry_on, (
            f"{method} call still retries a 400 (ADR-0103 reverted that)"
        )
        assert retry_on == http.TRANSIENT, (
            "every Workday call takes the shared set now, not a Workday-specific one"
        )


def test_workday_instance_resolution_never_retries_a_400(monkeypatch):
    """No instance probe retries a 400 — hinted or swept (ADR-0103 removed the `patient` arm).

    A wrong data centre answers 422, which `TRANSIENT` already excludes, so every probe fails over
    fast on the real wrong-centre answer. A 400 there would be a stale session cookie, and
    retrying one re-sends it; the fix belongs in the detail pass (`_detail_from_cookie_retry`),
    not in a data-centre probe that would spend 54 requests learning nothing."""
    from headstart import http
    from headstart.scrapers.workday import WorkdayScraper

    seen: list[frozenset] = []

    class _R:
        status_code = (
            422  # the real wrong-data-centre answer, so every probe fails over
        )

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {}

    monkeypatch.setattr(
        http,
        "fetch",
        lambda method, url, *, retry_on=http.TRANSIENT, **kw: (
            seen.append(retry_on),
            _R(),
        )[1],
    )
    WorkdayScraper("https://x.wd1.myworkdayjobs.com/ext")._resolve_instance()

    assert len(seen) > 1, "the sweep did not run"
    assert all(r == http.TRANSIENT for r in seen), (
        "every probe takes the shared set now — no 400-retrying `patient` arm"
    )


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


@pytest.mark.parametrize("async_fanout", ["1", "0"])
def test_eightfold_detail_requests_carry_the_referer_and_opt_into_the_spare_egress(
    monkeypatch, async_fanout
):
    """Both Detail passes — `position_details` and the sitemap fallback's job pages — send one
    request description on either transport: the careers-page Referer the multiplexed copies
    used to drop (ADR-0201), and the egress group that walls this ATS on a 403/405/429."""
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", async_fanout)
    sitemap = "https://acme.eightfold.ai/careers/sitemap.xml"
    job_page = "https://acme.eightfold.ai/careers/job/7"
    scraper, fetcher = _eightfold_board(
        job_pages={
            sitemap: f"<urlset><url><loc>{job_page}</loc></url></urlset>",
            job_page: "<html></html>",
        }
    )

    scraper._api_records("acme.com", [{"id": "1", "name": "Backend Engineer"}])
    scraper._sitemap_records()

    sent = [(request.url, request.kwargs) for request in fetcher.requests]
    assert [url for url, _ in sent] == [
        scraper._details_url("acme.com", "1"),
        sitemap,
        job_page,
    ]
    assert [kwargs["headers"]["Accept"] for _, kwargs in sent] == [
        "application/json",
        "application/xml",
        "text/html",
    ]
    for _, kwargs in sent:
        assert kwargs["headers"]["Referer"] == "https://acme.eightfold.ai/careers"
        assert kwargs["egress_group"] == "eightfold"
        assert kwargs["egress_on"] == frozenset({403, 405, 429})


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


@pytest.mark.parametrize("async_fanout", ["1", "0"])
def test_successfactors_marks_truncation_when_detail_pages_are_lost(
    monkeypatch, async_fanout
):
    """ADR-0053: a Board whose returned list is knowingly short must say so.

    Every SuccessFactors field comes from the job page, so `parse` drops a Job whose page did
    not arrive. `report_detail_gaps` counted those losses into a log line and stopped there —
    nothing reached `truncated`, so `index sync` saw a shorter list and evicted the difference
    as delistings. The Jobs were still posted; only their detail fetch had failed.
    """
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", async_fanout)
    scraper = _successfactors_board(
        monkeypatch,
        slug="jobs.example.com",
        sitemap=("urlset", "", None),
        search=(
            [
                (f"https://jobs.example.com/job/Engineer/{i}/", str(i))
                for i in (1, 2, 3)
            ],
            None,
            None,
        ),
        rss=([], {}, None),
        # the middle page 404s; the other two read fine
        job_page=lambda url: (
            FakeResponse(404)
            if url.endswith("/2/")
            else FakeResponse(text=_successfactors_job_page())
        ),
    )

    raw = scraper.fetch_raw()

    assert len(scraper.parse(raw, "2026-01-01")) == 2, "the lost page's Job is dropped"
    assert scraper.detail_losses == {"HTTP 404": 1}
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
            [
                (f"https://jobs.example.com/job/Engineer/{i}/", str(i))
                for i in (1, 2, 3)
            ],
            None,
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
    monkeypatch.setattr(scraper, "_sitemal_fields", dict)
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "0")

    raw = scraper.fetch_raw()

    assert len(scraper.parse(raw, "2026-01-01")) == 2, (
        "the title-less page's Job is dropped"
    )
    assert scraper.truncated and "unreadable" in scraper.truncated


def _eightfold_job_page_url(position_slug):
    return f"https://acme.eightfold.ai/careers/job/{position_slug}"


def _eightfold_sitemap_board(readable_slugs, listed_slugs=(1, 2, 3)):
    """An Eightfold Board on its sitemap fallback, listing the job page of each of
    ``listed_slugs``; the page of each of ``readable_slugs`` carries a JobPosting and every other
    one 404s."""
    posting = (
        '<script type="application/ld+json">'
        '{"@type": "JobPosting", "title": "Engineer"}</script>'
    )
    sitemap = "".join(
        f"<url><loc>{_eightfold_job_page_url(position_slug)}</loc></url>"
        for position_slug in listed_slugs
    )
    return _eightfold_board(
        job_pages={
            "https://acme.eightfold.ai/careers/sitemap.xml": f"<urlset>{sitemap}</urlset>",
            **{
                _eightfold_job_page_url(position_slug): posting
                for position_slug in readable_slugs
            },
        }
    )


@pytest.mark.parametrize("async_fanout", ["1", "0"])
def test_eightfold_sitemap_fallback_marks_truncation_when_pages_are_lost(
    monkeypatch, async_fanout
):
    """Same ADR-0053 hole on the surface eightfold takes whenever the API 403s."""
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", async_fanout)
    scraper, _fetcher = _eightfold_sitemap_board(readable_slugs=(1, 3))

    records = scraper._sitemap_records()

    assert {record["id"]: record["fields"] is None for record in records} == {
        "1": False,
        "2": True,
        "3": False,
    }
    assert scraper.detail_losses == {"HTTP 404": 1}
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


def test_darwinbox_marks_a_measured_shortfall_against_job_counts(monkeypatch):
    """A natural short-page end isn't proof the board is exhausted if the envelope's own
    `job_counts` says otherwise — issue #549, live-confirmed 2026-09-22 (module docstring):
    stable across a real multi-page board including its own terminal short page."""
    from headstart.scrapers import darwinbox as db

    s = db.DarwinboxScraper("acme")

    def _alljobs(host, page):
        s._job_counts = 10
        return [{"id": 1}]  # a short page, but job_counts says the board isn't done

    monkeypatch.setattr(s, "_alljobs", _alljobs)
    monkeypatch.setattr(s, "_portal_is_v2", lambda host: True)
    jobs = s.fetch_raw()

    assert len(jobs) == 1
    assert s.truncated and "job_counts=10" in s.truncated


def test_darwinbox_does_not_mark_a_board_whose_job_counts_matches(monkeypatch):
    """The healthy case: `job_counts` agrees with what the natural short-page end collected, so
    nothing fires — the other direction of ADR-0053 (a wrongly-marked Board is exempt from
    eviction indefinitely)."""
    from headstart.scrapers import darwinbox as db

    s = db.DarwinboxScraper("acme")

    def _alljobs(host, page):
        s._job_counts = 2
        return [{"id": 1}, {"id": 2}]

    monkeypatch.setattr(s, "_alljobs", _alljobs)
    monkeypatch.setattr(s, "_portal_is_v2", lambda host: True)
    jobs = s.fetch_raw()

    assert len(jobs) == 2
    assert s.truncated is None


def test_successfactors_does_not_mark_a_board_whose_pages_all_arrived(monkeypatch):
    """The other direction of ADR-0053: a Board wrongly marked truncated is exempt from
    eviction indefinitely, so its closed postings are served forever."""
    scraper = _successfactors_board(
        monkeypatch,
        slug="jobs.example.com",
        sitemap=("urlset", "", None),
        search=(
            [(f"https://jobs.example.com/job/x/{i}/", str(i)) for i in (1, 2)],
            None,
            None,
        ),
        rss=([], {}, None),
    )

    scraper.fetch_raw()

    assert scraper.truncated is None


def test_eightfold_sitemap_fallback_keeps_each_url_with_its_own_page():
    """`_job_urls` dedupes URLs, not position ids, so one position can be listed twice; each URL
    keeps the page it was read from rather than sharing whichever of the two arrived."""
    scraper, _fetcher = _eightfold_sitemap_board(
        readable_slugs=("7-engineer-pune",),
        listed_slugs=("7-engineer-pune", "7-engineer-remote"),
    )

    records = scraper._sitemap_records()

    assert {record["url"]: record["fields"] is None for record in records} == {
        _eightfold_job_page_url("7-engineer-pune"): False,
        _eightfold_job_page_url("7-engineer-remote"): True,
    }


def test_eightfold_sitemap_fallback_does_not_mark_a_complete_board():
    """Same negative direction on eightfold's fallback surface."""
    scraper, _fetcher = _eightfold_sitemap_board(readable_slugs=(1,), listed_slugs=(1,))

    records = scraper._sitemap_records()

    assert records[0]["fields"]["title"] == "Engineer"
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


def test_ripplehire_marks_its_page_cap_but_not_a_board_that_ended():
    """ADR-0053, both directions. ripplehire's natural exit is the tenant's own job count;
    exhausting the page cap means the board did not end, we stopped reading it."""
    from headstart.scrapers import ripplehire as rh

    def _board(total, per_page):
        # jobDesc is already set, so the detail pass has nothing to fetch
        page = [{"jobSeq": i, "jobDesc": "x"} for i in range(per_page)]
        fetcher = FakeFetcher(lambda method, url, kwargs: _RippleResp(page, total))
        return rh.RippleHireScraper("acme", fetcher=fetcher)

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


def test_oracle_pages_past_the_first_200():
    """The live shape: 299 across a full page and a short one. Both must arrive."""
    from headstart.scrapers.oracle import OracleScraper

    page_by_offset = {
        0: _oracle_page(_oracle_reqs(0, 200), 299),
        200: _oracle_page(_oracle_reqs(200, 99), 299),
    }
    seen: list[int] = []

    def route(method, url, kwargs):
        if "/recruitingCEJobRequisitions?" not in url:
            return FakeResponse(404)  # the Detail pass is not under test here
        offset = int(url.split("offset=")[1])
        seen.append(offset)
        return FakeResponse(text=page_by_offset[offset])

    s = OracleScraper("acme.fa.ocs.oraclecloud.com", "Acme", fetcher=FakeFetcher(route))
    jobs = s.parse(s.fetch_raw(), SCRAPED_AT)

    assert seen == [0, 200]  # the offset really advanced
    assert len(jobs) == 299
    assert len({j.id for j in jobs}) == 299


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


def _zwayam_answering(host: str, answer: FakeResponse | Exception):
    """A zwayam Scraper for ``host`` whose every request ``answer`` settles — returned, or raised
    when it is an exception."""
    from headstart.scrapers.zwayam import ZwayamScraper

    return ZwayamScraper(host, fetcher=FakeFetcher(lambda method, url, kwargs: answer))


def test_zwayam_unregistered_host_yields_no_jobs():
    """A hostname that is not a Board answers 200 with data: null — not an error, and not jobs."""
    null_body = FakeResponse(text=json.dumps({"code": 200, "data": None}))
    scraper = _zwayam_answering("careers.not-a-board.example", null_body)
    assert scraper.parse(scraper.fetch_raw(), SCRAPED_AT) == []


def test_zwayam_link_base_tells_the_three_frontend_generations_apart():
    """One API, three careers frontends, three job routes (live-classified across all 224 hiring
    Boards): Angular's `<base href>` + `jobview/`, Next.js's root `/job-view/` (where `jobview`
    hard-404s, 10/10 Boards), and the old Angular 1 shell's hash route `/#!/job-view/`."""
    cases = [
        ('<base href="/tavant/"><app-root>', "https://h.example/tavant/jobview/"),
        ('<script src="/_next/static/x.js">', "https://h.example/job-view/"),
        ('<div ng-view="" id="ng-view">', "https://h.example/#!/job-view/"),
    ]
    for homepage_html, expected in cases:
        homepage = FakeResponse(text=homepage_html)
        assert _zwayam_answering("h.example", homepage)._link_base() == expected


def test_zwayam_unreadable_homepage_falls_back_on_the_hostname_prior():
    """When the homepage GET fails the shape comes from the measured prior: `openings.co` hosts
    are the Next generation 102:12, custom domains Angular 92:0. A wrong guess costs a dead link,
    not a lost Job — so the Board must still return its rows."""
    refused = OSError("refused")
    assert (
        _zwayam_answering("x.openings.co", refused)._link_base()
        == "https://x.openings.co/job-view/"
    )
    assert (
        _zwayam_answering("careers.x.com", refused)._link_base()
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


def test_zwayam_absolute_base_href_does_not_corrupt_the_link():
    """An absolute <base href> is legal HTML; pasting it onto the Board host would build
    https://host/https://cdn.../jobview/… — unresolvable."""
    html = '<html><base href="https://cdn.example.com/x/"><app-root></app-root></html>'
    scraper = _zwayam_answering("careers.abs.example", FakeResponse(text=html))
    assert scraper._link_base() == "https://careers.abs.example/jobview/"


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

    _salary = get_scraper("zwayam", "acme")._salary_field

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

    _salary = get_scraper("zwayam", "acme")._salary_field

    assert _salary({"minJobSalary": "1000000", "maxJobSalary": "0"}) == "1000000 INR"
    assert extract("1000000 INR", None, "zwayam").min_annual == 1000000
    assert _salary({"minJobSalary": "0", "maxJobSalary": "0"}) is None


def test_zwayam_a_ceiling_without_a_floor_is_shown_but_never_read_as_a_floor():
    """A lone figure parses as a *floor*, so a bare ceiling would serve a job capped at 200k as
    one paying at least that (10 of 5,079 amount rows). "Upto" keeps the display column honest —
    `Job.salary` is "raw, for display" — while parsing to nothing, so no derived column inverts.
    """
    from headstart.salary import extract

    _salary = get_scraper("zwayam", "acme")._salary_field

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


def test_zwayam_a_body_error_code_raises_rather_than_reading_as_an_empty_board():
    """The endpoint reports its own failures as HTTP 200 with body `code: 500` and `data: null`
    (measured) — byte-identical to a dead Board except for the code. Reading it as "no jobs"
    marks every posting Unconfirmed, and a second one evicts them all (ADR-0083)."""
    error_body = FakeResponse(
        text=json.dumps({"code": 500, "data": None, "message": "Internal Server Error"})
    )
    scraper = _zwayam_answering("careers.err.example", error_body)
    with pytest.raises(RuntimeError, match="body code 500"):
        scraper.fetch_raw()


def _zwayam_served_board(rows, detail_for, config=None):
    """A zwayam Board on ``h.example`` whose whole conversation a FakeFetcher answers: the search
    lists ``rows``, the config call answers ``config`` (company 4242 by default), each detail
    POST answers ``detail_for(job_url)``, and the homepage declares an Angular ``<base href>``."""
    from headstart.scrapers import zwayam as zwayam_module

    page = {
        "code": 200,
        "data": {
            "totalCount": len(rows),
            "hasMoreData": False,
            "data": [{"_source": row} for row in rows],
        },
    }
    config = config or FakeResponse(
        text=json.dumps({"responseObject": {"company": {"id": 4242}}})
    )

    def route(method, url, kwargs):
        if url == zwayam_module._API:
            return FakeResponse(text=json.dumps(page))
        if url == zwayam_module._CONFIG_API:
            return config
        if url == zwayam_module._DETAIL_API:
            return detail_for(kwargs["json"]["jobUrl"])
        return FakeResponse(text='<base href="/">')

    fetcher = FakeFetcher(route)
    return zwayam_module.ZwayamScraper("h.example", fetcher=fetcher), fetcher


def _zwayam_detail_response(job_url: str) -> FakeResponse:
    return FakeResponse(
        text=json.dumps({"longDescription": f"<p>detail text for {job_url}</p>"})
    )


def _zwayam_detail_bodies(fetcher: FakeFetcher) -> list[dict]:
    from headstart.scrapers import zwayam as zwayam_module

    return sorted(
        (
            request.kwargs["json"]
            for request in fetcher.requests
            if request.url == zwayam_module._DETAIL_API
        ),
        key=lambda body: body["jobUrl"],
    )


def test_zwayam_detail_response_wins_and_the_skip_list_prunes_the_fetch():
    """The listing's text can be silently truncated with no way to tell (632 chars listed vs
    909 of stripped detail text, measured), so the detail is fetched for every row not on the
    ADR-0050 skip-list and its text wins over whatever the listing carried.

    The titles are real tech ones because setting ``have_details`` also arms the ADR-0017 tech
    gate, and a fixture titled "A"/"B"/"C" would be gated out before the skip-list this test is
    about ever ran — the test would then pass for the wrong reason."""
    scraper, fetcher = _zwayam_served_board(
        [
            {"id": 1, "jobTitle": "Backend Engineer", "jobUrl": "a"},
            {
                "id": 2,
                "jobTitle": "Data Engineer",
                "jobUrl": "b",
                "mediumDescriptionWithoutHtml": "possibly truncated listing",
            },
            {
                "id": 3,
                "jobTitle": "QA Engineer",
                "jobUrl": "c",
                # The row MUST carry listing text: without it this test passes whether
                # or not a skip-listed row falls through to the listing, which is the
                # exact regression it exists to catch.
                "mediumDescriptionWithoutHtml": "possibly truncated listing",
            },
        ],
        _zwayam_detail_response,
    )
    # id 3 is on the skip-list: the store already holds its (detail-derived) text, so the row
    # must ship None and let the store supply it. Shipping the listing text instead would be
    # worse than a no-op — `update_descriptions` treats fresh corpus text as authoritative
    # ("Fresh text always wins"), so run 2 would *overwrite* the stored full text with the
    # truncated listing, and every later run would re-confirm it.
    scraper.have_details = {"zwayam:h.example:3"}
    jobs = scraper.parse(scraper.fetch_raw(), SCRAPED_AT)
    assert _zwayam_detail_bodies(fetcher) == [
        {"jobUrl": "a", "companyId": 4242},
        {"jobUrl": "b", "companyId": 4242},
    ]
    by_id = {j.id.rsplit(":", 1)[1]: j.description for j in jobs}
    assert by_id == {"1": "detail text for a", "2": "detail text for b", "3": None}


#: A Board of two rows — one carrying listing text, one carrying none.
_ZWAYAM_TWO_ROWS = [
    {
        "id": 1,
        "jobTitle": "A",
        "jobUrl": "a",
        "mediumDescriptionWithoutHtml": "possibly truncated listing",
    },
    {"id": 2, "jobTitle": "B", "jobUrl": "b"},
]


def test_zwayam_a_failed_detail_ships_nothing_so_the_next_run_retries():
    """A transient failure must NOT fall back to the listing text: the store persists whatever
    the scrape emits and membership in it is the skip-list, so one bad fetch would freeze
    possibly-truncated text forever. Emitting nothing leaves `needs_detail` true."""
    scraper, _fetcher = _zwayam_served_board(
        _ZWAYAM_TWO_ROWS, lambda job_url: OSError("detail refused")
    )
    jobs = scraper.parse(scraper.fetch_raw(), SCRAPED_AT)
    assert {j.id.rsplit(":", 1)[1]: j.description for j in jobs} == {
        "1": None,
        "2": None,
    }
    assert scraper.detail_losses == {"OSError": 2}


def test_zwayam_a_failed_config_call_ships_no_descriptions_not_stale_ones():
    """The config call is per-Board, so its failure fails every detail on the Board — and must
    behave like any other failed detail rather than freezing the whole Board's listing text.
    Each loss is named, and none of them is a request made."""
    scraper, fetcher = _zwayam_served_board(
        _ZWAYAM_TWO_ROWS, _zwayam_detail_response, config=FakeResponse(500, "down")
    )
    jobs = scraper.parse(scraper.fetch_raw(), SCRAPED_AT)
    assert len(jobs) == 2  # the Jobs themselves still ship
    assert {j.description for j in jobs} == {None}
    assert _zwayam_detail_bodies(fetcher) == []
    assert scraper.detail_losses == {"no company id": 2}
    assert scraper.telemetry["detail_attempted"] == 0


def test_zwayam_a_detail_that_answers_empty_keeps_the_listing_text():
    """An answered-but-bodyless detail is the posting's final word, so the listing text is the
    best that will ever exist for it — kept, unlike the failed-fetch case above."""
    scraper, _fetcher = _zwayam_served_board(
        _ZWAYAM_TWO_ROWS, lambda job_url: FakeResponse(text="{}")
    )
    jobs = scraper.parse(scraper.fetch_raw(), SCRAPED_AT)
    assert {j.id.rsplit(":", 1)[1]: j.description for j in jobs} == {
        "1": "possibly truncated listing",
        "2": None,
    }
    assert scraper.telemetry["detail_losses"] == 0


def test_zwayam_asks_for_the_company_id_once_and_only_when_a_detail_is_wanted(
    monkeypatch,
):
    """The config call is metered like every other request here, so it is made once per Board
    however many workers form requests at once — and not at all on a Board whose every row is
    already held. The config answer is slowed so the thread pool's workers really do overlap."""
    import time

    from headstart.scrapers import zwayam as zwayam_module

    monkeypatch.delenv("HEADSTART_ASYNC_FANOUT", raising=False)
    monkeypatch.setattr(
        zwayam_module.ZwayamScraper,
        "fan_out_async",
        lambda *args, **kwargs: pytest.fail("the multiplexed path was taken"),
    )
    rows = [
        {"id": index, "jobTitle": "Backend Engineer", "jobUrl": f"job-{index}"}
        for index in range(40)
    ]

    class _SlowConfig(FakeResponse):
        def json(self):
            time.sleep(0.05)
            return super().json()

    slow_config = _SlowConfig(
        text=json.dumps({"responseObject": {"company": {"id": 4242}}})
    )
    scraper, fetcher = _zwayam_served_board(
        rows, _zwayam_detail_response, config=slow_config
    )
    scraper.fetch_raw()
    assert fetcher.urls().count(zwayam_module._CONFIG_API) == 1
    assert len(_zwayam_detail_bodies(fetcher)) == 40

    held, held_fetcher = _zwayam_served_board(rows, _zwayam_detail_response)
    held.have_details = {f"zwayam:h.example:{index}" for index in range(40)}
    held.fetch_raw()
    assert zwayam_module._CONFIG_API not in held_fetcher.urls()


def test_zwayam_detail_request_carries_the_browser_agent_the_edge_demands():
    """`jobs-service` 403s the shared bare agent (module docstring, 0/10 vs 10/10 measured), so
    the detail POST — and only it — carries `_DETAIL_USER_AGENT`."""
    from headstart.scrapers import zwayam as zwayam_module

    scraper, fetcher = _zwayam_served_board(_ZWAYAM_TWO_ROWS, _zwayam_detail_response)
    scraper.fetch_raw()
    detail_agents = {
        request.kwargs["headers"]["User-Agent"]
        for request in fetcher.requests
        if request.url == zwayam_module._DETAIL_API
    }
    other_agents = {
        request.kwargs["headers"]["User-Agent"]
        for request in fetcher.requests
        if request.url != zwayam_module._DETAIL_API
    }
    assert detail_agents == {zwayam_module._DETAIL_USER_AGENT}
    assert other_agents == {zwayam_module.USER_AGENT}  # search, config and homepage


def test_workday_detail_404_falls_back_to_the_public_page(monkeypatch):
    """A whole sub-site can list live postings whose CXS details all 404 — measured on
    iheartmedia: 12/12 pipeline runs at 100% detail loss (2,900+ details), reproduced from a
    residential IP, while every public job page returned 200 with the full description in
    server-rendered JSON-LD and `postedOn: Posted Yesterday`. The page's own embedded config
    names the exact tenant/site the scraper uses, so the tenant's own SPA would 404 on the same
    URL — the CXS detail simply does not exist for these sites, and the public page is the only
    source (ADR-0099). A settled 404 therefore tries the page before counting the loss."""
    import asyncio
    from collections import Counter

    from headstart import http
    from headstart.scrapers.workday import _PAGE_RECOVERED, WorkdayScraper

    page_html = (
        '<html><head><script type="application/ld+json">'
        '{"@type": "JobPosting", "title": "Payroll Specialist",'
        ' "description": "Payroll &amp; tax operations role"}'
        "</script></head><body></body></html>"
    )
    urls: list[str] = []

    def _respond(url):
        urls.append(url)

        class _R:
            status_code = 404 if "/wday/cxs/" in url else 200
            text = page_html

            @staticmethod
            def json():
                raise AssertionError("a 404 body must not be parsed")

        return _R()

    monkeypatch.setattr(http, "fetch", lambda method, url, **kw: _respond(url))

    async def fake_fetch_async(session, method, url, **kw):
        return _respond(url)

    monkeypatch.setattr(http, "fetch_async", fake_fetch_async)

    s = WorkdayScraper("https://acme.wd1.myworkdayjobs.com/sub_site")
    s._instance = "wd1"
    for detail in (
        s._job_detail("/job/x/Payroll-Specialist_R1", (classes := Counter())),
        asyncio.run(
            s._job_detail_async(
                object(), "/job/x/Payroll-Specialist_R1", (classes := Counter())
            )
        ),
    ):
        assert detail is not None, "the page fallback must save the detail"
        assert detail["description"] == "Payroll &amp; tax operations role"
        assert (
            detail["startDate"] is None
        )  # absent from this page's JSON-LD — stays None
        assert classes[_PAGE_RECOVERED] == 1
        assert "HTTP 404" not in classes, "a recovered detail is not a loss"
    assert (
        "https://acme.wd1.myworkdayjobs.com/sub_site/job/x/Payroll-Specialist_R1"
        in urls
    ), "the fallback must hit the public page, not the CXS API again"


def test_workday_detail_404_with_a_dead_page_still_counts_the_loss(monkeypatch):
    """When the public page 404s too (a genuinely delisted posting), the detail is lost
    exactly as before this fallback existed: None, counted under HTTP 404."""
    from collections import Counter

    from headstart import http
    from headstart.scrapers.workday import WorkdayScraper

    class _R:
        status_code = 404
        text = "not found"

        @staticmethod
        def json():
            raise AssertionError("a 404 body must not be parsed")

    monkeypatch.setattr(http, "fetch", lambda method, url, **kw: _R())
    s = WorkdayScraper("https://acme.wd1.myworkdayjobs.com/sub_site")
    s._instance = "wd1"
    classes: Counter = Counter()
    assert s._job_detail("/job/x/Gone_R2", classes) is None
    assert classes["HTTP 404"] == 1


def test_workday_detail_400_does_not_touch_the_public_page(monkeypatch):
    """400 is a throttle (ADR-0098) and already retried; adding a page fetch on top would
    hand a throttled Board extra load at the worst moment. Only a settled 404 — permanent by
    meaning — earns the second request."""
    from collections import Counter

    from headstart import http
    from headstart.scrapers.workday import WorkdayScraper

    urls: list[str] = []

    class _R:
        status_code = 400
        text = ""

        @staticmethod
        def json():
            return {}

    monkeypatch.setattr(
        http, "fetch", lambda method, url, **kw: (urls.append(url), _R())[1]
    )
    s = WorkdayScraper("https://acme.wd1.myworkdayjobs.com/sub_site")
    s._instance = "wd1"
    classes: Counter = Counter()
    assert s._job_detail("/job/x/Throttled_R3", classes) is None
    assert classes["HTTP 400"] == 1
    assert all("/wday/cxs/" in u for u in urls), "a 400 must never reach the page"


def test_workday_recovered_details_report_once_and_stay_out_of_the_loss_tally(caplog):
    """`_report_detail_losses`'s invariant is that every class label rides a path that also
    yields None, so the tally can only fall short of `missing`. A recovered detail is non-None,
    so its label must be popped out before the tally — and still surface, once per Board."""
    import logging
    from collections import Counter

    from headstart.scrapers.workday import _PAGE_RECOVERED, WorkdayScraper

    s = WorkdayScraper("https://acme.wd1.myworkdayjobs.com/sub_site")
    s._instance = "wd1"
    with caplog.at_level(logging.INFO):
        s._report_detail_losses(
            [{"description": "x"}, {"description": "y"}],
            Counter({_PAGE_RECOVERED: 2}),
            0,
        )
    assert "2 detail(s) recovered from the public page" in caplog.text
    assert "failed mid-crawl" not in caplog.text


def test_workday_extract_page_detail_reads_both_type_shapes_and_the_exact_fields():
    """JSON-LD allows ``@type`` to be a string or a list; Workday's own template serves the
    string form (measured n=2 tenants), so the full-path test uses that shape and this one
    keeps the list form and the not-a-JobPosting rejection covered. The three ride-along
    fields carry exact currency (ADR-0099): datePosted IS startDate's ISO form, TELECOMMUTE
    already maps through `_remote_from`, and schema.org's enum maps onto timeType's wording."""
    from headstart.scrapers.workday import _extract_page_detail, _remote_from

    def _page(block):
        class _R:
            status_code = 200
            text = f'<script type="application/ld+json">{block}</script>'

        return _R()

    detail = _extract_page_detail(
        _page(
            '{"@type": ["JobPosting"], "description": "d", "datePosted": "2026-08-31",'
            ' "employmentType": "FULL_TIME", "jobLocationType": "TELECOMMUTE"}'
        )
    )
    assert detail == {
        "description": "d",
        "startDate": "2026-08-31",
        "remoteType": "TELECOMMUTE",
        "timeType": "Full time",
    }
    assert _remote_from(detail["remoteType"]) is True
    # a page whose JobPosting has no description recovers nothing — the gate field
    assert (
        _extract_page_detail(
            _page('{"@type": "JobPosting", "datePosted": "2026-08-31"}')
        )
        is None
    )
    assert (
        _extract_page_detail(_page('{"@type": "Organization", "description": "d"}'))
        is None
    )


def _breaker_scraper():
    from headstart.scrapers.workday import WorkdayScraper

    s = WorkdayScraper("https://acme.wd1.myworkdayjobs.com/careers")
    s._instance = "wd1"
    return s


def test_workday_detail_pass_breaks_off_after_consecutive_settled_5xx(
    monkeypatch, caplog
):
    """An origin-side 500-episode is minutes long, IP-independent, and refuses everything —
    measured on massgeneralbrigham (run 33448251066): 2,324 of 2,420 details settled at 500
    across 10+ fresh egress IPs over a 1,153s pass. Nothing recovers in-run; the store heals
    next scrape. So after `_DETAIL_BREAK_STREAK` consecutive settled 5xx the pass stops paying:
    no further detail is fetched, the tail is counted under its own label, and one warning
    names the break-off (ADR-0100)."""
    import logging
    from collections import Counter

    from headstart import http
    from headstart.scrapers.workday import _BROKEN_OFF, _DETAIL_BREAK_STREAK

    calls = []

    class _R:
        status_code = 500
        text = ""

        @staticmethod
        def json():
            return {}

    monkeypatch.setattr(
        http, "fetch", lambda method, url, **kw: (calls.append(url), _R())[1]
    )
    s = _breaker_scraper()
    classes: Counter = Counter()
    # INFO: the break-off fires once per Board, and WARNING is a run-level annotation quota
    # under Actions (ADR-0039's 2026-09-08 amendment; tests/test_log_levels.py pins it).
    caplog.set_level(logging.INFO, logger="headstart.scrapers.workday")
    for i in range(_DETAIL_BREAK_STREAK + 10):
        assert s._job_detail(f"/job/x/J_{i}", classes) is None
    assert len(calls) == _DETAIL_BREAK_STREAK, "fetching must stop at the streak"
    assert classes["HTTP 500"] == _DETAIL_BREAK_STREAK
    assert classes[_BROKEN_OFF] == 10
    s._report_detail_losses(
        [None] * (_DETAIL_BREAK_STREAK + 10), classes, titled_stubs=0
    )
    assert s.telemetry["detail_attempted"] == _DETAIL_BREAK_STREAK
    assert s.telemetry["detail_http_failures"] == _DETAIL_BREAK_STREAK
    assert s.telemetry["detail_breaker_skips"] == 10
    breaks = [r for r in caplog.records if "breaking off the detail pass" in r.message]
    assert len(breaks) == 1, "the break-off is logged exactly once"


def test_workday_a_recovered_detail_resets_the_5xx_streak(monkeypatch):
    """A streak is *consecutive*: one successful detail proves the origin is answering, so the
    counter starts over — a board with interleaved successes is a lossy pass, not an episode."""
    from collections import Counter

    from headstart import http
    from headstart.scrapers.workday import _DETAIL_BREAK_STREAK

    n = {"i": 0}

    def fake_fetch(method, url, **kw):
        n["i"] += 1
        ok = n["i"] == _DETAIL_BREAK_STREAK  # one success right before the threshold

        class _R:
            status_code = 200 if ok else 500
            text = ""

            @staticmethod
            def json():
                return {"jobPostingInfo": {"jobDescription": "<p>d</p>"}}

        return _R()

    monkeypatch.setattr(http, "fetch", fake_fetch)
    s = _breaker_scraper()
    classes: Counter = Counter()
    for i in range(2 * _DETAIL_BREAK_STREAK - 2):
        s._job_detail(f"/job/x/J_{i}", classes)
    assert n["i"] == 2 * _DETAIL_BREAK_STREAK - 2, (
        "no break: every detail was attempted"
    )


def test_workday_400s_do_not_trip_the_5xx_breaker(monkeypatch):
    """A 400-storm is a stale session cookie (ADR-0103), recovered in-pass by clearing the jar —
    not the wholesale 5xx refusal the breaker is for. The breaker counts settled 5xx only, so a
    pure 400 storm never trips it, and the pass keeps attempting every detail."""
    from collections import Counter

    from headstart import http
    from headstart.scrapers.workday import _DETAIL_BREAK_STREAK

    calls = []

    class _R:
        status_code = (
            400  # persists through the cookie reset — a genuine loss, not a recovery
        )
        text = ""

        @staticmethod
        def json():
            return {}

    monkeypatch.setattr(
        http, "fetch", lambda method, url, **kw: (calls.append(url), _R())[1]
    )
    s = _breaker_scraper()
    classes: Counter = Counter()
    for i in range(_DETAIL_BREAK_STREAK + 5):
        s._job_detail(f"/job/x/J_{i}", classes)
    assert not s._detail_pass_broken, "400s must not trip the 5xx breaker"
    # Each detail is attempted once, then refetched once after the cookie reset (ADR-0103): a 400
    # that persists is a real loss, but the pass runs to the end rather than breaking off.
    assert len(calls) == 2 * (_DETAIL_BREAK_STREAK + 5), (
        "every detail attempted, plus its reset"
    )
    assert classes["HTTP 400"] == _DETAIL_BREAK_STREAK + 5


def test_workday_detail_break_off_applies_to_the_async_path_too(monkeypatch):
    """One episode, one board, either fan-out — the async path shares the same streak."""
    import asyncio
    from collections import Counter

    from headstart import http
    from headstart.scrapers.workday import _BROKEN_OFF, _DETAIL_BREAK_STREAK

    calls = []

    async def fake_fetch_async(session, method, url, **kw):
        calls.append(url)

        class _R:
            status_code = 500
            text = ""

            @staticmethod
            def json():
                return {}

        return _R()

    monkeypatch.setattr(http, "fetch_async", fake_fetch_async)
    s = _breaker_scraper()
    classes: Counter = Counter()

    async def drive():
        for i in range(_DETAIL_BREAK_STREAK + 4):
            await s._job_detail_async(object(), f"/job/x/J_{i}", classes)

    asyncio.run(drive())
    assert len(calls) == _DETAIL_BREAK_STREAK
    assert classes[_BROKEN_OFF] == 4


# ── the Board's company name, not its slug (headstart.company_name) ──────────────────


def _titled(title: str, status: int = 200):
    """A board-page response carrying one ``<title>`` — the only thing `resolve_company` reads.

    Local to these tests rather than a fixture: they differ only in the title and the status,
    and hand-rolling that same pair of fields per test is what a reviewer flagged.
    """
    return SimpleNamespace(status_code=status, text=f"<title>{title}</title>")


#: One row per ATS in `company_name.PATTERNS`. Named so both the resolve test and the
#: binding test below can read it directly, rather than reaching into pytest's own marker
#: internals to recover what was parametrised.
_RESOLVE_ROWS = [
    (
        "ashby",
        "1password",
        "1Password Jobs",
        "1Password",
        "https://jobs.ashbyhq.com/1password",
    ),
    (
        "eightfold",
        "jobs.vodafone.com",
        "Careers at Vodafone",
        "Vodafone",
        "https://jobs.vodafone.com/careers",
    ),
    (
        "jobvite",
        "barracuda-networks-inc",
        "Barracuda Networks Inc. Careers",
        "Barracuda Networks Inc.",
        "https://jobs.jobvite.com/barracuda-networks-inc/search",
    ),
    (
        "keka",
        "skylarkdrones",
        "Careers at Skylark Drones",
        "Skylark Drones",
        "https://skylarkdrones.keka.com/careers",
    ),
    (
        # The `| Zelis Jobs` tail is the point: `_CAREERS_WRAPPER` would take the whole thing.
        # The URL carries the probe prefix, which is what a scraper built but never fetched holds.
        "phenom",
        "careers.zelis.com",
        "Careers at Zelis | Zelis Jobs",
        "Zelis",
        "https://careers.zelis.com/us/en",
    ),
    (
        # Every board titles itself "Jobs at {Name} | {Name} Careers" (40 of 40 sampled).
        "pinpoint",
        "jed",
        "Jobs at The Jed Foundation | The Jed Foundation Careers",
        "The Jed Foundation",
        "https://jed.pinpointhq.com/",
    ),
    (
        "lever",
        "picklerobot",
        "Pickle Robot Company",
        "Pickle Robot Company",
        "https://jobs.lever.co/picklerobot",
    ),
    (
        # The title is the bare name — equal to the SSR payload's `companyDetails.name` on 757
        # of 757 live tenants — and here genuinely different from the slug, not a re-casing.
        "pyjamahr",
        "8byte",
        "Octa Byte AI Pvt Ltd",
        "Octa Byte AI Pvt Ltd",
        "https://jobs.pyjamahr.com/8byte",
    ),
    (
        "ripplehire",
        "tatasteel",
        "Tata Steel Ltd Careers | Latest jobs at Tata Steel Ltd",
        "Tata Steel Ltd",
        "https://tatasteel.ripplehire.com/candidate/careers",
    ),
    (
        "gem",
        "accel",
        "Accel Careers",
        "Accel",
        "https://jobs.gem.com/accel",
    ),
    (
        # The client host's `/jobs` page; 200 of 1,116 clients title it "{Name} Careers".
        "jibe",
        "rmeducation",
        "RM Education Limited Careers",
        "RM Education Limited",
        "https://rmeducation.jibeapply.com/jobs",
    ),
]


@pytest.mark.parametrize(("ats", "slug", "title", "expected", "url"), _RESOLVE_ROWS)
def test_every_wired_scraper_resolves_its_company(
    monkeypatch, ats, slug, title, expected, url
):
    """One row per ATS in `company_name.PATTERNS`; the test below enforces that count.

    Ashby and eightfold were the only two pinned for several rounds, and a stray rename of
    `RippleHireScraper.board_page` then reached the branch and turned ripplehire resolution off
    with the whole suite green. The URL is asserted too.
    """
    from headstart import http

    seen: list[str] = []

    def _fetch(method, fetched, **kwargs):
        seen.append(fetched)
        return _titled(title)

    monkeypatch.setattr(http, "fetch", _fetch)
    scraper = get_scraper(ats, slug, slug)
    scraper.resolve_company()
    assert scraper.company == expected
    # jibe reads the host's robots.txt before any other request to it (ADR-0189); the fake
    # answers that with the same page, whose absent rules allow everything.
    if ats == "jibe":
        assert seen[0].endswith("/robots.txt")
        seen = seen[1:]
    assert seen == [url]


#: taleo_enterprise deliberately has no `board_page` override and no `_RESOLVE_ROWS` row.
#: `resolve_company`'s generic mechanism reads one `<title>` from one dedicated request, but
#: this ATS needs the *last* of two `<title>` tags on a shell it already fetched for its
#: listing pass (`TaleoEnterpriseScraper._last_title`) — so `_company` reads that shell
#: directly instead, covered by `tests/test_taleo_enterprise.py`. It still needs the same
#: vendor-alias coverage as every other wired ATS.
#: adp (ADP Workforce Now) has no page naming the employer either — its title is the literal
#: "Recruitment" — so `ADPScraper.resolve_company` reads `ClientName` out of the
#: `client-features` JSON instead, covered by `tests/test_adp.py`. adp_recruiting (ADP
#: Recruiting Management, a separate product) reads `clientName` off the site record it already
#: fetched for its token, covered by `tests/test_adp_recruiting.py`.
_NO_BOARD_PAGE = {"taleo_enterprise", "adp", "adp_recruiting"}


def test_every_ats_with_patterns_has_a_scraper_that_offers_a_board_page():
    """Binds `company_name.PATTERNS` to the scrapers that override `board_page`.

    Without this, adding a seventh ATS to one side and not the other is silent, and the test above
    keeps the name "every" while covering less than every.
    """
    from headstart.company_name import _VENDOR_ALIASES, PATTERNS
    from headstart.scrapers.base import BaseScraper
    from headstart.scrapers.registry import SCRAPERS

    overriding = {
        ats
        for ats, cls in SCRAPERS.items()
        if cls.board_page is not BaseScraper.board_page
    }
    assert overriding == set(PATTERNS) - _NO_BOARD_PAGE, (
        "an ATS has a board_page but no patterns, or patterns but no board_page"
    )
    covered = {row[0] for row in _RESOLVE_ROWS}
    assert covered == set(PATTERNS) - _NO_BOARD_PAGE, (
        "every wired ATS needs a row in the resolve test"
    )
    assert set(_VENDOR_ALIASES) == set(PATTERNS), (
        "every wired ATS needs a vendor-alias entry, or its board page can serve the platform's "
        "own branding as the employer"
    )


def test_the_title_fetch_is_one_attempt_and_never_walls_its_ats(monkeypatch):
    """ADR-0114 sells both of these as why one extra request per Board is safe, and deleting
    either left the whole suite green.

    `attempts=1`: a display name is the most optional thing a scrape fetches, so it must not
    spend the retry ladder — three attempts against a walled origin is ~90s for one Board.
    `marks_wall=False`: `egress_on` empties, so this request's own non-200 can never be what
    routes every other Board of the ATS onto the spare egress — while `egress_group` stays, so it
    still *rides* the fallback once the ATS is walled (ADR-0063).
    """
    from headstart import http

    captured: dict = {}

    def _fetch(method, url, **kwargs):
        captured.update(kwargs)
        return _titled("Careers at Vodafone")

    monkeypatch.setattr(http, "fetch", _fetch)
    scraper = get_scraper("eightfold", "jobs.vodafone.com", "jobs.vodafone.com")
    assert scraper.egress_fallback_on, "this ATS must opt in for the test to bite"
    scraper.resolve_company()
    assert captured["attempts"] == 1
    assert captured["egress_on"] == frozenset(), "marking must be dropped"
    assert captured["egress_group"] == "eightfold", "routing must be kept"


def test_resolve_company_costs_nothing_for_an_ats_without_a_board_page(monkeypatch):
    """Every ATS with no measured title shape keeps its slug AND makes no extra request —
    the whole change is inert for them."""
    from headstart import http
    from headstart.scrapers.greenhouse import GreenhouseScraper

    # Records rather than raises. `resolve_company` catches every exception, so a raising stub
    # has its AssertionError swallowed and the test can never fail — which is how a renamed
    # `board_page` shipped to the branch with nothing red.
    calls: list[str] = []

    monkeypatch.setattr(http, "fetch", lambda method, url, **k: calls.append(url))
    scraper = GreenhouseScraper("acme")
    scraper.resolve_company()
    assert scraper.company == "acme"
    assert calls == [], "a scraper with no board_page must not fetch one"


def test_a_name_from_the_ledger_outranks_the_board_title(monkeypatch):
    """A Board whose ledger row already names the company is left alone, and not even fetched:
    the curated name is better evidence than a page title."""
    from headstart import http
    from headstart.scrapers.ashby import AshbyScraper

    calls: list[str] = []

    monkeypatch.setattr(http, "fetch", lambda method, url, **k: calls.append(url))
    scraper = AshbyScraper("1password", company="1Password, Inc.")
    scraper.resolve_company()
    assert scraper.company == "1Password, Inc."
    assert calls == [], "a Board that already has a name must not fetch a title"


def test_a_failed_title_fetch_leaves_the_company_untouched(monkeypatch):
    """A display name is never worth failing a Board for, so every error path degrades to today's
    behaviour rather than raising out of `fetch`."""
    from headstart import http
    from headstart.scrapers.lever import LeverScraper

    def _raise(*a, **k):
        raise http.RequestsError("boom")

    monkeypatch.setattr(http, "fetch", _raise)
    scraper = LeverScraper("acme")
    scraper.resolve_company()
    assert scraper.company == "acme"


def test_a_non_200_board_page_leaves_the_company_untouched(monkeypatch):
    from headstart import http
    from headstart.scrapers.lever import LeverScraper

    monkeypatch.setattr(http, "fetch", lambda *a, **k: _titled("Not Found", status=404))
    scraper = LeverScraper("acme")
    scraper.resolve_company()
    assert scraper.company == "acme"


def test_the_title_fetch_does_not_go_through_the_get_override(monkeypatch):
    """`_get` does not mean the same thing in every scraper — eightfold's override returns the
    `Response` where the base returns `.text` — so `resolve_company` uses the shared fetch seam
    directly. Routing it through `_get` fed a `Response` to the title parser and broke every
    eightfold Board; the suite passed, and only a live end-to-end run caught it."""
    from headstart import http
    from headstart.scrapers.eightfold import EightfoldScraper

    monkeypatch.setattr(http, "fetch", lambda *a, **k: _titled("Careers at Vodafone"))
    scraper = EightfoldScraper("jobs.vodafone.com")
    scraper.resolve_company()
    assert scraper.company == "Vodafone"


def test_fetch_resolves_the_company_before_parsing(monkeypatch):
    """The wiring, not the helper. Every other test here calls `resolve_company` directly, so
    they all stayed green when the call was deleted from `fetch` — the served Jobs would have
    carried the slug again with nothing red. This drives `fetch` end to end instead.
    """
    from headstart import http
    from headstart.scrapers.ashby import AshbyScraper

    monkeypatch.setattr(http, "fetch", lambda *a, **k: _titled("1Password Jobs"))
    scraper = AshbyScraper("1password")
    monkeypatch.setattr(scraper, "fetch_raw", lambda: {"jobs": []})
    captured: dict = {}

    def _parse(raw, scraped_at):
        captured["company"] = scraper.company
        return []

    monkeypatch.setattr(scraper, "parse", _parse)
    scraper.fetch()
    assert captured["company"] == "1Password", (
        "fetch() must resolve the name before parse()"
    )


@pytest.mark.parametrize(
    ("ats", "container"),
    [("ashby", "jobs"), ("recruitee", "offers"), ("workable", "jobs")],
)
def test_a_payload_with_no_postings_container_says_the_board_was_unread(
    ats, container, caplog
):
    """Zero postings from a board with nothing open and zero from a payload nobody could read
    are the same number downstream — `index sync` reads both as delistings — and these three
    scrapers said nothing at all about which had happened.

    The *empty* container stays silent on purpose: a Live row at `jobs=0` is routine at this
    scale, so a line for it would be noise, and `note_unreadable_board` is INFO rather than
    WARNING for the same reason (ADR-0039's annotation budget). Neither exit marks the Board
    truncated — what a container-less payload means on these APIs has not been measured, and
    ADR-0053's exclusion has no drain.
    """
    scraper = get_scraper(ats, "acme", "Acme")
    caplog.set_level(logging.INFO, logger=f"headstart.scrapers.{ats}")

    assert scraper.parse({}, SCRAPED_AT) == []
    assert "read no jobs — expected a payload with" in caplog.text
    assert scraper.truncated is None

    caplog.clear()
    assert scraper.parse({container: []}, SCRAPED_AT) == []
    assert caplog.text == ""


# --- the ADR-0017 pre-detail tech gate (issue #500) -------------------------------------------
def test_workday_gates_details_and_pairs_them_back_by_the_right_posting(monkeypatch):
    """The ADR-0048 alignment trap, at a second call site.

    The fan-out now covers a subset of the listing, so zipping its results against the full
    posting list would hang each description on the wrong Job. A gated posting must come back
    with an empty ``_detail`` — it is still a Job, it just has no description, and the Board's
    list stays whole so no truncation denominator moves."""
    # The threaded path, so `_job_detail` below is the seam that gets patched — the async
    # default would call `_job_detail_async` and put a real request on the wire.
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "0")
    scraper = _workday_scraper()
    scraper.have_details = frozenset()
    postings = [
        {"title": "Housekeeper", "externalPath": "/job/hk", "bulletFields": ["R1"]},
        {
            "title": "Backend Engineer",
            "externalPath": "/job/be",
            "bulletFields": ["R2"],
        },
        {"title": "Chef", "externalPath": "/job/chef", "bulletFields": ["R3"]},
    ]
    scraper._exhaust = lambda facets, absorb, depth: absorb(postings)
    fetched: list[str] = []

    def _detail(path, classes):
        fetched.append(path)
        return {"description": f"body for {path}", "startDate": "2026-09-17"}

    scraper._job_detail = _detail
    raw = scraper.fetch_raw()

    assert fetched == ["/job/be"], "only the tech posting cost a request"
    by_title = {item["title"]: item["_detail"] for item in raw}
    assert by_title["Backend Engineer"]["description"] == "body for /job/be"
    assert by_title["Housekeeper"] == {} and by_title["Chef"] == {}
    assert scraper.truncated is None


@pytest.mark.parametrize("async_fanout", ["1", "0"])
def test_smartrecruiters_gates_details_and_pairs_them_back(monkeypatch, async_fanout):
    """The same trap at smartrecruiters' call site — `p["_detail"]` is what `parse` reads —
    on both transports, which now run one request description instead of two copies."""
    from headstart.scrapers.smartrecruiters import SmartRecruitersScraper

    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", async_fanout)
    postings = [
        {"id": "1", "name": "Housekeeper"},
        {"id": "2", "name": "Backend Engineer"},
        {"id": "3", "name": "Chef"},
    ]

    def route(method, url, kwargs):
        if "/postings?" in url:
            return FakeResponse(text=json.dumps({"content": postings, "totalFound": 3}))
        posting_id = url.rsplit("/", 1)[1]
        sections = {"jobDescription": {"text": f"body {posting_id}"}}
        return FakeResponse(text=json.dumps({"jobAd": {"sections": sections}}))

    fetcher = FakeFetcher(route)
    scraper = SmartRecruitersScraper("acme", fetcher=fetcher)
    scraper.have_details = frozenset()
    raw = scraper.fetch_raw()

    detail_urls = fetcher.urls()[1:]
    assert [url.rsplit("/", 1)[1] for url in detail_urls] == ["2"], (
        "only the tech posting cost a request"
    )
    assert fetcher.requests[1].kwargs["headers"]["Accept"] == "application/json"
    by_id = {p["id"]: p["_detail"] for p in raw["content"]}
    assert by_id["2"] == {"description": "body 2", "compensation": None}
    assert by_id["1"] == {} and by_id["3"] == {}


def test_rippling_gates_details_and_reads_a_dict_department_like_parse_does(
    monkeypatch,
):
    """rippling states `department` as a bare string on some tenants and `{"id", "label"}` on
    others (measured live 2026-09-22, 76/76 postings on 3 boards — never a `name` key), and
    `parse` unpacks the dict. The gate must unpack it the same way or it classifies on a
    different string than `filter_tech` gets."""
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "0")
    items = [
        {
            "uuid": "1",
            "name": "Technician",
            "department": {"id": "IT", "label": "Information Technology"},
        },
        {
            "uuid": "2",
            "name": "Receptionist",
            "department": {"id": "Front Desk", "label": "Front Desk"},
        },
    ]
    scraper, fetcher = _rippling_board(
        items, {uuid: {"description": f"body {uuid}"} for uuid in ("1", "2")}
    )
    scraper.have_details = frozenset()
    raw = scraper.fetch_raw()

    assert fetcher.urls()[1:] == [f"{_RIPPLING_ACME_LISTING}/1"], (
        "a vague title is rescued by its department — the gate must see through the dict"
    )
    assert {it["uuid"]: it["_detail"] for it in raw} == {
        "1": {"description": "body 1"},
        "2": {},
    }


def test_apple_gates_details_on_the_listing_title_and_team(monkeypatch):
    """apple's accessors are `postingTitle` and `team.teamName` — the two `parse` reads."""
    from headstart.scrapers.apple import AppleScraper

    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "0")
    items = [
        {"id": "1", "postingTitle": "Housekeeper", "team": {"teamName": "Facilities"}},
        {
            "id": "2",
            "postingTitle": "Backend Engineer",
            "team": {"teamName": "Software"},
        },
        # A vague title rescued only by its team — the gate must read the department too.
        {
            "id": "3",
            "postingTitle": "Technician",
            "team": {"teamName": "Information Technology"},
        },
    ]

    def route(method, url, kwargs):
        if method == "POST":
            page = {"searchResults": items, "totalRecords": len(items)}
            return FakeResponse(text=json.dumps({"res": page}))
        job_number = url.rsplit("/", 1)[1]
        return FakeResponse(
            text=json.dumps({"res": {"description": f"body {job_number}"}})
        )

    fetcher = FakeFetcher(route)
    scraper = AppleScraper("jobs.apple.com", fetcher=fetcher)
    scraper.have_details = frozenset()

    raw = scraper.fetch_raw()

    fetched = sorted(url.rsplit("/", 1)[1] for url in fetcher.urls()[1:])
    assert fetched == ["2", "3"]
    assert set(raw["details"]) == {"2", "3"}


@pytest.mark.parametrize("async_fanout", ["1", "0"])
def test_jazzhr_gate_reads_the_row_title_and_department_not_its_location(
    monkeypatch, async_fanout
):
    """`_rows` yields `(key, title, location, department)` and the gate indexes into it.

    An accessor that drifted onto `location` would classify on the wrong string and nothing would
    raise, which is why `_row_title`/`_row_department` are named functions. The third row here is
    the one that proves it: its department rescues a title the gate would otherwise drop, and its
    *location* would not. On both transports, which now send one request description."""
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", async_fanout)
    listing = (
        '<table id="jobs_table">'
        '<tr id="row_job_1"><td><a href="/apply/jobs/details/k1">Housekeeper</a></td>'
        "<td>Software City</td></tr>"
        '<tr id="row_job_2"><td><a href="/apply/jobs/details/k2">Backend Engineer</a></td>'
        "<td>Remote</td></tr>"
        '<tr id="row_job_3"><td><a href="/apply/jobs/details/k3">Technician</a>'
        '<span class="resumator_department">Information Technology</span></td>'
        "<td>Remote</td></tr></table>"
    )

    def route(method, url, kwargs):
        if url.endswith("/apply/jobs"):
            return FakeResponse(text=listing)
        return FakeResponse(text=f'<div id="job-description">{url}</div>')

    fetcher = FakeFetcher(route)
    scraper = get_scraper("jazzhr", "acme", fetcher=fetcher)
    scraper.have_details = frozenset()

    raw = scraper.fetch_raw()

    assert sorted(fetcher.urls()[1:]) == [
        scraper.job_url("k2"),
        scraper.job_url("k3"),
    ], "k1's location says 'Software City' — reading it as the title would keep it"
    assert set(raw["details"]) == {"k2", "k3"}


def test_zwayam_gate_and_the_held_detail_skip_compose():
    """zwayam is the one scraper carrying both skips; each must prune independently."""
    scraper, fetcher = _zwayam_served_board(
        [
            {"id": 1, "jobTitle": "Backend Engineer", "jobUrl": "a"},
            {"id": 2, "jobTitle": "Housekeeper", "jobUrl": "b"},
            {"id": 3, "jobTitle": "Data Engineer", "jobUrl": "c"},
        ],
        _zwayam_detail_response,
    )
    scraper.have_details = {"zwayam:h.example:3"}  # id 3's text is already stored

    scraper.parse(scraper.fetch_raw(), SCRAPED_AT)

    fetched = [body["jobUrl"] for body in _zwayam_detail_bodies(fetcher)]
    assert fetched == ["a"], "2 is gated out as non-tech, 3 is already held"


def test_trakstar_gates_on_the_card_not_the_code():
    """trakstar fans out over codes, but the title and department live on the card block, so the
    gate reads the block and projects the codes from what survives."""
    import headstart.scrapers.trakstar as trakstar_module

    card = (
        '<div class="rb-source-item" data-href="/jobs/{code}/">'
        '<h3 class="js-job-list-opening-name" title="{title}"></h3>'
        '<div class="rb-text-4">{dept}</div></div>'
    )
    html = trakstar_module._ITEM.join(
        [
            "<html>",
            card.format(code="c1", title="Housekeeper", dept="Facilities"),
            card.format(code="c2", title="Backend Engineer", dept="Software"),
        ]
    )
    scraper, fetcher = _trakstar_board(
        html,
        job_pages={
            code: '<div class="jobdesciption">body</div>' for code in ("c1", "c2")
        },
    )
    scraper.have_details = frozenset()

    raw = scraper.fetch_raw()

    assert _trakstar_job_pages_requested(fetcher) == [scraper.job_url("c2")]
    assert set(raw["postings"]) == {"c2"}


def test_gem_gate_reads_the_listing_department_not_the_location(monkeypatch):
    """gem's department is nested at ``job.department.name``, beside a sibling ``locations``.

    ``_listing_department`` has to walk that nesting, and an accessor that drifted onto the
    location — the only other human-readable string on the row — would classify on the wrong
    string and nothing would raise. The third row is the one that proves it: its department
    rescues a title the gate would otherwise drop, and its location would not."""
    listed = [
        {
            "extId": "e1",
            "title": "Housekeeper",
            "locations": [{"name": "Software City"}],
            "job": {"department": {"name": "Facilities"}},
        },
        {
            "extId": "e2",
            "title": "Backend Engineer",
            "locations": [{"name": "Remote"}],
            "job": {"department": {"name": "Engineering"}},
        },
        {
            "extId": "e3",
            "title": "Technician",
            "locations": [{"name": "Remote"}],
            "job": {"department": {"name": "Information Technology"}},
        },
    ]
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "0")
    scraper = get_scraper("gem", "acme")
    scraper.have_details = frozenset()
    monkeypatch.setattr(scraper, "_listing", lambda: listed)
    batched: list[list[str]] = []
    monkeypatch.setattr(
        scraper,
        "_detail_batch",
        lambda batch: batched.append(batch) or {i: {"extId": i} for i in batch},
    )

    raw = scraper.fetch_raw()

    assert batched == [["e2", "e3"]], (
        "e1's location says 'Software City' — reading it as the department would keep it"
    )
    assert set(raw["details"]) == {"e2", "e3"}


def test_phenom_gate_reads_the_listing_title_and_category_not_the_teaser(monkeypatch):
    """phenom's listing row carries a `descriptionTeaser` beside its `title`, and its department
    label is `category` — not the `jobFamilyGroup` the *detail* uses.

    An accessor that drifted onto either neighbour would classify on the wrong string and nothing
    would raise. The first row proves the title: its teaser names an engineering team. The third
    proves the department: its `category` rescues a title the gate would otherwise drop, and the
    `jobFamilyGroup` sitting beside it would not. The gate runs before `needs_detail`, so the
    second row also shows the two skips composing."""
    from headstart.scrapers.phenom import PhenomScraper

    listed = [
        {
            "jobId": "1",
            "title": "Housekeeper",
            "descriptionTeaser": "Join our software engineering team",
            "category": "Facilities",
        },
        {"jobId": "2", "title": "Backend Engineer", "category": "Engineering"},
        {
            "jobId": "3",
            "title": "Technician",
            "category": "Information Technology",
            "jobFamilyGroup": "Facilities",
        },
        {"jobId": "4", "title": "Data Engineer", "category": "Engineering"},
    ]

    def route(method, url, kwargs):
        posting_id = kwargs["json"]["jobId"]
        job = {"description": f"body {posting_id}"}
        return FakeResponse(text=json.dumps({"jobDetail": {"data": {"job": job}}}))

    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "0")
    fetcher = FakeFetcher(route)
    scraper = PhenomScraper("careers.acme.com", fetcher=fetcher)
    # id 4's text is already stored
    scraper.have_details = {"phenom:careers.acme.com:4"}
    monkeypatch.setattr(scraper, "_prefix", lambda: ("us", "en"))
    monkeypatch.setattr(scraper, "_listing", lambda: listed)

    raw = scraper.fetch_raw()

    fetched = sorted(request.kwargs["json"]["jobId"] for request in fetcher.requests)
    assert fetched == ["2", "3"], (
        "1's teaser names engineering — reading it as the title would keep it; 3's "
        "jobFamilyGroup says Facilities — reading it as the department would drop it"
    )
    assert set(raw["details"]) == {"2", "3"}


def test_eightfold_smartapply_to_pcsx_shape_carries_the_requisition_ids():
    """The PCSX search states `atsJobId`/`displayJobId` — the backing ATS's requisition id — and
    SmartApply states the same as `ats_job_id`/`display_job_id` (albemarle `REQ-31366`,
    2026-09-24). `eightfold_backing_boards.py` matches on them (ADR-0205)."""
    from headstart.scrapers.eightfold import _smartapply_to_pcsx_shape

    got = _smartapply_to_pcsx_shape(
        {"id": 1, "ats_job_id": "REQ-31366", "display_job_id": "REQ-31366"}
    )
    assert (got["atsJobId"], got["displayJobId"]) == ("REQ-31366", "REQ-31366")
