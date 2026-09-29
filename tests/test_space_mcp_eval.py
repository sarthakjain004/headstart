"""Tests for scripts/eval/space_mcp_eval.py: the stream-json parser, every verifier kind, the
held-out hash guard and --dry-run. Nothing here starts Claude Code or reaches the Space."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from headstart.space_mcp.space_client import InvalidRequest, SpaceRoute
from headstart.space_mcp.tools import REGISTRY

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "eval" / "space_mcp_eval.py"


@pytest.fixture(scope="module")
def ev():
    spec = importlib.util.spec_from_file_location("space_mcp_eval", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    # Registered before it runs: @dataclass looks its own module up in sys.modules.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# The shape `claude -p --output-format stream-json --verbose` (Claude Code 2.1.212) wrote for a
# run against this server on 2026-09-28, trimmed to the fields the parser reads plus a few it
# must ignore: a successful result carries a list of text blocks and no is_error; a refused one
# carries a string and is_error true.
def _transcript_lines(*, ended: bool = True) -> list[str]:
    events = [
        {
            "type": "system",
            "subtype": "init",
            "tools": ["mcp__headstart-space__search_jobs"],
            "mcp_servers": [{"name": "headstart-space", "status": "connected"}],
            "model": "claude-opus-4-8[1m]",
        },
        {
            "type": "rate_limit_event",
            "rate_limit_info": {"status": "allowed", "rateLimitType": "five_hour"},
        },
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "thinking", "thinking": "", "signature": "x"}]
            },
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "mcp__headstart-space__search_jobs",
                        "input": {"query": "backend engineer", "salary_min": 3000000},
                        "caller": {"type": "direct"},
                    }
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "content": "salary_min and salary_max need salary_currency: ...",
                        "is_error": True,
                        "tool_use_id": "toolu_1",
                    }
                ],
            },
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_2",
                        "name": "mcp__headstart-space__search_jobs",
                        "input": {
                            "query": "backend engineer",
                            "salary_min": 3000000,
                            "salary_currency": "INR",
                        },
                    }
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "tool_use_id": "toolu_2",
                        "type": "tool_result",
                        "content": [
                            {"type": "text", "text": "37 jobs match these filters."}
                        ],
                    }
                ],
            },
            "tool_use_result": [
                {"type": "text", "text": "37 jobs match these filters."}
            ],
        },
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "There are 37 matching jobs."}]
            },
        },
    ]
    if ended:
        events.append(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "num_turns": 3,
                "result": "There are 37 matching jobs.",
                "total_cost_usd": 0.085,
            }
        )
    return [json.dumps(event) + "\n" for event in events]


def test_parse_reads_calls_results_errors_and_the_final_answer(ev):
    transcript = ev.parse(_transcript_lines())

    assert [c.name for c in transcript.calls] == ["search_jobs", "search_jobs"]
    first, second = transcript.calls
    assert first.arguments == {"query": "backend engineer", "salary_min": 3000000}
    assert first.is_error and first.result.startswith("salary_min and salary_max")
    assert not second.is_error and second.result == "37 jobs match these filters."
    assert transcript.final_answer == "There are 37 matching jobs."
    assert transcript.model == "claude-opus-4-8[1m]"
    assert transcript.cost_usd == 0.085
    assert transcript.run_error is None


def test_parse_reads_the_servers_status_at_the_runs_start(ev):
    connected = ev.parse(_transcript_lines())
    # Claude Code 2.1.212's -p over HTTP without MCP_CONNECTION_NONBLOCKING=false.
    pending = ev.parse(_init_line("pending"))

    assert (
        connected.server_status == "connected"
        and ev.why_not_connected(connected) is None
    )
    assert pending.server_status == "pending"
    assert "pending at the run's start" in ev.why_not_connected(pending)
    assert "not named" in ev.why_not_connected(ev.parse([]))


def _init_line(status):
    return [
        json.dumps(
            {
                "type": "system",
                "subtype": "init",
                "tools": [],
                "mcp_servers": [{"name": "headstart-space", "status": status}],
            }
        )
    ]


def test_parse_says_a_run_without_a_result_event_was_cut_off(ev):
    transcript = ev.parse(_transcript_lines(ended=False))

    assert transcript.run_error == "no result event: the run was cut off"
    assert (
        transcript.final_answer == "There are 37 matching jobs."
    )  # the last text block


def test_metrics_count_a_refusal_corrected_by_the_next_call(ev):
    metrics = ev._metrics(ev.parse(_transcript_lines()))

    assert metrics["tool_calls"] == 2
    assert metrics["errors"] == 1
    assert (metrics["refusals"], metrics["refusals_corrected"]) == (1, 1)
    assert metrics["largest_tool_result_chars"] == max(
        len("salary_min and salary_max need salary_currency: ..."),
        len("37 jobs match these filters."),
    )


def test_a_refusal_is_corrected_only_by_a_successful_retry_of_the_same_tool(ev):
    refused = ("search_jobs", {"salary_min": 1}, "needs salary_currency", True)
    elsewhere = ("hiring_now", {}, "Hiring now, expansion: ...", False)
    retried = (
        "search_jobs",
        {"salary_min": 1, "salary_currency": "INR"},
        "3 jobs",
        False,
    )

    def corrected(*calls):
        return ev._metrics(_transcript(ev, calls))["refusals_corrected"]

    assert corrected(refused, retried) == 1
    assert corrected(refused, elsewhere, retried) == 0


def test_a_space_failure_is_not_counted_as_a_refusal(ev):
    call = ev.ToolCall(
        "search_jobs", {}, "The HeadStart Space is starting. It restarts ...", True
    )

    assert not call.refused


# --- helpers for the verifiers -------------------------------------------------------------


def _transcript(ev, calls=(), answer=""):
    return ev.Transcript(calls=[ev.ToolCall(*c) for c in calls], final_answer=answer)


class FakeSpace:
    """Answers `read(route, params)` from ``answers`` keyed by route (a payload, or an exception
    to raise), and records every request."""

    def __init__(self, answers):
        self.answers = answers
        self.asked = []

    def read(self, route, params=()):
        self.asked.append((route, list(params)))
        answer = self.answers[route]
        if isinstance(answer, BaseException):
            raise answer
        return answer


# --- search_args ---------------------------------------------------------------------------

_T01 = {
    "tool": "search_jobs",
    "must": {
        "remote": True,
        "india_place": "bengaluru",
        "salary_min": 3000000,
        "salary_currency": "INR",
        "query": {"op": "contains", "value": "backend"},
    },
    "query_must_not_contain": ["bengaluru", "lakh"],
}


def test_tool_args_passes_when_one_successful_call_meets_every_rule(ev):
    good = {
        "query": "Backend engineer",
        "remote": True,
        "india_place": "Bengaluru",
        "salary_min": 3000000,
        "salary_currency": "inr",
    }
    transcript = _transcript(
        ev,
        [
            ("search_jobs", {"query": "backend"}, "12 jobs", False),
            ("search_jobs", good, "3 jobs", False),
        ],
    )

    assert ev.verify_tool_args(_T01, transcript, None).passed


def test_tool_args_fails_a_constraint_left_in_the_query(ev):
    args = {
        "query": "backend engineer bengaluru 30 lakh",
        "remote": True,
        "india_place": "bengaluru",
        "salary_min": 3000000,
        "salary_currency": "INR",
    }
    verdict = ev.verify_tool_args(
        _T01, _transcript(ev, [("search_jobs", args, "0 jobs", False)]), None
    )

    assert not verdict.passed
    assert (
        "contains 'bengaluru'" in verdict.detail and "contains 'lakh'" in verdict.detail
    )


def test_tool_args_ignores_a_refused_call_even_when_its_arguments_fit(ev):
    args = {
        **{k: v for k, v in _T01["must"].items() if k != "query"},
        "query": "backend",
    }
    verdict = ev.verify_tool_args(
        _T01, _transcript(ev, [("search_jobs", args, "the Space refused", True)]), None
    )

    assert not verdict.passed and verdict.detail == "no successful search_jobs call"


@pytest.mark.parametrize(
    ("value", "rule", "holds"),
    [
        (24, {"op": "<=", "value": 24}, True),
        (48, {"op": "<=", "value": 24}, False),
        (None, {"op": ">=", "value": 3}, False),
        (True, {"op": ">=", "value": 0}, False),  # a switch is not a number
        (None, {"op": "in", "value": [None, "company"]}, True),
        ("Company", {"op": "in", "value": [None, "company"]}, True),
        ([], {"op": "in", "value": [None, []]}, True),
        (
            ["Google", "microsoft"],
            {"op": "contains", "value": ["google", "Microsoft"]},
            True,
        ),
        (["Google"], {"op": "contains", "value": ["google", "microsoft"]}, False),
        ("Staff Engineer", {"op": "contains", "value": "staff"}, True),
        (None, {"op": "contains", "value": "staff"}, False),
        ("salary", "SALARY", True),
        (None, None, True),
    ],
)
def test_holds(ev, value, rule, holds):
    assert ev.holds(value, rule) is holds


def test_tool_args_needs_one_must_any_alternative(ev):
    expect = {
        "must_any": [
            {"india_place": "pune"},
            {"location": {"op": "contains", "value": "pune"}},
        ]
    }

    def verdict(args):
        return ev.verify_tool_args(
            expect, _transcript(ev, [("search_jobs", args, "5 jobs", False)]), None
        )

    assert verdict({"location": "Pune, India"}).passed
    assert verdict({"india_place": "pune"}).passed
    assert not verdict({"india_place": "mumbai"}).passed


def test_tool_args_judges_a_left_out_argument_at_its_schema_default(ev):
    expect = {"tool": "read_trends", "must": {"days": {"op": ">=", "value": 25}}}
    call = ("read_trends", {"companies": ["Google", "Microsoft"]}, "Total ...", False)

    assert ev.verify_tool_args(expect, _transcript(ev, [call]), None).passed  # days 30


def test_tool_args_reads_the_tool_it_names(ev):
    expect = {"tool": "read_trends", "must": {"category": "data-engineering"}}
    search = ("search_jobs", {"category": "data-engineering"}, "5 jobs", False)
    trends = ("read_trends", {"category": "data-engineering"}, "Total ...", False)

    assert ev.verify_tool_args(expect, _transcript(ev, [search, trends]), None).passed
    assert not ev.verify_tool_args(expect, _transcript(ev, [search]), None).passed


# --- trend_sign ----------------------------------------------------------------------------

_STRIPE = {
    "key": "greenhouse:stripe",
    "label": "Stripe",
    "atses": ["greenhouse"],
    "board_keys": ["greenhouse:stripe"],
    "openings": 217,
    "match": "exact",
}


def _trends_space(hiring=None, reading="default", lines=()):
    if reading == "default":
        reading = {
            "total": None if hiring is None else {"move": {"hiring": hiring}},
            "lines": list(lines),
        }
    return FakeSpace(
        {
            SpaceRoute.COMPANIES_SUGGEST: {"companies": [_STRIPE]},
            SpaceRoute.TRENDS: {"reading": reading},
        }
    )


_T03 = {"companies": ["Stripe"], "category": None, "days": 14}


def test_trend_sign_compares_the_spaces_own_sign_with_the_answer(ev):
    space = _trends_space(hiring=11)
    answer = "Stripe is hiring **more** than two weeks ago: hiring +11, not less."

    verdict = ev.verify_trend_sign(_T03, _transcript(ev, answer=answer), space)

    assert verdict.passed, verdict.detail
    route, params = space.asked[-1]
    assert route is SpaceRoute.TRENDS
    assert ("company", "greenhouse:stripe") in params
    assert [name for name, _ in params] == ["since", "company"]


def test_trend_sign_fails_the_opposite_direction(ev):
    answer = "Stripe is hiring fewer people than two weeks ago."

    assert not ev.verify_trend_sign(
        _T03, _transcript(ev, answer=answer), _trends_space(11)
    ).passed
    assert ev.verify_trend_sign(
        _T03, _transcript(ev, answer=answer), _trends_space(-4)
    ).passed


def test_trend_sign_skips_the_questions_own_more_or_less(ev):
    answer = "More or less than two weeks ago? Stripe's openings shrank: hiring −4."

    assert ev.verify_trend_sign(
        _T03, _transcript(ev, answer=answer), _trends_space(-4)
    ).passed


def test_trend_sign_reads_a_single_line_when_there_is_no_total(ev):
    space = _trends_space(lines=[{"move": {"hiring": 3}}])

    verdict = ev.verify_trend_sign(
        _T03, _transcript(ev, answer="It is growing."), space
    )

    assert verdict.passed


def test_trend_sign_fails_when_the_space_cannot_read_the_trend(ev):
    verdict = ev.verify_trend_sign(
        _T03, _transcript(ev, answer="growing"), _trends_space(reading=None)
    )

    assert not verdict.passed and "could not read" in verdict.detail


def _turnover_space(hiring, opened, closed):
    move = {
        "hiring": hiring,
        "turnover": {"opened": opened, "closed": closed, "net": opened - closed},
    }
    return _trends_space(reading={"total": {"move": move}, "lines": []})


def test_trend_sign_takes_hiring_from_postings_opened_and_closed(ev):
    """ADR-0272: the whole index's netted "hiring" read +111,851 while opened less closed was
    −514. The answer that followed the netted figure is wrong."""
    space = _turnover_space(111_851, 17_032, 17_546)
    followed_the_stock = "Hiring is up sharply: hiring +111,851 (+42%)."
    followed_turnover = "Roughly flat: 17,032 opened and 17,546 closed, net −514."
    said_down = "Slightly down: more postings closed than opened."

    assert not ev.verify_trend_sign(
        _T03, _transcript(ev, answer=followed_the_stock), space
    ).passed
    verdict = ev.verify_trend_sign(
        _T03, _transcript(ev, answer=followed_turnover), space
    )
    assert verdict.passed and "-514 (down or flat)" in verdict.detail
    assert ev.verify_trend_sign(_T03, _transcript(ev, answer=said_down), space).passed


def test_trend_sign_calls_a_large_net_by_its_sign_only(ev):
    space = _turnover_space(-40, 100, 20)

    assert not ev.verify_trend_sign(
        _T03, _transcript(ev, answer="Flat, more or less."), space
    ).passed
    assert ev.verify_trend_sign(
        _T03, _transcript(ev, answer="Growing: net +80."), space
    ).passed


def test_trend_sign_fails_a_hiring_figure_turnover_and_sized_causes_cannot_make(ev):
    """Review of #865: the sign alone passed an answer quoting the whole change in openings
    listed as hiring, when its sign happened to match."""
    move = {
        "hiring": 900,
        "not_hiring_total": -50,
        "turnover": {"opened": 100, "closed": 20, "net": 80},
    }
    space = _trends_space(reading={"total": {"move": move}, "lines": []})
    quoted_the_listed_change = "Hiring rose: net +111,929 openings this month."
    within = "Hiring rose: net +80, 100 opened and 20 closed."

    failed = ev.verify_trend_sign(
        _T03, _transcript(ev, answer=quoted_the_listed_change), space
    )
    assert not failed.passed and "more than the 170 opened, closed" in failed.detail
    assert ev.verify_trend_sign(_T03, _transcript(ev, answer=within), space).passed


def _span_space(began="2026-09-25T18:16:40+00:00"):
    """Stripe over 14 days to 2026-09-29 04:04, with opened and closed counted since ``began``:
    3.4 of the window's 14.4 days."""
    move = {"hiring": 4, "turnover": {"opened": 8, "closed": 4, "net": 4}}
    window = {"from": "2026-09-14T18:00:00+00:00", "to": "2026-09-29T04:04:35+00:00"}
    return FakeSpace(
        {
            SpaceRoute.COMPANIES_SUGGEST: {"companies": [_STRIPE]},
            SpaceRoute.TRENDS: {
                "reading": {"total": {"move": move}, "lines": [], "window": window},
                "turnover_since": began,
            },
        }
    )


