"""Tests for `headstart.scrapers.breezy`.

The fixtures are real `GET https://{slug}.breezy.hr/json?verbose=true` responses captured
2026-09-23, trimmed to a subset of rows (never edited):

- `breezy_ssg-corp.json` — 8 of 33 postings, chosen for a posting in three places, one remote,
  one hybrid, one whose stale `remote_details` says remote while `is_remote` is false, a bare-`$`
  salary in the US, in Canada and in Costa Rica, an `RD$` one, and a `temporary` type.
- `breezy_german-american-chambers-of-commerce.json` — 4 of 28, for a posting with no
  `is_remote` key and an empty `locations`, a yearly range, a yearly floor, and a range that
  states no period.

Every assertion pins something measured in `docs/breezy/2026-09-23_json-api-measurement.md`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from headstart import employment_type
from headstart.scrapers.breezy import BreezyScraper
from headstart.scrapers.registry import get_scraper

FIXTURES = Path(__file__).parent / "fixtures"
SCRAPED_AT = "2026-01-01T00:00:00+00:00"
SSG = "ssg-corp"
GACC = "german-american-chambers-of-commerce"


def _rows(slug: str) -> list[dict]:
    with open(FIXTURES / f"breezy_{slug}.json", encoding="utf-8") as fh:
        return json.load(fh)


def _jobs(slug: str) -> dict:
    scraper = get_scraper("breezy", slug)
    return {j.id.rsplit(":", 1)[1]: j for j in scraper.parse(_rows(slug), SCRAPED_AT)}


def test_every_row_becomes_a_job_keyed_on_the_native_id_and_linked_to_its_page():
    """One Job per row; the id is `breezy:{slug}:{id}` and the link is the row's own `url`,
    which is `https://{slug}.breezy.hr/p/{friendly_id}` on all 38,314 postings measured."""
    jobs = _jobs(SSG)
    assert len(jobs) == len(_rows(SSG)) == 8
    job = jobs["65fef260f81c"]
    assert job.id == "breezy:ssg-corp:65fef260f81c"
    assert job.ats == "breezy"
    assert job.title == ".NET Developer"
    assert job.url == "https://ssg-corp.breezy.hr/p/65fef260f81c-net-developer"


def test_the_description_is_read_from_the_listing_so_there_is_no_detail_pass():
    """`?verbose=true` adds `description`, and its text equals the detail page's JSON-LD text on
    180 of 180 postings measured, so no second request is made (ADR-0050's flag stays False)."""
    job = _jobs(SSG)["65fef260f81c"]
    assert job.description.startswith("*This position is remote within the")
    assert "<" not in job.description
    assert get_scraper("breezy", SSG).has_detail_pass is False
    assert get_scraper("breezy", SSG).url() == (
        "https://ssg-corp.breezy.hr/json?verbose=true"
    )


def test_company_department_and_date_come_straight_from_the_row():
    """`company.name` is one value per Board on 2,174 of 2,174 hiring Boards; `published_date`
    is stable across refetches (293 of 293) and is the latest publish."""
    job = _jobs(GACC)["27c8426c9db9"]
    assert job.company == "German American Chambers of Commerce"
    assert job.department == "Opportunities at external client companies"
    assert job.posted_at == _rows(GACC)[2]["published_date"]
    assert _jobs(SSG)["5958b9530413"].department is None


def test_every_place_a_posting_names_is_joined_primary_first():
    """`locations` holds 2-5 places on 2,017 of 38,314 postings; cutting to the primary would fail
    the location filter everywhere else. The primary leads, and is kept even when `locations`
    does not name it (828 of the 35,344 rows that list places) or is empty (2,970 rows)."""
    ssg = _jobs(SSG)
    assert ssg["65fef260f81c"].location == "Hermosillo, MX; Santiago, DO; Colon, PA"
    assert ssg["bcf5df2f3a94"].location == "Canada; Toronto, ON; NB, CA"
    assert ssg["7f4877e9f32f"].location == "Waco, TX"
    assert _jobs(GACC)["dcc694c6b681"].location == "New York, NY"


def test_remote_is_the_stated_flag_and_hybrid_is_not_remote():
    """`location.is_remote` matched the page's JSON-LD `jobLocationType` on 103 of 103 checked;
    `remote_details` is stale where the flag is false. True-and-hybrid is `None` (ashby's rule).
    With no flag at all (2,194 rows) the location text is the only evidence left."""
    ssg = _jobs(SSG)
    assert ssg["65fef260f81c"].remote is True  # remote-location
    assert ssg["5a4de9273eb1"].remote is None  # is_remote true, remote_details hybrid
    assert (
        ssg["26d87665a903"].remote is False
    )  # remote_details says remote; the flag does not
    assert _jobs(GACC)["dcc694c6b681"].remote is False  # no flag: "New York, NY"


def test_employment_type_is_keyed_on_the_type_id_not_its_localised_name():
    """`type.name` is the tenant's language ("Vollzeit", "Повна зайнятість") and reaches no
    filter; `type.id` is one of five values on all 38,314 rows and maps to a label that does."""
    ssg = _jobs(SSG)
    assert ssg["65fef260f81c"].employment_type == "Full-Time"
    assert ssg["26d87665a903"].employment_type == "Temporary"
    assert employment_type.flags("Full-Time")["is_full_time"]
    assert employment_type.flags("Part-Time")["is_part_time"]
    assert employment_type.flags("Contract")["is_contract"]


def test_an_unobserved_type_id_passes_through_and_a_localised_name_does_not():
    row = {**_rows(SSG)[0], "type": {"id": "seasonal", "name": "Saisonal"}}
    job = get_scraper("breezy", SSG).parse([row], SCRAPED_AT)[0]
    assert job.employment_type == "seasonal"
    row = {**_rows(SSG)[0], "type": {"id": "partTime", "name": "Teilzeit"}}
    assert get_scraper("breezy", SSG).parse([row], SCRAPED_AT)[0].employment_type == (
        "Part-Time"
    )


def _salary(raw: str, country: str | None) -> str | None:
    location = {"country": {"id": country}} if country else None
    return get_scraper("breezy", SSG)._salary_field(
        {"salary": raw, "location": location}
    )


def test_a_bare_dollar_names_its_currency_by_the_postings_country():
    """ADR-0181, the user's scoped exception to `salary._symbol_currency`: against the page's
    JSON-LD a bare `$` was USD on 141 of 141 US postings and CAD on 179 of 188 Canadian ones, but
    USD on only 41 of 47 elsewhere — so only the first two are named."""
    assert _salary("$19 – $20 / hour", "US") == "19-20 USD HOUR"
    assert _salary("$19 – $20 / hour", "CA") == "19-20 CAD HOUR"
    assert _salary("$5.00 – $6.50 / hour", "CR") == "5.00-6.50 HOUR"
    assert _salary("$19 – $20 / hour", None) == "19-20 HOUR"


def test_the_fixture_salaries_as_they_reach_the_job():
    ssg = _jobs(SSG)
    assert ssg["7f4877e9f32f"].salary == "19-20 USD HOUR"  # Waco, TX
    assert ssg["26d87665a903"].salary == "19.35 CAD HOUR"  # "$19.35+", Toronto
    assert ssg["16bd33721adf"].salary == "5.00-6.50 HOUR"  # "$", Costa Rica
    assert ssg["e75cea4657ad"].salary == "210-300 DOP HOUR"  # "RD$", Dominican Republic
    assert ssg["65fef260f81c"].salary is None  # none stated
    gacc = _jobs(GACC)
    assert gacc["27c8426c9db9"].salary == "165000-180000 USD YEAR"
    assert gacc["90e5a5fcf651"].salary == "110000 USD YEAR"  # "$110,000+ / year"
    assert gacc["246e9841bf65"].salary == "75000-85000 USD"  # no period stated


def test_a_ceiling_alone_a_biweekly_period_and_an_unknown_shape_state_no_salary():
    """A lone "Up to" figure would read as a floor, and no shared parser annualises biweekly."""
    assert _salary("Up to $60,000 / year", "US") is None
    assert _salary("$2,000 – $2,500 / biweekly", "US") is None
    assert _salary("Competitive", "US") is None
    assert _salary("kr30,000 – kr40,000 / month", "SE") == "30000-40000 MONTH"


def test_url_shape_matches_every_link_the_scraper_builds():
    """`verify_filters.URL_SHAPES` is generated from `url_shape` (ADR-0157), so it must match what
    `job_url()` emits — here on every fixture row, underscored `friendly_id`s included (3 of
    38,314 measured carry one)."""
    shape = re.compile(BreezyScraper.url_shape)
    for slug in (SSG, GACC):
        for job in _jobs(slug).values():
            assert shape.fullmatch(job.url), job.url
    underscored = BreezyScraper("acme").job_url("86f8d21aa9c8-middle-python_engineer")
    assert shape.fullmatch(underscored)
