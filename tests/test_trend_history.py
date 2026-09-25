"""Tests for ``headstart.trend_history`` (ADR-0230 step 3), Trends' one reader of its history.

The Space's ``/trends`` answers are pinned by ``tests/test_space_app.py``; this file pins what
the history must hold for those answers to be right:

- The replay of the Board-delta ledger reproduces the aggregate ledger at every tick. The ticks
  are written by ``ingest.role_trends``' own writers, through an archive, a baseline, a tick where
  nothing moved, a re-base and a tick that carries turnover.
- A tick's Methodology is read from its own file and compared with the tick before it; the
  counting changes from before the first stamped file still come from the epoch ledger.
- An unreadable ledger is an empty history, never a failed boot.
"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

from headstart import roles, trend_history
from headstart.ingest import role_trends
from headstart.trend_history import (
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


_BACKEND = roles.WatchRole("backend", "Backend", "software-engineering", [r"backend"])

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

# (days after _START, series version, the Jobs served, family moves, re-banded Jobs)
_ARCHIVE_TICKS = [(0, 2, "12345", {}, {}), (1, 2, "12345", {}, {})]
_DELTA_TICKS = [
    # the delta ledger's first tick: a baseline
    (2, 2, "12345", {}, {}),
    # the cashier closes, so non-tech falls to 0; a Board is found; a Job re-bands
    (3, 2, "12456", {}, {"2": 2}),
    # nothing moved: an empty file
    (3.5, 2, "12456", {}, {"2": 2}),
    # a new head: a re-base, and a Job moves family
    (4, 3001, "12456", {"4": "ai-ml"}, {"2": 2}),
    # one closes, one opens, and the tick books turnover
    (7, 3001, "24567", {"4": "ai-ml"}, {"2": 2}),
    # Job 6 ages out of `new`
    (12, 3001, "24567", {"4": "ai-ml"}, {"2": 2}),
]


def _write_ticks(state: Path) -> None:
    """The archive, then the Board-delta ledger beside the aggregate, as ``role_trends`` writes
    them: its own counting, delta and ledger functions, one tick at a time."""
    ledger = state / "role_trends.parquet"
    deltas = state / "role_trend_board_deltas"
    previous: dict = {}
    last_version = None
    for days, version, served, moved, rebanded in _ARCHIVE_TICKS + _DELTA_TICKS:
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
        if (days, version, served, moved, rebanded) in _DELTA_TICKS:
            turnover = {}
            if days == 7:
                turnover = {
                    (
                        "workday:big/a",
                        "opened",
                        "data-engineering",
                        "mid",
                        "workday",
                    ): 1,
                    (
                        "greenhouse:acme",
                        "closed",
                        "software-engineering",
                        "mid",
                        "greenhouse",
                    ): 1,
                    ("lever:newco", "unscoped", "all", "all", "lever"): 1,
                }
            role_trends._append_board_deltas(
                deltas,
                previous if version == last_version else {},
                board_counts,
                version,
                ts,
                turnover,
                {"family_classifier_version": version - 3000, "dedup_version": 5},
            )
            previous, last_version = board_counts, version
        role_trends.append_ledger(ledger, counts, non_tech, version, ts)


def _aggregate(state: Path) -> dict[str, dict[tuple[str, str, str, str], int]]:
    """The aggregate ledger's counts at every tick."""
    out: dict[str, dict] = {}
    for row in pq.read_table(state / "role_trends.parquet").to_pylist():
        key = (row["metric"], row["family"], row["band"], row["ats"])
        out.setdefault(row["ts"].isoformat(timespec="seconds"), {})[key] = row["count"]
    return out


def test_the_replay_reproduces_the_aggregate_at_every_tick(tmp_path):
    _write_ticks(tmp_path)
    history = TrendHistory.load(tmp_path, _NO_CONFIG)
    aggregate = _aggregate(tmp_path)
    assert history.ticks == tuple(sorted(aggregate))
    mismatches = [ts for ts in aggregate if history.index_counts(ts) != aggregate[ts]]
    assert mismatches == []
    # the fixture reaches what it means to: a tick with nothing moved, and non-tech at 0
    files = sorted((tmp_path / "role_trend_board_deltas").glob("*.parquet"))
    assert len(files) == len(_DELTA_TICKS)
    assert min(pq.read_table(file).num_rows for file in files) == 0
    assert aggregate[_stamp(3)][("stock", roles.NON_TECH, "all", "all")] == 0


