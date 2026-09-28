#!/usr/bin/env python3
"""Run the Space MCP server's evaluation tasks with Claude Code as the client (plan §9).

Each task is one ``claude -p`` run in an empty scratch directory, with the ``headstart-space``
server as the only MCP server, no built-in tools, and only the server's registered tools
allowed. The run's stream-json transcript is saved line by line as it arrives, then parsed into
tool calls (name and arguments), tool results (their characters and whether they were errors)
and the final answer, and judged by the task's verifier. ``trend_sign`` and ``hot_top`` re-read
the Space's public read routes themselves, as the server does, so they check the answer against
the Space's own figures rather than against what the agent was told. ``tool_args`` (``search_args``
in the brief's fixed schema) checks the arguments instead and trusts the Space to apply them:
``strict=1`` makes it refuse any it would drop.

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
  python scripts/eval/space_mcp_eval.py --heldout <sealed file>
  python scripts/eval/space_mcp_eval.py --http https://imposeidon-headstart-search.hf.space/mcp
``HEADSTART_SPACE_URL``, when set, points both the server and the verifiers at another Space.
``--http`` registers the hosted Streamable HTTP endpoint (ADR-0267) in place of the stdio server;
the verifiers still read ``HEADSTART_SPACE_URL`` or the deployed Space.
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
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
from headstart.mcp_protocol import tool_arguments
from headstart.mcp_protocol.messages import ToolFailure
from headstart.space_mcp import company_scope
from headstart.space_mcp.server import BY_NAME, NAME, URL_VAR
from headstart.space_mcp.space_client import (
    SPACE_URL,
    SpaceClient,
    SpaceError,
    SpaceRoute,
)
from headstart.space_mcp.space_tool import ANSWER_CEILING_CHARS
from headstart.space_mcp.tools import REGISTRY

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


def _found(text: str, term: str | list[str]) -> bool:
    """``term`` in ``text``, case-blind; a list is alternatives, any one of which will do."""
    terms = term if isinstance(term, list) else [term]
    return any(t and t.casefold() in text.casefold() for t in terms)


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


def verify_tool_args(
    expect: dict[str, Any], transcript: Transcript, space: Space
) -> Verdict:
    """One successful call of ``expect["tool"]`` (search_jobs by default) whose arguments meet
    every ``must`` rule, one ``must_any`` alternative when given, and ``query_must_not_contain``.
    An argument the call left out is judged at its schema default, as the server reads it."""
    tool = expect.get("tool") or "search_jobs"
    schema = BY_NAME[tool].input_schema
    calls = [c for c in transcript.calls if c.name == tool and c.succeeded]
    if not calls:
        return Verdict(False, f"no successful {tool} call")
    judged = [
        (call, _misses(expect, tool_arguments.with_defaults(schema, call.arguments)))
        for call in calls
    ]
    for call, misses in judged:
        if not misses:
            return Verdict(
                True, f"{tool} {json.dumps(call.arguments, ensure_ascii=False)}"
            )
    call, misses = min(judged, key=lambda pair: len(pair[1]))
    return Verdict(
        False,
        f"no {tool} call meets every rule; closest "
        f"{json.dumps(call.arguments, ensure_ascii=False)}: {'; '.join(misses)}",
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


def stated_direction(answer: str) -> tuple[str | None, str | None]:
    """The direction an answer states ("up", "down" or "flat") and the text that states it.

    A quoted signed hiring figure decides first: it is the netted figure itself, where a
    direction word may describe raw openings ("openings fell, but hiring is +3"). Otherwise the
    first direction word decides; answers lead with their verdict."""
    if signed := _SIGNED_HIRING.search(answer):
        return ("up" if signed.group(1) == "+" else "down"), signed.group(0)
    match = _DIRECTION_RE.search(_ECHO.sub(" ", answer))
    if match is None:
        return None, None
    return _DIRECTION[match.group(1).lower()], match.group(1)


def verify_trend_sign(
    expect: dict[str, Any], transcript: Transcript, space: Space
) -> Verdict:
    """The sign of the Space's own netted hiring for these companies, category and days, against
    the direction the final answer states."""
    picks = [company_scope.for_trends(space, c) for c in expect.get("companies") or []]
    days = int(expect.get("days") or 30)
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
    params = [("since", since)]
    if expect.get("category"):
        params.append(("family", expect["category"]))
    params += [("company", pick.key) for pick in picks]
    # No `split`: the reading's total is the same under every breakdown, and the total is all
    # this checks. Built here, not borrowed from read_trends, so a bug there cannot hide here.
    reading = space.read(SpaceRoute.TRENDS, params).get("reading")
    if reading is None:
        return Verdict(
            False, "the Space could not read this trend, so there is no sign to check"
        )
    move = (reading.get("total") or {}).get("move")
    if move is None and len(reading.get("lines") or []) == 1:
        move = reading["lines"][0]["move"]
    if move is None:
        return Verdict(False, "the reading has neither a total nor a single line")
    hiring = move.get("hiring") or 0
    want = "up" if hiring > 0 else "down" if hiring < 0 else "flat"
    said, word = stated_direction(transcript.final_answer)
    return Verdict(
        said == want,
        f"the Space's hiring is {hiring:+,} ({want}); the answer says {said} ({word!r})",
    )


# --- hot_top -------------------------------------------------------------------------------

_COMPANY_SUFFIX = re.compile(
    r"[,.]?\s+(inc|llc|ltd|limited|corp|corporation|co|plc|gmbh|ag|sa|pvt)\.?$",
    re.IGNORECASE,
)


def _named(answer: str, row: dict[str, Any]) -> bool:
    company = str(row.get("company") or "")
    return _found(
        answer, [company, _COMPANY_SUFFIX.sub("", company), str(row.get("key") or "")]
    )


def verify_hot_top(
    expect: dict[str, Any], transcript: Transcript, space: Space
) -> Verdict:
    """At least N-1 of /hot's top N on the Lens named, after the Operators the Hiring now tab
    hides."""
    lens, top = expect.get("lens") or "expansion", int(expect.get("top") or 5)
    hot = space.read(SpaceRoute.HOT)
    hidden = set(hot.get("hidden_by_default") or ())
    rows = [
        row
        for row in (hot.get("lenses") or {}).get(lens) or []
        if row.get("operator") not in hidden
    ][:top]
    need = max(len(rows) - 1, 0)
    named = [row["company"] for row in rows if _named(transcript.final_answer, row)]
    return Verdict(
        len(named) >= need,
        f"names {len(named)} of /hot's top {len(rows)} on {lens} (needs {need}): "
        f"{', '.join(r['company'] for r in rows)}",
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
    Blocking filter, and the final answer mentions it."""
    want = expect.get("argument")
    named = [
        argument
        for call in transcript.calls
        if call.name == "search_jobs" and call.succeeded
        if (argument := _blocking_named_in(call.result or ""))
    ]
    candidates = [a for a in named if want is None or a == want]
    if not candidates:
        return Verdict(
            False,
            f"no search_jobs result named {want or 'any'} as blocking (named: {named})",
        )
    for argument in candidates:
        if _mentions_argument(transcript.final_answer, argument):
            return Verdict(
                True, f"a result named {argument} and the answer mentions it"
            )
    return Verdict(
        False,
        f"a result named {candidates[0]}, but the final answer does not mention it",
    )