@pytest.mark.parametrize(
    "answer",
    [
        "Up: net +4, counted only since 2026-09-25.",
        "Up (net +4), though HeadStart counts postings only since Sept 25.",
        "Up: net +4. Opened and closed cover only 25th September onwards.",
        "Up, net +4 over the last 3.4 days it has counted.",
        "Up: net +4, across about three days of counting.",
        "Up: net +4 (counted for 3.4 of the window's 14.4 days).",
    ],
)
def test_trend_sign_passes_an_answer_that_states_the_turnover_span(ev, answer):
    verdict = ev.verify_trend_sign(_T03, _transcript(ev, answer=answer), _span_space())

    assert verdict.passed, verdict.detail


@pytest.mark.parametrize(
    "answer",
    [
        "Stripe is hiring more: net +4 over two weeks.",
        "Up: net +4 over the last 14 days.",  # the window, not the days counted
        "Up: net +4 since September 14.",  # the window's start, not counting's
    ],
)
def test_trend_sign_fails_an_answer_silent_on_the_turnover_span(ev, answer):
    verdict = ev.verify_trend_sign(_T03, _transcript(ev, answer=answer), _span_space())

    assert not verdict.passed
    assert "counted only since 2026-09-25 (3.4 days)" in verdict.detail


def test_trend_sign_needs_no_span_when_turnover_covers_the_window(ev):
    space = _span_space(began="2026-09-01T00:00:00+00:00")
    answer = "Stripe is hiring more: net +4 over two weeks."

    assert ev.verify_trend_sign(_T03, _transcript(ev, answer=answer), space).passed


