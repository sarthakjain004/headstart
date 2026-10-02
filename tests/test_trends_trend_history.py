"""Tests for ``headstart.trends.trend_history`` (ADR-0230), Trends' one owner of its history.

The Space's ``/trends`` answers are pinned by ``tests/test_space_app.py``; this file pins what
the history must hold for those answers to be right:

- ``record_tick`` writes one file a tick, even when nothing moved, and a new classifier head as an
  ordinary delta; replayed, the files give back every level each tick recorded.
- A tick's Methodology rides its own file, and a counting change is a tick whose Methodology
  differs from the tick before it.
- An unreadable ledger is an empty history, never a failed boot.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pytest

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")
old_layout_converter = pytest.importorskip("old_layout_trends_state_converter")

import duplicate_removal_trends_state

from headstart.ingest import role_trends
from headstart.trends import line_reading, netting, role_taxonomy, trend_history
from headstart.trends.trend_history import (
    TrendHistory,
    TrendQuestion,
    TrendsUnavailable,
)

_NO_CONFIG = Path(__file__).resolve().parent / "no-trends-config"
_START = datetime.fromisoformat("2026-09-11T00:00:00+00:00")


def _stamp(days: float) -> str:
    return (_START + timedelta(days=days)).isoformat(timespec="seconds")


def _served(jobs: list[dict]):
    """The served table's columns `count_board_groups` reads, one row per Job."""
    return pa.table(
        {
            "id": [job["id"] for job in jobs],
            "min_years": pa.array([job.get("years") for job in jobs], pa.int32()),
            "title": [job["title"] for job in jobs],
            "employment_type": [job.get("type", "full-time") for job in jobs],
            "ats": [job["board"].split(":", 1)[0] for job in jobs],
            "first_seen": [job["seen"] for job in jobs],
        }
    )


_BACKEND = role_taxonomy.WatchRole(
    "backend", "Backend", "software-engineering", [r"backend"]
)

# Every Job ever served; each tick below serves some of them, some under another family.
_JOBS = {
    "1": {"board": "greenhouse:acme", "title": "Backend Engineer", "years": 3},
    "2": {"board": "greenhouse:acme", "title": "ML Engineer", "years": 6},
    "3": {"board": "greenhouse:acme", "title": "Cashier"},
    "4": {"board": "workday:big/a", "title": "Senior Backend Developer", "years": 8},
    "5": {"board": "workday:big/a", "title": "QA Engineer"},
    "6": {"board": "lever:newco", "title": "Frontend Engineer", "years": 1},
    "7": {"board": "workday:big/a", "title": "Data Engineer", "years": 2},
}
_FAMILY = {
    "1": "software-engineering",
    "2": "ai-ml",
    "3": None,  # non-tech
    "4": "software-engineering",
    "5": "qa-test",
    "6": "software-engineering",
    "7": "data-engineering",
}
_SEEN = {"1": 1.5, "2": -30, "3": -30, "4": -30, "5": -30, "6": 2.5, "7": 6.5}

# (days after _START, the classifier head, the Jobs served, family moves, re-banded Jobs)
_TICKS = [
    (2, 2, "12345", {}, {}),
    # the cashier closes, so non-tech falls to 0; a Board is found; a Job re-bands
    (3, 2, "12456", {}, {"2": 2}),
    # nothing moved: an empty file
    (3.5, 2, "12456", {}, {"2": 2}),
    # a new head: a Job moves family, one more delta
    (4, 3, "12456", {"4": "ai-ml"}, {"2": 2}),
    # one closes, one opens, and the tick books turnover
    (7, 3, "24567", {"4": "ai-ml"}, {"2": 2}),
    # Job 6 ages out of `new`
    (12, 3, "24567", {"4": "ai-ml"}, {"2": 2}),
]
_TURNOVER = {
    ("workday:big/a", "opened", "data-engineering", "mid"): 1,
    ("greenhouse:acme", "closed", "software-engineering", "mid"): 1,
    ("lever:newco", "unscoped", "all", "all"): 1,
}


def _methodology(head: int) -> trend_history.Methodology:
    return trend_history.Methodology("7681eb07a2b5", head, 5, 15, 5)


def _write_ticks(state: Path) -> dict[str, tuple[dict, dict]]:
    """Every tick recorded as ``role_trends`` records it: its own counting, then ``record_tick``.
    Returns each tick's Board levels and its index-wide counts as the aggregate ledger held them.
    """
    recorded = {}
    for days, head, served, moved, rebanded in _TICKS:
        ts = _stamp(days)
        jobs = [
            {
                **_JOBS[i],
                "id": f"{_JOBS[i]['board']}:{i}",
                "years": rebanded.get(i, _JOBS[i].get("years")),
                "seen": _stamp(_SEEN[i]),
            }
            for i in served
        ]
        families = [moved.get(i, _FAMILY[i]) for i in served]
        counts, non_tech, _, board_counts = role_trends.count_board_groups(
            _served(jobs),
            families,
            [_BACKEND],
            _stamp(days - role_trends.NEW_WINDOW_DAYS),
            [job["board"] for job in jobs],
        )
        turnover = _TURNOVER if days == 7 else {}
        trend_history.record_tick(state, ts, board_counts, turnover, _methodology(head))
        index = {**counts, ("stock", role_taxonomy.NON_TECH, "all", "all"): non_tech}
        recorded[ts] = (board_counts, index)
    return recorded


