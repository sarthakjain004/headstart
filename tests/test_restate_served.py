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


DAYS = [f"2026-09-{d:02d}T00:00:00+00:00" for d in (1, 3, 6, 9, 12)]


def _dated(job_id: str, posted_at: str | None, title: str = "Backend Engineer") -> dict:
    return {
        "id": job_id,
        "title": title,
        "department": "Engineering",
        "posted_at": posted_at,
    }


def _clipped(tmp_path: Path, steps) -> dict[str, list[tuple]]:
    facts = tmp_path / "facts"
    for stamp, (jobs, read) in zip(DAYS, steps, strict=False):
        _record(facts, stamp, jobs, read)
    versions = rr.job_versions(facts)
    served = rs.served_intervals(
        versions,
        rr.board_reads(facts),
        is_tech=lambda title, department: "Cashier" not in (title or ""),
        live={},
        keep_set=None,
    )
    clipped = rs.clip_dormant(
        served, rs.dormant_periods(versions, rr.board_reads(facts), {})
    )
    out: dict[str, list[tuple]] = {}
    for row in clipped.to_pylist():
        out.setdefault(row["id"], []).append(
            (row["served_from"], row["served_to"], row["ended_as"], row["starts_as"])
        )
    return out


def test_a_board_whose_newest_posting_is_years_old_never_counts(tmp_path):
    served = _clipped(tmp_path, [([(BOARD_A, _dated(A1, "2020-01-01"))], {BOARD_A})])

    assert served == {}


def test_a_board_turning_dormant_loses_its_jobs_at_its_next_read(tmp_path):
    """Posted 2024-09-04: still hiring on Sep 1 and 3, judged Dormant by the Sep 6 read, and its
    Job leaves at the Sep 9 read, as `index sync` evicts after the grace period."""
    served = _clipped(
        tmp_path, [([(BOARD_A, _dated(A1, "2024-09-04"))], {BOARD_A})] * 5
    )

    assert served == {A1: [(DAYS[0], DAYS[3], "dormant", None)]}


def test_only_an_authoritative_read_judges_a_board_dormant(tmp_path):
    """A read that was not authoritative (truncated, say) may miss its newer postings."""
    served = _clipped(
        tmp_path,
        [([(BOARD_A, _dated(A1, "2024-09-04"))], {BOARD_A})]
        + [([(BOARD_A, _dated(A1, "2024-09-04"))], set())] * 4,
    )

    assert served == {A1: [(DAYS[0], None, None, None)]}


def test_an_undated_job_keeps_its_board_from_being_dormant(tmp_path):
    served = _clipped(
        tmp_path,
        [
            (
                [(BOARD_A, _dated(A1, "2020-01-01")), (BOARD_A, _dated(A2, None))],
                {BOARD_A},
            )
        ],
    )

    assert set(served) == {A1, A2}


def test_a_non_tech_posting_is_evidence_the_board_still_posts(tmp_path):
    """Dormancy is judged over every listed Job, as scrape_join judges it."""
    served = _clipped(
        tmp_path,
        [
            (
                [
                    (BOARD_A, _dated(A1, "2020-01-01")),
                    (BOARD_A, _dated(A2, "2026-08-30", "Cashier")),
                ],
                {BOARD_A},
            )
        ],
    )

    assert served == {A1: [(DAYS[0], None, None, None)]}


def test_a_dormant_board_that_posts_again_revives_its_old_jobs(tmp_path):
    served = _clipped(
        tmp_path,
        [
            ([(BOARD_A, _dated(A1, "2020-01-01"))], {BOARD_A}),
            ([(BOARD_A, _dated(A1, "2020-01-01"))], {BOARD_A}),
            (
                [
                    (BOARD_A, _dated(A1, "2020-01-01")),
                    (BOARD_A, _dated(A2, "2026-09-05")),
                ],
                {BOARD_A},
            ),
        ],
    )

    assert served[A1] == [(DAYS[2], None, None, "revived")]
    assert served[A2] == [(DAYS[2], None, None, None)]


def _english(tmp_path: Path, steps, descriptions) -> dict[str, list[tuple]]:
    facts = tmp_path / "facts"
    for stamp, (jobs, read) in zip(RUNS, steps, strict=False):
        _record(facts, stamp, jobs, read)
    served = rs.english_only(
        rs.served_intervals(
            rr.job_versions(facts),
            rr.board_reads(facts),
            is_tech=lambda title, department: True,
            live={},
            keep_set=None,
        ),
        descriptions,
        is_english=lambda title, description: "Nous" not in f"{title} {description}",
    )
    out: dict[str, list[tuple]] = {}
    for row in served.to_pylist():
        out.setdefault(row["id"], []).append((row["served_from"], row["served_to"]))
    return out


def test_a_job_the_english_gate_holds_out_never_counts(tmp_path):
    served = _english(
        tmp_path,
        [([(BOARD_A, _job(A1)), (BOARD_A, _job(A2))], {BOARD_A})],
        {A1: "You will build APIs.", A2: "Nous recrutons un ingénieur."},
    )

    assert served == {A1: [(RUNS[0], None)]}


