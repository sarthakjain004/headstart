"""Tests for counting served intervals into tick levels and turnover (headstart.ingest.restate_count,
ADR-0330).

The identity ADR-0227 holds for the live ledger holds here too, for every key and tick:
Δstock = opened − closed + recounted_in − recounted_out.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from headstart.boards.board_identity import lower_key
from headstart.ingest import job_facts as jf
from headstart.ingest import restate_count as rc
from headstart.ingest import restate_replay as rr
from headstart.ingest import restate_served as rs
from headstart.ingest.observability import ShardReport

BOARD_A = "greenhouse:acme"
BOARD_B = "lever:globex"
A1, A2, A3, B1 = (
    f"{BOARD_A}:1",
    f"{BOARD_A}:2",
    f"{BOARD_A}:3",
    f"{BOARD_B}:1",
)
RUNS = [f"2026-09-{d:02d}T00:00:00+00:00" for d in (1, 3, 6, 9, 12)]
SE = ("software-engineering", "mid")
DE = ("data-engineering", "mid")


def _job(job_id: str, title: str = "Backend Engineer") -> dict:
    return {"id": job_id, "title": title, "department": "Engineering"}


def _place(row) -> tuple[str, str] | None:
    title = row["title"] or ""
    if "Warehouse" in title:
        return None  # the classifier's non-tech
    return DE if "Data" in title else SE


#: A shard names a Workday Board by its careers URL; `board_key_of` turns it into the Board key.
_SCRAPER_KEYS = {
    "workday:acme/External": "workday:https://acme.wd1.myworkdayjobs.com/External",
    "workday:acme/Campus": "workday:https://acme.wd1.myworkdayjobs.com/Campus",
}


def _record(facts: Path, stamp: str, jobs, read: set[str]) -> None:
    lines = jf.ScrapedLines(facts / jf.SCRAPED_LINES)
    for board, job in jobs:
        lines.see(board, job)
    scope = jf.RunScope(
        authoritative=frozenset(lower_key(b) for b in read), keep_set=None, live={}
    )
    reads = jf.board_reads(
        [ShardReport(boards_ok=sorted(_SCRAPER_KEYS.get(b, b) for b in read))],
        lines.board_lines,
        scope,
    )
    jf.record_run(lines.close(), facts, stamp, reads, scope)
    (facts / jf.SCRAPED_LINES).unlink(missing_ok=True)


def _ticks(tmp_path: Path, steps):
    facts = tmp_path / "facts"
    for stamp, (jobs, read) in zip(RUNS, steps, strict=False):
        _record(facts, stamp, jobs, read)
    reads = rr.board_reads(facts)
    served = rs.served_intervals(
        rr.job_versions(facts), reads, is_tech=lambda t, d: True, live={}, keep_set=None
    )
    return list(rc.tick_counts(served, rr.runs(facts), rr.first_reads(reads), _place))


STEPS = [
    # 1: both Boards found: their backlogs are Recounted, not Opened
    (
        [(BOARD_A, _job(A1)), (BOARD_A, _job(A2)), (BOARD_B, _job(B1))],
        {BOARD_A, BOARD_B},
    ),
    # 2: A3 opens on a known Board; A1 retitled into data engineering; A2 missed once
    ([(BOARD_A, _job(A1, "Data Engineer")), (BOARD_A, _job(A3))], {BOARD_A}),
    # 3: A2 missed again, so it closes; A3 retitled into the classifier's non-tech
    (
        [(BOARD_A, _job(A1, "Data Engineer")), (BOARD_A, _job(A3, "Warehouse Lead"))],
        {BOARD_A},
    ),
    # 4, 5: nothing read
    ([], set()),
    ([], set()),
]


def _metric(ticks, run_index: int, metric: str) -> Counter:
    _, _, turnover = ticks[run_index]
    return Counter({k: n for k, n in turnover.items() if k[1] == metric})


def test_a_found_boards_backlog_is_recounted_not_opened(tmp_path):
    ticks = _ticks(tmp_path, STEPS)

    assert _metric(ticks, 0, rc.OPENED) == Counter()
    assert sum(_metric(ticks, 0, rc.RECOUNTED_IN).values()) == 3


def test_a_job_new_on_a_known_board_is_opened(tmp_path):
    ticks = _ticks(tmp_path, STEPS)

    assert _metric(ticks, 1, rc.OPENED) == Counter({(BOARD_A, rc.OPENED, *SE): 1})


def test_a_job_missed_twice_closes_at_its_second_absence(tmp_path):
    ticks = _ticks(tmp_path, STEPS)

    assert _metric(ticks, 1, rc.CLOSED) == Counter()
    assert _metric(ticks, 2, rc.CLOSED) == Counter({(BOARD_A, rc.CLOSED, *SE): 1})


def test_a_job_that_moved_family_is_recounted_out_and_in(tmp_path):
    ticks = _ticks(tmp_path, STEPS)

    assert _metric(ticks, 1, rc.RECOUNTED_OUT)[(BOARD_A, rc.RECOUNTED_OUT, *SE)] == 1
    assert _metric(ticks, 1, rc.RECOUNTED_IN)[(BOARD_A, rc.RECOUNTED_IN, *DE)] == 1


def test_a_job_the_classifier_moves_out_of_tech_counts_as_non_tech_stock(tmp_path):
    ticks = _ticks(tmp_path, STEPS)
    _, levels, _ = ticks[2]

    assert levels[(BOARD_A, "stock", rc.NON_TECH, rc.NOT_SPLIT)] == 1
    assert _metric(ticks, 2, rc.RECOUNTED_OUT)[(BOARD_A, rc.RECOUNTED_OUT, *SE)] == 1


def test_stock_moves_exactly_by_its_turnover_at_every_tick(tmp_path):
    """ADR-0227's identity, per (board, family, band) and tick, on tech keys."""
    ticks = _ticks(tmp_path, STEPS)
    before: dict = {}
    for run, levels, turnover in ticks:
        assert rc.unbalanced(before, levels, turnover) == [], run
        before = levels


