"""Tests for `headstart.scrapers.pinpoint`.

`pinpoint_postings.json` is six real rows of `GET https://{slug}.pinpointhq.com/postings.json`,
captured 2026-09-23 from six Boards (woodrodgers, clientearth, wearehuman8, butlins, hopwood,
onlineriver) and put in one envelope. They cover all three `workplace_type`s, a visible hourly,
yearly and weekly salary, a visible free-text one, a hidden one, a vanity-host `url`
(`jobs.clientearth.org`) and a location with no city. `pinpoint_posting_page.html` is the page of
the first row (`woodrodgers`), trimmed to its `<title>` and its JSON-LD block.

Every assertion pins something measured in `docs/pinpoint/2026-09-23_postings-api-measurement.md`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fake_fetcher import FakeFetcher, FakeResponse

from headstart import company_name, salary
from headstart.scrapers.pinpoint import PinpointScraper
from headstart.scrapers.registry import detail_pass_atses, get_scraper

FIXTURES = Path(__file__).parent / "fixtures"
SCRAPED_AT = "2026-01-01T00:00:00+00:00"
SLUG = "woodrodgers"
ENGINEER = (
    "f0bd8a53-6215-4532-b62a-9c8034c77f56"  # woodrodgers, onsite, hourly, page fixture
)
LAWYER = (
    "d2b86ef0-8d16-41f6-9962-63a64fdc0631"  # clientearth, hybrid, yearly, vanity host
)
ASSOCIATE = "1c7cff75-1df2-43a5-b35d-44683c855500"  # wearehuman8, remote, salary hidden
CREW = "d165410f-8070-4eed-91c1-4cc9fbaa99dc"  # butlins, weekly salary
LECTURER = "789ce85c-e59b-4a93-a298-7943dc614846"  # hopwood, free-text salary
DATA_ENTRY = "d10a9b11-2d83-4955-b874-5f46ec30164c"  # onlineriver, remote, no city


def _listing() -> dict:
    with open(FIXTURES / "pinpoint_postings.json", encoding="utf-8") as fh:
        return json.load(fh)


def _page() -> str:
    return (FIXTURES / "pinpoint_posting_page.html").read_text(encoding="utf-8")


def _scraper() -> PinpointScraper:
    return get_scraper("pinpoint", SLUG)


def _jobs(details: dict | None = None) -> dict:
    raw = {"data": _listing()["data"], "details": details or {}}
    return {j.id.rsplit(":", 1)[1]: j for j in _scraper().parse(raw, SCRAPED_AT)}


def test_a_listing_row_becomes_a_job_keyed_by_its_posting_uuid():
    """The UUID is the only id that addresses a page (`/en/postings/{numeric id}` is a 404),
    so it is the native id; the listing's numeric `id` is not used."""
    job = _jobs()[ENGINEER]
    assert job.id == f"pinpoint:{SLUG}:{ENGINEER}"
    assert job.ats == "pinpoint"
    assert job.title == "Assistant Engineer"
    assert job.department == "Transportation"
    assert job.url == f"https://{SLUG}.pinpointhq.com/en/postings/{ENGINEER}"


def test_the_description_is_every_listing_section_under_its_header():
    """The listing is not a teaser: the page's own JSON-LD description is these four sections in
    this order under their tenant-chosen headers. `skills_knowledge_expertise` is absent on 14.3%
    and `benefits` on 27.7% of rows, so a missing section is skipped with its header."""
    row = next(r for r in _listing()["data"] if r["url"].endswith(ENGINEER))
    text = _jobs()[ENGINEER].description
    assert text.startswith("Are you ")
    heads = [
        row["key_responsibilities_header"],
        row["skills_knowledge_expertise_header"],
        row["benefits_header"],
    ]
    positions = [text.index(h) for h in heads]
    assert positions == sorted(positions)
    assert "<" not in text


def test_a_missing_section_drops_its_header_too():
    row = dict(_listing()["data"][0], benefits=None)
    raw = {"data": [row], "details": {}}
    (job,) = _scraper().parse(raw, SCRAPED_AT)
    assert row["benefits_header"] not in job.description


