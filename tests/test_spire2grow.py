"""Tests for `headstart.scrapers.spire2grow`.

`spire2grow_search.json` is the `/requisition/_search` envelope trimmed to seven real postings
captured 2026-09-30 from the three hiring workspaces (Myntra, Tata Communications, Spire), with
each row's `recruiter` (a named person's email) dropped. They cover every `jobType` observed
(ONSITE, HYBRID, `NA`, absent), every `employmentType` (FULL_TIME, PART_TIME, APPRENTICESHIP), a
bound that is not whole years (41-66 months) and a posting in two places. Each workspace's rows
are parsed under one host here, since `parse` never reads the row's workspace.

Every assertion pins something measured in `docs/spire2grow/2026-09-30_career-api-measurement.md`.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest
from fake_fetcher import FakeFetcher, FakeResponse

from headstart.jobs import experience
from headstart.scrapers import spire2grow
from headstart.scrapers.registry import detail_pass_atses, get_scraper
from headstart.scrapers.spire2grow import Spire2GrowScraper
from headstart.search_filters import employment_type_filter

FIXTURES = Path(__file__).parent / "fixtures"
SCRAPED_AT = "2026-01-01T00:00:00+00:00"
HOST = "jobs.myntra.com"
WORKSPACE = "MYNTRA-93as3"


def _envelope() -> dict:
    with open(FIXTURES / "spire2grow_search.json", encoding="utf-8") as fh:
        return json.load(fh)


def _jobs() -> dict:
    jobs = get_scraper("spire2grow", HOST).parse(_envelope(), SCRAPED_AT)
    return {job.id.rsplit(":", 1)[1]: job for job in jobs}


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch):
    """The process-wide pacer spaces real searches 31 s apart; tests get a fresh, instant one."""
    monkeypatch.setattr(Spire2GrowScraper, "pacer", spire2grow.Pacer(0))


def _route(pages: list[dict], *, workspace_status: int = 200, search_statuses=()):
    """A fake API: the domain lookup, then `_search` pages in order, each optionally preceded by
    the listed statuses (a 429, say) before the page itself."""
    queued = list(search_statuses)
    served = iter(pages)

    def route(method, url, kwargs):
        if "/workspaceId?" in url:
            if workspace_status == 404:
                return FakeResponse(
                    404,
                    '{"errorMessages":["No Workspace Found for the domain name :: x"]}',
                )
            return FakeResponse(workspace_status, WORKSPACE)
        assert kwargs["headers"]["workspaceid"] == WORKSPACE
        if queued:
            return FakeResponse(
                queued.pop(0),
                "Too many requests",
                headers={"X-Rate-Limit-Retry-After-Seconds": "0"},
            )
        return FakeResponse(text=json.dumps(next(served)))

    return route


def _scraper(route) -> Spire2GrowScraper:
    return Spire2GrowScraper(HOST, fetcher=FakeFetcher(route))


def test_it_is_registered_without_a_detail_pass():
    assert isinstance(get_scraper("spire2grow", HOST), Spire2GrowScraper)
    assert "spire2grow" not in detail_pass_atses()


def test_the_slug_is_the_lower_cased_career_host_from_either_column():
    assert Spire2GrowScraper.slug_from("jobs.myntra.com", "") == HOST
    assert Spire2GrowScraper.slug_from("JOBS.Myntra.com", "") == HOST
    assert Spire2GrowScraper.slug_from("", "https://jobs.myntra.com/home") == HOST


def test_the_job_url_is_the_hosts_jobs_page_by_display_id_and_matches_the_shape():
    jobs = _jobs()
    assert jobs["4524734408"].url == "https://jobs.myntra.com/jobs/4524734408"
    for job in jobs.values():
        assert re.fullmatch(Spire2GrowScraper.url_shape, job.url), job.url
    assert jobs["S-049"].url == "https://jobs.myntra.com/jobs/S-049"


def test_a_full_row_maps_every_field():
    job = _jobs()["4524734408"]
    assert job.id == "spire2grow:jobs.myntra.com:4524734408"
    assert job.title == "Technical Lead-Backend Engineering"
    assert job.location == "Bangalore, Karnataka, India"
    assert job.department == "SF (F1216)"
    assert job.employment_type == "Full-Time"
    assert job.salary is None
    assert job.description and "<" not in job.description
    # `jobPosting.startDate` in epoch ms, served as UTC ISO-8601.
    assert job.posted_at is not None and job.posted_at.endswith("+00:00")


def test_posted_at_is_the_posting_start_date():
    row = _envelope()["entities"][0]
    start = row["jobPosting"]["startDate"]
    job = _jobs()[row["displayId"]]
    assert datetime.fromisoformat(job.posted_at).timestamp() * 1000 == start


def test_remote_reads_the_stated_workplace_and_hybrid_is_neither():
    jobs = _jobs()
    assert jobs["1845624696"].remote is False  # ONSITE
    assert jobs["4524734408"].remote is None  # HYBRID
    # `NA` and absent state nothing, so the location decides: a city is not remote.
    assert jobs["6446684148"].remote is False
    assert jobs["6878339879"].remote is False


def test_a_remote_workplace_is_remote():
    envelope = _envelope()
    envelope["entities"][0]["jobType"] = "REMOTE"
    job = get_scraper("spire2grow", HOST).parse(envelope, SCRAPED_AT)[0]
    assert job.remote is True


def test_every_location_is_joined_without_repeats():
    envelope = _envelope()
    row = next(r for r in envelope["entities"] if r["displayId"] == "S-049")
    row["jobLocation"][1]["fqLocationName"] = "Pune, Maharashtra, India"
    job = get_scraper("spire2grow", HOST).parse(envelope, SCRAPED_AT)[-1]
    assert job.location == "Bengaluru, Karnataka, India; Pune, Maharashtra, India"
    row["jobLocation"][1]["fqLocationName"] = "Bengaluru, Karnataka, India"
    job = get_scraper("spire2grow", HOST).parse(envelope, SCRAPED_AT)[-1]
    assert job.location == "Bengaluru, Karnataka, India"


def test_a_posting_with_no_department_has_none():
    assert _jobs()["S-049"].department is None


def test_experience_is_the_months_bounds_the_field_parser_rounds_outward():
    jobs = _jobs()
    assert jobs["6446684148"].experience == "41-66 months"
    span = experience.from_field(jobs["6446684148"].experience)
    assert (span.min_years, span.max_years) == (3, 6)
    span = experience.from_field(jobs["4524734408"].experience)
    assert (span.min_years, span.max_years) == (5, 7)
    span = experience.from_field(jobs["1845624696"].experience)
    assert (span.min_years, span.max_years) == (0, 0)


def test_employment_types_reach_the_filter():
    jobs = _jobs()
    assert jobs["7729170962"].employment_type == "Part-Time"
    assert employment_type_filter.flags("Part-Time")["is_part_time"]
    assert jobs["1845624696"].employment_type == "Apprenticeship"


def test_an_unobserved_employment_type_passes_through():
    envelope = _envelope()
    envelope["entities"][0]["employmentType"] = "CONTRACT"
    job = get_scraper("spire2grow", HOST).parse(envelope, SCRAPED_AT)[0]
    assert job.employment_type == "CONTRACT"


def test_a_row_without_id_or_title_is_skipped():
    envelope = _envelope()
    envelope["entities"][0]["displayId"] = None
    envelope["entities"][1]["jobTitle"] = "  "
    jobs = get_scraper("spire2grow", HOST).parse(envelope, SCRAPED_AT)
    assert len(jobs) == len(envelope["entities"]) - 2


def test_fetch_resolves_the_workspace_and_reads_the_board_in_one_search():
    envelope = _envelope()
    fetcher = FakeFetcher(_route([envelope]))
    scraper = Spire2GrowScraper(HOST, fetcher=fetcher)
    jobs = scraper.fetch()
    assert len(jobs) == len(envelope["entities"])
    assert scraper.truncated is None
    lookup, search = fetcher.requests
    assert lookup.url.endswith("/workspaceId?domain=jobs.myntra.com")
    assert "page=1" in search.url and f"size={spire2grow._SIZE}" in search.url
    assert search.kwargs["headers"]["workspaceid"] == WORKSPACE


def test_an_unknown_host_is_a_gone_board():
    scraper = _scraper(_route([], workspace_status=404))
    with pytest.raises(Exception, match="410"):
        scraper.fetch()


def test_the_walk_pages_until_total_and_drops_repeated_rows():
    rows = _envelope()["entities"]
    first = {"entities": rows[:4], "total": len(rows)}
    # The small-page instability measured on Tata Communications: a row served twice.
    second = {"entities": [rows[3], *rows[4:]], "total": len(rows)}
    scraper = _scraper(_route([first, second]))
    jobs = scraper.fetch()
    assert len(jobs) == len(rows)
    assert scraper.truncated is None


def test_a_walk_short_of_total_is_marked_truncated():
    rows = _envelope()["entities"]
    pages = [{"entities": rows[:3], "total": 40}, {"entities": [], "total": 40}]
    scraper = _scraper(_route(pages))
    scraper.fetch()
    assert scraper.truncated and "read 3 of 40" in scraper.truncated


def test_the_result_window_ends_the_walk_as_truncated(monkeypatch):
    monkeypatch.setattr(spire2grow, "_WINDOW", 2 * spire2grow._SIZE)
    rows = _envelope()["entities"]
    pages = [{"entities": rows[:2], "total": 50}, {"entities": rows[2:4], "total": 50}]
    scraper = _scraper(_route(pages))
    scraper.fetch()
    assert scraper.truncated and "result window" in scraper.truncated


def test_a_429_rests_and_asks_again():
    envelope = _envelope()
    fetcher = FakeFetcher(_route([envelope], search_statuses=[429]))
    scraper = Spire2GrowScraper(HOST, fetcher=fetcher)
    assert len(scraper.fetch()) == len(envelope["entities"])
    assert len(fetcher.requests) == 3


def test_a_429_that_never_clears_fails_the_board():
    scraper = _scraper(_route([], search_statuses=[429] * spire2grow._TRIES))
    with pytest.raises(spire2grow._RateLimited):
        scraper.fetch()
