"""Tests for `headstart.scrapers.google`.

The fixture (`tests/fixtures/google_jobs_page1.json`) holds two real postings from
`https://www.google.com/about/careers/applications/jobs/results?hl=en_US`, captured 2026-09-11 —
already-extracted `ds:1` job arrays (not the ~1.4MB page HTML, which would be too heavy to
commit): one multi-location "Google" posting (Mountain View + Cambridge) and one single-location
"YouTube" posting, chosen to cover both the multi-location join and the per-job company field
differing from a hardcoded "Google". The page also stated `total=3414, page_size=20` at capture
time — the measurements behind every assertion are in `docs/google/2026-09-11_api-measurement.md`.
"""

from __future__ import annotations

import json
from pathlib import Path

from headstart.scrapers.google import GoogleScraper, _ds1_data
from headstart.scrapers.registry import get_scraper

FIXTURES = Path(__file__).parent / "fixtures"
SCRAPED_AT = "2026-01-01T00:00:00+00:00"
SWE_ID, YT_ID = "100397577702122182", "112497746115470022"


def _page1():
    with open(FIXTURES / "google_jobs_page1.json", encoding="utf-8") as fh:
        return json.load(fh)["page_1"]


def _scraper():
    return get_scraper("google", "careers.google.com", "Google")


def _jobs():
    raw = _page1()["jobs"]
    return {j.id.rsplit(":", 1)[1]: j for j in _scraper().parse(raw, SCRAPED_AT)}


# --------------------------------------------------------------------------- the URL contract


def test_the_slug_is_fixed_and_never_discovered():
    """ADR-0139: a Single source scraper has one tenant, forever — no `slug_from` override, the
    ledger's one hand-entered row is the only input."""
    scraper = _scraper()
    assert scraper.slug == "careers.google.com"
    assert scraper.board_key() == "google:careers.google.com"


def test_the_listing_url_hits_the_real_host_not_the_vanity_redirect():
    """careers.google.com 301s to www.google.com/about/careers/applications/... (measured
    2026-09-11) — url() targets the real host directly rather than paying that redirect on
    every request."""
    url = _scraper().url()
    assert url == (
        "https://www.google.com/about/careers/applications/jobs/results?hl=en_US"
    )


def test_the_job_url_is_the_id_only_path():
    """Verified live 2026-09-11: the id alone resolves the correct posting with no slug
    needed (og:title on the fetched page matched the listing's title), so no title-slugify
    logic is needed here."""
    job = _jobs()[SWE_ID]
    assert job.url == (
        "https://www.google.com/about/careers/applications/jobs/results/"
        f"{SWE_ID}?hl=en_US"
    )


def test_alias_key_is_the_scrapers_own_slug():
    """No sibling host to alias against for a Single source scraper (ADR-0139's per-scraper
    call) — the base default's redirect-following is skipped entirely."""
    assert _scraper().alias_key() == "careers.google.com"


# ------------------------------------------------------------------------------ field mapping


def test_the_core_identity_fields_come_from_the_listing():
    job = _jobs()[SWE_ID]
    assert job.id == f"google:careers.google.com:{SWE_ID}"
    assert job.ats == "google"
    assert job.title == "Software Engineer, Chrome Networking Security"
    assert (
        job.posted_at == "2026-08-26"
    )  # field 12, the earliest of the three timestamps


def test_company_is_read_per_job_not_hardcoded():
    """The careers site serves several Alphabet brands through one board — "Google" and
    "YouTube" both appear on page 1, and each posting's own company field is used."""
    jobs = _jobs()
    assert jobs[SWE_ID].company == "Google"
    assert jobs[YT_ID].company == "YouTube"


def test_multiple_locations_are_joined_the_way_the_sites_own_ui_joins_them():
    job = _jobs()[SWE_ID]
    assert job.location == "Mountain View, CA, USA; Cambridge, MA, USA"


def test_a_single_location_is_not_semicolon_joined():
    job = _jobs()[YT_ID]
    assert job.location == "Hyderabad, Telangana, India"


def test_remote_falls_back_to_the_location_string():
    """No explicit remote/hybrid flag was found in the payload (module docstring) — every
    posting here is a real office location, so both read not-remote."""
    jobs = _jobs()
    assert jobs[SWE_ID].remote is False
    assert jobs[YT_ID].remote is False


