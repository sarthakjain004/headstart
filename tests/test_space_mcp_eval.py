"""Tests for scripts/eval/space_mcp_eval.py: the stream-json parser, every verifier kind, the
held-out hash guard and --dry-run. Nothing here starts Claude Code or reaches the Space."""

from __future__ import annotations

import hashlib
import importlib.util
import json
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

    assert connected.server_status == "connected" and ev.unconnected(connected) is None
    assert pending.server_status == "pending"
    assert "pending at the run's start" in ev.unconnected(pending)
    assert "not named" in ev.unconnected(ev.parse([]))


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
    return _transcript(ev, [("hiring_now", {}, _HIRING_NOW, False)], answer)


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
    assert {t["verifier"] for t in tasks} == set(ev.VERIFIERS)
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


def test_the_sentences_the_harness_reads_are_the_servers_own(ev):
    # The server's sentences, as written in its source.
    package = _SCRIPT.parents[2] / "src" / "headstart" / "space_mcp"
    text = "\n".join(p.read_text(encoding="utf-8") for p in package.rglob("*.py"))

    for marker in ev._INFRASTRUCTURE_ERRORS:
        assert marker in text, marker
    assert "The filter costing the most is `" in text
    assert ev._COMPANY_BLOCKING in text
    assert ev._HOT_FLAG in text  # how hiring_now marks a row it disowns


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
    assert claude.envs[-1] == {"HOME": "/x", "MCP_CONNECTION_NONBLOCKING": "false"}
    assert ev.run_env({"HOME": "/x"}, None) == {"HOME": "/x"}  # stdio: unchanged


def _record(task_id, verdict):
    return {
        "id": task_id,
        "verdict": verdict,
        "tool_calls": 1,
        "largest_tool_result_chars": 10,
        "refusals": 0,
        "refusals_corrected": 0,
    }


def test_the_summary_names_the_tasks_it_could_not_judge_first(ev):
    lines = ev.summary([_record("t01", "pass"), _record("t05", "error")])

    assert lines[0] == "not judged: 1 of 2 (t05) — MISSED"
    assert lines[1].startswith("correct: 1 of 2")
    assert ev.summary([_record("t01", "pass")])[0] == "not judged: 0 of 1 — met"


def test_tally_counts_each_tasks_passes_across_repeats(ev):
    passes = [
        [_record("t01", "pass"), _record("t02", "fail")],
        [_record("t01", "pass"), _record("t02", "pass")],
        [_record("t01", "error"), _record("t02", "pass")],
    ]

    assert ev.tally(passes) == [
        "t01: 2 of 3 passed (pass, pass, error)",
        "t02: 2 of 3 passed (fail, pass, pass)",
    ]


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