def test_the_replay_gives_back_every_recorded_tick(tmp_path):
    recorded = _write_ticks(tmp_path)
    history = TrendHistory.load(tmp_path, _NO_CONFIG)
    assert history.ticks == tuple(recorded)
    assert [
        ts for ts, (_, index) in recorded.items() if history.index_counts(ts) != index
    ] == []
    newest, levels = trend_history.board_levels(tmp_path)
    assert (newest, levels) == (_stamp(12), list(recorded.values())[-1][0])
    # the fixture reaches what it means to: a tick with nothing moved, and non-tech at 0
    files = sorted((tmp_path / trend_history.DELTAS).glob("*.parquet"))
    assert len(files) == len(_TICKS)
    assert min(pq.read_table(file).num_rows for file in files) == 0
    assert recorded[_stamp(3)][1][("stock", role_taxonomy.NON_TECH, "all", "all")] == 0


def test_a_new_head_is_one_more_delta_not_a_baseline(tmp_path):
    _write_ticks(tmp_path)
    directory = tmp_path / trend_history.DELTAS
    tick = pq.read_table(trend_history.tick_path(directory, _stamp(4)))
    # only Job 4 moves, out of software-engineering into ai-ml; no other Board is re-written
    assert {row["board"] for row in tick.to_pylist()} == {"workday:big/a"}
    stamped = json.loads(tick.schema.metadata[b"methodology"])
    assert stamped["family_classifier_version"] == 3


def test_openings_are_each_boards_tech_stock_now(tmp_path):
    _write_ticks(tmp_path)
    openings = TrendHistory.load(tmp_path, _NO_CONFIG).openings()
    # Job 2 (ai-ml) and Job 4, moved to ai-ml by the new head; Jobs 5 and 7; Job 6. The
    # watched Backend role re-counts Job 4 and is no opening of its own.
    assert openings == {"greenhouse:acme": 1, "workday:big/a": 3, "lever:newco": 1}


def test_a_tick_no_newer_than_the_newest_is_refused(tmp_path):
    _write_ticks(tmp_path)
    with pytest.raises(ValueError, match="not newer"):
        trend_history.record_tick(tmp_path, _stamp(12), {}, {}, _methodology(3))


def test_a_counting_change_is_a_tick_whose_methodology_moved(tmp_path):
    _write_ticks(tmp_path)
    epochs = TrendHistory.load(tmp_path, _NO_CONFIG).unnetted_answer(TrendQuestion())[
        "epochs"
    ]
    assert epochs == [
        {
            "ts": _stamp(4),
            "changed": [
                "we sorted jobs into categories more accurately, so some jobs moved to a different category"
            ],
            "fields": ["family_classifier_version"],
        }
    ]


def test_comparable_coverage_serves_the_removals_on_its_cohorts_boards_only(tmp_path):
    """A Board found later is out of a comparable cohort, and so is its removal; a removal on a
    cohort Board still halves what that Board counted (ADR-0233). Serving none, Micron read +160
    under Comparable and +83 under All."""
    state = duplicate_removal_trends_state
    state.write(tmp_path, board_found_later=True)
    history = TrendHistory.load(tmp_path, _NO_CONFIG)
    micro = (state.MICRO,)
    cohorts = {
        "ts": state.TICKS[state.REMOVAL],
        "company": state.MICRO,
        "count": state.REMOVED_ROWS,
    }
    late = {
        "ts": state.TICKS[state.LATE_REMOVAL],
        "company": state.MICRO,
        "count": state.LATE_REMOVED_ROWS,
    }
    every = history.unnetted_answer(TrendQuestion(companies=micro))["evicted"]
    assert every == [cohorts, late]
    comparable = TrendQuestion(companies=micro, coverage="comparable")
    assert history.unnetted_answer(comparable)["evicted"] == [cohorts]


def test_the_index_sizes_each_board_found_by_the_line_it_lands_in(tmp_path):
    """A Board first counted after the window's first run lands its backlog in the index's lines
    at once; the answer sizes it line by line from the Board's first deltas (ADR-0304). The
    ledger's first tick is every Board's baseline, not a Board found."""
    _write_ticks(tmp_path)
    history = TrendHistory.load(tmp_path, _NO_CONFIG)
    found = [
        {
            "ts": _stamp(3),
            "company": None,
            "boards": 1,
            "openings": 1,
            "lines": {"software-engineering": 1},
            "served": 1,
        }
    ]
    assert history.unnetted_answer(TrendQuestion())["discovered"] == found
    by_level = history.unnetted_answer(TrendQuestion(family="software-engineering"))
    assert [f["lines"] for f in by_level["discovered"]] == [{"entry": 1}]
    # Not a Board of the ATS picked, not in a comparable cohort, and not under New.
    for question in (
        TrendQuestion(ats=("greenhouse",)),
        TrendQuestion(coverage="comparable"),
        TrendQuestion(metric="new"),
        TrendQuestion(since=_stamp(3)),
    ):
        assert history.unnetted_answer(question)["discovered"] == [], question


