"""Tests for deciding when each Job version counted (headstart.ingest.restate_served, ADR-0330).

Today's rules apply to the whole past alike: a Board outside today's keep-set and a version today's
tech filter rejects never count, and a Job the scrape stopped returning counts until its Board's
next authoritative read, as `index sync` serves it (ADR-0083).
"""

from __future__ import annotations

from pathlib import Path

from headstart.boards.board_identity import lower_key
from headstart.ingest import job_facts as jf
from headstart.ingest import restate_replay as rr
from headstart.ingest import restate_served as rs
from headstart.ingest.observability import ShardReport

BOARD_A = "greenhouse:acme"
BOARD_B = "lever:globex"
A1, A2, B1 = f"{BOARD_A}:1", f"{BOARD_A}:2", f"{BOARD_B}:1"
RUNS = [f"2026-09-29T0{h}:00:00+00:00" for h in range(1, 6)]


def _job(job_id: str, title: str = "Backend Engineer") -> dict:
    return {"id": job_id, "title": title, "department": "Engineering"}


def _record(
    facts: Path, stamp: str, jobs: list[tuple[str, dict]], read: set[str]
) -> None:
    lines = jf.ScrapedLines(facts / jf.SCRAPED_LINES)
    for board, job in jobs:
        lines.see(board, job)
    scope = jf.RunScope(
        authoritative=frozenset(lower_key(b) for b in read), keep_set=None, live={}
    )
    reads = jf.board_reads(
        [ShardReport(boards_ok=sorted(read))], lines.board_lines, scope
    )
    jf.record_run(lines.close(), facts, stamp, reads, scope)
    (facts / jf.SCRAPED_LINES).unlink(missing_ok=True)


def _served(tmp_path: Path, steps, keep_set=None) -> dict[str, list[tuple]]:
    facts = tmp_path / "facts"
    for stamp, (jobs, read) in zip(RUNS, steps, strict=False):
        _record(facts, stamp, jobs, read)
    served = rs.served_intervals(
        rr.job_versions(facts),
        rr.board_reads(facts),
        is_tech=lambda title, department: "Cashier" not in (title or ""),
        live={},
        keep_set=keep_set,
    )
    out: dict[str, list[tuple]] = {}
    for row in served.to_pylist():
        out.setdefault(row["id"], []).append((row["served_from"], row["served_to"]))
    return out


def test_a_job_missed_once_counts_until_its_boards_next_authoritative_read(tmp_path):
    served = _served(
        tmp_path,
        [
            ([(BOARD_A, _job(A1))], {BOARD_A}),
            ([], {BOARD_A}),  # first absence
            ([], set()),  # Board not read: no evidence
            ([], {BOARD_A}),  # second absence: it goes here
        ],
    )

    assert served[A1] == [(RUNS[0], RUNS[3])]


def test_a_job_listed_again_is_served_without_a_gap(tmp_path):
    served = _served(
        tmp_path,
        [
            ([(BOARD_A, _job(A1))], {BOARD_A}),
            ([], {BOARD_A}),
            ([(BOARD_A, _job(A1))], {BOARD_A}),
        ],
    )

    assert served[A1] == [(RUNS[0], RUNS[2]), (RUNS[2], None)]


def test_a_job_missed_once_and_never_read_again_still_counts(tmp_path):
    served = _served(tmp_path, [([(BOARD_A, _job(A1))], {BOARD_A}), ([], {BOARD_A})])

    assert served[A1] == [(RUNS[0], None)]


def test_a_changed_job_counts_on_across_the_change(tmp_path):
    served = _served(
        tmp_path,
        [
            ([(BOARD_A, _job(A1))], {BOARD_A}),
            ([(BOARD_A, _job(A1, "Staff Engineer"))], {BOARD_A}),
        ],
    )

    assert served[A1] == [(RUNS[0], RUNS[1]), (RUNS[1], None)]


def test_todays_tech_filter_applies_to_the_whole_past(tmp_path):
    served = _served(
        tmp_path,
        [
            ([(BOARD_A, _job(A1)), (BOARD_A, _job(A2, "Cashier"))], {BOARD_A}),
            (
                [(BOARD_A, _job(A1, "Cashier")), (BOARD_A, _job(A2, "Cashier"))],
                {BOARD_A},
            ),
        ],
    )

    assert served == {A1: [(RUNS[0], RUNS[1])]}


def test_a_board_outside_todays_keep_set_is_gone_from_the_past(tmp_path):
    steps = [([(BOARD_A, _job(A1)), (BOARD_B, _job(B1))], {BOARD_A, BOARD_B})]

    assert set(_served(tmp_path, steps, keep_set={lower_key(BOARD_A)})) == {A1}


def test_without_a_keep_set_every_board_counts(tmp_path):
    steps = [([(BOARD_A, _job(A1)), (BOARD_B, _job(B1))], {BOARD_A, BOARD_B})]

    assert set(_served(tmp_path, steps)) == {A1, B1}
