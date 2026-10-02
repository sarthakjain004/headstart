#!/usr/bin/env python3
"""Run the Space MCP server's evaluation tasks with Claude Code as the client (plan §9).

Each task is one ``claude -p`` run in an empty scratch directory, with the ``headstart-space``
server as the only MCP server, no built-in tools, and only the server's registered tools
allowed. The run's stream-json transcript is saved line by line as it arrives, then parsed into
tool calls (name and arguments), tool results (their characters and whether they were errors)
and the final answer, and judged by the task's verifier. ``trend_sign`` and ``hot_top`` re-read
the Space's public read routes themselves, as the server does, so they check the answer against
the Space's own figures rather than against what the agent was told; ``trend_sign`` also needs
the answer to say when opened and closed cover only part of the window, and ``hot_top`` fails an
answer that leads with a row hiring_now flagged, or names a row whose opened was mostly found
late without saying so. ``tool_args`` (``search_args`` in the brief's fixed schema) checks the
arguments instead and trusts the Space to apply them: ``strict=1`` makes
it refuse any it would drop. ``title_keyword_rows`` checks the arguments, then reads the rows that
call returned back from ``/job`` and needs the keyword where a word starts in every title, the
keyword's own rule (ADR-0299, ADR-0325). ``sponsorship_polarity`` reads back every row the calls
listed and fails an answer naming a job that does not offer sponsorship, judged apart from the
Space's rules: by a person's label where the job has one, else by a negation check of its own
(ADR-0333). ``operator_mix`` reads each role_requirements sample's companies and fails one that
counts a curated staffing firm or job board it was not asked for, or more of one company's
postings than the cap (ADR-0352). ``country_split`` reads `/facets` once per country the task
names, with the filters of a search_jobs call the agent made, and needs each total stated
beside that country's name (ADR-0355). ``page_companies`` needs a search page, and the answer, to
name at least so many companies (ADR-0365). ``found_late_share`` reads `/trends` for a company
whose postings opened were mostly found late and needs the answer to say so and name how many;
its task is retired while the live data no longer shows that (ADR-0369). ``all_of`` and
``any_of`` combine checks.
A run whose server was not connected at its start is not judged: it is an error, left out of the
summary's scores and named on a line of its own, first.

It invokes Claude Code, the MCP client under test, not an LLM API from project code, so it does
not route through the llm-router. The plan lists that reading for the owner (§12, item 8).

One JSON line per task goes to ``experiment/space-mcp-eval/artifacts/<stamp>_<set>_results.jsonl``
as each task finishes, with its transcript and stderr beside it, and one verdict line is printed.
The end prints §9's bar: correct count, median tool calls, the largest tool result (flagged past
40,000 characters, about 10,000 tokens) and tool refusals corrected within one call. §9's dated
summary under ``docs/mcp/`` is written by hand from the results file.

A held-out file (``--heldout``) is read only after its sha256 matches the ``heldout_sha256`` the
committed iteration tasks file seals, so tuning against the iteration tasks cannot see it.

Run (a live run needs only the network and a signed-in ``claude``):
  python scripts/eval/space_mcp_eval.py
  python scripts/eval/space_mcp_eval.py --dry-run
  python scripts/eval/space_mcp_eval.py --only t03
  python scripts/eval/space_mcp_eval.py --only t07,t12 --http <url> --repeat 2
  python scripts/eval/space_mcp_eval.py --heldout <sealed file>
  python scripts/eval/space_mcp_eval.py --http https://imposeidon-headstart-search.hf.space/mcp
  python scripts/eval/space_mcp_eval.py --repeat 3
``HEADSTART_SPACE_URL``, when set, points both the server and the verifiers at another Space.
``--http`` registers the hosted Streamable HTTP endpoint (ADR-0267) in place of the stdio server,
and runs ``claude`` with ``MCP_CONNECTION_NONBLOCKING=false`` and ``MCP_CONNECT_TIMEOUT_MS``
so it waits for that server to connect; the verifiers still read ``HEADSTART_SPACE_URL`` or the deployed Space. ``--repeat N``
runs the set N times and tallies each task.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
from headstart.boards import board_operator
from headstart.boards.board_operator import OPERATORS
from headstart.mcp_protocol import tool_arguments
from headstart.mcp_protocol.messages import ToolFailure
from headstart.search_filters import country_filter
from headstart.serving import per_company_cap
from headstart.space_mcp import answer_date, company_scope, search_arguments
from headstart.space_mcp.server import BY_NAME, NAME, URL_VAR
from headstart.space_mcp.server import call as call_tool
from headstart.space_mcp.space_client import (
    SPACE_URL,
    Fetch,
    SpaceClient,
    SpaceError,
    SpaceRoute,
)
from headstart.space_mcp.space_tool import ANSWER_CEILING_CHARS
from headstart.space_mcp.tools import (
    REGISTRY,
    company_profile,
    get_job,
    hiring_now,
    read_trends,
    role_requirements,
    search_jobs,
)
from headstart.trends.found_late import MIN_OPENED as FOUND_LATE_MIN_OPENED

ITERATION_TASKS = _ROOT / "scripts" / "eval" / "space_mcp_eval_tasks.json"
ARTIFACTS = _ROOT / "experiment" / "space-mcp-eval" / "artifacts"

TOOL_PREFIX = f"mcp__{NAME}__"

#: §9's "no result over 10,000 tokens": the ceiling every tool's answer is held to.
LARGE_RESULT_CHARS = ANSWER_CEILING_CHARS
#: §9's bar for median tool calls.
MEDIAN_CALLS_BAR = 3
#: A run is killed past this: each tool call waits at most 45 s for the Space (ADR-0276).
TASK_TIMEOUT_S = 600

#: Tool errors that are the Space or the setup failing, not the tool refusing the arguments the
#: agent sent (`space_client`'s and the server's own sentences). Every other tool error is a
#: refusal the agent should correct in its next call.
_INFRASTRUCTURE_ERRORS = (
    "The HeadStart Space is starting",
    "is older than this server",
    "failed on this request",
    "stopped answering",
    "with non-JSON",
    "requests this minute",
    "Not on this deployment yet",
    "did not answer within",
    "still finishing earlier searches",
)


@dataclass
class ToolCall:
    """One tool call in a transcript, with its result once the transcript carries one."""

    name: str
    arguments: dict[str, Any]
    result: str | None = None
    is_error: bool = False

    @property
    def succeeded(self) -> bool:
        return self.result is not None and not self.is_error

    @property
    def refused(self) -> bool:
        return self.is_error and not any(
            marker in (self.result or "") for marker in _INFRASTRUCTURE_ERRORS
        )


@dataclass
class Transcript:
    """What one ``claude -p --output-format stream-json`` run did."""

    calls: list[ToolCall] = field(default_factory=list)
    final_answer: str = ""
    model: str | None = None
    cost_usd: float | None = None
    #: Why the run did not end in a successful result, or None when it did.
    run_error: str | None = None
    #: The server's status in the run's init event ("connected", "pending", "failed"), or None
    #: when the init event did not name it.
    server_status: str | None = None
    #: The tools the init event lists, each as the model calls it (``mcp__<server>__<tool>``).
    tools: list[str] = field(default_factory=list)


def _text(content: Any) -> str:
    """A tool result's text: stream-json carries it as a string or as a list of blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def parse(lines: Iterable[str]) -> Transcript:
    """The tool calls, results and final answer in a stream-json transcript."""
    transcript = Transcript()
    by_id: dict[str, ToolCall] = {}
    last_text = ""
    ended = False
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            transcript.model = event.get("model")
            transcript.tools = [
                tool for tool in event.get("tools") or [] if isinstance(tool, str)
            ]
            transcript.server_status = next(
                (
                    server.get("status")
                    for server in event.get("mcp_servers") or []
                    if server.get("name") == NAME
                ),
                None,
            )
        elif kind in ("assistant", "user"):
            content = (event.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else ():
                if block.get("type") == "tool_use":
                    name = block.get("name", "").removeprefix(TOOL_PREFIX)
                    call = ToolCall(name, block.get("input") or {})
                    by_id[block.get("id")] = call
                    transcript.calls.append(call)
                elif (
                    block.get("type") == "tool_result"
                    and block.get("tool_use_id") in by_id
                ):
                    call = by_id[block["tool_use_id"]]
                    call.result = _text(block.get("content"))
                    call.is_error = bool(block.get("is_error"))
                elif block.get("type") == "text" and kind == "assistant":
                    last_text = block.get("text", "")
        elif kind == "result":
            ended = True
            transcript.cost_usd = event.get("total_cost_usd")
            if isinstance(event.get("result"), str):
                transcript.final_answer = event["result"]
            if event.get("is_error") or event.get("subtype") != "success":
                transcript.run_error = str(event.get("subtype") or "error")
    if not ended:
        transcript.run_error = "no result event: the run was cut off"
    transcript.final_answer = transcript.final_answer or last_text
    return transcript


@dataclass(frozen=True)
class Verdict:
    passed: bool
    detail: str


class Space(Protocol):
    """What a verifier reads the Space through: `SpaceClient`, or a fake in tests."""

    def read(
        self, route: SpaceRoute, params: Sequence[tuple[str, str]] = ()
    ) -> Any: ...


Verifier = Callable[[dict[str, Any], Transcript, Space], Verdict]


def why_not_connected(transcript: Transcript) -> str | None:
    """Why the model had no tools in this run, or None when the server was connected at its
    start and listed its tools. Such a run says nothing about the model or the tools, so it is
    not judged. A server can connect and still list none: round 3's t24 r2 had its `tools/list`
    refused by the Space's rate limit, and the model then made HeadStart figures up (ADR-0334)."""
    if transcript.server_status != "connected":
        return (
            f"the {NAME} server was {transcript.server_status or 'not named'} at the run's "
            "start, so the model had no tools: not judged"
        )
    if not any(tool.startswith(TOOL_PREFIX) for tool in transcript.tools):
        return (
            f"the {NAME} server connected but the run's start listed none of its tools, so "
            "the model had none: not judged"
        )
    return None


def _found_at(text: str, term: str | list[str]) -> int | None:
    """Where ``term`` first appears in ``text``, case-blind, or None; a list is alternatives, and
    the earliest of them counts."""
    terms = term if isinstance(term, list) else [term]
    folded = text.casefold()
    found = [at for t in terms if t and (at := folded.find(t.casefold())) >= 0]
    return min(found, default=None)


def _found(text: str, term: str | list[str]) -> bool:
    """``term`` in ``text``, case-blind; a list is alternatives, any one of which will do."""
    return _found_at(text, term) is not None


# --- tool_args (and search_args, its name in the brief's fixed schema) ---------------------


def _same(value: Any, want: Any) -> bool:
    if isinstance(value, str) and isinstance(want, str):
        return value.strip().casefold() == want.strip().casefold()
    return value == want


def _contains(value: Any, want: str) -> bool:
    if isinstance(value, str):
        return _found(value, want)
    if isinstance(value, list):
        return any(isinstance(v, str) and _found(v, want) for v in value)
    return False


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def holds(value: Any, rule: Any) -> bool:
    """Whether an argument's ``value`` (None when absent) satisfies one ``must`` rule: a plain
    value it must equal (strings case-blind), or ``{"op", "value"}``."""
    if not (isinstance(rule, dict) and "op" in rule):
        return _same(value, rule)
    op, want = rule["op"], rule["value"]
    if op == ">=":
        return _number(value) and value >= want
    if op == "<=":
        return _number(value) and value <= want
    if op == "in":
        return any(_same(value, option) for option in want)
    if op == "contains":
        return all(
            _contains(value, w) for w in (want if isinstance(want, list) else [want])
        )
    raise ValueError(f"unknown op {op!r}")


def _misses(expect: dict[str, Any], arguments: dict[str, Any]) -> list[str]:
    misses = [
        f"{name}={arguments.get(name)!r} fails {rule!r}"
        for name, rule in (expect.get("must") or {}).items()
        if not holds(arguments.get(name), rule)
    ]
    alternatives = expect.get("must_any") or []
    if alternatives and not any(
        all(holds(arguments.get(n), r) for n, r in alternative.items())
        for alternative in alternatives
    ):
        misses.append(f"none of must_any {alternatives!r} holds")
    query = arguments.get("query") or ""
    misses += [
        f"query {query!r} contains {word!r}"
        for word in expect.get("query_must_not_contain") or []
        if _found(query, word)
    ]
    return misses


def _call_meeting_the_rules(
    expect: dict[str, Any], transcript: Transcript
) -> tuple[ToolCall | None, Verdict]:
    """The first successful call of ``expect["tool"]`` whose arguments meet every rule, and the
    tool_args verdict on the transcript."""
    tool = expect.get("tool") or "search_jobs"
    schema = BY_NAME[tool].input_schema
    calls = [c for c in transcript.calls if c.name == tool and c.succeeded]
    if not calls:
        return None, Verdict(False, f"no successful {tool} call")
    judged = [
        (call, _misses(expect, tool_arguments.with_defaults(schema, call.arguments)))
        for call in calls
    ]
    for call, misses in judged:
        if not misses:
            return call, Verdict(
                True, f"{tool} {json.dumps(call.arguments, ensure_ascii=False)}"
            )
    call, misses = min(judged, key=lambda pair: len(pair[1]))
    return None, Verdict(
        False,
        f"no {tool} call meets every rule; closest "
        f"{json.dumps(call.arguments, ensure_ascii=False)}: {'; '.join(misses)}",
    )


def verify_tool_args(
    expect: dict[str, Any], transcript: Transcript, space: Space
) -> Verdict:
    """One successful call of ``expect["tool"]`` (search_jobs by default) whose arguments meet
    every ``must`` rule, one ``must_any`` alternative when given, and ``query_must_not_contain``.
    An argument the call left out is judged at its schema default, as the server reads it.
    ``answer_any``, when given, is terms one of which the final answer must say: what a path
    those arguments take obliges the answer to tell the user (ADR-0334)."""
    call, verdict = _call_meeting_the_rules(expect, transcript)
    terms = expect.get("answer_any")
    if call is None or not terms or _found(transcript.final_answer, terms):
        return verdict
    return Verdict(False, f"{verdict.detail}; the answer says none of {terms!r}")


# --- title_keyword_rows --------------------------------------------------------------------

#: A row's id as search_jobs prints it: `id "greenhouse:stripe:123"`, JSON-quoted.
_ROW_ID = re.compile(r'\bid ("(?:[^"\\]|\\.)*")')


def _row_ids(result: str) -> list[str]:
    return list(dict.fromkeys(json.loads(quoted) for quoted in _ROW_ID.findall(result)))


def starts_a_word(text: str, term: str) -> bool:
    """``term`` in ``text`` case-blind where a word starts, ADR-0299's keyword rule restated
    rather than imported: after the text's start or a character that is not a letter or digit,
    when the term begins with one, and with its end not anchored. So "rust" is in "Rust-based" and
    "Rustacean", not in "Trust"; "ml" is not in "HTML"."""
    anchor = r"(?<![a-z0-9])" if re.match(r"[a-z0-9]", term, re.IGNORECASE) else ""
    return re.search(anchor + re.escape(term), text, re.IGNORECASE) is not None


def verify_title_keyword_rows(
    expect: dict[str, Any], transcript: Transcript, space: Space
) -> Verdict:
    """tool_args's check, then the truth behind it: every row the call that meets it returned
    has ``expect["word"]`` in its title where a word starts. The rows are read back from the
    Space's `/job` by the ids the call printed, so the check does not trust the tool's own
    rendering."""
    call, verdict = _call_meeting_the_rules(expect, transcript)
    if call is None:
        return verdict
    ids = _row_ids(call.result or "")
    if not ids:
        return Verdict(False, f"{verdict.detail}; its result lists no row ids")
    titles: dict[str, str] = {}
    for start in range(0, len(ids), get_job.MAX_IDS):  # the ids one /job read takes
        chunk = ids[start : start + get_job.MAX_IDS]
        read = space.read(SpaceRoute.JOB, [("id", i) for i in chunk])
        titles |= {
            job["id"]: str(job.get("title") or "") for job in read.get("jobs") or []
        }
    word = expect["word"]
    without = [title for title in titles.values() if not starts_a_word(title, word)]
    unread = len(ids) - len(titles)
    return Verdict(
        bool(titles) and not without,
        f"{len(titles) - len(without)} of {len(titles)} rows read back have {word!r} where a "
        f"word starts in the title"
        + (f" ({unread} no longer in the index)" if unread else "")
        + (f"; not: {'; '.join(repr(t) for t in without[:5])}" if without else ""),
    )


# --- trend_sign ----------------------------------------------------------------------------

#: Words that state a direction, by direction.
_DIRECTION_WORDS = {
    "up": (
        "up", "more", "growing", "grew", "grown", "increase", "increased", "increasing",
        "rising", "rose", "expanding", "expanded", "gained", "gaining", "higher",
    ),
    "down": (
        "down", "less", "fewer", "shrinking", "shrank", "shrunk", "decline", "declined",
        "declining", "decrease", "decreased", "decreasing", "falling", "fell", "dropped",
        "contracting", "contracted", "lower", "slowing", "slowed",
    ),
    "flat": ("flat", "unchanged", "steady"),
}  # fmt: skip
_DIRECTION = {word: sign for sign, words in _DIRECTION_WORDS.items() for word in words}
#: "up to 40" and "down to 199" state an amount, not a direction.
_DIRECTION_RE = re.compile(
    r"\b(" + "|".join(_DIRECTION) + r")\b(?! to\b)", re.IGNORECASE
)
#: The question's own wording, which an answer often repeats before answering it.
_ECHO = re.compile(r"\bmore or less\b|\bmore or fewer\b", re.IGNORECASE)
#: The netted figure as the tool writes it and answers quote it: "hiring +11", "Hiring: −4",
#: "hiring is +3"; never "Not hiring +7", which is the counting changes set apart from it.
_SIGNED_HIRING = re.compile(
    r"(?<!not )\bhiring(?:\s+(?:is|was|of|at))?\W{0,3}([+−-])\s?\d", re.IGNORECASE
)
#: Postings opened less closed as read_trends writes it and answers quote it: "net −514",
#: "a net of +2" (ADR-0272).
_SIGNED_NET = re.compile(
    r"\bnet(?:\s+(?:of|is|was))?\W{0,3}([+−-])\s?\d", re.IGNORECASE
)
#: A net of opened less closed within this share of everything opened and closed may be called
#: flat as well as by its sign: −514 on 34,578 opened and closed is 1.5%.
FLAT_SHARE = 0.05


def stated_direction(answer: str) -> tuple[str | None, str | None]:
    """The direction an answer states ("up", "down" or "flat") and the text that states it.

    A quoted signed figure decides first: the net of postings opened and closed, then a signed
    hiring figure, where a direction word may describe raw openings ("openings fell, but hiring
    is +3"). Otherwise the first direction word decides; answers lead with their verdict."""
    for signed_figure in (_SIGNED_NET, _SIGNED_HIRING):
        if signed := signed_figure.search(answer):
            return ("up" if signed.group(1) == "+" else "down"), signed.group(0)
    match = _DIRECTION_RE.search(_ECHO.sub(" ", answer))
    if match is None:
        return None, None
    return _DIRECTION[match.group(1).lower()], match.group(1)


#: A signed hiring figure an answer states, with its number: "net −514", "hiring +1,234".
_STATED_FIGURE = re.compile(
    r"(?<!not )\b(?:net|hiring)(?:\s+(?:of|is|was|at))?\W{0,3}[+−-]\s?(\d[\d,]*)",
    re.IGNORECASE,
)


def stated_figure(answer: str) -> int | None:
    """The size of the first signed hiring figure the answer states, or None."""
    stated = _STATED_FIGURE.search(answer)
    return int(stated.group(1).replace(",", "")) if stated else None


def _sign(value: int) -> str:
    return "up" if value > 0 else "down" if value < 0 else "flat"


def verify_trend_sign(
    expect: dict[str, Any], transcript: Transcript, space: Space
) -> Verdict:
    """The direction of hiring for these companies, category and days, as the Space's own
    postings opened and closed give it (ADR-0272), against the direction the final answer
    states. Where the reading has no net of opened and closed, its netted hiring decides, and
    the verdict says so. A net within FLAT_SHARE of everything opened and closed may be called
    flat too."""
    picks = [company_scope.for_trends(space, c) for c in expect.get("companies") or []]
    days = int(expect.get("days") or 30)
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
    params = [("since", since)]
    if expect.get("category"):
        params.append(("family", expect["category"]))
    params += [("company", pick.key) for pick in picks]
    # No `split`: the reading's total is the same under every breakdown, and the total is all
    # this checks. Built here, not borrowed from read_trends, so a bug there cannot hide here.
    payload = space.read(SpaceRoute.TRENDS, params)
    reading = payload.get("reading")
    if reading is None:
        return Verdict(
            False, "the Space could not read this trend, so there is no sign to check"
        )
    move = (reading.get("total") or {}).get("move")
    if move is None and len(reading.get("lines") or []) == 1:
        move = reading["lines"][0]["move"]
    if move is None:
        return Verdict(False, "the reading has neither a total nor a single line")
    said, word = stated_direction(transcript.final_answer)
    turnover = move.get("turnover") or {}
    if turnover.get("net") is None:
        hiring = move.get("hiring") or 0
        return Verdict(
            said == _sign(hiring),
            f"no net of opened and closed; the Space's hiring is {hiring:+,} "
            f"({_sign(hiring)}); the answer says {said} ({word!r})",
        )
    net = turnover["net"]
    want = {_sign(net)}
    if abs(net) <= FLAT_SHARE * (turnover["opened"] + turnover["closed"]):
        want.add("flat")
    span_missing = _unstated_span(
        transcript.final_answer,
        payload.get("turnover_since"),
        reading.get("window") or {},
    )
    # Faithfulness, not only the sign (critique of #865): no hiring figure can be larger than
    # everything opened and closed plus the re-counting HeadStart sized. The +111,929 change in
    # openings listed over 30 days is.
    stated = stated_figure(transcript.final_answer)
    bound = (
        turnover["opened"] + turnover["closed"] + abs(move.get("not_hiring_total") or 0)
    )
    overstated = stated is not None and stated > bound
    return Verdict(
        said in want and span_missing is None and not overstated,
        f"postings opened less closed is {net:+,} ({' or '.join(sorted(want))}); the "
        f"answer says {said} ({word!r})"
        + (f"; {span_missing}" if span_missing else "")
        + (
            f"; it states hiring of {stated:,}, more than the {bound:,} opened, closed and "
            "sized re-counting could make"
            if overstated
            else ""
        ),
    )


_MONTHS = (
    "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
)  # fmt: skip
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
}  # fmt: skip
#: A number of days: "3.4 days", "four-day", or the tool's own "3.4 of the window's 14.0 days".
_DAYS_SAID = re.compile(
    r"\b(\d+(?:\.\d+)?|" + "|".join(_NUMBER_WORDS) + r")"
    r"(?=[\s-]*days?\b|\s+of\s+(?:the|its|this)\b)",
    re.IGNORECASE,
)