def test_an_unreadable_ledger_is_an_empty_history(tmp_path):
    directory = tmp_path / "role_trend_board_deltas"
    directory.mkdir()
    (directory / "2026-09-13T00-00-00+00-00.parquet").write_bytes(b"not parquet")
    (tmp_path / "company_directory.json").write_text(
        json.dumps({"companies": [{"name": "Acme", "boards": ["greenhouse:acme"]}]}),
        encoding="utf-8",
    )
    history = TrendHistory.load(tmp_path, _NO_CONFIG)
    assert history.ticks == ()
    assert list(history.companies) == ["greenhouse:acme"]
    with pytest.raises(TrendsUnavailable):
        history.unnetted_answer(TrendQuestion())


@pytest.mark.parametrize(
    ("question", "message"),
    [
        (TrendQuestion(metric="flow"), "metric must be 'stock' or 'new'"),
        (TrendQuestion(coverage="future"), "coverage must be 'all' or 'comparable'"),
        (
            TrendQuestion(split="everything"),
            "split must be 'bands', 'roles' or 'company'",
        ),
        (TrendQuestion(split="company"), "split=company needs at least one company"),
        (TrendQuestion(since=""), "since/until/base must be ISO-8601"),
        (
            TrendQuestion(companies=("greenhouse:nobody",)),
            "unknown company: greenhouse:nobody",
        ),
    ],
)
def test_a_bad_question_is_a_value_error_naming_what_is_wrong(
    tmp_path, question, message
):
    _write_ticks(tmp_path)
    (tmp_path / "company_directory.json").write_text(
        json.dumps({"companies": [{"name": "Acme", "boards": ["greenhouse:acme"]}]}),
        encoding="utf-8",
    )
    history = TrendHistory.load(tmp_path, _NO_CONFIG)
    with pytest.raises(ValueError, match=re.escape(message)):
        history.unnetted_answer(question)


def test_the_module_keeps_no_copy_of_the_reserved_names():
    """NON_TECH and WATCH_PREFIX have one home, `headstart.trends.role_taxonomy` (ADR-0230)."""
    assert trend_history.NON_TECH is role_taxonomy.NON_TECH
    assert trend_history.WATCH_PREFIX is role_taxonomy.WATCH_PREFIX


def _coverage_ticks(state):
    """An old employer adds a second Board with backlog, then records actual turnover.

    The baseline Board leaves the served stock; failed and unscheduled reads do not
    establish endpoint freshness. An authoritative empty Board leaves no count history.
    """
    old, added, newcomer = "workday:acme/main", "workday:acme/second", "lever:newco"
    for day, stocks, events in (
        (0, {old: 10}, {}),
        (
            1,
            {old: 10, added: 100, newcomer: 20},
            {
                (added, "recounted_in", "software-engineering", "mid"): 100,
                (newcomer, "recounted_in", "software-engineering", "mid"): 20,
            },
        ),
        (
            2,
            {old: 10, added: 102, newcomer: 20},
            {
                (added, "opened", "software-engineering", "mid"): 3,
                (added, "closed", "software-engineering", "mid"): 1,
                (old, "unscoped", "all", "all"): 1,
            },
        ),
        (
            3,
            {added: 102, newcomer: 20},
            {
                (old, "recounted_out", "software-engineering", "mid"): 10,
                (added, "unscoped", "all", "all"): 1,
            },
        ),
    ):
        trend_history.record_tick(
            state,
            _stamp(day),
            {(b, "stock", "software-engineering", "mid"): n for b, n in stocks.items()},
            events,
            _methodology(2),
        )
    (state / "company_directory.json").write_text(
        json.dumps(
            {
                "companies": [
                    {"name": "Acme", "boards": [old, added]},
                    {"name": "Newco", "boards": [newcomer]},
                ]
            }
        )
    )
    return TrendHistory.load(state, _NO_CONFIG)


