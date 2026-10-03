"""Public hosted board data recorded 2026-10-03, trimmed to two postings."""

from pathlib import Path

from headstart.scrapers.registry import get_scraper

RAW = (Path(__file__).parent / "fixtures/comeet_port.html").read_text()


def test_hosted_board_supplies_complete_descriptions_and_honest_workplace():
    jobs = get_scraper("comeet", "port/59.004").parse(RAW, "2026-10-03T00:00:00Z")
    account, engineer = jobs
    assert account.company == "Port"
    assert account.remote is None  # location.is_remote is true, but workplace is Hybrid
    assert engineer.title == "Senior Backend Engineer"
    assert engineer.department == "R&D"
    assert "Requirements" in engineer.description
    assert engineer.posted_at is None  # only time_updated is supplied, not date posted
    assert engineer.url.startswith("https://www.comeet.com/jobs/port/59.004/")


def test_renaming_the_public_label_keeps_the_same_board_and_job_identity():
    # Both names read company UID 9A.006 and the same posting set on 2026-10-03.
    old = get_scraper("comeet", "echosoftware/9a.006")
    new = get_scraper("comeet", "echo/9a.006")
    assert old.board_key() == new.board_key() == "comeet:9a.006"
    assert old.job_id("44.A00") == new.job_id("44.A00")


def test_consent_or_marketing_html_is_a_failed_read_and_internal_posts_are_hidden():
    import pytest

    with pytest.raises(ValueError):
        get_scraper("comeet", "port/59.004").parse(
            "<title>Request for consent</title>", "now"
        )
    hidden = RAW.replace('"is_internal": false', '"is_internal": true')
    assert get_scraper("comeet", "port/59.004").parse(hidden, "now") == []