@pytest.mark.parametrize(
    ("answer", "direction"),
    [
        ("Hiring is up slightly; they are expanding.", "up"),
        ("Stripe's hiring is up 4%.", "up"),
        ("Openings declined.", "down"),
        ("Roughly flat over the window.", "flat"),
        ("Regardless of the window, it is unless ...", None),  # no whole-word direction
        ("It lists up to 40 roles.", None),  # an amount, not a direction
        # The netted figure outranks a word about raw openings, and "Not hiring" is not it.
        ("Openings fell from 217 to 199, but hiring is +3.", "up"),
        ("Not hiring +7 (re-counting); hiring −4 net.", "down"),
    ],
)
def test_stated_direction(ev, answer, direction):
    assert ev.stated_direction(answer)[0] == direction


# --- hot_top -------------------------------------------------------------------------------


def _hot_space():
    def row(n, company, operator="employer"):
        return {"key": f"workday:c{n}", "company": company, "operator": operator}

    rows = [
        row(1, "Acme Robotics"),
        row(2, "Staffing Hub", "staffing"),
        row(3, "Borealis Data"),
        row(4, "Cobalt Payments, Inc."),
        row(5, "Dune Analytics"),
        row(6, "Ember Health"),
        row(7, "Fjord Security"),
    ]
    return FakeSpace(
        {
            SpaceRoute.HOT: {
                "lenses": {"expansion": rows},
                "hidden_by_default": ["staffing", "aggregator"],
            }
        }
    )


def test_hot_top_drops_hidden_operators_and_needs_n_minus_one(ev):
    expect = {"lens": "expansion", "top": 5}
    four = "1. Acme Robotics 2. Borealis Data 3. Cobalt Payments 4. Dune Analytics 5. Fjord"
    three = "Acme Robotics, Borealis Data and Staffing Hub lead, then Fjord Security."

    passed = ev.verify_hot_top(expect, _transcript(ev, answer=four), _hot_space())
    failed = ev.verify_hot_top(expect, _transcript(ev, answer=three), _hot_space())

    assert passed.passed, passed.detail
    assert "Staffing Hub" not in passed.detail  # hidden, so not one of the top five
    assert not failed.passed


# The shape hiring_now printed on 2026-09-29 (round-2 critique mr08), trimmed.
_HIRING_NOW = """\
Hiring now, expansion: ...
 1. "Acme Robotics" · key workday:c1 · employer · 958 open now · net +442 · opened 23 · \
closed 33 (opened less closed -10) · rate 2% · FLAG net not backed by postings opened: \
mostly re-counting, not hiring
 2. "Borealis Data" · key workday:c3 · employer · 714 open now · net +100 · opened 304 · \
closed 218 (opened less closed +86) · rate 43%
 3. "Cobalt Payments, Inc." · key workday:c4 · employer · 144 open now · net +94"""


def _hot_answer(ev, answer):
    return _transcript(
        ev, [("hiring_now", {"lens": "expansion"}, _HIRING_NOW, False)], answer
    )


def test_hot_top_fails_an_answer_that_leads_with_a_row_hiring_now_flagged(ev):
    expect = {"lens": "expansion", "top": 5}
    in_site_order = (
        "Acme Robotics, Borealis Data, Cobalt Payments and Dune Analytics lead."
    )
    unflagged_first = (
        "Borealis Data leads, then Cobalt Payments and Dune Analytics; Acme Robotics ranks "
        "first on the site, but that is re-counting."
    )

    led = ev.verify_hot_top(expect, _hot_answer(ev, in_site_order), _hot_space())
    passed = ev.verify_hot_top(expect, _hot_answer(ev, unflagged_first), _hot_space())

    assert not led.passed and "leads with 'Acme Robotics'" in led.detail
    assert passed.passed, passed.detail


def test_flagged_headline_reads_only_the_rows_hiring_now_printed(ev):
    no_call = _transcript(ev, answer="Acme Robotics leads.")
    by_key = _hot_answer(ev, "workday:c1 leads.")
    suffix = _hot_answer(ev, "Cobalt Payments leads, then Acme Robotics.")

    assert ev.flagged_headline(no_call) is None
    assert ev.flagged_headline(by_key) == "Acme Robotics"
    assert ev.flagged_headline(suffix) is None


def test_hot_top_judges_the_order_hiring_now_lists_so_a_disowned_leader_is_not_needed(
    ev,
):
    """ADR-0321: on the site's older Lenses a flagged row is listed after the unflagged ones,
    so an answer that leads with real rows names the tool's top five, not the page's."""
    space = _hot_space()
    rows = space.answers[SpaceRoute.HOT]["lenses"]["expansion"]
    # Acme's net is re-counting: +442 on 23 opened and 33 closed.
    rows[0].update(stock=958, net=442, opened=23, closed=33)
    answer = "Borealis Data, Cobalt Payments, Dune Analytics, Ember Health and Fjord Security."
    verdict = ev.verify_hot_top(
        {"lens": "expansion", "top": 5}, _transcript(ev, answer=answer), space
    )
    assert verdict.passed, verdict.detail
    assert "Acme Robotics" not in verdict.detail


def test_hot_top_judges_at_the_calls_own_limit(ev):
    """A `limit: 2` call lists two rows, so the answer is judged on those two (review of
    #896), in the order worked out from /hot here, not by hiring_now."""
    space = _hot_space()
    rows = space.answers[SpaceRoute.HOT]["lenses"]["expansion"]
    rows[0].update(stock=958, net=442, opened=23, closed=33)
    answer = "Borealis Data leads, then Cobalt Payments."
    transcript = _transcript(
        ev, [("hiring_now", {"lens": "expansion", "limit": 2}, "", False)], answer
    )
    verdict = ev.verify_hot_top({"lens": "expansion", "top": 5}, transcript, space)
    assert verdict.passed, verdict.detail
    assert "top 2 on expansion" in verdict.detail


def test_the_expected_order_flags_what_hiring_now_flags(ev):
    """The eval's own flag rules, row by row: each is what ADR-0321 says a site Lens flags."""
    window = {
        "base": "2026-09-22T00:00:00+00:00",
        "to": "2026-09-29T00:00:00+00:00",
        "turnover_from": "2026-09-25T12:00:00+00:00",
    }

    def disowned(lens="expansion", **row):
        return ev._disowned({"stock": 500, **row}, lens, window, 25)

    assert disowned(
        net=100, opened=306, closed=321
    )  # a gain against opened less closed
    assert not disowned(net=90, opened=83, closed=75)
    assert disowned(net=10, opened=40, closed=None)  # closures not counted
    assert disowned(net=10, opened=40, closed=5, closures_uncounted_boards=1)
    assert disowned(net=10, opened=600, closed=500)  # more opened than open now
    assert disowned("rate", net=10, opened=10, closed=5, stock=30)  # small base
    assert not disowned("opened_less_closed", net=900, opened=10, closed=5)
    # opened mostly found late, on every Lens (ADR-0351)
    starbucks = {"net": 50, "opened": 50, "closed": 0, "opened_found_late": 28}
    assert disowned("opened_less_closed", **starbucks, opened_fresh=22)
    assert not disowned("opened_less_closed", **starbucks, opened_fresh=25)
    assert not disowned("opened_less_closed", **starbucks, opened_fresh=None)


def test_hot_top_fails_an_answer_that_reports_a_found_late_row_as_hiring(ev):
    """Round-4 critique P1-1 (ADR-0351): Starbucks' 50 opened were mostly posted months before
    HeadStart first saw them. An answer naming it without saying so fails; one that says so, or
    leaves it out, passes."""
    space = _hot_space()
    rows = space.answers[SpaceRoute.HOT]["lenses"]["expansion"]
    rows[4].update(
        company="Starbucks", opened=50, opened_fresh=22, opened_found_late=28
    )
    top = (
        "Acme Robotics, Borealis Data, Cobalt Payments, Ember Health and Fjord Security"
    )
    expect = {"lens": "expansion", "top": 5}

    reported = ev.verify_hot_top(
        expect, _transcript(ev, answer=f"{top} lead; Starbucks opened 50."), space
    )
    said = ev.verify_hot_top(
        expect,
        _transcript(
            ev,
            answer=f"{top} lead. Starbucks' 50 opened were mostly found late, posted "
            "weeks before HeadStart saw them.",
        ),
        space,
    )
    left_out = ev.verify_hot_top(expect, _transcript(ev, answer=f"{top} lead."), space)

    assert not reported.passed
    assert "reports Starbucks as hiring" in reported.detail
    assert said.passed, said.detail
    assert left_out.passed, left_out.detail
    # Listed after the unflagged rows, so the top five are the others.
    assert "Starbucks" not in left_out.detail