def test_coverage_summary_keeps_backlog_separate_from_later_openings(tmp_path):
    history = _coverage_ticks(tmp_path)
    answer = history.unnetted_answer(TrendQuestion(coverage="comparable"))
    summary = answer["coverage_summary"]
    assert summary["membership_basis"] == "first_stock_count"
    assert summary["cohort"]["boards"] == 1
    assert summary["cohort"]["stock_start"] == 10
    assert summary["cohort"]["stock_latest"] == 0
    assert summary["cohort"]["net_recounted"] == -10
    assert summary["entrants"]["boards"] == 2
    assert summary["entrants"]["first_counted_backlog"] == 120
    assert summary["entrants"]["observed_opened"] == 3
    assert summary["entrants"]["recorded_closed"] == 1
    assert summary["entrants"]["net_recounted"] == 0
    assert summary["entrants"]["observed_since"] == _stamp(1)
    assert summary["all_known"] == {"boards": 3, "stock_start": 10, "stock_latest": 122}
    # Newcomer activity is visible even though absent from the fixed cohort chart.
    assert answer["series"][0]["latest"] == 0
    assert summary["entrants"]["stock_latest"] == 122
    assert line_reading.trends_payload(answer)[0]["coverage_summary"] == summary


def test_existing_employers_new_board_is_a_coverage_addition(tmp_path):
    history = _coverage_ticks(tmp_path)
    summary = history.unnetted_answer(
        TrendQuestion(
            coverage="comparable",
            companies=("workday:acme/main",),
        )
    )["coverage_summary"]
    assert summary["cohort"]["boards"] == 1
    assert summary["entrants"]["boards"] == 1
    assert summary["entrants"]["first_counted_backlog"] == 100
    assert summary["entrants"]["observed_opened"] == 3
    assert summary["all_known"]["stock_latest"] == 102


def test_failed_and_missing_reads_leave_coverage_quality_unknown(tmp_path):
    summary = _coverage_ticks(tmp_path).unnetted_answer(TrendQuestion())[
        "coverage_summary"
    ]
    assert summary["cohort"]["closures_unseen"] == 1
    assert summary["entrants"]["closures_unseen"] == 1
    assert summary["quality"] == {
        "start_eligibility": "unknown",
        "endpoint_freshness": "unknown",
        "successful_zero_boards": "unknown",
        "event_causes": "unknown",
    }
    # No closure marker on Newco does not establish a successful endpoint read.
    assert summary["entrants"]["recorded_closed"] == 1


def test_range_changes_choose_different_first_counted_populations(tmp_path):
    history = _coverage_ticks(tmp_path)
    start = history.unnetted_answer(TrendQuestion(coverage="comparable"))[
        "coverage_summary"
    ]
    later = history.unnetted_answer(
        TrendQuestion(coverage="comparable", base=_stamp(1))
    )["coverage_summary"]
    assert (start["baseline"], start["cohort"]["boards"]) == (_stamp(0), 1)
    assert (later["baseline"], later["cohort"]["boards"]) == (_stamp(1), 3)
    assert later["entrants"]["boards"] == 0
    assert later["cohort"]["observed_opened"] == 3


def test_summary_respects_explicit_earlier_base_and_source_filter(tmp_path):
    history = _coverage_ticks(tmp_path)
    summary = history.unnetted_answer(
        TrendQuestion(
            coverage="comparable",
            base=_stamp(0),
            since=_stamp(2),
            ats=("workday",),
        )
    )["coverage_summary"]
    assert summary["baseline"] == _stamp(0)
    assert summary["cohort"]["boards"] == 1
    assert summary["entrants"]["first_counted_backlog"] == 100
    # The opening booked at the window start predates this window's activity.
    assert summary["entrants"]["observed_opened"] == 0
    assert summary["all_known"]["stock_start"] == 112


@pytest.mark.parametrize("metric", ["stock", "new"])
def test_coverage_inventory_has_same_stock_meaning_under_every_metric(tmp_path, metric):
    history = _coverage_ticks(tmp_path)
    summary = history.unnetted_answer(TrendQuestion(metric=metric))["coverage_summary"]
    assert summary["all_known"]["stock_latest"] == 122
    assert summary["entrants"]["first_counted_backlog"] == 120


def test_single_tick_has_unknown_turnover_not_zero(tmp_path):
    summary = _coverage_ticks(tmp_path).unnetted_answer(TrendQuestion(until=_stamp(1)))[
        "coverage_summary"
    ]
    assert summary["entrants"]["observed_opened"] is None
    assert summary["entrants"]["recorded_closed"] is None


def test_first_recorded_turnover_tick_is_available_at_window_end(tmp_path):
    board = "lever:acme"
    for day, n, turnover in (
        (0, 10, {}),
        (1, 12, {(board, "opened", "software-engineering", "mid"): 2}),
    ):
        trend_history.record_tick(
            tmp_path,
            _stamp(day),
            {(board, "stock", "software-engineering", "mid"): n},
            turnover,
            _methodology(2),
        )
    summary = TrendHistory.load(tmp_path, _NO_CONFIG).unnetted_answer(TrendQuestion())[
        "coverage_summary"
    ]
    assert summary["cohort"]["observed_opened"] == 2


def test_category_summary_excludes_nontech_and_watch_duplicates(tmp_path):
    _write_ticks(tmp_path)
    history = TrendHistory.load(tmp_path, _NO_CONFIG)
    summary = history.unnetted_answer(
        TrendQuestion(
            family="software-engineering",
            split="roles",
        )
    )["coverage_summary"]
    assert summary["scope"] == "family"
    assert summary["all_known"] == {"boards": 3, "stock_start": 2, "stock_latest": 1}
    assert summary["entrants"]["first_counted_backlog"] == 1


