"""The Trends line reading (ADR-0233), checked at ``line_reading.read_answer``.

- **Golden readings** (``tests/fixtures/trend_readings/*.json``): every golden answer of
  ADR-0230, and the answers the page's node tests draw, as ``{answer_input, reading}``. Each
  reads exactly as stored and passes the checker; the node tests run the page's ``checkReading``
  over the same files and draw each one, so both state the same equalities. After a deliberate
  rule change, rewrite them with ``WRITE_TREND_READINGS=1 pytest tests/test_trends_line_reading.py``
  and read the diff.
- **A change has one size in every window that holds it** (invariant 4), by re-reading each
  golden over every window narrowed from either end.
- **The checker catches each broken invariant**, so a passing reading is evidence.
- **A history holding one duplicate removal**, read through ``TrendHistory``.
"""

from __future__ import annotations

import copy
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from headstart.trends.line_reading import (
    _FIELD_ID,
    _OTHER,
    INDEX_BASE_FLOOR,
    LINES_CHARTED,
    MIN_SPAN_DAYS,
    MOSTLY_RECOUNTED,
    MOVER_FLOOR,
    CauseKind,
    TrendWindow,
    check_reading,
    read_answer,
    read_company_moves,
    read_trends,
    trends_payload,
    unread_trends_payload,
)
from headstart.trends.netting import js_round, net_answer

READINGS = Path(__file__).parent / "fixtures" / "trend_readings"
GOLDEN = sorted(READINGS.glob("*.json"))
_GROWTH = (CauseKind.GROWTH_COUNTED_TWICE, CauseKind.GROWTH_SCALED_BY_A_CHANGE)


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


def _netted_move(line: dict) -> int | None:
    netted = [v for v in line["net"]["count"] if v is not None]
    return js_round(netted[-1] - netted[0]) if len(netted) >= 2 else None


@pytest.mark.parametrize("path", GOLDEN, ids=[p.stem for p in GOLDEN])
def test_the_first_row_and_each_category_read_their_netted_figure(path: Path) -> None:
    """A company's line is netted exactly as before (decision 4): Hot must not move. A category
    keeps its own netted figure too, where it was counted all through the window; the rows are
    rounded together, so in general a row is its netted figure rounded down or up."""
    golden = json.loads(path.read_text(encoding="utf-8"))
    reading = golden["reading"]
    served = net_answer(golden["answer_input"])
    if reading["total"] is not None and _netted_move(served["series_sum"]) is not None:
        assert reading["total"]["move"]["hiring"] == _netted_move(served["series_sum"])
    rows = {line["name"]: line["move"]["hiring"] for line in reading["lines"]}
    for line in served["series"]:
        whole_window = line["points"][0] is not None and line["points"][-1] is not None
        if whole_window and _netted_move(line) is not None and line["name"] in rows:
            assert rows[line["name"]] == _netted_move(line), line["name"]


@pytest.mark.parametrize("path", GOLDEN, ids=[p.stem for p in GOLDEN])
def test_each_line_is_drawn_as_it_is_netted(path: Path) -> None:
    """The Change plot's line is the line netted as its figures are, and a line drawn in counts
    breaks where a step lands: what ``net_answer`` served as ``net.count`` and ``jumps``."""
    golden = json.loads(path.read_text(encoding="utf-8"))
    reading = golden["reading"]
    served = net_answer(golden["answer_input"])
    lines = {line["name"]: line for line in reading["lines"]}
    for line in served["series"]:
        if line["name"] not in lines:
            continue
        drawn = lines[line["name"]]
        assert drawn["netted"] == [
            None if v is None else round(v, 2) for v in line["net"]["count"]
        ], line["name"]
        assert drawn["steps_at"] == [j["i"] for j in line["jumps"]], line["name"]
    if reading["total"] is not None:
        total = served["series_sum"]
        assert reading["total"]["points"] == total["points"]
        assert reading["total"]["netted"] == [
            None if v is None else round(v, 2) for v in total["net"]["count"]
        ]


