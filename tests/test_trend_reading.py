"""The Trends line reading (ADR-0232), checked at ``trend_reading.read_answer``.

- **Golden readings** (``tests/fixtures/trend_readings/*.json``): every golden answer of
  ADR-0230 as ``{answer_input, reading}``. Each reads exactly as stored and passes the checker;
  the node tests run the page's ``checkReading`` over the same files, so both state the same
  equalities. After a deliberate rule change, rewrite them with
  ``WRITE_TREND_READINGS=1 pytest tests/test_trend_reading.py`` and read the diff.
- **A change has one size in every window that holds it** (invariant 4), by re-reading each
  golden over every narrower window.
- **The checker catches each broken invariant**, so a passing reading is evidence.
- **A history holding one duplicate removal**, read through ``TrendHistory``.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from headstart.trend_netting import js_round, net_answer
from headstart.trend_reading import (
    GROWTH_COUNTED_TWICE,
    GROWTH_SCALED_BY_A_CHANGE,
    TrendWindow,
    check_reading,
    read_answer,
    read_company_moves,
    read_trends,
)

READINGS = Path(__file__).parent / "fixtures" / "trend_readings"
GOLDEN = sorted(READINGS.glob("*.json"))


def _golden(name: str) -> dict:
    return json.loads((READINGS / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", GOLDEN, ids=[p.stem for p in GOLDEN])
def test_golden_reading_is_what_the_module_reads(path: Path) -> None:
    golden = json.loads(path.read_text(encoding="utf-8"))
    reading = read_answer(golden["answer_input"]).to_json()
    if os.environ.get("WRITE_TREND_READINGS"):
        golden["reading"] = reading
        path.write_text(
            json.dumps(golden, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    assert json.loads(json.dumps(reading)) == golden["reading"]
    assert check_reading(golden["reading"]) == []


@pytest.mark.parametrize("path", GOLDEN, ids=[p.stem for p in GOLDEN])
def test_the_first_row_is_netted_as_net_answer_nets_it(path: Path) -> None:
    """A company's line is netted exactly as before (decision 4): Hot must not move."""
    golden = json.loads(path.read_text(encoding="utf-8"))
    total = golden["reading"]["total"]
    if total is None:
        return
    netted = [
        v
        for v in net_answer(golden["answer_input"])["series_sum"]["net"]["count"]
        if v is not None
    ]
    if len(netted) >= 2:
        assert total["move"]["hiring"] == js_round(netted[-1] - netted[0])


# ---- invariant 4: one size in every window -----------------------------------------------------

_PER_RUN = ("totals", "non_tech")


def _narrowed(answer: dict, first: int) -> dict:
    """``answer`` over its runs from ``first`` on, as ``TrendHistory`` would answer that window."""
    out = copy.deepcopy(answer)
    out["stamps"] = answer["stamps"][first:]
    for key in _PER_RUN:
        if out.get(key):
            out[key] = out[key][first:]
    for line in out["series"]:
        line["points"] = line["points"][first:]
        if line.get("turnover"):
            line["turnover"] = {m: v[first:] for m, v in line["turnover"].items()}
    for key in ("company_totals", "pick_series"):
        out[key] = {k: v[first:] for k, v in (out.get(key) or {}).items()}
    out["pick_parts"] = {
        name: {k: v[first:] for k, v in parts.items()}
        for name, parts in (out.get("pick_parts") or {}).items()
    }
    out["pick_turnover"] = {
        k: {m: v[first:] for m, v in t.items()}
        for k, t in (out.get("pick_turnover") or {}).items()
    }
    return out


def _sizes(reading: dict) -> dict[tuple[str, str], int]:
    """Each (line, change) size the reading gives, the growth a window holds left out."""
    kinds = {c["id"]: c["kind"] for c in reading["marked_changes"]}
    out = {}
    for line in [*reading["lines"], *reading["company_lines"]]:
        for cause in line["move"]["not_hiring"]:
            if kinds.get(cause["change"]) in (
                GROWTH_COUNTED_TWICE,
                GROWTH_SCALED_BY_A_CHANGE,
            ):
                continue
            out[(line["name"], cause["change"])] = cause["size"]
    return out