def _unstated_span(
    answer: str, began: str | None, window: dict[str, str]
) -> str | None:
    """Why the answer fails to say that opened and closed cover only part of the window, or None
    when it says so or they cover all of it. It says so by naming the day counting began (any
    usual spelling of the date) or the days counted, within a day."""
    if not began or not window.get("from") or began <= window["from"]:
        return None
    start = datetime.fromisoformat(began)
    counted = (datetime.fromisoformat(window["to"]) - start).total_seconds() / 86400
    month, day = _MONTHS[start.month - 1], start.day
    dated = (
        rf"{start:%Y-%m-%d}|\b{month}[a-z]*\.?\s+{day}(?:st|nd|rd|th)?\b"
        rf"|\b{day}(?:st|nd|rd|th)?\s+(?:of\s+)?{month}"
    )
    if re.search(dated, answer, re.IGNORECASE):
        return None
    for said in _DAYS_SAID.findall(answer):
        days = _NUMBER_WORDS.get(said.lower()) or float(said)
        if abs(days - counted) <= 1:
            return None
    return (
        f"the answer does not say opened and closed are counted only since {began[:10]} "
        f"({counted:.1f} days)"
    )


# --- hot_top -------------------------------------------------------------------------------

_COMPANY_SUFFIX = re.compile(
    r"[,.]?\s+(inc|llc|ltd|limited|corp|corporation|co|plc|gmbh|ag|sa|pvt)\.?$",
    re.IGNORECASE,
)


