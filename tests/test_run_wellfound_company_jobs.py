"""Company-page parsing — the surface that lifts the role board's 3-jobs-per-company cap.

The live gap this pins: role boards return at most 3 `highlightedJobListings` per company, so
the sweep captured 19 Deepgram jobs against 78 real ones. The company page has no such cap, and
these tests run against the real captured page to keep its parse honest.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "scrape"))

# The module imports pydoll at module scope; skip cleanly where it isn't installed (CI
# installs base deps only).
pytest.importorskip("pydoll")

import asyncio
import json

import run_wellfound_company_jobs as cj
from run_wellfound_company_jobs import (
    PER_PAGE,
    _job_type,
    parse_company_page,
    scrape_company,
)

ARTIFACT = ROOT / "tests" / "fixtures" / "wellfound_company_page.html"


@pytest.fixture(scope="module")
def deepgram():
    if not ARTIFACT.exists():
        pytest.skip(f"capture missing: {ARTIFACT}")
    html = ARTIFACT.read_text(encoding="utf-8")
    return parse_company_page(html, "deepgram", "2026-08-05T00:00:00+00:00")


def test_total_count_far_exceeds_the_boards_cap(deepgram):
    """The whole point: the company page reports every job, not min(3, n)."""
    _, total, _found = deepgram
    assert total >= 77
    assert total > 3


def test_returns_a_full_page_of_listings(deepgram):
    jobs, _total, _found = deepgram
    assert (
        len(jobs) == 10
    )  # this capture was taken with first:10; live pages return PER_PAGE
    assert PER_PAGE == 20


def test_every_listing_has_the_id_and_slug_the_detail_url_needs(deepgram):
    """Stage 3 builds /jobs/{id}-{slug} from these; a missing slug silently breaks the chain."""
    jobs, _total, _found = deepgram
    for j in jobs:
        assert j["id"].startswith("wellfound:deepgram:")
        assert j["id"].split(":")[2].isdigit()
        assert j["url"].startswith("https://wellfound.com/jobs/")
        assert not j["url"].endswith("/")


def test_job_type_is_normalized_to_the_boards_spelling():
    """The company page emits `full_time`, the board `full-time`. One value, one spelling."""
    assert _job_type("full_time") == "full-time"
    assert _job_type("part_time") == "part-time"
    assert _job_type(None) is None


def test_parsed_rows_use_the_boards_job_type_spelling(deepgram):
    jobs, _total, _found = deepgram
    assert {j["job_type"] for j in jobs} <= {
        "full-time",
        "part-time",
        "contract",
        "internship",
        None,
    }
    assert not any("_" in (j["job_type"] or "") for j in jobs)


def test_description_is_only_the_snippet_and_is_flagged_as_such(deepgram):
    """Stage 3 keys off desc_source to know what still needs a detail fetch."""
    jobs, _total, _found = deepgram
    assert {j["desc_source"] for j in jobs} == {"snippet"}
    assert all(len(j["description"] or "") < 1000 for j in jobs)


def test_company_name_resolves_rather_than_falling_back_to_the_slug(deepgram):
    jobs, _total, _found = deepgram
    assert {j["company"] for j in jobs} == {"Deepgram"}


def test_remote_only_listing_still_reports_a_location(deepgram):
    """locationNames is empty for remote-only roles; acceptedRemoteLocationNames covers it."""
    jobs, _total, _found = deepgram
    assert all(j["location"] for j in jobs)


def test_garbage_html_parses_to_nothing_and_reports_not_found():
    """found=False, not "this company has no jobs" — the caller must retry, not retire it."""
    assert parse_company_page("<html>no next data</html>", "x", "t") == ([], 0, False)


# --- pagination must not depend on totalCount -------------------------------------------------
#
# Live on `staple-3` the company page reported totalCount 0 while serving 20 listings. Deriving
# the page count from that hint stopped after page 1 and silently dropped the rest — the exact
# truncation this whole module exists to remove. These pin the walk to what pages return.


def _page(
    slug: str, job_ids: list[int], total: int | None, others: list[int] = ()
) -> str:
    """A minimal company page: the subject's listings, an optional totalCount, and `others` —
    listings belonging to a *recommended* startup, which must never be claimed as the subject's."""
    subject_key = f"Startup:{slug}"
    cache: dict = {
        f"JobListing:{i}": {
            "__typename": "JobListing",
            "id": str(i),
            "slug": f"job-{i}",
            "title": "Backend Engineer",
            "locationNames": ["Remote"],
            "jobType": "full_time",
            "startup": {"__ref": subject_key},
        }
        for i in job_ids
    }
    for i in others:
        cache[f"JobListing:{i}"] = {
            "__typename": "JobListing",
            "id": str(i),
            "slug": f"other-{i}",
            "title": "Senior Mechanical Engineer",
            "locationNames": ["Victoria"],
            "jobType": "full_time",
            "startup": {"__ref": "Startup:someone-else"},
        }
    cache["Startup:someone-else"] = {
        "__typename": "Startup",
        "slug": "someone-else",
        "name": "Someone Else",
    }
    startup: dict = {"__typename": "Startup", "slug": slug, "name": slug.title()}
    conn: dict = {
        "__typename": "JobListingConnection",
        "edges": [
            {"__typename": "JobListingEdge", "node": {"__ref": f"JobListing:{i}"}}
            for i in job_ids
        ],
    }
    if total is not None:
        conn["totalCount"] = total
    startup['jobListingsConnection({"first":20})'] = conn
    cache[subject_key] = startup
    payload = {"props": {"pageProps": {"apolloState": {"data": cache}}}}
    return (
        '<script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(payload)
        + "</script>"
    )


