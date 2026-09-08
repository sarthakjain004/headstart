"""The log lines `scripts/runlog/` parses are a public contract — this is where it is written down.

Ten analysers under `scripts/runlog/` carry ~90 compiled regexes against pipeline log lines. Until
this file existed nothing tied a single one of them to the code that emits it, and the predictable
happened twice: a regex outlived its emitter's wording and **said nothing**. `re.search` returns
`None`, the analyser's `if m:` skips its print, and the report shows a stage that "did nothing".

- `fanout_merge.KEEP_SET` asked for `keep-set: N live Boards` while `index prune` had been
  corrected to `keep-set: N Scrapable Boards (enabled ATSes)` — CONTEXT.md's counting vocabulary.
  Every `index prune` report lost its keep-set line, silently.
- `fanout_timing.PLAN_SHARD` and `fanout_plan.MAKESPAN` both required a clause `scrape_plan`
  **omits on a cold start**, where it packs in unitless cost units instead of minutes — so the two
  tools went blank on exactly the runs whose plan is least trustworthy.

A silent zero is worse than a crash here, because a human believes it. This file makes the drift
loud instead: **change an emitter's wording without changing its consumer and a test fails.**

## How it works

One table, :data:`CONTRACT`, with one :class:`Line` per log line a consumer parses. Each entry
names the consumer (`module.PATTERN` under `scripts/runlog/`), the emitter that writes the line,
and the message body as it really appears. Four checks run over it:

1. **The consumer parses a real line** — the pattern must match the body in both renderings
   `headstart.log._Formatter` produces: the local `HH:MM:SS [tag] body` and the GitHub Actions
   `::warning::[tag] body` an anomaly becomes.
2. **The emitter still says it** — every literal run inside the consumer's regex must still appear
   in a string the emitter's source can produce (f-strings composed through `+` and `if/else`,
   read with `ast`, no import). This is what catches a rewording. It also pins the `[tag]` prefix
   to the emitter module's own name, which is ADR-0039's rule and was broken once already
   (`__main__.py` logged `[feed]`).
3. **The emitter really emits it** (`emit=` entries only) — the emitter function is *called* under
   `caplog` and the pattern is matched against what it actually logged. This is the strongest form
   and it is used wherever the emitter is reachable without heavy deps or real data.
4. **Nothing is unaccounted for** — every module-level `re.compile` in `scripts/runlog/*.py` is
   either in this table or in :data:`EXEMPT` with a reason. Adding an analyser regex without a
   contract entry is what fails, so no human has to remember.

**Emitter-verified vs source-verified, and why the mix.** CI installs base deps only, so
`index.py` (lancedb/pyarrow/numpy), `role_trends.py` (numpy) and `embed_run.py` (torch,
sentence-transformers) cannot be imported here at all, and several other lines sit inside a
`main()` that wants a real ledger on disk. Those entries are source-verified: check 2 pins their
wording, check 1 pins the shape the pattern reads out of it. Entries carrying `emit=` add check 3
on top, so prefer `emit=` whenever an emitter becomes cheaply callable, and treat a growing
source-verified set as debt.

**Where the line between them actually falls is *values*.** Check 2 reads format strings, so `{n}`
becoming `{n:,}` leaves every literal around the placeholder untouched and sails through. Check 3
reads the rendered line, so it can catch that — but only when the fixture's own number renders
differently, and `{12:,}` is still `12`. Measured, not assumed: rewriting `scrape_run`'s job count
as `{progress.jobs:,}` left this file green while `run_logs.DONE` provably no longer matched the
line, because the fixture's shard had scraped 12 jobs. Every `emit=` fixture's counts are
four-figure for that reason. Three kinds of number stay below the line and a `:,` on one of them
would still pass: a per-run shard count (15 at most), `QUARANTINE_AT` (a five-strike streak), and
anything rendered through a float format — a minute figure, a percentage, an actual/predicted
ratio. Raising those would make the fixture lie about the pipeline rather than about the format
string, which is a worse trade than the gap.

**Neither check reads `body` against what the emitter produced**, either: check 1 matches the
pattern against `body` and check 3 matches it against the real records, but nothing asserts the
two are the same string. `body` is documentation, and it is only as honest as the person who last
edited the fixture beside it.

**One thing this file pins is not in the table at all**: the `stage= run= attempt=` line every
ingest entry point opens with (`observability.context`). No analyser parses it — a human greps it
— so it has no CONTRACT row and no regex to drift against. What it can lose instead is its
*vocabulary*, and it had: ten call sites saying the module's name, three borrowing
`pipeline.yml`'s job name, and one hyphenated and alone in that. The last two tests in this file
hold every call site to one rule, and catch an entry point that ships without the line at all.

## Adding a line

Append a `Line(...)`. `consumer` is `"<module>.<NAME>"` under `scripts/runlog/`; `emitter` is the
dotted module that writes it (or a repo-relative path, for the workflow's own shell); `body` is the
message without the clock or `[tag]` prefix, which the test adds. Give `why` one sentence saying
what this entry pins that its neighbours do not — several lines exist twice on purpose, once per
optional clause, because "a regex requiring an omitted-when-zero clause drops rows instead of
erroring" is this repo's recorded failure mode and each variant needs its own row.
"""

from __future__ import annotations

import ast
import functools
import importlib
import json
import logging
import re
import re._parser as sre_parse
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest

from headstart import log

_ROOT = Path(__file__).resolve().parents[1]
_RUNLOG = _ROOT / "scripts" / "runlog"

# An `emit` builds its log lines by calling the real emitter. It gets a tmp dir (several emitters
# write a shard report or a ledger beside their logging) and pytest's monkeypatch.
EmitFn = Callable[[Path, pytest.MonkeyPatch], None]


@dataclass(frozen=True)
class Line:
    """One pipeline log line, its consumer, and how this file proves the two still agree."""

    consumer: str
    """`"<module>.<NAME>"` — the compiled pattern under `scripts/runlog/` that reads this line."""

    emitter: str
    """The dotted module that writes it, or a repo-relative path for a non-Python emitter."""

    body: str
    """The message as emitted — no clock, no `[tag]`; the test renders those around it."""

    why: str = ""
    """What this entry pins that its neighbours do not (which optional clause, which branch)."""

    emit: EmitFn | None = None
    """Set to call the real emitter under `caplog`. Strongest check; use it where you can."""

    waived: tuple[str, ...] = ()
    """Literal runs check 2 must skip because they are not the emitter's to say. `why` says why."""

    tail: str = ""
    """Extra text appended to `body` before matching — a traceback, a second log record."""

    @property
    def tag(self) -> str | None:
        """The `[tag]` `log._Formatter` stamps — the emitter module's last dotted segment.

        None when the emitter is not a Python module at all: the storage check is shell inside
        `pipeline.yml`, so its lines carry no tag and go through the workflow's own stdout.
        """
        if self.emitter.endswith((".yml", ".yaml")):
            return None
        return self.emitter.rsplit(".", 1)[-1]

    @property
    def source(self) -> Path:
        """The emitter's file on disk."""
        if self.tag is None:
            return _ROOT / self.emitter
        return _ROOT / "src" / Path(*self.emitter.split(".")).with_suffix(".py")


# --------------------------------------------------------------------------------------------
# Emitters this test can call. Everything here must import under base deps alone (CI installs
# `.[dev]` and nothing else), so `index`, `role_trends` and `embed_run` are deliberately absent.
# --------------------------------------------------------------------------------------------


