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


def test_scrape_all_streams_without_retaining_when_feed_off(monkeypatch, tmp_path):
    """collect_feed=False (the harvest path): jobs stream to disk but are NOT held in memory."""
    jobs = [make_job(f"greenhouse:acme:{i}", ats="greenhouse") for i in range(5)]
    monkeypatch.setattr(pipeline, "get_scraper", lambda *a, **k: FakeScraper(jobs))

    result = scrape_all([CompanyRef("greenhouse", "acme")], jobs_dir=tmp_path, collect_feed=False)

    assert result.jobs == []          # nothing retained -> no OOM at scale
    assert result.unique == 5         # but the count is still reported
    assert result.boards == 1
    assert len((tmp_path / "greenhouse.jsonl").read_text("utf-8").splitlines()) == 5


def test_scrape_all_dedupes_duplicate_boards_in_jsonl(monkeypatch, tmp_path):
    """Duplicate slug forms of one board (e.g. dollartree x3) must not triplicate its Jobs."""
    shared = [make_job("workday:dollartree:1", ats="workday"),
              make_job("workday:dollartree:2", ats="workday")]
    monkeypatch.setattr(pipeline, "get_scraper", lambda *a, **k: FakeScraper(shared))
    companies = [CompanyRef("workday", "dollartree"),
                 CompanyRef("workday", "dollartree/dollartreeus"),
                 CompanyRef("workday", "dollartree.wd5.myworkdayjobs.com/dollartreeus")]

    result = scrape_all(companies, jobs_dir=tmp_path, collect_feed=False)

    assert result.unique == 2  # two distinct ids, not six
    assert len((tmp_path / "workday.jsonl").read_text("utf-8").splitlines()) == 2


def test_scrape_all_resume_skips_completed_boards(monkeypatch, tmp_path):
    """A resume run skips boards already in .done and appends rather than re-scraping."""
    calls: list[str] = []

    def fake_get(ats, slug, name=None):
        calls.append(slug)
        return FakeScraper([make_job(f"{ats}:{slug}:1", ats=ats)])

    monkeypatch.setattr(pipeline, "get_scraper", fake_get)
    companies = [CompanyRef("greenhouse", "a"), CompanyRef("greenhouse", "b")]

    r1 = scrape_all(companies, jobs_dir=tmp_path, collect_feed=False)
    assert sorted(calls) == ["a", "b"]
    assert r1.boards == 2
    assert (tmp_path / ".done").read_text("utf-8").split() == ["greenhouse:a", "greenhouse:b"]
    assert len((tmp_path / "greenhouse.jsonl").read_text("utf-8").splitlines()) == 2

    # Resume: both boards are done -> none re-scraped, JSONL untouched (append, no new writes).
    calls.clear()
    r2 = scrape_all(companies, jobs_dir=tmp_path, collect_feed=False, resume=True)
    assert calls == []
    assert r2.boards == 0
    assert len((tmp_path / "greenhouse.jsonl").read_text("utf-8").splitlines()) == 2

    # Resume with a new board appended -> only the new one scrapes, its line is appended.
    companies.append(CompanyRef("greenhouse", "c"))
    r3 = scrape_all(companies, jobs_dir=tmp_path, collect_feed=False, resume=True)
    assert calls == ["c"]
    assert r3.boards == 1
    assert len((tmp_path / "greenhouse.jsonl").read_text("utf-8").splitlines()) == 3