def test_a_stock_move_without_its_turnover_is_unbalanced():
    key = (BOARD_A, "stock", *SE)

    assert rc.unbalanced({}, {key: 1}, Counter()) == [(BOARD_A, *SE)]
    assert rc.unbalanced({}, {key: 1}, Counter({(BOARD_A, rc.OPENED, *SE): 1})) == []


def test_a_job_counts_as_new_for_seven_days_after_it_was_first_listed(tmp_path):
    ticks = _ticks(tmp_path, [([(BOARD_A, _job(A1))], {BOARD_A})] + [([], set())] * 4)
    new = [levels.get((BOARD_A, "new", *SE), 0) for _, levels, _ in ticks]

    # listed on the 1st: new on the 1st, 3rd and 6th, not on the 9th or 12th
    assert new == [1, 1, 1, 0, 0]


MAIN, SUB = "workday:acme/External", "workday:acme/Campus"
HIDDEN = "workday:acme/Internal_Careers"
SITE_JOBS = {"workday:acme/external": 900, "workday:acme/campus": 40}


def _folded_ticks(tmp_path: Path, steps):
    """The Workday tenant's two sites, one requisition: `index prune` keeps the External copy."""
    from headstart.ingest.index_plan import boards_by_canon, duplicate_ranks

    facts = tmp_path / "facts"
    keep = {MAIN, SUB, HIDDEN}
    for stamp, (jobs, read) in zip(RUNS, steps, strict=False):
        _record(facts, stamp, jobs, read)
    reads = rr.board_reads(facts)
    live = boards_by_canon(keep)
    served = rs.served_intervals(
        rr.job_versions(facts),
        reads,
        is_tech=lambda t, d: True,
        live=live,
        keep_set=None,
    )
    ranks = duplicate_ranks(served["id"].to_pylist(), keep, site_jobs=SITE_JOBS)
    folded = rs.fold_duplicates(served, ranks)
    return list(rc.tick_counts(folded, rr.runs(facts), rr.first_reads(reads), _place))


def _stock(ticks, i: int) -> dict:
    _, levels, _ = ticks[i]
    return {k: n for k, n in levels.items() if k[1] == "stock"}


def test_two_copies_of_one_requisition_count_once_on_the_site_prune_keeps(tmp_path):
    ticks = _folded_ticks(
        tmp_path,
        [([(SUB, _job(f"{SUB}:R-100")), (MAIN, _job(f"{MAIN}:R-100"))], {MAIN, SUB})],
    )

    assert _stock(ticks, 0) == {(MAIN, "stock", *SE): 1}


def test_a_copy_arriving_on_another_site_leaves_the_incumbent_in_place(tmp_path):
    """`index sync` refuses a copy while an incumbent of the same class stands."""
    ticks = _folded_ticks(
        tmp_path,
        [
            ([(SUB, _job(f"{SUB}:R-100"))], {MAIN, SUB}),
            ([(SUB, _job(f"{SUB}:R-100")), (MAIN, _job(f"{MAIN}:R-100"))], {MAIN, SUB}),
        ],
    )

    assert _stock(ticks, 1) == {(SUB, "stock", *SE): 1}
    assert sum(_metric(ticks, 1, rc.RECOUNTED_IN).values()) == 0
    assert sum(_metric(ticks, 1, rc.OPENED).values()) == 0


def test_a_public_copy_takes_over_from_a_non_public_incumbent_without_a_closure(
    tmp_path,
):
    ticks = _folded_ticks(
        tmp_path,
        [
            ([(HIDDEN, _job(f"{HIDDEN}:R-100"))], {MAIN, HIDDEN}),
            (
                [(HIDDEN, _job(f"{HIDDEN}:R-100")), (MAIN, _job(f"{MAIN}:R-100"))],
                {MAIN, HIDDEN},
            ),
        ],
    )

    assert _stock(ticks, 1) == {(MAIN, "stock", *SE): 1}
    assert _metric(ticks, 1, rc.CLOSED) == Counter()
    assert _metric(ticks, 1, rc.OPENED) == Counter()
    assert _metric(ticks, 1, rc.RECOUNTED_OUT) == Counter(
        {(HIDDEN, rc.RECOUNTED_OUT, *SE): 1}
    )
    assert _metric(ticks, 1, rc.RECOUNTED_IN) == Counter(
        {(MAIN, rc.RECOUNTED_IN, *SE): 1}
    )


def test_a_copy_left_when_the_kept_one_goes_still_counts_the_posting(tmp_path):
    ticks = _folded_ticks(
        tmp_path,
        [
            ([(SUB, _job(f"{SUB}:R-100")), (MAIN, _job(f"{MAIN}:R-100"))], {MAIN, SUB}),
            ([(SUB, _job(f"{SUB}:R-100"))], {MAIN, SUB}),
            ([(SUB, _job(f"{SUB}:R-100"))], {MAIN, SUB}),
        ],
    )

    assert _stock(ticks, 2) == {(SUB, "stock", *SE): 1}
    assert sum(_metric(ticks, 2, rc.CLOSED).values()) == 0