def _scrape_shard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    boards: bool,
    predicted: float | None,
    killed: bool,
) -> None:
    """Run one scrape shard's whole end-of-run report — six patterns read this one call.

    Every count here is deliberately four-figure. Check 3 only sees a value's *rendering*, so a
    fixture in single digits cannot notice one gaining thousands separators: `{12:,}` is still
    `12`, and this shard used to scrape 12 jobs from 2 boards.

    `retry_stats` is stubbed rather than driven through `http.fetch`: the retries line is formatted
    by `_report` itself, which is the part under contract, and reaching it for real would need a
    rate-limiting origin.
    """
    from headstart.ingest import scrape_run

    monkeypatch.setattr(
        scrape_run.http,
        "retry_stats",
        lambda: Counter({"429-ratelimit": 1207, "5xx": 2393}),
    )
    progress = scrape_run._Progress(2400)
    if boards:
        # 2393s clears `_SLOW_BOARD_S` (120), which is what emits the `slow board` line.
        progress.on_board("workday:acme/External", 1204, None, 2393.0)
        progress.on_board(
            "lever:beta", 0, "HTTPError: HTTP Error 404: Not Found", 1207.0
        )
        # The other 2,300 Boards, poked into the counters instead of driven through `on_board`.
        # `_report` reads only these aggregates, the two lines `on_board` writes for itself are
        # already pinned by the two real calls above, and 2,300 more records in `caplog` would
        # bury the assertion message under the shard's whole log on every failure in this file.
        # A four-figure error count is not contrived: it is one ATS walling this project's
        # egress, and it is the only way `(N board errors)` clears 999 as well.
        progress.seconds.extend([1.0] * 1200 + [2.0] * 1100)
        progress.jobs += 1200 * 3
        progress.boards_ok.extend(f"greenhouse:ok-{n}" for n in range(1200))
        progress.errors.update(
            {
                f"workable:walled-{n}": "HTTPError: HTTP Error 429: Too Many Requests"
                for n in range(1100)
            }
        )
    scrape_run._report(
        progress,
        tmp_path,
        elapsed=3603.0,
        predicted=predicted,
        serial=136.3 if predicted else None,
        killed=killed,
        shard="7",
        deferred=["greenhouse:gamma", "ashby:delta"] if killed else None,
    )