def test_unknown_other_drill_summary_explicitly_covers_all_tech(tmp_path):
    history = _coverage_ticks(tmp_path)
    summary = history.unnetted_answer(TrendQuestion(family="other"))["coverage_summary"]
    assert summary["scope"] == "tech"
    assert summary["family"] is None
    assert summary["all_known"]["stock_latest"] == 122


def test_hidden_family_summary_counts_its_actual_rows(tmp_path):
    board = "lever:acme"
    for day, count in ((0, 3), (1, 4)):
        trend_history.record_tick(
            tmp_path,
            _stamp(day),
            {
                (board, "stock", "unclassified-tech", "mid"): count,
            },
            {},
            _methodology(2),
        )
    summary = TrendHistory.load(tmp_path, _REPO_FAMILIES.parent).unnetted_answer(
        TrendQuestion(family="unclassified-tech")
    )["coverage_summary"]
    assert summary["scope"] == "family"
    assert summary["family"] == "unclassified-tech"
    assert summary["family_label"] == "Other"
    assert summary["all_known"] == {"boards": 1, "stock_start": 3, "stock_latest": 4}


def test_category_lineage_keeps_entrant_activity_and_arrival_read_gaps(tmp_path):
    old, entrant = "lever:old", "lever:added"
    for day, count, events in (
        (0, 0, {}),
        (
            1,
            100,
            {
                (entrant, "recounted_in", "ai-ml", "mid"): 100,
                (entrant, "unscoped", "all", "all"): 1,
            },
        ),
        (2, 103, {(entrant, "opened", "ai-ml", "mid"): 3}),
    ):
        levels = {(old, "stock", "software-engineering", "mid"): 10}
        if count:
            levels[entrant, "stock", "ai-ml", "mid"] = count
        trend_history.record_tick(
            tmp_path, _stamp(day), levels, events, _methodology(2)
        )
    history = TrendHistory.load(tmp_path, _REPO_FAMILIES.parent)
    summary = history.unnetted_answer(
        TrendQuestion(
            family="ai-ml-data-science",
            coverage="comparable",
        )
    )["coverage_summary"]
    assert summary["scope"] == "family"
    assert summary["family"] == "ai-ml-data-science"
    assert summary["cohort"]["stock_latest"] == 0
    assert summary["entrants"]["first_counted_backlog"] == 100
    assert summary["entrants"]["stock_latest"] == 103
    assert summary["entrants"]["observed_opened"] == 3
    # Read quality includes arrival-tick failures even though its backlog is not activity.
    assert summary["entrants"]["closures_unseen"] == 1
    assert summary["entrants"]["net_recounted"] == 0


def test_count_summary_cannot_see_an_authoritatively_read_zero_board(tmp_path):
    # Count files have no read outcome. Even naming a zero level does not store its Board.
    empty = "lever:empty"
    trend_history.record_tick(
        tmp_path,
        _stamp(-1),
        {
            (empty, "stock", "software-engineering", "mid"): 0,
        },
        {},
        _methodology(2),
    )
    history = _coverage_ticks(tmp_path)
    answer = history.unnetted_answer(TrendQuestion(coverage="comparable"))
    summary = answer["coverage_summary"]
    assert answer["base"] == _stamp(0)
    assert summary["from"] == _stamp(0)
    assert summary["cohort"]["boards"] == 1
    assert summary["quality"]["successful_zero_boards"] == "unknown"
    assert (
        history.unnetted_answer(TrendQuestion(until=_stamp(-1)))["coverage_summary"]
        is None
    )


# ---- the taxonomy, directory and rule copies the answers read (moved from the Space's tests)

_REPO_FAMILIES = Path(__file__).resolve().parents[1] / "config" / "role_families.json"


def test_load_directory_keys_each_company_by_its_first_board(tmp_path):
    path = tmp_path / "company_directory.json"
    path.write_text(
        '{"companies": [{"name": "Hpe", "boards": ["workday:hpe/a", "workday:hpe/b"]}]}',
        encoding="utf-8",
    )
    assert list(trend_history._load_directory(path)) == ["workday:hpe/a"]
    path.write_text("{half-written", encoding="utf-8")
    assert trend_history._load_directory(path) == {}
    assert trend_history._load_directory(tmp_path / "absent.json") == {}


