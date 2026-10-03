"""Public JobScore HTML, recorded 2026-10-03; styles and unrelated scripts removed."""

from pathlib import Path

from fake_fetcher import FakeFetcher, FakeResponse

from headstart.scrapers.registry import get_scraper

FIXTURES = Path(__file__).parent / "fixtures"


def test_public_pages_never_poll_a_feed_or_serve_confirmed_gone_postings():
    page = (FIXTURES / "jobscore_pricefx_public.html").read_text()
    detail = (FIXTURES / "jobscore_pricefx_detail.html").read_text()

    def route(method, url, kwargs):
        assert "feed." not in url
        if url.endswith("/careers/pricefx"):
            return FakeResponse(text=page)
        return (
            FakeResponse(text=detail)
            if "/solution-strategist-" in url
            else FakeResponse(404)
        )

    scraper = get_scraper("jobscore", "pricefx", fetcher=FakeFetcher(route))
    jobs = scraper.fetch()
    assert len(jobs) == 1
    job = next(j for j in jobs if j.title == "Solution Strategist")
    assert job.id == "jobscore:pricefx:dvcDLS919kwikf8MefTVkM"
    assert job.department == "Solution Strategy"
    assert job.posted_at == "2026-09-28T17:00:55Z"
    assert job.remote is True
    assert "base salary range" in job.description
    assert job.description.endswith("#BI-REMOTE")
    assert job.salary == "145000.0-170000.0 USD YEAR"
    assert scraper.detail_losses["HTTP 404"] == 2


def test_missing_jsonld_still_has_the_full_visible_description_and_no_invented_date():
    page = (FIXTURES / "jobscore_jobscore_public.html").read_text()
    detail = (FIXTURES / "jobscore_jobscore_detail.html").read_text()
    scraper = get_scraper(
        "jobscore",
        "jobscore",
        fetcher=FakeFetcher(
            lambda m, u, k: FakeResponse(text=detail if "/jobs/" in u else page)
        ),
    )
    (job,) = scraper.fetch()
    assert job.id == "jobscore:jobscore:dpi9LtxFTluy3ZQtfTfYWo"
    assert job.company == "JobScore"
    assert job.title == "Senior Front-End Engineer"
    assert job.remote is True
    assert job.department == "Engineering"
    assert job.employment_type == "Full Time"
    assert "7+ years of commercial software engineering" in job.description
    assert job.posted_at is None


def test_transient_detail_failures_keep_listed_jobs_and_explicit_empty_boards_are_empty():
    page = (FIXTURES / "jobscore_pricefx_public.html").read_text()
    scraper = get_scraper(
        "jobscore",
        "pricefx",
        fetcher=FakeFetcher(
            lambda m, u, k: (
                FakeResponse(503) if "/jobs/" in u else FakeResponse(text=page)
            )
        ),
    )
    jobs = scraper.fetch()
    assert len(jobs) == 3
    assert all(j.description is None for j in jobs)
    assert scraper.detail_losses["HTTP 503"] == 3
    empty = (FIXTURES / "jobscore_blueleaf_public.html").read_text()
    assert (
        get_scraper(
            "jobscore",
            "blueleaf",
            fetcher=FakeFetcher(lambda *_: FakeResponse(text=empty)),
        ).fetch()
        == []
    )
