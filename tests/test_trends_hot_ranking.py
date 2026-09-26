"""The Hot tab's company ranking (ADR-0171, ADR-0230, ADR-0233).

`rank` reads the history through `openings` and `trailing_week`, and each company's own line
through `line_reading.read_company_moves`, so most tests here hand it a fake of all three and
check only what the ranking itself decides: who is a candidate, how each lens sorts, and what is
counted as left out. The last ones rank a real history and hold a row to the trend it opens (the
design's invariant 4)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pytest

old_layout_converter = pytest.importorskip("old_layout_trends_state_converter")
from headstart.trends import hot_ranking, line_reading, trend_history
from headstart.trends.line_reading import CompanyMove, LineMove, Turnover

_CONFIG = Path(__file__).resolve().parents[1] / "config"

_NOW = "2026-09-25T05:15:02+00:00"
_LONG_AGO = "2026-09-13T00:00:00+00:00"
_WINDOW = {
    "base": "2026-09-18T05:10:30+00:00",
    "from": "2026-09-18T06:04:28+00:00",
    "to": _NOW,
    "turnover_from": "2026-09-18T06:04:28+00:00",
}


@dataclass(frozen=True)
class _Move:
    """What Hot reads of a company's line: its hiring (``net``) and turnover, None where it was
    not counted, and what its answer says of the company."""

    net: int = 0
    opened: int | None = 0
    closed: int | None = 0
    counted_since: str = _LONG_AGO
    closures_uncounted_boards: int = 0
    boards_in_scope: int = 1

    def as_company_move(self) -> CompanyMove:
        """As `read_company_moves` gives it."""
        opened, closed = self.opened, self.closed
        turnover = (
            None
            if opened is None
            else Turnover(opened, closed, None if closed is None else opened - closed)
        )
        line = LineMove(
            start=0,
            latest=self.net,
            hiring=self.net,
            not_hiring=(),
            not_hiring_total=0,
            percent=None,
            percent_withheld=None,
            span_days=7.0,
            per_week=self.net,
            turnover=turnover,
        )
        return CompanyMove(
            line,
            self.counted_since,
            self.closures_uncounted_boards,
            self.boards_in_scope,
        )


class _History:
    """`openings` and `trailing_week` as the ranking reads them, and each company's line as
    `read_company_moves` would read it (`_read_fake_company_moves`)."""

    def __init__(
        self, openings: dict[str, int], moves: dict[str, _Move | None]
    ) -> None:
        self._openings = openings
        self.moves = moves
        self.asked: list[str] = []

    def openings(self) -> dict[str, int]:
        return dict(self._openings)

    def trailing_week(self) -> dict[str, str | None]:
        return dict(_WINDOW)


@pytest.fixture(autouse=True)
def _read_fake_company_moves(monkeypatch):
    """A fake history's moves stand in for `read_company_moves`; a real history is read."""
    real = line_reading.read_company_moves

    def read(history, window, keys):
        if not isinstance(history, _History):
            return real(history, window, keys)
        assert window == line_reading.TrendWindow(since=_WINDOW["base"])
        history.asked = list(keys)
        # A company mapped to None had nothing counted in the window, and is left out.
        moves = {key: history.moves.get(key, _Move()) for key in keys}
        return {
            key: move.as_company_move()
            for key, move in moves.items()
            if move is not None
        }

    monkeypatch.setattr(line_reading, "read_company_moves", read)


def _company(name: str, *boards: str, operator: str = "employer") -> dict:
    return {"name": name, "boards": list(boards), "operator": operator}


def _keys(payload: dict, lens: str) -> list[str]:
    return [row["key"] for row in payload["lenses"][lens]]


