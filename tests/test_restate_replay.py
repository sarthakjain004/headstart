"""Tests for replaying Job facts into Job versions (headstart.ingest.restate_replay, ADR-0330).

The invariant: the versions open at a run are exactly the Listed set that run's facts left, with
the raw fields its latest fact recorded. Everything a Restatement counts rests on it.
"""

from __future__ import annotations

from pathlib import Path

import pyarrow.parquet as pq

from headstart.boards.board_identity import lower_key
from headstart.ingest import job_facts as jf
from headstart.ingest import restate_replay as rr
from headstart.ingest.observability import ShardReport

BOARD_A = "greenhouse:acme"
BOARD_B = "lever:globex"
A1, A2, B1 = f"{BOARD_A}:1", f"{BOARD_A}:2", f"{BOARD_B}:1"
RUNS = [f"2026-09-29T0{h}:00:00+00:00" for h in range(1, 6)]


def _job(job_id: str, title: str = "Backend Engineer") -> dict:
    return {"id": job_id, "title": title, "department": "Engineering"}


def _record(
    facts: Path,
    stamp: str,
    jobs: list[tuple[str, dict]],
    read: set[str],
    keep_set: set[str] | None = None,
) -> None:
    lines = jf.ScrapedLines(facts / jf.SCRAPED_LINES)
    for board, job in jobs:
        lines.see(board, job)
    scope = jf.RunScope(
        authoritative=frozenset(lower_key(b) for b in read),
        keep_set=None if keep_set is None else frozenset(keep_set),
        live={},
    )
    reads = jf.board_reads(
        [ShardReport(boards_ok=sorted(read))], lines.board_lines, scope
    )
    jf.record_run(lines.close(), facts, stamp, reads, scope)
    (facts / jf.SCRAPED_LINES).unlink(missing_ok=True)


def _listed(facts: Path) -> dict[str, str]:
    table = pq.read_table(facts / jf.LISTED_JOBS)
    return dict(zip(table["id"].to_pylist(), table["board"].to_pylist(), strict=True))


def _history(tmp_path: Path) -> tuple[Path, dict[str, dict[str, str]]]:
    """Five runs covering every kind of fact, and the Listed set after each."""
    facts = tmp_path / "facts"
    after: dict[str, dict[str, str]] = {}
    steps = [
        # run 1: three Jobs listed
        (
            [(BOARD_A, _job(A1)), (BOARD_A, _job(A2)), (BOARD_B, _job(B1))],
            {BOARD_A, BOARD_B},
        ),
        # run 2: a:1 retitled, a:2 gone from an authoritative read, b's Board not read
        ([(BOARD_A, _job(A1, "Staff Engineer"))], {BOARD_A}),
        # run 3: a:2 listed again, b:1 still unread
        ([(BOARD_A, _job(A1, "Staff Engineer")), (BOARD_A, _job(A2))], {BOARD_A}),
        # run 4: nothing read
        ([], set()),
        # run 5: b's Board read clean and empty
        ([], {BOARD_B}),
    ]
    for stamp, (jobs, read) in zip(RUNS, steps, strict=True):
        _record(facts, stamp, jobs, read)
        after[stamp] = _listed(facts)
    return facts, after


def test_the_versions_open_at_every_run_are_the_listed_set_it_left(tmp_path):
    facts, after = _history(tmp_path)
    versions = rr.job_versions(facts)

    for stamp in RUNS:
        open_now = rr.open_at(versions, stamp)
        assert (
            dict(
                zip(
                    open_now["id"].to_pylist(),
                    open_now["board"].to_pylist(),
                    strict=True,
                )
            )
            == after[stamp]
        ), stamp


def test_a_changed_job_is_two_versions_and_the_first_ends_as_changed(tmp_path):
    facts, _ = _history(tmp_path)
    versions = rr.job_versions(facts).to_pylist()

    a1 = [v for v in versions if v["id"] == A1]
    assert [
        (v["title"], v["valid_from"], v["valid_to"], v["ended_as"]) for v in a1
    ] == [
        ("Backend Engineer", RUNS[0], RUNS[1], "changed"),
        ("Staff Engineer", RUNS[1], None, None),
    ]


def test_a_job_that_went_and_came_back_is_two_versions(tmp_path):
    facts, _ = _history(tmp_path)
    versions = rr.job_versions(facts).to_pylist()

    a2 = [
        (v["valid_from"], v["valid_to"], v["ended_as"])
        for v in versions
        if v["id"] == A2
    ]
    assert a2 == [(RUNS[0], RUNS[1], "unlisted"), (RUNS[2], None, None)]


def test_a_board_read_empty_ends_its_jobs_versions(tmp_path):
    facts, _ = _history(tmp_path)
    versions = rr.job_versions(facts).to_pylist()

    (b1,) = [v for v in versions if v["id"] == B1]
    assert (b1["valid_from"], b1["valid_to"], b1["ended_as"]) == (
        RUNS[0],
        RUNS[4],
        "unlisted",
    )


def test_every_run_is_a_tick_even_one_that_changed_nothing(tmp_path):
    facts, _ = _history(tmp_path)

    assert rr.runs(facts) == RUNS


def test_board_reads_carry_their_run(tmp_path):
    facts, _ = _history(tmp_path)
    reads = rr.board_reads(facts).to_pylist()

    assert [(r["run"], r["scraper_key"], r["outcome"]) for r in reads] == [
        (RUNS[0], BOARD_A, "authoritative"),
        (RUNS[0], BOARD_B, "authoritative"),
        (RUNS[1], BOARD_A, "authoritative"),
        (RUNS[2], BOARD_A, "authoritative"),
        (RUNS[4], BOARD_B, "authoritative"),
    ]


def test_no_facts_is_no_versions(tmp_path):
    assert rr.job_versions(tmp_path) is None
    assert rr.board_reads(tmp_path) is None
    assert rr.runs(tmp_path) == []