def test_flagged_headline_reads_a_row_that_gives_its_place_on_the_page(ev):
    """ADR-0321: a reordered row starts "site #N · "."""
    moved = _HIRING_NOW.replace(' 1. "Acme', ' 3. site #1 · "Acme')
    transcript = _transcript(
        ev, [("hiring_now", {}, moved, False)], "Acme Robotics leads."
    )
    assert ev.flagged_headline(transcript) == "Acme Robotics"


# --- title_keyword_rows --------------------------------------------------------------------

_RUST_ROWS = r"""3 jobs match these filters. Showing 1–3.
 1. "Senior Rust Engineer" · "Threema AG" · remote
    id "teamtailor:threemagmbh:07fb" · "https://example.com/1"
 2. "Rust-based Platform Engineer" · "webAI" · remote
    id "ashby:webai:daf8" · "https://example.com/2"
    also #3: "Contract"
      id "ashby:webai:\"quoted\"" · "https://example.com/3"
"""
_RUST_EXPECT = {
    "tool": "search_jobs",
    "must": {"keyword": {"op": "contains", "value": "rust"}},
    "word": "rust",
}


def _job_space(*titles):
    ids = ["teamtailor:threemagmbh:07fb", "ashby:webai:daf8", 'ashby:webai:"quoted"']
    jobs = [{"id": i, "title": t} for i, t in zip(ids, titles, strict=True)]
    return FakeSpace({SpaceRoute.JOB: {"jobs": jobs, "missing": []}})


def test_title_keyword_rows_reads_back_every_row_the_call_returned(ev):
    transcript = _transcript(
        ev, [("search_jobs", {"keyword": "Rust"}, _RUST_ROWS, False)]
    )
    space = _job_space(
        "Senior Rust Engineer", "Rust-based Platform Engineer", "Rust Contractor"
    )

    verdict = ev.verify_title_keyword_rows(_RUST_EXPECT, transcript, space)

    assert verdict.passed, verdict.detail
    route, params = space.asked[-1]
    assert route is SpaceRoute.JOB
    assert [v for _, v in params] == [
        "teamtailor:threemagmbh:07fb",
        "ashby:webai:daf8",
        'ashby:webai:"quoted"',
    ]


def test_title_keyword_rows_fails_a_row_matching_only_inside_a_word(ev):
    transcript = _transcript(
        ev, [("search_jobs", {"keyword": "rust"}, _RUST_ROWS, False)]
    )
    space = _job_space("Senior Rust Engineer", "Director, Data Trust", "Thrusters Lead")

    verdict = ev.verify_title_keyword_rows(_RUST_EXPECT, transcript, space)

    assert not verdict.passed
    assert (
        "1 of 3 rows" in verdict.detail and "'Director, Data Trust'" in verdict.detail
    )


def test_title_keyword_rows_checks_the_arguments_first(ev):
    transcript = _transcript(
        ev, [("search_jobs", {"query": "rust"}, _RUST_ROWS, False)]
    )

    verdict = ev.verify_title_keyword_rows(_RUST_EXPECT, transcript, FakeSpace({}))

    assert (
        not verdict.passed and "no search_jobs call meets every rule" in verdict.detail
    )


# --- sponsorship_polarity ------------------------------------------------------------------

_POLARITY_EXPECT = {"at_least": 1}


def _stance_space(*jobs):
    """`/job` answering ``jobs``, as (id, title, company, mentions), to every read. The stances
    the Space reads are left out: the verdict does not read them (round-3 review SP8)."""
    served = [
        {"id": i, "title": t, "company": c, "work_authorization": {"mentions": m}}
        for i, t, c, m in jobs
    ]
    return FakeSpace({SpaceRoute.JOB: {"jobs": served, "missing": []}})


_POLARITY_JOBS = (
    (
        "teamtailor:threemagmbh:07fb",
        "Backend Engineer",
        "Threema AG",
        ["We sponsor H-1B visas for this role."],
    ),
    (
        "ashby:webai:daf8",
        "Platform Engineer",
        "webAI",
        ["Visa sponsorship is not available for this position."],
    ),
    ('ashby:webai:"quoted"', "Data Engineer", "webAI", []),
)


def test_sponsorship_polarity_passes_an_answer_naming_only_jobs_that_do_not_refuse(ev):
    transcript = _transcript(
        ev,
        [
            (
                "search_jobs",
                {"work_authorization": "offers_sponsorship"},
                _RUST_ROWS,
                False,
            )
        ],
        answer="Backend Engineer at Threema AG offers H-1B sponsorship.",
    )
    space = _stance_space(*_POLARITY_JOBS)

    verdict = ev.verify_sponsorship_polarity(_POLARITY_EXPECT, transcript, space)

    assert verdict.passed, verdict.detail
    assert (
        "names 1 of the 3 jobs read back; each offers sponsorship as far as the eval "
        "reads it" in verdict.detail
    )


def test_sponsorship_polarity_fails_an_answer_naming_a_job_that_refuses(ev):
    transcript = _transcript(
        ev,
        [("search_jobs", {"keyword": "sponsorship"}, _RUST_ROWS, False)],
        answer=(
            "These mention sponsorship: Backend Engineer (Threema AG), and "
            "ashby:webai:daf8, the Platform Engineer role at webAI."
        ),
    )
    space = _stance_space(*_POLARITY_JOBS)

    verdict = ev.verify_sponsorship_polarity(_POLARITY_EXPECT, transcript, space)

    assert not verdict.passed
    assert (
        "1 of them do not offer sponsorship: 'Platform Engineer' at 'webAI' (says 'Visa "
        "sponsorship is not available for this position.')" in verdict.detail
    )


@pytest.mark.parametrize(
    ("mention", "offers"),
    [
        ("We're unable to offer visa sponsorship for this role", False),
        ("Please note that we currently don’t sponsor visas.", False),
        ("U.S. citizenship is required for this role.", False),
        ("Visa sponsorship is available for this position.", True),
        (
            (
                "We support visa sponsorship and relocation within Europe, where it makes "
                "the difference between hiring the right person and not."
            ),
            True,
        ),
    ],
)
def test_sponsorship_polarity_reads_only_a_negation_near_a_sponsorship_word(
    ev, mention, offers
):
    job = {"id": "lever:acme:1", "work_authorization": {"mentions": [mention]}}
    assert (ev._not_offering(job, {}) is None) is offers


def test_sponsorship_polarity_takes_a_persons_label_over_any_reading(ev):
    """A labelled job is judged by its label (#947's fixture), whatever its sentences say."""
    labelled_refusing = "workday:pae/Amentum_Careers:R0166374"
    labelled_offering = "ashby:clera:8dcd8b07-459d-46f0-a93c-d9c82f7820b6"
    space = _stance_space(
        (labelled_refusing, "Field Engineer", "Amentum", ["We sponsor visas."]),
        (labelled_offering, "ML Engineer", "Clera", ["No visa? No problem."]),
    )
    rows = f'1. "Field Engineer"\n   id "{labelled_refusing}"\n2. "ML Engineer"\n   id "{labelled_offering}"'

    def verdict(answer):
        transcript = _transcript(
            ev, [("search_jobs", {"keyword": "visa"}, rows, False)], answer=answer
        )
        return ev.verify_sponsorship_polarity(_POLARITY_EXPECT, transcript, space)

    assert verdict("ML Engineer at Clera sponsors visas.").passed
    refused = verdict("Field Engineer at Amentum sponsors visas.")
    assert not refused.passed and "(labelled refuses by hand)" in refused.detail


def test_sponsorship_polarity_with_said_ok_passes_a_job_reported_as_not_offering(ev):
    """A keyword answer may name a job that refuses, on a line saying so; one naming it as a
    match with no such word still fails."""
    expect = {"at_least": 0, "said_ok": True}
    space = _stance_space(*_POLARITY_JOBS)

    def verdict(answer):
        transcript = _transcript(
            ev,
            [("search_jobs", {"keyword": "sponsorship"}, _RUST_ROWS, False)],
            answer=answer,
        )
        return ev.verify_sponsorship_polarity(expect, transcript, space)

    assert verdict(
        "Backend Engineer at Threema AG offers it.\n"
        "Platform Engineer at webAI mentions it but refuses sponsorship."
    ).passed
    assert not verdict("Matches: Platform Engineer at webAI.").passed
    assert verdict("50 jobs mention sponsorship.").passed


_HEDGED_JOB = (
    "workday:amgen/Careers:R-1",
    "Software Engineer",
    "Amgen",
    ["Sponsorship Sponsorship for this role is not guaranteed."],
)
_HEDGED_ROWS = (
    '1. "Software Engineer"\n   id "workday:amgen/Careers:R-1"\n' + _RUST_ROWS
)