@pytest.mark.parametrize("path", GOLDEN, ids=[p.stem for p in GOLDEN])
def test_other_is_the_lines_past_the_charted_ones(path: Path) -> None:
    reading = json.loads(path.read_text(encoding="utf-8"))["reading"]
    folded = reading["lines"][LINES_CHARTED:]
    assert (reading["other"] is None) == (not folded)
    if folded:
        for k in ("start", "latest", "hiring", "not_hiring_total"):
            assert reading["other"]["move"][k] == sum(f["move"][k] for f in folded)


# ---- the payload the Space serves ---------------------------------------------------------------


def test_the_payload_is_the_answer_as_drawn_with_its_reading() -> None:
    """A partial read is dropped from the points the page draws, and counted; the pieces only the
    reading nets stay off the wire (ADR-0233 decision 7)."""
    answer = _golden("partial_read_put_straight_back")["answer_input"]
    payload, reading = trends_payload(answer)
    points = {line["name"]: line["points"] for line in payload["series"]}
    assert points["a"] == [26, None, 26, 27]
    assert points["b"] == [100, 110, 120, 130], "steady growth is kept"
    assert payload["partial"] == 1
    assert payload["reading"] == reading.to_json() == read_answer(answer).to_json()
    for piece in (
        "pick_series",
        "pick_parts",
        "pick_turnover",
        "evicted",
        "discovered",
        "epochs",
    ):
        assert piece not in payload, piece
    assert not any("turnover" in line for line in payload["series"])
    assert answer["series"][0]["points"][1] == 104, (
        "the answer passed in is not changed"
    )


def test_an_answer_whose_reading_failed_is_served_with_no_reading_and_why() -> None:
    answer = _golden("busy_company_turnover_leaves_out_a_filter_change")["answer_input"]
    payload = unread_trends_payload(answer, "ValueError: no")
    assert payload["reading"] is None
    assert payload["reading_error"] == "ValueError: no"
    assert "epochs" not in payload
    assert not any("turnover" in line for line in payload["series"])


def test_no_label_is_a_raw_field_id() -> None:
    """The echo of a change the window does not hold read "tech_filter_version", dated at the
    change. It reads as the echo it is, at its own run."""
    reading = _golden("new_echo_of_a_change_before_the_window")["reading"]
    [echo] = reading["marked_changes"]
    assert echo["label"] == "the week-later echo of the Sep 11 tech-job filter update"
    assert echo["ts"] == "2026-09-19T00:00:00+00:00"
    for path in GOLDEN:
        reading = json.loads(path.read_text(encoding="utf-8"))["reading"]
        for change in reading["marked_changes"]:
            assert "_" not in change["label"], (path.stem, change["label"])


# ---- invariant 4: one size in every window -----------------------------------------------------

_PER_RUN = ("totals", "non_tech")


def _narrowed(answer: dict, first: int, end: int) -> dict:
    """``answer`` over its runs ``[first, end)``, as ``TrendHistory`` would answer that window."""
    cut = slice(first, end)
    out = copy.deepcopy(answer)
    out["stamps"] = answer["stamps"][cut]
    for key in _PER_RUN:
        if out.get(key):
            out[key] = out[key][cut]
    for line in out["series"]:
        line["points"] = line["points"][cut]
        if line.get("turnover"):
            line["turnover"] = {m: v[cut] for m, v in line["turnover"].items()}
    for key in ("company_totals", "pick_series"):
        out[key] = {k: v[cut] for k, v in (out.get(key) or {}).items()}
    out["pick_parts"] = {
        name: {k: v[cut] for k, v in parts.items()}
        for name, parts in (out.get("pick_parts") or {}).items()
    }
    out["pick_turnover"] = {
        k: {m: v[cut] for m, v in t.items()}
        for k, t in (out.get("pick_turnover") or {}).items()
    }
    return out


