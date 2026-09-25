"""Tests for `headstart.scrapers.apple`.

The fixtures are two real postings captured live 2026-09-11: `apple_listing.json` is the search
API's `res` envelope trimmed to one `PIPE` row (an evergreen Retail role, India) and one `REQ` row
(a specific Hardware requisition, China); `apple_details.json` maps each listing `id` to its
`jobDetails` response. They were chosen to cover both listing types, a posting with
`employmentType` stated and one without, and to pin the `PIPE-` prefix stripped from the id to
build the detail endpoint's `jobNumber`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fake_fetcher import FakeFetcher, FakeResponse, Route

from headstart.models import html_to_text
from headstart.network import fanout_stats
from headstart.scrapers.apple import _SEARCH_URL, AppleScraper
from headstart.scrapers.registry import get_scraper

FIXTURES = Path(__file__).parent / "fixtures"
SCRAPED_AT = "2026-01-01T00:00:00+00:00"
SLUG = "jobs.apple.com"
PIPE_ID, REQ_ID = "PIPE-200313970", "200681917-3715"


def _listing():
    with open(FIXTURES / "apple_listing.json", encoding="utf-8") as fh:
        return json.load(fh)["res"]["searchResults"]


def _details():
    with open(FIXTURES / "apple_details.json", encoding="utf-8") as fh:
        return {k: v["res"] for k, v in json.load(fh).items()}


def _raw():
    return {"searchResults": _listing(), "details": _details()}


def _scraper():
    return get_scraper("apple", SLUG)


def _jobs():
    return {j.id.rsplit(":", 1)[1]: j for j in _scraper().parse(_raw(), SCRAPED_AT)}


class _TransportRecordingFetcher(FakeFetcher):
    """A :class:`FakeFetcher` that also counts the requests sent on the multiplexed path, so a
    test can say which transport the Detail pass rode."""

    def __init__(self, route: Route) -> None:
        super().__init__(route)
        self.multiplexed_requests = 0

    async def fetch_async(
        self, session: Any, method: str, url: str, **kwargs: Any
    ) -> FakeResponse:
        self.multiplexed_requests += 1
        return self.fetch(method, url, **kwargs)


def _serve_board(
    listed: list[dict], details_by_job_number: dict[str, dict]
) -> tuple[AppleScraper, _TransportRecordingFetcher]:
    """An Apple scraper whose search POST answers ``listed`` as one short page and whose detail
    GETs answer from ``details_by_job_number``; an unknown job number is Apple's own 404."""

    def route(method: str, url: str, kwargs: dict) -> FakeResponse:
        if method == "POST" and url == _SEARCH_URL:
            page = {"searchResults": listed, "totalRecords": len(listed)}
            return FakeResponse(text=json.dumps({"res": page}))
        job_number = url.rsplit("/", 1)[1]
        if job_number not in details_by_job_number:
            return FakeResponse(404, '{"error":"jobsite.general.serviceError"}')
        return FakeResponse(text=json.dumps({"res": details_by_job_number[job_number]}))

    fetcher = _TransportRecordingFetcher(route)
    return AppleScraper(SLUG, fetcher=fetcher), fetcher


# --------------------------------------------------------------------------- the URL contract


def test_the_slug_is_fixed_regardless_of_input():
    """ADR-0139: a Single source Board's slug is never discovered. A caller can pass anything;
    the scraper's own class attributes (`ats`) stay pinned to Apple's board."""
    scraper = get_scraper("apple", SLUG)
    assert scraper.ats == "apple"
    assert scraper.slug == SLUG
    assert scraper.board_key() == f"apple:{SLUG}"


def test_company_is_apple_even_when_the_ledger_passes_a_hostname():
    """A Single source scraper has exactly one company — declared as `BaseScraper.COMPANY` rather
    than resolved from a scraped page title, unlike the multi-tenant ATSes' `resolve_company` path.

    This used to assert the opposite for a passed name (`"Apple Inc."` outranking `"Apple"`), and
    that rule is gone deliberately: the only caller that passes one is the pipeline, which passes
    `CompanyRef.name` — and for a Board discovered by hostname that IS the hostname, so the
    override fired on `jobs.apple.com` and served it to the UI. There is exactly one company here,
    so nothing a caller supplies can be better informed than the class itself.
    """
    assert get_scraper("apple", SLUG).company == "Apple"
    assert get_scraper("apple", SLUG, SLUG).company == "Apple"
    assert get_scraper("apple", SLUG, "Apple Inc.").company == "Apple"