def _shard_full(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A shard that scraped boards, hit its time budget, and had errors and retries."""
    _scrape_shard(tmp_path, monkeypatch, boards=True, predicted=20.3, killed=True)


def _shard_unpredicted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A shard the planner had no estimate for — `done:` loses its `| predicted ...` tail."""
    _scrape_shard(tmp_path, monkeypatch, boards=True, predicted=None, killed=False)


def _shard_no_boards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A shard that finished zero boards — `observability.percentiles([])` is `{}`, not absent."""
    _scrape_shard(tmp_path, monkeypatch, boards=False, predicted=None, killed=False)


def _shard_report(**over: object) -> dict:
    """One shard's telemetry as `observability.write_shard` records it.

    Four figures of Boards attempted and errors raised, for the reason `_scrape_shard` gives: the
    run-level digest built from this counts both, and neither count can be checked for thousands
    separators from a fixture that stays under 999.
    """
    return {
        "shard": 0,
        "seconds": 903,
        "done": 18422,
        "undone": 3,
        "killed_by_budget": True,
        "deferred": ["workday:dollartree/dollartreeus"],
        "errors": {
            "lever:beta": "HTTPError: HTTP Error 404: Not Found",
            "greenhouse:gamma": "ReadTimeout: timed out",
            **{
                f"lever:bulk-{n}": "HTTPError: HTTP Error 404: Not Found"
                for n in range(1202)
            },
        },
        "retries": {"429-ratelimit": 7},
        "board_seconds": {"p50": 1.2, "max": 2393.0},
        "predicted_minutes": 20.3,
        "egress_ips": {"ip:1.2.3.4": 3, "colo:AMS": 3},
        **over,
    }


def _join_fanout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The join's run-level digest, with its `(R% of A attempted)` rate clause present."""
    from headstart.ingest import scrape_join

    scrape_join._report_shards([_shard_report()], 1204331, 21)


def _join_fanout_nothing_attempted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same digest from shards that finished no Board: the rate clause is omitted entirely."""
    from headstart.ingest import scrape_join

    scrape_join._report_shards([_shard_report(done=0)], 0, 0)


def _ledger_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`update_ledgers failures` over one shard: boards read as gone, and boards that were not.

    Every board here is seeded one strike short of `QUARANTINE_AT` so this run's 404 quarantines
    it and the capped per-Board sample line is emitted too; `lever:beta` is named rather than
    numbered because that line samples `sorted(quarantined)[:20]` and `beta` sorts ahead of the
    bulk. The `ashby:alive-*` rows exist only to be cleared, which is the one count on the
    `failures:` line no other input reaches.

    The bulk is four-figure for the reason `_scrape_shard` gives, and at a realistic magnitude:
    the real ledger carries a row per Board that has ever 404'd across ~20k Scrapable Boards.
    """
    import argparse

    from headstart.ingest import board_failures, update_ledgers

    gone = "HTTPError: HTTP Error 404: Not Found"
    fragments = tmp_path / "fragments"
    (fragments / "shard-0").mkdir(parents=True)
    (fragments / "shard-0" / "_shard_report.json").write_text(
        json.dumps(
            {
                "errors": {
                    "lever:beta": gone,
                    "greenhouse:gamma": "ReadTimeout: timed out",
                    **{f"lever:gone-{n}": gone for n in range(1203)},
                    **{
                        f"greenhouse:slow-{n}": "ReadTimeout: timed out"
                        for n in range(1010)
                    },
                },
                "boards_ok": [f"ashby:alive-{n}" for n in range(1150)],
            }
        ),
        encoding="utf-8",
    )
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    ledger = tmp_path / "board_failures.csv"
    row = functools.partial(
        board_failures.Failure,
        strikes=board_failures.QUARANTINE_AT - 1,
        last_seen_gone="2026-09-01T00:00:00+00:00",
    )
    board_failures.save(
        ledger,
        {
            "lever:beta": row(last_reason=gone),
            **{f"lever:gone-{n}": row(last_reason=gone) for n in range(1203)},
            **{f"ashby:alive-{n}": row(last_reason=gone) for n in range(1150)},
        },
    )
    update_ledgers.failures(
        argparse.Namespace(fragments=fragments, jobs=jobs, ledger=ledger)
    )


# --------------------------------------------------------------------------------------------
# The contract.
# --------------------------------------------------------------------------------------------

_SCRAPE_RUN = "headstart.ingest.scrape_run"
_SCRAPE_JOIN = "headstart.ingest.scrape_join"
_SCRAPE_PLAN = "headstart.ingest.scrape_plan"
_FILTER_TECH = "headstart.ingest.filter_tech"
_DESCRIPTIONS = "headstart.ingest.update_descriptions"
_LEDGERS = "headstart.ingest.update_ledgers"
_EMBED_PLAN = "headstart.ingest.embed_plan"
_EMBED_RUN = "headstart.ingest.embed_run"
_EMBED_MERGE = "headstart.ingest.embed_merge"
_META = "headstart.ingest.update_meta"
_INDEX = "headstart.ingest.index"
_TRENDS = "headstart.ingest.role_trends"
_EGRESS = "headstart.spare_egress"
_PIPELINE = ".github/workflows/pipeline.yml"

CONTRACT: tuple[Line, ...] = (
    # -- scrape shards (emitter-verified: `_report` and `_Progress.on_board` are callable) -----
    Line(
        consumer="run_logs.DONE",
        emitter=_SCRAPE_RUN,
        body=(
            "done: 4804 jobs from 2302 boards in 3603s (1101 board errors) | board seconds "
            "{'p50': 1.0, 'p90': 2.0, 'p99': 2.0, 'max': 2393.0} | predicted 20.3 min, "
            "actual/predicted 2.96x"
        ),
        why="the full shape: percentiles present and the planner's predicted/actual tail present",
        emit=_shard_full,
    ),
    Line(
        consumer="run_logs.DONE",
        emitter=_SCRAPE_RUN,
        body=(
            "done: 4804 jobs from 2302 boards in 3603s (1101 board errors) | board seconds "
            "{'p50': 1.0, 'p90': 2.0, 'p99': 2.0, 'max': 2393.0}"
        ),
        why="no plan estimate: the `| predicted ... actual/predicted ...x` tail is omitted",
        emit=_shard_unpredicted,
    ),
    Line(
        consumer="run_logs.DONE",
        emitter=_SCRAPE_RUN,
        body="done: 0 jobs from 0 boards in 3603s (0 board errors) | board seconds {}",
        why=(
            "zero boards finished: `percentiles([])` is `{}`, so the dict group must be `[^}]*`. "
            "With `+` the shard vanishes from every table AND from the run totals"
        ),
        emit=_shard_no_boards,
    ),
    Line(
        consumer="fanout_timing.SLOW_BOARD",
        emitter=_SCRAPE_RUN,
        body="slow board workday:acme/External: 1204 jobs in 2393s",
        why="the floor-ratio input; INFO by design (ADR-0039) so it never spends an annotation",
        emit=_shard_full,
    ),
    Line(
        consumer="fanout_errors.FAILED",
        emitter=_SCRAPE_RUN,
        body="lever:beta failed after 1207s: HTTPError: HTTP Error 404: Not Found",
        why="the per-board live failure line, logged as it happens rather than at the end",
        emit=_shard_full,
    ),
    Line(
        consumer="fanout_errors.KILLED",
        emitter=_SCRAPE_RUN,
        body=(
            "time budget reached after 60.0 min — banking a partial fragment; 2302/2400 boards "
            "done, 98 deferred to the next run"
        ),
        why="the budget kill that banks a partial fragment",
        emit=_shard_full,
    ),
    Line(
        consumer="fanout_errors.DEFERRED",
        emitter=_SCRAPE_RUN,
        body="deferred: greenhouse:gamma, ashby:delta",
        why="the deferred Board *names* — the whole point of the line over the count above it",
        emit=_shard_full,
    ),
    Line(
        consumer="fanout_errors.ERR_DIGEST",
        emitter=_SCRAPE_RUN,
        body="1101 board errors: 1101 HTTPError (workable 1100, lever 1)",
        why="the per-shard digest, distinct from the join's run-level one below",
        emit=_shard_full,
    ),
    Line(
        consumer="fanout_retries.RETRIES",
        emitter=_SCRAPE_RUN,
        body="retries: 429-ratelimit 1207, 5xx 2393 (total 3600)",
        why="`([^(]+)` stops at the `(total N)` tail, so the class list must not contain a paren",
        emit=_shard_full,
    ),
    # -- the join (emitter-verified: `_report_shards` takes plain dicts) -----------------------
    Line(
        consumer="fanout_errors.JOIN_ERR_DIGEST",
        emitter=_SCRAPE_JOIN,
        body=(
            "1204 board errors across 1 shards (6.5% of 18422 attempted): "
            "1203 HTTPError (lever 1203); 1 ReadTimeout (greenhouse 1)"
        ),
        why="the run-level digest WITH its rate clause — a count alone cannot say if a run is bad",
        emit=_join_fanout,
    ),
    Line(
        consumer="fanout_errors.JOIN_ERR_DIGEST",
        emitter=_SCRAPE_JOIN,
        body=(
            "1204 board errors across 1 shards: 1203 HTTPError (lever 1203); "
            "1 ReadTimeout (greenhouse 1)"
        ),
        why=(
            "no Board was finished, so `attempted` is 0 and the rate clause is omitted. A pattern "
            "requiring it drops the line instead of erroring"
        ),
        emit=_join_fanout_nothing_attempted,
    ),
    # -- update_ledgers failures (emitter-verified: one shard report and a seeded ledger) ------
    Line(
        consumer="fanout_errors.FAILURES",
        emitter=_LEDGERS,
        body=(
            "failures: 1204 of 2215 board error(s) read as gone (404/410) across 1 shard(s) | "
            "1204 ledger rows (1150 cleared by a successful scrape) | 1204 at/over 5 strikes -> "
            "board_failures.csv"
        ),
        why=(
            "the authoritative quarantine total. This pattern once read `N board(s) reported "
            "gone` and matched nothing for runs whose log carried the line verbatim"
        ),
        emit=_ledger_failures,
    ),
    Line(
        consumer="fanout_errors.UNMATCHED",
        emitter=_LEDGERS,
        body="  1011 error(s) did not read as gone; top classes: greenhouse ReadTimeout x1011",
        why="the gone-matcher's blind spot; a 404-ish class here run after run is the signal",
        emit=_ledger_failures,
    ),
    Line(
        consumer="fanout_errors.QUARANTINE",
        emitter=_LEDGERS,
        body="  quarantined  lever:beta (5 strikes, HTTPError: HTTP Error 404: Not Found)",
        why="the per-Board sample the emitter caps at 20 — never a total (see fanout_errors' docstring)",
        emit=_ledger_failures,
    ),
    # -- scrape_join corpus volume ------------------------------------------------------------
    Line(
        consumer="fanout_corpus.ATS_LINES",
        emitter=_SCRAPE_JOIN,
        body="workday.jsonl: 541801 lines from 15 shard(s)",
        why="raw per-ATS scrape volume, the denominator every later stage divides",
        waived=(".jsonl: ",),
        # `.jsonl` is part of the runtime filename (`f"{ats_file}: ..."`), not the format string.
    ),
    Line(
        consumer="fanout_corpus.JOIN_TOTAL",
        emitter=_SCRAPE_JOIN,
        body="wrote 1204331 lines across 21 ATS files -> data/jobs",
        why="the corpus total the tech gate's percentage is taken against",
    ),
    # -- filter_tech --------------------------------------------------------------------------
    Line(
        consumer="fanout_corpus.TECH",
        emitter=_FILTER_TECH,
        body="workday            76219   541801   14.1%",
        why="a fixed-width table row: the pattern reads columns, so `\\s+` must stay greedy",
    ),
    Line(
        consumer="fanout_corpus.TECH_TOTAL",
        emitter=_FILTER_TECH,
        body="TOTAL             183044  1204331   15.2%  (dropped 1021287 non-tech) -> data/jobs/tech",
        why="the TOTAL row, which carries a tail the per-ATS rows do not",
    ),
    Line(
        consumer="fanout_corpus.TECH_EMPTY",
        emitter=_FILTER_TECH,
        body=(
            "3 ATS(es) were in this run's slice but contributed zero rows: jazzhr, jobvite, "
            "sensehq — their boards failed, were deferred, or are genuinely empty"
        ),
        why="an ATS that scraped nothing used to be simply absent from the table above",
    ),
    Line(
        consumer="fanout_corpus.TECH_ZERO_TOTAL",
        emitter=_FILTER_TECH,
        body="no rows at all reached the tech filter -> data/jobs/tech is empty",
        why="the corpus-wide zero, which otherwise printed a header and stopped",
    ),
    # -- update_descriptions ------------------------------------------------------------------
    Line(
        consumer="fanout_corpus.STORE",
        emitter=_DESCRIPTIONS,
        body="prior store: 287,144 already-embedded ids",
        why="thousands separators: the group must be `[\\d,]+`, not `\\d+`",
    ),
    Line(
        consumer="fanout_corpus.DESC",
        emitter=_DESCRIPTIONS,
        body="workday: filled 6,012 from the store, learned 1,204, queued 830 to re-derive",
        why=(
            "the plain form. This pattern once also required a `settled N as having none` clause "
            "ADR-0089 deleted, so every ATS printed 0 while the log said `filled 6,012`"
        ),
    ),
    Line(
        consumer="fanout_corpus.DESC",
        emitter=_DESCRIPTIONS,
        body=(
            "workday: filled 6,012 from the store, learned 1,204, queued 830 to re-derive, "
            "412 still unrecorded"
        ),
        why="with the optional `, N still unrecorded` tail — the pattern must not require it",
    ),
    Line(
        consumer="fanout_corpus.SKIP",
        emitter=_DESCRIPTIONS,
        body="skip-list: 118,433 Jobs held",
        why="the ADR-0048 detail skip-list size",
    ),
    # -- embed_plan ---------------------------------------------------------------------------
    Line(
        consumer="fanout_plan.EMBED_PRIOR",
        emitter=_EMBED_PLAN,
        body="prior store: 287144 embedded ids (18320 without a description)",
        why="the embed planner's starting point",
    ),
    Line(
        consumer="fanout_plan.EMBED_NEW",
        emitter=_EMBED_PLAN,
        body="new Docs: 38912 (scanned 183044, already 143210, non-English 812, upgraded 110)",
        why="the English-only gate's own count, which nothing else reports",
    ),
    Line(
        consumer="fanout_plan.EMBED_ADMISSION",
        emitter=_EMBED_PLAN,
        body="admission: capped 52310 -> 39000 top-priority Docs",
        why="admission control; its absence means the plan fit under the cap",
    ),
    Line(
        consumer="fanout_plan.EMBED_MAKESPAN",
        emitter=_EMBED_PLAN,
        body="38912 Docs across 15 shards; predicted makespan ~41.2 min (total work Σ 610.4 min)",
        why="embed has one form only — token-bucket cost is always in minutes, unlike scrape_plan",
    ),
    Line(
        consumer="fanout_embed.EMBED_PLAN_SHARD",
        emitter=_EMBED_PLAN,
        body="shard 3: 2604 docs, ~41.2 min -> data/embeddings/assignments/shard-3.jsonl",
        why="the per-shard plan the executed side is compared against",
    ),
    # -- embed_run (heavy: torch + sentence-transformers, so source-verified only) -------------
    Line(
        consumer="fanout_embed.LOADING",
        emitter=_EMBED_RUN,
        body="loading nomic-ai/nomic-embed-text-v1.5 on cpu ...",
        why="which model and which device — the two facts every throughput number depends on",
    ),
    Line(
        consumer="fanout_embed.ASSIGNMENT",
        emitter=_EMBED_RUN,
        body=(
            "assignment: 2604 docs from data/embeddings/assignments/shard-7.jsonl | "
            "≤512:1980, ≤2048:624"
        ),
        why=(
            "the SHARDED path, which is what pipeline.yml runs (`--assignment`). The mono path's "
            "`to embed:` line is a local whole-corpus run and is not what CI produces"
        ),
    ),
    Line(
        consumer="fanout_embed.DONE",
        emitter=_EMBED_RUN,
        body=(
            "done: shard embedded 2604 (0 failed) -> data/embeddings/fragments/shard-7 "
            "(2604 vectors)"
        ),
        why="the sharded finish line; the mono path says `done: embedded N this run` instead",
    ),
    Line(
        consumer="fanout_embed.BATCH_FAILED",
        emitter=_EMBED_RUN,
        body=(
            "batch FAILED (RuntimeError: MPS backend out of memory) — skipped 32 "
            "(e.g. ['lever:beta:1', 'lever:beta:2']); retry with --resume"
        ),
        tail='\nTraceback (most recent call last):\n  File "embed_run.py", line 1, in run\nRuntimeError: MPS backend out of memory',
        why=(
            "this record now carries `exc_info=True`, so off Actions the raw line is followed by "
            "a traceback. `[^)]+` closes at the message's own paren, so the traceback cannot be "
            "read as data — and under Actions the annotation folds it to one line anyway"
        ),
    ),
    Line(
        consumer="fanout_embed.WEDGED",
        emitter=_EMBED_RUN,
        body="64 consecutive failures — allocator looks wedged; stopping (re-run with --resume)",
        why="the stop, not the individual failures: a wedged allocator fails everything after it",
    ),
    # -- embed_merge --------------------------------------------------------------------------
    Line(
        consumer="fanout_merge.MERGE_PRIOR",
        emitter=_EMBED_MERGE,
        body="prior store: 287144 vectors; 15 fragment(s) under data/embeddings/fragments",
        why="fragment count below the embed shard count means an artifact never arrived",
    ),
    Line(
        consumer="fanout_merge.MERGE_FRAG",
        emitter=_EMBED_MERGE,
        body="+2604 from shard-7 (running total 41230)",
        why="per-fragment arrival, so a missing shard is nameable rather than only a shortfall",
    ),
    Line(
        consumer="fanout_merge.MERGE_DONE",
        emitter=_EMBED_MERGE,
        body="merged 38912 new vectors — store now holds 326056 (dim 768) -> data/embeddings/jobs",
        why="the store total, which grows correctly even when a shard's fragment is missing",
    ),
    Line(
        consumer="fanout_merge.MERGE_UPGRADE_DROP",
        emitter=_EMBED_MERGE,
        body="upgrades: dropped 1204 stale rows for 1204 ids",
        why="ADR-0021 re-embeds: an upgrade is a delete-then-add, not growth",
    ),
    Line(
        consumer="fanout_merge.MERGE_UPGRADE_HOLD",
        emitter=_EMBED_MERGE,
        body="upgrades: holding 38 id(s) whose replacement did not arrive",
        why="held upgrades are the symptom of a failed embed shard, seen from the merge side",
    ),
    Line(
        consumer="fanout_merge.MERGE_SHORTFALL",
        emitter=_EMBED_MERGE,
        body=(
            "only 14 of 15 shard fragment(s) arrived — the missing shard(s)' Docs are not in this "
            "merge and will not be indexed until a later run re-plans them"
        ),
        why="a shortfall against the plan's own count; before it existed this was a mystery",
    ),
    Line(
        consumer="fanout_merge.MERGE_NO_FRAGS",
        emitter=_EMBED_MERGE,
        body=(
            "no fragments under data/embeddings/fragments — nothing to merge; the embed stage "
            "produced nothing, was skipped, or its artifacts did not download"
        ),
        why="distinct from the healthy `nothing was planned` line — never conflate the two",
    ),
    # -- update_meta --------------------------------------------------------------------------
    Line(
        consumer="fanout_merge.META_LINE",
        emitter=_META,
        body=(
            "derivations v14 stored, v15 in code — SWEEPING; corpus facts for 183044 Jobs; "
            "287144 queued to re-derive; 168711 held descriptions"
        ),
        why="a sweep, with the optional `; N held descriptions` clause present",
    ),
    Line(
        consumer="fanout_merge.META_LINE",
        emitter=_META,
        body=(
            "derivations v15 stored, v15 in code — no sweep; corpus facts for 183044 Jobs; "
            "0 queued to re-derive"
        ),
        why="the no-sweep branch, and the held-descriptions clause omitted",
    ),
    Line(
        consumer="fanout_merge.META_REFRESHED",
        emitter=_META,
        body=(
            "refreshed 41230 rows: 12004 with changed facts, 38112 with changed derivations, "
            "220 given a has_description they never had"
        ),
        why="what the sweep touched; direction-blind, hence the next line",
    ),
    Line(
        consumer="fanout_merge.META_DIRECTION",
        emitter=_META,
        body=(
            "experience derivations: 4120 gained, 88 lost, 210 retiered, 640 moved (same tier, "
            "new value) (ADR-0066)"
        ),
        why="ADR-0066's direction split — `lost` is the number worth an alarm",
    ),
    Line(
        consumer="fanout_merge.META_WATERMARK",
        emitter=_META,
        body="watermark -> v15",
        why="the stamp that stops the next run re-sweeping; absent when the store was lost",
    ),
    # -- index sync / prune (heavy: lancedb + pyarrow, so source-verified only) ----------------
    Line(
        consumer="fanout_merge.SCOPE_OUTCOME",
        emitter=_INDEX,
        body=(
            "scrape outcome: 96 Board(s) returned a list that is not authoritative (truncated, or "
            "the scrape raised) and are excluded from the eviction scope — their missing rows are "
            "unscraped, not closed: eightfold:careers.qualcomm.com, +95 more"
        ),
        why="ADR-0053's per-run headline; the pattern prefix-searches so the sample tail may grow",
    ),
    Line(
        consumer="fanout_merge.SCOPE_EXCLUDED",
        emitter=_INDEX,
        body="scope-excluded Board: eightfold:careers.qualcomm.com — returned 412 of 1204 expected",
        why=(
            "one line per excluded Board with its reason, now INFO rather than WARNING (a GitHub "
            "annotation is a quota, not a level). `_log_reasons` builds it from a label argument, "
            "so the `label: ` join is not in any one format string"
        ),
        waived=("scope-excluded Board: ",),
    ),
    Line(
        consumer="fanout_merge.SCOPE_ROWS",
        emitter=_INDEX,
        body=(
            "scope exclusion keeps 1276 eviction-candidate row(s) out of scope across 96 Board(s) "
            "— ADR-0053 has no drain, so a Board short on every run never re-enters scope; watch "
            "this number across runs, not within one; worst: eightfold:careers.qualcomm.com (105), "
            "+95 more"
        ),
        why="with the `; worst: ...` sample present",
    ),
    Line(
        consumer="fanout_merge.SCOPE_ROWS",
        emitter=_INDEX,
        body=(
            "scope exclusion keeps 0 eviction-candidate row(s) out of scope across 96 Board(s) — "
            "ADR-0053 has no drain, so a Board short on every run never re-enters scope; watch "
            "this number across runs, not within one"
        ),
        why="the `; worst: ...` clause is omitted when the sample is empty — the omitted-when-zero shape",
    ),
    Line(
        consumer="fanout_merge.SCOPE_ROW_BOARD",
        emitter=_INDEX,
        body="  105 eviction-candidate row(s) kept out of scope on eightfold:careers.qualcomm.com",
        why=(
            "the per-Board row cost. No longer capped at a top-N and no longer WARNING, so every "
            "excluded Board now carries one"
        ),
    ),
    Line(
        consumer="fanout_merge.GRACE",
        emitter=_INDEX,
        body=(
            "grace period: 1176 id(s) unconfirmed, awaiting a second look before eviction; of the "
            "842 carried in, 411 reappeared in this scrape and 431 are unconfirmed again (their "
            "Board sat out this run's slice, or came back unauthoritative)"
        ),
        why="ADR-0083's per-Job grace period — the only mechanism left that withholds in-scope",
    ),
    Line(
        consumer="fanout_merge.SYNC_PLAN",
        emitter=_INDEX,
        body="plan: add 12004 (11800 new listings + 204 re-embedded), evict 605 -> net +11195 rows",
        why="the one line saying whether the served index grew; `+d` on a gain",
    ),
    Line(
        consumer="fanout_merge.SYNC_PLAN",
        emitter=_INDEX,
        body="plan: add 204 (0 new listings + 204 re-embedded), evict 592 -> net -592 rows",
        why="a shrinking run: the net carries a leading `-`, so the group must be `[+-]\\d+`",
    ),
    Line(
        consumer="fanout_merge.SYNC_DONE",
        emitter=_INDEX,
        body="done: table 'jobs' now holds 287144 rows at data/lancedb",
        why="the served row count after sync, before prune touches it",
    ),
    Line(
        consumer="fanout_merge.ID_BATCH",
        emitter=_INDEX,
        body="add [1-3 of 12004]: workday:acme/External:R1 lever:beta:2 icims:foo-bar:3",
        why="the add side of the id batches the per-ATS churn table is built from",
    ),
    Line(
        consumer="fanout_merge.ID_BATCH",
        emitter=_INDEX,
        body="evict [101-103 of 605]: workday:acme/External:R9 lever:beta:8 rippling:rippling:7",
        why="the evict side; both labels come from `_log_ids` call sites, not a format string",
    ),
    Line(
        consumer="fanout_merge.KEEP_SET",
        emitter=_INDEX,
        body="keep-set: 20114 Scrapable Boards (enabled ATSes)",
        why=(
            "CONTEXT.md's counting vocabulary. This pattern said `live Boards` — a phrase CLAUDE.md "
            "forbids — and matched nothing. `index_plan` also emits a `keep-set:` line, but under "
            "the `[index_plan]` tag and saying `Scrapable Board(s)`, so it cannot collide"
        ),
    ),
    Line(
        consumer="fanout_merge.PRUNE_SUMMARY",
        emitter=_INDEX,
        body="index: 287144 rows | evict 4312 (4012 off-Board + 300 duplicate) -> 282832 remain",
        why="prune's two reasons split out; they have different fixes and must not be summed",
    ),
    Line(
        consumer="fanout_merge.PRUNE_BREAKDOWN",
        emitter=_INDEX,
        body="evict off-Board: 4012 rows across 3 ATSes (workday 3800, lever 200, ashby 12)",
        why="the emitter's own top-5 ranking, with no overflow tail",
    ),
    Line(
        consumer="fanout_merge.PRUNE_BREAKDOWN",
        emitter=_INDEX,
        body=(
            "evict duplicate: 300 rows across 9 ATSes (workday 120, lever 60, ashby 40, "
            "icims 30, keka 20, +4 more)"
        ),
        why="with the `, +N more` overflow inside the parens — `[^)]*` must reach past the commas",
    ),
    Line(
        consumer="fanout_merge.PRUNE_DONE",
        emitter=_INDEX,
        body="done: pruned 4312 rows; table 'jobs' now holds 282832",
        why="the final served count; distinct wording from sync's own `done:` line",
    ),
    # -- role_trends (heavy: numpy, so source-verified only) -----------------------------------
    Line(
        consumer="fanout_merge.TRENDS_ASSIGNING",
        emitter=_TRENDS,
        body="assigning 287144 served rows to 41 families via 120 clusters (centroid version 7)",
        why="logged before the slow vector read, so a stalled step is not unnarrated",
    ),
    Line(
        consumer="fanout_merge.TRENDS_APPENDED",
        emitter=_TRENDS,
        body=(
            "appended 820 rows @ 2026-09-08T00:00:00+00:00 -> data/state/role_trends.csv | top: "
            "software-engineering/mid/workday 12004, data/mid/lever 3120 | new in 7d: 18422"
        ),
        why="the trends ledger tick; `new` is a 7-day LEVEL, never inflow (CONTEXT.md)",
    ),
    Line(
        consumer="fanout_merge.TRENDS_APPENDED",
        emitter=_TRENDS,
        body="appended 0 rows @ 2026-09-08T00:00:00+00:00 -> data/state/role_trends.csv | top:  | new in 7d: 0",
        why=(
            "the empty-`top` form: `stock_top` is a slice of a filtered comprehension, so a run "
            "with no stock-family row joins to '' and renders `| top:  |`. A `(.+)` group dropped "
            "this line outright — no trends tick reported, no error raised"
        ),
    ),
    Line(
        consumer="fanout_merge.TRENDS_NONTECH",
        emitter=_TRENDS,
        body=(
            "non-tech: 4120 of 287144 served rows (1.4% — the ADR-0017 filter's creep) excluded "
            "from the chart"
        ),
        why="the measured creep of the recall-biased tech gate into the served table",
    ),
    Line(
        consumer="fanout_merge.TRENDS_ASSIGNMENTS",
        emitter=_TRENDS,
        body=(
            "assignments: 957 of 287144 rows changed family (0.33%), 41 transition rows | top: "
            "software-engineering->data 412, data->ml 120"
        ),
        why="reassignment vs closure — with the optional `| top:` tail present",
    ),
    Line(
        consumer="fanout_merge.TRENDS_ASSIGNMENTS",
        emitter=_TRENDS,
        body="assignments: 0 of 287144 rows changed family (0.00%), 0 transition rows",
        why="nothing moved, so `top` is empty and the whole `| top:` clause is omitted",
    ),
    Line(
        consumer="fanout_merge.TRENDS_FIRST_SNAPSHOT",
        emitter=_TRENDS,
        body=(
            "assignments: first snapshot — wrote 287144 rows to data/state/role_assignments.csv; "
            "transitions start next run"
        ),
        why="reachable on any run, not just the first: a centroid refit re-bases the snapshot",
    ),
    Line(
        consumer="fanout_merge.TRENDS_DIFF_SKIPPED",
        emitter=_TRENDS,
        body="assignment diff skipped: OSError: [Errno 28] No space left on device",
        why="the diff is `except Exception` on purpose — a diagnostic must not sink a good run",
    ),
    Line(
        consumer="fanout_merge.TRENDS_SKIP_MISSING",
        emitter=_TRENDS,
        body=(
            "skipping trends this run — missing data/state/role_centroids.npz (fit centroids with "
            "the cluster-roles workflow; the family map ships in git, ADR-0040)"
        ),
        why="one of three distinct skip paths, matched on its own line rather than a substring",
    ),
    Line(
        consumer="fanout_merge.TRENDS_SKIP_EMPTY",
        emitter=_TRENDS,
        body="served table 'jobs' is empty — no trend rows this run",
        why="the second skip path; `np.stack` has no empty case, so this returns before it",
    ),
    Line(
        consumer="fanout_merge.TRENDS_SKIP_TAXONOMY",
        emitter=_TRENDS,
        body="role taxonomy unusable, no trends this run: 3 families have no centroid",
        why="the third: a real defect (a refit shipped without re-curating the map), so ERROR",
    ),
    # -- the workflow's own storage check (shell in pipeline.yml, not the Python package) ------
    Line(
        consumer="fanout_merge.STORAGE_LINE",
        emitter=_PIPELINE,
        body="usedStorage 12.4 GB · live 8.1 GB · 1204 commits",
        why="`live` is the number to trend — squash-independent, unlike `usedStorage`",
    ),
    Line(
        consumer="fanout_merge.SQUASHED",
        emitter=_PIPELINE,
        body="squashed; live 8.1 GB",
        why="the reclaim actually fired this run",
    ),
    Line(
        consumer="fanout_merge.NOTHING_TO_RECLAIM",
        emitter=_PIPELINE,
        body="under 20 GB — nothing to reclaim",
        why="the reclaim deliberately did nothing — not the same as the step not running",
    ),
    # -- scrape_plan --------------------------------------------------------------------------
    Line(
        consumer="fanout_plan.QUARANTINE_SKIP",
        emitter=_SCRAPE_PLAN,
        body="quarantine: skipped 114 of 114 confirmed-gone board(s)",
        why="ADR-0058 quarantine acting on the plan; the ledger itself is untouched",
    ),
    Line(
        consumer="fanout_plan.VALUE_GATE",
        emitter=_SCRAPE_PLAN,
        body=(
            "value gate: skipped 7 Board(s) costing over 15 min for under 2 tech jobs/min — "
            "workday:dollartree/dollartreeus (0.03/min), icims:foo-bar (0.11/min), +5 more"
        ),
        why="ADR-0064 removing work before packing; the sample is capped at 10 by `named_sample`",
    ),
    Line(
        consumer="fanout_plan.GATE_BOARD",
        emitter=_SCRAPE_PLAN,
        body="workday:dollartree/dollartreeus (0.03/min)",
        why="one item inside the gate's sample above — a fragment pattern, not a whole line",
    ),
    Line(
        consumer="fanout_plan.SLICE",
        emitter=_SCRAPE_PLAN,
        body=(
            "slice: 20114 boards (5000 priority + 15114 exploration); 712 hold unsettled "
            "descriptions, out of 12,004 gap boards (118,433 jobs) still to drain"
        ),
        why="a thin per-ATS scrape is often this run's exploration draw, not a regression",
    ),
    Line(
        consumer="fanout_plan.COST_COVERAGE",
        emitter=_SCRAPE_PLAN,
        body=(
            "cost: measured seconds for 18422/20114 boards (44210 in ledger); rest estimated from "
            "their ATS median"
        ),
        why="low coverage means the pack is sized on medians and a straggler can hide",
    ),
    Line(
        consumer="fanout_plan.COST_COLDSTART",
        emitter=_SCRAPE_PLAN,
        body=(
            "cost: no measurements yet — cold-start heuristic (ADR-0026); the join writes "
            "data/state/board_cost.csv and the next run packs on seconds"
        ),
        why="the cold-start branch, which is what makes the two forms below reachable",
    ),
    Line(
        consumer="fanout_plan.MAKESPAN",
        emitter=_SCRAPE_PLAN,
        body="20114 boards across 15 shards; predicted makespan ~76.4 min (total work Σ 1043.2 min)",
        why="the measured form, with the makespan tail",
    ),
    Line(
        consumer="fanout_plan.MAKESPAN",
        emitter=_SCRAPE_PLAN,
        body="20114 boards across 15 shards (cold-start cost units)",
        why=(
            "the cold-start form. The makespan-only pattern matched nothing here and printed no "
            "`predicted:` line at all, on exactly the runs whose plan is least trustworthy"
        ),
    ),
    Line(
        consumer="fanout_timing.PLAN_SHARD",
        emitter=_SCRAPE_PLAN,
        body="shard 0: 1336 boards (~136.3 min)",
        why="the measured per-shard serial estimate — NOT a wall estimate",
    ),
    Line(
        consumer="fanout_timing.PLAN_SHARD",
        emitter=_SCRAPE_PLAN,
        body="shard 0: 1336 boards (cost ~4218)",
        why="the cold-start form: unitless pack weights, deliberately not written as fake minutes",
    ),
    Line(
        consumer="fanout_plan.SPREAD",
        emitter=_SCRAPE_PLAN,
        body=(
            "predicted spread: min 100.6 / mean 100.8 / max 137.4 min (1.36x mean); "
            "single-board floor 52.3 min"
        ),
        why="the planner naming its own straggler, in the units floor_table reports after the fact",
    ),
    Line(
        consumer="fanout_plan.FLOOR_WARN",
        emitter=_SCRAPE_PLAN,
        body=(
            "one board costs 52.3 min, above the 9.1 min even share — the makespan floor is this "
            "board, not the packing"
        ),
        why="fires only when it applies; a better packer cannot help a floor-bound shard",
    ),
    Line(
        consumer="fanout_plan.BUDGET_WARN",
        emitter=_SCRAPE_PLAN,
        body=(
            "predicted makespan ~76.4 min exceeds the 60 min shard budget — shards matching their "
            "prediction will bank partials"
        ),
        why="an advance warning of budget kills, printed before any shard has run",
    ),
    # -- update_ledgers' other three subcommands ----------------------------------------------
    Line(
        consumer="fanout_ledgers.PRIORITY_HEADER",
        emitter=_LEDGERS,
        body=(
            "priority: 20114 boards in snapshot | 63127 ledger rows (412 new, 38 pruned, "
            "62677 carried) -> data/state/board_priority.csv"
        ),
        why="the measured-tech-yield ledger the plan ranks Boards by",
    ),
    Line(
        consumer="fanout_ledgers.PRIORITY_TOP",
        emitter=_LEDGERS,
        body="      41.3  workday:acme/External (1204 tech jobs)",
        why="a right-aligned sample row, so the pattern reads it through `\\s+` not fixed columns",
    ),
    Line(
        consumer="fanout_ledgers.COST_HEADER",
        emitter=_LEDGERS,
        body=(
            "cost: 18422 boards timed across 15 shard(s) | 44210 ledger rows (1204 new) | "
            "Σ 8431 board-minutes -> data/state/board_cost.csv"
        ),
        why="the cost ledger that decides whether the next plan is measured or a cold start",
    ),
    Line(
        consumer="fanout_ledgers.COST_MEDIAN",
        emitter=_LEDGERS,
        body="    2393.0s median  workday:dollartree/dollartreeus",
        why="the slowest Boards by measured median — where a makespan floor comes from",
    ),
    Line(
        consumer="fanout_ledgers.GAP_HEADER",
        emitter=_LEDGERS,
        body=(
            "gap: 287,144 stored rows | 168,711 held | 118,433 unsettled across 12,004 boards "
            "(2,110 on a disabled ATS, 3,402 gone from a Board this run scraped in full — both "
            "unreachable) -> data/state/board_description_gap.csv"
        ),
        why=(
            "`held`, not `settled`: the emitter's wording moved and this pattern did not, so every "
            "run printed `no gap summary line found` while the line was right there"
        ),
    ),
    Line(
        consumer="fanout_ledgers.GAP_TOP",
        emitter=_LEDGERS,
        body="   1,204 unsettled  workday:acme/External",
        why="thousands separators again — `[\\d,]+`, and `\\s+` for the alignment",
    ),
    Line(
        consumer="fanout_ledgers.GAP_NO_STORE",
        emitter=_LEDGERS,
        body="gap: no data/descriptions yet — nothing embedded, so no gap to record",
        why="no store at all; distinct from the store existing and being empty, below",
    ),
    Line(
        consumer="fanout_ledgers.GAP_EMPTY_STORE",
        emitter=_LEDGERS,
        body=(
            "gap: data/descriptions holds nothing — the store is missing, not empty; leaving the "
            "ledger as it is"
        ),
        why="the store was lost in transit — writing a gap from it would erase real progress",
    ),
    # -- spare egress -------------------------------------------------------------------------
    Line(
        consumer="fanout_retries.ROTATED",
        emitter=_EGRESS,
        body="spare egress: rotated to a fresh egress IP (#3)",
        why="ADR-0067/0081 rotation actually happening, as opposed to being attempted",
    ),
    Line(
        consumer="fanout_retries.WALLED",
        emitter=_EGRESS,
        body="spare egress: workable walled the current IP — rotating",
        why="which ATS walled us — a per-IP wall and a tombstone 429 need different fixes",
    ),
    Line(
        consumer="fanout_retries.SPENT",
        emitter=_EGRESS,
        body="workable: origin returned 429 — spending this shard's spare egress for the rest of the run",
        why="the shard's Origin budget going; every later Board of that ATS rides the spare",
    ),
)


# Patterns that are deliberately not in CONTRACT. Each needs a reason, and the coverage test
# below fails on anything in neither place.
EXEMPT: dict[str, str] = {
    "run_logs._SHARDED": (
        "parses an Actions job NAME ('scrape (7)'), not a log line — GitHub's shape, not ours"
    ),
    "run_logs._STAMP": (
        "strips the ISO timestamp GitHub prefixes to every raw log line — again GitHub's format"
    ),
    "fanout_merge._ID_BOUNDARY": (
        "a splitter built from the scraper registry at import, not a log line. It divides the "
        "payload of `fanout_merge.ID_BATCH`, whose own entries cover the line it splits"
    ),
    "scope_exclusion_persistence.SCOPE_EXCLUDED_BATCH": (
        "the pre-#160 batched spelling, kept on purpose so a window reaching back past "
        "2026-08-18 does not read those runs as zero exclusions. It has no live emitter BY "
        "DESIGN — that is the point of it, not drift"
    ),
}


# --------------------------------------------------------------------------------------------
# Machinery.
# --------------------------------------------------------------------------------------------

_PLACEHOLDER = (
    "\x00"  # stands in for an interpolated value inside a rendered format string
)


@functools.cache
def _analyser(module: str) -> ModuleType:
    """Import one `scripts/runlog/` module. They import each other by bare name, so the package
    directory has to be on `sys.path` — it is a script folder, not an installed package."""
    if str(_RUNLOG) not in sys.path:
        sys.path.insert(0, str(_RUNLOG))
    return importlib.import_module(module)


def _pattern(consumer: str) -> re.Pattern[str]:
    module, name = consumer.rsplit(".", 1)
    return getattr(_analyser(module), name)


def _literal_runs(pattern: str) -> list[str]:
    """The maximal runs of literal text a regex demands, groups and alternatives included.

    This is the half of a pattern that is a claim about the emitter's wording — `\\d+` says
    nothing, `" Scrapable Boards"` says everything — so it is the half worth checking against the
    emitter's source.
    """
    out: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            out.append("".join(current))
            current.clear()

    def walk(sequence: object) -> None:
        for op, argument in sequence:  # type: ignore[union-attr]
            if op.name == "LITERAL":
                current.append(chr(argument))  # type: ignore[arg-type]
                continue
            flush()
            if op.name == "SUBPATTERN":
                walk(argument[3])  # type: ignore[index]
            elif op.name == "BRANCH":
                for alternative in argument[1]:  # type: ignore[index]
                    walk(alternative)
                    flush()
            elif op.name in ("MAX_REPEAT", "MIN_REPEAT"):
                walk(argument[2])  # type: ignore[index]
            elif op.name == "ATOMIC_GROUP":
                walk(argument)
            flush()
        flush()

    walk(sre_parse.parse(pattern))
    return [run for run in out if run]


def _render(node: ast.AST, depth: int = 0) -> list[str]:
    """Every string an expression can render to, with `\\x00` for each interpolated value.

    Follows `+` and `A if c else B` because emitters really are written that way — `scrape_plan`'s
    per-shard line is one f-string plus a conditional, and its `(cost ~N)` half lives only in the
    `else`. Anything else collapses to a placeholder.
    """
    if depth > 6:
        return [_PLACEHOLDER]
    if isinstance(node, ast.Constant):
        return [node.value] if isinstance(node.value, str) else [_PLACEHOLDER]
    if isinstance(node, ast.JoinedStr):
        rendered = [""]
        for value in node.values:
            rendered = [
                head + tail
                for head in rendered
                for tail in _render(value, depth + 1)[:4]
            ]
        return rendered[:8]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return [
            head + tail
            for head in _render(node.left, depth + 1)[:4]
            for tail in _render(node.right, depth + 1)[:4]
        ][:8]
    if isinstance(node, ast.IfExp):
        return (_render(node.body, depth + 1) + _render(node.orelse, depth + 1))[:8]
    return [_PLACEHOLDER]


@functools.cache
def _emitter_strings(source: Path) -> frozenset[str]:
    """Every string literal the emitter's source can produce, read without importing it.

    Read with `ast` rather than by import on purpose: `index.py`, `role_trends.py` and
    `embed_run.py` need lancedb/numpy/torch, which CI does not install — and a check that skips
    in CI is not a check. Docstrings are excluded, or a module's own prose would satisfy anchors
    its code no longer emits.
    """
    if source.suffix != ".py":
        return frozenset(source.read_text(encoding="utf-8").splitlines())
    tree = ast.parse(source.read_text(encoding="utf-8"), str(source))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstrings.add(id(first.value))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.JoinedStr, ast.BinOp, ast.IfExp)):
            out.update(_render(node))
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            out.add(node.value)
    return frozenset(out)