def _names(company: str, key: str) -> list[str]:
    """The ways an answer may name a company: as written, without its legal suffix, or by key."""
    return [company, _COMPANY_SUFFIX.sub("", company), key]


def _named(answer: str, row: dict[str, Any]) -> bool:
    return _found(
        answer, _names(str(row.get("company") or ""), str(row.get("key") or ""))
    )


#: A hiring_now row as the tool prints it: rank, its place on the page when reordered (ADR-0321),
#: quoted company, key, then the rest, where `hiring_now.FLAG_MARK` marks a row the tool itself
#: says is not hiring.
_HOT_ROW = re.compile(
    r'^\s*\d+\. (?:site #\d+ · )?("(?:[^"\\]|\\.)*") · key (\S+) · (.*)$', re.MULTILINE
)


def _hiring_now_calls(transcript: Transcript, lens: str | None) -> list[dict[str, Any]]:
    """Each successful hiring_now call's arguments as the server read them, on ``lens`` only
    when one is named."""
    schema = BY_NAME["hiring_now"].input_schema
    called = [
        (call, tool_arguments.with_defaults(schema, call.arguments))
        for call in transcript.calls
        if call.name == "hiring_now" and call.succeeded
    ]
    return [
        {**arguments, "result": call.result or ""}
        for call, arguments in called
        if lens is None or arguments["lens"] == lens
    ]


def flagged_headline(transcript: Transcript, lens: str | None = None) -> str | None:
    """The company the final answer names first among the hiring_now rows it was shown (on
    ``lens``, when named), when the tool flagged that row; else None. Leading with a row the tool
    disowns is the answer's fault, whatever order the ranking itself has."""
    rows = [
        (json.loads(company), key, hiring_now.FLAG_MARK in f" · {rest}")
        for call in _hiring_now_calls(transcript, lens)
        for company, key, rest in _HOT_ROW.findall(call["result"])
    ]
    named = [
        (at, company, flagged)
        for company, key, flagged in rows
        if (at := _found_at(transcript.final_answer, _names(company, key))) is not None
    ]
    if not named:
        return None
    _, company, flagged = min(named)
    return company if flagged else None


def _days_between(start: str, end: str) -> float:
    return (
        datetime.fromisoformat(end) - datetime.fromisoformat(start)
    ).total_seconds() / 86400


def _opened_mostly_found_late(row: dict[str, Any]) -> bool:
    """Whether most of ``row``'s postings opened were found late, posted weeks before HeadStart
    first saw them (ADR-0351): of `found_late.MIN_OPENED` or more opened, its served postings first seen in the window
    and posted long before are at least half, and those posted since fewer than half."""
    opened, fresh, late = (
        row.get("opened"),
        row.get("opened_fresh"),
        row.get("opened_found_late"),
    )
    if (
        opened is None
        or fresh is None
        or late is None
        or opened < FOUND_LATE_MIN_OPENED
    ):
        return False
    return 2 * fresh < opened <= 2 * late


def _disowned(
    row: dict[str, Any], lens: str, window: dict[str, Any], min_stock: int
) -> bool:
    """Whether hiring_now flags ``row`` on ``lens`` (ADR-0321), worked out here from /hot's own
    fields rather than borrowed from the tool, so a bug in the tool's order cannot hide here.
    Every Lens flags opened mostly found late (ADR-0351); Opened less closed flags nothing else,
    since nothing else questions its figure."""
    if _opened_mostly_found_late(row):
        return True
    if lens == "opened_less_closed":
        return False
    net, opened, closed = row.get("net"), row.get("opened"), row.get("closed")
    stock = row.get("stock") or 0
    if (opened and closed is None) or (
        closed is not None and row.get("closures_uncounted_boards")
    ):
        return True
    if stock and ((opened or 0) > stock or (lens == "rate" and stock < 2 * min_stock)):
        return True
    if not net or opened is None:
        return False
    base, to, began = window.get("base"), window.get("to"), window.get("turnover_from")
    pace = (
        _days_between(base, to) / _days_between(began, to)
        if base and to and began and base < began < to
        else 1.0
    )
    if net > opened * pace:
        return True
    if closed is None:
        return False
    turnover_net = opened - closed
    return -net > closed * pace or (
        net * turnover_net < 0 and abs(net) > abs(turnover_net) * pace
    )


def expected_hot_order(
    hot: dict[str, Any], lens: str, limit: int
) -> list[dict[str, Any]]:
    """The rows a correct hiring_now answer on ``lens`` lists first, from /hot itself: the
    page's rows after the Operators the Hiring now tab hides, and on the site's older Lenses
    every row a flag disowns after the rest, then the first ``limit``."""
    hidden = set(hot.get("hidden_by_default") or ())
    rows = [
        row
        for row in (hot.get("lenses") or {}).get(lens) or []
        if row.get("operator") not in hidden
    ]
    window = hot.get("window") or {}
    min_stock = (hot.get("counts") or {}).get("min_stock", 25)
    rows.sort(key=lambda row: _disowned(row, lens, window, min_stock))
    return rows[:limit]


#: Words by which an answer says a company's postings opened were not newly posted (ADR-0351),
#: the tools' own "posted over 14 days before" among them (ADR-0369).
_FOUND_LATE_SAID = re.compile(
    r"found late|posted (?:weeks|months|long|well) (?:before|earlier|ago)|posted earlier"
    r"|posted (?:more than |over )?(?:\d+|two|three|four) (?:days|weeks) (?:before|earlier)"
    r"|older postings|not newly posted|backfill|listed again|re-?listed|re-?posted",
    re.IGNORECASE,
)


def unflagged_found_late(answer: str, rows: list[dict[str, Any]]) -> list[str]:
    """The companies among ``rows`` whose opened was mostly found late (ADR-0351) that the
    answer names without saying so on a line that names them: reported as hiring this week. A
    caveat elsewhere in the answer does not reach the row (round-4 review SP4)."""
    lines = answer.splitlines()
    return [
        row["company"]
        for row in rows
        if _opened_mostly_found_late(row)
        and _named(answer, row)
        and not any(
            _named(line, row) and _FOUND_LATE_SAID.search(line) for line in lines
        )
    ]


def out_of_order(answer: str, rows: list[dict[str, Any]]) -> list[str]:
    """The companies among ``rows``, in the order hiring_now lists them, that the answer names
    before a row the tool lists above them (by where each is first named): a flagged row, found
    late or otherwise disowned, presented above an unflagged one (round-4 review SP4)."""
    named = [
        (at, row["company"])
        for row in rows
        if (
            at := _found_at(
                answer, _names(str(row.get("company") or ""), str(row.get("key") or ""))
            )
        )
        is not None
    ]
    return [
        company
        for i, (at, company) in enumerate(named)
        if any(earlier > at for earlier, _ in named[:i])
    ]


def verify_hot_top(
    expect: dict[str, Any], transcript: Transcript, space: Space
) -> Verdict:
    """At least N-1 of the top N on the Lens named, in the order a correct hiring_now answer
    lists them (`expected_hot_order`, at the call's own `limit`), none named before a row listed
    above it, the answer does not lead with a row the tool flagged on that Lens, and it names no
    row listed whose opened was mostly found late without saying so beside it (ADR-0351)."""
    lens = expect.get("lens") or hiring_now.DEFAULT_LENS
    top = int(expect.get("top") or 5)
    calls = _hiring_now_calls(transcript, lens)
    schema = BY_NAME["hiring_now"].input_schema
    limit = (calls[0] if calls else tool_arguments.with_defaults(schema, {}))["limit"]
    listed = expected_hot_order(space.read(SpaceRoute.HOT), lens, limit)
    rows = listed[:top]
    need = max(len(rows) - 1, 0)
    named = [row["company"] for row in rows if _named(transcript.final_answer, row)]
    headline = flagged_headline(transcript, lens)
    found_late = unflagged_found_late(transcript.final_answer, listed)
    early = out_of_order(transcript.final_answer, rows)
    return Verdict(
        len(named) >= need and headline is None and not found_late and not early,
        f"names {len(named)} of the top {len(rows)} on {lens} (needs {need}): "
        f"{', '.join(r['company'] for r in rows)}"
        + (f"; leads with {headline!r}, a row hiring_now flagged" if headline else "")
        + (
            f"; reports {', '.join(found_late)} as hiring, though most of what each opened "
            "was found late"
            if found_late
            else ""
        )
        + (
            f"; names {', '.join(early)} above a row hiring_now lists before it"
            if early
            else ""
        ),
    )


# --- found_late_share ----------------------------------------------------------------------


class NotJudged(Exception):
    """A data-dependent task whose case went from the live data between its `requires` check
    and its verdict: the run says nothing about the model, so it is not judged, never failed."""


#: How far, in percentage points, a stated share may sit from the Space's own.
SHARE_TOLERANCE = 3
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def _found_late_turnover(
    fact: dict[str, Any], space: Space
) -> tuple[str, dict[str, Any]]:
    """The companies' keys, and their first row's turnover over the trailing ``days``, read from
    /trends by the verifier itself, so a bug in a tool cannot hide here (ADR-0369)."""
    picks = [company_scope.for_trends(space, c) for c in fact.get("companies") or []]
    days = int(fact.get("days") or 7)
    since = (_now() - timedelta(days=days)).isoformat(timespec="seconds")
    payload = space.read(
        SpaceRoute.TRENDS, [("since", since), *(("company", p.key) for p in picks)]
    )
    move = ((payload.get("reading") or {}).get("total") or {}).get("move") or {}
    return ", ".join(pick.key for pick in picks), move.get("turnover") or {}


