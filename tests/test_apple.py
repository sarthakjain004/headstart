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

from headstart.models import html_to_text
from headstart.scrapers.apple import AppleScraper
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


# --------------------------------------------------------------------------- the URL contract


def test_the_slug_is_fixed_regardless_of_input():
    """ADR-0139: a Single source Board's slug is never discovered. A caller can pass anything;
    the scraper's own class attributes (`ats`) stay pinned to Apple's board."""
    scraper = get_scraper("apple", SLUG)
    assert scraper.ats == "apple"
    assert scraper.slug == SLUG
    assert scraper.board_key() == f"apple:{SLUG}"


def test_company_defaults_to_apple_not_the_slug():
    """A Single source scraper has exactly one company — hardcoded rather than resolved from a
    scraped page title, unlike the multi-tenant ATSes' `resolve_company` path."""
    assert get_scraper("apple", SLUG).company == "Apple"
    assert get_scraper("apple", SLUG, "Apple Inc.").company == "Apple Inc."


def test_alias_key_is_the_slug_itself():
    """ADR-0139: no sibling tenant to alias against, so the base class's redirect-following
    default (built for vanity-hostname platforms) is overridden rather than making a live probe
    that could only ever find nothing."""
    assert AppleScraper(SLUG).alias_key() == SLUG


def test_the_search_request_carries_an_empty_query_and_filters(monkeypatch):
    """An empty query/filters returns the whole board (module docstring) — the scraper must not
    narrow it with a keyword or team filter, since the tech gate is a post-hoc filter
    (`headstart.tech_filter`), not something scraped-in."""
    import headstart.scrapers.apple as apple_module

    calls = []

    def fake_fetch(method, url, **kwargs):
        calls.append((method, url, kwargs.get("json")))
        raise RuntimeError("stop after first call")

    monkeypatch.setattr(apple_module.http, "fetch", fake_fetch)
    scraper = _scraper()
    try:
        scraper._listing()
    except RuntimeError:
        pass

    assert len(calls) == 1
    method, url, body = calls[0]
    assert method == "POST"
    assert url == "https://jobs.apple.com/api/v1/search"
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
    assert scraper._detail_url(PIPE_ID) == (
        f"https://{SLUG}/api/v1/jobDetails/200313970"
    )
    assert scraper._detail_url(REQ_ID) == (f"https://{SLUG}/api/v1/jobDetails/{REQ_ID}")


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


def test_employment_type_comes_from_the_detail_pass_when_present():
    jobs = _jobs()
    assert jobs[REQ_ID].employment_type == "Intern"
    # Measured: not every detail payload states it (module docstring).
    assert jobs[PIPE_ID].employment_type is None


def test_a_missing_detail_payload_leaves_the_job_listed_with_no_description():
    """Enrichment, not a hard dependency — unlike a genuinely truncated teaser (Oracle), Apple's
    listing carries no posting-specific text at all, so there is nothing to fall back to."""
    raw = {"searchResults": _listing(), "details": {}}
    jobs = {j.id.rsplit(":", 1)[1]: j for j in _scraper().parse(raw, SCRAPED_AT)}
    assert len(jobs) == 2
    assert jobs[REQ_ID].description is None
    assert jobs[REQ_ID].employment_type is None
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


def test_the_scraper_declares_a_detail_pass():
    assert AppleScraper.has_detail_pass is True
    from headstart.scrapers.registry import detail_pass_atses

    assert "apple" in detail_pass_atses()