def _renderings(entry: Line) -> list[str]:
    """The line as a run log really carries it — both forms `log._Formatter` can produce.

    An anomaly logs at WARNING, which under Actions is an annotation with no clock; everything
    else keeps the local `HH:MM:SS` prefix. A pattern must read either, so both are checked.
    """
    body = entry.body + entry.tail
    if entry.tag is None:
        return [body]
    return [f"12:00:00 [{entry.tag}] {body}", f"::warning::[{entry.tag}] {body}"]


def _emitted(entry: Line, caplog: pytest.LogCaptureFixture, *args: object) -> list[str]:
    """Call the emitter and render its records the way a CI log carries them.

    Rendered under `GITHUB_ACTIONS`, because that is the only log these analysers ever read. It
    matters: off Actions a WARNING renders `HH:MM:SS [tag] WARNING: message`, with the level name
    sitting between the tag and the text, and a pattern anchored on `[tag] message` would fail
    against a line CI never produces.
    """
    formatter = log._Formatter()
    logging.getLogger("headstart").setLevel(logging.INFO)
    with caplog.at_level(logging.DEBUG, logger="headstart"):
        entry.emit(*args)  # type: ignore[misc]
    return [formatter.format(record) for record in caplog.records]


def _id(entry: Line) -> str:
    """Name a parametrized case after the pattern, so a failure says which one broke."""
    return f"{entry.consumer}-{abs(hash(entry.body)) % 1000:03d}"