def test_location_joins_the_site_label_city_and_province_without_repeats():
    """`location.name` is the tenant's label for the place — often a site or office name, equal to
    `city` on only 1,704 of 13,419 rows — so it leads, and `city`/`province` follow only when the
    label does not already say them."""
    jobs = _jobs()
    assert jobs[ENGINEER].location == "Denver, CO, Colorado"
    assert jobs[LAWYER].location == "Tokyo, Minato-ku"
    assert jobs[DATA_ENTRY].location == "New York"
    assert jobs[ASSOCIATE].location == "US, Royal Oak, Michigan"


def test_remote_is_the_stated_workplace_type_and_hybrid_is_unknown():
    """`workplace_type` is populated on 100% of rows; 919 of 1,399 `remote` rows name only a
    city, so the location guess would miss them. Hybrid is not remote (ashby's rule)."""
    jobs = _jobs()
    assert jobs[ASSOCIATE].remote is True
    assert jobs[DATA_ENTRY].remote is True
    assert jobs[ENGINEER].remote is False
    assert jobs[LAWYER].remote is None


def test_employment_type_is_the_tenant_label():
    """`employment_type_text` is the display label of the `employment_type` code on all 13,419
    rows; the label is what `employment_type_filter.flags` reads ("Permanent - Full Time" is full-time,
    "Fixed Term Contract" a contract), where the code (`permanent_full_time`) reaches no filter."""
    assert _jobs()[ENGINEER].employment_type == "Full Time"


def test_a_visible_structured_salary_is_the_range_the_field_parser_reads():
    """min, max, ISO currency and `compensation_frequency`, spelled the way
    `salary._field_generic` reads a range at a period. min = max on 1,342 rows and stays a range."""
    jobs = _jobs()
    assert jobs[ENGINEER].salary == "31.75-39.50 USD per-hour"
    assert jobs[LAWYER].salary == "13000000-15000000 JPY per-year"
    assert salary.extract(jobs[ENGINEER].salary, None, ats="pinpoint").min_annual > 0


def test_a_hidden_salary_is_not_emitted():
    """`compensation_visible` is the tenant's publication choice; 0 of 6,418 hidden rows carried
    a figure, so this only states the choice."""
    assert _jobs()[ASSOCIATE].salary is None


def test_a_period_the_parser_cannot_read_yields_no_salary():
    """week (33), day (16) and two_weeks (10) have no spelling `salary._field_generic` reads at
    the right period, and its default is annual — so a weekly wage passed through would be served
    52x too low. None rather than wrong."""
    assert _jobs()[CREW].salary is None


def test_a_free_text_salary_passes_through_verbatim():
    """466 visible rows state compensation only as prose; the shared parser reads what it can
    and declines the rest."""
    assert _jobs()[LECTURER].salary.startswith("£17,513 - £22,969 per annum")


# ------------------------------------------------------------------------------ the page pass


def _fetching_scraper(
    *listings: dict, page=None
) -> tuple[PinpointScraper, FakeFetcher]:
    """A scraper behind a fake fetcher that answers the Board URL with each of ``listings`` in
    turn (the last one again once they run out) and any posting page with ``page`` (default: the
    fixture page). The fake records every request."""
    answers = list(listings)
    posting_page = page or FakeResponse(text=_page())

    def route(method, url, kwargs):
        if url.endswith("/postings.json"):
            listing = answers.pop(0) if len(answers) > 1 else answers[0]
            return FakeResponse(text=json.dumps(listing))
        return posting_page

    fetcher = FakeFetcher(route)
    return get_scraper("pinpoint", SLUG, fetcher=fetcher), fetcher


@pytest.mark.parametrize("async_fanout", ["1", "0"])
def test_the_page_is_asked_for_as_html_and_read_on_either_transport(
    monkeypatch, async_fanout
):
    """The posting page content-negotiates: `Accept: application/json, text/html` — what the
    shared `_get` sends — answers **406** with a 52-byte JSON error, on every page (192 of 192 on
    impulsespace). `text/html` answers the page — on either transport."""
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", async_fanout)
    scraper, fetcher = _fetching_scraper({"data": [_listing()["data"][0]]})
    raw = scraper.fetch_raw()
    page_request = fetcher.requests[-1]
    assert page_request.url == scraper.job_url(ENGINEER)
    assert page_request.kwargs["headers"]["Accept"] == "text/html"
    assert raw["details"][ENGINEER] == {
        "posted_at": "2026-08-25T18:11:43+01:00",
        "country": "United States",
        "company": "Wood Rodgers",
    }
    # the posting's hiring organization is kept for a Board whose title names no one
    assert scraper._posting_company == "Wood Rodgers"