def _runs(change: dict, answer: dict) -> set[str] | None:
    """The runs a change lands on in the full window: its own, a counting change's settling run
    and, under New, a tech-filter change's week-later echo. None for a change whose own run is
    outside the window, which no narrower window holds whole."""
    stamps = answer["stamps"]
    if change["ts"] not in stamps:
        return None
    i = stamps.index(change["ts"])
    runs = {change["ts"]}
    if change["kind"] == CauseKind.COUNTING:
        runs |= set(stamps[i + 1 : i + 2])
        if answer["metric"] == "new" and "tech_filter_version" in change["fields"]:
            echo = datetime.fromisoformat(change["ts"]) + timedelta(
                days=answer["new_window_days"]
            )
            runs |= {
                next(
                    (ts for ts in stamps if datetime.fromisoformat(ts) >= echo),
                    change["ts"],
                )
            }
    return runs


def _sizes(reading: dict, with_growth: bool) -> dict[tuple[str, str], int]:
    out = {}
    for line in [*reading["lines"], *reading["company_lines"]]:
        for cause in line["move"]["not_hiring"]:
            if with_growth or cause["kind"] not in _GROWTH:
                out[(line["name"], cause["change"])] = cause["size"]
    return out


def _windows(n: int):
    """Every window narrowed from one end or the other: ``[first, n)`` and ``[0, end)``."""
    yield from ((first, n) for first in range(1, n - 1))
    yield from ((0, end) for end in range(2, n))


@pytest.mark.parametrize("path", GOLDEN, ids=[p.stem for p in GOLDEN])
def test_a_change_has_one_size_in_every_window_that_holds_it(path: Path) -> None:
    """Narrowed from either end, a window that holds a change's runs reads it at one size. The
    growth a scaling took out is sized by the growth the window holds before it, so it is
    compared only where the window cuts runs after every change and keeps all of that growth."""
    golden = json.loads(path.read_text(encoding="utf-8"))
    answer = golden["answer_input"]
    whole = golden["reading"]
    runs = {c["id"]: _runs(c, answer) for c in whole["marked_changes"]}
    for first, end in _windows(len(answer["stamps"])):
        narrow = _narrowed(answer, first, end)
        stamps = narrow["stamps"]
        reading = read_answer(narrow).to_json()
        assert check_reading(reading) == [], (first, end)
        held = {
            change
            for change, landing in runs.items()
            if landing is not None
            and landing <= set(stamps)
            and stamps.index(whole_ts(whole, change)) >= 1
        }
        every_change_held = held == {c for c, landing in runs.items() if landing}
        with_growth = first == 0 and every_change_held
        sizes = _sizes(whole, with_growth)
        for (line, change), size in _sizes(reading, with_growth).items():
            if (change in held or with_growth) and (line, change) in sizes:
                assert size == sizes[(line, change)], (first, end, line, change)


def whole_ts(reading: dict, change: str) -> str:
    return next(c["ts"] for c in reading["marked_changes"] if c["id"] == change)


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
    """Micron's list summed −2,447 under a "Not hiring" of −2,378 (ADR-0233's context)."""
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
    assert (closing["start"], closing["latest"], closing["hiring"]) == (0, 0, 40)
    assert [(c["kind"], c["size"]) for c in closing["not_hiring"]] == [
        ("counting", -40)
    ]
    assert closing["turnover"] is None
    change = reading["lines"][0]["move"]["not_hiring"]
    assert [c["size"] for c in change] == [-120, 40], (
        "the refit at its size, then the growth"
    )
    assert [c["label"] for c in change] == [
        "job categories re-sorted",
        "growth rescaled when job categories re-sorted",
    ]


def test_growth_rescaled_by_a_change_stands_on_that_changes_day() -> None:
    """The erase guard's growth is a Marked change of its own, marked with its change."""
    reading = _golden("whole_company_shift_that_would_erase_its_history_scales")[
        "reading"
    ]
    kinds = {c["id"]: c["kind"] for c in reading["marked_changes"]}
    assert sorted(kinds.values()) == ["counting", "growth_scaled_by_a_change"]
    assert [sorted(d["changes"]) for d in reading["day_markers"]] == [sorted(kinds)]