_IDS = [_id(entry) for entry in CONTRACT]


# --------------------------------------------------------------------------------------------
# The checks.
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("entry", CONTRACT, ids=_IDS)
def test_the_consumer_parses_the_line(entry: Line) -> None:
    """Check 1 — the analyser's pattern reads a line the emitter really writes."""
    pattern = _pattern(entry.consumer)
    for rendering in _renderings(entry):
        assert pattern.search(rendering), (
            f"{entry.consumer} no longer matches the line it exists to read.\n"
            f"  line:    {rendering}\n"
            f"  pattern: {pattern.pattern}\n"
            f"  why this entry exists: {entry.why}"
        )


@pytest.mark.parametrize(
    "entry",
    [entry for entry in CONTRACT if entry.emit is None],
    ids=[_id(entry) for entry in CONTRACT if entry.emit is None],
)
def test_the_emitter_still_says_it(entry: Line) -> None:
    """Check 2 — every literal the pattern demands is still in the emitter's source.

    This is the check that catches a rewording. It is a source read, not a call, so it holds for
    the stages CI cannot import; what it cannot see is a change in the *values* a line carries.
    """
    pattern = _pattern(entry.consumer)
    runs = _literal_runs(pattern.pattern)
    if runs:
        tag_prefix = re.match(r"\[(\w+)\]\s?", runs[0])
        if tag_prefix:
            assert tag_prefix.group(1) == entry.tag, (
                f"{entry.consumer} greps for the tag [{tag_prefix.group(1)}], but "
                f"{entry.emitter} logs under [{entry.tag}]. ADR-0039 fixes the tag as the "
                "module name's last segment — one of the two has moved."
            )
            runs[0] = runs[0][tag_prefix.end() :]
    strings = _emitter_strings(entry.source)
    for run in runs:
        if not run or run in entry.waived:
            continue
        assert any(run in candidate for candidate in strings), (
            f"{entry.consumer} demands the literal {run!r}, which {entry.emitter} no longer "
            "writes. The emitter's wording moved and this pattern did not — which does not "
            "error, it silently reports zero.\n"
            f"  why this entry exists: {entry.why}"
        )