def test_a_directory_written_before_staffing_is_tagged_as_the_current_list_says(
    tmp_path,
):
    """The live file had no `staffing` Operator, so until the next run rewrote it Hot would have
    shown Randstad, Collabera and Sonsoft as IT services (review of #731). The Space decides
    each company's Operator as it loads the file (ADR-0238)."""
    path = tmp_path / "company_directory.json"
    old_layout = [
        ("Randstad", ["workable:randstad"], "services"),
        ("Collabera", ["smartrecruiters:collabera2"], "services"),
        ("Sonsoft Inc", ["smartrecruiters:SonsoftInc"], "services"),
        ("Mindlance", ["smartrecruiters:mindlance2"], "employer"),
        ("Wipro", ["successfactors:careers.wipro.com"], "services"),
        ("Jobgether", ["lever:jobgether"], "aggregator"),
        ("Acme", ["greenhouse:acme"], "employer"),
    ]
    path.write_text(
        json.dumps(
            {
                "companies": [
                    {"name": name, "boards": boards, "operator": op}
                    for name, boards, op in old_layout
                ]
            }
        ),
        encoding="utf-8",
    )
    operators = {
        entry["name"]: entry["operator"]
        for entry in trend_history._load_directory(path).values()
    }
    assert operators == {
        "Randstad": "staffing",
        "Collabera": "staffing",
        "Sonsoft Inc": "staffing",
        "Mindlance": "staffing",
        "Wipro": "services",
        "Jobgether": "aggregator",
        "Acme": "employer",
    }
    # A file from before any Operator is tagged the same way.
    path.write_text(
        '{"companies": [{"name": "Randstad", "boards": ["workable:randstad"]}]}',
        encoding="utf-8",
    )
    assert trend_history._load_directory(path)["workable:randstad"]["operator"] == (
        "staffing"
    )


def test_retired_families_keep_their_labels(tmp_path):
    """While a new head's title cache warms up, the Space still serves the older series, whose
    families the curated list no longer names; `retired` keeps them readable."""
    path = tmp_path / "role_families.json"
    path.write_text(
        json.dumps(
            {
                "families": [{"name": "frontend-web", "label": "Frontend & Web"}],
                "retired": [
                    {"name": "web-development", "label": "Web & .NET Development"}
                ],
            }
        ),
        encoding="utf-8",
    )
    labels = trend_history.family_labels(path)
    assert labels == {
        "frontend-web": "Frontend & Web",
        "web-development": "Web & .NET Development",
    }


def test_every_retired_family_names_a_current_successor():
    spec = json.loads(_REPO_FAMILIES.read_text(encoding="utf-8"))
    current = {f["name"] for f in spec["families"]}
    successors = trend_history.family_successors(_REPO_FAMILIES)
    assert set(successors) == {f["name"] for f in spec["retired"]}
    assert set(successors.values()) <= current


def test_a_family_is_read_by_the_name_the_data_holds(monkeypatch):
    """ADR-0220 renamed families; old links and new config meet the data by either name."""
    from collections import Counter

    successors = trend_history.family_successors(_REPO_FAMILIES)

    def resolve(family, present):
        return trend_history._resolve_family(family, present, successors)

    assert resolve("ai-ml", Counter({"ai-ml": 3, "devops": 1})) == "ai-ml"
    assert resolve("ai-ml", Counter({"ai-ml-data-science": 5})) == "ai-ml-data-science"
    assert (
        resolve("security", Counter({"security-engineering": 2}))
        == "security-engineering"
    )
    # two predecessors hold data: the larger answers for the new name
    assert (
        resolve("ai-ml-data-science", Counter({"ai-ml": 9, "data-science": 4}))
        == "ai-ml"
    )
    assert resolve(None, Counter()) is None


def test_a_stock_series_a_run_leaves_out_is_at_zero_there():
    """Emptied by a refit, a category reads 0, so its drop is booked, not hidden in a gap."""
    held = trend_history._held_at_zero
    assert held([None, 46, 46, None, None], "stock") == [None, 46, 46, 0, 0]
    assert held([None, 3, None], "new") == [None, 3, None], "new keeps its own rule"
    assert held([None, 3, None], None) == [None, 3, None], (
        "so does the chart with no pick"
    )


@pytest.mark.parametrize(
    ("boards", "touched"),
    [
        (["workday:acme/a", "workday:acme/b"], True),
        (["workday:acme/a", "workday:other/b"], False),  # two Tenants, nothing to do
        (["workday:ACME/a", "workday:acme/b"], True),  # one Tenant, compared case-blind
        (["workday:acme/a", "greenhouse:acme"], False),
        (["eightfold:jobs.acme.com"], True),
        (["taleo_enterprise:acme/1", "taleo_enterprise:acme/2"], True),
    ],
)
def test_duplicate_removal_touches_a_company_by_its_boards(boards, touched):
    """ADR-0227: the Boards duplicate removal can move. Hot kept a copy of this rule, pinned
    here to trend_netting's, until ADR-0230 ranked it from the history; now it has none."""
    assert netting.dedup_touched(boards) is touched


def _record_stock(state: Path, ts: str, by_family: dict[str, int]) -> None:
    """One tick of one Board holding ``by_family`` openings, in the current layout."""
    levels = {
        ("greenhouse:acme", "stock", family, "mid"): count
        for family, count in by_family.items()
    }
    methodology = trend_history.Methodology(
        family_list_fingerprint="f",
        family_classifier_version=1,
        tech_filter_version=1,
        derivations_version=1,
        dedup_version=1,
    )
    trend_history.record_tick(state, ts, levels, {}, methodology)