def test_no_percentage_off_a_netted_start_under_five_openings() -> None:
    """NVIDIA's Internships read "+350%" as a tile's Biggest riser: 14 hired off a netted 4."""
    reading = _golden("percentage_withheld_off_a_netted_start_under_five")["reading"]
    interns = next(line for line in reading["lines"] if line["name"] == "interns")[
        "move"
    ]
    assert (interns["hiring"], interns["latest"] - interns["hiring"]) == (14, 4)
    assert interns["percent"] is None
    assert interns["percent_withheld"] == MOSTLY_RECOUNTED, (
        "56 of its 60 were taken out"
    )
    lines = {line["name"]: line for line in reading["lines"]}
    assert lines["interns"]["index_base"] is None, "nor is it indexed off 4"
    assert lines["big"]["index_base"] == 100
    # Nor is a share's change read off it: 0.4% becoming 1.8% is the same arithmetic.
    assert interns["share"]["percent"] is None


def test_a_line_mostly_recounted_gives_no_percentage_in_any_unit() -> None:
    """NVIDIA's Embedded & Firmware started at 2,706 and netted to 96, and read "Biggest faller
    −14.6%": a netted start well over INDEX_BASE_FLOOR, but what is left after the steps took
    more than half. Here a refit takes 150 of 200 and 10 are hired: no +20%, under Change,
    Count or Share, and no index line. A line the steps left more than half of keeps its own."""
    reading = _golden("category_mostly_recounted_gives_no_percentage")["reading"]
    lines = {line["name"]: line for line in reading["lines"]}
    embedded = lines["embedded"]["move"]
    assert (embedded["start"], embedded["latest"] - embedded["hiring"]) == (200, 50)
    assert embedded["hiring"] == 10
    assert embedded["percent"] is None
    assert embedded["percent_withheld"] == MOSTLY_RECOUNTED
    assert embedded["share"]["percent"] is None
    assert lines["embedded"]["index_base"] is None
    big = lines["big"]["move"]
    assert big["percent"] == pytest.approx(10.0)
    assert big["share"]["percent"] is not None
    assert lines["big"]["index_base"] == 100
    # 7 of 12 taken, 5 left: at the index floor, yet mostly re-counted, so not indexed; and too
    # small for a percentage, which stays its reason.
    tiny = lines["tiny"]
    assert tiny["index_base"] is None
    assert (
        tiny["move"]["percent_withheld"] == f"under {MOVER_FLOOR} openings at the start"
    )


def test_no_share_change_off_a_share_of_zero_at_the_start() -> None:
    reading = _golden("share_change_withheld_off_a_share_of_zero_at_the_start")[
        "reading"
    ]
    interns = next(line for line in reading["lines"] if line["name"] == "interns")
    assert interns["move"]["share"]["start"] == 0
    assert interns["move"]["share"]["percent"] is None
    assert interns["move"]["percent"] is None
    assert interns["index_base"] is None


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


def test_the_checker_catches_a_closing_row_that_is_not_one_figure() -> None:
    def breaking(r):
        closing = r["breakdown"]["closing"]
        closing["not_hiring"][0]["kind"] = "growth_scaled_by_a_change"

    violations = _broken(
        "refit_moving_more_than_a_category_held_closes_the_table", breaking
    )
    assert any(v.startswith("closing row:") for v in violations)


def test_the_checker_catches_a_change_no_day_marker_names() -> None:
    def breaking(r):
        r["day_markers"] = r["day_markers"][1:]

    violations = _broken("duplicate_removal_scales_the_history_before_it", breaking)
    assert any("named by 0 day markers" in v for v in violations)


def test_the_checker_catches_a_share_netted_a_second_time() -> None:
    def breaking(r):
        r["company_lines"][0]["move"]["share"]["start"] *= 1.5

    violations = _broken("duplicate_removal_scales_the_history_before_it", breaking)
    assert any("share" in v for v in violations)