def test_posted_at_and_country_come_from_the_posting_page():
    """No listing field states a date on any of 13,419 rows; the page's JSON-LD `datePosted` did
    on 76 of 76, unchanged across two fetches 5 s apart. The country the listing lacks is the
    JSON-LD `applicantLocationRequirements` name (75 of 76)."""
    scraper, fetcher = _fetching_scraper({"data": [_listing()["data"][0]]})
    raw = scraper.fetch_raw()
    assert fetcher.urls() == [scraper.url(), scraper.job_url(ENGINEER)]
    (job,) = scraper.parse(raw, SCRAPED_AT)
    assert job.posted_at == "2026-08-25T18:11:43+01:00"
    assert job.location == "Denver, CO, Colorado, United States"


def test_a_failed_page_is_a_counted_gap_and_the_job_still_ships():
    """A posting closed between the listing and its page 404s. Only the date and country are
    lost; the Board's list is whole, so it is not truncated."""
    scraper, _fetcher = _fetching_scraper(_listing(), page=FakeResponse(404))
    raw = scraper.fetch_raw()
    assert raw["details"] == {}
    assert scraper.detail_losses == {"HTTP 404": 6}
    assert scraper.truncated is None
    jobs = scraper.parse(raw, SCRAPED_AT)
    assert len(jobs) == 6
    assert all(j.posted_at is None and j.description for j in jobs)


def test_a_page_without_json_ld_is_a_labelled_gap():
    """The vendor's own 404 page is HTML with no JSON-LD; a 200 of anything else like it must
    count as a loss rather than pass as a posting with no date."""
    scraper, _fetcher = _fetching_scraper(
        {"data": [_listing()["data"][0]]}, page=FakeResponse(text="<html></html>")
    )
    raw = scraper.fetch_raw()
    assert raw["details"] == {}
    assert scraper.detail_losses == {"no JSON-LD on a 200": 1}


def test_the_tech_gate_skips_non_tech_pages_but_still_emits_the_job(monkeypatch):
    """ADR-0166's gate as an exact site: `parse` reads `title` and `job.department.name` off the
    same listing row the gate reads (department is populated on 13,419 of 13,419 rows), and the
    page overrides neither. Armed only inside the pipeline (`have_details` set)."""
    monkeypatch.delenv("HEADSTART_TECH_GATE", raising=False)
    scraper, fetcher = _fetching_scraper(_listing())
    scraper.have_details = frozenset()
    raw = scraper.fetch_raw()
    # Of the six, `is_tech` keeps only "Assistant Engineer" — not even "Data Entry Agent" filed
    # under a "Technical" department.
    assert set(raw["details"]) == {ENGINEER}
    assert fetcher.urls() == [scraper.url(), scraper.job_url(ENGINEER)]
    assert len(scraper.parse(raw, SCRAPED_AT)) == 6
    assert scraper.telemetry.get("tech_gated_details") == 5


def test_the_already_described_skip_is_not_taken(monkeypatch):
    """ADR-0048 skips a detail whose description the store holds. Here the description comes
    from the listing and the page supplies only `posted_at` and the country, which exist nowhere
    else, so skipping it would blank the date on every run after the first."""
    monkeypatch.setenv("HEADSTART_TECH_GATE", "0")
    scraper, fetcher = _fetching_scraper({"data": [_listing()["data"][0]]})
    scraper.have_details = frozenset({f"pinpoint:{SLUG}:{ENGINEER}"})
    raw = scraper.fetch_raw()
    assert scraper.job_url(ENGINEER) in fetcher.urls()
    assert raw["details"][ENGINEER]["posted_at"]


# ------------------------------------------------------------------------------ identity


def test_the_slug_is_the_lowercased_subdomain_label():
    """DNS and the board are case-insensitive (`CINVEN.pinpointhq.com/postings.json` serves
    `cinven`), and the seed list spells one tenant `Cinven`; one Board, one key."""
    assert (
        PinpointScraper.slug_from("Cinven", "https://Cinven.pinpointhq.com") == "cinven"
    )
    assert _scraper().url() == f"https://{SLUG}.pinpointhq.com/postings.json"


