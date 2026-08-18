import json
import time

import pytest

import headstart.harvest as harvest
from headstart.board_cost import read_shard_rows
from headstart.config import CompanyRef
from headstart.harvest import build_feed, scrape_all, write_feed
from headstart.models import Job


def make_job(job_id: str, ats: str = "x", description: str | None = None) -> Job:
    return Job(
        id=job_id,
        ats=ats,
        company="C",
        title="T",
        location=None,
        remote=None,
        department=None,
        url="u",
        posted_at=None,
        scraped_at="2026-01-01T00:00:00+00:00",
        description=description,
    )


class FakeScraper:
    # Every real scraper inherits this from BaseScraper and scrape_all reads it unguarded, so a
    # double without it is not a stand-in for a scraper (ADR-0053).
    truncated: str | None = None

    def __init__(self, jobs=None, error=None):
        self._jobs = jobs or []
        self._error = error

    def fetch(self):
        if self._error:
            raise self._error
        return self._jobs


def test_scrape_all_dedupes_and_isolates_errors(monkeypatch, tmp_path):
    job_a, job_a_dup, job_b = make_job("x:a:1"), make_job("x:a:1"), make_job("x:b:2")

    def fake_get(ats, slug, name=None, **_):
        return {
            "good": FakeScraper([job_a, job_b]),
            "dup": FakeScraper([job_a_dup]),
            "bad": FakeScraper(error=RuntimeError("boom")),
        }[slug]

    monkeypatch.setattr(harvest, "get_scraper", fake_get)
    companies = [
        CompanyRef("x", "good"),
        CompanyRef("x", "dup"),
        CompanyRef("x", "bad"),
    ]

    result = scrape_all(companies, jobs_dir=tmp_path)

    # x:a:1 came from two boards but is written once; the failed board is isolated.
    ids = [
        json.loads(line)["id"]
        for line in (tmp_path / "x.jsonl").read_text("utf-8").splitlines()
    ]
    assert sorted(ids) == ["x:a:1", "x:b:2"]
    assert result.unique == 2
    assert "x:bad" in result.errors and "boom" in result.errors["x:bad"]


def test_build_and_write_feed(monkeypatch, tmp_path):
    """build_feed reads the streamed .jsonl back; errors are carried in from the run."""

    def fake_get(ats, slug, name=None, **_):
        if slug == "bad":
            return FakeScraper(error=RuntimeError("oops"))
        return FakeScraper([make_job("x:a:1")])

    monkeypatch.setattr(harvest, "get_scraper", fake_get)
    result = scrape_all(
        [CompanyRef("x", "a"), CompanyRef("x", "bad")], jobs_dir=tmp_path
    )

    feed = build_feed(tmp_path, result.errors)
    assert feed["count"] == 1
    assert feed["jobs"][0]["id"] == "x:a:1"
    assert "generated_at" in feed
    assert "x:bad" in feed["errors"]  # not in the .jsonl; passed in from the run

    out = tmp_path / "docs" / "jobs.json"
    write_feed(feed, out)
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["count"] == 1
    assert "x:bad" in loaded["errors"]


def test_build_feed_dedupes_duplicate_jsonl_lines(tmp_path):
    """A crash-and-resume can re-emit a board's lines; build_feed dedups by id on read."""
    line1 = json.dumps(make_job("greenhouse:acme:1", ats="greenhouse").to_dict()) + "\n"
    line2 = json.dumps(make_job("greenhouse:acme:2", ats="greenhouse").to_dict()) + "\n"
    # acme:1 appears twice (re-emitted on resume), acme:2 once.
    (tmp_path / "greenhouse.jsonl").write_text(line1 + line2 + line1, encoding="utf-8")

    feed = build_feed(tmp_path, errors={})
    assert feed["count"] == 2  # deduped
    assert sorted(j["id"] for j in feed["jobs"]) == [
        "greenhouse:acme:1",
        "greenhouse:acme:2",
    ]