def test_a_company_is_one_row_over_all_of_its_boards() -> None:
    """Bosch Group ranked on one of its two Boards while its trend summed both (ADR-0230)."""
    directory = {"sr:bosch": _company("Bosch Group", "sr:bosch", "workday:bosch/x")}
    history = _History(
        {"sr:bosch": 1230, "workday:bosch/x": 40}, {"sr:bosch": _Move(net=442)}
    )
    payload = hot_ranking.rank(history, directory)
    (row,) = payload["lenses"]["expansion"]
    assert row["stock"] == 1270, "openings sum over every Board of the company"
    assert row["boards"] == ["sr:bosch", "workday:bosch/x"]
    assert row["atses"] == ["sr", "workday"]
    assert history.asked == ["sr:bosch"], "the history is asked about the company once"


def test_small_companies_are_counted_not_ranked() -> None:
    """One posting on a three-posting company is a 33% rate and pure noise."""
    directory = {"gh:tiny": _company("Tiny", "gh:tiny")}
    history = _History({"gh:tiny": 3}, {"gh:tiny": _Move(net=1, opened=1)})
    payload = hot_ranking.rank(history, directory)
    assert all(not rows for rows in payload["lenses"].values())
    assert payload["counts"]["below_min_stock"] == 1
    assert history.asked == [], "a company below the floor costs no history call"


def test_expansion_separates_growth_from_churn() -> None:
    """Amazon's measured shape: ~1,400 roles opened in a week at a near-zero net change."""
    directory = {
        "a:churner": _company("Churner", "a:churner"),
        "b:grower": _company("Grower", "b:grower"),
    }
    history = _History(
        {"a:churner": 9081, "b:grower": 500},
        {
            "a:churner": _Move(net=-3, opened=1396, closed=1399),
            "b:grower": _Move(net=200, opened=210, closed=10),
        },
    )
    payload = hot_ranking.rank(history, directory)
    assert _keys(payload, "expansion") == ["b:grower"]
    assert _keys(payload, "volume") == ["a:churner", "b:grower"]
    churner = payload["lenses"]["volume"][0]
    assert (churner["opened"], churner["closed"], churner["net"]) == (1396, 1399, -3)


def test_a_company_whose_turnover_was_not_counted_says_so_rather_than_zero() -> None:
    """A company whose runs all fell inside a step of unknown size has no turnover that week: its
    row read "0 opened · 0 closed" as fact, and a 0% rate. It carries None, and ranks under no
    lens that reads opened; a measured 0 is still 0."""
    directory = {
        "a:unmeasured": _company("Unmeasured", "a:unmeasured"),
        "b:measured": _company("Measured", "b:measured"),
    }
    history = _History(
        {"a:unmeasured": 400, "b:measured": 400},
        {
            "a:unmeasured": _Move(net=50, opened=None, closed=None),
            "b:measured": _Move(net=40, opened=0, closed=0),
        },
    )
    payload = hot_ranking.rank(history, directory)
    rows = {row["key"]: row for row in payload["lenses"]["expansion"]}
    assert (rows["a:unmeasured"]["opened"], rows["a:unmeasured"]["closed"]) == (
        None,
        None,
    )
    assert rows["a:unmeasured"]["rate"] is None
    assert (rows["b:measured"]["opened"], rows["b:measured"]["rate"]) == (0, 0)
    assert _keys(payload, "volume") == [] and _keys(payload, "rate") == []


def test_rate_is_the_weeks_openings_as_a_share_of_openings_now() -> None:
    directory = {
        "a:big": _company("Big", "a:big"),
        "b:small": _company("Small", "b:small"),
    }
    history = _History(
        {"a:big": 1000, "b:small": 40},
        {"a:big": _Move(opened=300), "b:small": _Move(opened=30)},
    )
    payload = hot_ranking.rank(history, directory)
    assert _keys(payload, "rate") == ["b:small", "a:big"]
    assert [row["rate"] for row in payload["lenses"]["rate"]] == [75, 30]


