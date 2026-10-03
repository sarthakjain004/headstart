"""Real PageUp RSS items captured 2026-10-03; descriptions are not invented."""

from pathlib import Path

import pytest
from fake_fetcher import FakeFetcher, FakeResponse

from headstart.network import http
from headstart.scrapers.base import BoardUnreadable
from headstart.scrapers.pageup import PageUpScraper, feed_items

FIXTURES = Path(__file__).parent / "fixtures"
SCRAPED_AT = "2026-10-03T00:00:00+00:00"


def test_feed_uses_full_namespaced_description_and_all_locations_and_categories():
    scraper = PageUpScraper("1083/cw/en", "Kinetic IT")
    jobs = scraper.parse((FIXTURES / "pageup_kinetic.xml").read_text(), SCRAPED_AT)
    assert len(jobs) == 3
    first, engineer, interest = jobs
    assert first.id == "pageup:1083/cw/en:495930"
    assert first.location == "Melbourne, VIC; Perth, WA"
    assert first.department == "Marketing & Communications"
    assert first.posted_at == "2026-10-01T01:00:00+00:00"
    assert engineer.title == "Senior ServiceNow Developer"
    assert engineer.department == "Developers & Programmers"
    assert "Vulnerability Management" in engineer.description
    assert len(engineer.description) > 1000
    assert engineer.url == "https://careers.pageuppeople.com/1083/cw/en/job/495865"
    assert engineer.company == "Kinetic IT"
    assert engineer.salary is None
    assert interest.employment_type == "Full Time,Part Time"
    assert len(interest.location.split("; ")) == 7
    assert interest.department.count("Cyber Security") == 1


def test_slug_retains_account_channel_and_locale_but_not_job_or_search_filters():
    assert (
        PageUpScraper.slug_from(
            "kinetic",
            "https://careers.pageuppeople.com/1083/cw/en/job/495865?search-keyword=engineer",
        )
        == "1083/cw/en"
    )
    assert PageUpScraper.slug_from("873/sf/en-us", "") == "873/sf/en-us"
    assert (
        PageUpScraper.slug_from(
            "", "https://careers.pageuppeople.com/mob/1083/cw/en/listing/"
        )
        == "1083/cw/en"
    )


def test_fetch_reads_full_rss_and_names_the_board_from_its_public_page():
    def route(_method, url, _kwargs):
        if url.endswith("/rss"):
            return FakeResponse(text=(FIXTURES / "pageup_kinetic.xml").read_text())
        return FakeResponse(
            text="<title>Kinetic IT / Careers - Browse Current Jobs</title>"
        )

    scraper = PageUpScraper("1083/cw/en", fetcher=FakeFetcher(route))
    jobs = scraper.fetch()
    assert len(jobs) == 3
    assert {j.company for j in jobs} == {"Kinetic IT"}
    assert scraper.has_detail_pass is False


def test_migrated_board_cannot_serve_a_retained_feed_with_generic_search_job_links():
    def route(_method, url, _kwargs):
        if url.endswith("/rss"):
            return FakeResponse(text=(FIXTURES / "pageup_kinetic.xml").read_text())
        return FakeResponse(
            text="new career search", url="https://careers.example.com/jobs/search"
        )

    scraper = PageUpScraper("1083/cw/en", fetcher=FakeFetcher(route))
    with pytest.raises(http.RequestsError, match="gone"):
        scraper.fetch_raw()


@pytest.mark.parametrize(
    "account,slug", [("1032", "1032/cw/en"), ("539", "539/cawtwo/en")]
)
def test_http_200_meta_refresh_cannot_hide_a_generic_search_migration(account, slug):
    def route(_method, url, _kwargs):
        fixture = (
            f"pageup_{account}_migration_feed.xml"
            if url.endswith("/rss")
            else f"pageup_{account}_soft_migration.html"
        )
        return FakeResponse(text=(FIXTURES / fixture).read_text())

    scraper = PageUpScraper(slug, fetcher=FakeFetcher(route))
    with pytest.raises(http.RequestsError, match="gone"):
        scraper.fetch_raw()


def test_a_fragment_addressed_external_job_is_unknown_not_proven_gone():
    from headstart.scrapers.pageup import MigratedBoard

    def route(_method, url, _kwargs):
        if url.endswith("/rss"):
            return FakeResponse(
                text=(FIXTURES / "pageup_1032_migration_feed.xml").read_text()
            )
        page = (FIXTURES / "pageup_1032_soft_migration.html").read_text()
        return FakeResponse(
            text=page.replace(
                "https://careers.g8education.edu.au/jobs/search",
                "https://example.com/#job/521876",
            )
        )

    with pytest.raises(BoardUnreadable) as caught:
        PageUpScraper("1032/cw/en", fetcher=FakeFetcher(route)).fetch_raw()
    assert not isinstance(caught.value, MigratedBoard)


def test_one_closed_job_does_not_declare_an_externally_fronted_board_dead():
    def route(_method, url, _kwargs):
        if url.endswith("/rss"):
            return FakeResponse(text=(FIXTURES / "pageup_kinetic.xml").read_text())
        if "/job/" in url:
            return FakeResponse(404, "gone posting")
        return FakeResponse(
            text="current branded career site", url="https://example.com/careers"
        )

    with pytest.raises(BoardUnreadable, match="one retained"):
        PageUpScraper("1083/cw/en", fetcher=FakeFetcher(route)).fetch_raw()


def test_generic_jobs_title_is_not_adopted_as_the_employer():
    def route(_method, url, _kwargs):
        body = (
            (FIXTURES / "pageup_kinetic.xml").read_text()
            if url.endswith("/rss")
            else "<title>Jobs - Recent Jobs</title>"
        )
        return FakeResponse(text=body)

    scraper = PageUpScraper("1041/cw/en", fetcher=FakeFetcher(route))
    scraper.fetch_raw()
    assert scraper.company == "1041/cw/en"


def test_partial_remote_is_hybrid_and_does_not_match_remote_only():
    jobs = PageUpScraper("865/cw/en-us", "Lehigh University").parse(
        (FIXTURES / "pageup_partial_remote.xml").read_text(), SCRAPED_AT
    )
    assert len(jobs) == 1
    assert "Partial Remote" in jobs[0].location
    assert jobs[0].remote is None


def test_explicit_hybrid_category_overrides_an_onsite_location_inference():
    jobs = PageUpScraper("859/cw/en-us").parse(
        (FIXTURES / "pageup_hybrid_categories.xml").read_text(), SCRAPED_AT
    )
    assert len(jobs) == 5
    assert all(j.remote is None for j in jobs)


def test_unrelated_rss_or_an_item_with_mismatched_identity_is_not_a_board():
    with pytest.raises(BoardUnreadable):
        feed_items(
            "<rss><channel><link>https://example.com/news</link></channel></rss>"
        )
    fixture = (FIXTURES / "pageup_kinetic.xml").read_text()
    with pytest.raises(BoardUnreadable):
        feed_items(
            fixture.replace(
                "<job:refNo>495930</job:refNo>", "<job:refNo>999</job:refNo>"
            )
        )