def test_the_checker_catches_something_taken_out_with_no_pick() -> None:
    def breaking(r):
        move = r["lines"][0]["move"]
        move["hiring"] -= 5
        move["not_hiring"] = [
            {"change": "counting@x", "kind": "counting", "label": "x", "size": 5}
        ]

    violations = _broken("index_marks_counting_changes_and_takes_nothing_out", breaking)
    assert any("no pick" in v for v in violations)


def test_the_checker_catches_a_not_hiring_total_that_is_not_its_causes() -> None:
    def breaking(r):
        r["company_lines"][0]["move"]["not_hiring_total"] += 1

    violations = _broken("duplicate_removal_scales_the_history_before_it", breaking)
    assert any("its causes sum to" in v for v in violations)


def test_the_checker_catches_a_weekly_rate_that_is_not_the_hiring_over_its_days() -> (
    None
):
    def breaking(r):
        r["company_lines"][0]["move"]["per_week"] += 1

    violations = _broken("duplicate_removal_scales_the_history_before_it", breaking)
    assert any("weekly rate" in v for v in violations)


def test_the_checker_catches_a_share_change_not_read_off_the_shares() -> None:
    def breaking(r):
        r["company_lines"][0]["move"]["share"]["percent"] += 1

    violations = _broken("duplicate_removal_scales_the_history_before_it", breaking)
    assert any("share's change" in v for v in violations)


def test_the_checker_catches_a_turnover_net_that_is_not_opened_less_closed() -> None:
    def breaking(r):
        r["lines"][0]["move"]["turnover"]["net"] += 1

    violations = _broken("busy_company_turnover_leaves_out_a_filter_change", breaking)
    assert any("turnover's net" in v for v in violations)


def test_the_checker_catches_an_other_row_that_is_not_its_lines_added_together() -> (
    None
):
    def breaking(r):
        r["other"]["move"]["hiring"] += 1
        r["other"]["move"]["latest"] += 1

    violations = _broken("index_folds_the_categories_past_eight_into_other", breaking)
    assert any(v.startswith("other row: its hiring") for v in violations)

    def dropping(r):
        r["other"] = None

    assert _broken("index_folds_the_categories_past_eight_into_other", dropping) == [
        f"other row: missing with 2 lines past the first {LINES_CHARTED}"
    ]


def test_the_checker_catches_a_percentage_off_a_netted_start_under_five() -> None:
    def breaking(r):
        interns = next(line for line in r["lines"] if line["name"] == "interns")
        interns["move"]["percent"] = 350.0

    violations = _broken("percentage_withheld_off_a_netted_start_under_five", breaking)
    assert (
        "line interns: its percentage is not hiring over the netted start" in violations
    )


def test_the_checker_catches_a_percentage_off_a_line_mostly_recounted() -> None:
    def breaking(r):
        embedded = next(line for line in r["lines"] if line["name"] == "embedded")
        move = embedded["move"]
        move["percent"] = move["hiring"] / (move["latest"] - move["hiring"]) * 100
        move["share"]["percent"] = 5.0
        embedded["index_base"] = embedded["netted"][0]
        big = next(line for line in r["lines"] if line["name"] == "big")["move"]
        big["percent_withheld"] = MOSTLY_RECOUNTED

    violations = _broken("category_mostly_recounted_gives_no_percentage", breaking)
    assert sorted(violations) == [
        "line big: it is said to be mostly re-counted where it is not",
        "line embedded: it gives a percentage though mostly re-counted",
        "line embedded: it is indexed though mostly re-counted",
        "line embedded: its share's change is not its latest share over its start",
    ]