def test_the_share_denominator_takes_out_every_job_a_found_board_brought(tmp_path):
    """The share's denominator is every served job, so a found Board's backlog comes out of it
    whole, non-tech and every category included (#889 review). Netted by the view's own lines'
    openings, All tech roles read a share of −4.17% over a week of −1.35% hiring, and Software
    Engineering by level −17.2% on −7.3%."""
    methodology = trend_history.Methodology("f", 1, 1, 1, 1)
    acme = {("greenhouse:acme", "stock", role_taxonomy.NON_TECH, "all"): 100}
    newco = {
        ("lever:newco", "stock", "software-engineering", "mid"): 10,
        ("lever:newco", "stock", "qa-test", "mid"): 20,
        ("lever:newco", "stock", role_taxonomy.NON_TECH, "all"): 70,
    }
    for days, swe, found in (
        (0, 100, False),
        (1, 100, False),
        (2, 100, True),
        (4, 110, True),
    ):
        levels = {
            **acme,
            ("greenhouse:acme", "stock", "software-engineering", "mid"): swe,
        }
        trend_history.record_tick(
            tmp_path,
            _stamp(days),
            {**levels, **(newco if found else {})},
            {},
            methodology,
        )
    history = TrendHistory.load(tmp_path, _NO_CONFIG)
    index = history.unnetted_answer(TrendQuestion())
    assert [(f["openings"], f["served"]) for f in index["discovered"]] == [(30, 100)]
    total = line_reading.read_answer(index).total.move
    # 130 netted tech openings over 300 served jobs at the start, 140 over 310 now.
    assert (total.hiring, total.share.denominator_start) == (10, 300)
    assert total.share.percent == pytest.approx((140 / 310) / (130 / 300) * 100 - 100)
    # A view narrowed to some ATSes holds no non-tech in its denominator: the index counts
    # non-tech as one row over every ATS.
    lever = history.unnetted_answer(TrendQuestion(ats=("greenhouse", "lever")))
    assert [(f["openings"], f["served"]) for f in lever["discovered"]] == [(30, 30)]
    by_level = history.unnetted_answer(TrendQuestion(family="software-engineering"))
    assert [(f["openings"], f["served"]) for f in by_level["discovered"]] == [(10, 100)]
    swe = line_reading.read_answer(by_level).total.move
    assert (swe.hiring, swe.share.denominator_start) == (10, 300)
    assert swe.share.percent == pytest.approx((120 / 310) / (110 / 300) * 100 - 100)


def test_a_hidden_family_is_the_last_series_and_reads_as_other(tmp_path):
    """The largest line by far is the hidden one; it must still come last, under a name that
    gives nothing away, and the answer says which series the readers do not list."""
    config = tmp_path / "config"
    config.mkdir()
    (config / "role_families.json").write_text(
        json.dumps(
            {
                "families": [
                    {"name": "software-engineering", "label": "Software Engineering"},
                    {"name": "frontend-web", "label": "Frontend & Web"},
                    {"name": "mystery", "label": "Mystery", "hidden": True},
                ]
            }
        ),
        encoding="utf-8",
    )
    state = tmp_path / "state"
    _record_stock(
        state,
        _stamp(0),
        {"software-engineering": 50, "frontend-web": 10, "mystery": 200},
    )
    answer = TrendHistory.load(state, config).unnetted_answer(TrendQuestion())
    assert [(s["name"], s["label"]) for s in answer["series"]] == [
        ("software-engineering", "Software Engineering"),
        ("frontend-web", "Frontend & Web"),
        ("mystery", "Other"),
    ]
    assert answer["unlisted_series"] == ["mystery"]
    # the hidden family stays in the whole: the total still counts its 200
    assert answer["totals"][-1] == 260


