"""Tests for `headstart.scrapers.jobvite`.

The fixture (`tests/fixtures/jobvite_postings.json`) is four real postings captured live on
2026-09-07, one per shape the detail pass has to survive: JSON-LD with an empty `baseSalary`,
JSON-LD with a populated one, a tenant whose template emits no JSON-LD at all, and one whose
rendered heading carries a nested `<h3>` the title must not absorb. The HTML the last two were
parsed from is committed beside the analysis in `docs/jobvite/artifacts/`.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from headstart import http
from headstart.scrapers.jobvite import JobviteScraper, total_of
from headstart.scrapers.registry import get_scraper

FIXTURES = Path(__file__).parent / "fixtures"
SCRAPED_AT = "2026-01-01T00:00:00+00:00"


def _fixture():
    with open(FIXTURES / "jobvite_postings.json", encoding="utf-8") as fh:
        return json.load(fh)


def _raw_for(slug):
    """The `{ids, postings}` `fetch_raw` returns, narrowed to one board's postings."""
    data = _fixture()
    ids = [job_id for board, job_id in data["picks"] if board == slug]
    return {"ids": ids, "postings": {i: data["postings"][i] for i in ids}}


def test_parses_a_real_json_ld_posting():
    (job,) = get_scraper("jobvite", "barracuda-networks-inc", "Barracuda").parse(
        _raw_for("barracuda-networks-inc"), SCRAPED_AT
    )
    assert job.id == "jobvite:barracuda-networks-inc:oZOEAfwL"
    assert job.ats == "jobvite"
    # `hiringOrganization` outranks the Board's own name — a parent tenant's posting can name
    # the subsidiary that owns it.
    assert job.company == "Barracuda Networks Inc."
    assert job.title == "Cloud Site Reliability Senior Engineer"
    # City/region/country joined, each stripped of the tenant's own trailing comma and padding
    # ("Bangalore ", "Karnataka,").
    assert job.location == "Bangalore, Karnataka, India"
    assert job.remote is False
    assert job.department == "Engineering"  # schema.org `industry`
    assert job.url == "https://jobs.jobvite.com/barracuda-networks-inc/job/oZOEAfwL"
    assert job.posted_at == "2026-08-17"
    assert job.description and "</" not in job.description
    # `baseSalary` is present on this posting but every field of it is blank, which is the
    # majority case (measured 89% of 1,077 sampled JSON-LD postings) — a bare currency or unit
    # is not a figure, so it must not reach `Job.salary`.
    assert job.salary is None


def test_reads_a_populated_base_salary():
    (job,) = get_scraper("jobvite", "aarete").parse(_raw_for("aarete"), SCRAPED_AT)
    assert job.employment_type == "Full-Time"
    assert job.salary is not None
    from headstart import salary as salary_module

    span = salary_module.extract(job.salary, None, "jobvite")
    assert span is not None and span.min_annual and span.source == "field"


def test_falls_back_to_the_rendered_page_when_a_tenant_emits_no_json_ld():
    """29 of the 401 hiring boards have a template that emits no JSON-LD anywhere, so the
    posting is read off `jv-header` / `jv-job-detail-meta` / `jv-job-detail-description`
    instead. The page renders no date, so `posted_at` is genuinely absent rather than dropped."""
    (job,) = get_scraper("jobvite", "nutanix").parse(_raw_for("nutanix"), SCRAPED_AT)
    assert job.title == "Enterprise Account Manager, FSI"
    assert job.department == "Sales"  # first segment of the meta line
    assert job.location == "Paris, France"  # second segment
    assert job.posted_at is None
    assert job.description and "Nutanix" in job.description
    # The Board name stands in when the page states no employer.
    assert job.company == "nutanix"


def test_a_nested_heading_does_not_leak_into_the_title():
    """agscareer's template puts the location inside the `<h2 class="jv-header">` as a
    `<br><h3>Canada</h3>`; stripping tags first produced "Account Executive- Slots Canada"."""
    (job,) = get_scraper("jobvite", "agscareer").parse(
        _raw_for("agscareer"), SCRAPED_AT
    )
    assert job.title == "Account Executive- Slots"


def test_posting_of_reads_the_heading_only_to_its_first_nested_tag():
    page = (
        '<h2 class="jv-header">\n  Staff Engineer\n<br>\n<h3>\n Canada\n</h3>\n</h2>'
        '<div class="jv-job-detail-description" ng-non-bindable><p>Build it.</p></div><div>'
    )
    posting = JobviteScraper._posting_of(page)
    assert posting["title"] == "Staff Engineer"
    assert "Build it." in posting["description"]


