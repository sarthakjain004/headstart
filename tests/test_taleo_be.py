import re

import pytest
from fake_fetcher import FakeFetcher, FakeResponse

from headstart.ingest.doc_prep import to_meta
from headstart.scrapers.base import DEFAULT_REQUEST_HEADERS
from headstart.scrapers.registry import SCRAPERS, get_scraper
from headstart.scrapers.taleo_be import TaleoBEScraper, _posted_at, _workplace_remote

URL = "https://phe.tbe.taleo.net/phe03/ats/careers/v2/searchResults?org=ICANN&cws=37"


def _listing(rid: int, title: str, next_href: str = "") -> str:
    next_link = (
        f'<a href="{next_href}" class="jscroll-next">next</a>' if next_href else ""
    )
    return f"""<div class="oracletaleocwsv2-accordion-block"><div class="oracletaleocwsv2-accordion-head-info">
    <h4><a href="/phe03/ats/careers/v2/viewRequisition?org=ICANN&amp;cws=37&amp;rid={rid}" class="viewJobLink">{title}</a></h4>
    <div>Engineering</div><div>Remote</div><div>Anywhere</div>
    <button data-href="mailto:?body=Company: ICANN%0D%0ATitle: {title}">Email</button>
    </div><!--/.accordion-head-info --></div><!--/.accordion-block-->{next_link}"""


DETAIL = """<div class="well oracletaleocwsv2-job-description">
<span>Primary Location</span><strong>Los Angeles</strong><span>Department</span><strong>Platform</strong>
<span>Employment Type</span><strong>Full Time</strong></div>
<div class="cws-V2-reqfieldcell-right">Targeted Base Salary Low:</div><div class="cws-V2-reqfieldcell-left"><strong>142,000</strong></div>
<div class="cws-V2-reqfieldcell-right">Targeted Base Salary High:</div><div class="cws-V2-reqfieldcell-left"><strong>197,400</strong></div>
<div name="cwsJobDescription"><div><p>This position is fully remote. Build &amp; operate systems.</p></div></div><section>"""

# Live-measured spellings (2026-09-15, docs/taleo_be/2026-09-15_workplace-arrangement-field.md):
# NBF1199 states "Workplace Arrangement:" (colon and all) with values Hybrid/In-Office; Covestic
# states "Location Type" (no colon) with values Onsite/Remote.
DETAIL_HYBRID = """<div class="well oracletaleocwsv2-job-description">
<span>Primary Location</span><strong>Denver, CO</strong><span>Department</span><strong>Platform</strong>
<span>Workplace Arrangement:</span><strong>Hybrid</strong></div>
<div name="cwsJobDescription"><div><p>Build things.</p></div></div><section>"""

DETAIL_REMOTE = """<div class="well oracletaleocwsv2-job-description">
<span>Primary Location</span><strong>Austin, TX</strong><span>Department</span><strong>Platform</strong>
<span>Location Type</span><strong>Remote</strong></div>
<div name="cwsJobDescription"><div><p>Build things.</p></div></div><section>"""


def _served_board(
    listing_pages: dict[str, str], detail: str | FakeResponse
) -> tuple[TaleoBEScraper, FakeFetcher]:
    """A TBE Board whose listing pages a FakeFetcher answers by URL, and every other GET — each
    detail page — with ``detail``."""

    def route(method, url, kwargs):
        if url in listing_pages:
            return FakeResponse(text=listing_pages[url])
        return detail if isinstance(detail, FakeResponse) else FakeResponse(text=detail)

    fetcher = FakeFetcher(route)
    return TaleoBEScraper(URL, "ICANN", fetcher=fetcher), fetcher


def test_registry_and_ledger_url_slug():
    assert SCRAPERS["taleo_be"] is TaleoBEScraper
    assert get_scraper("taleo_be", "ignored").ats == "taleo_be"
    assert TaleoBEScraper.slug_from("ICANN", URL + "&act=sort") == URL
    assert (
        TaleoBEScraper(URL, "ICANN [Taleo ICANN:37@phe.tbe.taleo.net/phe03]").company
        == "ICANN"
    )