# --- mentions ------------------------------------------------------------------------------


def verify_mentions(
    expect: dict[str, Any], transcript: Transcript, space: Space
) -> Verdict:
    """Every ``all`` term (a list is alternatives) and one ``any`` term in the final answer, and
    every ``tool_results_all`` term in the tool results."""
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
    return Verdict(not missing, "; ".join(missing) or "every term found")


VERIFIERS: dict[str, Verifier] = {
    "tool_args": verify_tool_args,
    # The brief's fixed name, which the sealed held-out file uses; the same check.
    "search_args": verify_tool_args,
    "trend_sign": verify_trend_sign,
    "hot_top": verify_hot_top,
    "blocking_named": verify_blocking_named,
    "mentions": verify_mentions,
}


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
    not read the Space or resolve a company, so the task was not judged."""
    try:
        verdict = VERIFIERS[task["verifier"]](
            task.get("expect") or {}, transcript, space
        )
    except (SpaceError, ToolFailure) as exc:
        return "error", f"the verifier could not judge: {exc}"
    return ("pass" if verdict.passed else "fail"), verdict.detail


def run_task(
    task: dict[str, Any],
    env: dict[str, str],
    prefix: Path,
    space: Callable[[], Space],
    http_url: str | None = None,
) -> dict[str, Any]:
    """Run one task, saving its transcript as it streams, and return its result record."""
    transcript_path = prefix.with_name(f"{prefix.name}_{task['id']}_transcript.jsonl")
    stderr_path = prefix.with_name(f"{prefix.name}_{task['id']}_stderr.log")
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
                env=env,
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
    outcome, detail = judge(task, transcript, space())
    return {
        "id": task["id"],
        "verifier": task["verifier"],
        "verdict": outcome,
        "detail": detail,
        **_metrics(transcript),
        "wall_s": round(wall_s, 1),
        "timed_out": timed_out.is_set(),
        "run_error": transcript.run_error,
        "model": transcript.model,
        "cost_usd": transcript.cost_usd,
        "final_answer": transcript.final_answer,
        "transcript": str(transcript_path.relative_to(_ROOT)),
    }


def summary(records: list[dict[str, Any]]) -> list[str]:
    """§9's bar over one run's records: at most one task wrong (11 of 12, 3 of 4), a median of at
    most three tool calls, no result past ~10,000 tokens, and every refusal corrected next call."""
    n = len(records)
    correct = sum(r["verdict"] == "pass" for r in records)
    median = statistics.median(r["tool_calls"] for r in records) if records else 0
    largest = max((r["largest_tool_result_chars"] for r in records), default=0)
    refusals = sum(r["refusals"] for r in records)
    corrected = sum(r["refusals_corrected"] for r in records)

    def mark(met: bool) -> str:
        return "met" if met else "MISSED"

    tokens = f"{LARGE_RESULT_CHARS:,}, about 10,000 tokens"
    return [
        f"correct: {correct} of {n} (bar: at most one wrong) — {mark(correct >= n - 1)}",
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
    parser.add_argument("--only", help="run one task id")
    parser.add_argument("--dry-run", action="store_true", help="print, run nothing")
    parser.add_argument(
        "--http",
        metavar="URL",
        help="register the Streamable HTTP endpoint at URL (the Space's /mcp) instead of "
        "this checkout's stdio server",
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
        tasks = [task for task in tasks if task["id"] == args.only]
        if not tasks:
            raise SystemExit(f"no task {args.only!r}")
    if unknown := sorted({t["verifier"] for t in tasks} - VERIFIERS.keys()):
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
    print(f"{len(tasks)} {label} tasks; results to {results_path}", flush=True)
    records = []
    with results_path.open("a", encoding="utf-8") as results:
        for task in tasks:
            record = run_task(
                task, env, prefix, lambda: SpaceClient(base=base), args.http
            )
            records.append(record)
            results.write(json.dumps(record, ensure_ascii=False) + "\n")
            results.flush()
            print(
                f"{record['id']} {record['verdict'].upper()} · {record['tool_calls']} calls · "
                f"largest result {record['largest_tool_result_chars']:,} chars · "
                f"{record['wall_s']:.0f}s · {record['detail']}",
                flush=True,
            )
    print("\n" + "\n".join(summary(records)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
