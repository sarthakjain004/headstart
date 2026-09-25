"""Tests for `headstart.scrapers.clearcompany`.

The fixtures are real HRM Direct responses captured 2026-09-23, trimmed but never edited:

- `clearcompany_kingarthurbaking.xml` — `kingarthurbaking.hrmdirect.com/employment/xml.php`, cut
  to three `<job>` rows. It carries raw cp1252 bytes (`0x92`) inside an XML declared UTF-8.
- `clearcompany_fisherphillips.xml` — the same feed on a tenant whose reqs repeat once per
  location: two reqs, 14 rows.
- `clearcompany_detail_*.html` — `job-opening.php?req=N` pages with scripts and the page head
  removed: a cp1252 page (King Arthur), a page with the tenant's `Salary:` label (HBT Bank), and a
  closed/unknown req (a 200 with no posting on it).

Every assertion pins something measured in
`docs/clearcompany/2026-09-23_careers-surfaces-measurement.md`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fake_fetcher import FakeFetcher, FakeResponse

from headstart.jobs import salary
from headstart.network import http
from headstart.scrapers.clearcompany import (
    ClearCompanyScraper,
    _field,
    decode_hrm_bytes,
)
from headstart.scrapers.registry import detail_pass_atses, get_scraper

FIXTURES = Path(__file__).parent / "fixtures"
SCRAPED_AT = "2026-01-01T00:00:00+00:00"


def _text(name: str) -> str:
    return decode_hrm_bytes((FIXTURES / name).read_bytes())


def _scraper(slug: str = "kingarthurbaking"):
    return get_scraper("clearcompany", slug)


def _jobs(raw: dict, slug: str = "kingarthurbaking") -> dict:
    return {j.id.rsplit(":", 1)[1]: j for j in _scraper(slug).parse(raw, SCRAPED_AT)}


def _kab(details: dict | None = None) -> dict:
    return {"xml": _text("clearcompany_kingarthurbaking.xml"), "details": details or {}}


def test_the_listing_parses_every_row_with_its_listing_fields():
    jobs = _jobs(_kab())
    assert set(jobs) == {"3781760", "3813473", "3804210"}
    steward = jobs["3781760"]
    assert steward.id == "clearcompany:kingarthurbaking:3781760"
    assert steward.ats == "clearcompany"
    assert steward.title == "Bakery Steward"
    assert steward.department == "Bakery VT Fixed"
    assert steward.company == "King Arthur Baking Company"
    assert steward.location == "Norwich, VT, UNITED STATES"
    assert (
        steward.url
        == "https://kingarthurbaking.hrmdirect.com/employment/job-opening.php?req=3781760"
    )


# --------------------------------------------------------------------------- encoding


def test_cp1252_bytes_in_a_feed_declared_utf8_decode_to_the_intended_characters():
    """443 of 510 detail pages and the xml.php feeds carry cp1252 bytes (`0x92` for an
    apostrophe) — xml.php even under an `encoding="UTF-8"` declaration. A UTF-8 decode would
    error or serve U+FFFD; the fallback serves the apostrophe the tenant typed."""
    raw = (FIXTURES / "clearcompany_kingarthurbaking.xml").read_bytes()
    assert b"\x92" in raw
    text = decode_hrm_bytes(raw)
    assert "we’re the ultimate" in text
    assert "�" not in text


def test_one_feed_mixing_cp1252_utf8_and_double_encoded_bytes_decodes_cleanly():
    """Achievement Centers' feed carries a cp1252 apostrophe (`0x92`), a cp1252 en dash (`0x96`)
    and the same en dash double-encoded through Latin-1 (`C2 96`) in one document. A
    whole-document fallback served "Specialist \u00c2\u2013 Home Visitor" (46 of 174 feeds hit
    something like it); read byte by byte, every one is the character the tenant typed."""
    raw = (FIXTURES / "clearcompany_achievementcenters.xml").read_bytes()
    assert b"\xc2\x96" in raw and b"\x92" in raw
    text = decode_hrm_bytes(raw)
    assert "Help Me Grow Specialist \u2013 Home Visitor" in text
    assert "Ohio\u2019s" in text
    assert not any(ch in text for ch in ("\u00c2", "\u00c3", "\ufffd"))
    assert not re.search("[\x80-\x9f]", text)


def test_valid_utf8_is_decoded_as_utf8():
    assert decode_hrm_bytes("café – résumé".encode()) == "café – résumé"


# --------------------------------------------------------------------------- listing fields


def _fisher() -> dict:
    return {"xml": _text("clearcompany_fisherphillips.xml"), "details": {}}


def test_a_req_repeated_once_per_location_is_one_job_with_every_place_joined():
    """xml.php emits one row per req per location (434 of 6,377 reqs span 2-30 rows); only
    city/state/country differ. One Job per req, every place kept, so the location filter
    matches each of them."""
    jobs = _jobs(_fisher(), "fisherphillips")
    assert set(jobs) == {"3733861", "3720060"}
    assert jobs["3733861"].location == "CA, UNITED STATES; Denver, CO, UNITED STATES"
    places = jobs["3720060"].location.split("; ")
    assert len(places) == 12
    assert places[0] == "CA, UNITED STATES"
    assert "Kansas City, MO, UNITED STATES" in places
    assert places[-1] == "UNITED STATES"


def test_posted_at_is_the_calendar_date_not_the_mislabelled_clock_time():
    """Every `<date>` reads 04:00 or 05:00 with a literal `+0100` — US-Eastern midnight
    mislabelled. The calendar date matches ClearCompany's own OpenDate (11 of 11), so the date is
    kept and the clock is not."""
    jobs = _jobs(_kab())
    assert jobs["3781760"].posted_at == "2026-08-07"
    assert jobs["3813473"].posted_at == "2026-09-14"


def test_remote_reads_the_office_and_location_since_no_field_states_it():
    """No native workplace field (1 tenant of 176 has a `Workplace Type:` label). Fisher
    Phillips files its remote req under the office named "Remote"."""
    jobs = _jobs(_fisher(), "fisherphillips")
    assert jobs["3720060"].remote is True
    assert jobs["3733861"].remote is False


# --------------------------------------------------------------------------- the detail page


def test_the_description_is_the_detail_pages_job_desc_not_the_1000_char_listing_teaser():
    """xml.php's `descriptionrich` stops at exactly 1,000 chars on 6,601 of 8,335 rows; the
    detail's `jobDesc` block is the whole body (p50 5,269 chars)."""
    page = _text("clearcompany_detail_kingarthurbaking_3813473.html")
    job = _jobs(_kab({"3813473": page}))["3813473"]
    assert job.description.startswith("Meet Us: King Arthur Baking Company is an award")
    # The page's one cp1252 byte (0x92) decodes to U+2019, never U+FFFD.
    assert "respects others\u2019 point of view" in job.description
    assert "\ufffd" not in job.description
    assert len(job.description) > 1000
    # The block ends where the page's own markup resumes: no apply button, no footer.
    assert "START YOUR APPLICATION" not in job.description