def test_alias_key_uses_the_final_canonical_tbe_url(monkeypatch):
    from headstart import http

    target = "https://lde.tbe.taleo.net/lde01/ats/careers/v2/searchResults?org=DEFEHEAL&cws=37&act=sort"

    class Response:
        url = target

        def close(self):
            pass

    seen = {}

    def fetch(*args, **kwargs):
        seen.update(kwargs)
        return Response()

    monkeypatch.setattr(http, "fetch", fetch)
    scraper = TaleoBEScraper(URL)
    assert scraper.alias_key() == target.removesuffix("&act=sort")
    # No production 429 evidence for this ATS (see docs/code-review/
    # 2026-09-15_last-5-prs-retrospective-critique.md, finding 6) — the alias fetch must not
    # route or wall, but it must still name its board in the retry log.
    assert seen["egress_board"] == scraper.board_key()
    assert "egress_group" not in seen and "egress_on" not in seen


def test_pages_with_session_relative_next_and_parses_detail():
    pages = {
        URL: _listing(
            1,
            "Platform Engineer",
            "/phe03/ats/careers/v2/searchResults?next&rowFrom=10",
        ),
        "https://phe.tbe.taleo.net/phe03/ats/careers/v2/searchResults?next&rowFrom=10": _listing(
            2, "Systems Engineer"
        ),
    }
    scraper, _fetcher = _served_board(pages, DETAIL)
    jobs = scraper.fetch()
    assert [job.id.rsplit(":", 1)[-1] for job in jobs] == ["1", "2"]
    assert jobs[0].url == (
        "https://phe.tbe.taleo.net/phe03/ats/careers/v2/"
        "viewRequisition?org=ICANN&cws=37&rid=1"
    )
    assert re.fullmatch(TaleoBEScraper.url_shape, jobs[0].url)
    assert (
        jobs[0].description == "This position is fully remote. Build & operate systems."
    )
    assert jobs[0].department == "Platform"
    assert jobs[0].company == "ICANN"
    assert jobs[0].location == "Los Angeles"
    assert jobs[0].remote is False
    assert jobs[0].employment_type == "Full Time"
    assert jobs[0].salary == "142,000 - 197,400"
    meta = to_meta(jobs[0].to_dict())
    assert meta["remote"] is True  # HeadStart's JD overlay, not Taleo-specific logic.
    assert meta["min_salary_annual"] == 142_000
    assert meta["salary_source"] == "field"


def test_repeated_next_link_marks_truncated():
    scraper, _fetcher = _served_board({}, _listing(1, "Engineer", URL))
    assert len(scraper.fetch()) == 1
    assert scraper.truncated == "listing next link looped before the Board ended"


def test_workplace_remote_mapping():
    """Both live-measured label spellings feed the same value vocabulary and cascade."""
    assert _workplace_remote("Remote") is True
    assert _workplace_remote("Onsite") is False
    assert _workplace_remote("In-Office") is False
    assert _workplace_remote("Hybrid") is None  # neither purely remote nor onsite
    assert _workplace_remote(None) is None
    assert _workplace_remote("") is None
    assert _workplace_remote("Some Unrecognized Value") is None


def test_native_field_decides_when_location_gives_no_signal():
    """A decisive native field wins even though the location string says nothing on its own."""
    scraper, _fetcher = _served_board(
        {URL: _listing(1, "Platform Engineer")}, DETAIL_REMOTE
    )
    jobs = scraper.fetch()
    assert jobs[0].location == "Austin, TX"
    assert jobs[0].remote is True


def test_hybrid_native_field_falls_through_to_location_text():
    """Hybrid is not decisive on its own (matches workday._remote_from's convention), so the
    cascade falls through to the location string — same as a board stating no field at all."""
    scraper, _fetcher = _served_board(
        {URL: _listing(1, "Platform Engineer")}, DETAIL_HYBRID
    )
    jobs = scraper.fetch()
    assert jobs[0].location == "Denver, CO"
    assert jobs[0].remote is False  # "Denver, CO" carries no remote signal of its own


