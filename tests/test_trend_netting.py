"""The Trends netting rule (ADR-0185, ADR-0230), checked at ``trend_netting.net_answer``.

Two kinds of test:

- **Golden answers** (``tests/fixtures/trend_answers/*.json``): each holds an answer as
  ``TrendHistory.answer`` builds it (``unnetted``) and the answer ``/trends`` serves (``served``).
  They were first written by the page's own netting (app.js before ADR-0230 step 4), so passing
  them proves the port decides what the page decided. The node tests load the same files as the
  answers they draw, so the page and this rule cannot drift apart. After a deliberate rule change,
  rewrite them with ``WRITE_TREND_ANSWERS=1 pytest tests/test_trend_netting.py`` and read the diff.
- **The measured cases** the page's netting tests carried, each named for what it measured, and
  the design's invariants 2 and 3 (``docs/trends/2026-09-25_trends-feature-design.md`` §8.1).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from headstart.trend_netting import js_round, net_answer

ANSWERS = Path(__file__).parent / "fixtures" / "trend_answers"
GOLDEN = sorted(ANSWERS.glob("*.json"))


@pytest.mark.parametrize("path", GOLDEN, ids=[p.stem for p in GOLDEN])
def test_golden_answer_is_what_the_rule_serves(path: Path) -> None:
    golden = json.loads(path.read_text(encoding="utf-8"))
    served = net_answer(golden["unnetted"])
    if os.environ.get("WRITE_TREND_ANSWERS"):
        golden["served"] = served
        path.write_text(
            json.dumps(golden, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    assert json.loads(json.dumps(served)) == golden["served"]


def _served(name: str) -> dict:
    return net_answer(
        json.loads((ANSWERS / f"{name}.json").read_text(encoding="utf-8"))["unnetted"]
    )


def _rounded(values: list) -> list:
    return [None if v is None else js_round(v) for v in values]


# ---- the measured cases ------------------------------------------------------------------------


def test_amazon_filter_change_over_two_runs_leaves_only_the_hiring_after_it() -> None:
    """Amazon, Sep 17: +308 at the change and −439 at the next run were one change."""
    assert _served("amazon_filter_change_lands_over_two_runs")["totals_net"] == [
        8869,
        8869,
        8869,
        8869,
        8880,
    ]


def test_google_refit_comes_out_by_openings_not_by_ratio() -> None:
    """Google SWE: the −33 before a refit that halved the category stays −33."""
    net = _served("google_refit_halves_a_category_by_openings")["totals_net"]
    assert _rounded(net) == [337, 304, 304, 304, 304]


def test_microsoft_filter_change_off_a_small_base_keeps_one_opening_lost() -> None:
    """Microsoft architecture: scaled by 14.5, the one real opening lost became −12."""
    net = _served("microsoft_filter_change_off_a_small_base")["totals_net"]
    assert _rounded(net) == [60, 59, 59, 59, 59]


def test_a_step_bigger_than_the_history_starts_the_line_after_it() -> None:
    assert _served("step_larger_than_the_history_starts_the_line_after_it")[
        "totals_net"
    ] == [
        None,
        5,
        5,
        5,
        5,
    ]


def test_a_found_board_on_a_whole_company_line_keeps_that_runs_hiring() -> None:
    """200 found; the other 10 that run were hiring and stay in, as Hot sums them."""
    acme = _served("found_board_lifted_by_its_own_size_keeping_the_run_hiring")[
        "series"
    ][0]
    assert acme["net"]["count"] == [300, 300, 310, 310]


def test_squircle_a_one_board_company_is_shifted_so_its_line_equals_hot() -> None:
    """Squircle read +513 scaled where Hot read +459: a whole line comes out by openings."""
    acme = _served("whole_company_line_takes_a_filter_change_out_by_openings")[
        "series"
    ][0]
    assert acme["net"]["count"] == [200, 210, 210, 210, 230]


def test_micron_filter_change_is_one_change_with_its_settling_run() -> None:
    """Micron, Sep 17: +32 at its run and −296 at the settling run, sized −264."""
    served = _served("micron_filter_change_sized_with_its_settling_run")
    micron = served["series"][0]
    change = next(s for s in micron["steps"] if served["notes"][s["note"]]["epoch"])
    assert js_round(change["size"]) == -264


def test_micron_a_removal_scales_the_doubled_growth_before_it() -> None:
    """#690: 1,000 jobs listed twice; +100 before the removal was 50 real hires, then 10 more."""
    micron = _served("duplicate_removal_scales_the_history_before_it")["series"][0]
    assert _rounded(micron["net"]["count"]) == [1000, 1050, 1050, 1060]