def test_a_vanity_host_job_links_to_the_vendor_host_and_matches_the_declared_shape():
    """29 of 433 hiring Boards run a vanity host and the listing's `url` names it
    (`jobs.clientearth.org`), but the same path on the vendor host serves the same page (5 of 5
    checked), so every link has one shape."""
    job = _jobs()[LAWYER]
    assert job.url == f"https://{SLUG}.pinpointhq.com/en/postings/{LAWYER}"
    for j in _jobs().values():
        assert re.fullmatch(PinpointScraper.url_shape, j.url)


def test_the_scraper_declares_a_detail_pass():
    assert PinpointScraper.has_detail_pass is True
    assert "pinpoint" in detail_pass_atses()


def test_the_company_name_is_the_board_page_title():
    """`Jobs at {Name} | {Name} Careers` on 40 of 40 sampled Boards. The vendor's own Board
    (`workwithus`, "Jobs at Pinpoint") keeps its slug."""
    assert _scraper().board_page() == f"https://{SLUG}.pinpointhq.com/"
    assert (
        company_name.from_title(
            "pinpoint", "Jobs at The Jed Foundation | The Jed Foundation Careers", "jed"
        )
        == "The Jed Foundation"
    )
    assert (
        company_name.from_title(
            "pinpoint", "Jobs at Reconomy  | Reconomy  Careers", "x"
        )
        == "Reconomy"
    )
    assert (
        company_name.from_title(
            "pinpoint", "Jobs at Pinpoint | Pinpoint Careers", "workwithus"
        )
        is None
    )


def test_location_skips_only_whole_repeats_not_substrings():
    """A place is dropped only when it repeats a whole part already written — "Indiana" is not a
    repeat of "Indianapolis", nor "Bath" of "Bathgate Office"."""
    row = dict(
        _listing()["data"][0],
        location={
            "name": "Indianapolis",
            "city": "Indianapolis",
            "province": "Indiana",
        },
    )
    (job,) = _scraper().parse({"data": [row], "details": {}}, SCRAPED_AT)
    assert job.location == "Indianapolis, Indiana"
    row["location"] = {
        "name": "Bathgate Office",
        "city": "Bath",
        "province": "Somerset",
    }
    (job,) = _scraper().parse({"data": [row], "details": {}}, SCRAPED_AT)
    assert job.location == "Bathgate Office, Bath, Somerset"


def test_the_gate_reads_the_same_title_and_department_parse_emits(monkeypatch):
    """ADR-0166's exactness holds only if the gate and `parse` read the same strings; both strip
    the tenant's stray whitespace ("Assistant Engineer ", "Access ")."""
    seen: list[tuple] = []
    monkeypatch.setattr(
        "headstart.scrapers.base.is_tech", lambda t, d: seen.append((t, d)) or False
    )
    monkeypatch.delenv("HEADSTART_TECH_GATE", raising=False)
    scraper, _fetcher = _fetching_scraper(_listing())
    scraper.have_details = frozenset()
    raw = scraper.fetch_raw()
    emitted = [(j.title, j.department) for j in scraper.parse(raw, SCRAPED_AT)]
    assert seen == emitted


def test_an_empty_listing_is_asked_again_before_it_is_believed():
    """The listing sometimes answers a Board that has postings with a spurious `{"data":[]}`
    (12 of 6,030 fetches over 670 hiring Boards, as often at concurrency 4 as at 16; each Board
    answered with postings on its other fetches). An empty Board costs 11 bytes, so an empty
    answer is asked once more."""
    scraper, fetcher = _fetching_scraper(
        {"data": []}, {"data": [_listing()["data"][0]]}
    )
    raw = scraper.fetch_raw()
    assert fetcher.urls()[:2] == [scraper.url(), scraper.url()]
    assert len(raw["data"]) == 1


def test_a_board_that_is_empty_twice_is_empty():
    scraper, fetcher = _fetching_scraper({"data": []})
    assert scraper.fetch_raw() == {"data": [], "details": {}}
    assert fetcher.urls() == [scraper.url(), scraper.url()]


def test_a_board_page_that_names_no_one_falls_back_to_its_postings(monkeypatch):
    """The board title first (brand before legal name); where the board page is gone or its title
    is the slug, the hiring organization a posting page names (6 of 10 affected Boards,
    2026-09-24)."""
    from headstart import http

    monkeypatch.setattr(http, "fetch", lambda *a, **k: FakeResponse(404))
    scraper = PinpointScraper("kharon")
    scraper._posting_company = "Kharon"
    scraper.resolve_company()
    assert scraper.company == "Kharon"