def test_a_whole_companys_line_is_never_mostly_recounted() -> None:
    """The owner's call on #731 (ADR-0238): a company's own line keeps its percentage, since the
    netting keeps it sound and Hot ranks by it. Micron's change took 1,867 of 1,887 and left
    20, which on a category would withhold it."""
    reading = _golden("micron_eightfold_only_company_steps_at_duplicate_removal")[
        "reading"
    ]
    micron = reading["lines"][0]
    assert micron["whole_company"]
    assert (micron["move"]["start"], micron["move"]["latest"]) == (1887, 20)
    assert micron["move"]["percent_withheld"] is None
    assert micron["move"]["percent"] == 0.0
    assert micron["index_base"] == 20
    categories = _golden("category_mostly_recounted_gives_no_percentage")["reading"]
    assert not any(line["whole_company"] for line in categories["lines"])
    assert categories["total"]["whole_company"]


def test_the_checker_catches_a_company_line_said_to_be_mostly_recounted() -> None:
    def breaking(r):
        move = r["lines"][0]["move"]
        move["percent"], move["percent_withheld"] = None, MOSTLY_RECOUNTED

    violations = _broken(
        "micron_eightfold_only_company_steps_at_duplicate_removal", breaking
    )
    assert (
        "line eightfold:careers.micron.com: it is said to be mostly re-counted where it is not"
        in violations
    )


def test_the_checker_catches_causes_out_of_the_order_their_changes_ran() -> None:
    def breaking(r):
        r["company_lines"][0]["move"]["not_hiring"].reverse()

    violations = _broken("three_counting_changes_named_once_each", breaking)
    assert violations == [
        "company line greenhouse:acme: its Not hiring is not in the order its changes ran"
    ]


def test_no_closed_count_where_every_board_had_its_closures_go_uncounted() -> None:
    """Google, one Board, read "about 1 opened, 18 closed since Sep 25, closures not counted on
    1 board". Where every Board of a line had a run whose closures went uncounted
    (``closures_uncounted``), the line gives no closed count and no net; a company with a Board
    still counted keeps both."""
    answer = _golden("busy_company_step_disclosed_turnover_kept")["answer_input"]
    uncounted = read_answer({**answer, "closures_uncounted": ["greenhouse:acme"]})
    assert uncounted.reconciles, uncounted.violations
    turnover = uncounted.company_lines[0].move.turnover
    assert (turnover.opened, turnover.closed, turnover.net) == (500, None, None)
    counted = read_answer({**answer, "closures_uncounted": []})
    assert counted.company_lines[0].move.turnover.closed == 490


def test_a_lines_causes_stand_in_the_order_their_changes_ran() -> None:
    """Micron's "Not hiring" gave Sep 24's growth counted twice after Sep 25's change: its
    causes stood in the order the netting met them, not the order they ran."""
    for path in GOLDEN:
        answer = json.loads(path.read_text(encoding="utf-8"))["answer_input"]
        reading = read_answer(answer).to_json()
        ran = {c["id"]: c["ts"] for c in reading.get("marked_changes") or []}
        for line in reading.get("company_lines") or []:
            order = [ran[c["change"]] for c in line["move"]["not_hiring"]]
            assert order == sorted(order), (path.stem, line["name"])


def test_the_checker_catches_a_net_given_without_its_closed_count() -> None:
    def breaking(r):
        r["company_lines"][0]["move"]["turnover"]["closed"] = None

    violations = _broken("busy_company_step_disclosed_turnover_kept", breaking)
    assert any("its turnover's net is not opened less closed" in v for v in violations)


def test_the_checker_catches_a_raw_field_id_as_a_label() -> None:
    def breaking(r):
        r["marked_changes"][0]["label"] = "tech_filter_version"
        r["company_lines"][0]["move"]["not_hiring"][0]["label"] = "tech_filter_version"

    violations = _broken("micron_filter_change_sized_with_its_settling_run", breaking)
    assert violations == ["label 'tech_filter_version': it is a field id, not words"]


def test_the_checker_catches_an_index_base_under_the_floor() -> None:
    def breaking(r):
        interns = next(line for line in r["lines"] if line["name"] == "interns")
        interns["index_base"] = interns["netted"][-1] = interns["netted"][0] = 4

    violations = _broken("percentage_withheld_off_a_netted_start_under_five", breaking)
    assert "line interns: its index base is not a first netted count of 5 or more" in (
        violations
    )