def test_wipro_shape_a_filter_step_is_taken_out_of_the_category_it_doubles() -> None:
    """Wipro's "+74.7%" carried a filter step; netted, the category grows only its last 10%."""
    a = _served("tech_filter_doubles_one_category")["series"][0]
    assert a["net"]["count"] == [200, 200, 200, 200, 220]


def test_nvidia_duplicates_have_their_own_size_beside_a_counting_change() -> None:
    """NVIDIA: the counting change on the run is −97, not the run's −2,138."""
    served = _served("nvidia_counting_change_without_the_duplicates_on_its_run")
    nvidia = served["series"][0]
    sizes = {
        served["notes"][step["note"]]["kind"]: step["size"] for step in nvidia["steps"]
    }
    assert sizes["duplicates"] == -2041
    assert (
        next(
            step["size"]
            for step in nvidia["steps"]
            if served["notes"][step["note"]]["epoch"]
        )
        == -97
    )


def test_duplicate_removal_touches_only_a_company_it_can_move() -> None:
    served = _served("duplicate_removal_touches_only_a_two_site_tenant")
    acme, beta = served["series"]
    assert acme["net"]["count"] == [80, 80, 80, 80]
    assert beta["net"]["count"] == [100, 100, 80, 80]


def test_under_new_a_filter_change_is_taken_out_again_a_week_later() -> None:
    assert _served("new_filter_change_taken_out_again_a_week_later")["totals_net"] == [
        100,
        100,
        100,
        100,
        100,
    ]


def test_a_partial_read_is_dropped_before_anything_is_netted() -> None:
    served = _served("partial_read_put_straight_back")
    assert served["series"][0]["points"] == [26, None, 26, 27]
    assert served["series"][1]["points"] == [100, 110, 120, 130]
    assert served["partial"] == 1


def test_with_no_pick_nothing_is_taken_out() -> None:
    served = _served("index_marks_counting_changes_and_takes_nothing_out")
    for line in served["series"]:
        assert line["net"]["count"] == line["points"]
        assert line["steps"] == []
    assert [note["withhold"] for note in served["notes"]] == [False, False]


# ---- the design's invariants (§8.1) ----------------------------------------------------------------


def _change(values: list) -> float:
    seen = [v for v in values if v is not None]
    return seen[-1] - seen[0]


@pytest.mark.parametrize(
    "name",
    [
        "refit_moves_openings_between_categories",
        "refit_with_a_settling_run_still_adds_up",
        "tech_filter_doubles_one_category",
        "nine_categories_one_folded_into_other",
        "found_board_on_a_category_line",
    ],
)
def test_invariant_2_a_company_equals_the_sum_of_its_categories(name: str) -> None:
    """A company's hiring, in openings, is the sum of its category lines' (ADR-0185, round 13)."""
    served = _served(name)
    company = _change(served["series_sum"]["net"]["count"])
    categories = sum(_change(line["net"]["count"]) for line in served["series"])
    assert js_round(company) == js_round(categories)


def test_invariant_2_total_equals_the_sum_of_its_picks() -> None:
    """Several picks summed read the sum of each pick's own netted line: Total is the Company
    breakdown's sum, +30 where the sum netted whole read 0."""
    total = _served("duplicate_removal_total_sums_each_company")["series_sum"]
    breakdown = _served("duplicate_removal_breakdown_by_company")["series"]
    picks = sum(_change(line["net"]["count"]) for line in breakdown)
    assert _change(total["net"]["count"]) == picks == 30


@pytest.mark.parametrize("path", GOLDEN, ids=[p.stem for p in GOLDEN])
def test_invariant_3_every_netted_line_ends_on_its_measured_latest_value(
    path: Path,
) -> None:
    """Netting adjusts backwards: a line's last value is always the one the index measured."""
    served = net_answer(json.loads(path.read_text(encoding="utf-8"))["unnetted"])
    lines = [*served["series"], served["series_sum"]]
    for line in lines:
        measured = [v for v in line["points"] if v is not None]
        netted = [v for v in line["net"]["count"] if v is not None]
        if measured:
            assert netted[-1] == measured[-1], line["name"]
    measured = [v for v in served["totals"] if v is not None]
    if measured:
        assert [v for v in served["totals_net"] if v is not None][-1] == measured[-1]