def _hedged_verdict(ev, answer, expect=_POLARITY_EXPECT):
    transcript = _transcript(
        ev,
        [
            (
                "search_jobs",
                {"work_authorization": "offers_sponsorship"},
                _HEDGED_ROWS,
                False,
            )
        ],
        answer=answer,
    )
    space = _stance_space(_HEDGED_JOB, *_POLARITY_JOBS)
    return ev.verify_sponsorship_polarity(expect, transcript, space)


def test_sponsorship_polarity_passes_a_job_named_only_to_say_it_was_dropped(ev):
    # Round-4 critique P1-4's t36 r1: the answer named Amgen only to drop it (ADR-0353).
    answer = (
        "Backend Engineer at Threema AG sponsors H-1B visas.\n"
        "I dropped an Amgen listing (Software Engineer) because its description says "
        "'Sponsorship for this role is not guaranteed'."
    )
    assert _hedged_verdict(ev, answer, {"at_least": 1, "said_ok": True}).passed


def test_sponsorship_polarity_passes_a_hedged_offer_only_when_the_answer_says_so(ev):
    assert not _hedged_verdict(
        ev, "Software Engineer at Amgen offers visa sponsorship."
    ).passed
    assert _hedged_verdict(
        ev, "Software Engineer at Amgen: sponsorship is possible but not guaranteed."
    ).passed


@pytest.mark.parametrize(
    "mention",
    [
        "Sponsorship for this role is not guaranteed.",
        "Visa sponsorship may be available for select positions.",
        "Sponsorship decisions are made on a case-by-case basis.",
    ],
)
def test_sponsorship_polarity_reads_a_hedge_before_a_negation(ev, mention):
    job = {"id": "lever:acme:1", "work_authorization": {"mentions": [mention]}}
    assert ev._not_offering(job, {}).startswith("hedged")


def test_sponsorship_polarity_fails_an_answer_naming_no_job(ev):
    transcript = _transcript(
        ev,
        [
            (
                "search_jobs",
                {"work_authorization": "offers_sponsorship"},
                _RUST_ROWS,
                False,
            )
        ],
        answer="Several companies sponsor visas.",
    )
    verdict = ev.verify_sponsorship_polarity(
        _POLARITY_EXPECT, transcript, _stance_space(*_POLARITY_JOBS)
    )
    assert not verdict.passed and "fewer than 1" in verdict.detail


def test_sponsorship_polarity_reads_get_job_rows_and_needs_some(ev):
    empty = _transcript(ev, [], answer="Threema AG")
    assert not ev.verify_sponsorship_polarity(
        _POLARITY_EXPECT, empty, _stance_space()
    ).passed
    read = _transcript(
        ev,
        [("get_job", {"ids": ["teamtailor:threemagmbh:07fb"]}, _RUST_ROWS, False)],
        answer="Threema AG's Backend Engineer role sponsors visas.",
    )
    verdict = ev.verify_sponsorship_polarity(
        _POLARITY_EXPECT, read, _stance_space(*_POLARITY_JOBS)
    )
    assert verdict.passed, verdict.detail


@pytest.mark.parametrize(
    ("text", "term", "starts"),
    [
        ("Senior Rust Engineer", "rust", True),
        ("Rust-based systems", "rust", True),
        ("RUST developer", "rust", True),
        (
            "Rustacean wanted",
            "rust",
            True,
        ),  # ADR-0299 anchors a term's start, not its end
        (
            "IN_Senior Associate_AI/ML Engineer",
            "ai",
            True,
        ),  # `_` and `/` separate words
        ("Director, Data Trust", "rust", False),
        ("Hall-Effect Thrusters", "rust", False),
        ("HTML and XML", "ml", False),
        ("ML Engineer", "ml", True),
        ("Senior C++ Developer", "c++", True),
        (
            "ASP.NET Developer",
            ".net",
            True,
        ),  # a term starting with punctuation is not anchored
    ],
)
def test_starts_a_word(ev, text, term, starts):
    assert ev.starts_a_word(text, term) is starts


# --- blocking_named ------------------------------------------------------------------------

_BLOCKED = (
    "0 jobs. The filter costing the most is `salary_min`; try without it.\nScope: ..."
)


def test_blocking_named_needs_the_result_to_name_it_and_the_answer_to_mention_it(ev):
    calls = [("search_jobs", {"keyword": "haskell"}, _BLOCKED, False)]

    def verdict(expect, answer):
        return ev.verify_blocking_named(expect, _transcript(ev, calls, answer), None)

    assert verdict(
        {"argument": None}, "None match; the salary floor is what blocks."
    ).passed
    assert verdict({"argument": "salary_min"}, "Drop salary_min to see some.").passed
    assert not verdict({"argument": "india_place"}, "Drop salary_min.").passed
    assert not verdict(
        {"argument": None}, "There are no Haskell jobs in Indore."
    ).passed
    # A ₹1 crore answer says "salary" whatever it concludes; that alone names no filter.
    assert not verdict(
        {"argument": None}, "No Haskell jobs in Indore at that salary."
    ).passed


def test_blocking_named_reads_the_company_form(ev):
    result = '0 jobs: no company name contains "razorpai". Try a shorter ...'
    transcript = _transcript(
        ev,
        [("search_jobs", {"company": "razorpai"}, result, False)],
        "No company matches.",
    )

    assert ev.verify_blocking_named({"argument": "company"}, transcript, None).passed


# --- operator_mix --------------------------------------------------------------------------

_SAMPLE = (
    "What the newest postings in DevOps (devops) ask for: counted over 270 distinct ...\n"
    'Companies with the most sampled postings: "Cognizant" (key '
    '"happydance:careers.cognizant.com") 8 · "Northwind Staffing LLC" (operator unverified) '
    '(key "lever:northwindstaffing") 3 · no company name (key "oracle:egud.fa.us2.oraclecloud.com") 2.\n'
)


def _sample(*companies):
    """A role_requirements result whose companies line lists ``companies`` as the tool does."""
    listed = " · ".join(
        f"{json.dumps(name)} (key {json.dumps(key)}) {count}"
        for name, key, count in companies
    )
    return f"Companies with the most sampled postings: {listed}.\n"


def test_sampled_companies_reads_names_tags_keys_and_the_counted_figure(ev):
    assert ev.sampled_companies(_SAMPLE) == [
        ("Cognizant", "happydance:careers.cognizant.com", 8),
        ("Northwind Staffing LLC", "lever:northwindstaffing", 3),
        ("", "oracle:egud.fa.us2.oraclecloud.com", 2),
    ]
    capped = _sample(
        ("DigitalXNode", "wp_job_openings:digitalxnode.com", "15 sampled, 8 counted")
    )
    assert ev.sampled_companies(capped)[0][2] == 8


def test_operator_mix_fails_a_curated_agency_or_board_it_was_not_asked_for(ev):
    """Round-4 critique P1-2 (ADR-0352): an agency led the DevOps sample."""

    def verdict(result, arguments=None):
        calls = [
            ("role_requirements", arguments or {"category": "devops"}, result, False)
        ]
        return ev.verify_operator_mix({}, _transcript(ev, calls, "..."), None)

    assert verdict(_SAMPLE).passed
    jobgether = _sample(("Jobgether", "lever:jobgether", 6), ("Cognizant", "x:y", 5))
    staffing = _sample(("Vrinda International", "zoho:vrindainternational", 5))
    assert "is aggregator" in verdict(jobgether).detail
    assert not verdict(jobgether).passed and not verdict(staffing).passed
    # Asked for by its operators, or by name, it is the user's own choice.
    every = ["employer", "services", "staffing", "aggregator"]
    assert verdict(jobgether, {"category": "devops", "operators": every}).passed
    assert verdict(jobgether, {"category": "devops", "company": "Jobgether"}).passed


def test_operator_mix_fails_one_company_counted_past_the_cap_unless_named(ev):
    over = _sample(("Cognizant", "happydance:careers.cognizant.com", 11))

    def verdict(arguments):
        calls = [("role_requirements", arguments, over, False)]
        return ev.verify_operator_mix({}, _transcript(ev, calls, "..."), None)

    assert "counted 11, over 8" in verdict({"category": "devops"}).detail
    assert verdict({"category": "devops", "company": "Cognizant"}).passed


def test_operator_mix_needs_a_sample_that_lists_its_companies(ev):
    none = ev.verify_operator_mix({}, _transcript(ev, [], "..."), None)
    assert not none.passed and "no successful role_requirements" in none.detail
    unlisted = [("role_requirements", {"query": "x"}, "No postings to count.", False)]
    assert not ev.verify_operator_mix({}, _transcript(ev, unlisted, "..."), None).passed


# --- mentions ------------------------------------------------------------------------------