def test_the_checker_catches_openings_that_are_not_the_lines_latest() -> None:
    def breaking(r):
        r["openings"] += 1
        r["non_tech_jobs"] += 1

    violations = _broken("duplicate_removal_scales_the_history_before_it", breaking)
    assert violations == [
        "openings: not every line's latest added together",
        "non-tech jobs: not the served jobs less the openings",
    ]


def test_the_checker_catches_a_count_that_is_not_whole() -> None:
    def breaking(r):
        r["company_lines"][0]["move"]["hiring"] += 0.5
        r["company_lines"][0]["move"]["latest"] += 0.5

    violations = _broken("duplicate_removal_scales_the_history_before_it", breaking)
    assert any("whole number" in v for v in violations)


# ---- the page states the same rules -----------------------------------------------------------

APP_JS = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "headstart"
    / "ui"
    / "static"
    / "app.js"
)


def _js_constant(name: str) -> str:
    match = re.search(
        rf"^const {name} = (.+?);", APP_JS.read_text(encoding="utf-8"), re.MULTILINE
    )
    assert match, f"app.js declares no {name}"
    return match.group(1)


def test_the_page_states_the_readings_rules_with_the_same_values() -> None:
    """The page decides its words and its checker off these; were one side to move alone, a
    line the reading withholds a percentage for would read one on the page, or the checker
    would refuse a reading the Space serves. Parity, like `test_role_watchlist_config.py`'s,
    rather than a rules object on every reading: the values are code, not data."""
    assert int(_js_constant("MOVER_FLOOR").split()[0]) == MOVER_FLOOR
    assert int(_js_constant("MIN_SPAN_DAYS").split()[0]) == MIN_SPAN_DAYS
    assert int(_js_constant("INDEX_BASE_FLOOR").split()[0]) == INDEX_BASE_FLOOR
    assert int(_js_constant("CHART_MAX").split()[0]) == LINES_CHARTED
    assert _js_constant("MOSTLY_RECOUNTED") == f"'{MOSTLY_RECOUNTED}'"
    counting = set(re.findall(r"'([a-z_]+)'", _js_constant("COUNTING_KINDS")))
    assert counting == {CauseKind.COUNTING, CauseKind.GROWTH_SCALED_BY_A_CHANGE}
    assert f"'{_OTHER}'" in APP_JS.read_text(encoding="utf-8")
    assert _js_constant("FIELD_ID") == f"/{_FIELD_ID.pattern}/"


# ---- over a history ---------------------------------------------------------------------------

pa = pytest.importorskip("pyarrow")

import duplicate_removal_trends_state as removal_state

from headstart.trends.trend_history import TrendHistory, TrendQuestion

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
    causes = {c.kind: c.size for c in micro.not_hiring}
    assert causes == {
        CauseKind.DUPLICATES_REMOVED: -removal_state.REMOVED_TECH,
        CauseKind.GROWTH_COUNTED_TWICE: 9,
    }
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


def test_comparable_coverage_takes_its_cohorts_removal_out_and_not_a_later_boards(
    tmp_path: Path,
) -> None:
    """Serving no removals under Comparable, Micron read +160 there against +83 under All.
    Under Comparable the cohort Board's removal comes out; the later Board is not in the
    cohort, and neither is its removal."""
    removal_state.write(tmp_path, board_found_later=True)
    history = TrendHistory.load(tmp_path, _NO_CONFIG)
    comparable = TrendQuestion(companies=(removal_state.MICRO,), coverage="comparable")
    reading = read_trends(history, comparable)
    assert reading.reconciles, reading.violations
    removed = {
        c.change: c.size
        for c in reading.company_lines[0].move.not_hiring
        if c.kind == CauseKind.DUPLICATES_REMOVED
    }
    at = removal_state.TICKS[removal_state.REMOVAL]
    assert removed == {
        f"removed@{at}/{removal_state.MICRO}": -removal_state.REMOVED_TECH
    }
