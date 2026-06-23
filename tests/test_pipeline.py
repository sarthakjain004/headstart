import json

import headstart.pipeline as pipeline
from headstart.config import CompanyRef
from headstart.models import Job
from headstart.pipeline import RunResult, build_feed, scrape_all, write_feed


def make_job(job_id: str, ats: str = "x", description: str | None = None) -> Job:
    return Job(
        id=job_id, ats=ats, company="C", title="T", location=None, remote=None,
        department=None, url="u", posted_at=None, scraped_at="2026-01-01T00:00:00+00:00",
        description=description,
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


def test_scrape_all_streams_per_ats_jsonl(monkeypatch, tmp_path):
    gh1 = make_job("greenhouse:acme:1", ats="greenhouse", description="multi\nline, with comma")
    gh2 = make_job("greenhouse:acme:2", ats="greenhouse")
    lev = make_job("lever:beta:9", ats="lever")

    def fake_get(ats, slug, name=None):
        return {
            "acme": FakeScraper([gh1, gh2]),
            "beta": FakeScraper([lev]),
            "bad": FakeScraper(error=RuntimeError("boom")),
        }[slug]

    monkeypatch.setattr(pipeline, "get_scraper", fake_get)
    companies = [CompanyRef("greenhouse", "acme"), CompanyRef("lever", "beta"),
                 CompanyRef("greenhouse", "bad")]

    result = scrape_all(companies, jobs_dir=tmp_path)

    # Combined result is unchanged: still deduped and error-isolated.
    assert {j.id for j in result.jobs} == {"greenhouse:acme:1", "greenhouse:acme:2", "lever:beta:9"}
    assert "greenhouse:bad" in result.errors

    # Each ATS streamed to its own JSONL, one full Job per line; the failed board wrote nothing.
    gh_lines = (tmp_path / "greenhouse.jsonl").read_text(encoding="utf-8").splitlines()
    lev_lines = (tmp_path / "lever.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(gh_lines) == 2 and len(lev_lines) == 1
    record = json.loads(gh_lines[0])
    assert set(record) == set(Job.__dataclass_fields__)  # full Job, not a reduced row
    assert record["description"] == "multi\nline, with comma"