def test_alias_key_is_the_slug_itself():
    """ADR-0139: no sibling tenant to alias against, so the base class's redirect-following
    default (built for vanity-hostname platforms) is overridden rather than making a live probe
    that could only ever find nothing."""
    assert AppleScraper(SLUG).alias_key() == SLUG


def test_the_search_request_carries_an_empty_query_and_filters():
    """An empty query/filters returns the whole board (module docstring) — the scraper must not
    narrow it with a keyword or team filter, since the tech gate is a post-hoc filter
    (`headstart.tech_filter`), not something scraped-in."""
    fetcher = FakeFetcher(
        lambda method, url, kwargs: RuntimeError("stop after first call")
    )
    scraper = AppleScraper(SLUG, fetcher=fetcher)
    with pytest.raises(RuntimeError):
        scraper._listing()

    (request,) = fetcher.requests
    assert request.method == "POST"
    assert request.url == "https://jobs.apple.com/api/v1/search"
    body = request.kwargs["json"]
    assert body["query"] == ""
    assert body["filters"] == {}
    assert body["page"] == 1


def test_the_job_url_is_the_details_page():
    job = _jobs()[REQ_ID]
    assert job.url == (
        "https://jobs.apple.com/en-us/details/200681917/"
        "apple-vision-pro-hardware-system-ee-intern"
    )


def test_the_detail_url_strips_the_pipe_prefix_but_not_a_req_id():
    scraper = _scraper()
    assert scraper.detail_request({"id": PIPE_ID}).url == (
        f"https://{SLUG}/api/v1/jobDetails/200313970"
    )
    assert scraper.detail_request({"id": REQ_ID}).url == (
        f"https://{SLUG}/api/v1/jobDetails/{REQ_ID}"
    )


# ------------------------------------------------------------------------------ field mapping


def test_the_core_identity_fields_come_from_the_listing():
    job = _jobs()[REQ_ID]
    assert job.id == f"apple:{SLUG}:{REQ_ID}"
    assert job.ats == "apple"
    assert job.company == "Apple"
    assert job.title == "Apple Vision Pro Hardware System EE Intern"
    assert job.department == "Hardware"
    assert job.location == "Shanghai"
    assert job.posted_at


def test_description_is_built_from_the_detail_body_not_the_listing_summary():
    """`jobSummary` is team-level boilerplate present on both listing and detail (module
    docstring) — the description must come from the detail-only fields instead."""
    job = _jobs()[REQ_ID]
    listing_summary = next(r for r in _listing() if r["id"] == REQ_ID)["jobSummary"]
    detail = _details()[REQ_ID]

    assert job.description is not None
    assert listing_summary not in job.description
    assert "Learn EE design" in job.description  # detail's `description`
    assert "Bachelor or master degree" in job.description  # `minimumQualifications`
    assert "Hands-on experience on PCB" in job.description  # `preferredQualifications`
    assert html_to_text(detail["description"]) in job.description


def test_employment_type_comes_from_the_listing_not_the_detail():
    """`standardWeeklyHours` (both fixture rows: 40) drives Full-time/Part-time; a title
    carrying the whole word "Intern" (the REQ row) wins over that split (module docstring)."""
    jobs = _jobs()
    assert jobs[REQ_ID].employment_type == "Intern"  # title says "... EE Intern"
    assert jobs[PIPE_ID].employment_type == "Full-time"  # no "intern" in the title, 40h


def test_employment_type_survives_a_missing_detail():
    """The whole point of moving this off the detail: it must not depend on the detail fetch
    happening at all, since the ADR-0048 skip means most runs won't make it."""
    raw = {"searchResults": _listing(), "details": {}}
    jobs = {j.id.rsplit(":", 1)[1]: j for j in _scraper().parse(raw, SCRAPED_AT)}
    assert jobs[REQ_ID].employment_type == "Intern"
    assert jobs[PIPE_ID].employment_type == "Full-time"


