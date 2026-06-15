import json

import headstart.pipeline as pipeline
from headstart.config import CompanyRef
from headstart.models import Job
from headstart.pipeline import RunResult, build_feed, scrape_all, write_feed


def make_job(job_id: str) -> Job:
    return Job(
        id=job_id, ats="x", company="C", title="T", location=None, remote=None,
        department=None, url="u", posted_at=None, scraped_at="2026-01-01T00:00:00+00:00",
    )


class FakeScraper:
    def __init__(self, jobs=None, error=None):
        self._jobs = jobs or []
        self._error = error

    def fetch(self):
        if self._error:
            raise self._error
        return self._jobs


def test_scrape_all_dedupes_and_isolates_errors(monkeypatch):
    job_a, job_a_dup, job_b = make_job("x:a:1"), make_job("x:a:1"), make_job("x:b:2")

    def fake_get(ats, slug, name=None):
        return {
            "good": FakeScraper([job_a, job_b]),
            "dup": FakeScraper([job_a_dup]),
            "bad": FakeScraper(error=RuntimeError("boom")),
        }[slug]

    monkeypatch.setattr(pipeline, "get_scraper", fake_get)
    companies = [CompanyRef("x", "good"), CompanyRef("x", "dup"), CompanyRef("x", "bad")]

    result = scrape_all(companies)

    assert {j.id for j in result.jobs} == {"x:a:1", "x:b:2"}  # deduped
    assert len(result.jobs) == 2
    assert "x:bad" in result.errors and "boom" in result.errors["x:bad"]


def test_build_and_write_feed(tmp_path):
    result = RunResult(jobs=[make_job("x:a:1")], errors={"x:bad": "oops"})
    feed = build_feed(result)
    assert feed["count"] == 1
    assert feed["jobs"][0]["id"] == "x:a:1"
    assert "generated_at" in feed

    out = tmp_path / "docs" / "jobs.json"
    write_feed(feed, out)
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["count"] == 1
    assert loaded["errors"] == {"x:bad": "oops"}