def test_salary_is_the_tenants_salary_label_on_the_detail_page():
    """`Salary:` is a tenant-configured detail label (15 of 510 pages), templated
    `$X - $Y Per Year`; the generic field parser reads it."""
    raw = {
        "xml": _text("clearcompany_hbtbank.xml"),
        "details": {"3781556": _text("clearcompany_detail_hbtbank_3781556.html")},
    }
    job = _jobs(raw, "hbtbank")["3781556"]
    assert job.salary == "$74513 - $120394 Per Year"
    span = salary.extract(job.salary, None, ats="clearcompany")
    assert (span.min_annual, span.max_annual) == (74513, 120394)


def test_a_detail_label_matches_whatever_case_and_colon_either_side_uses():
    """Labels are tenant-typed (`Salary:`, `Location` with no colon, `Benefits :`), so the
    lookup normalises both the page's label and the one asked for."""
    page = _text("clearcompany_detail_hbtbank_3781556.html")
    for asked in ("salary", "Salary", "SALARY:", "salary :"):
        assert _field(page, asked) == "$74513 - $120394 Per Year"


def test_a_job_with_no_detail_still_ships_without_description_or_salary():
    job = _jobs(_kab())["3781760"]
    assert job.description is None
    assert job.salary is None


def test_a_closed_req_page_carries_no_posting_and_yields_no_description():
    """An unknown or closed req answers 200 with the tenant's page chrome and no posting on it
    (no `jobDesc`, no `<h2>`) — a soft 404, not an error."""
    job = _jobs(_kab({"3813473": _text("clearcompany_detail_closed.html")}))["3813473"]
    assert job.description is None


# --------------------------------------------------------------------------- fetch_raw


def _serve(pages: dict[str, FakeResponse], slug: str = "kingarthurbaking"):
    """A Scraper whose every request is answered from ``pages`` by URL, and the fake that
    records what it was asked for; an unknown URL is refused like a dead host."""

    def route(method, url, kwargs):
        return pages.get(url) or http.RequestsError("Connection refused")

    fetcher = FakeFetcher(route)
    return ClearCompanyScraper(slug, fetcher=fetcher), fetcher


def _bytes(status: int, content: bytes) -> FakeResponse:
    return FakeResponse(status, content=content)


def test_fetch_raw_reads_the_feed_bytes_and_every_detail_outside_the_pipeline():
    """Outside the pipeline (`have_details` None) the tech gate is off, so every req's detail
    page is fetched — and both surfaces arrive as cp1252 bytes, decoded before parsing."""
    board = _scraper()
    xml = (FIXTURES / "clearcompany_kingarthurbaking.xml").read_bytes()
    page = (FIXTURES / "clearcompany_detail_kingarthurbaking_3813473.html").read_bytes()
    scraper, fetcher = _serve(
        {board.url(): _bytes(200, xml), board.job_url("3813473"): _bytes(200, page)}
    )
    raw = scraper.fetch_raw()
    requested = fetcher.urls()
    assert requested[0] == "https://kingarthurbaking.hrmdirect.com/employment/xml.php"
    assert len(requested) == 4  # the feed, then one detail per req
    assert set(raw["details"]) == {"3813473"}
    assert sum(scraper.detail_losses.values()) == 2  # the two refused details
    jobs = {j.id.rsplit(":", 1)[1]: j for j in scraper.parse(raw, SCRAPED_AT)}
    assert len(jobs) == 3
    assert "others’ point of view" in jobs["3813473"].description
    assert jobs["3781760"].description is None
    assert scraper.truncated is None