def _no_found_late_burst(fact: dict[str, Any], space: Space) -> str | None:
    """Why the companies' postings opened over ``days`` are not mostly found late in today's
    data, by the rule re-derived from /trends' own fields, or None while they are."""
    names, turnover = _found_late_turnover(fact, space)
    if _opened_mostly_found_late(turnover):
        return None
    return (
        f"{names}'s postings opened over {fact.get('days') or 7} days are not mostly found "
        f"late in today's data (opened {turnover.get('opened')}, found late "
        f"{turnover.get('opened_found_late')}, fresh {turnover.get('opened_fresh')})"
    )


def verify_found_late_share(
    expect: dict[str, Any], transcript: Transcript, space: Space
) -> Verdict:
    """For companies whose postings opened over ``days`` were mostly found late, posted weeks
    before HeadStart first saw them (ADR-0369), the answer must say so and name how many: the
    found-late count, or its share of opened or of the postings the counts date, within
    SHARE_TOLERANCE points. Any tool path that gives the split passes. The task's `requires`
    retires it while the burst is gone (`_no_found_late_burst`); a burst gone by the verdict is
    not judged (NotJudged), never failed."""
    if gone := _no_found_late_burst(expect, space):
        raise NotJudged(gone)
    names, turnover = _found_late_turnover(expect, space)
    opened, late = turnover["opened"], turnover["opened_found_late"]
    fresh = turnover["opened_fresh"]
    answer = transcript.final_answer
    said = bool(_FOUND_LATE_SAID.search(answer))
    numbers = {float(n.replace(",", "")) for n in _NUMBER.findall(answer)}
    shares = (100 * late / opened, 100 * late / (late + fresh))
    percents = [float(p) for p in _PERCENT.findall(answer)]
    named = late in numbers or any(
        abs(p - share) <= SHARE_TOLERANCE for p in percents for share in shares
    )
    return Verdict(
        said and named,
        f"{names}: {late:,} found late and {fresh:,} fresh against {opened:,} opened "
        f"({shares[0]:.0f}% of opened); the answer "
        + ("says" if said else "does not say")
        + " they were found late, and "
        + ("names" if named else "does not name")
        + " how many",
    )


# --- blocking_named ------------------------------------------------------------------------

_BLOCKING = re.compile(r"The filter costing the most is `([a-z_]+)`")
_COMPANY_BLOCKING = "0 jobs: no company name contains"

#: Plain words an answer may use for an argument besides its own name.
_ARGUMENT_WORDS = {
    # Never a bare "salary" or "location": an answer about "₹1 crore in Indore" says those
    # anyway, so only a phrase that names the filter counts.
    "salary_min": ("salary filter", "minimum salary", "salary minimum", "salary floor"),
    "salary_max": ("salary filter", "maximum salary", "salary maximum", "salary cap"),
    "india_place": ("location filter", "city filter", "place filter"),
}


def _blocking_named_in(result: str) -> str | None:
    if result.startswith(_COMPANY_BLOCKING):
        return "company"
    match = _BLOCKING.search(result)
    return match.group(1) if match else None


def _mentions_argument(answer: str, argument: str) -> bool:
    words = [argument, argument.replace("_", " "), *_ARGUMENT_WORDS.get(argument, ())]
    return _found(answer, words)


def verify_blocking_named(
    expect: dict[str, Any], transcript: Transcript, space: Space
) -> Verdict:
    """A search_jobs result named ``expect["argument"]`` (any argument when null) as the
    Blocking filter, and the final answer mentions it. With ``value_names_it``, the value that
    call sent for it counts as naming it too: "no Haskell jobs in Indore at any pay" names the
    place filter by its place (round-3 critique P1-6, ADR-0334)."""
    want = expect.get("argument")
    named = [
        (argument, call.arguments.get(argument))
        for call in transcript.calls
        if call.name == "search_jobs" and call.succeeded
        if (argument := _blocking_named_in(call.result or ""))
    ]
    candidates = [(a, v) for a, v in named if want is None or a == want]
    if not candidates:
        return Verdict(
            False,
            f"no search_jobs result named {want or 'any'} as blocking "
            f"(named: {[a for a, _ in named]})",
        )
    for argument, value in candidates:
        if _mentions_argument(transcript.final_answer, argument):
            return Verdict(
                True, f"a result named {argument} and the answer mentions it"
            )
        if (
            expect.get("value_names_it")
            and isinstance(value, str)
            and _found(transcript.final_answer, value)
        ):
            return Verdict(
                True, f"a result named {argument} and the answer names its {value!r}"
            )
    return Verdict(
        False,
        f"a result named {candidates[0][0]}, but the final answer does not mention it",
    )


# --- sponsorship_polarity ------------------------------------------------------------------

#: The most row ids one verdict reads back from `/job`: eight reads.
POLARITY_IDS_READ = 40

#: The descriptions a person labelled when ADR-0333 measured the Space's rules (#947): a job's
#: label is the verdict's truth wherever the answer names a labelled job.
LABELLED_DESCRIPTIONS = (
    _ROOT / "tests" / "fixtures" / "work_authorization_labelled.jsonl"
)
#: The labels of a job that does offer sponsorship; "mixed" offers it in one place. A job
#: labelled "may_offer" (hedged: "not guaranteed", "case by case") offers it only on an answer
#: line that says so (ADR-0353).
_OFFERING_LABELS = ("offers", "mixed")
_HEDGED_LABEL = "may_offer"
#: The eval's own reading of an unlabelled job, deliberately simpler than the Space's rules so it
#: does not share their errors: a quoted sentence with a negating word within :data:`_NEAR_WORDS`
#: words of one about sponsorship. Near, since "We support visa sponsorship … the right person
#: and not" (coera, 2026-09-29) refuses nothing; ADR-0368 reads it as hedged instead.
_SPONSORSHIP_TOPIC = re.compile(r"(?i)\w*(?:sponsor|visa|h-?1-?b|citizen)\w*")
_NEAR_WORDS = 5
_NEGATED = re.compile(
    r"(?i)\b(?:no|not|never|unable|cannot|without|nor|refus\w*)\b|n['’]t\b|"
    r"citizenship (?:is )?required|must (?:be|hold) (?:a )?(?:u\.?s\.?|united states) citizen"
)


#: The eval's own reading of a hedged offer, again apart from the Space's rules (ADR-0353): a
#: quoted sentence about sponsorship that promises nothing. Read before :data:`_NEGATED`, since
#: "not guaranteed" is not a refusal.
_HEDGED = re.compile(
    r"(?i)not guaranteed|case[- ]by[- ]case|\bmay (?:be )?(?:available|considered|offered|"
    r"possible|sponsor)|\bmight\b|select positions|certain (?:positions|roles)"
)
#: A hedge stands further from its word than a negation: "Sponsorship for this role is not
#: guaranteed", "Sponsorship decisions are made on a case-by-case basis".
_HEDGE_NEAR_WORDS = 8
#: The eval's own hedges, sought anywhere in a quoted sentence about sponsorship: plain phrases
#: written from the offers a person labelled may_offer (:data:`LABELLED_DESCRIPTIONS`), each
#: beside the labelled wording it came from, and not from the Space's rules, which this module
#: does not import, so the two do not share their errors (round-5 review SP7). The Space reads
#: "We sponsor visas, pending company approval" as a firm offer; this list reads it as hedged.
_HEDGE_PHRASES = (
    # "we aren't able to successfully sponsor visas for every role and every candidate"
    "every role",
    "every candidate",
    # "Sponsorship for this role is not guaranteed", "we can't always guarantee success"
    "guarantee",
    # "open to considering candidates who require visa sponsorship", "shall be considered",
    # "may be considered on a case-by-case basis"; never a bare "consider", which 119 labelled
    # firm offers carry ("Capital One will consider sponsoring a new qualified applicant")
    "open to consider",
    "shall be considered",
    "may be considered",
    # "(subject to eligibility and company approval)"
    "approval",
    # "to bring you to SF, if possible", "where possible will offer visa sponsorship"
    "if possible",
    "where possible",
    # "open to sponsoring international visas where we can"
    "where we can",
    # "where it makes the difference between hiring the right person and not"
    "makes the difference",
    # "only for candidates that are already based in the UK", "you must already be in Singapore"
    "already based",
    "already located",
    "already be in",
    # "Open to visa transfers", "support transfer of visa sponsorship", "H-1B transfer
    # sponsorship available"; never a bare "visa transfer", which a firm offer names beside a
    # new visa ("visa transfers and new visa sponsorship are listed as available")
    "open to visa transfer",
    "transfer of visa",
    "transfer sponsorship",
    # "may be limited to certain roles", "Certain positions may be eligible"
    "certain roles",
    "certain positions",
    "select positions",
)
#: What an answer line says of a hedged job to report it truly.
_SAID_HEDGED = re.compile(
    r"(?i)not guaranteed|case[- ]by[- ]case|\bmay\b|\bmight\b|hedg|possib|not a firm|"
    r"uncertain|conditional|not promised|depends"
)
#: What an answer line says of a job it left out ("I dropped an Amgen listing …").
_LEFT_OUT = re.compile(
    r"(?i)\b(?:dropp\w*|left (?:it )?out|leav\w*|exclud\w*|skipp\w*|omitt\w*|remov\w*|"
    r"filtered out|set aside|ruled out)\b"
)


def _named_in(answer: str, job: dict[str, Any]) -> bool:
    """Whether ``answer`` names ``job``: by its id, or by both its title and its company."""
    if _found(answer, str(job.get("id") or "")):
        return True
    title, company = str(job.get("title") or ""), str(job.get("company") or "")
    return bool(title and company) and _found(answer, title) and _found(answer, company)


def _sponsorship_labels() -> dict[str, str]:
    """Each hand-labelled job's sponsorship label, by id."""
    lines = LABELLED_DESCRIPTIONS.read_text(encoding="utf-8").splitlines()
    return {row["id"]: row["sponsorship"] for row in map(json.loads, lines) if row}


def _not_offering(job: dict[str, Any], labels: dict[str, str]) -> str | None:
    """Why the eval reads ``job`` as not offering sponsorship, or None: its label where a
    person gave it one, else the first sentence `/job` quotes about sponsorship that negates.
    A hedged offer's reason starts "hedged" (:func:`_hedged`)."""
    if (label := labels.get(str(job.get("id")))) is not None:
        if label == _HEDGED_LABEL:
            return "hedged: labelled may_offer by hand"
        return None if label in _OFFERING_LABELS else f"labelled {label} by hand"
    for mention in (job.get("work_authorization") or {}).get("mentions") or []:
        if _SPONSORSHIP_TOPIC.search(mention) and any(
            phrase in mention.casefold() for phrase in _HEDGE_PHRASES
        ):
            return f"hedged: says {mention[:80]!r}"
        for topic in _SPONSORSHIP_TOPIC.finditer(mention):
            near = mention[: topic.start()].split()[-_NEAR_WORDS:] + [topic.group()]
            near += mention[topic.end() :].split()[:_NEAR_WORDS]
            hedge = mention[: topic.start()].split()[-_HEDGE_NEAR_WORDS:] + [
                topic.group()
            ]
            hedge += mention[topic.end() :].split()[:_HEDGE_NEAR_WORDS]
            if _HEDGED.search(" ".join(hedge)):
                return f"hedged: says {mention[:80]!r}"
            if _NEGATED.search(" ".join(near)):
                return f"says {mention[:80]!r}"
    return None


def _hedged(why: str) -> bool:
    return why.startswith("hedged")


def _lines_naming(answer: str, job: dict[str, Any]) -> list[str]:
    """The lines of ``answer`` naming ``job``: by id, by title and company, or by its company
    on a line saying it was left out ("I dropped an Amgen listing")."""
    company = str(job.get("company") or "")
    return [
        line
        for line in answer.splitlines()
        if _named_in(line, job)
        or (company and _found(line, company) and _LEFT_OUT.search(line))
    ]