def test_recommended_companies_listings_are_not_claimed_as_this_companys():
    """The staple-3 corruption: a company page caches ~20 recommended startups, and scooping
    every JobListing on it filed Checkfront's 'Staff Software Engineer' under Staple."""
    html = _page("acme", [1, 2], total=2, others=[900, 901])
    jobs, _total, found = parse_company_page(html, "acme", "t")
    assert found is True
    assert [j["id"] for j in jobs] == ["wellfound:acme:1", "wellfound:acme:2"]
    assert {j["company"] for j in jobs} == {"Acme"}
    assert not any("900" in j["id"] or "901" in j["id"] for j in jobs)


def test_page_that_does_not_render_the_requested_company_claims_nothing():
    """A redirect or dead slug must yield zero jobs, not the recommended companies' listings."""
    html = _page("acme", [1, 2], total=2, others=[900])
    jobs, total, found = parse_company_page(html, "not-on-this-page", "t")
    assert jobs == [] and total == 0 and found is False


def test_backref_fallback_when_the_connection_has_no_edges():
    html = _page("acme", [1, 2], total=2, others=[900])
    stripped = html.replace('"edges"', '"edgesRemoved"')
    jobs, _total, _found = parse_company_page(stripped, "acme", "t")
    assert [j["id"] for j in jobs] == ["wellfound:acme:1", "wellfound:acme:2"]


class _Rows:
    def __init__(self):
        self.rows = []

    def writerow(self, r):
        self.rows.append(r)

    def flush(self):
        pass


def _walk(monkeypatch, pages: dict[int, str], seen: set[str] | None = None) -> _Rows:
    """Run scrape_company against a canned page->html map. `seen` pre-seeds the dedupe set the
    way a resumed run does, so resume behaviour is testable without touching the filesystem."""
    seen = set() if seen is None else seen

    async def fake_load(tab, url, browser=None, blocked=None):
        n = int(url.rsplit("page=", 1)[1])
        return pages.get(n, _page("acme", [], 0))

    async def no_pause(tab):
        return None

    monkeypatch.setattr(cj, "_load_page", fake_load)
    monkeypatch.setattr(cj, "_human_pause", no_pause)
    sink = _Rows()
    added, complete = asyncio.run(
        scrape_company(None, None, "acme", "t", sink, sink, seen, 0.0, 0.0, False)
    )
    sink.added, sink.complete = added, complete
    return sink


def test_walks_past_page_one_when_total_count_is_missing(monkeypatch):
    """The staple-3 regression: totalCount absent must not mean 'one page'."""
    pages = {
        1: _page("acme", list(range(20)), None),
        2: _page("acme", list(range(20, 40)), None),
        3: _page("acme", [], None),
    }
    sink = _walk(monkeypatch, pages)
    assert len(sink.rows) == 40
    assert sink.complete is True