def test_scrape_all_streams_per_ats_jsonl(monkeypatch, tmp_path):
    gh1 = make_job(
        "greenhouse:acme:1", ats="greenhouse", description="multi\nline, with comma"
    )
    gh2 = make_job("greenhouse:acme:2", ats="greenhouse")
    lev = make_job("lever:beta:9", ats="lever")

    def fake_get(ats, slug, name=None, **_):
        return {
            "acme": FakeScraper([gh1, gh2]),
            "beta": FakeScraper([lev]),
            "bad": FakeScraper(error=RuntimeError("boom")),
        }[slug]

    monkeypatch.setattr(harvest, "get_scraper", fake_get)
    companies = [
        CompanyRef("greenhouse", "acme"),
        CompanyRef("lever", "beta"),
        CompanyRef("greenhouse", "bad"),
    ]

    result = scrape_all(companies, jobs_dir=tmp_path)

    # Still deduped and error-isolated; three distinct jobs streamed.
    assert result.unique == 3
    assert "greenhouse:bad" in result.errors

    # Each ATS streamed to its own JSONL, one full Job per line; the failed board wrote nothing.
    gh_lines = (tmp_path / "greenhouse.jsonl").read_text(encoding="utf-8").splitlines()
    lev_lines = (tmp_path / "lever.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(gh_lines) == 2 and len(lev_lines) == 1
    record = json.loads(gh_lines[0])
    assert set(record) == set(Job.__dataclass_fields__)  # full Job, not a reduced row
    assert record["description"] == "multi\nline, with comma"


def test_scrape_all_dedupes_duplicate_boards_in_jsonl(monkeypatch, tmp_path):
    """Duplicate slug forms of one board (e.g. dollartree x3) must not triplicate its Jobs."""
    shared = [
        make_job("workday:dollartree:1", ats="workday"),
        make_job("workday:dollartree:2", ats="workday"),
    ]
    monkeypatch.setattr(harvest, "get_scraper", lambda *a, **k: FakeScraper(shared))
    companies = [
        CompanyRef("workday", "dollartree"),
        CompanyRef("workday", "dollartree/dollartreeus"),
        CompanyRef("workday", "dollartree.wd5.myworkdayjobs.com/dollartreeus"),
    ]

    result = scrape_all(companies, jobs_dir=tmp_path)

    assert result.unique == 2  # two distinct ids, not six
    assert len((tmp_path / "workday.jsonl").read_text("utf-8").splitlines()) == 2


def test_scrape_all_resume_skips_completed_boards(monkeypatch, tmp_path):
    """A resume run skips boards already in .done and appends rather than re-scraping."""
    calls: list[str] = []

    def fake_get(ats, slug, name=None, **_):
        calls.append(slug)
        return FakeScraper([make_job(f"{ats}:{slug}:1", ats=ats)])

    monkeypatch.setattr(harvest, "get_scraper", fake_get)
    companies = [CompanyRef("greenhouse", "a"), CompanyRef("greenhouse", "b")]

    r1 = scrape_all(companies, jobs_dir=tmp_path)
    assert sorted(calls) == ["a", "b"]
    assert r1.boards == 2
    assert (tmp_path / ".done").read_text("utf-8").split() == [
        "greenhouse:a",
        "greenhouse:b",
    ]
    assert len((tmp_path / "greenhouse.jsonl").read_text("utf-8").splitlines()) == 2

    # Resume: both boards are done -> none re-scraped, JSONL untouched (append, no new writes).
    calls.clear()
    r2 = scrape_all(companies, jobs_dir=tmp_path, resume=True)
    assert calls == []
    assert r2.boards == 0
    assert len((tmp_path / "greenhouse.jsonl").read_text("utf-8").splitlines()) == 2

    # Resume with a new board appended -> only the new one scrapes, its line is appended.
    companies.append(CompanyRef("greenhouse", "c"))
    r3 = scrape_all(companies, jobs_dir=tmp_path, resume=True)
    assert calls == ["c"]
    assert r3.boards == 1
    assert len((tmp_path / "greenhouse.jsonl").read_text("utf-8").splitlines()) == 3


def test_records_measured_seconds_for_every_board_including_failures(
    monkeypatch, tmp_path
):
    """ADR-0027: the packer needs a cost for every board it dispatched.

    A board that raises still burned wall time, so its row must land too — timing lives in a
    `finally`, not on the success path. The file is undotted so upload-artifact includes it
    (hidden files are skipped by default, which is why the `.done` journal never reaches the join).
    """

    def fake_get(ats, slug, name=None, **_):
        if slug == "bad":
            return FakeScraper(error=RuntimeError("boom"))
        return FakeScraper([make_job(f"x:{slug}:1")])

    monkeypatch.setattr(harvest, "get_scraper", fake_get)
    scrape_all([CompanyRef("x", "good"), CompanyRef("x", "bad")], jobs_dir=tmp_path)

    rows = read_shard_rows(tmp_path / harvest.COST_FILENAME)
    assert set(rows) == {"x:good", "x:bad"}
    assert all(r.seconds >= 0.0 for r in rows.values())
    assert rows["x:good"].jobs == 1  # jobs written
    assert rows["x:bad"].jobs == 0  # errored board wrote none, but is still costed
    assert not harvest.COST_FILENAME.startswith(".")


def test_scrape_all_carries_a_short_list_from_the_scraper_to_its_callers(
    monkeypatch, tmp_path
):
    """The hop ADR-0053 turns on: a scraper's ``truncated`` must reach both the per-board
    callback and the RunResult, or the signal dies one module short of the shard report.

    The Board still writes its Jobs — which is exactly why it needs saying out loud. A partial
    Board is indistinguishable from a complete one at the ``.jsonl``, so without this
    ``index sync`` reads its unfetched postings as delistings and evicts them.
    """
    short = FakeScraper([make_job("x:short:1")])
    short.truncated = "HTTP 429 on page 2 — got 1 of 50 postings"

    def fake_get(ats, slug, name=None, **_):
        return {"short": short, "whole": FakeScraper([make_job("x:whole:1")])}[slug]

    monkeypatch.setattr(harvest, "get_scraper", fake_get)
    reported: dict[str, str | None] = {}

    result = scrape_all(
        [CompanyRef("x", "short"), CompanyRef("x", "whole")],
        jobs_dir=tmp_path,
        on_board=lambda key, jobs, error, seconds, truncated: reported.update(
            {key: truncated}
        ),
    )

    assert result.truncated == {"x:short": short.truncated}
    assert reported == {"x:short": short.truncated, "x:whole": None}
    assert result.errors == {}  # a short list is not a failure — it produced Jobs
    assert result.unique == 2  # and both Boards' Jobs were written


def test_a_kill_mid_harvest_abandons_the_queue_instead_of_draining_it(
    tmp_path, monkeypatch
):
    """The behaviour the scrape time budget depends on.

    Every Board is submitted up front, so an exception raised inside the loop used to leave
    ``ThreadPoolExecutor.__exit__`` waiting for the *whole* queue (shutdown(wait=True)). A shard
    killed at 60 min would then keep scraping until the 66-min step timeout killed the runner —
    reporting nothing, which is the failure this whole change exists to remove.
    """
    import time as _time

    started: list[str] = []

    class _Slow:
        ats = "lever"
        truncated = None  # BaseScraper's; scrape_all reads it on every board (ADR-0053)

        def __init__(self, slug):
            self.slug = slug

        def fetch(self):
            started.append(self.slug)
            _time.sleep(0.2)
            return []

    companies = [CompanyRef(ats="lever", slug=f"c{i}") for i in range(40)]
    monkeypatch.setattr(
        "headstart.harvest.get_scraper", lambda ats, slug, name=None, **_: _Slow(slug)
    )

    def stop_after_two(key, jobs, error, seconds, truncated=None):
        if len(started) >= 2:
            raise SystemExit("signal 15")

    with pytest.raises(SystemExit):
        harvest.scrape_all(
            companies, jobs_dir=tmp_path, max_workers=2, on_board=stop_after_two
        )

    # The count, not the clock: draining runs all 40 Boards, abandoning runs only the couple
    # already in flight. Asserting on elapsed time would be the same claim, measured flakily.
    assert len(started) < 10, (
        f"the queue drained instead of being abandoned ({len(started)})"
    )


def test_shutdown_does_not_wait_for_a_board_still_in_flight(monkeypatch, tmp_path):
    """The straggler that killed three shards on 2026-08-13.

    When the time budget fires, `scrape_all`'s `finally` used to call `shutdown(wait=True)`,
    which blocks on any Board already running — you cannot cancel a Python thread. A
    SuccessFactors board trickling its RSS feed under a 300s read timeout outlasted the 6 min of
    slack between the 60m budget and the 66m step timeout, so the runner was killed before
    anything was reported, and a shard that reports nothing takes the whole run's embed stage
    with it. The in-flight result is discarded either way — the loop that would have written it
    has already exited — so waiting bought teardown, not work.
    """
    import threading

    blocked = threading.Event()
    released = threading.Event()

    class _Blocking:
        truncated: str | None = None

        def fetch(self):
            blocked.set()
            released.wait(
                30
            )  # never set until teardown; stands in for a hung socket read
            return []

    def fake_get(ats, slug, name=None, **_):
        return FakeScraper([make_job("x:quick:1")]) if slug == "quick" else _Blocking()

    monkeypatch.setattr(harvest, "get_scraper", fake_get)

    def on_board(key, jobs, error, seconds, truncated=None):
        if key.endswith(":quick"):
            blocked.wait(10)  # make sure the other Board really is mid-fetch
            raise SystemExit("time budget")

    companies = [
        CompanyRef(ats="x", slug="quick"),
        CompanyRef(ats="x", slug="hangs"),
    ]
    try:
        started = time.monotonic()
        with pytest.raises(SystemExit):
            harvest.scrape_all(companies, jobs_dir=tmp_path, on_board=on_board)
        elapsed = time.monotonic() - started
    finally:
        released.set()

    # With wait=True this sits on the blocked Board for its full 30s. The bug is "waits at all".
    assert elapsed < 5, f"shutdown blocked {elapsed:.1f}s on an in-flight Board"


def test_a_board_still_running_at_the_kill_is_costed_for_what_it_burned(
    monkeypatch, tmp_path
):
    """The survivorship hole that let one Board kill a shard every run, forever.

    A Board that never finishes writes no cost row, so the packer keeps whatever stale estimate
    it held. Measured 2026-08-18 on run 32133497258: `workday:dollartree/dollartreeus` (24,017
    postings, ~67 min to page at Workday's 20-per-page cap) ran the last 52 minutes of shard 13
    and was killed unfinished — while the ledger priced it at 411.9 s. It was re-drawn as a cheap
    Board and killed a shard again the next run, and would have indefinitely: the one Board whose
    cost the model most needed to learn was the one Board it could never measure.

    So the timing a kill *does* prove — that the Board ran at least this long without finishing —
    has to reach the ledger. It is a floor, not a measurement, which is why it is only ever
    written for a Board still in flight when the harvest goes down.
    """
    import threading

    blocked = threading.Event()
    released = threading.Event()

    class _Blocking:
        truncated: str | None = None

        def fetch(self):
            blocked.set()
            released.wait(30)  # stands in for the giant board still paging at the kill
            return []

    def fake_get(ats, slug, name=None, **_):
        return FakeScraper([make_job("x:quick:1")]) if slug == "quick" else _Blocking()

    monkeypatch.setattr(harvest, "get_scraper", fake_get)

    def on_board(key, jobs, error, seconds, truncated=None):
        if key.endswith(":quick"):
            blocked.wait(10)  # the monster really is mid-fetch when the budget fires
            time.sleep(0.05)  # ... and has burned time worth recording when it does
            raise SystemExit("time budget")

    companies = [CompanyRef(ats="x", slug="quick"), CompanyRef(ats="x", slug="monster")]
    try:
        with pytest.raises(SystemExit):
            scrape_all(companies, jobs_dir=tmp_path, on_board=on_board)
    finally:
        released.set()

    rows = read_shard_rows(tmp_path / harvest.COST_FILENAME)
    assert set(rows) == {"x:quick", "x:monster"}, (
        "the unfinished Board must be costed, not silently dropped"
    )
    monster = rows["x:monster"]
    assert monster.seconds >= 0.04, (
        f"the floor must be the seconds it actually burned, got {monster.seconds}"
    )
    assert monster.unfinished, "a bound must not reach the ledger as a measurement"
    assert not rows["x:quick"].unfinished


def test_a_clean_finish_costs_every_board_exactly_once(monkeypatch, tmp_path):
    """The floor must not double-write. Every Board finished, so nothing is in flight, and the
    rows are the ordinary measured ones — a second row per Board would blend a Board's own
    timing into itself and drag the whole ledger."""

    def fake_get(ats, slug, name=None, **_):
        return FakeScraper([make_job(f"x:{slug}:1")])

    monkeypatch.setattr(harvest, "get_scraper", fake_get)
    scrape_all([CompanyRef("x", "a"), CompanyRef("x", "b")], jobs_dir=tmp_path)

    lines = (tmp_path / harvest.COST_FILENAME).read_text().strip().split("\n")
    assert len(lines) == 3, f"header + one row per Board, got {lines}"


def test_a_board_whose_scraper_never_constructed_gets_no_floor(monkeypatch, tmp_path):
    """The floor must cost a fetch, not a failure to start one.

    `get_scraper` raises on an unknown ATS or a malformed slug. Registering the Board as
    in-flight before that call leaked the key — nothing ever popped it — so the teardown costed
    an instantly-failed Board the shard's entire remaining hour, and the ADR-0064 value gate then
    dropped it permanently on a number it never earned.
    """

    def fake_get(ats, slug, name=None, **_):
        if slug == "unbuildable":
            raise ValueError(f"unknown ats: {ats}")
        return FakeScraper([make_job("x:ok:1")])

    monkeypatch.setattr(harvest, "get_scraper", fake_get)
    scrape_all(
        [CompanyRef("x", "unbuildable"), CompanyRef("x", "ok")], jobs_dir=tmp_path
    )

    rows = read_shard_rows(tmp_path / harvest.COST_FILENAME)
    assert not any(r.unfinished for r in rows.values()), (
        f"a Board that never started a fetch was costed as if it had: {rows}"
    )
    assert [k for k, r in rows.items() if r.unfinished] == []