def test_openings_are_each_boards_tech_stock_at_the_live_version(tmp_path):
    _write_ticks(tmp_path)
    openings = TrendHistory.load(tmp_path, _NO_CONFIG).openings()
    # Job 2 (ai-ml) and Job 4, moved to ai-ml by the new head; Jobs 5 and 7; Job 6. The
    # watched Backend role re-counts Job 4 and is no opening of its own.
    assert openings == {"greenhouse:acme": 1, "workday:big/a": 3, "lever:newco": 1}


def _write_tick_file(directory: Path, ts: str, methodology: dict | None) -> None:
    """One tick's file with one Board delta, stamped with ``methodology`` or, as before
    ADR-0230 step 2, with none."""
    metadata = {b"centroid_version": b"3003", b"ts": ts.encode()}
    if methodology is not None:
        metadata[b"methodology"] = json.dumps(methodology).encode()
    row = {
        "ts": ts,
        "board": "greenhouse:acme",
        "metric": "stock",
        "family": "software-engineering",
        "band": "mid",
        "ats": "greenhouse",
        "delta": 1,
    }
    table = pa.Table.from_pylist([row]).replace_schema_metadata(metadata)
    directory.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, directory / f"{ts.replace(':', '-')}.parquet")


def test_a_stamped_tick_marks_what_changed_as_the_epoch_ledger_did(tmp_path):
    """Before the first stamped file the epoch ledger holds the boundaries; after it, each
    tick's Methodology is compared with the one before it, the ledger's last row first."""
    ledger_columns = {
        "centroid_version": "2",
        "family_map_fingerprint": "7681eb07a2b5",
        "tech_filter_version": "5",
        "derivations_version": "15",
        "dedup_version": "5",
        "family_classifier_version": "3b5cc5d9183c",
    }
    methodology = {
        "family_list_fingerprint": "7681eb07a2b5",
        "family_classifier_version": 3,
        "tech_filter_version": 5,
        "derivations_version": 15,
        "dedup_version": 6,
    }
    t0, t1, t2, t3, t4 = (_stamp(days) for days in range(5))
    with (tmp_path / "trends_epochs.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["ts", *ledger_columns])
        writer.writeheader()
        writer.writerow({"ts": t0, **ledger_columns, "dedup_version": "4"})
        writer.writerow({"ts": t1, **ledger_columns})
        # what the pipeline also wrote at the first stamped tick: read from the file instead
        writer.writerow({"ts": t2, **ledger_columns, "centroid_version": "none"})
    directory = tmp_path / "role_trend_board_deltas"
    _write_tick_file(directory, t1, None)
    _write_tick_file(directory, t2, methodology)
    _write_tick_file(directory, t3, methodology)
    _write_tick_file(directory, t4, {**methodology, "tech_filter_version": 6})
    epochs = TrendHistory.load(tmp_path, _NO_CONFIG).answer(TrendQuestion())["epochs"]
    assert epochs == [
        {
            "ts": t1,
            "changed": ["duplicate removal changed"],
            "fields": ["dedup_version"],
        },
        {
            "ts": t2,
            "changed": [
                "role taxonomy refit",
                "duplicate removal changed",
                "role family assignment changed",
            ],
            "fields": [
                "centroid_version",
                "dedup_version",
                "family_classifier_version",
            ],
        },
        {
            "ts": t4,
            "changed": ["tech filter changed"],
            "fields": ["tech_filter_version"],
        },
    ]


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
    """NON_TECH and WATCH_PREFIX have one home, `headstart.roles` (ADR-0230)."""
    assert trend_history.NON_TECH is roles.NON_TECH
    assert trend_history.WATCH_PREFIX is roles.WATCH_PREFIX


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


def test_the_space_and_the_hot_list_leave_out_the_same_runs_and_boards():
    """ADR-0227: the index's turnover (the Space) and Hot (hot_boards) leave out the same
    counting changes, and duplicate removal touches the same Boards, so the two never tell a
    reader different figures for one week. Each keeps its own copy of the rule."""
    from headstart.ingest import hot_boards

    assert set(trend_history._LINE_MOVING) == set(hot_boards._STOCK_MOVING)
    assert set(trend_history._DEDUP_ATSES) == set(hot_boards._DEDUP_SIBLING_ATSES)
    assert trend_history._MIRROR_ATS == hot_boards._DEDUP_MIRROR_ATS
    for boards in (
        ["workday:acme/a", "workday:acme/b"],
        ["workday:acme/a", "workday:other/b"],  # two Tenants: nothing to deduplicate
        ["workday:ACME/a", "workday:acme/b"],  # one Tenant, compared case-blind
        ["workday:acme/a", "greenhouse:acme"],
        ["eightfold:jobs.acme.com"],
        ["taleo_enterprise:acme/1", "taleo_enterprise:acme/2"],
    ):
        assert trend_history._dedup_touched(boards) == bool(
            hot_boards.dedup_touches(boards)
        ), boards
