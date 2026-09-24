"""The description container on a real Taleo Business Edition page.

Fixture cut from a live page (`Buffalorock`, 2026-09-16). The shape that matters: on most pages
every ``<section>`` sits *before* the ``name="cwsJobDescription"`` anchor, so a terminator that
looks forward for one never fires. Measured live over 14 random hiring tenants the same day: the
old ``(?=<section\\b)`` terminator matched 5 and dropped the description on the other 9;
depth-counting the container parses 13 of the 14 and loses none of the 5.
"""

from pathlib import Path

from headstart.scrapers import taleo_be

FIXTURE = Path(__file__).parent / "fixtures" / "taleo_be_detail.html"


def _page() -> str:
    return FIXTURE.read_text()


def test_fixture_has_no_section_after_the_anchor():
    """Pin the property the old terminator depended on, so this test explains itself later."""
    page = _page()
    anchor = page.index('name="cwsJobDescription"')
    assert "<section" in page[:anchor]
    assert "<section" not in page[anchor:]


def test_description_body_is_extracted():
    page = _page()
    body = taleo_be._description_html(page)
    assert body is not None, "the description container was not found"
    assert "Account Merchandiser" in body


def test_description_stops_at_its_own_container():
    """Depth-counting must close on the container's own </div>, not run to end of page."""
    body = taleo_be._description_html(page := _page())
    assert body is not None
    assert "FOOTER MUST NOT APPEAR IN DESCRIPTION" not in body
    assert len(body) < len(page)


def test_a_page_without_the_anchor_returns_none():
    """A page with neither layout's container must be reported, not silent."""
    assert (
        taleo_be._description_html("<html><body><p>no anchor here</p></body></html>")
        is None
    )


def test_unbalanced_markup_refuses_rather_than_swallowing_the_page():
    """A body whose container never closes must not drag the page footer into the description."""
    page = (FIXTURE.parent / "taleo_be_detail_unbalanced.html").read_text()
    assert taleo_be._description_html(page) is None


SECOND_LAYOUT = FIXTURE.parent / "taleo_be_detail_second_layout.html"


def test_the_second_layout_body_is_read_from_its_main_column():
    """INVXIS, live 2026-09-25: no ``cwsJobDescription`` anchor. The description sits in the
    ``col-md-8`` column beside the ``well`` header; 1,031-1,100 details a run were lost to this
    layout on 13-17 Boards (``docs/pipeline/2026-09-24_five-run-log-review.md`` finding 5)."""
    text = taleo_be._text(taleo_be._description_html(SECOND_LAYOUT.read_text()))
    assert text is not None
    assert "RealmOne was built on the principle that people matter" in text
    assert "Apply Now" not in text  # the column's own Back / Share / Apply buttons


def test_the_second_layout_drops_inline_style_and_its_button_bar():
    page = (
        '<div class="well oracletaleocwsv2-job-description">Header</div>'
        '<div class="col-xs-12 col-sm-12 col-md-8">'
        "<style>li{ margin: -8px;}</style><div><p>Build things.</p></div>"
        '<div class="oracletaleocwsv2-button-navigation oracletaleocwsv2-job-description clearfix">'
        "<a>Back</a><div>Share</div><a>Apply Now</a></div>"
        "</div><footer>FOOTER</footer>"
    )
    assert taleo_be._text(taleo_be._description_html(page)) == "Build things."


def test_a_main_column_without_the_job_header_is_not_read():
    """``col-md-8`` is a Bootstrap class any page may carry; only a job page's column counts."""
    page = '<div class="col-xs-12 col-sm-12 col-md-8"><p>Search our openings</p></div>'
    assert taleo_be._description_html(page) is None