def test_posting_of_prefers_json_ld_over_the_rendered_blocks():
    page = (
        '<script type="application/ld+json">'
        '{"@type":"JobPosting","title":"Backend Engineer","datePosted":"2026-03-01"}'
        "</script>"
        '<h2 class="jv-header">Ignored when JSON-LD is present</h2>'
    )
    posting = JobviteScraper._posting_of(page)
    assert posting["title"] == "Backend Engineer"
    assert posting["datePosted"] == "2026-03-01"


def test_posting_of_falls_back_when_the_json_ld_will_not_parse():
    """27 of 1,155 sampled detail pages carry a JSON-LD block that does not parse. Dropping the
    posting there would cost the Job; the rendered blocks are still on the same page."""
    page = (
        '<script type="application/ld+json">{"@type":"JobPosting", "title": "oops",,}</script>'
        '<h2 class="jv-header">Data Engineer</h2>'
        '<div class="jv-job-detail-description"><p>Pipelines.</p></div><div>'
    )
    assert JobviteScraper._posting_of(page)["title"] == "Data Engineer"


def test_a_posting_with_no_page_is_dropped():
    """The listing carries no title, so a Job cannot be built without its detail page."""
    scraper = get_scraper("jobvite", "acme")
    assert scraper.parse({"ids": ["aaa"], "postings": {"aaa": None}}, SCRAPED_AT) == []


def test_total_of_reads_the_counter_in_any_locale():
    # English, thousands-separated, and the Portuguese "de" one board serves — the total is the
    # last number so no connecting word has to be known.
    assert total_of('<div class="jv-pagination-text">1-50 of 2,831</div>') == 2831
    assert total_of('<div class="jv-pagination-text">1-50 de 166</div>') == 166
    # An extra class, and the number wrapped in <strong> (affcareers).
    assert (
        total_of(
            '<div class="jv-pagination-text ml-auto">1-17 of <strong>17</strong></div>'
        )
        == 17
    )
    # An empty board carries no counter at all — that is "no total", never zero.
    assert total_of("<html><body>No results found.</body></html>") is None


def test_slug_from_keeps_the_bare_tenant():
    """Jobvite boards are a path on one shared host, so the discovered tenant *is* the slug and
    the base default is right — unlike zoho/personio, whose slug has to be normalised to a host
    because `url()` appends a path to it."""
    assert JobviteScraper.slug_from(
        "barracuda-networks-inc", "jobs.jobvite.com/barracuda-networks-inc"
    ) == ("barracuda-networks-inc")
    assert JobviteScraper("acme").url() == "https://jobs.jobvite.com/acme/search"
    assert JobviteScraper("acme").board_key() == "jobvite:acme"


def _listing(*, jobs=(), next_href=None, total=None, slug="acme"):
    rows = "".join(
        f'<tr><td class="jv-job-list-name"><a href="/{slug}/job/{j}">{j}</a></td></tr>'
        for j in jobs
    )
    counter = (
        f'<div class="jv-pagination-text">1-{len(jobs)} of {total}</div>'
        if total
        else ""
    )
    nxt = (
        f'<a href="{next_href}" class="jv-pagination-next">Next</a>'
        if next_href
        else ""
    )
    return f"<html><body>{rows}{counter}{nxt}</body></html>"


def _responses(monkeypatch, pages):
    """Serve `pages` (url -> (status, body, location)) through the fetch seam, recording order."""
    seen = []

    def _fetch(method, url, **kwargs):
        seen.append(url)
        status, body, location = pages[url]
        return SimpleNamespace(
            status_code=status,
            text=body,
            headers={"location": location} if location else {},
        )

    monkeypatch.setattr(http, "fetch", _fetch)
    return seen


def test_the_walk_follows_the_next_link_to_the_end(monkeypatch):
    base = "https://jobs.jobvite.com/acme/search"
    seen = _responses(
        monkeypatch,
        {
            base: (
                200,
                _listing(jobs=("a", "b"), next_href="/acme/search/?p=1", total=3),
                None,
            ),
            "https://jobs.jobvite.com/acme/search/?p=1": (
                200,
                _listing(jobs=("c",), total=3),
                None,
            ),
        },
    )
    scraper = JobviteScraper("acme")
    assert scraper._listing_ids() == ["a", "b", "c"]
    assert seen == [base, "https://jobs.jobvite.com/acme/search/?p=1"]
    assert scraper.truncated is None