def test_a_company_counted_for_under_three_days_is_too_new_to_rank() -> None:
    """SiTime, counted from Sep 23, ranked on Hot while its trend called it too new to read."""
    directory = {
        "adp:sitime": _company("SiTime", "adp:sitime"),
        "gh:older": _company("Older", "gh:older"),
    }
    history = _History(
        {"adp:sitime": 70, "gh:older": 70},
        {
            "adp:sitime": _Move(
                net=59, opened=70, counted_since="2026-09-23T09:00:00+00:00"
            ),
            "gh:older": _Move(
                net=5, opened=6, counted_since="2026-09-22T05:15:02+00:00"
            ),
        },
    )
    payload = hot_ranking.rank(history, directory)
    assert _keys(payload, "expansion") == ["gh:older"], "exactly three days is enough"
    assert payload["counts"]["too_new"] == 1


def test_a_company_with_nothing_counted_in_the_window_is_left_out_not_fatal() -> None:
    """`read_company_moves` leaves out a company with no counted run in the window. Looked up
    as if it were there, one such company raised KeyError and darkened the whole tab; it ranks
    nowhere, as its net of 0 did before, and is counted with the companies too new to rank."""
    directory = {
        "gh:uncounted": _company("Uncounted", "gh:uncounted"),
        "gh:acme": _company("Acme", "gh:acme"),
    }
    history = _History(
        {"gh:uncounted": 300, "gh:acme": 100},
        {"gh:uncounted": None, "gh:acme": _Move(net=10, opened=12)},
    )
    payload = hot_ranking.rank(history, directory)
    assert _keys(payload, "expansion") == ["gh:acme"]
    assert payload["counts"]["ranked"] == 1
    assert payload["counts"]["too_new"] == 1


def test_rows_carry_the_directorys_operator_and_the_counts_say_how_many() -> None:
    directory = {
        "lever:jobgether": _company(
            "Jobgether", "lever:jobgether", operator="aggregator"
        ),
        "sf:wipro": _company("Wipro", "sf:wipro", operator="services"),
        "sr:mindlance": _company("Mindlance", "sr:mindlance", operator="staffing"),
        "gh:acme": _company("Acme", "gh:acme"),
    }
    history = _History(
        {
            "lever:jobgether": 1773,
            "sf:wipro": 2962,
            "sr:mindlance": 350,
            "gh:acme": 400,
        },
        {
            "lever:jobgether": _Move(net=503, opened=900),
            "sf:wipro": _Move(net=213, opened=945),
            "sr:mindlance": _Move(net=79, opened=0),
            "gh:acme": _Move(net=60, opened=40),
        },
    )
    payload = hot_ranking.rank(history, directory)
    labels = {row["key"]: row["operator"] for row in payload["lenses"]["expansion"]}
    assert labels == {
        "lever:jobgether": "aggregator",
        "sf:wipro": "services",
        "sr:mindlance": "staffing",
        "gh:acme": "employer",
    }
    counts = payload["counts"]
    assert (counts["aggregator"], counts["services"], counts["staffing"]) == (1, 1, 1)


def test_a_closed_count_not_counted_stays_none() -> None:
    """Amazon, one Board whose closures went uncounted, read "0 closed" (ADR-0227): a closed
    count a company's line gives as None reaches the row as None, never 0."""
    directory = {"amazon:jobs": _company("Amazon", "amazon:jobs")}
    history = _History(
        {"amazon:jobs": 7896}, {"amazon:jobs": _Move(net=5, opened=66, closed=None)}
    )
    (row,) = hot_ranking.rank(history, directory)["lenses"]["volume"]
    assert (row["opened"], row["closed"]) == (66, None)


def test_a_closed_count_over_some_boards_says_how_many() -> None:
    """A company with some Boards' closures uncounted keeps its closed count, with how many of
    its Boards it leaves out (review of #731)."""
    directory = {"sr:acme": _company("Acme", "sr:acme", "gh:acme")}
    history = _History(
        {"sr:acme": 300, "gh:acme": 100},
        {
            "sr:acme": _Move(
                net=5,
                opened=9,
                closed=3,
                closures_uncounted_boards=1,
                boards_in_scope=2,
            )
        },
    )
    (row,) = hot_ranking.rank(history, directory)["lenses"]["volume"]
    assert (
        row["closed"],
        row["closures_uncounted_boards"],
        row["boards_in_scope"],
    ) == (
        3,
        1,
        2,
    )