def _held(reading: dict, stamps: list[str]) -> set[str]:
    """The changes a window holds whole: their own run inside it, after its first run."""
    return {
        c["id"]
        for c in reading["marked_changes"]
        if c["ts"] in stamps and stamps.index(c["ts"]) >= 1
    }


@pytest.mark.parametrize("path", GOLDEN, ids=[p.stem for p in GOLDEN])
def test_a_change_has_one_size_in_every_window_that_holds_it(path: Path) -> None:
    golden = json.loads(path.read_text(encoding="utf-8"))
    answer = golden["answer_input"]
    whole = golden["reading"]
    sizes = _sizes(whole)
    for first in range(1, len(answer["stamps"]) - 1):
        narrow = _narrowed(answer, first)
        reading = read_answer(narrow).to_json()
        assert check_reading(reading) == [], first
        held = _held(reading, narrow["stamps"]) & _held(whole, answer["stamps"])
        for (line, change), size in _sizes(reading).items():
            if change in held and (line, change) in sizes:
                assert size == sizes[(line, change)], (first, line, change)


# ---- what the golden readings read ------------------------------------------------------------


def _company_line(reading: dict, name: str | None = None) -> dict:
    lines = reading["company_lines"]
    return next(line for line in lines if name is None or line["name"] == name)


def _causes(line: dict, reading: dict) -> dict[str, int]:
    kinds = {c["id"]: c["kind"] for c in reading["marked_changes"]}
    out: dict[str, int] = {}
    for cause in line["move"]["not_hiring"]:
        kind = kinds[cause["change"]]
        out[kind] = out.get(kind, 0) + cause["size"]
    return out


def test_a_removal_has_one_size_and_the_growth_it_doubled_is_its_own_cause() -> None:
    """#690's Micron: 1,000 jobs listed twice. The removal is its share of the tech openings
    before it, −1,050 of 2,100; the +100 before it was 50 real hires and 50 counted twice."""
    reading = _golden("duplicate_removal_scales_the_history_before_it")["reading"]
    micron = _company_line(reading)
    assert micron["move"]["hiring"] == 60
    assert _causes(micron, reading) == {
        "duplicates_removed": -1050,
        "growth_counted_twice": 50,
    }


def test_the_marked_changes_sum_to_not_hiring() -> None:
    """Micron's list summed −2,447 under a "Not hiring" of −2,378 (ADR-0232's context)."""
    reading = _golden("change_before_a_removal_counts_at_the_scale_it_leaves")[
        "reading"
    ]
    micron = _company_line(reading)
    listed = sum(
        c["sizes"][micron["name"]]
        for c in reading["marked_changes"]
        if micron["name"] in c["sizes"]
    )
    move = micron["move"]
    assert listed == move["latest"] - move["start"] - move["hiring"]


def test_the_categories_add_up_to_the_company_with_a_closing_row() -> None:
    """Wipro read +444 as a company and +156 summed: its Sep 24 refit moved more jobs out of
    Software Engineering than it held, so the guard scaled that category. The company keeps
    its figure, the category its own, and the closing row says what lies between."""
    reading = _golden("refit_moving_more_than_a_category_held_closes_the_table")[
        "reading"
    ]
    assert reading["total"]["move"]["hiring"] == 60
    hiring = {line["name"]: line["move"]["hiring"] for line in reading["lines"]}
    assert hiring == {"software-engineering": 20, "ai-ml": 0}
    closing = reading["breakdown"]["closing"]
    assert closing["hiring"] == 40
    change = reading["lines"][0]["move"]["not_hiring"]
    assert [c["size"] for c in change] == [-120, 40], (
        "the refit at its size, then the growth"
    )


def test_with_no_pick_nothing_is_taken_out_and_changes_are_still_marked() -> None:
    reading = _golden("index_marks_counting_changes_and_takes_nothing_out")["reading"]
    for line in reading["lines"]:
        assert line["move"]["not_hiring"] == []
        assert line["move"]["hiring"] == line["move"]["latest"] - line["move"]["start"]
    assert [c["kind"] for c in reading["marked_changes"]] == ["counting", "counting"]
    assert reading["day_markers"]


# ---- the checker catches each broken invariant ------------------------------------------------


def _broken(name: str, breaking) -> list[str]:
    reading = copy.deepcopy(_golden(name)["reading"])
    breaking(reading)
    return check_reading(reading)