def test_mentions_reads_alternatives_any_and_tool_results(ev):
    transcript = _transcript(
        ev,
        [("read_trends", {}, 'Companies: "Citi" (workday:citi/2) ...', False)],
        "The search matched any company name containing Citi; the trend read one directory company.",
    )
    expect = {
        "all": ["citi", ["contain", "substring"], ["directory", "largest company"]],
        "any": ["trend", "hiring"],
        "tool_results_all": ["workday:citi"],
    }

    assert ev.verify_mentions(expect, transcript, None).passed
    missing = ev.verify_mentions(
        {"all": [["substring", "prefix"]], "any": ["nope"]}, transcript, None
    )
    assert not missing.passed
    assert "substring" in missing.detail and "nope" in missing.detail


def test_judge_marks_a_verifier_that_could_not_read_the_space_as_error(ev):
    space = FakeSpace({SpaceRoute.COMPANIES_SUGGEST: InvalidRequest("q is required")})
    task = {"id": "t03", "verifier": "trend_sign", "expect": _T03}

    assert ev.judge(task, _transcript(ev, answer="more"), space) == (
        "error",
        "the verifier could not judge: q is required",
    )


# --- the tasks file, the held-out guard and --dry-run -------------------------------------


def test_the_iteration_tasks_use_known_verifiers_and_real_arguments(ev):
    tasks_file = json.loads(ev.ITERATION_TASKS.read_text(encoding="utf-8"))
    tasks = tasks_file["tasks"]
    arguments = {tool.name: set(tool.input_schema["properties"]) for tool in REGISTRY}

    assert [t["id"] for t in tasks] == [f"t{n:02d}" for n in range(1, len(tasks) + 1)]

    def used(check):
        """The verifier ``check`` names, and those its ``checks`` combine, at any depth."""
        nested = (check.get("expect") or {}).get("checks") or []
        return {check["verifier"]}.union(*(used(c) for c in nested))

    assert set().union(*(used(t) for t in tasks)) == set(ev.VERIFIERS)
    for task in tasks:
        assert task["prompt"].strip() and task["why"].strip()
        if task["verifier"] in ("search_args", "tool_args"):
            expect = task["expect"]
            named = set(expect.get("must", {}))
            named |= {
                n for alternative in expect.get("must_any", []) for n in alternative
            }
            assert named <= arguments[expect["tool"]], task["id"]
    assert len(tasks_file["heldout_sha256"]) == 64


#: A tool description's steer away from one argument path: "For visa sponsorship or relocation
#: use `work_authorization`, never `keyword`" (search_jobs).
_STEER = re.compile(r"For ([^.`]+?) use `(\w+)`, never `(\w+)`")


def _steers():
    """(tool, topic stems, the argument steered away from) for every steer a tool states."""
    found = []
    for tool in REGISTRY:
        for topics, _, avoided in _STEER.findall(tool.description):
            stems = {
                w[:5] for w in re.findall(r"[a-z]+", topics) if w not in ("or", "and")
            }
            found.append((tool.name, stems, avoided))
    return found


def _demands(check, steer):
    """Whether ``check`` passes only an answer that took the path ``steer`` steers away from:
    a tool_args check whose rules require the steered-away argument on a steered topic, an
    all_of holding such a check, or an any_of every path of which is one."""
    tool, stems, avoided = steer
    verifier, expect = check["verifier"], check.get("expect") or {}
    if verifier == "any_of":
        return all(_demands(c, steer) for c in expect["checks"])
    if verifier == "all_of":
        return any(_demands(c, steer) for c in expect["checks"])
    if verifier not in ("tool_args", "search_args") or expect.get("tool") != tool:
        return False

    def on_topic(rules):
        value = json.dumps((rules or {}).get(avoided), ensure_ascii=False).casefold()
        return avoided in (rules or {}) and any(stem in value for stem in stems)

    alternatives = expect.get("must_any") or []
    return (
        on_topic(expect.get("must"))
        or bool(alternatives)
        and all(on_topic(a) for a in alternatives)
    )


def test_no_task_demands_the_path_its_tools_description_steers_away_from(ev):
    """Round-4 critique P1-4: t27 demanded `keyword: relocation` while search_jobs' description
    says to use `work_authorization`, never `keyword`, for relocation, so an agent that followed
    the tool failed the task; t13 had the same fault in round 3 (SP7). A task may accept that
    path, as one of its any_of paths, but may not require it."""
    steers = _steers()
    assert [(t, a) for t, _, a in steers] == [("search_jobs", "keyword")]
    tasks = json.loads(ev.ITERATION_TASKS.read_text(encoding="utf-8"))["tasks"]
    for task in tasks:
        for steer in steers:
            assert not _demands(task, steer), task["id"]

    # The check itself: round 4's t27 fails it, and its any_of form passes.
    keyword_path = {
        "verifier": "tool_args",
        "expect": {
            "tool": "search_jobs",
            "must": {
                "country": "NL",
                "keyword": {"op": "contains", "value": "relocation"},
            },
        },
    }
    filter_path = {
        "verifier": "tool_args",
        "expect": {
            "tool": "search_jobs",
            "must": {"work_authorization": "offers_relocation"},
        },
    }
    assert _demands(keyword_path, steers[0])
    assert _demands(
        {"verifier": "all_of", "expect": {"checks": [keyword_path]}}, steers[0]
    )
    assert not _demands(
        {"verifier": "any_of", "expect": {"checks": [filter_path, keyword_path]}},
        steers[0],
    )


def test_the_sentences_the_harness_reads_are_the_servers_own(ev):
    # The server's sentences, as written in its source.
    package = _SCRIPT.parents[2] / "src" / "headstart" / "space_mcp"
    text = "\n".join(p.read_text(encoding="utf-8") for p in package.rglob("*.py"))

    for marker in ev._INFRASTRUCTURE_ERRORS:
        assert marker in text, marker
    assert "The filter costing the most is `" in text
    assert ev._COMPANY_BLOCKING in text


def _heldout(tmp_path, text):
    path = tmp_path / "heldout.json"
    path.write_text(text, encoding="utf-8")
    return path, hashlib.sha256(text.encode()).hexdigest()


def test_heldout_loads_only_the_sealed_file(ev, tmp_path):
    path, digest = _heldout(tmp_path, json.dumps({"tasks": [{"id": "h1"}]}))

    assert ev.load_heldout(path, digest) == [{"id": "h1"}]
    with pytest.raises(SystemExit, match="heldout_sha256 is null"):
        ev.load_heldout(path, None)


def test_heldout_refuses_a_changed_file_before_parsing_it(ev, tmp_path):
    path, _ = _heldout(tmp_path, "not json: the guard must refuse before parsing")

    with pytest.raises(SystemExit, match="not the sealed"):
        ev.load_heldout(path, "0" * 64)


def _seal(ev, monkeypatch, tmp_path, digest):
    """Point the iteration tasks file, whose hash is the only seal, at one sealing ``digest``."""
    sealed = tmp_path / "iteration_tasks.json"
    sealed.write_text(json.dumps({"tasks": [], "heldout_sha256": digest}), "utf-8")
    monkeypatch.setattr(ev, "ITERATION_TASKS", sealed)


def test_main_refuses_a_heldout_file_whose_hash_is_not_sealed(
    ev, monkeypatch, tmp_path
):
    path, _ = _heldout(tmp_path, json.dumps({"tasks": []}))
    _seal(ev, monkeypatch, tmp_path, "0" * 64)

    with pytest.raises(SystemExit, match="not the sealed"):
        ev.main(["--heldout", str(path), "--dry-run"])


def test_the_seal_cannot_come_from_a_tasks_file_the_caller_names(ev, tmp_path):
    path, digest = _heldout(tmp_path, json.dumps({"tasks": []}))
    forged = tmp_path / "forged.json"
    forged.write_text(json.dumps({"tasks": [], "heldout_sha256": digest}), "utf-8")

    with pytest.raises(SystemExit):  # argparse: --tasks and --heldout are exclusive
        ev.main(["--tasks", str(forged), "--heldout", str(path), "--dry-run"])


def _no_process(*args, **kwargs):
    raise AssertionError("--dry-run started a process")


def test_dry_run_prints_the_command_and_runs_nothing(ev, monkeypatch, capsys):
    monkeypatch.setattr(ev.subprocess, "Popen", _no_process)

    assert ev.main(["--dry-run"]) == 0

    out = capsys.readouterr().out
    assert "t01 [search_args]" in out and "t12 [blocking_named]" in out
    assert "claude -p 'Find me remote backend" in out
    assert "--strict-mcp-config" in out and "--tools ''" in out
    assert "nothing was run" in out


def test_the_run_registers_this_checkouts_server_and_nothing_else(ev):
    server = ev.mcp_config({})["mcpServers"]

    assert list(server) == ["headstart-space"]
    assert server["headstart-space"]["args"] == ["-m", "headstart.space_mcp"]
    assert server["headstart-space"]["env"] == {"PYTHONPATH": str(ev._ROOT / "src")}
    elsewhere = ev.mcp_config({"HEADSTART_SPACE_URL": "http://127.0.0.1:8765"})
    assert elsewhere["mcpServers"]["headstart-space"]["env"]["HEADSTART_SPACE_URL"] == (
        "${HEADSTART_SPACE_URL}"  # expanded by Claude Code, as the verifiers read it
    )