def test_with_no_hidden_family_every_series_stays_listed_by_size(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    (config / "role_families.json").write_text(
        json.dumps(
            {"families": [{"name": "a", "label": "A"}, {"name": "b", "label": "B"}]}
        ),
        encoding="utf-8",
    )
    state = tmp_path / "state"
    _record_stock(state, _stamp(0), {"a": 1, "b": 5})
    answer = TrendHistory.load(state, config).unnetted_answer(TrendQuestion())
    assert [s["name"] for s in answer["series"]] == ["b", "a"]
    assert answer["unlisted_series"] == []


def _write_opened_history(state: Path) -> None:
    """One Board over 16 daily ticks: a 10-job backlog, then two jobs opened a day from day 3,
    the first tick that books turnover (ADR-0227)."""
    directory = state / "role_trend_board_deltas"
    directory.mkdir(parents=True, exist_ok=True)
    for day in range(16):
        ts = _stamp(day)
        grown = 10 if day == 0 else 2 if day >= 3 else 0
        metrics = {"stock": grown, "new": grown, "opened": 2 if day >= 3 else 0}
        rows = [
            {
                "ts": ts,
                "board": "greenhouse:acme",
                "metric": metric,
                "family": "software-engineering",
                "band": "mid",
                "ats": "greenhouse",
                "delta": delta,
            }
            for metric, delta in metrics.items()
            if delta or metric == "stock"
        ]
        table = pa.Table.from_pylist(rows).replace_schema_metadata(
            {b"centroid_version": b"3003", b"ts": ts.encode()}
        )
        pq.write_table(table, directory / f"{ts.replace(':', '-')}.parquet")
    (state / "company_directory.json").write_text(
        json.dumps({"companies": [{"name": "Acme", "boards": ["greenhouse:acme"]}]}),
        encoding="utf-8",
    )


@pytest.mark.parametrize("companies", [(), ("greenhouse:acme",)])
def test_new_becomes_the_week_of_opened_jobs_once_a_whole_week_has_them(
    tmp_path, companies
):
    """ADR-0230 decision 5: `new` is the jobs Opened over the trailing week, from the first tick
    whose whole week has Opened facts; before it, the level it always was. The switch is a
    counting change of its own, marked where it lands."""
    _write_opened_history(tmp_path)
    history = TrendHistory.load(
        old_layout_converter.store_in_current_layout(tmp_path), _NO_CONFIG
    )
    answer = history.unnetted_answer(TrendQuestion(metric="new", companies=companies))

    # turnover began on day 3; day 10 is the first with a whole week
    switch = _stamp(10)
    assert answer["new_inflow_from"] == switch
    points = answer["series"][0]["points"]
    at = answer["stamps"].index(switch)
    # Seven days of two opened jobs each, where the level had counted the backlog as new.
    assert points[at:] == [14] * (len(points) - at)
    assert points[at - 1] != 14
    switched = [e for e in answer["epochs"] if e["ts"] == switch]
    assert switched and switched[0]["fields"] == ["new_became_inflow"]
    # its reading nets every line, and a netted line ends on its measured latest value
    reading = line_reading.read_answer(answer)
    points = {line["name"]: line["points"] for line in answer["series"]}
    for line in reading.lines:
        assert [v for v in line.netted if v is not None][-1] == points[line.name][-1]
    if reading.total is not None:
        total = reading.total
        assert [v for v in total.netted if v is not None][-1] == total.points[-1]


def test_all_openings_carry_no_switch_of_new(tmp_path):
    _write_opened_history(tmp_path)
    answer = TrendHistory.load(
        old_layout_converter.store_in_current_layout(tmp_path), _NO_CONFIG
    ).unnetted_answer(TrendQuestion())
    assert answer["new_inflow_from"] is None
    assert all("new_became_inflow" not in e["fields"] for e in answer["epochs"])


def test_a_tick_written_from_a_given_replay_is_the_one_written_unaided(tmp_path):
    """#716: `role_trends` hands `record_tick` the replay it already made; the file is the same."""
    _write_ticks(tmp_path)
    replayed = trend_history.board_levels(tmp_path)
    moved, *kept = sorted(replayed[1])  # one group moves, one drops to 0, the rest hold
    levels = {moved: replayed[1][moved] + 3, **{k: replayed[1][k] for k in kept[1:]}}
    other = tmp_path / "copy"
    shutil.copytree(tmp_path / trend_history.DELTAS, other / trend_history.DELTAS)
    ts = _stamp(20)
    trend_history.record_tick(
        tmp_path, ts, levels, {}, _methodology(3), replayed=replayed
    )
    trend_history.record_tick(other, ts, levels, {}, _methodology(3))
    name = trend_history.tick_path(Path(trend_history.DELTAS), ts)
    written = pq.read_table(tmp_path / name)
    assert written.num_rows == 2 and written.equals(pq.read_table(other / name))


def test_a_jobs_board_and_directory_name_come_from_the_directorys_own_keys(tmp_path):
    """A Workday native id can carry a colon ("REQ: 228"), where `board_of` guesses a Board that
    does not exist; the directory's own keys name the real one (ADR-0049, ADR-0332)."""
    (tmp_path / "company_directory.json").write_text(
        json.dumps(
            {
                "companies": [
                    {"name": "Kotak", "boards": ["oracle:hcbt.fa.em2.oraclecloud.com"]},
                    {
                        "name": "Acme",
                        "boards": ["workday:acme.wd1.myworkdayjobs.com/External"],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    history = trend_history.TrendHistory.load(tmp_path, _REPO_FAMILIES.parent)
    assert history.board_and_name_of_job("oracle:hcbt.fa.em2.oraclecloud.com:123") == (
        "oracle:hcbt.fa.em2.oraclecloud.com",
        "Kotak",
    )
    assert history.board_and_name_of_job(
        "workday:acme.wd1.myworkdayjobs.com/External:REQ: 228"
    ) == ("workday:acme.wd1.myworkdayjobs.com/External", "Acme")
    assert history.board_and_name_of_job("lever:nobody:1") == ("lever:nobody", None)