def test_the_walk_stops_when_a_page_repeats_itself(monkeypatch):
    """A posting can occupy two pagination slots (`cascade`: 71 slots, 70 distinct), so ids are
    de-duplicated — and a next link that yields nothing new ends the walk rather than looping."""
    base = "https://jobs.jobvite.com/acme/search"
    page = _listing(jobs=("a", "b"), next_href="/acme/search/?p=1", total=2)
    _responses(
        monkeypatch,
        {
            base: (200, page, None),
            "https://jobs.jobvite.com/acme/search/?p=1": (200, page, None),
        },
    )
    scraper = JobviteScraper("acme")
    assert scraper._listing_ids() == ["a", "b"]
    assert scraper.truncated is None


def test_an_empty_board_is_no_postings_not_an_error(monkeypatch):
    """33 of the 434 live boards serve a 200 with no postings and no counter. That is a live
    Board hiring nobody, and it must parse as zero Jobs rather than raise."""
    base = "https://jobs.jobvite.com/acme/search"
    _responses(
        monkeypatch, {base: (200, "<html><body>No results found.</body></html>", None)}
    )
    scraper = JobviteScraper("acme")
    assert scraper.fetch_raw() == {"ids": [], "postings": {}}
    assert scraper.parse({"ids": [], "postings": {}}, SCRAPED_AT) == []
    assert scraper.truncated is None


@pytest.mark.parametrize(
    "location",
    [
        "http://search.jobvite.com?invalid=1",  # 78 of the 83 redirecting tenants
        "https://app.jobvite.com/Login/Login.aspx?cid=q5u9Vfwn",  # login-walled internal board
        "https://opentrons.com/about/jobs/?p=search&nl=1",  # moved off the hosted surface
    ],
)
def test_a_redirecting_tenant_raises_instead_of_reading_as_empty(monkeypatch, location):
    """Following the redirect lands on a 200 with no postings, which reads exactly like an
    emptied board — and `index sync` would then evict every row the Board ever had. So the walk
    refuses redirects and a 3xx surfaces as the per-company failure it is."""
    base = "https://jobs.jobvite.com/acme/search"
    _responses(monkeypatch, {base: (302, "", location)})
    with pytest.raises(http.RequestsError) as excinfo:
        JobviteScraper("acme").fetch_raw()
    assert "302" in str(excinfo.value)
    assert location.split("?")[0] in str(excinfo.value)


def test_the_walk_asks_for_no_redirects(monkeypatch):
    """The guard above is only real if the request itself opts out — `curl_cffi` follows
    redirects by default, so leaving this off makes every dead tenant look like an empty one."""
    captured: dict = {}

    def _fetch(method, url, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(status_code=200, text=_listing(), headers={})

    monkeypatch.setattr(http, "fetch", _fetch)
    JobviteScraper("acme")._listing_ids()
    assert captured["allow_redirects"] is False


def test_an_unreadable_detail_page_marks_the_board_truncated(monkeypatch):
    """The detail pass is load-bearing here — no page, no title, no Job — so a Board that lost
    one comes back short for a reason `harvest` cannot see. ADR-0053 exists to carry exactly
    that alongside the Jobs that did survive, instead of letting `index sync` read the gap as
    two delistings."""
    base = "https://jobs.jobvite.com/acme/search"
    detail = "https://jobs.jobvite.com/acme/job/"
    pages = {
        base: (200, _listing(jobs=("a", "b"), total=2), None),
        detail + "a": (200, '<h2 class="jv-header">Staff Engineer</h2>', None),
        detail + "b": (404, "", None),
    }
    _responses(monkeypatch, pages)
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "0")  # keep the stubbed sync path
    scraper = JobviteScraper("acme")
    raw = scraper.fetch_raw()
    assert raw["postings"] == {"a": {"title": "Staff Engineer"}, "b": None}
    assert scraper.truncated == "1/2 detail pages could not be read"
    (job,) = scraper.parse(raw, SCRAPED_AT)
    assert job.id == "jobvite:acme:a"


def test_location_drops_empty_and_repeated_segments():
    from headstart.scrapers.jobvite import _location

    posting = {
        "jobLocation": [
            {
                "address": {
                    "addressLocality": "Riyadh",
                    "addressRegion": "Riyadh",
                    "addressCountry": "Saudi Arabia",
                }
            }
        ]
    }
    assert _location(posting) == "Riyadh, Saudi Arabia"
    assert (
        _location({"jobLocation": [{"address": {"addressCountry": "Germany"}}]})
        == "Germany"
    )
    assert _location({}) is None


def test_hiring_organization_is_read_in_both_shapes():
    from headstart.scrapers.jobvite import _organization

    assert _organization("Barracuda Networks Inc.") == "Barracuda Networks Inc."
    assert (
        _organization({"@type": "Organization", "name": "Zones LLC."}) == "Zones LLC."
    )
    assert _organization(None) is None
    assert _organization("") is None