def test_the_english_gate_judges_each_version_on_its_own_title(tmp_path):
    served = _english(
        tmp_path,
        [
            ([(BOARD_A, _job(A1))], {BOARD_A}),
            ([(BOARD_A, _job(A1, "Nous recrutons"))], {BOARD_A}),
        ],
        {A1: "You will build APIs."},
    )

    assert served == {A1: [(RUNS[0], RUNS[1])]}


def test_selected_wide_replay_matches_full_with_non_tech_dormancy_and_backing_dedup(
    tmp_path,
):
    import pyarrow as pa

    from headstart.ingest import restate_count
    from headstart.ingest.index_plan import duplicate_ranks

    front, backing = "eightfold:jobs.acme.com", "workday:acme/external"
    front_id, backing_id = front + ":123", backing + ":R123"
    facts = tmp_path / "facts"
    jobs = [
        (front, _dated(front_id, "2020-01-01") | {"requisition": "R123"}),
        (backing, _dated(backing_id, "2020-01-01") | {"requisition": "R123"}),
        (front, _dated(front + ":cashier", "2026-09-01", "Cashier")),
        (backing, _dated(backing + ":cashier", None, "Cashier")),
    ]
    for stamp in DAYS[:2]:
        _record(facts, stamp, jobs, {front, backing})
    reads = rr.board_reads(facts)
    full = rr.job_versions(facts)
    is_tech = lambda title, department: title != "Cashier"
    selected = rr.job_versions(facts, wanted=rr.eligible_ids(facts, is_tech))
    narrow = rr.job_versions(facts, columns=["id", "kind", "posted_at"])
    periods = rs.dormant_periods(narrow, reads, {})
    assert periods == rs.dormant_periods(full, reads, {}) == {}
    results = []
    for versions in (full, selected):
        served = rs.clip_dormant(
            rs.served_intervals(
                versions, reads, is_tech=is_tech, live={}, keep_set=None
            ),
            periods,
        )
        ranks = duplicate_ranks(
            served["id"].to_pylist(),
            {front, backing},
            requisitions={front_id: "R123", backing_id: "R123"},
            backing={"jobs.acme.com": (backing,)},
        )
        folded = rs.fold_duplicates(served, ranks)
        assert folded["id"].to_pylist() == [backing_id]
        folded = folded.append_column(
            "family", pa.array(["software-engineering"])
        ).append_column("band", pa.array(["mid"]))
        results.append(
            list(
                restate_count.tick_counts(
                    folded,
                    DAYS[:2],
                    rr.first_reads(reads),
                    lambda row: (row["family"], row["band"]),
                )
            )
        )
    assert results[0] == results[1]


def test_initially_non_tech_id_becomes_tech_then_removed_without_backdating(tmp_path):
    from headstart.ingest import restate_count

    facts = tmp_path / "facts"
    steps = [
        [(BOARD_A, _job(A1, "Cashier"))],
        [(BOARD_A, _job(A1))],
        [],
        [],
    ]
    for stamp, jobs in zip(RUNS, steps):
        _record(facts, stamp, jobs, {BOARD_A})
    is_tech = lambda title, department: title == "Backend Engineer"
    full = rr.job_versions(facts)
    selected = rr.job_versions(facts, wanted=rr.eligible_ids(facts, is_tech))
    assert selected.equals(full)
    rows = selected.to_pylist()
    assert [(r["valid_from"], r["valid_to"], r["ended_as"]) for r in rows] == [
        (RUNS[0], RUNS[1], "changed"),
        (RUNS[1], RUNS[2], "unlisted"),
    ]
    reads = rr.board_reads(facts)
    ticks = []
    for versions in (full, selected):
        served = rs.served_intervals(
            versions, reads, is_tech=is_tech, live={}, keep_set=None
        )
        assert [(r["served_from"], r["served_to"]) for r in served.to_pylist()] == [
            (RUNS[1], RUNS[3])
        ]
        ticks.append(
            list(
                restate_count.tick_counts(
                    served,
                    RUNS[:4],
                    rr.first_reads(reads),
                    lambda row: ("software-engineering", "mid"),
                )
            )
        )
    assert ticks[0] == ticks[1]
    assert ticks[1][0][1] == {}
    assert ticks[1][3][1] == {}


def test_dormancy_clipping_does_not_convert_whole_table():
    import pyarrow as pa

    served = pa.Table.from_pylist(
        [
            {
                "id": A1,
                "board": BOARD_A,
                "served_from": DAYS[0],
                "served_to": None,
                "ended_as": None,
            }
        ],
        schema=pa.schema(
            [
                (name, pa.string())
                for name in ("id", "board", "served_from", "served_to", "ended_as")
            ]
        ),
    )

    class BatchOnly:
        def __getattr__(self, name):
            return getattr(served, name)

        def to_pylist(self):
            raise AssertionError("whole-table Python conversion")

    clipped = rs.clip_dormant(BatchOnly(), {BOARD_A: [(DAYS[1], DAYS[2], DAYS[3])]})
    assert [
        (r["served_from"], r["served_to"], r["starts_as"]) for r in clipped.to_pylist()
    ] == [(DAYS[0], DAYS[2], None), (DAYS[3], None, "revived")]