def test_walks_past_page_one_when_total_count_is_zero(monkeypatch):
    pages = {
        1: _page("acme", list(range(20)), 0),
        2: _page("acme", list(range(20, 35)), 0),  # short page = last page
    }
    sink = _walk(monkeypatch, pages)
    assert len(sink.rows) == 35


def test_stops_when_a_page_repeats_rather_than_looping_forever(monkeypatch):
    """Past the end the role board wraps to page 1; a repeat must terminate the walk."""
    first = _page("acme", list(range(20)), None)
    pages = {1: first, 2: first, 3: first}
    sink = _walk(monkeypatch, pages)
    assert len(sink.rows) == 20
    assert sink.complete is True


def test_undercounting_total_does_not_truncate_the_walk(monkeypatch):
    """totalCount is display-only. Deepgram's page disagreed with itself (77 vs 78); capping
    pages by it would drop the tail — the same trusted-but-wrong count the board truncated on."""
    pages = {
        1: _page("acme", list(range(20)), 25),  # claims 25 -> 2 pages
        2: _page("acme", list(range(20, 40)), 25),  # but a full page: keep going
        3: _page("acme", list(range(40, 45)), 25),
    }
    sink = _walk(monkeypatch, pages)
    assert len(sink.rows) == 45


# --- resume must not stop at the first already-captured page ----------------------------------
#
# Caught in review: the walk used to break when a page added no *new* rows. On resume the global
# `seen` already holds the earlier pages' ids, so page 2 added nothing, the walk broke, and every
# page beyond it was never re-fetched — then the company was marked done permanently.


def test_resume_walks_through_already_captured_pages_to_the_unseen_tail(monkeypatch):
    pages = {
        1: _page("acme", list(range(20)), None),
        2: _page("acme", list(range(20, 40)), None),
        3: _page("acme", list(range(40, 55)), None),  # short page = last
    }
    already = {f"wellfound:acme:{i}" for i in range(40)}  # pages 1-2 captured last run
    sink = _walk(monkeypatch, pages, seen=already)
    assert [r["id"] for r in sink.rows] == [
        f"wellfound:acme:{i}" for i in range(40, 55)
    ]
    assert sink.complete is True


def test_a_soft_blocked_company_is_not_reported_complete(monkeypatch):
    """Only a walk that reached the end may be recorded done; otherwise --append retires a
    company that never finished, keeping whatever partial set it happened to get."""
    pages = {1: _page("acme", list(range(20)), None), 2: "<html>blocked</html>"}
    sink = _walk(monkeypatch, pages)
    assert sink.complete is False
    assert len(sink.rows) == 20


def test_a_company_blocked_on_page_one_is_not_reported_complete(monkeypatch):
    sink = _walk(monkeypatch, {1: "<html>blocked</html>"})
    assert sink.complete is False
    assert sink.rows == []


def test_page_one_that_does_not_render_the_company_is_not_reported_complete(
    monkeypatch,
):
    """Caught in review: a page that loads fine but doesn't render this company (redirect,
    rename, garbled) yielded 0 jobs AND complete=True, so the done-file retired the company
    permanently having never read it. It must be retried instead."""
    pages = {1: _page("someone-else", [1, 2], total=2)}  # renders, but not `acme`
    sink = _walk(monkeypatch, pages)
    assert sink.complete is False
    assert sink.rows == []


def test_mid_walk_page_that_stops_rendering_the_company_is_not_reported_complete(
    monkeypatch,
):
    """Same hole one page in: the tail is unread, so this is truncation, not an ending."""
    pages = {
        1: _page("acme", list(range(20)), None),
        2: _page("someone-else", [900], total=1),
    }
    sink = _walk(monkeypatch, pages)
    assert sink.complete is False
    assert len(sink.rows) == 20  # page 1 is still banked


def test_a_company_with_no_openings_is_complete_not_retried(monkeypatch):
    """The other side of the same coin: staple-3 is on its page and genuinely has no jobs.
    That is a finished walk, not a failure — retrying it every run would be pure waste."""
    pages = {1: _page("acme", [], total=0)}
    sink = _walk(monkeypatch, pages)
    assert sink.complete is True
    assert sink.rows == []