def test_the_checker_catches_a_line_that_does_not_add_up() -> None:
    def breaking(r):
        r["lines"][0]["move"]["hiring"] += 1

    assert any(
        "latest − start" in v
        for v in _broken("refit_moves_openings_between_categories", breaking)
    )


def test_the_checker_catches_marked_changes_that_are_not_the_company_line() -> None:
    def breaking(r):
        change = r["marked_changes"][0]
        change["sizes"] = {k: v + 1 for k, v in change["sizes"].items()}

    violations = _broken("duplicate_removal_scales_the_history_before_it", breaking)
    assert any("Marked changes" in v for v in violations)


def test_the_checker_catches_rows_that_do_not_reach_the_first_row() -> None:
    def breaking(r):
        r["breakdown"] = {"closing": None}
        r["lines"][0]["move"]["start"] += 1
        r["lines"][0]["move"]["hiring"] += 1

    violations = _broken("refit_moves_openings_between_categories", breaking)
    assert any(v.startswith("breakdown:") for v in violations)


def test_the_checker_catches_a_share_netted_a_second_time() -> None:
    def breaking(r):
        r["company_lines"][0]["move"]["share"]["start"] *= 1.5

    violations = _broken("duplicate_removal_scales_the_history_before_it", breaking)
    assert any("share" in v for v in violations)


def test_the_checker_catches_something_taken_out_with_no_pick() -> None:
    def breaking(r):
        move = r["lines"][0]["move"]
        move["hiring"] -= 5
        move["not_hiring"] = [{"change": "counting@x", "size": 5}]

    violations = _broken("index_marks_counting_changes_and_takes_nothing_out", breaking)
    assert any("no pick" in v for v in violations)


def test_the_checker_catches_a_count_that_is_not_whole() -> None:
    def breaking(r):
        r["company_lines"][0]["move"]["hiring"] += 0.5
        r["company_lines"][0]["move"]["latest"] += 0.5

    violations = _broken("duplicate_removal_scales_the_history_before_it", breaking)
    assert any("whole number" in v for v in violations)


# ---- over a history ---------------------------------------------------------------------------

pa = pytest.importorskip("pyarrow")

import duplicate_removal_trends_state as removal_state

from headstart.trend_history import TrendHistory, TrendQuestion

_NO_CONFIG = Path(__file__).resolve().parent / "no-trends-config"


@pytest.fixture
def history(tmp_path: Path) -> TrendHistory:
    removal_state.write(tmp_path)
    return TrendHistory.load(tmp_path, _NO_CONFIG)


def test_a_removal_is_sized_in_tech_openings_not_the_rows_it_removed(history) -> None:
    """119 rows removed, 10 of them non-tech: the line gives up 109 openings, and none of the
    10 reaches its hiring."""
    reading = read_trends(history, TrendQuestion(companies=(removal_state.MICRO,)))
    assert reading.reconciles, reading.violations
    micro = reading.company_lines[0].move
    causes = {c.change.split("@")[0]: c.size for c in micro.not_hiring}
    assert causes == {"removed": -removal_state.REMOVED_TECH, "growth_counted_twice": 9}
    # 9 runs of +2 before it, halved; +2 on its run; 6 runs of +2 after it.
    assert micro.hiring == 9 + 2 + 12


def test_hot_reads_the_line_its_trend_opens(history) -> None:
    window = TrendWindow(since=removal_state.TICKS[4])
    keys = [removal_state.MICRO, removal_state.BETA]
    moves = read_company_moves(history, window, keys)
    for key in keys:
        reading = read_trends(
            history, TrendQuestion(companies=(key,), since=window.since)
        )
        assert moves[key] == reading.company_lines[0].move


def test_comparable_coverage_reads_its_cohorts_removal_as_all_coverage_does(
    history,
) -> None:
    """Serving no removals under Comparable, Micron read +160 there against +83 under All."""
    question = TrendQuestion(companies=(removal_state.MICRO,))
    comparable = TrendQuestion(companies=(removal_state.MICRO,), coverage="comparable")
    assert (
        read_trends(history, comparable).company_lines[0].move
        == read_trends(history, question).company_lines[0].move
    )