def test_a_board_no_directory_entry_holds_is_counted_not_ranked() -> None:
    """ADR-0212: an Oracle pod names nobody, so the directory holds no company for it."""
    history = _History({"oracle:eeho.fa.us2.oraclecloud.com": 400}, {})
    payload = hot_ranking.rank(history, {})
    assert payload["counts"]["unnamed"] == 1
    assert payload["counts"]["ranked"] == 0


def test_each_lens_keeps_its_top_n_and_breaks_ties_on_the_key() -> None:
    directory = {
        f"gh:c{n:03}": _company(f"C{n}", f"gh:c{n:03}")
        for n in range(hot_ranking.TOP_N + 5)
    }
    history = _History(
        {key: 100 for key in directory},
        {key: _Move(net=10, opened=10) for key in directory},
    )
    payload = hot_ranking.rank(history, directory)
    assert _keys(payload, "expansion") == sorted(directory)[: hot_ranking.TOP_N]


def test_the_window_is_the_historys_so_a_row_links_to_its_own_base() -> None:
    directory = {"gh:acme": _company("Acme", "gh:acme")}
    history = _History({"gh:acme": 100}, {"gh:acme": _Move(net=10, opened=12)})
    assert hot_ranking.rank(history, directory)["window"] == _WINDOW


def test_no_measured_window_ranks_nothing() -> None:
    """A history with no tick yet has no week to rank over, and the tab stays dark."""

    class _Empty(_History):
        def trailing_week(self):
            return {"base": None, "from": None, "to": None, "turnover_from": None}

    empty = _Empty({}, {})
    assert hot_ranking.rank(empty, {}) == {}
    assert empty.asked == [], "no company is read"


# ---- over a real history ------------------------------------------------------------------------

_START = datetime.fromisoformat("2026-09-10T00:00:00+00:00")
# Twenty-one ticks, twelve hours apart: Sep 10 00:00 to Sep 20 00:00.
_TICKS = [(_START + timedelta(hours=12 * k)).isoformat() for k in range(21)]
_FILTER_CHANGE = "2026-09-15T00:00:00+00:00"
_ACME_FOUND = "2026-09-16T00:00:00+00:00"


def _write_history(state: Path, with_turnover: bool = True) -> None:
    """Three companies whose raw change and hiring differ, one way each.

    Acme hires 2 a tick and finds a second Board (40 openings) mid-week, a step that is not
    hiring. Beta hires 1 a tick and is moved by a tech-filter change, +30 and then −10 on the run
    after. Young is counted for under a day."""
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    deltas = state / "role_trend_board_deltas"
    deltas.mkdir(parents=True)
    for k, ts in enumerate(_TICKS):
        rows = [
            ("greenhouse:acme", "stock", 100 if k == 0 else 2),
            ("greenhouse:beta", "stock", 60 if k == 0 else 1),
        ]
        if k and with_turnover:
            rows += [("greenhouse:acme", "opened", 3), ("greenhouse:acme", "closed", 1)]
        if ts == _FILTER_CHANGE:
            rows.append(("greenhouse:beta", "stock", 30))
        if ts == _TICKS[_TICKS.index(_FILTER_CHANGE) + 1]:
            rows.append(("greenhouse:beta", "stock", -10))
        if ts == _ACME_FOUND:
            rows.append(("lever:acme", "stock", 40))
        if ts == _TICKS[-2]:
            rows.append(("greenhouse:young", "stock", 50))
        table = pa.table(
            {
                "ts": [ts] * len(rows),
                "board": [board for board, _, _ in rows],
                "metric": [metric for _, metric, _ in rows],
                "family": ["software-engineering"] * len(rows),
                "band": ["mid"] * len(rows),
                "ats": [board.split(":", 1)[0] for board, _, _ in rows],
                "delta": [delta for _, _, delta in rows],
            }
        ).replace_schema_metadata({"centroid_version": "3001"})
        pq.write_table(table, deltas / f"{ts.replace(':', '-')}.parquet")
    header = "ts,centroid_version,family_map_fingerprint,tech_filter_version,"
    header += "derivations_version,dedup_version,family_classifier_version\n"
    (state / "trends_epochs.csv").write_text(
        header + f"{_TICKS[0]},none,f,1,1,1,h\n" + f"{_FILTER_CHANGE},none,f,2,1,1,h\n",
        encoding="utf-8",
    )
    companies = [
        {"name": "Acme", "boards": ["greenhouse:acme", "lever:acme"]},
        {"name": "Beta", "boards": ["greenhouse:beta"]},
        {"name": "Young", "boards": ["greenhouse:young"]},
    ]
    for entry in companies:
        entry["operator"] = "employer"
    (state / "company_directory.json").write_text(
        json.dumps({"companies": companies}), encoding="utf-8"
    )


