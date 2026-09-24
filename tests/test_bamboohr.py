"""Tests for the BambooHR scraper (headstart.scrapers.bamboohr).

The widget fixture is a real capture of `4thdimension.bamboohr.com/jobs/embed2.php`
(2026-09-16): 4 departments, 5 jobs, including one job (335, "Motor Claims Handler") whose
listing location carries the "(Hybrid)" suffix this scraper's own live measurement used to find
that `locationType == "2"` means Hybrid, not remote — see the module docstring's finding 1. The
detail fixture carries real `/careers/{id}/detail` records for two of those jobs plus one fully
remote job from a different tenant (350.org, job 63); job 336 ("Parts Production Team Leader")
has no detail record on purpose, to exercise the listing-only fallback path.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest
from fake_fetcher import FakeFetcher, FakeResponse

from headstart.scrapers.bamboohr import (
    BambooHRScraper,
    _canonical_location,
    _department_map,
    _remote,
)
from headstart.scrapers.registry import get_scraper

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
SCRAPED_AT = "2026-09-16T00:00:00+00:00"

_WIDGET = (FIXTURES / "bamboohr_widget.html").read_text(encoding="utf-8")
_DETAILS = json.loads((FIXTURES / "bamboohr_details.json").read_text(encoding="utf-8"))


def _raw(details_by_native_id: dict[str, dict]) -> dict[str, Any]:
    return {"page": _WIDGET, "details": details_by_native_id}


# --- parse(): the listing + detail merge ---------------------------------------------------


def test_parse_reads_every_position_across_departments():
    jobs = get_scraper("bamboohr", "4thdimension").parse(_raw({}), SCRAPED_AT)
    assert [j.title for j in jobs] == [
        "Van Drivers Wanted",
        "Van Drivers Wanted Tuesday to Saturday",
        "Motor Claims Handler",
        "Parts Production Team Leader",
        "Sales Advisor",
    ]


def test_parse_merges_detail_fields_over_the_listing():
    details = {"331": _DETAILS["4thdimension:331"]}
    jobs = get_scraper("bamboohr", "4thdimension").parse(_raw(details), SCRAPED_AT)
    job = next(j for j in jobs if j.title == "Van Drivers Wanted")
    assert job.id == "bamboohr:4thdimension:331"
    assert job.url == "https://4thdimension.bamboohr.com/careers/331"
    assert job.department == "Drivers"
    assert job.location == "Egham, Surrey, United Kingdom"  # canonical, from detail
    assert job.remote is False  # locationType "0"
    assert job.experience == "Entry-level"  # minimumExperience
    assert job.employment_type == "Employed"  # employmentStatusLabel
    assert job.salary == "£28,090"  # compensation, passed through
    assert job.posted_at == "2026-09-16"
    assert "Van Drivers" in (job.description or "")


def test_parse_falls_back_to_listing_when_detail_is_missing():
    """Job 336 has no detail record in the fixture — the listing-only degrade ADR-0050 expects
    of any detail-pass ATS. `department` is the one field that no longer goes null with it: it
    is read off the widget's own department block (ADR-0017 gate support, 2026-09-22)."""
    jobs = get_scraper("bamboohr", "4thdimension").parse(_raw({}), SCRAPED_AT)
    job = next(j for j in jobs if j.title == "Parts Production Team Leader")
    assert job.id == "bamboohr:4thdimension:336"
    assert job.location == "Egham, Surrey"  # the listing's own terse text
    assert job.remote is False  # is_remote() fallback: no "remote" substring
    assert (
        job.department == "Motomine Parts Sales & Operations"
    )  # from the widget, not detail
    assert job.experience is None
    assert job.description is None
    assert job.salary is None


def test_parse_reads_hybrid_as_remote_none_not_true():
    """The measured finding this scraper exists to fix: locationType "2" is Hybrid, and
    kalil0321's own upstream reads it as remote=True (see module docstring, finding 1)."""
    details = {"335": _DETAILS["4thdimension:335"]}
    jobs = get_scraper("bamboohr", "4thdimension").parse(_raw(details), SCRAPED_AT)
    job = next(j for j in jobs if j.title == "Motor Claims Handler")
    assert job.remote is None
    assert job.location == "Egham, Surrey, United Kingdom"


def test_a_fully_remote_jobs_detail_record_maps_to_remote_true():
    """350.org job 63 — real fixture data for a fully remote posting, whose structured
    ``location`` is all-None (see test_canonical_location_is_none_for_an_all_empty_dict) and
    whose signal is `locationType == "1"` instead."""
    opening = _DETAILS["350:63"]
    assert opening["locationType"] == "1"
    assert _remote(opening["locationType"], "Remote") is True


def test_empty_widget_page_returns_no_jobs():
    assert BambooHRScraper("acme").parse({"page": "", "details": {}}, SCRAPED_AT) == []


def test_job_url_matches_url_shape():
    import re

    scraper = get_scraper("bamboohr", "4thdimension")
    jobs = scraper.parse(_raw({}), SCRAPED_AT)
    for job in jobs:
        assert re.fullmatch(scraper.url_shape, job.url)