def test_employment_type_from_hours_below_thirty_is_part_time():
    listed = [
        {
            "id": "REQ-4",
            "positionId": "4",
            "postingTitle": "UK - Specialist: Seasonal, Part-time",
            "standardWeeklyHours": 20,
        }
    ]
    (job,) = _scraper().parse({"searchResults": listed, "details": {}}, SCRAPED_AT)
    assert job.employment_type == "Part-time"


def test_employment_type_is_none_when_hours_are_absent():
    listed = [{"id": "REQ-5", "positionId": "5", "postingTitle": "X"}]
    (job,) = _scraper().parse({"searchResults": listed, "details": {}}, SCRAPED_AT)
    assert job.employment_type is None


def test_employment_type_intern_title_does_not_false_positive_on_international():
    listed = [
        {
            "id": "REQ-6",
            "positionId": "6",
            "postingTitle": "International Trade Compliance Manager",
            "standardWeeklyHours": 40,
        }
    ]
    (job,) = _scraper().parse({"searchResults": listed, "details": {}}, SCRAPED_AT)
    assert job.employment_type == "Full-time"


def test_a_missing_detail_payload_leaves_the_job_listed_with_no_description():
    """Enrichment, not a hard dependency — unlike a genuinely truncated teaser (Oracle), Apple's
    listing carries no posting-specific text at all, so there is nothing to fall back to.
    `employment_type` is unaffected: it is sourced from the listing (see the dedicated tests)."""
    raw = {"searchResults": _listing(), "details": {}}
    jobs = {j.id.rsplit(":", 1)[1]: j for j in _scraper().parse(raw, SCRAPED_AT)}
    assert len(jobs) == 2
    assert jobs[REQ_ID].description is None
    assert jobs[REQ_ID].title  # still a real, listed Job


def test_remote_reads_the_home_office_flag_directly():
    listed = [
        {
            "id": "REQ-1",
            "positionId": "1",
            "postingTitle": "X",
            "homeOffice": True,
            "team": {"teamName": "Software"},
        }
    ]
    (job,) = _scraper().parse({"searchResults": listed, "details": {}}, SCRAPED_AT)
    assert job.remote is True


def test_location_joins_multiple_places_by_name():
    listed = [
        {
            "id": "REQ-2",
            "positionId": "2",
            "postingTitle": "X",
            "locations": [
                {"name": "Austin"},
                {"name": "", "countryName": "Ireland"},
            ],
        }
    ]
    (job,) = _scraper().parse({"searchResults": listed, "details": {}}, SCRAPED_AT)
    assert job.location == "Austin; Ireland"


def test_a_row_with_no_title_is_dropped():
    listed = [{"id": "REQ-3", "positionId": "3", "postingTitle": ""}]
    jobs = _scraper().parse({"searchResults": listed, "details": {}}, SCRAPED_AT)
    assert jobs == []


# -------------------------------------------------------------------------------- pagination


class _FakeSearch:
    """Serves 20-row pages from a canned id count, recording every page asked for."""

    def __init__(self, total_ids, reported_total=None):
        self.total_ids = total_ids
        self.reported_total = total_ids if reported_total is None else reported_total
        self.pages_seen: list[int] = []

    def __call__(self, page):
        self.pages_seen.append(page)
        start = (page - 1) * 20
        # Out-of-range page: totalRecords resets to 0 (module docstring's measured quirk).
        if start >= self.total_ids:
            return {"searchResults": [], "totalRecords": 0}
        end = min(start + 20, self.total_ids)
        rows = [
            {"id": f"REQ-{i}", "positionId": str(i), "postingTitle": f"job {i}"}
            for i in range(start, end)
        ]
        return {"searchResults": rows, "totalRecords": self.reported_total}