def _trend_hiring(history, key: str, since: str) -> int:
    """The hiring the trend a Hot row's "See trend" opens reads: its company line's."""
    reading = line_reading.read_trends(
        history, trend_history.TrendQuestion(companies=(key,), since=since)
    )
    return reading.company_lines[0].move.hiring


def test_every_rows_net_is_the_hiring_of_the_trend_it_opens(tmp_path: Path) -> None:
    """The design's invariant 4 (ADR-0230, ADR-0233): Hot is read by the same code as the trend
    it opens, so the two agree by construction. Before it, Hot read Google −27 against its
    trend's −42, and Bosch Group +440 from one of the two Boards its trend summed."""
    _write_history(tmp_path)
    history = trend_history.TrendHistory.load(
        old_layout_converter.store_in_current_layout(tmp_path), _CONFIG
    )
    payload = hot_ranking.rank(history, history.companies)
    base = payload["window"]["base"]
    rows = [row for lens in payload["lenses"].values() for row in lens]
    assert {row["key"] for row in rows} == {"greenhouse:acme", "greenhouse:beta"}
    for row in rows:
        # As the row's link opens it: `since` cut to UTC minutes (app.js openCompanyTrend).
        assert row["net"] == _trend_hiring(history, row["key"], base[:16]), row["key"]


def test_a_rows_net_leaves_out_what_its_trend_leaves_out(tmp_path: Path) -> None:
    """Not a tautology of the test above: the raw change holds a found Board and a counting
    change, and the row holds neither."""
    _write_history(tmp_path)
    history = trend_history.TrendHistory.load(
        old_layout_converter.store_in_current_layout(tmp_path), _CONFIG
    )
    payload = hot_ranking.rank(history, history.companies)
    assert payload["window"]["base"] == "2026-09-12T12:00:00+00:00"
    net = {row["key"]: row["net"] for row in payload["lenses"]["expansion"]}
    # Fifteen runs after the base, less the counting change's run and the run after it, which
    # every line leaves out.
    hiring_runs = 15 - 2
    assert net == {
        "greenhouse:acme": 2 * hiring_runs,
        "greenhouse:beta": hiring_runs,
    }, "raw, Acme moved +70 with its found Board and Beta +35 with the filter change"
    acme = next(r for r in payload["lenses"]["volume"] if r["key"] == "greenhouse:acme")
    assert (acme["opened"], acme["closed"]) == (3 * hiring_runs, hiring_runs)
    assert payload["counts"]["too_new"] == 1, "Young, counted for half a day"


def test_before_turnover_is_counted_a_row_carries_none_not_zero(tmp_path: Path) -> None:
    """With no run counting turnover, Hot read the missing figures as 0, and every Growing row
    said "0 opened · 0 closed this week" beside its net."""
    _write_history(tmp_path, with_turnover=False)
    history = trend_history.TrendHistory.load(
        old_layout_converter.store_in_current_layout(tmp_path), _CONFIG
    )
    payload = hot_ranking.rank(history, history.companies)
    assert payload["window"]["turnover_from"] is None
    rows = payload["lenses"]["expansion"]
    assert rows and all(row["opened"] is None and row["closed"] is None for row in rows)
