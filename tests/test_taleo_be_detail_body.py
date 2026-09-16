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
    """The second live layout (YKHC, INVXIS) carries no anchor; it must be reported, not silent."""
    assert (
        taleo_be._description_html("<html><body><p>no anchor here</p></body></html>")
        is None
    )


def test_unbalanced_markup_refuses_rather_than_swallowing_the_page():
    """A body whose container never closes must not drag the page footer into the description."""
    page = (FIXTURE.parent / "taleo_be_detail_unbalanced.html").read_text()
    assert taleo_be._description_html(page) is None