# --- _remote(): the locationType -> Job.remote mapping ---------------------------------------


def test_remote_helper_maps_location_type():
    assert _remote("0", "Egham, Surrey") is False
    assert _remote("1", "Remote") is True
    assert _remote("2", "Egham, Surrey (Hybrid)") is None  # Hybrid stays None, not True


def test_remote_helper_falls_back_to_listing_text_when_locationType_absent():
    assert _remote(None, "Remote") is True
    assert _remote(None, "Egham, Surrey") is False
    assert _remote(None, None) is None


# --- _canonical_location(): the detail record's structured location dict ---------------------


def test_canonical_location_joins_city_state_country():
    loc = {
        "city": "Egham",
        "state": "Surrey",
        "postalCode": "TW20 9AB",
        "addressCountry": "United Kingdom",
    }
    assert (
        _canonical_location(loc) == "Egham, Surrey, United Kingdom"
    )  # postal code omitted


def test_canonical_location_is_none_for_an_all_empty_dict():
    """A remote job's detail location is all-None (real, from the 350.org fixture record) —
    parse() falls back to the listing's own terse text in that case."""
    assert _canonical_location(_DETAILS["350:63"]["location"]) is None


def test_canonical_location_handles_a_missing_or_non_dict_value():
    assert _canonical_location(None) is None
    assert _canonical_location("not a dict") is None


# --- fetch_raw(): dead vs. live-but-empty, from the same HTTP 200 ---------------------------
# See the module docstring's measurement: a dead tenant's widget is a 200 with an EMPTY body,
# while a live tenant (jobs or not) always serves the BambooHR-ATS-board wrapper. FakeFetcher
# mirrors tests/test_fetcher.py's pattern (ADR-0153) rather than hitting the network.


class _FakeResponse:
    def __init__(self, text: str = "") -> None:
        self.status_code = 200
        self.text = text

    def raise_for_status(self) -> None:
        pass


class _FakeFetcher:
    def __init__(self, text: str) -> None:
        self._text = text

    def fetch(self, method: str, url: str, **_kwargs: Any) -> _FakeResponse:
        return _FakeResponse(self._text)

    async def fetch_async(self, session: Any, method: str, url: str, **kwargs: Any):
        return self.fetch(method, url, **kwargs)


def test_fetch_raw_treats_a_dead_tenants_empty_body_as_an_unread_board():
    scraper = BambooHRScraper("gone", fetcher=_FakeFetcher(""))
    assert scraper.fetch_raw() == {"page": "", "details": {}}


def test_fetch_raw_reads_a_live_but_jobless_tenants_blank_state():
    blank = (
        '<div class="BambooHR-ATS-board"><div class="BambooHR-ATS-blankState">'
        "We currently have no open positions.</div></div>"
    )
    scraper = BambooHRScraper("empty-board", fetcher=_FakeFetcher(blank))
    assert scraper.fetch_raw() == {"page": blank, "details": {}, "departments": {}}


# --- fetch_raw(): parse-drift guard (issue #534) ----------------------------------------------


def test_fetch_raw_treats_changed_row_markup_as_an_unread_board_not_an_empty_one():
    """The wrapper is present and the `bhrPositionID_` marker appears in the HTML, but nothing
    matches `_POSITION` — the row markup itself changed, not a Board with nothing open. Must not
    read as a whole, empty listing (ADR-0083's one-scrape grace would then evict every row)."""
    drifted = (
        '<div class="BambooHR-ATS-board">'
        '<li data-position-id="331">bhrPositionID_331 moved to a data attribute</li>'
        "</div>"
    )
    scraper = BambooHRScraper("drifted", fetcher=_FakeFetcher(drifted))
    assert scraper.fetch_raw() == {"page": "", "details": {}}


def test_fetch_raw_still_reads_a_genuinely_empty_but_well_formed_board():
    """The guard must not misfire on the real empty-board shape: wrapper present, no
    `bhrPositionID_` marker anywhere (nothing to have drifted)."""
    blank = (
        '<div class="BambooHR-ATS-board"><div class="BambooHR-ATS-blankState">'
        "We currently have no open positions.</div></div>"
    )
    scraper = BambooHRScraper("empty-board", fetcher=_FakeFetcher(blank))
    assert scraper.fetch_raw() == {"page": blank, "details": {}, "departments": {}}


# --- fetch_raw(): the ADR-0017 tech gate (issue #533) ------------------------------------------


def _tech_gate_page() -> str:
    return (
        '<div class="BambooHR-ATS-board">'
        '<li id="bhrDepartmentID_1" class="BambooHR-ATS-Department-Item">'
        '<div class="BambooHR-ATS-Department-Header">Engineering</div>'
        '<ul><li id="bhrPositionID_1">'
        '<a href="https://acme.bamboohr.com/careers/1">Backend Engineer</a>'
        '<span class="BambooHR-ATS-Location">Remote</span></li></ul></li>'
        '<li id="bhrDepartmentID_2" class="BambooHR-ATS-Department-Item">'
        '<div class="BambooHR-ATS-Department-Header">Logistics</div>'
        '<ul><li id="bhrPositionID_2">'
        '<a href="https://acme.bamboohr.com/careers/2">Warehouse Associate</a>'
        '<span class="BambooHR-ATS-Location">Onsite</span></li></ul></li>'
        "</div>"
    )