def test_no_native_field_falls_back_to_is_remote():
    """A board that states no workplace field at all keeps the pre-existing behavior."""
    scraper, _fetcher = _served_board({URL: _listing(1, "Platform Engineer")}, DETAIL)
    jobs = scraper.fetch()
    assert jobs[0].location == "Los Angeles"
    assert jobs[0].remote is False  # unchanged from before this field existed


def _headed_listing(headers: list[str], fields: list[str]) -> str:
    """One card under the page's own "Sort by" select, whose options name the card's columns
    in order after the title (sortColumn=0) — HTML shape as served live 2026-09-23."""
    options = "".join(
        f'<option value="{URL}&act=sort&sortColumn={n}">{label}</option>'
        for n, label in enumerate(["Title", *headers])
    )
    divs = "".join(f'<div tabindex="0" >{field}</div>' for field in fields)
    return f"""<select class="form-select orderbyPicker" id="sel1">{options}</select>
    <div class="oracletaleocwsv2-accordion-block"><div class="oracletaleocwsv2-accordion-head-info">
    <h4><a href="/phe03/ats/careers/v2/viewRequisition?org=ICANN&amp;cws=37&amp;rid=1">Nurse</a></h4>
    {divs}
    </div><!--/.accordion-head-info --></div><!--/.accordion-block-->"""


def _served(listing: str) -> tuple[str | None, str | None]:
    """(location, department) served when the detail page states neither label."""
    bare = '<div name="cwsJobDescription"><p>Care.</p></div>'
    scraper, _fetcher = _served_board({URL: listing}, bare)
    (job,) = scraper.fetch()
    return job.location, job.department


def test_listing_columns_are_read_by_their_header_not_their_position():
    """Each tenant picks and orders its own card columns (live: HENRYMAYO, DSB, MBA)."""
    henrymayo = _headed_listing(
        ["Department", "Employment Status", "Shift Start/End Time"],
        ["Cardiology", "Per Diem", "6:30AM- 3:00PM"],
    )
    assert _served(henrymayo) == (None, "Cardiology")
    dsb = _headed_listing(
        ["Post date", "Division", "Dept/branch"],
        ["17/06/2026", "Retail Banking", "Direct Channels"],
    )
    assert _served(dsb) == (None, "Direct Channels")
    mba = _headed_listing(["Location", "Department"], ["Washington, DC", "Research"])
    assert _served(mba) == ("Washington, DC", "Research")


def test_posted_at_falls_back_to_the_json_ld_date_posted():
    """No sampled tenant renders a "Date Posted" label; 9 of 15 state JSON-LD `datePosted`
    (live 2026-09-22), in the shape NBF1199 rid=11231 serves."""
    ld = '<script type="application/ld+json">{"datePosted" : "2026-08-12 00:00:00.0"}</script>'
    scraper, _fetcher = _served_board({URL: _listing(1, "Engineer")}, ld + DETAIL)
    assert scraper.fetch()[0].posted_at == "2026-08-12T00:00:00+00:00"


def test_salary_bounds_drop_their_bonus_tail_before_joining():
    """ICANN states each bound as "40,000.00 + 10% Bonus + Benefits" (live 2026-09-22);
    joined whole, no range survives and `salary.extract` keeps only the floor."""
    from headstart import salary

    scraper = TaleoBEScraper(URL, "ICANN")
    labels = {
        "targeted base salary low:": "40,000.00 + 10% Bonus + Benefits",
        "targeted base salary high:": "55,000.00 + 10% Bonus + Benefits",
    }
    field = scraper._salary_field(labels)
    assert field == "40,000.00 - 55,000.00"
    span = salary.extract(field, None, "taleo_be")
    assert (span.min_annual, span.max_annual) == (40_000, 55_000)


def test_pay_range_min_and_max_labels_are_joined():
    """ASPENGOV states its bounds as "Pay Range (Min):" / "Pay Range (Max):" (live 2026-09-23),
    which neither the salary low/high pair nor the single-value fallback read."""
    scraper = TaleoBEScraper(URL, "ICANN")
    labels = {"pay range (max)": "27.93", "pay range (min)": "19.14"}
    assert scraper._salary_field(labels) == "19.14 - 27.93"