def _said_not_offering(answer: str, job: dict[str, Any]) -> bool:
    """Whether a line of ``answer`` naming ``job`` says it does not offer sponsorship, or that
    it was left out."""
    return any(
        _NEGATED.search(line) or _LEFT_OUT.search(line)
        for line in _lines_naming(answer, job)
    )


def _said_hedged(answer: str, job: dict[str, Any]) -> bool:
    """Whether a line of ``answer`` naming ``job`` says its offer is hedged."""
    return any(_SAID_HEDGED.search(line) for line in _lines_naming(answer, job))


def verify_sponsorship_polarity(
    expect: dict[str, Any], transcript: Transcript, space: Space
) -> Verdict:
    """The truth of a "which jobs sponsor visas" answer (ADR-0333), judged apart from the
    Space's rules: every row a search_jobs or get_job result listed is read back from `/job`, and
    none the final answer names (by id, or by title and company) may be one the eval reads as not
    offering sponsorship. A job a person labelled (:data:`LABELLED_DESCRIPTIONS`) is judged by
    its label; any other by :data:`_NEGATED` near a sponsorship word in the sentences `/job`
    quotes.
    With ``expect["said_ok"]``, a job named on a line saying it does not offer sponsorship, or
    that it was left out, is fine: the answer reported it truly. A hedged offer ("not
    guaranteed", "case by case", a person's ``may_offer`` label) is fine on a line that says it
    is hedged (ADR-0353). At least ``expect["at_least"]`` must be named.

    It catches a named job a person labelled refusing, silent or hedged, one whose quoted
    sentence about sponsorship negates ("not available", "without sponsorship", "citizenship
    required"), and one whose sentence carries a hedge of its own list (:data:`_HEDGE_PHRASES`)
    reported as a firm offer, including hedges the Space's rules miss. It cannot catch an
    unlabelled job whose refusal no quoted sentence states, nor a hedge in words its list lacks
    ("where feasible") or a scope it cannot read (sponsorship only for a move to another city).
    It fails a right answer whose job offers sponsorship in a sentence that also negates ("no
    matter your visa status, we sponsor") or that uses a listed phrase firmly ("we guarantee
    sponsorship")."""
    ids: list[str] = []
    for call in transcript.calls:
        if call.name in ("search_jobs", "get_job") and call.succeeded:
            ids += [i for i in _row_ids(call.result or "") if i not in ids]
    if not ids:
        return Verdict(False, "no search_jobs or get_job result lists a job id")
    jobs: list[dict[str, Any]] = []
    for start in range(0, min(len(ids), POLARITY_IDS_READ), get_job.MAX_IDS):
        chunk = ids[start : min(start + get_job.MAX_IDS, POLARITY_IDS_READ)]
        jobs += space.read(SpaceRoute.JOB, [("id", i) for i in chunk]).get("jobs") or []
    answer = transcript.final_answer
    named = [job for job in jobs if _named_in(answer, job)]
    labels = _sponsorship_labels()
    wrong = [
        (job, why)
        for job in named
        if (why := _not_offering(job, labels))
        and not (expect.get("said_ok") and _said_not_offering(answer, job))
        and not (_hedged(why) and _said_hedged(answer, job))
    ]
    at_least = int(expect.get("at_least", 1))
    enough = len(named) >= at_least
    return Verdict(
        enough and not wrong,
        f"the answer names {len(named)} of the {len(jobs)} jobs read back"
        + (f", fewer than {at_least}" if not enough else "")
        + (
            f"; {len(wrong)} of them do not offer sponsorship: "
            + "; ".join(
                f"{job.get('title')!r} at {job.get('company')!r} ({why})"
                for job, why in wrong[:5]
            )
            if wrong
            else "; each offers sponsorship as far as the eval reads it"
            if named
            else ""
        ),
    )


# --- senior_caveat -------------------------------------------------------------------------

#: A search_jobs row's title as the tool prints it: rank, an optional score, then the title.
_SEARCH_ROW_TITLE = re.compile(
    r'^\s*\d+\. (?:\d\.\d+ )?("(?:[^"\\]|\\.)*")', re.MULTILINE
)

#: A title above a new graduate's level, read here apart from the tool's own tag (ADR-0359).
_SENIOR_WORD = re.compile(
    r"(?i)\b(?:senior|sr\.?|staff|principal|lead|manager|director)\b"
)
_JUNIOR_WORD = re.compile(r"(?i)\b(?:associate|junior|jr\.?)\b")

#: Words by which an answer says a senior-titled job may not fit a new graduate, or drops it.
_SENIOR_CAVEAT = re.compile(
    r"(?i)side clause|stated minimum|floors?\b|may not (?:be )?(?:a )?(?:good )?fit|"
    r"not (?:an? )?(?:entry|junior|new[- ]grad)|too senior|senior (?:title|role|level)|"
    r"likely (?:needs|requires|wants|expects)|probably (?:needs|requires|expects)|stretch|"
    r"caveat|check (?:get_job|the (?:full )?(?:posting|description))|verify|"
    r"dropped|left (?:it |them )?out|excluded|skipped|leaving out"
)


def senior_rows(transcript: Transcript) -> list[str]:
    """The titles of rows search_jobs listed, at a `max_years` of 2 or less, that read Senior,
    Staff, Principal, Lead, Manager or Director and not Associate or Junior."""
    schema = BY_NAME["search_jobs"].input_schema
    titles = []
    for call in transcript.calls:
        if call.name != "search_jobs" or not call.succeeded:
            continue
        years = tool_arguments.with_defaults(schema, call.arguments).get("max_years")
        if years is None or years > 2:
            continue
        for quoted in _SEARCH_ROW_TITLE.findall(call.result or ""):
            title = json.loads(quoted)
            if _SENIOR_WORD.search(title) and not _JUNIOR_WORD.search(title):
                titles.append(title)
    return list(dict.fromkeys(titles))


def verify_senior_caveat(
    expect: dict[str, Any], transcript: Transcript, space: Space
) -> Verdict:
    """No senior-titled row a new graduate's search listed (:func:`senior_rows`) is named in the
    answer without a caveat on a line that names it: its served floor is the smallest its
    description states (ADR-0079), which may be a side clause (round-4 review SP3, ADR-0359)."""
    lines = transcript.final_answer.splitlines()
    bare = [
        title
        for title in senior_rows(transcript)
        if _found(transcript.final_answer, title)
        and not any(
            _found(line, title) and _SENIOR_CAVEAT.search(line) for line in lines
        )
    ]
    return Verdict(
        not bare,
        "presents "
        + ", ".join(repr(t) for t in bare)
        + " as a fit for a new graduate without a caveat"
        if bare
        else "names no senior-titled row without a caveat",
    )


# --- operator_mix --------------------------------------------------------------------------

#: One company on role_requirements' companies line: its quoted name, any tags, its quoted key,
#: and its count, "15" or "15 sampled, 8 counted".
_SAMPLED_COMPANY = re.compile(
    r'(?:("(?:[^"\\]|\\.)*")|no company name)(?: \([^)]*\))* \(key ("(?:[^"\\]|\\.)*")\) '
    r"([\d,]+)(?: sampled, ([\d,]+) counted)?"
)
_COMPANIES_LINE = "Companies with the most sampled postings:"
#: Operators a sample leaves out unless its call keeps them (ADR-0335).
_LEFT_OUT_OPERATORS = ("staffing", "aggregator")


def sampled_companies(result: str) -> list[tuple[str, str, int]]:
    """Each company on a role_requirements result's companies line: its name, its key, and how
    many of its postings were counted."""
    line = next(
        (ln for ln in result.splitlines() if ln.startswith(_COMPANIES_LINE)), ""
    )
    return [
        (
            json.loads(name) if name else "",
            json.loads(key),
            int((counted or sampled).replace(",", "")),
        )
        for name, key, sampled, counted in _SAMPLED_COMPANY.findall(line)
    ]


def verify_operator_mix(
    expect: dict[str, Any], transcript: Transcript, space: Space
) -> Verdict:
    """Round-4 critique P1-2 (ADR-0352): in every successful role_requirements call, no company
    the sample counted is one the curated lists (`board_operator.classify`, read here, not by the
    tool) call a staffing firm or job board unless that call's `operators` kept it, and, unless a
    `company` was named, no company counted more than ``expect["per_company"]`` postings."""
    schema = BY_NAME["role_requirements"].input_schema
    calls = [
        (call, tool_arguments.with_defaults(schema, call.arguments))
        for call in transcript.calls
        if call.name == "role_requirements" and call.succeeded
    ]
    if not calls:
        return Verdict(False, "no successful role_requirements call")
    cap = int(expect.get("per_company", role_requirements.PER_COMPANY))
    wrong: list[str] = []
    listed = 0
    for call, arguments in calls:
        kept = set(search_arguments.operators_kept(arguments) or OPERATORS)
        for name, key, counted in sampled_companies(call.result or ""):
            listed += 1
            operator = board_operator.classify(key, name)
            if operator in _LEFT_OUT_OPERATORS and operator not in kept:
                wrong.append(f"{name or key!r} is {operator}")
            if counted > cap and not (arguments.get("company") or "").strip():
                wrong.append(f"{name or key!r} counted {counted}, over {cap}")
    if not listed:
        return Verdict(False, "no role_requirements result lists its sampled companies")
    return Verdict(
        not wrong,
        f"{listed} sampled companies read"
        + (
            f"; {'; '.join(wrong[:5])}"
            if wrong
            else "; none a left-out operator or over the cap"
        ),
    )


# --- country_split -------------------------------------------------------------------------

#: How far either side of a country's name the answer's figure for it may sit: "India: 12,345"
#: and "2,345 in Germany" both.
_NEAR_A_NAME = 60
_FIGURE = re.compile(r"(?<![\d.])\d{1,3}(?:,\d{3})+(?!\d)|(?<![\d.,])\d+(?![\d,])")


def _country_total(space: Space, arguments: dict[str, Any], code: str) -> int:
    """The total `/facets` gives for ``arguments`` (a search_jobs call's, at their defaults)
    with ``code`` as their only country: what the answer's figure for that country must be."""
    scope = None
    if company := (arguments.get("company") or "").strip():
        scope = company_scope.for_search(
            space, company, needs_boards=bool(arguments.get("category"))
        )
    placeless = {
        k: v for k, v in arguments.items() if k not in ("country", "india_place")
    }
    params = [
        (name, value)
        for name, value in search_jobs.space_params(placeless, scope)
        if name not in ("k", "page", "sort")
    ]
    facets = space.read(
        SpaceRoute.FACETS, [*params, ("country", code), ("counts", "total")]
    )
    return int(facets.get("total") or 0)


def _figures_near(answer: str, name: str) -> list[int]:
    """Every figure within :data:`_NEAR_A_NAME` characters of each place ``answer`` says
    ``name``."""
    figures = []
    for match in re.finditer(rf"\b{re.escape(name)}\b", answer, re.IGNORECASE):
        start = max(0, match.start() - _NEAR_A_NAME)
        near = answer[start : match.end() + _NEAR_A_NAME]
        figures += [int(f.replace(",", "")) for f in _FIGURE.findall(near)]
    return figures