def test_description_combines_about_qualifications_and_responsibilities():
    job = _jobs()[SWE_ID]
    assert job.description is not None
    assert "About the job" in job.description
    assert "Minimum qualifications" in job.description
    assert "Preferred qualifications" in job.description
    assert "Responsibilities" in job.description
    assert "Chrome SWAN" in job.description  # from the "about" body
    assert "<" not in job.description  # html_to_text stripped every tag


def test_department_and_employment_type_are_unset():
    """No team/org field was found in the listing payload, and the one enum field present has
    no decoded label anywhere on the page — both stay None rather than guess."""
    job = _jobs()[SWE_ID]
    assert job.department is None
    assert job.employment_type is None


def test_a_job_missing_a_title_or_id_is_dropped_not_crashed_on():
    raw = [["", "no id"], ["123", ""], ["456", "Real Job"]]
    jobs = _scraper().parse(raw, SCRAPED_AT)
    assert len(jobs) == 1
    assert jobs[0].id == "google:careers.google.com:456"


def test_a_non_list_item_is_skipped():
    """Defensive against a shape change in Google's own internal array — one malformed row
    must not crash the whole page's parse."""
    raw = [None, "not a job", ["1", "Fine"]]
    jobs = _scraper().parse(raw, SCRAPED_AT)
    assert len(jobs) == 1


def test_the_scraper_declares_no_detail_pass():
    """Every field, description included, comes off the listing page's own ds:1 payload."""
    assert GoogleScraper.has_detail_pass is False


# -------------------------------------------------------------------------------- ds:1 extraction


def _wrap_ds1(data_json: str) -> str:
    return (
        "<html><body><script>"
        "AF_initDataCallback({key: 'ds:0', data:[[]], sideChannel: {}});"
        "</script><script>"
        f"AF_initDataCallback({{key: 'ds:1', hash: '2', data:{data_json}, sideChannel: {{}}}});"
        "</script></body></html>"
    )


def test_ds1_data_extracts_the_jobs_total_and_page_size():
    page = _wrap_ds1('[[["1", "Job One"]], null, 42, 20]')
    data = _ds1_data(page)
    assert data == [[["1", "Job One"]], None, 42, 20]


def test_ds1_data_is_none_without_the_marker():
    assert _ds1_data("<html>no callback here</html>") is None


def test_ds1_data_is_none_on_unparseable_json():
    """A template change that breaks the JSON must not raise — the caller treats this the same
    as a page carrying no data at all."""
    page = "AF_initDataCallback({key: 'ds:1', data:[not valid json, sideChannel: {}});</script>"
    assert _ds1_data(page) is None


def test_ds1_data_does_not_get_confused_by_the_ds0_block():
    """Two AF_initDataCallback blocks exist on every real page (ds:0, ds:1) — extraction must
    find the second one, not the first."""
    page = _wrap_ds1('[[["99", "Real Job"]], null, 1, 20]')
    data = _ds1_data(page)
    assert data[0] == [["99", "Real Job"]]


# --------------------------------------------------------------------------------- pagination


class _FakePages:
    """Serves a canned multi-page board, recording every page requested."""

    def __init__(self, total_ids, total_stated=None):
        self.ids = [str(i) for i in range(total_ids)]
        self.total_stated = total_ids if total_stated is None else total_stated
        self.requested: list[int] = []

    def page_body(self, page: int) -> str:
        self.requested.append(page)
        start = (page - 1) * 20
        batch = self.ids[start : start + 20]
        jobs = [[i, f"job {i}"] for i in batch]
        return _wrap_ds1(f"[{json.dumps(jobs)}, null, {self.total_stated}, 20]")


def _paged(monkeypatch, fake):
    scraper = _scraper()
    monkeypatch.setattr(scraper, "_get", lambda url=None: fake.page_body(_page_of(url)))
    return scraper


def _page_of(url: str) -> int:
    if "page=" not in url:
        return 1
    return int(url.split("page=")[1].split("&")[0])


def test_pagination_walks_every_page_the_stated_total_implies(monkeypatch):
    fake = _FakePages(total_ids=45)  # 3 pages: 20, 20, 5
    scraper = _paged(monkeypatch, fake)
    raw = scraper.fetch_raw()
    assert len(raw) == 45
    assert sorted(fake.requested) == [1, 2, 3]
    assert scraper.truncated is None