def test_custom_field_labels_match_despite_their_trailing_colon():
    """NBF1199 rid=11231 renders "Employment Type: " in a custom-field cell (live 2026-09-22)."""
    page = (
        '<div class="cws-V2-reqfieldcell-right"> Employment Type: </div>'
        '<div class="cws-V2-reqfieldcell-left"><strong>Full time</strong></div>'
        '<div class="cws-V2-reqfieldcell-right"> Workplace Arrangement: </div>'
        '<div class="cws-V2-reqfieldcell-left"><strong>Remote</strong></div>'
        '<div name="cwsJobDescription"><p>Count.</p></div>'
    )
    scraper, _fetcher = _served_board({URL: _listing(1, "Accountant")}, page)
    (job,) = scraper.fetch()
    assert job.employment_type == "Full time"
    assert job.remote is True


def test_posted_at_parses_the_fractional_second_shape_the_live_page_serves():
    # Real live value, NBF1199 rid=11231, verified 2026-09-22: a bare `%Y-%m-%d %H:%M:%S` has
    # no fractional-second group to consume the trailing ".0", so it fails to parse this exactly
    # as the pre-fix format list did.
    assert _posted_at("2026-08-12 00:00:00.0") == "2026-08-12T00:00:00+00:00"


def test_posted_at_still_parses_the_pre_existing_formats():
    assert _posted_at("08/12/2026") == "2026-08-12T00:00:00+00:00"
    assert _posted_at("2026-08-12") == "2026-08-12T00:00:00+00:00"
    assert _posted_at("2026-08-12 09:30:00") == "2026-08-12T09:30:00+00:00"


def test_posted_at_none_on_unparseable_input():
    assert _posted_at(None) is None
    assert _posted_at("") is None
    assert _posted_at("not a date") is None


# The second live layout (YKHC, 2026-09-24): no `cwsJobDescription` anchor, but real labels.
DETAIL_WITHOUT_BODY = """<div class="well oracletaleocwsv2-job-description">
<span>Primary Location</span><strong>Bethel, AK</strong><span>Department</span><strong>Clinic</strong></div>"""


def test_a_detail_page_without_a_body_keeps_its_labels_and_is_a_labelled_gap(caplog):
    """YKHC's layout carries no description anchor on 128 of 128 pages yet states a location and
    department on each (measured 2026-09-24): the Job keeps those, and the page still counts as
    the gap it is, named, on the Board's one gap line."""
    scraper, _fetcher = _served_board(
        {URL: _listing(1, "Platform Engineer")}, DETAIL_WITHOUT_BODY
    )

    with caplog.at_level("INFO"):
        (job,) = scraper.fetch()

    assert (job.location, job.department, job.description) == (
        "Bethel, AK",
        "Clinic",
        None,
    )
    assert scraper.telemetry["detail_losses"] == 1
    assert (
        "1/1 detail pages missing (200 without a parseable description body x1)"
        in caplog.text
    )


def test_a_refused_detail_page_is_labelled_by_its_status_and_the_job_ships():
    scraper, _fetcher = _served_board(
        {URL: _listing(1, "Platform Engineer")}, FakeResponse(404, "gone")
    )

    (job,) = scraper.fetch()

    assert job.title == "Platform Engineer"  # the listing row alone still makes the Job
    assert job.description is None
    assert scraper.detail_losses == {"HTTP 404": 1}


def test_every_detail_request_rides_the_thread_path_with_the_default_headers(
    monkeypatch,
):
    """`async_fanout = False` on a measurement (ADR-0167), and the request itself is exactly
    the one `_get` always sent."""
    monkeypatch.delenv("HEADSTART_ASYNC_FANOUT", raising=False)
    scraper, fetcher = _served_board({URL: _listing(1, "Platform Engineer")}, DETAIL)
    monkeypatch.setattr(
        type(scraper),
        "fan_out_async",
        lambda *args, **kwargs: pytest.fail("the multiplexed path was taken"),
    )

    scraper.fetch()

    detail_request = fetcher.requests[-1]
    assert detail_request.url.endswith("viewRequisition?org=ICANN&cws=37&rid=1")
    assert detail_request.kwargs["headers"] == dict(DEFAULT_REQUEST_HEADERS)
    assert detail_request.kwargs["timeout"] == 30