def test_the_http_mode_registers_the_hosted_endpoint_and_nothing_else(
    ev, monkeypatch, capsys
):
    url = "https://imposeidon-headstart-search.hf.space/mcp"
    assert ev.mcp_config({}, url) == {
        "mcpServers": {"headstart-space": {"type": "http", "url": url}}
    }
    monkeypatch.setattr(ev.subprocess, "Popen", _no_process)
    assert ev.main(["--dry-run", "--http", url]) == 0
    out = capsys.readouterr().out
    assert f'"url": "{url}"' in out and "headstart.space_mcp" not in out


class _FakeClaude:
    """A `subprocess.Popen` stand-in that prints ``lines`` and records the env it was given."""

    def __init__(self, lines):
        self.lines = lines
        self.envs = []

    def __call__(self, argv, **kwargs):
        self.envs.append(kwargs["env"])
        self.stdout = iter(line + "\n" for line in self.lines)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def wait(self):
        return 0

    def kill(self):
        pass


def test_an_http_run_waits_for_the_server_and_one_left_pending_is_not_judged(
    ev, monkeypatch, tmp_path
):
    """Round-2 critique P1-8: Claude Code's -p left the hosted server "pending", so every task
    failed with 0 calls and was scored as the model's miss."""
    claude = _FakeClaude(_init_line("pending"))
    monkeypatch.setattr(ev.subprocess, "Popen", claude)
    monkeypatch.setattr(ev, "_ROOT", tmp_path)
    task = {"id": "t03", "prompt": "p", "verifier": "trend_sign", "expect": _T03}
    url = "https://imposeidon-headstart-search.hf.space/mcp"

    def space():
        raise AssertionError("a run with no tools reached the verifier")

    record = ev.run_task(task, {"HOME": "/x"}, tmp_path / "run", space, url, 2)

    assert record["verdict"] == "error" and "pending" in record["detail"]
    assert record["server_status"] == "pending" and record["repeat"] == 2
    assert record["transcript"].endswith("run_t03_r2_transcript.jsonl")
    assert claude.envs[-1] == {
        "HOME": "/x",
        "MCP_TIMEOUT": "60000",
        "MCP_CONNECTION_NONBLOCKING": "false",
    }
    # Round-4 critique P1-4: a slow network left 44 of 123 runs pending; every run waits up to
    # 60 s for its server, unless the caller set its own wait.
    assert ev.run_env({"HOME": "/x"}, None) == {"HOME": "/x", "MCP_TIMEOUT": "60000"}
    assert ev.run_env({"MCP_TIMEOUT": "5000"}, None) == {"MCP_TIMEOUT": "5000"}


def _record(task_id, verdict):
    return {
        "id": task_id,
        "verdict": verdict,
        "tool_calls": 1,
        "largest_tool_result_chars": 10,
        "refusals": 0,
        "refusals_corrected": 0,
    }


def test_the_summary_scores_only_judged_runs_and_names_the_rest_first(ev):
    """Round-2 critique P1-8: a run whose server never connected says nothing about the model,
    so it is not counted wrong; it is reported apart."""
    records = [_record("t01", "pass"), _record("t02", "fail"), _record("t05", "error")]
    records[2]["tool_calls"] = 0

    lines = ev.summary(records)

    assert lines[0] == "not judged: 1 of 3 (t05) — MISSED"
    assert lines[1] == "correct: 1 of 2 judged (bar: at most one wrong) — met"
    assert lines[2].startswith("median tool calls: 1 ")  # the unjudged 0 is not counted
    assert ev.summary([_record("t01", "pass")])[0] == "not judged: 0 of 1 — met"


def test_tally_counts_each_tasks_passes_across_repeats(ev):
    passes = [
        [_record("t01", "pass"), _record("t02", "fail")],
        [_record("t01", "pass"), _record("t02", "pass")],
        [_record("t01", "error"), _record("t02", "pass")],
    ]

    assert (
        ev.tally(passes)
        == [
            "t01: 2 of 2 judged passed (pass, pass, error)",  # the unjudged run is not scored
            "t02: 2 of 3 judged passed (fail, pass, pass)",
        ]
    )


def test_a_summary_with_nothing_judged_meets_no_bar(ev):
    lines = ev.summary([_record("t01", "error"), _record("t02", "error")])

    assert all(line.endswith("MISSED") for line in lines), lines


def test_the_run_allows_every_registered_tool_and_nothing_else(ev):
    argv = ev.command("a prompt", "mcp.json")
    allowed = argv[argv.index("--allowedTools") + 1].split(",")

    assert allowed == [f"mcp__headstart-space__{tool.name}" for tool in REGISTRY]
    assert argv[argv.index("--tools") + 1] == ""  # no built-in tool


def test_dry_run_of_a_heldout_file_keeps_its_prompts_sealed(
    ev, monkeypatch, capsys, tmp_path
):
    monkeypatch.setattr(ev.subprocess, "Popen", _no_process)
    task = {
        "id": "h1",
        "prompt": "a sealed question",
        "verifier": "mentions",
        "expect": {},
    }
    path, digest = _heldout(tmp_path, json.dumps({"tasks": [task]}))
    _seal(ev, monkeypatch, tmp_path, digest)

    assert ev.main(["--heldout", str(path), "--dry-run"]) == 0

    out = capsys.readouterr().out
    assert "h1 [mentions]" in out and "<sealed prompt>" in out
    assert "a sealed question" not in out


# --- a connected server that lists no tools (round-3 critique P1-4, ADR-0334) ---------------


def test_a_connected_server_that_listed_no_tools_is_not_judged(ev):
    """Round 3's t24 r2: the Space refused `tools/list` for its rate limit, the init event said
    `connected` with `tools: []`, and the model made figures up. That says nothing about the
    model or the tools."""
    empty = ev.parse(_init_line("connected"))
    listed = ev.parse(_transcript_lines())

    assert empty.server_status == "connected" and empty.tools == []
    assert "listed none of its tools" in ev.why_not_connected(empty)
    assert listed.tools == ["mcp__headstart-space__search_jobs"]
    assert ev.why_not_connected(listed) is None


def test_a_run_with_no_tools_is_scored_error_not_fail(ev, monkeypatch, tmp_path):
    lines = _init_line("connected") + [
        json.dumps(
            {"type": "result", "subtype": "success", "result": "~1,900 postings"}
        )
    ]

    class _Proc:
        def __init__(self, *args, **kwargs):
            self.stdout = iter(line + "\n" for line in lines)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def wait(self):
            return 0

        def kill(self):
            pass

    monkeypatch.setattr(ev.subprocess, "Popen", _Proc)
    monkeypatch.setattr(
        ev, "_ROOT", tmp_path
    )  # the record names its transcript from here
    task = {
        "id": "t24",
        "prompt": "p",
        "verifier": "mentions",
        "expect": {"all": ["x"]},
    }
    record = ev.run_task(task, {}, tmp_path / "run", lambda: FakeSpace({}))

    assert record["verdict"] == "error" and "listed none" in record["detail"]


# --- the new verifier shapes (ADR-0334) -------------------------------------------------------


def test_all_of_passes_only_when_every_check_passes(ev):
    expect = {
        "checks": [
            {"verifier": "mentions", "expect": {"all": ["Citi"]}},
            {"verifier": "mentions", "expect": {"all": ["37"]}},
        ]
    }
    assert ev.verify_all_of(expect, _transcript(ev, answer="Citi has 37."), None).passed
    failed = ev.verify_all_of(expect, _transcript(ev, answer="Citi has 12."), None)
    assert not failed.passed and failed.detail.startswith("check 2 (mentions)")


def test_any_of_passes_on_the_first_path_that_passes_and_names_every_miss(ev):
    expect = {
        "checks": [
            {"verifier": "mentions", "expect": {"all": ["directory"]}},
            {"verifier": "mentions", "expect": {"all": ["citi"]}},
        ]
    }
    passed = ev.verify_any_of(expect, _transcript(ev, answer="Citi has 37."), None)
    failed = ev.verify_any_of(expect, _transcript(ev, answer="Nothing."), None)

    assert passed.passed and passed.detail.startswith("path 2 (mentions)")
    assert not failed.passed
    assert "path 1 (mentions): answer lacks 'directory'" in failed.detail
    assert "path 2 (mentions): answer lacks 'citi'" in failed.detail