def test_a_single_short_page_needs_no_further_fetch(monkeypatch):
    fake = _FakePages(total_ids=7, total_stated=7)
    scraper = _paged(monkeypatch, fake)
    raw = scraper.fetch_raw()
    assert len(raw) == 7
    assert fake.requested == [1]


def test_a_materially_short_walk_is_reported_via_the_shared_tolerance(monkeypatch):
    """The board is live and its count moves during a multi-page walk (module docstring) —
    reuses BaseScraper's 0.99 share tolerance rather than a bespoke slack constant, so a real
    5/45 (11.1%) shortfall — well outside it — is still reported."""
    fake = _FakePages(total_ids=45, total_stated=45)

    def flaky_get(url=None):
        page = _page_of(url)
        if page == 3:
            fake.requested.append(page)
            return _wrap_ds1("[[], null, 45, 20]")  # this page came back empty
        return fake.page_body(page)

    scraper = _scraper()
    monkeypatch.setattr(scraper, "_get", flaky_get)
    raw = scraper.fetch_raw()
    assert len(raw) == 40  # 45 - the 5 on the lost page 3
    assert scraper.truncated and "40 of 45" in scraper.truncated


def test_a_stale_understated_total_does_not_strand_the_tail(monkeypatch):
    """The bug the tail walk exists to fix: page 1's total is only an estimate of the fan-out
    width, and a total that grew mid-crawl would otherwise mean the fan-out never even
    requests the pages past the stale estimate — silently missing real postings rather than
    merely undercounting them. 45 real postings (3 full-shaped pages: 20, 20, 5) behind a
    stated total of only 30 (implying just 2 pages) must still all be read."""
    fake = _FakePages(total_ids=45, total_stated=30)
    scraper = _paged(monkeypatch, fake)
    raw = scraper.fetch_raw()
    assert len(raw) == 45
    assert sorted(fake.requested) == [1, 2, 3]
    assert scraper.truncated is None


def test_the_tail_walk_stops_at_the_first_short_page(monkeypatch):
    """A full last-estimated page walks forward one page at a time; a short (or empty) page
    is the real terminator and must not trigger a further page fetch."""
    fake = _FakePages(total_ids=41, total_stated=20)  # pages: 20, 20, 1
    scraper = _paged(monkeypatch, fake)
    raw = scraper.fetch_raw()
    assert len(raw) == 41
    assert sorted(fake.requested) == [1, 2, 3]


def test_a_negligible_shortfall_stays_within_the_shared_tolerance(monkeypatch):
    """The same 0.99-share mechanism tolerates a one-in-a-hundred miss — proof this scraper's
    call into it behaves like every other caller's, not a re-implementation."""
    fake = _FakePages(
        total_ids=100, total_stated=101
    )  # 100/101 = 99.0%, exactly on the line
    scraper = _paged(monkeypatch, fake)
    raw = scraper.fetch_raw()
    assert len(raw) == 100
    assert scraper.truncated is None


def test_hitting_the_page_cap_marks_truncated(monkeypatch):
    fake = _FakePages(total_ids=10**6, total_stated=10**6)
    scraper = _paged(monkeypatch, fake)
    from headstart.scrapers import google as google_mod

    monkeypatch.setattr(google_mod, "_MAX_PAGES", 3)
    raw = scraper.fetch_raw()
    assert len(raw) == 60  # 3 pages x 20
    assert scraper.truncated
    assert "page cap" in scraper.truncated


def test_an_unreadable_first_page_returns_no_jobs_without_crashing(monkeypatch):
    scraper = _scraper()
    monkeypatch.setattr(scraper, "_get", lambda url=None: "<html>no data here</html>")
    assert scraper.fetch_raw() == []


def test_parse_still_reads_the_pre_pagination_fixture_shape():
    """`fetch_raw`'s shape is a plain job-array list; a recorded fixture's `jobs` list is
    exactly that, so it can be handed to `parse` directly."""
    jobs = _scraper().parse(_page1()["jobs"], SCRAPED_AT)
    assert len(jobs) == 2
    assert all(j.title for j in jobs)
