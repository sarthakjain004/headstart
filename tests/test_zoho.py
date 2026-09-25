import html
import json
from pathlib import Path

import pytest
from fake_fetcher import FakeFetcher, FakeResponse

from headstart.scrapers.base import DetailLost
from headstart.scrapers.registry import get_scraper
from headstart.scrapers.zoho import _THROTTLE_LOSS, ZohoScraper

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCRAPED_AT = "2026-01-01T00:00:00+00:00"


def _page(records):
    """Wrap job records the way Zoho renders them: HTML-escaped JSON in the jobs input."""
    return f'<input type="hidden" value="{html.escape(json.dumps(records))}" id="jobs">'


def test_zoho_parse():
    html_text = (FIXTURES / "zoho_pnbcsl.html").read_text(encoding="utf-8")
    jobs = get_scraper("zoho", "pnbcsl.zohorecruit.in", "Fallback Co").parse(
        html_text, SCRAPED_AT
    )
    assert len(jobs) == 2
    j = jobs[0]
    assert j.id == "zoho:pnbcsl.zohorecruit.in:91994000000294203"
    assert j.ats == "zoho"
    assert (
        j.company == "PNB Cards & Services Limited"
    )  # from embedded org_info, not fallback
    assert j.title == "Sales Manager (Vehicle Loan)"
    assert j.remote is False
    assert j.department == "Sales - Marketing"
    assert "Kolkata" in j.location
    assert j.posted_at == "2026-01-16"
    assert j.url == (
        "https://pnbcsl.zohorecruit.in/jobs/Careers/"
        "91994000000294203/Sales-Manager-Vehicle-Loan-?source=CareerSite"
    )
    assert j.scraped_at == SCRAPED_AT


def test_zoho_no_jobs_input_returns_empty():
    jobs = get_scraper("zoho", "x.zohorecruit.in").parse(
        "<html>nothing</html>", SCRAPED_AT
    )
    assert jobs == []


def test_zoho_skips_locked_unpublished_and_idless():
    records = [
        {
            "id": "1",
            "Posting_Title": "Remote Engineer",
            "Remote_Job": True,
            "State": "Karnataka",
            "Country": "India",
        },  # kept; no City -> "State, Country"
        {
            "id": "2",
            "Posting_Title": "Locked Role",
            "Is_Locked": True,
            "City": "Pune",
        },  # skipped
        {
            "id": "3",
            "Posting_Title": "Draft Role",
            "Publish": False,
            "City": "Delhi",
        },  # skipped
        {"Posting_Title": "No Id Role", "City": "Mumbai"},  # skipped (no id)
        {
            "id": "5",
            "Job_Opening_Name": "Ops Lead",
            "City": "Chennai",
        },  # title via Job_Opening_Name
    ]
    jobs = get_scraper("zoho", "acme.zohorecruit.com").parse(_page(records), SCRAPED_AT)
    assert [j.title for j in jobs] == ["Remote Engineer", "Ops Lead"]
    assert jobs[0].remote is True
    assert jobs[0].location == "Karnataka, India"
    assert jobs[1].location == "Chennai"


# --- location: join City, State, Country instead of the old `City or (State, Country)` -----------
# The old code discarded a real Country on 85.69% of jobs whenever City was present (audit:
# experiment/location-audit-2026-08-25/zoho.md).


def test_zoho_location_joins_city_state_and_country():
    records = [
        {
            "id": "1",
            "Posting_Title": "Backend Engineer",
            "City": "Tampa",
            "State": "Florida",
            "Country": "United States",
        }
    ]
    jobs = get_scraper("zoho", "acme.zohorecruit.com").parse(_page(records), SCRAPED_AT)
    assert jobs[0].location == "Tampa, Florida, United States"


def test_zoho_location_dedupes_city_equal_state_and_state_equal_country():
    records = [
        {"id": "1", "Posting_Title": "A", "City": "Riyadh", "State": "Riyadh"},
        {"id": "2", "Posting_Title": "B", "State": "Singapore", "Country": "Singapore"},
    ]
    jobs = get_scraper("zoho", "acme.zohorecruit.com").parse(_page(records), SCRAPED_AT)
    assert jobs[0].location == "Riyadh"
    assert jobs[1].location == "Singapore"


def test_zoho_location_filters_junk_state():
    records = [
        {
            "id": "1",
            "Posting_Title": "A",
            "City": "Casablanca",
            "State": ".",
            "Country": "Morocco",
        }
    ]
    jobs = get_scraper("zoho", "acme.zohorecruit.com").parse(_page(records), SCRAPED_AT)
    assert jobs[0].location == "Casablanca, Morocco"