def test_answer_carries_needs_the_figure_the_tool_gave_whole(ev):
    expect = {"answer_carries": [["counted over ([\\d,]+) distinct postings"]]}
    result = ("role_requirements", {}, "…counted over 1,271 distinct postings…", False)

    def verdict(answer):
        return ev.verify_mentions(expect, _transcript(ev, [result], answer), None)

    assert verdict("Based on 1,271 postings.").passed
    assert verdict("Based on 1271 postings.").passed
    assert not verdict(
        "Based on 271 postings."
    ).passed  # a part of the figure is not it
    assert not verdict("Based on 300 postings.").passed
    none = ev.verify_mentions(expect, _transcript(ev, answer="1,271"), None)
    assert not none.passed and "tool results match none" in none.detail


def test_answer_carries_reads_a_name_case_blind(ev):
    expect = {"answer_carries": ['"([^"]+)" · key oracle:x']}
    result = ("find_company", {}, ' 1. "Kotak" · key oracle:x · 256', False)

    assert ev.verify_mentions(expect, _transcript(ev, [result], "KOTAK"), None).passed


def test_blocking_named_by_the_value_sent_only_when_the_task_allows_it(ev):
    result = (
        "search_jobs",
        {"india_place": "indore", "salary_min": 10000000},
        "0 jobs. The filter costing the most is `india_place`; try without it.",
        False,
    )
    transcript = _transcript(ev, [result], "No Haskell jobs in Indore at any pay.")

    assert not ev.verify_blocking_named({"argument": None}, transcript, None).passed
    by_value = ev.verify_blocking_named(
        {"argument": None, "value_names_it": True}, transcript, None
    )
    assert by_value.passed and "'indore'" in by_value.detail


def test_tool_args_answer_any_asks_the_answer_to_say_what_the_path_obliges(ev):
    expect = {
        "tool": "search_jobs",
        "must": {"sort": "salary"},
        "answer_any": ["closest", "2,000"],
    }
    call = ("search_jobs", {"query": "staff", "sort": "salary"}, "5 jobs", False)

    said = _transcript(ev, [call], "Among the 2,000 closest matches: …")
    unsaid = _transcript(ev, [call], "The top-paying staff roles: …")
    assert ev.verify_tool_args(expect, said, None).passed
    verdict = ev.verify_tool_args(expect, unsaid, None)
    assert not verdict.passed and "says none of" in verdict.detail


def test_only_takes_several_task_ids(ev, monkeypatch, capsys):
    monkeypatch.setattr(ev.subprocess, "Popen", _no_process)

    assert ev.main(["--only", "t07,t12", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "t07 [any_of]" in out and "t12 [blocking_named]" in out
    assert "2 tasks; nothing was run." in out
    with pytest.raises(SystemExit, match="no task t99"):
        ev.main(["--only", "t07,t99", "--dry-run"])


# --- country_split (ADR-0355) ----------------------------------------------------------------

_T37 = {
    "countries": ["IN", "DE"],
    "must_any": [
        {"category": "ai-ml-data-science"},
        {"keyword": {"op": "contains", "value": "ai"}},
    ],
}


class _CountrySpace:
    """`/facets` answering each country's total, recording what each read sent."""

    def __init__(self, totals):
        self.totals = totals
        self.asked = []

    def read(self, route, params=()):
        assert route == SpaceRoute.FACETS
        self.asked.append(list(params))
        return {"total": self.totals[dict(params)["country"]]}


def test_country_split_needs_each_countrys_facets_total_beside_its_name(ev):
    space = _CountrySpace({"IN": 12345, "DE": 2345})
    full = ("search_jobs", {"category": "ai-ml-data-science", "detail": "full"}, "…")
    passed = ev.verify_country_split(
        _T37,
        _transcript(ev, [full], "India has 12,345 open AI roles; Germany 2345."),
        space,
    )
    assert passed.passed, passed.detail
    # Each read is the call's filters with that country as its only place, a total alone.
    sent = [dict(params) for params in space.asked]
    assert [s["country"] for s in sent] == ["IN", "DE"]
    assert all(s["family"] == "ai-ml-data-science" for s in sent)
    assert all(s["counts"] == "total" and "page" not in s for s in sent)


def test_country_split_fails_a_figure_that_is_not_the_countrys_own(ev):
    space = _CountrySpace({"IN": 12345, "DE": 2345})
    full = ("search_jobs", {"category": "ai-ml-data-science", "detail": "full"}, "…")
    failed = ev.verify_country_split(
        _T37, _transcript(ev, [full], "India 12,345 and Germany 2,300."), space
    )
    assert not failed.passed and "Germany is 2,345" in failed.detail


def test_country_split_passes_a_search_per_country_with_its_place_replaced(ev):
    """One search per country is a right path too: each call's own country is replaced."""
    space = _CountrySpace({"IN": 900, "DE": 80})
    calls = [
        ("search_jobs", {"keyword": "ai", "country": "IN"}, "…"),
        ("search_jobs", {"keyword": "ai", "india_place": "pune"}, "…"),
    ]
    verdict = ev.verify_country_split(
        _T37, _transcript(ev, calls, "900 in India against 80 in Germany."), space
    )
    assert verdict.passed, verdict.detail
    assert all("india" not in dict(p) and dict(p)["kw"] == "ai" for p in space.asked)


def test_country_split_needs_a_search_for_the_category(ev):
    space = _CountrySpace({"IN": 1, "DE": 1})
    anything = ("search_jobs", {"query": "ai engineer", "detail": "full"}, "…")
    failed = ev.verify_country_split(
        _T37, _transcript(ev, [anything], "India 1, Germany 1."), space
    )
    assert not failed.passed and "no successful search_jobs call meets" in failed.detail


# --- the verifier self-test: recorded tool results (ADR-0334) ---------------------------------

_RECORDED = (
    Path(__file__).resolve().parent / "fixtures" / "space_mcp_eval_recorded_calls.json"
)
_RECORDED_RUNS = [
    (task_id, n, run)
    for task_id, runs in json.loads(_RECORDED.read_text(encoding="utf-8"))[
        "runs"
    ].items()
    for n, run in enumerate(runs, 1)
]


def _replay_fetch(replies):
    """The Space as it answered when the fixture was recorded, stamped with this checkout's
    agent contract; a URL it did not read is a failure naming the re-record script."""
    from headstart.space_mcp.space_client import AGENT_API, Reply

    def fetch(url, headers, timeout_s):
        if url not in replies:
            raise AssertionError(
                f"no recorded reply for {url}: a tool reads differently now; run "
                "scripts/eval/record_space_mcp_eval_calls.py"
            )
        reply = replies[url]
        return Reply(
            reply["status"],
            {**reply["headers"], "x-headstart": f"app; agent-api={AGENT_API}"},
            reply["body"].encode("utf-8"),
        )

    return fetch


@pytest.mark.parametrize(
    "task_id, n, run",
    _RECORDED_RUNS,
    ids=[f"{task_id}-run{n}" for task_id, n, _ in _RECORDED_RUNS],
)
def test_a_right_run_passes_its_tasks_verifier_on_recorded_tool_results(
    ev, task_id, n, run
):
    """Round-3 critique P1-6: four verifiers went stale when the tools' output changed, and
    only the next hosted eval showed it. Each run here is a right answer's calls, answered by
    today's tools from the Space's recorded replies, and its verifier must pass it: a tool
    whose output change breaks a verifier fails the PR that makes it."""
    from datetime import datetime

    fixture = json.loads(_RECORDED.read_text(encoding="utf-8"))
    tasks = {t["id"]: t for t in json.loads(ev.ITERATION_TASKS.read_text())["tasks"]}
    fetch = _replay_fetch(fixture["replies"])
    with ev.tools_clock_at(datetime.fromisoformat(fixture["recorded_at"])):
        transcript = ev.replayed(run, fetch)
        outcome, detail = ev.judge(
            tasks[task_id], transcript, ev.SpaceClient(base=ev.SPACE_URL, fetch=fetch)
        )

    assert ev.why_not_connected(transcript) is None
    assert not any(call.is_error for call in transcript.calls), transcript.calls
    assert outcome == "pass", detail


def test_the_recording_covers_every_task_whose_verifier_reads_tool_results(ev):
    """A task that judges the tools' words is in the self-test, so its words cannot drift from
    the verifier unseen."""

    def reads_results(task):
        checks = (task.get("expect") or {}).get("checks") or [task]
        return any(
            c["verifier"] in ("blocking_named", "title_keyword_rows", "operator_mix")
            or {"tool_results_all", "answer_carries", "answer_any"}
            & set(c.get("expect") or {})
            for c in checks
        )

    tasks = json.loads(ev.ITERATION_TASKS.read_text())["tasks"]
    recorded = {task_id for task_id, _, _ in _RECORDED_RUNS}
    wanted = {t["id"] for t in tasks if reads_results(t)}
    assert wanted <= recorded, wanted - recorded