def verify_country_split(
    expect: dict[str, Any], transcript: Transcript, space: Space
) -> Verdict:
    """Each of ``expect["countries"]`` stated with its own count (ADR-0355): for some successful
    search_jobs call meeting ``must_any``, the answer says, near each country's name, the total
    `/facets` gives for that call's filters with that country and no other place — read here per
    country, so an answer taken from `detail` full's places is held to the `country` filter's own
    count, and one made from a search per country passes too."""
    schema = BY_NAME["search_jobs"].input_schema
    rules = {k: v for k, v in expect.items() if k == "must_any"}
    calls = [
        call
        for call in transcript.calls
        if call.name == "search_jobs"
        and call.succeeded
        and not _misses(rules, tool_arguments.with_defaults(schema, call.arguments))
    ]
    if not calls:
        return Verdict(False, f"no successful search_jobs call meets {rules!r}")
    tried = []
    for call in calls:
        arguments = tool_arguments.with_defaults(schema, call.arguments)
        wrong = []
        for code in expect["countries"]:
            name = country_filter.name(code)
            truth = _country_total(space, arguments, code)
            if truth not in _figures_near(transcript.final_answer, name):
                wrong.append(f"{name} is {truth:,}")
        if not wrong:
            return Verdict(
                True,
                f"search_jobs {json.dumps(call.arguments, ensure_ascii=False)}: each "
                "country's figure is its /facets total",
            )
        tried.append(
            f"{json.dumps(call.arguments, ensure_ascii=False)}: {', '.join(wrong)}, "
            "not stated beside its name"
        )
    return Verdict(False, " | ".join(tried))


# --- page_companies ------------------------------------------------------------------------

#: A numbered row of a search_jobs page: its number, score, quoted title and quoted company. An
#: "also #N" row is the same posting and names no company of its own.
_PAGE_ROW = re.compile(
    r'^\s*\d+\. (?:[\d.]+ )?"(?:[^"\\]|\\.)*" · ("(?:[^"\\]|\\.)*")', re.MULTILINE
)


def page_companies(result: str) -> list[str]:
    """The companies a search_jobs page names on its numbered rows, first-seen order, each once
    case-blind, without the tool's cut mark."""
    names: dict[str, str] = {}
    for quoted in _PAGE_ROW.findall(result):
        name = json.loads(quoted).removesuffix("…").strip()
        names.setdefault(name.casefold(), name)
    return list(names.values())


def verify_page_companies(
    expect: dict[str, Any], transcript: Transcript, space: Space
) -> Verdict:
    """Round-5 critique R5-P1-2 (ADR-0365): the first successful search_jobs call meeting
    ``must``/``must_any`` lists at least ``min_companies`` companies on its page, and the answer
    names that many of them. The task's ``piled_ranking`` requirement retires it when the data
    no longer piles one company up."""
    call, verdict = _call_meeting_the_rules(expect, transcript)
    if call is None:
        return verdict
    least = int(expect["min_companies"])
    listed = page_companies(call.result or "")
    named = [name for name in listed if _found(transcript.final_answer, name)]
    return Verdict(
        len(listed) >= least and len(named) >= least,
        f"{verdict.detail}: the page lists {len(listed)} companies, the answer names "
        f"{len(named)}",
    )


# --- mentions ------------------------------------------------------------------------------


def verify_mentions(
    expect: dict[str, Any], transcript: Transcript, space: Space
) -> Verdict:
    """Every ``all`` term (a list is alternatives) and one ``any`` term in the final answer,
    every ``tool_results_all`` term in the tool results, and for each ``answer_carries``
    pattern (a list is alternatives) a value its first group captured in the tool results said
    in the final answer: the figure or name the tool gave, whatever it is today, rather than one
    frozen into the task (ADR-0334)."""
    answer = transcript.final_answer
    results = "\n".join(call.result or "" for call in transcript.calls)
    missing = [
        f"answer lacks {t!r}" for t in expect.get("all") or [] if not _found(answer, t)
    ]
    if (choices := expect.get("any")) and not any(_found(answer, t) for t in choices):
        missing.append(f"answer lacks any of {choices!r}")
    missing += [
        f"tool results lack {t!r}"
        for t in expect.get("tool_results_all") or []
        if not _found(results, t)
    ]
    missing += [
        miss
        for pattern in expect.get("answer_carries") or []
        if (miss := _carried(pattern, results, answer))
    ]
    return Verdict(not missing, "; ".join(missing) or "every term found")


def _carried(pattern: str | list[str], results: str, answer: str) -> str | None:
    """Why the answer does not say a value ``pattern``'s first group captures in ``results``,
    or None when it says one. A figure counts written with or without its thousands commas."""
    patterns = pattern if isinstance(pattern, list) else [pattern]
    values = list(
        dict.fromkeys(
            match.group(1).strip()
            for p in patterns
            for match in re.finditer(p, results)
        )
    )
    if not values:
        return f"tool results match none of {patterns!r}"
    said = [v for v in values if _states(answer, v)]
    return None if said else f"answer states none of the tool results' {values!r}"


def _states(answer: str, value: str) -> bool:
    """``value`` in ``answer``, case-blind; a figure whole ("271" is not in "1,271" or "2710"),
    with or without its thousands commas."""
    if not re.fullmatch(r"[\d,]+", value):
        return _found(answer, value)
    return any(
        re.search(rf"(?<![\d,]){re.escape(form)}(?![\d]|,\d)", answer)
        for form in (value, value.replace(",", ""))
    )


# --- employer_unflagged --------------------------------------------------------------------

#: An agency, a staffing firm, a recruiter, or unverified. An agency's possessive is about
#: agencies ("when a company's name reads like an agency's").
_AGENCY_KIND = (
    r"(?:staffing(?: (?:agency|agencies|firm|firms|company))?|agenc(?:y|ies)(?!['’]s)|"
    r"recruit(?:ers?|ing firms?|ment firms?|ment agenc(?:y|ies))|"
    r"(?:operator )?unverified(?: operator)?)"
)
#: An answer calling a company one (round-5 review SP8): it may be one ("may be a staffing
#: agency", "possibly a recruiter"), it is one ("is an unverified operator"), or HeadStart
#: flags or treats it as one. Explaining what a tag means ("HeadStart flags a row as `staffing`
#: when …") and listing tags it does not carry call it nothing.
_CALLED_AGENCY = re.compile(
    r"\b(?:(?:may|might|could|would)(?: well)? be|possibly|perhaps|probably|likely|"
    r"apparently|(?:seems?|appears?) to be|looks like|is|(?:flagged|treated|listed|"
    r"label(?:l)?ed|tagged|marked|read) as|(?:treats?|lists?|labels?|tags?|marks?|reads?|"
    r"flags?) (?:it|them|lockheed martin) as)\s+(?:(?:an?|the)\s+)?"
    r"(?:(?:possible|potential|likely|probable)\s+)?[`\"'“‘*]*"
    + _AGENCY_KIND
    + r"|\bflagged\s+(?:an?\s+)?[`\"'“‘*]*(?:operator\s+)?unverified",
    re.IGNORECASE,
)
_NOT_ITS_EMPLOYER = re.compile(r"\bnot (?:the|its own|an?) employer", re.IGNORECASE)
#: A negation, which denies a call after it in its own clause: "does not suggest it might be a
#: staffing agency", "no sign that it is a recruiter".
_NEGATION = re.compile(r"\b(?:not|never|no|nor|none|neither)\b|n['’]t\b", re.IGNORECASE)
#: What ends a clause, so a "not" of another clause denies nothing: "HeadStart does not verify
#: it, so it may be a staffing agency", "is not on any curated list and may be a recruiter".
_CLAUSE_END = re.compile(
    r"[,;:.!?()—–]|\b(?:and|but|so|yet|while|though|although|whereas|however)\b",
    re.IGNORECASE,
)
#: A question, as an answer restates the one it answers ("Does HeadStart suggest it might be
#: a staffing agency?"): it calls the company nothing.
_QUESTION = re.compile(r"[^.!?\n]*\?")


def _calls_an_agency(line: str) -> bool:
    """Whether ``line`` calls its company not the employer, or a possible agency in a clause no
    negation denies."""
    if _NOT_ITS_EMPLOYER.search(line):
        return True
    for clause in _CLAUSE_END.split(_QUESTION.sub(" ", line)):
        for call in _CALLED_AGENCY.finditer(clause):
            if not _NEGATION.search(clause[: call.start()]):
                return True
    return False


def verify_employer_unflagged(
    expect: dict[str, Any], transcript: Transcript, space: Space
) -> Verdict:
    """Each of ``expect["companies"]`` is an employer, read from its own postings, and neither
    the tools nor the answer may call it a possible agency: round 5's R5-P1-3 flagged Lockheed
    Martin "operator unverified" off SAP's host label `hr` (ADR-0366). Fails a tool result line
    naming one that carries the tag, an answer that leaves one unnamed, and an answer line
    naming one that calls it unverified, a staffing firm, an agency, a recruiter or not the
    employer; a clause denying it ("not flagged as a staffing agency") is no such call."""
    results = [
        line
        for call in transcript.calls
        if call.succeeded
        for line in (call.result or "").splitlines()
    ]
    answer = transcript.final_answer.splitlines()
    missing = []
    for company in expect["companies"]:
        tagged = [
            line
            for line in results
            if _found(line, company) and _found(line, "operator unverified")
        ]
        if tagged:
            missing.append(f"a tool flagged {company}: {tagged[0].strip()[:160]!r}")
        naming = [line for line in answer if _found(line, company)]
        if not naming:
            missing.append(f"the answer does not name {company}")
        elif called := [line for line in naming if _calls_an_agency(line)]:
            missing.append(
                f"the answer calls {company} a possible agency: {called[0]!r}"
            )
    return Verdict(not missing, "; ".join(missing) or "named, and flagged by no one")


# --- watched_roles_total -------------------------------------------------------------------

#: A figure an answer states, signed or not: "+593", "−3,427", "12,044".
_FIGURE = re.compile(r"(?<![\w.])[+−-]?\d[\d,]*(?![\d.]\d)")


def _figures(answer: str) -> list[int]:
    return [
        abs(int(re.sub(r"[+−,-]", "", said)))
        for said in _FIGURE.findall(answer)
        if re.sub(r"[+−,-]", "", said)
    ]


def _near(figure: int, want: int) -> bool:
    return abs(figure - abs(want)) <= max(5, 0.02 * abs(want))


def _watched_roles(expect: dict[str, Any], space: Space) -> list[dict[str, Any]]:
    """The watched roles' lines of ``expect["category"]`` since ``expect["since"]``, read from
    the Space's own reading, not from read_trends."""
    payload = space.read(
        SpaceRoute.TRENDS,
        [
            ("since", f"{expect['since']}T00:00:00+00:00"),
            ("family", expect["category"]),
            ("split", "roles"),
        ],
    )
    return [line["move"] for line in (payload.get("reading") or {}).get("lines") or []]


def verify_watched_roles_total(
    expect: dict[str, Any], transcript: Transcript, space: Space
) -> Verdict:
    """One total for a category's watched roles, on one basis (round 5's R5-P1-4, ADR-0366).
    A role counted only from partway through the window adds its whole stock to the end alone,
    so the total that mixes bases is no change at all: p5e read +12,044 where the roles counted
    from the start moved +593 and every role's own change summed to −3,427. The answer passes
    when it states either like-for-like figure and not the mixed one."""
    moves = _watched_roles(expect, space)
    if not moves:
        return Verdict(False, "the Space read no watched roles for this category")
    longest = max(move["span_days"] for move in moves)
    whole = [move for move in moves if move["span_days"] == longest]
    from_start = sum(move["latest"] - move["start"] for move in whole)
    own_changes = sum(move["latest"] - move["start"] for move in moves)
    mixed = sum(move["latest"] for move in moves) - sum(m["start"] for m in whole)
    said = _figures(transcript.final_answer)
    like = [
        want for want in (from_start, own_changes) if any(_near(f, want) for f in said)
    ]
    wrong = any(_near(f, mixed) for f in said) and not any(
        _near(abs(mixed), want) for want in (from_start, own_changes)
    )
    detail = (
        f"roles counted from the start {from_start:+,}, each role's own change summed "
        f"{own_changes:+,}, the mixed-basis total {mixed:+,}"
    )
    if wrong:
        return Verdict(False, f"the answer states the mixed-basis total; {detail}")
    if not like:
        return Verdict(False, f"the answer states no like-for-like total; {detail}")
    return Verdict(True, detail)