def test_zoho_location_keeps_placeless_city_marker_and_appends_country():
    # H1: a placeless City marker used to win the old truthy `or` and discard a real Country.
    # Remote_Job is False here on purpose — the audit found 0/68 such jobs have it set, so the
    # fix can't key off that flag; it has to fall out of the plain join.
    records = [
        {
            "id": "1",
            "Posting_Title": "A",
            "City": "Remote",
            "Country": "Luxembourg",
            "Remote_Job": False,
        }
    ]
    jobs = get_scraper("zoho", "acme.zohorecruit.com").parse(_page(records), SCRAPED_AT)
    assert jobs[0].location == "Remote, Luxembourg"


def test_zoho_location_none_when_every_part_blank_or_junk():
    records = [{"id": "1", "Posting_Title": "A", "State": "-"}]
    jobs = get_scraper("zoho", "acme.zohorecruit.com").parse(_page(records), SCRAPED_AT)
    assert jobs[0].location is None


def test_zoho_parse_fills_description_from_details():
    # tenants that omit the Job_Description column get it from the detail pass
    records = [
        {"id": "1", "Posting_Title": "Backend Engineer"},
        {"id": "2", "Posting_Title": "Data Engineer", "Job_Description": "<p>Own</p>"},
    ]
    raw = {
        "page": _page(records),
        "details": {"1": {"id": "1", "Job_Description": "<p>5+ years of Python</p>"}},
    }
    jobs = get_scraper("zoho", "acme.zohorecruit.com").parse(raw, SCRAPED_AT)
    assert jobs[0].description == "5+ years of Python"
    assert (
        jobs[1].description == "Own"
    )  # listing value wins when the detail fetch found nothing


def _detail_page(record):
    """A detail page the way Zoho renders it: JS-escaped JSON inside JSON.parse('…')."""
    payload = json.dumps([record])
    escaped = payload.replace("\\", "\\\\").replace('"', "\\x22").replace("/", "\\/")
    return f"<script>var jobs = JSON.parse('{escaped}');</script>"


def _zoho_board(listing_page, detail_for):
    """A zoho Board on ``acme.zohorecruit.com`` whose careers page is ``listing_page`` and whose
    detail pages ``detail_for(job_id)`` answers, both through a FakeFetcher."""
    board = "https://acme.zohorecruit.com/jobs/Careers"

    def route(method, url, kwargs):
        if url == board:
            return FakeResponse(text=listing_page)
        return detail_for(url.rsplit("/", 1)[1])

    fetcher = FakeFetcher(route)
    return ZohoScraper("acme.zohorecruit.com", fetcher=fetcher), fetcher


@pytest.mark.parametrize("async_fanout", ["1", "0"])
def test_zoho_fetch_raw_detail_pass(monkeypatch, async_fanout):
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", async_fanout)
    # Every published, non-locked record gets a detail fetch, not just description-less ones —
    # Salary/Currency live ONLY on the detail page, so gating on a listing-level description
    # meant most jobs never had it fetched at all (user decision 2026-08-24).
    records = [
        {"id": "1", "Posting_Title": "Backend Engineer"},
        {"id": "2", "Posting_Title": "Filled", "Job_Description": "<p>x</p>"},
        {"id": "3", "Posting_Title": "Locked", "Is_Locked": True},
    ]
    detail = {"id": "1", "Job_Description": "<div>4+ years of Go</div>"}
    scraper, fetcher = _zoho_board(
        _page(records), lambda job_id: FakeResponse(text=_detail_page(detail))
    )
    raw = scraper.fetch_raw()
    assert sorted(fetcher.urls()[1:]) == [
        "https://acme.zohorecruit.com/jobs/Careers/1",
        "https://acme.zohorecruit.com/jobs/Careers/2",
    ]  # not "3" — locked
    assert raw["details"] == {"1": detail, "2": detail}
    jobs = scraper.parse(raw, SCRAPED_AT)
    # The detail record wins over the listing's own Job_Description for both jobs — it is a
    # measured strict superset (experiment/location-audit-2026-08-25/zoho.md).
    assert jobs[0].description == "4+ years of Go"
    assert jobs[1].description == "4+ years of Go"


# --- detail record: build the Job from it, falling back to the listing -------------------------
# The detail page is already fetched for every published job (`fetch_raw`), and was measured a
# strict superset of the listing across 205 paired tenants: Date_Opened, Work_Experience, State,
# Industry and Salary/Currency all show up there at meaningfully higher coverage (audit:
# experiment/location-audit-2026-08-25/zoho.md).