# --- the Board's company name ---------------------------------------------------------------

BOARD_KEY = "G94W9A:37@lde.tbe.taleo.net/lde01"
KEYED_URL = (
    "https://lde.tbe.taleo.net/lde01/ats/careers/v2/searchResults?org=G94W9A&cws=37"
)


class _Feed(FakeResponse):
    """An RSS feed read in chunks; records how many it was asked for."""

    def __init__(self, text: str, status_code: int = 200) -> None:
        super().__init__(status_code, text)
        self.chunks_read = 0

    def iter_content(self):
        for start in range(0, len(self.content), 1024):
            self.chunks_read += 1
            yield self.content[start : start + 1024]


def _feed_board(feed: _Feed, url: str = KEYED_URL, company: str = BOARD_KEY):
    seen: list[str] = []

    def route(method, fetched, kwargs):
        seen.append(fetched)
        return feed

    return TaleoBEScraper(url, company, fetcher=FakeFetcher(route)), seen


def _rss(title: str, items: int = 0) -> str:
    item = (
        "<item><title>Engineer</title><description>"
        + "x" * 2000
        + "</description></item>"
    )
    return f"<rss><channel><title>{title}</title>{item * items}</channel></rss>"


@pytest.fixture(autouse=True)
def _no_cached_names(monkeypatch):
    monkeypatch.setattr("headstart.scrapers.taleo_be._ORG_NAMES", {})


@pytest.mark.parametrize(
    ("channel_title", "expected"),
    [
        # channel titles live 2026-09-24
        ("Nektar Therapeutics Job Feed", "Nektar Therapeutics"),
        ("Egov Select VZW functiefeed", "Egov Select VZW"),
        ("Feed lavoro PARFOIS", "PARFOIS"),
        ("Feed de Cargo de PARFOIS", "PARFOIS"),
        ("Carga de puesto de Wagman, Inc.", "Wagman, Inc."),
        (
            "Flux d&apos;offres d&apos;emploi de Arobas Personnel Inc.",
            "Arobas Personnel Inc.",
        ),
        ("Defence Construction Canada fil d'emploi", "Defence Construction Canada"),
        ("Job Not Available", BOARD_KEY),
    ],
)
def test_an_unnamed_board_is_named_by_its_rss_channel_title(channel_title, expected):
    scraper, seen = _feed_board(_Feed(_rss(channel_title)))
    scraper.resolve_company()
    assert scraper.company == expected
    assert seen == ["https://lde.tbe.taleo.net/lde01/ats/servlet/Rss?org=G94W9A&cws=37"]


def test_the_feed_is_read_only_as_far_as_its_title():
    feed = _Feed(_rss("Nektar Therapeutics Job Feed", items=40))
    scraper, _ = _feed_board(feed)
    scraper.resolve_company()
    assert scraper.company == "Nektar Therapeutics"
    assert feed.chunks_read == 8  # 8 KB of an ~80 KB feed


def test_a_named_board_makes_no_request():
    scraper, seen = _feed_board(_Feed(_rss("Other Job Feed")), company="ICANN")
    scraper.resolve_company()
    assert scraper.company == "ICANN" and seen == []


def test_an_orgs_other_boards_reuse_its_name():
    first, _ = _feed_board(_Feed(_rss("Three Saints Bay, LLC Job Feed")))
    first.resolve_company()
    second, seen = _feed_board(
        _Feed(_rss("unused")), url=KEYED_URL.replace("cws=37", "cws=46")
    )
    second.resolve_company()
    assert second.company == "Three Saints Bay, LLC" and seen == []


def test_a_failed_feed_leaves_the_board_key():
    scraper, _ = _feed_board(_Feed("", status_code=404))
    scraper.resolve_company()
    assert scraper.company == BOARD_KEY