# --- retiring a task whose fixture is gone -------------------------------------------------


def _jobs_gone(ids: list[str], space: Space) -> str | None:
    missing = space.read(SpaceRoute.JOB, [("id", i) for i in ids]).get("missing") or []
    return f"postings {', '.join(missing)} are no longer served" if missing else None


def _not_on_hot(fact: dict[str, Any], space: Space) -> str | None:
    hot = space.read(SpaceRoute.HOT)
    hidden = set(hot.get("hidden_by_default") or ())
    rows = [
        row
        for row in (hot.get("lenses") or {}).get(fact["lens"]) or []
        if row.get("operator") not in hidden
    ][: fact["within"]]
    if any(_found(str(row.get("company") or ""), fact["company"]) for row in rows):
        return None
    return (
        f"{fact['company']} is not in the first {fact['within']} rows of {fact['lens']}"
    )


def _no_role_joined(fact: dict[str, Any], space: Space) -> str | None:
    moves = _watched_roles(fact, space)
    if len({move["span_days"] for move in moves}) > 1:
        return None
    return f"no watched role of {fact['category']} joined partway since {fact['since']}"


def _ranking_spread_out(fact: dict[str, Any], space: Space) -> str | None:
    """Why a per-company cap has nothing to spread, or None: the Space's own ranking of
    ``fact["arguments"]`` (a search_jobs call's), read with no cap, already names
    ``fact["companies_below"]`` companies or more on its first page (ADR-0365)."""
    schema = BY_NAME["search_jobs"].input_schema
    uncapped = tool_arguments.with_defaults(
        schema, {**fact["arguments"], "per_company": 0}
    )
    rows = space.read(SpaceRoute.SEARCH, search_jobs.space_params(uncapped, None))
    companies = {per_company_cap.company(row) for row in rows}
    if len(companies) < fact["companies_below"]:
        return None
    return (
        f"the uncapped ranking's first page already names {len(companies)} companies, so no "
        "one company piles up"
    )


#: A task's ``requires``: the live facts its premise rests on, each read before the run.
_REQUIREMENTS: dict[str, Callable[[Any, Space], str | None]] = {
    "jobs": _jobs_gone,
    "hot_row": _not_on_hot,
    "roles_joined_partway": _no_role_joined,
    "piled_ranking": _ranking_spread_out,
    "found_late_burst": _no_found_late_burst,
}


def retired(task: dict[str, Any], space: Space) -> str | None:
    """Why ``task`` cannot be judged today, or None: a fact its premise rests on (``requires``)
    is gone from the live data, as t32's Eversource pair closed (round-5 critique R5-P2-9,
    ADR-0366). Its run is not made, and is counted apart from the judged ones, never failed."""
    for kind, fact in (task.get("requires") or {}).items():
        if reason := _REQUIREMENTS[kind](fact, space):
            return reason
    return None


# --- any_of --------------------------------------------------------------------------------


def verify_any_of(
    expect: dict[str, Any], transcript: Transcript, space: Space
) -> Verdict:
    """The first of ``expect["checks"]`` (each ``{"verifier", "expect"}``) that passes: a task
    two right paths answer, each judged by its own truth, rather than one path frozen in as the
    only right one (round-3 critique P1-6, ADR-0334)."""
    failed = []
    for n, check in enumerate(expect["checks"], 1):
        verdict = VERIFIERS[check["verifier"]](
            check.get("expect") or {}, transcript, space
        )
        if verdict.passed:
            return Verdict(True, f"path {n} ({check['verifier']}): {verdict.detail}")
        failed.append(f"path {n} ({check['verifier']}): {verdict.detail}")
    return Verdict(False, " | ".join(failed))


# --- all_of --------------------------------------------------------------------------------


def verify_all_of(
    expect: dict[str, Any], transcript: Transcript, space: Space
) -> Verdict:
    """Every one of ``expect["checks"]`` (each ``{"verifier", "expect"}``): one path judged on
    more than one truth."""
    said = []
    for n, check in enumerate(expect["checks"], 1):
        verdict = VERIFIERS[check["verifier"]](
            check.get("expect") or {}, transcript, space
        )
        if not verdict.passed:
            return Verdict(False, f"check {n} ({check['verifier']}): {verdict.detail}")
        said.append(f"check {n} ({check['verifier']}): {verdict.detail}")
    return Verdict(True, " & ".join(said))


VERIFIERS: dict[str, Verifier] = {
    "tool_args": verify_tool_args,
    # The brief's fixed name, which the sealed held-out file uses; the same check.
    "search_args": verify_tool_args,
    "title_keyword_rows": verify_title_keyword_rows,
    "sponsorship_polarity": verify_sponsorship_polarity,
    "operator_mix": verify_operator_mix,
    "senior_caveat": verify_senior_caveat,
    "trend_sign": verify_trend_sign,
    "hot_top": verify_hot_top,
    "found_late_share": verify_found_late_share,
    "blocking_named": verify_blocking_named,
    "mentions": verify_mentions,
    "country_split": verify_country_split,
    "employer_unflagged": verify_employer_unflagged,
    "watched_roles_total": verify_watched_roles_total,
    "page_companies": verify_page_companies,
    "any_of": verify_any_of,
    "all_of": verify_all_of,
}


# --- replaying recorded calls: the verifier self-test (ADR-0334) --------------------------

#: The eval's recorded tool calls, each task's runs with the final answer a right run gives, and
#: the Space's replies behind them: `record_space_mcp_eval_calls.py` writes it, and
#: `tests/test_space_mcp_eval.py` replays it, so a change to a tool's output that breaks a
#: verifier fails the PR that makes it rather than the next hosted eval.
RECORDED_CALLS = _ROOT / "tests" / "fixtures" / "space_mcp_eval_recorded_calls.json"


def _now() -> datetime:
    """The verifiers' "now", which :func:`tools_clock_at` holds with the tools'."""
    return datetime.now(UTC)


@contextmanager
def tools_clock_at(when: datetime) -> Iterator[None]:
    """Every tool's "now", and the verifiers', held at ``when`` while inside: a window or an age
    is counted back from it into the URLs a tool or a verifier reads, which a replay must build
    exactly as recorded."""
    patched = [
        (sys.modules[__name__], "_now", lambda: when),
        (company_profile, "_now", lambda: when),
        (read_trends, "_now", lambda: when),
        (answer_date, "today", lambda: when.date()),
    ]
    saved = [(module, name, getattr(module, name)) for module, name, _ in patched]
    for module, name, value in patched:
        setattr(module, name, value)
    try:
        yield
    finally:
        for module, name, value in saved:
            setattr(module, name, value)


def replayed(run: dict[str, Any], fetch: Fetch) -> Transcript:
    """A transcript of ``run``'s calls (``{"name", "arguments"}`` each), answered by the tools
    themselves reading the Space through ``fetch``, and ending in ``run["answer"]``: what a
    connected run that made those calls would have been judged on."""
    transcript = Transcript(
        final_answer=run["answer"],
        server_status="connected",
        tools=[TOOL_PREFIX + tool.name for tool in REGISTRY],
    )
    for asked in run["calls"]:
        client = SpaceClient(base=SPACE_URL, fetch=fetch)
        try:
            result, failed = call_tool(client, asked["name"], asked["arguments"]), False
        except ToolFailure as exc:
            result, failed = str(exc), True
        transcript.calls.append(
            ToolCall(asked["name"], asked["arguments"], result, failed)
        )
    return transcript


# --- running -------------------------------------------------------------------------------


def mcp_config(env: dict[str, str], http_url: str | None = None) -> dict[str, Any]:
    """The one server a run may use: this checkout's ``python -m headstart.space_mcp``, or with
    ``http_url`` the Streamable HTTP endpoint at that URL (ADR-0267's hosted ``/mcp``). Another
    Space's URL, when set, is written as ``${HEADSTART_SPACE_URL}``, which Claude Code expands
    from its own environment."""
    if http_url:
        return {"mcpServers": {NAME: {"type": "http", "url": http_url}}}
    server_env = {"PYTHONPATH": str(_ROOT / "src")}
    if env.get(URL_VAR):
        server_env[URL_VAR] = "${" + URL_VAR + "}"
    return {
        "mcpServers": {
            NAME: {
                "command": sys.executable,
                "args": ["-m", "headstart.space_mcp"],
                "env": server_env,
            }
        }
    }


def command(prompt: str, config_path: str) -> list[str]:
    """The ``claude`` invocation for one task. ``--tools ""`` removes every built-in tool (Bash,
    web search, files), so the answer can only come from this server; ``--strict-mcp-config``
    drops every other configured MCP server; ``--allowedTools`` lets every tool in the
    server's registry run without a permission prompt, so a tool added later needs no edit here."""
    return [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--mcp-config",
        config_path,
        "--strict-mcp-config",
        "--allowedTools",
        ",".join(TOOL_PREFIX + tool.name for tool in REGISTRY),
        "--tools",
        "",
        "--no-session-persistence",
    ]


def _metrics(transcript: Transcript) -> dict[str, Any]:
    calls = transcript.calls
    sizes = [len(call.result or "") for call in calls]
    refused = [i for i, call in enumerate(calls) if call.refused]
    # Corrected: the very next call retries the same tool and is answered.
    corrected = [
        i
        for i in refused
        if i + 1 < len(calls)
        and calls[i + 1].name == calls[i].name
        and calls[i + 1].succeeded
    ]
    return {
        "tool_calls": len(calls),
        "tool_result_chars": sum(sizes),
        "largest_tool_result_chars": max(sizes, default=0),
        "errors": sum(call.is_error for call in calls),
        "refusals": len(refused),
        "refusals_corrected": len(corrected),
        "calls": [
            {
                "name": call.name,
                "arguments": call.arguments,
                "is_error": call.is_error,
                "result_chars": len(call.result or ""),
            }
            for call in calls
        ],
    }


def judge(
    task: dict[str, Any], transcript: Transcript, space: Space
) -> tuple[str, str]:
    """The task's outcome, ``"pass" | "fail" | "error"``, and why; error is a verifier that could
    not read the Space or resolve a company, or whose case is gone from the live data
    (:class:`NotJudged`), so the task was not judged."""
    try:
        verdict = VERIFIERS[task["verifier"]](
            task.get("expect") or {}, transcript, space
        )
    except (SpaceError, ToolFailure, NotJudged) as exc:
        return "error", f"the verifier could not judge: {exc}"
    return ("pass" if verdict.passed else "fail"), verdict.detail


#: How long, in milliseconds, ``claude`` waits for the server to connect before a run starts.
#: Waiting was not enough on a slow client network: 44 of 123 round-4 runs still started with
#: the server "pending" (round-4 critique P1-4).
MCP_CONNECT_TIMEOUT_MS = "60000"