def test_zoho_detail_record_enriches_posted_at_experience_department_state_and_salary():
    records = [
        {
            "id": "1",
            "Posting_Title": "Backend Engineer",
            "City": "Reyrieux",
            "Country": "France",
        }
    ]
    detail = _detail_page(
        {
            "id": "1",
            "City": "Reyrieux",
            "State": "Auvergne-Rhone-Alpes",
            "Country": "France",
            "Date_Opened": "2025-09-25",
            "Work_Experience": "+3 ans",
            "Industry": "Industrie",
            "Salary": "30-32",
            "Currency": "EUR",
        }
    )
    scraper, _fetcher = _zoho_board(
        _page(records), lambda job_id: FakeResponse(text=detail)
    )
    jobs = scraper.parse(scraper.fetch_raw(), SCRAPED_AT)
    j = jobs[0]
    assert j.posted_at == "2025-09-25"  # listing had none
    assert j.experience == "+3 ans"  # listing had none
    assert j.department == "Industrie"  # listing had none
    assert (
        j.location == "Reyrieux, Auvergne-Rhone-Alpes, France"
    )  # State only on detail
    assert j.salary == "30-32 EUR"  # never read from the listing at all


def test_zoho_falls_back_to_listing_when_detail_fetch_missing():
    records = [
        {
            "id": "1",
            "Posting_Title": "Backend Engineer",
            "City": "Chennai",
            "Date_Opened": "2026-01-01",
            "Work_Experience": "1-3 years",
            "Industry": "Technology",
        }
    ]
    raw = {"page": _page(records), "details": {}}
    jobs = get_scraper("zoho", "acme.zohorecruit.com").parse(raw, SCRAPED_AT)
    j = jobs[0]
    assert j.posted_at == "2026-01-01"
    assert j.experience == "1-3 years"
    assert j.department == "Technology"
    assert j.salary is None  # never in the listing to begin with


def _listing_with_description(job_id: str) -> str:
    return _page(
        [
            {
                "id": job_id,
                "Posting_Title": "Backend Engineer",
                "Job_Description": "<p>The listing's rendering of the posting.</p>",
            }
        ]
    )


def test_a_failed_detail_never_replaces_a_held_description_with_the_listings():
    """ADR-0208. The listing renders a posting's description differently from the detail page,
    and whole detail passes fail on some runs, so falling back to the listing flipped the stored
    text back and forth. A Job whose description the store holds gets none instead, and
    `update_descriptions` keeps the held text."""
    scraper = get_scraper("zoho", "acme.zohorecruit.com")
    scraper.have_details = {"zoho:acme.zohorecruit.com:1"}
    raw = {"page": _listing_with_description("1"), "details": {}}
    [job] = scraper.parse(raw, SCRAPED_AT)
    assert job.description is None


def test_a_failed_detail_still_falls_back_to_the_listing_for_an_unheld_job():
    """With nothing held, the listing's text is the best text there is: a new Job keeps it
    rather than being embedded from its title alone."""
    scraper = get_scraper("zoho", "acme.zohorecruit.com")
    scraper.have_details = set()
    raw = {"page": _listing_with_description("1"), "details": {}}
    [job] = scraper.parse(raw, SCRAPED_AT)
    assert job.description == "The listing's rendering of the posting."


@pytest.mark.parametrize("async_fanout", ["1", "0"])
def test_zoho_classifies_a_source_declared_unavailable_detail(
    monkeypatch, async_fanout
) -> None:
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", async_fanout)
    unavailable = '<div class="sorry-block"><h4>This job posting is no longer available.</h4></div>'
    scraper, _fetcher = _zoho_board(
        _page([{"id": "1", "Posting_Title": "Backend Engineer"}]),
        lambda job_id: FakeResponse(text=unavailable),
    )

    raw = scraper.fetch_raw()

    assert raw["details"] == {}
    assert scraper.detail_losses == {"posting explicitly unavailable": 1}


# The shell Zoho serves at a posting's detail URL once the posting is closed, captured live
# 2026-09-25 (harrisonconsultingsolutions, a listed id): no jobs blob, one <h4> verdict.
def _unavailable_shell(verdict: str) -> str:
    return (
        '<html><head></head><body><div class="sorry-block"><h2>Sorry,</h2>\n'
        f"<h4>{verdict}</h4>\n"
        "<p>For more details, please contact the website administrator.</p>\n"
        "</div></body></html>"
    )


@pytest.mark.parametrize("async_fanout", ["1", "0"])
def test_zoho_drops_a_listed_posting_its_detail_page_says_is_gone(
    monkeypatch, async_fanout
) -> None:
    # The listing still carries a closed posting; only its detail page says it is gone, and a
    # Job built from the listing would serve a dead link (docs/pipeline/
    # 2026-09-24_five-run-log-review.md finding 3). It is a closure, so the Board stays
    # authoritative and eviction sees the id as absent.
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", async_fanout)
    records = [
        {"id": "1", "Posting_Title": "Closed Role"},
        {"id": "2", "Posting_Title": "Open Role"},
    ]
    pages = {
        "1": _unavailable_shell("This job posting is no longer available."),
        "2": _detail_page({"id": "2", "Job_Description": "<p>Go</p>"}),
    }
    scraper, _fetcher = _zoho_board(
        _page(records), lambda job_id: FakeResponse(text=pages[job_id])
    )

    jobs = scraper.parse(scraper.fetch_raw(), SCRAPED_AT)

    assert [j.title for j in jobs] == ["Open Role"]
    assert scraper.detail_losses == {"posting explicitly unavailable": 1}
    assert scraper.truncated is None