_ACME_WIDGET_URL = "https://acme.bamboohr.com/jobs/embed2.php"


def _acme_board(
    detail_body_by_id: dict[str, str],
) -> tuple[BambooHRScraper, FakeFetcher]:
    """The two-department `acme` widget, with each posting's `/detail` answering the body given
    for its id (a 404 for any other)."""

    def route(method: str, url: str, kwargs: dict) -> FakeResponse:
        if url == _ACME_WIDGET_URL:
            return FakeResponse(text=_tech_gate_page())
        posting_id = url.removeprefix("https://acme.bamboohr.com/careers/").split("/")[
            0
        ]
        if posting_id not in detail_body_by_id:
            return FakeResponse(404)
        return FakeResponse(text=detail_body_by_id[posting_id])

    fetcher = FakeFetcher(route)
    return BambooHRScraper("acme", "Acme", fetcher=fetcher), fetcher


def _opening_body(department: str) -> str:
    return json.dumps({"result": {"jobOpening": {"departmentLabel": department}}})


def _detail_urls(fetcher: FakeFetcher) -> list[str]:
    return [url for url in fetcher.urls() if url != _ACME_WIDGET_URL]


def test_fetch_raw_skips_the_detail_fetch_for_a_posting_the_tech_filter_will_drop():
    """ADR-0017, added 2026-09-22 — BambooHR was the only `has_detail_pass` ATS without this
    gate. `department` comes off the widget's own blocks (`_department_map`), so the gate can
    run before any detail is fetched."""
    scraper, fetcher = _acme_board(
        {"1": _opening_body("Engineering"), "2": _opening_body("Logistics")}
    )
    scraper.have_details = (
        set()
    )  # arms the gate (off for every non-pipeline caller otherwise)
    raw = scraper.fetch_raw()

    # the tech posting's detail was fetched; the non-tech posting's never was
    assert _detail_urls(fetcher) == ["https://acme.bamboohr.com/careers/1/detail"]
    assert raw["details"] == {"1": {"departmentLabel": "Engineering"}}


@pytest.mark.parametrize("async_fanout_switch", ["1", "0"])
def test_fetch_raw_fetches_every_detail_outside_the_pipeline_on_either_transport(
    monkeypatch, async_fanout_switch
):
    """`have_details is None` (the default for a direct caller) disarms the gate entirely — the
    same contract every other gated scraper honours — and the detail request is the same bare
    GET whichever transport carries it."""
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", async_fanout_switch)
    scraper, fetcher = _acme_board(
        {"1": _opening_body("Engineering"), "2": _opening_body("Logistics")}
    )
    assert scraper.have_details is None
    raw = scraper.fetch_raw()

    assert sorted(_detail_urls(fetcher)) == [
        "https://acme.bamboohr.com/careers/1/detail",
        "https://acme.bamboohr.com/careers/2/detail",
    ]
    assert raw["details"] == {
        "1": {"departmentLabel": "Engineering"},
        "2": {"departmentLabel": "Logistics"},
    }
    detail_request = next(
        request for request in fetcher.requests if request.url.endswith("/detail")
    )
    assert detail_request.method == "GET"
    assert detail_request.kwargs["timeout"] == 30


def test_every_lost_detail_is_labelled_by_what_lost_it():
    """An unparseable body and an empty `jobOpening` each read differently in the gap line, and
    neither as a refusal, so a refused Board is never mistaken for one whose pages are empty."""
    scraper, _fetcher = _acme_board(
        {"1": "<html>not json</html>", "2": json.dumps({"result": {}})}
    )
    raw = scraper.fetch_raw()

    assert raw["details"] == {}
    assert scraper.detail_losses == {
        "unparseable detail JSON": 1,
        "empty jobOpening": 1,
    }
    # Both Jobs still ship, each with the listing's own department.
    jobs = scraper.parse(raw, SCRAPED_AT)
    assert [(job.title, job.department) for job in jobs] == [
        ("Backend Engineer", "Engineering"),
        ("Warehouse Associate", "Logistics"),
    ]


# --- _department_map(): the widget's department blocks -----------------------------------------


def test_department_map_reads_every_position_in_the_fixture():
    assert _department_map(_WIDGET) == {
        "331": "Drivers",
        "332": "Drivers",
        "335": "FNOL",
        "336": "Motomine Parts Sales & Operations",  # &amp; decoded
        "334": "MotoMine Projects Sales Team",
    }


def test_department_map_has_no_entry_for_a_position_before_any_department_header():
    page = (
        '<div class="BambooHR-ATS-board">'
        '<li id="bhrPositionID_9"><a href="#">Straggler</a></li>'
        "</div>"
    )
    assert _department_map(page) == {}