def test_pagination_walks_every_page_until_a_short_page(monkeypatch):
    fake = _FakeSearch(total_ids=45)
    scraper = _scraper()
    monkeypatch.setattr(scraper, "_search_page", fake)
    items = scraper._listing()
    assert len(items) == 45
    assert fake.pages_seen == [1, 2, 3]
    assert scraper.truncated is None


def test_a_row_repeated_across_pages_is_deduped_by_id(monkeypatch):
    """Measured live 2026-09-12: a `sort: newest` walk over the real board sees a handful of ids
    repeat mid-scrape (module docstring) as the board reshuffles underneath a multi-minute walk.
    `_listing` must not double-count them."""
    scraper = _scraper()

    def fake(page):
        if page == 1:
            rows = [{"id": f"REQ-{i}", "postingTitle": f"job {i}"} for i in range(20)]
        elif page == 2:
            # REQ-19 repeats here, shifted by the board's own reordering; nothing new after it.
            rows = [{"id": "REQ-19", "postingTitle": "job 19"}]
        else:
            rows = []
        return {"searchResults": rows, "totalRecords": 20}

    monkeypatch.setattr(scraper, "_search_page", fake)
    items = scraper._listing()
    assert len(items) == 20
    assert len({i["id"] for i in items}) == 20
    assert scraper.truncated is None


def test_the_total_going_to_zero_on_an_out_of_range_page_does_not_truncate(monkeypatch):
    """The measured quirk this scraper is built around: unlike Oracle, the terminator is a short
    page, not the total — because the total itself is unreliable past the end."""
    fake = _FakeSearch(total_ids=20)  # ends exactly on a page boundary
    scraper = _scraper()
    monkeypatch.setattr(scraper, "_search_page", fake)
    items = scraper._listing()
    assert len(items) == 20
    # Only one page fetched: a 20-row page IS short of the fixed 20-size check (`< _PAGE_SIZE`
    # is false for a full page), so the walk needs a second, empty-ish page to confirm the end.
    assert fake.pages_seen == [1, 2]
    assert scraper.truncated is None


def test_a_short_walk_against_its_own_total_is_marked_truncated(monkeypatch):
    fake = _FakeSearch(total_ids=15, reported_total=100)
    scraper = _scraper()
    monkeypatch.setattr(scraper, "_search_page", fake)
    scraper._listing()
    assert scraper.truncated and "15 of 100" in scraper.truncated


def test_hitting_the_page_cap_marks_truncated(monkeypatch):
    import headstart.scrapers.apple as apple_module

    scraper = _scraper()
    monkeypatch.setattr(apple_module, "_MAX_PAGES", 2)
    monkeypatch.setattr(
        scraper,
        "_search_page",
        lambda page: {
            "searchResults": [{"id": f"REQ-{page}-{i}"} for i in range(20)],
            "totalRecords": 10**6,
        },
    )
    scraper._listing()
    assert scraper.truncated and "page cap" in scraper.truncated


def test_fetch_raw_skips_details_it_already_holds():
    """ADR-0048, re-enabled: `employment_type` moved onto the listing's `standardWeeklyHours`
    (module docstring), so the detail fetch supplies only `description` — a held Job's detail is
    skipped rather than re-fetched every run."""
    listed = [
        {
            "id": "REQ-10",
            "positionId": "10",
            "postingTitle": "Held Engineer",
            "team": {"teamName": "Software"},
            "standardWeeklyHours": 40,
        },
        {
            "id": "REQ-11",
            "positionId": "11",
            "postingTitle": "Fresh Engineer",
            "team": {"teamName": "Software"},
            "standardWeeklyHours": 40,
        },
    ]
    scraper, fetcher = _serve_board(
        listed,
        {row["id"]: {"description": f"desc-{row['id']}"} for row in listed},
    )
    scraper.have_details = {f"apple:{SLUG}:REQ-10"}
    raw = scraper.fetch_raw()

    detail_urls = fetcher.urls()[1:]
    assert detail_urls == [f"https://{SLUG}/api/v1/jobDetails/REQ-11"]  # REQ-10 is held
    assert "REQ-10" not in raw["details"]
    assert raw["details"]["REQ-11"]["description"] == "desc-REQ-11"


