import re

from headstart.ingest.doc_prep import to_meta
from headstart.scrapers.registry import SCRAPERS, get_scraper
from headstart.scrapers.taleo_be import TaleoBEScraper, _workplace_remote

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


def test_pages_with_session_relative_next_and_parses_detail(monkeypatch):
    scraper = TaleoBEScraper(URL, "ICANN")
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

    def get(url=None):
        if url in pages:
            return pages[url]
        return DETAIL

    monkeypatch.setattr(scraper, "_get", get)
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


def test_repeated_next_link_marks_truncated(monkeypatch):
    scraper = TaleoBEScraper(URL)
    monkeypatch.setattr(scraper, "_get", lambda url=None: _listing(1, "Engineer", URL))
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


def test_native_field_decides_when_location_gives_no_signal(monkeypatch):
    """A decisive native field wins even though the location string says nothing on its own."""
    scraper = TaleoBEScraper(URL, "ICANN")

    def get(url=None):
        if url == URL:
            return _listing(1, "Platform Engineer")
        return DETAIL_REMOTE

    monkeypatch.setattr(scraper, "_get", get)
    jobs = scraper.fetch()
    assert jobs[0].location == "Austin, TX"
    assert jobs[0].remote is True


def test_hybrid_native_field_falls_through_to_location_text(monkeypatch):
    """Hybrid is not decisive on its own (matches workday._remote_from's convention), so the
    cascade falls through to the location string — same as a board stating no field at all."""
    scraper = TaleoBEScraper(URL, "ICANN")

    def get(url=None):
        if url == URL:
            return _listing(1, "Platform Engineer")
        return DETAIL_HYBRID

    monkeypatch.setattr(scraper, "_get", get)
    jobs = scraper.fetch()
    assert jobs[0].location == "Denver, CO"
    assert jobs[0].remote is False  # "Denver, CO" carries no remote signal of its own


def test_no_native_field_falls_back_to_is_remote(monkeypatch):
    """A board that states no workplace field at all keeps the pre-existing behavior."""
    scraper = TaleoBEScraper(URL, "ICANN")
    monkeypatch.setattr(
        scraper,
        "_get",
        lambda url=None: _listing(1, "Platform Engineer") if url == URL else DETAIL,
    )
    jobs = scraper.fetch()
    assert jobs[0].location == "Los Angeles"
    assert jobs[0].remote is False  # unchanged from before this field existed
