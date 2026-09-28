"""Tests for scripts/eval/space_mcp_eval.py: the stream-json parser, every verifier kind, the
held-out hash guard and --dry-run. Nothing here starts Claude Code or reaches the Space."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from headstart.space_mcp.server import build_server
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

    assert [t["id"] for t in tasks] == [f"t{n:02d}" for n in range(1, 13)]
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
    # The server's sentences, as written in its source, plus the one it builds without a token.
    package = _SCRIPT.parents[2] / "src" / "headstart" / "space_mcp"
    text = "\n".join(p.read_text(encoding="utf-8") for p in package.rglob("*.py"))
    text += str(build_server({}).unconfigured)

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
    monkeypatch.delenv("HEADSTART_AGENT_TOKEN", raising=False)

    assert ev.main(["--dry-run"]) == 0

    out = capsys.readouterr().out
    assert "t01 [search_args]" in out and "t12 [blocking_named]" in out
    assert "claude -p 'Find me remote backend" in out
    assert "--strict-mcp-config" in out and "--tools ''" in out
    assert '"HEADSTART_AGENT_TOKEN": "${HEADSTART_AGENT_TOKEN}"' in out
    assert "nothing was run" in out


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


def test_a_live_run_needs_the_token(ev, monkeypatch):
    monkeypatch.setattr(ev.subprocess, "Popen", _no_process)
    monkeypatch.delenv("HEADSTART_AGENT_TOKEN", raising=False)

    with pytest.raises(SystemExit, match="HEADSTART_AGENT_TOKEN is not set"):
        ev.main(["--only", "t01"])