def test_a_detail_with_no_res_is_a_labelled_loss_and_the_job_still_ships():
    """A 200 whose body carries no `res` has no detail in it; the loss is named rather than left
    to the gap line's `unlabelled`, and the Job is still listed without a description."""
    listed = [{"id": "REQ-12", "positionId": "12", "postingTitle": "Engineer"}]

    def route(method: str, url: str, kwargs: dict) -> FakeResponse:
        if method == "POST":
            page = {"searchResults": listed, "totalRecords": 1}
            return FakeResponse(text=json.dumps({"res": page}))
        return FakeResponse(text=json.dumps({"error": "none"}))

    scraper = AppleScraper(SLUG, fetcher=FakeFetcher(route))
    raw = scraper.fetch_raw()

    assert raw["details"] == {}
    assert scraper.detail_losses == {"no res on a 200": 1}
    (job,) = scraper.parse(raw, SCRAPED_AT)
    assert job.description is None


def test_the_scraper_declares_a_detail_pass():
    assert AppleScraper.has_detail_pass is True
    from headstart.scrapers.registry import detail_pass_atses

    assert "apple" in detail_pass_atses()


# --- transport: this origin meters per connection, not per stream -----------------------------
# Measured live 2026-09-17, interleaved A/B on fresh ids, three rounds, zero non-200s either way:
# one AsyncSession at 16/32/64 streams lands at ~4.2-4.4 req/s no matter the width, while 32
# threads (32 connections) reach ~9-12 req/s. The server is not the one refusing — its own
# SETTINGS frame advertises MAX_CONCURRENT_STREAMS=128. End to end through `fetch_raw` over 400
# deduped postings: 85.5s async vs 41.1s threaded, same details, same employment_type coverage.


def test_apple_uses_connections_not_streams_for_its_detail_pass():
    """Pins the decision, because it is invisible at the call site: `fetch_raw` just asks
    `async_fanout_enabled()`. Flipping this back silently halves the Board's throughput, and this
    Board owns the scrape stage's critical path."""
    assert AppleScraper.async_fanout_enabled() is False


def test_the_width_is_the_connection_count():
    """`detail_workers`, not `detail_streams`: widening streams on the one shared connection is
    the thing measured to have no effect, so a number parked there would read as a tuning knob
    while doing nothing."""
    assert AppleScraper.detail_workers == 32
    assert AppleScraper.detail_streams is None


def _fixture_board() -> tuple[AppleScraper, _TransportRecordingFetcher]:
    """The two fixture postings, each with its real detail behind its job number."""
    details_by_job_number = {
        native_id.removeprefix("PIPE-"): detail
        for native_id, detail in _details().items()
    }
    return _serve_board(_listing(), details_by_job_number)


@pytest.mark.parametrize("async_fanout_switch", ["1", "0"])
def test_fetch_raw_takes_the_threaded_path_whatever_the_fanout_switch(
    monkeypatch, async_fanout_switch
):
    """The attribute above only matters if `fetch_raw` actually routes on it — and the operator's
    switch only ever turns the multiplexed path off, never on (ADR-0167)."""
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", async_fanout_switch)
    scraper, fetcher = _fixture_board()

    raw = scraper.fetch_raw()

    assert fetcher.multiplexed_requests == 0, (
        "the multiplexed path is the slow one for this origin"
    )
    assert set(raw["details"]) == {PIPE_ID, REQ_ID}
    assert scraper.detail_losses == {}


def test_the_threaded_detail_pass_records_its_operating_point_at_the_measured_width(
    monkeypatch,
):
    """`fan_out` records nothing on its own; the base's thread transport records the
    `concurrency apple details @N` line — the one the transport decision was read from — at the
    width the measurement was taken at, so the pass opens that many connections."""
    monkeypatch.setattr(fanout_stats, "_rows", {})
    scraper, _fetcher = _fixture_board()

    scraper.fetch_raw()

    recorded = fanout_stats.stats()
    key = ("apple details", AppleScraper.detail_workers)
    assert key in recorded, f"no operating point recorded; got {list(recorded)}"
    assert recorded[key]["items"] == len(_details())
