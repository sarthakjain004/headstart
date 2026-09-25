"""Tests for ``headstart.trends.trend_history`` (ADR-0230), Trends' one owner of its history.

The Space's ``/trends`` answers are pinned by ``tests/test_space_app.py``; this file pins what
the history must hold for those answers to be right:

- ``record_tick`` writes one file a tick, even when nothing moved, and a new classifier head as an
  ordinary delta; replayed, the files give back every level each tick recorded.
- A tick's Methodology rides its own file, and a counting change is a tick whose Methodology
  differs from the tick before it.
- A history still in the older layout reads exactly as the one-off migration rewrites it.
- An unreadable ledger is an empty history, never a failed boot.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

import duplicate_removal_trends_state
import old_layout_trends_state

from headstart.ingest import role_trends
from headstart.trends import history_migration as migration
from headstart.trends import netting, role_taxonomy, trend_history
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
    epochs = TrendHistory.load(tmp_path, _NO_CONFIG).answer(TrendQuestion())["epochs"]
    assert epochs == [
        {
            "ts": _stamp(4),
            "changed": ["role family assignment changed"],
            "fields": ["family_classifier_version"],
        }
    ]


def _migrated(old_state: Path, out: Path) -> Path:
    """``old_state`` rewritten into the step-6 layout under ``out``, as the one-off migration
    writes it."""
    tables = [
        pq.read_table(path)
        for path in sorted((old_state / trend_history.DELTAS).glob("*.parquet"))
    ]
    ticks, _ = migration.rewritten_ticks(tables, old_state / migration.EPOCHS)
    for table in ticks:
        path = trend_history.tick_path(
            out / trend_history.DELTAS, migration.tick_stamp(table)
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, path)
    archive = migration.archive_from_aggregate(
        old_state / migration.AGGREGATE,
        migration.tick_stamp(ticks[0]),
        old_state / migration.EPOCHS,
    )
    pq.write_table(archive, out / trend_history.ARCHIVE)
    return out


def test_an_older_layout_reads_as_its_migration_stores_it(tmp_path):
    old_state = old_layout_trends_state.write(tmp_path / "old")
    before = TrendHistory.load(old_state, _NO_CONFIG)
    after = TrendHistory.load(_migrated(old_state, tmp_path / "new"), _NO_CONFIG)
    assert before.ticks == after.ticks == tuple(old_layout_trends_state.T)
    for ts in before.ticks:
        assert before.index_counts(ts) == after.index_counts(ts)
    assert before.openings() == after.openings()
    for question in (TrendQuestion(), TrendQuestion(metric="new")):
        assert before.answer(question) == after.answer(question)


def test_a_tick_recorded_onto_the_older_layout_counts_against_its_migration(tmp_path):
    """The step-6 writer runs before the one-off migration: its first ticks land beside files
    in the older layout, and must count against the history the migration will store."""
    old = old_layout_trends_state
    state = old.write(tmp_path / "old")
    _, before = trend_history.board_levels(state)
    assert before == old.LEVELS[old.T[5]][1]
    now = {**before, (old.ACME, "stock", "software", "mid"): 6}
    ts = "2026-09-16T00:00:00+00:00"
    assert trend_history.record_tick(state, ts, now, {}, _methodology(3)) == 1
    assert trend_history.board_levels(state) == (ts, now)
    assert TrendHistory.load(state, _NO_CONFIG).ticks[-1] == ts


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
        history.answer(TrendQuestion())


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
        history.answer(question)


def test_the_module_keeps_no_copy_of_the_reserved_names():
    """NON_TECH and WATCH_PREFIX have one home, `headstart.trends.role_taxonomy` (ADR-0230)."""
    assert trend_history.NON_TECH is role_taxonomy.NON_TECH
    assert trend_history.WATCH_PREFIX is role_taxonomy.WATCH_PREFIX


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
    labels = trend_history._family_labels(path)
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
    history = TrendHistory.load(tmp_path, _NO_CONFIG)
    answer = history.answer(TrendQuestion(metric="new", companies=companies))

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
    # answer() nets every line it serves, and a netted line ends on its measured latest value
    for line in [*answer["series"], answer["series_sum"]]:
        assert [v for v in line["net"]["count"] if v is not None][-1] == line["points"][
            -1
        ]


def test_all_openings_carry_no_switch_of_new(tmp_path):
    _write_opened_history(tmp_path)
    answer = TrendHistory.load(tmp_path, _NO_CONFIG).answer(TrendQuestion())
    assert answer["new_inflow_from"] is None
    assert all("new_became_inflow" not in e["fields"] for e in answer["epochs"])