@pytest.mark.parametrize(
    "entry",
    [entry for entry in CONTRACT if entry.emit is not None],
    ids=[_id(entry) for entry in CONTRACT if entry.emit is not None],
)
def test_the_emitter_really_emits_it(
    entry: Line,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Check 3 — run the emitter and match the pattern against what it actually logged."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    pattern = _pattern(entry.consumer)
    lines = _emitted(entry, caplog, tmp_path, monkeypatch)
    assert lines, f"{entry.emitter} logged nothing at all for {entry.consumer}"
    assert any(pattern.search(line) for line in lines), (
        f"{entry.consumer} matched none of the {len(lines)} lines {entry.emitter} emitted.\n"
        f"  pattern: {pattern.pattern}\n"
        f"  why this entry exists: {entry.why}\n"
        "  emitted:\n    " + "\n    ".join(lines)
    )


def _compiled_patterns() -> dict[str, str]:
    """Every module-level `NAME = re.compile(...)` under `scripts/runlog/`, by `module.NAME`."""
    found: dict[str, str] = {}
    for path in sorted(_RUNLOG.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            call = node.value
            if not (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "compile"
            ):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found[f"{path.stem}.{target.id}"] = str(path)
    return found


def test_every_runlog_pattern_is_accounted_for() -> None:
    """Check 4 — this file is what makes the contract self-maintaining.

    A new analyser regex with neither a CONTRACT entry nor an EXEMPT reason fails here, so the
    next person cannot add one silently. And a CONTRACT entry naming a pattern that no longer
    exists fails too, so a deleted regex takes its contract row with it.
    """
    compiled = _compiled_patterns()
    covered = {entry.consumer for entry in CONTRACT} | set(EXEMPT)
    missing = sorted(set(compiled) - covered)
    assert not missing, (
        "these scripts/runlog patterns have no contract entry and no EXEMPT reason — add a "
        f"`Line(...)` to CONTRACT, or an EXEMPT entry saying why not: {missing}"
    )
    stale = sorted(covered - set(compiled))
    assert not stale, (
        f"CONTRACT/EXEMPT name patterns that no longer exist in scripts/runlog: {stale}"
    )
    assert all(reason.strip() for reason in EXEMPT.values()), (
        "every EXEMPT pattern needs a reason — an unexplained exemption is how a dead regex hides"
    )


# --------------------------------------------------------------------------------------------
# The correlation vocabulary — not a `scripts/runlog/` pattern, but the same failure mode.
# --------------------------------------------------------------------------------------------

_INGEST = _ROOT / "src" / "headstart" / "ingest"


def _ingest_modules() -> dict[str, ast.Module]:
    """Every module under `src/headstart/ingest/`, parsed — read, never imported.

    Same reason as `_emitter_strings`: `index`, `role_trends` and `embed_run` need
    lancedb/numpy/torch, which CI does not install, and a check that skips in CI is not a check.
    """
    return {
        path.stem: ast.parse(path.read_text(encoding="utf-8"), str(path))
        for path in sorted(_INGEST.glob("*.py"))
    }


def _context_stages() -> dict[str, str]:
    """The literal each module passes as `observability.context`'s `stage`, by module name."""
    found: dict[str, str] = {}
    for module, tree in _ingest_modules().items():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "context"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                found[module] = node.args[0].value
    return found


_STAGES = sorted(_context_stages().items())


@pytest.mark.parametrize(("module", "stage"), _STAGES, ids=[m for m, _ in _STAGES])
def test_the_stage_field_is_the_module_name(module: str, stage: str) -> None:
    """`stage=` is the emitting module's own name, so that grepping it asks one question.

    These values had drifted into three vocabularies at once: `scrape-plan` hyphenated and alone
    in that, `scrape`/`join`/`embed` borrowed from `pipeline.yml`'s job names, and the other ten
    the module name verbatim. A job is not a stage — `join` is one runner executing seven of
    these modules — so a grep across the mix answered neither "which runner" nor "which code".
    """
    assert stage == module, (
        f"{module}.py logs `stage={stage}`, but the rule is the module's own name, "
        f"`stage={module}` — `observability.context`'s docstring says why. A module running "
        "several passes puts the pass in an extra field (`step=`, `ledger=`), not in `stage`."
    )


def test_every_ingest_entry_point_opens_with_a_context_line() -> None:
    """Without it a stage's log says nothing about which run, attempt or shard produced it.

    GitHub stamps every raw line with a timestamp and nothing else, and thirty shards write into
    one run. `context` is the only line that closes that, so every module runnable as `python -m
    headstart.ingest.<module>` — which is every module here with a `main()` — has to call it.
    This is also what stops the parametrized rule above going vacuously green if the walk above
    ever stops finding call sites.
    """
    entry_points = {
        module
        for module, tree in _ingest_modules().items()
        if any(
            isinstance(node, ast.FunctionDef) and node.name == "main"
            for node in tree.body
        )
    }
    missing = sorted(entry_points - set(_context_stages()))
    assert not missing, (
        "these `python -m headstart.ingest.*` entry points never call `observability.context`, "
        f"so nothing in their logs says which run, attempt or shard wrote them: {missing}"
    )