@pytest.mark.parametrize(
    "verdict",
    [
        # Each seen live 2026-09-25 on a Board in that language (host in the comment).
        "A postagem desta vaga não está mais disponível.",  # resourceit (.com)
        "Cette offre d’emploi n’est plus disponible.",  # 4-icanada (.com)
        "Dieses Jobangebot ist nicht mehr verfügbar.",  # alltagsbegleitung-sw (.eu)
        "この求人は終了しています。",  # corp (.com)
    ],
)
def test_zoho_reads_the_unavailable_verdict_in_the_boards_language(verdict) -> None:
    with pytest.raises(DetailLost, match="posting explicitly unavailable"):
        ZohoScraper._detail_record_of(_unavailable_shell(verdict))


def test_zoho_keeps_a_posting_behind_the_page_unavailable_shell() -> None:
    # Zoho's .com data centre answers a throttled client with a *different* shell (a 302 to
    # /html/portal.html), for live postings too: measured 2026-09-25 after ~1,000 requests from
    # one IP. It says nothing about the posting, so the Job stays; the loss is labelled for what
    # it is, so a CI run's gap line says whether this is the CI-only "no jobs blob" loss.
    throttled = _unavailable_shell("this page is currently unavailable.")
    scraper, _fetcher = _zoho_board(
        _page([{"id": "1", "Posting_Title": "Open Role"}]),
        lambda job_id: FakeResponse(text=throttled),
    )

    jobs = scraper.parse(scraper.fetch_raw(), SCRAPED_AT)

    assert [j.title for j in jobs] == ["Open Role"]
    assert scraper.detail_losses == {_THROTTLE_LOSS: 1}


def test_zoho_labels_any_other_blobless_page_as_a_missing_jobs_blob() -> None:
    """The fallback stays: a page with no record and neither shell is a shape that moved."""
    with pytest.raises(DetailLost, match="no jobs blob on the page"):
        ZohoScraper._detail_record_of("<html><body><p>something else</p></body></html>")


@pytest.mark.parametrize("async_fanout", ["1", "0"])
def test_zoho_a_throttle_redirect_walls_the_group_and_is_retried(
    monkeypatch, async_fanout
):
    """The .com throttle is a 302 to /html/portal.html (2026-09-25; 30 of 30 live detail pages
    across .com/.eu/.in answer 200 directly, open or closed). Seen unfollowed, it marks the zoho
    egress group walled and is retried, so the retry rides the spare egress (ADR-0063)."""
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", async_fanout)
    scraper, fetcher = _zoho_board(
        _page([{"id": "1", "Posting_Title": "Open Role"}]),
        lambda job_id: FakeResponse(text=_detail_page({"id": "1"})),
    )
    scraper.fetch_raw()

    detail = next(r for r in fetcher.requests if r.url.endswith("/jobs/Careers/1"))
    assert detail.kwargs["allow_redirects"] is False
    assert 302 in detail.kwargs["retry_on"]
    assert detail.kwargs["egress_on"] == frozenset({302})
    assert detail.kwargs["egress_group"] == "zoho"


def test_zoho_labels_a_throttle_redirect_that_never_cleared() -> None:
    """Retries spent and still redirected: the loss says throttle, not a bare status."""
    throttled = FakeResponse(302, "", headers={"location": "/html/portal.html"})
    scraper, _fetcher = _zoho_board(
        _page([{"id": "1", "Posting_Title": "Open Role"}]), lambda job_id: throttled
    )

    jobs = scraper.parse(scraper.fetch_raw(), SCRAPED_AT)

    assert [j.title for j in jobs] == ["Open Role"]  # a throttle is not a closure
    assert scraper.detail_losses == {_THROTTLE_LOSS: 1}


def test_a_listing_served_as_the_throttle_shell_is_named_not_read_as_empty(caplog):
    """The detail pages' throttle shell can answer the listing too; without the jobs input it
    reads as an empty Board, so the line names which page came back."""
    shell = (
        f"<html><body>{'Sorry, ' + 'this page is currently unavailable.'}</body></html>"
    )
    fetcher = FakeFetcher(lambda method, url, kwargs: FakeResponse(text=shell))
    scraper = get_scraper("zoho", "acme.zohorecruit.com", fetcher=fetcher)

    with caplog.at_level("INFO", logger="headstart"):
        raw = scraper.fetch_raw()

    assert scraper.parse(raw, SCRAPED_AT) == []
    assert 'expected the id="jobs" <input>, got the throttle shell' in caplog.text