def run_env(env: dict[str, str], http_url: str | None) -> dict[str, str]:
    """The environment ``claude`` runs in. Claude Code 2.1.212's ``-p`` does not wait for an
    HTTP server to connect: the run starts with it "pending" and no tools, and every task fails
    with 0 calls (round-2 critique, 2026-09-29). ``MCP_CONNECTION_NONBLOCKING=false`` makes it
    wait (ADR-0325), but only for ``MCP_CONNECT_TIMEOUT_MS``, 5,000 ms by default, and the
    hosted server connects and lists its tools in about 3 to 10 s, so the start waits up to
    :data:`MCP_CONNECT_TIMEOUT_MS` too. ``MCP_TIMEOUT`` bounds each connection attempt, not the
    start. The caller's environment may set its own of either."""
    env = {"MCP_TIMEOUT": MCP_CONNECT_TIMEOUT_MS, **env}
    if http_url:
        return {
            "MCP_CONNECT_TIMEOUT_MS": MCP_CONNECT_TIMEOUT_MS,
            **env,
            "MCP_CONNECTION_NONBLOCKING": "false",
        }
    return env


def run_task(
    task: dict[str, Any],
    env: dict[str, str],
    prefix: Path,
    space: Callable[[], Space],
    http_url: str | None = None,
    repeat: int = 1,
) -> dict[str, Any]:
    """Run one task, saving its transcript as it streams, and return its result record. A run
    whose server was not connected at its start is an error, not judged: the model had no
    tools. ``repeat`` numbers the pass this run belongs to. A task whose fixture is gone
    (:func:`retired`) is not run: its record says why, as ``retired``."""
    unrun = {
        "id": task["id"],
        "repeat": repeat,
        "verifier": task["verifier"],
        "tool_calls": 0,
        "largest_tool_result_chars": 0,
        "wall_s": 0.0,
    }
    try:
        gone = retired(task, space()) if task.get("requires") else None
    except (SpaceError, ToolFailure) as exc:
        return {
            **unrun,
            "verdict": "error",
            "detail": f"its fixture went unread: {exc}",
        }
    if gone:
        return {**unrun, "verdict": "retired", "detail": gone}
    stem = f"{prefix.name}_{task['id']}_r{repeat}"
    transcript_path = prefix.with_name(f"{stem}_transcript.jsonl")
    stderr_path = prefix.with_name(f"{stem}_stderr.log")
    lines: list[str] = []
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="space-mcp-eval-") as scratch:
        config_path = Path(scratch) / "mcp.json"
        config_path.write_text(json.dumps(mcp_config(env, http_url)), encoding="utf-8")
        with (
            transcript_path.open("w", encoding="utf-8") as saved,
            stderr_path.open("w", encoding="utf-8") as stderr,
            subprocess.Popen(
                command(task["prompt"], str(config_path)),
                cwd=scratch,  # no CLAUDE.md, project MCP servers or memory of this repo
                env=run_env(env, http_url),
                stdout=subprocess.PIPE,
                stderr=stderr,
                text=True,
                encoding="utf-8",
            ) as proc,
        ):
            timed_out = threading.Event()

            def kill() -> None:
                timed_out.set()
                proc.kill()

            killer = threading.Timer(TASK_TIMEOUT_S, kill)
            killer.start()
            for line in proc.stdout:
                saved.write(line)
                saved.flush()
                lines.append(line)
            killer.cancel()
            proc.wait()
    wall_s = time.monotonic() - started
    transcript = parse(lines)
    if reason := why_not_connected(transcript):
        outcome, detail = "error", reason
    else:
        outcome, detail = judge(task, transcript, space())
    return {
        "id": task["id"],
        "repeat": repeat,
        "verifier": task["verifier"],
        "verdict": outcome,
        "detail": detail,
        **_metrics(transcript),
        "wall_s": round(wall_s, 1),
        "timed_out": timed_out.is_set(),
        "run_error": transcript.run_error,
        "server_status": transcript.server_status,
        "model": transcript.model,
        "cost_usd": transcript.cost_usd,
        "final_answer": transcript.final_answer,
        "transcript": str(transcript_path.relative_to(_ROOT)),
    }


def summary(records: list[dict[str, Any]]) -> list[str]:
    """§9's bar over one run's records: at most one task wrong (11 of 12, 3 of 4), a median of at
    most three tool calls, no result past ~10,000 tokens, and every refusal corrected next call.
    The bars score only the runs that were judged. A run not judged (its server was not
    connected, or its verifier could not read the Space) says nothing about the model, so it is
    named on a line of its own, first, and that line is missed while any is left. With nothing
    judged, no bar is met. A retired task (its fixture is gone, ADR-0366) is neither: it is
    named on a line that holds no bar, saying to replace it."""
    unjudged = [r["id"] for r in records if r["verdict"] == "error"]
    gone = [r["id"] for r in records if r["verdict"] == "retired"]
    judged = [r for r in records if r["verdict"] not in ("error", "retired")]
    n = len(judged)
    correct = sum(r["verdict"] == "pass" for r in judged)
    median = statistics.median(r["tool_calls"] for r in judged) if judged else 0
    largest = max((r["largest_tool_result_chars"] for r in judged), default=0)
    refusals = sum(r["refusals"] for r in judged)
    corrected = sum(r["refusals_corrected"] for r in judged)

    def mark(met: bool) -> str:
        return "met" if met and judged else "MISSED"

    tokens = f"{LARGE_RESULT_CHARS:,}, about 10,000 tokens"
    retired_line = (
        f"retired: {len(gone)} of {len(records)} ({', '.join(gone)}), their fixture gone "
        "from the live data: replace them"
    )
    return ([retired_line] if gone else []) + [
        f"not judged: {len(unjudged)} of {len(records)}"
        + (f" ({', '.join(unjudged)})" if unjudged else "")
        + f" — {'MISSED' if unjudged else 'met'}",
        (
            f"correct: {correct} of {n} judged (bar: at most one wrong) — "
            f"{mark(correct >= n - 1)}"
        ),
        (
            f"median tool calls: {median:g} (bar: at most {MEDIAN_CALLS_BAR}) — "
            f"{mark(median <= MEDIAN_CALLS_BAR)}"
        ),
        (
            f"largest tool result: {largest:,} characters (bar: {tokens}) — "
            f"{mark(largest <= LARGE_RESULT_CHARS)}"
        ),
        (
            f"refusals corrected within one call: {corrected} of {refusals} — "
            f"{mark(corrected == refusals)}"
        ),
    ]


def tally(passes: list[list[dict[str, Any]]]) -> list[str]:
    """Each task's verdicts across passes, scored over its judged runs only, as the summary is:
    "t03: 1 of 2 judged passed (pass, fail, error)"."""
    by_task: dict[str, list[str]] = {}
    for records in passes:
        for record in records:
            by_task.setdefault(record["id"], []).append(record["verdict"])
    return [
        f"{task}: {verdicts.count('pass')} of "
        f"{len(verdicts) - verdicts.count('error') - verdicts.count('retired')} "
        f"judged passed ({', '.join(verdicts)})"
        for task, verdicts in by_task.items()
    ]


def load_heldout(path: Path, sealed: str | None) -> list[dict[str, Any]]:
    """The held-out tasks, parsed only once the file's sha256 is the one the tasks file seals."""
    raw = path.read_bytes()
    if sealed is None:
        raise SystemExit(
            "the tasks file seals no held-out hash (heldout_sha256 is null)"
        )
    digest = hashlib.sha256(raw).hexdigest()
    if digest != sealed:
        raise SystemExit(
            f"{path} has sha256 {digest}, not the sealed {sealed}; not reading its tasks"
        )
    return json.loads(raw)["tasks"]


def _dry_run(
    tasks: list[dict[str, Any]],
    sealed: bool,
    env: dict[str, str],
    http_url: str | None = None,
) -> None:
    config_path = "<a scratch directory>/mcp.json"
    print(f"MCP config ({config_path}):", flush=True)
    print(json.dumps(mcp_config(env, http_url), indent=2), flush=True)
    for task in tasks:
        prompt = "<sealed prompt>" if sealed else task["prompt"]
        print(f"\n{task['id']} [{task['verifier']}]", flush=True)
        print("  " + shlex.join(command(prompt, config_path)), flush=True)
    print(f"\n{len(tasks)} tasks; nothing was run.", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    tasks_from = parser.add_mutually_exclusive_group()
    tasks_from.add_argument("--tasks", type=Path, default=ITERATION_TASKS)
    tasks_from.add_argument(
        "--heldout",
        type=Path,
        help="a sealed held-out tasks file, checked against the iteration file's hash",
    )
    parser.add_argument(
        "--only", help="run only these task ids, comma-separated (t07,t12)"
    )
    parser.add_argument("--dry-run", action="store_true", help="print, run nothing")
    parser.add_argument(
        "--http",
        metavar="URL",
        help="register the Streamable HTTP endpoint at URL (the Space's /mcp) instead of "
        "this checkout's stdio server",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        metavar="N",
        help="run the whole set N times, one pass after another, and tally each task",
    )
    args = parser.parse_args(argv)

    if args.heldout:
        # The seal is always the committed iteration file's, never a file the caller names.
        sealed = json.loads(ITERATION_TASKS.read_text(encoding="utf-8"))
        tasks = load_heldout(args.heldout, sealed.get("heldout_sha256"))
        label = "heldout"
    else:
        tasks = json.loads(args.tasks.read_text(encoding="utf-8"))["tasks"]
        label = "iteration"
    if args.only:
        wanted = [i.strip() for i in args.only.split(",") if i.strip()]
        if missing := sorted(set(wanted) - {task["id"] for task in tasks}):
            raise SystemExit(f"no task {', '.join(missing)}")
        tasks = [task for task in tasks if task["id"] in wanted]
    kinds = {t["verifier"] for t in tasks} | {
        check["verifier"]
        for t in tasks
        for check in (t.get("expect") or {}).get("checks") or []
    }
    if unknown := sorted(kinds - VERIFIERS.keys()):
        raise SystemExit(f"unknown verifier kinds: {', '.join(unknown)}")

    env = dict(os.environ)
    if args.dry_run:
        _dry_run(tasks, sealed=bool(args.heldout), env=env, http_url=args.http)
        return 0
    base = (env.get(URL_VAR) or "").strip() or SPACE_URL

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H%MZ")
    prefix = ARTIFACTS / f"{stamp}_{label}{'_http' if args.http else ''}"
    results_path = prefix.with_name(f"{prefix.name}_results.jsonl")
    print(
        f"{len(tasks)} {label} tasks × {args.repeat}; results to {results_path}",
        flush=True,
    )
    passes: list[list[dict[str, Any]]] = []
    with results_path.open("a", encoding="utf-8") as results:
        for repeat in range(1, args.repeat + 1):
            passes.append([])
            for task in tasks:
                record = run_task(
                    task, env, prefix, lambda: SpaceClient(base=base), args.http, repeat
                )
                passes[-1].append(record)
                results.write(json.dumps(record, ensure_ascii=False) + "\n")
                results.flush()
                print(
                    f"{record['id']} r{repeat} {record['verdict'].upper()} · "
                    f"{record['tool_calls']} calls · "
                    f"largest result {record['largest_tool_result_chars']:,} chars · "
                    f"{record['wall_s']:.0f}s · {record['detail']}",
                    flush=True,
                )
            print(
                f"\npass {repeat} of {args.repeat}:\n" + "\n".join(summary(passes[-1])),
                flush=True,
            )
    print("\n" + "\n".join(tally(passes)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