def test_the_tech_gate_is_exact_and_skips_every_non_tech_detail(monkeypatch):
    """`title` and `department` come from xml.php and the detail overrides neither, so the gate
    asks `filter_tech`'s own question. King Arthur's three reqs are all non-tech: armed, the gate
    fetches no detail, and all three Jobs still ship."""
    monkeypatch.delenv("HEADSTART_TECH_GATE", raising=False)
    xml = (FIXTURES / "clearcompany_kingarthurbaking.xml").read_bytes()
    scraper, fetcher = _serve({_scraper().url(): _bytes(200, xml)})
    scraper.have_details = frozenset()
    raw = scraper.fetch_raw()
    assert fetcher.urls() == [scraper.url()]
    assert scraper.telemetry.get("tech_gated_details") == 3
    assert len(scraper.parse(raw, SCRAPED_AT)) == 3


def test_a_closed_req_is_a_counted_detail_gap():
    board = _scraper()
    xml = (FIXTURES / "clearcompany_kingarthurbaking.xml").read_bytes()
    closed = (FIXTURES / "clearcompany_detail_closed.html").read_bytes()
    pages = {board.url(): _bytes(200, xml)}
    for req in ("3781760", "3813473", "3804210"):
        pages[board.job_url(req)] = _bytes(200, closed)
    scraper, _fetcher = _serve(pages)
    raw = scraper.fetch_raw()
    assert raw["details"] == {}
    assert scraper.detail_losses["no posting"] == 3


def test_a_departed_tenant_raises_rather_than_reading_as_empty():
    """A departed or unknown tenant's xml.php is a 404 (51 of 51 measured); raising keeps it a
    failed Board instead of a Board that has nothing open."""
    scraper, _fetcher = _serve(
        {_scraper().url(): _bytes(404, b"<html>Not Found</html>")}
    )
    with pytest.raises(http.RequestsError):
        scraper.fetch_raw()


def test_a_200_that_is_not_the_feed_is_an_unreadable_board(caplog):
    scraper, _fetcher = _serve(
        {_scraper().url(): _bytes(200, b"<html>maintenance</html>")}
    )
    with caplog.at_level("INFO"):
        raw = scraper.fetch_raw()
    assert scraper.parse(raw, SCRAPED_AT) == []
    assert "read no jobs" in caplog.text


def test_an_empty_live_board_is_the_feed_with_no_jobs_and_not_unreadable(caplog):
    empty = (
        b'<?xml version="1.0" encoding="UTF-8"?>\n<source>\n<publisher>HRM Direct</publisher>\n'
        b"<publisherurl>http:www.hrmdirect.com</publisherurl>\n</source>\n "
    )
    scraper, _fetcher = _serve({_scraper().url(): _bytes(200, empty)})
    with caplog.at_level("INFO"):
        raw = scraper.fetch_raw()
    assert scraper.parse(raw, SCRAPED_AT) == []
    assert "read no jobs" not in caplog.text


# --------------------------------------------------------------------------- identity


def test_the_job_url_is_the_public_posting_page_and_matches_the_declared_shape():
    """`job-opening.php?req=N` is the page ClearCompany's own job links resolve to (through
    `/api/v1/careers/jobs/{guid}/posting-url`)."""
    scraper = _scraper()
    url = scraper.job_url("3813473")
    assert re.fullmatch(scraper.url_shape, url)


def test_the_slug_is_the_lowercased_label_of_either_vendor_host():
    """One label keys both `{slug}.hrmdirect.com` (the Board) and `{slug}.clearcompany.com` (the
    same tenant's app); discovery emits either host, and DNS is case-insensitive."""
    slug_from = get_scraper("clearcompany", "x").slug_from
    assert (
        slug_from("kingarthurbaking", "https://kingarthurbaking.hrmdirect.com")
        == "kingarthurbaking"
    )
    assert slug_from("KingArthurBaking", "") == "kingarthurbaking"
    assert slug_from("kingarthurbaking.hrmdirect.com", "") == "kingarthurbaking"
    assert slug_from("ehs-support.clearcompany.com", "") == "ehs-support"


def test_the_scraper_declares_a_detail_pass_below_the_measured_knee():
    """Detail pages peaked at 90.5 req/s at conc 32 (676 requests, all 200); 16 is half that."""
    scraper = _scraper()
    assert scraper.has_detail_pass is True
    assert scraper.detail_workers == 16
    assert "clearcompany" in detail_pass_atses()
