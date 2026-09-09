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
and the message body as it really appears. Five checks run over it:

1. **The consumer parses a real line** — the pattern must match the body in both renderings
   `headstart.log._Formatter` produces: the local `HH:MM:SS [tag] body` and the GitHub Actions
   `::warning::[tag] body` an anomaly becomes.
2. **The emitter still says it** (source-verified entries only) — every literal run inside the
   consumer's regex must still appear in a string the emitter's source can produce (f-strings
   composed through `+` and `if/else`, read with `ast`, no import). This is what catches a
   rewording. It also pins the `[tag]` prefix to the emitter module's own name, which is
   ADR-0039's rule and was broken once already (`__main__.py` logged `[feed]`). An `emit=` entry
   skips it because checks 3 and 4 subsume it: they read the rendered record, tag included.
3. **The emitter really emits it** (`emit=` entries only) — the emitter function is *called* under
   `caplog` and the pattern is matched against what it actually logged. This is the strongest form
   and it is used wherever the emitter is reachable without heavy deps or real data.
4. **The documented body is one of those lines** (`emit=` entries only) — the entry's own `body`
   must appear, character for character, among the messages the emitter produced. Checks 1 and 3
   both match the *pattern*, one against `body` and one against the records, and nothing compared
   the two to each other until this check: an audit replaced `run_logs.DONE`'s `body` with
   invented numbers and got a green run.
5. **Nothing is unaccounted for** — every module-level `re.compile` in `scripts/runlog/*.py` is
   either in this table or in :data:`EXEMPT` with a reason. Adding an analyser regex without a
   contract entry is what fails, so no human has to remember.

**Emitter-verified vs source-verified, and why the mix.** 49 of the 100 entries carry `emit=`.
The 51 that do not are blocked rather than neglected, and the block is one of three things:

- **A dependency CI does not install** (`.[dev]` and nothing else — no numpy, torch, pyarrow,
  lancedb or langdetect). `index` (16 entries), `role_trends` (11), `embed_merge` (7, whose merge
  reads the vectors with numpy even though the module imports fine), `embed_run` (5) and
  `embed_plan` (5, which needs langdetect for the English gate and transformers for the token
  counts) cannot be *run* here. A check that skips in CI is not a check, so these stay on 1 and 2.
- **An emitter that is not Python**: the storage check is shell inside `pipeline.yml` (3).
- **A `body` that is not a whole line**: `fanout_plan.GATE_BOARD` (1) is a fragment of the value
  gate's own sample, and check 4 asks whether `body` is one of the messages the emitter logged.

That leaves `spare_egress`'s three, two of which sit inside the WARP rotation path (a subprocess
and a SOCKS5 dial); the third, `mark_walled`, is plainly callable and is the next one worth taking.
Prefer `emit=` whenever an emitter becomes cheaply callable, and treat the rest as debt.

**Where the line between them actually falls is *values*.** Check 2 reads format strings, so `{n}`
becoming `{n:,}` leaves every literal around the placeholder untouched and sails through. Check 3
reads the rendered line, so it can catch that — but only when the fixture's own number renders
differently, and `{12:,}` is still `12`. Measured, not assumed: rewriting `scrape_run`'s job count
as `{progress.jobs:,}` left this file green while `run_logs.DONE` provably no longer matched the
line, because the fixture's shard had scraped 12 jobs. Every `emit=` fixture's counts are
four-figure for that reason, including where the emitter has no separator today — that is the
case a `:,` would be *added* to. What stays below the line is a number the pipeline itself keeps
small, where raising it would make the fixture lie about the run rather than about the format
string: a shard count (15 at most) and its ATS-file count (21), the ATSes that contributed
nothing (3), the Boards the ADR-0064 value gate skips (7), `QUARANTINE_AT` (a five-strike
streak), `DERIVATIONS_VERSION` (pinned by the fixture, see `_meta_sweep`), and anything rendered
through a float format — a minute figure, a percentage, an actual/predicted ratio.

**And `body` itself is worth two different things, depending on the entry.** On the 49 `emit=`
entries it is a measured fact: check 4 asserts the documented string is one the emitter really
logged, so a number drifting (`4804` to `4,804`) fails there as well, and the fabricated body an
audit fed `run_logs.DONE` — `done: 111 jobs from 222 boards in 333s ...`, which passed every check
this file had — now fails on the first run. On the other 51 it remains documentation: check 1
proves the consumer's pattern reads it and check 2 proves the emitter still writes the literals
around the numbers, but nothing has ever watched those emitters run, so **the values in a
source-verified `body` are still only the word of whoever last edited the entry** — an invented
count there would sail through exactly as `run_logs.DONE`'s did.

It is worth being exact about how much of a `body` those two checks leave unread, because it is
more than it looks. Both are claims about the *pattern*: check 1 that it matches, check 2 that
its literals survive. Neither reads what sits outside the pattern's own span, and an audit proved
it by replacing `fanout_corpus.JOIN_TOTAL`'s tail with `-> /nonsense/invented/path AND A SENTENCE
NOBODY EMITS` for a fully green run — that entry now carries an `emit=`, and the same fabrication
fails on check 4.

Converting is also how the fiction already in this table came to light, and there was more of it
than anyone expected: **seven** entries documented a line no run can produce. A per-**ATS** cost
median written as a Board (`ats_medians` keys by `_ats_of`); a gap Board written capitalised
where the emitter lowercases (ADR-0049); a `gap: no ...` line naming the description store where
the emitter names the embedding store's metadata; three right-aligned rows a space narrower than
the emitter's own column widths; and a seven-item sample carrying a `+5 more` tail `named_sample`
cannot produce for any count under eleven. Every one had passed every check this file had, for as
long as it had been written down. Move an entry to `emit=` the moment its emitter becomes cheaply
callable.

**One thing this file pins is not in the table at all**: the `stage= run= attempt=` line every
ingest entry point opens with (`observability.context`). No analyser parses it — a human greps it
— so it has no CONTRACT row and no regex to drift against. What it can lose instead is its
*vocabulary*, and it had: ten call sites saying the module's name, three borrowing
`pipeline.yml`'s job name, and one hyphenated and alone in that. The last two tests in this file
hold every call site to one rule, and catch an entry point that ships without the line at all.

## Adding a line

Append a `Line(...)`. `consumer` is `"<module>.<NAME>"` under `scripts/runlog/`; `emitter` is the
dotted module that writes it (or a repo-relative path, for the workflow's own shell); `body` is the
message without the clock or `[tag]` prefix, which the test adds. Give it an `emit=` if you
possibly can: without one, `body` is prose nobody has checked, and check 4 is what turns it into a
fact. Give `why` one sentence saying what this entry pins that its neighbours do not — several
lines exist twice on purpose, once per optional clause, because "a regex requiring an
omitted-when-zero clause drops rows instead of erroring" is this repo's recorded failure mode and
each variant needs its own row.
"""

from __future__ import annotations

import argparse
import ast
import functools
import importlib
import json
import logging
import re
import re._parser as sre_parse
import sys
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
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
    """The message as emitted — no clock, no `[tag]`; the test renders those around it.

    With `emit=` this is checked against the emitter's real output (check 4) and is therefore a
    fact. Without it, nothing has seen the line and its numbers are the entry author's word.
    """

    why: str = ""
    """What this entry pins that its neighbours do not (which optional clause, which branch)."""

    emit: EmitFn | None = None
    """Set to call the real emitter under `caplog`. Strongest check; use it where you can."""

    heavy: bool = False
    """The `emit` needs a dependency CI does not install, so it *skips* on the quality job.

    A skipped check is not a check, so these entries keep check 2 as well — it is an `ast` read
    of the emitter's source and needs no import, so it costs nothing and holds everywhere. Set it
    on every entry whose `emit` opens with a `pytest.importorskip`, or CI silently drops from two
    checks to one on the entry.
    """

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


# The 21 ATS files a real join writes, so the `21 ATS files` in the body below is the pipeline's
# own number rather than an arbitrary one. Only `workday.jsonl` is written by every shard.
_ATS_FILES = (
    "workday",
    "greenhouse",
    "lever",
    "ashby",
    "smartrecruiters",
    "recruitee",
    "workable",
    "personio",
    "zoho",
    "keka",
    "darwinbox",
    "freshteam",
    "successfactors",
    "icims",
    "oracle",
    "eightfold",
    "jazzhr",
    "jobvite",
    "zwayam",
    "trakstar",
    "rippling",
)


def _jsonl(path: Path, rows: Iterable[dict]) -> None:
    """Write one corpus or fragment file. Every fixture below builds its inputs through this."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _run_main(module: ModuleType, monkeypatch: pytest.MonkeyPatch, *argv: str) -> None:
    """Run a stage the way `pipeline.yml` does — `main()`, with a real argument vector.

    Several emitters write their contract line inside `main()` and nowhere else, so reaching it
    means parsing arguments. Every path passed is relative, under a `chdir`ed tmp dir: a `body`
    check 4 compares character for character cannot carry this machine's temp directory, and a
    real run prints exactly these names under the checkout's own root.
    """
    monkeypatch.setattr(sys, "argv", [module.__name__, *argv])
    assert module.main() == 0


def _join_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The join's union pass: 15 shards' fragments concatenated into one snapshot dir.

    `workday.jsonl` is in every shard and the other twenty in one, which is what gives the
    per-ATS line's `from N shard(s)` something to be wrong about. Four-figure line counts for the
    reason `_scrape_shard` gives: neither carries a thousands separator today, and a fixture in
    double digits would let one be added without this file noticing.
    """
    from headstart.ingest import scrape_join

    monkeypatch.chdir(tmp_path)
    for shard in range(15):
        _jsonl(
            Path(f"data/scrape/fragments/shard-{shard}/workday.jsonl"),
            ({"id": f"workday:acme/External:{shard}-{n}"} for n in range(134)),
        )
    for ats in _ATS_FILES[1:]:
        _jsonl(
            Path(f"data/scrape/fragments/shard-0/{ats}.jsonl"),
            ({"id": f"{ats}:beta:{n}"} for n in range(100)),
        )
    _run_main(
        scrape_join,
        monkeypatch,
        "--shards",
        "data/scrape/fragments",
        "--out",
        "data/jobs",
        "--unauthoritative-boards",
        "data/state/unauthoritative_boards.json",
        "--speedup-ledger",
        "data/state/shard_speedup.csv",
    )


def _tech_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The tech gate over one scraped ATS and three that contributed nothing.

    The three empty files are the second line's whole point: `harvest` opens a handle per ATS in
    the shard's list, so an ATS whose every Board failed or was deferred arrives here as a file
    with no rows rather than as an absence, and used to be skipped out of the table entirely.
    """
    from headstart.ingest import filter_tech

    monkeypatch.chdir(tmp_path)
    _jsonl(
        Path("data/jobs/workday.jsonl"),
        [
            {"id": f"workday:acme/External:{n}", "title": "Backend Engineer"}
            for n in range(1204)
        ]
        + [
            {"id": f"workday:acme/External:nt-{n}", "title": "Warehouse Associate"}
            for n in range(3612)
        ],
    )
    for ats in ("jazzhr", "jobvite", "sensehq"):
        _jsonl(Path(f"data/jobs/{ats}.jsonl"), ())
    _run_main(filter_tech, monkeypatch, "--src", "data/jobs", "--dst", "data/jobs/tech")


def _tech_gate_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every ATS in the slice contributed zero rows — the corpus-wide zero, logged at ERROR."""
    from headstart.ingest import filter_tech

    monkeypatch.chdir(tmp_path)
    for ats in ("jazzhr", "jobvite", "sensehq"):
        _jsonl(Path(f"data/jobs/{ats}.jsonl"), ())
    _run_main(filter_tech, monkeypatch, "--src", "data/jobs", "--dst", "data/jobs/tech")


def _descriptions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One reconcile pass over two ATSes — one with a Job nothing can describe, one without.

    Both halves of the per-ATS line's optional tail come from a single call: `workday` carries
    Jobs with no fresh text and none stored, `lever` carries none, so the `, N still unrecorded`
    clause is present on one line and absent on the other. A pattern requiring it would drop
    every healthy ATS's line rather than error — this repo's recorded failure mode.

    Every count clears 999 because all four are formatted `{n:,}`, where check 3 can only see a
    separator that actually renders: `{1002:,}` is `1,002` and `{12:,}` is still `12`.
    """
    import gzip

    from headstart.ingest import update_descriptions

    monkeypatch.chdir(tmp_path)
    embedded: list[str] = []
    for ats, unrecorded in (("lever", 0), ("workday", 1002)):
        filled = [f"{ats}:beta:f{n}" for n in range(1204)]
        learned = [f"{ats}:beta:l{n}" for n in range(1005)]
        _jsonl(
            Path(f"data/jobs/tech/{ats}.jsonl"),
            [{"id": i} for i in filled]
            + [{"id": i, "description": "a fresh detail fetch"} for i in learned]
            + [{"id": f"{ats}:beta:u{n}"} for n in range(unrecorded)],
        )
        ats_dir = Path("data/descriptions") / ats
        ats_dir.mkdir(parents=True)
        with gzip.open(ats_dir / "0001.jsonl.gz", "wt", encoding="utf-8") as fh:
            for job_id in filled:
                fh.write(json.dumps({"id": job_id, "description": "held text"}) + "\n")
        # Only a Job the embedding store already holds is queued to re-derive, so the prior meta
        # has to carry the learned ids or `queued` is 0 whatever the store learned.
        embedded += filled + learned
    _jsonl(Path("data/embeddings/jobs/meta.jsonl"), ({"id": i} for i in embedded))
    _run_main(
        update_descriptions,
        monkeypatch,
        "--jobs",
        "data/jobs/tech",
        "--store",
        "data/descriptions",
        "--held-details",
        "data/state/held_details.txt.gz",
        "--pending-rederive",
        "data/state/pending_rederive.txt",
        "--prior-meta",
        "data/embeddings/jobs/meta.jsonl",
    )


def _ledger_priority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`update_ledgers priority` over one snapshot: Boards learned, decayed out, and carried.

    All three of the header's counts are four-figure, and so is the sampled Board's tech count.
    None of them carries a thousands separator today — which is exactly why the fixture has to
    clear 999: check 4 compares the rendered line, so a `{n:,}` *added* to any of them fails here
    rather than passing unnoticed.

    `greenhouse:fading-*` are seeded one bad run from the prune floor and scrape zero tech jobs
    this run, which is the only input that moves `pruned`; `greenhouse:carried-*` are absent from
    the snapshot entirely, which is the only input that moves `carried`.
    """
    from headstart import board_priority
    from headstart.ingest import update_ledgers

    monkeypatch.chdir(tmp_path)
    top = [f"greenhouse:top:{n}" for n in range(1204)]
    fresh = [f"greenhouse:new-{n}:1" for n in range(1204)]
    fading = [f"greenhouse:fading-{n}:1" for n in range(1010)]
    _jsonl(
        Path("data/jobs/greenhouse.jsonl"), ({"id": i} for i in top + fresh + fading)
    )
    _jsonl(Path("data/jobs/tech/greenhouse.jsonl"), ({"id": i} for i in top + fresh))
    ledger = Path("data/state/board_priority.csv")
    board_priority.save(
        ledger,
        {
            **{
                f"greenhouse:fading-{n}": board_priority.BoardPriority(
                    board_priority.PRUNE_BELOW, 0, "2026-09-01"
                )
                for n in range(1010)
            },
            **{
                f"greenhouse:carried-{n}": board_priority.BoardPriority(
                    3.0, 3, "2026-09-01"
                )
                for n in range(1020)
            },
        },
    )
    update_ledgers.priority(
        argparse.Namespace(
            jobs=Path("data/jobs"), tech=Path("data/jobs/tech"), ledger=ledger
        )
    )


def _ledger_cost(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`update_ledgers cost` over 15 shards' timing fragments, blended into a seeded ledger.

    Boards are keyed `greenhouse:*` on purpose: the ADR-0096 read-time shim re-keys a legacy row
    and logs how many it moved, and a Workday key that is not a careers URL would trip that
    (and `board_identity`'s own warning) rather than exercise the plain path this line reports.
    """
    from headstart import board_cost
    from headstart.harvest import COST_FILENAME
    from headstart.ingest import update_ledgers

    monkeypatch.chdir(tmp_path)
    fragments = Path("data/scrape/fragments")
    for shard in range(15):
        rows = "".join(
            board_cost.shard_row(f"greenhouse:timed-{shard}-{n}", 2393.0, 1204)
            for n in range(80)
        )
        path = fragments / f"shard-{shard}" / COST_FILENAME
        path.parent.mkdir(parents=True)
        path.write_text(board_cost.SHARD_HEADER + rows, encoding="utf-8")
    ledger = Path("data/state/board_cost.csv")
    board_cost.save(
        ledger,
        {
            f"greenhouse:carried-{n}": board_cost.BoardCost(2393.0, 1204, "2026-09-01")
            for n in range(1010)
        },
    )
    update_ledgers.cost(argparse.Namespace(fragments=fragments, ledger=ledger))


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
    # The ledger's own path is the last thing the `failures:` line prints, so it is inside the
    # `body` check 4 matches exactly. Passed relative from inside `tmp_path`, or that `body`
    # would have to carry this machine's temp directory; a real run prints the same filename
    # under the checkout's own `data/state/`.
    monkeypatch.chdir(tmp_path)
    ledger = Path("board_failures.csv")
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


def _gap_args(**over: object) -> argparse.Namespace:
    """`update_ledgers gap`'s arguments, as its own parser builds them.

    Every default is the real relative path the pipeline runs with, so the two paths that reach a
    log line — the embedding store's metadata and the description store — are named in the body
    exactly as a real run names them.
    """
    return argparse.Namespace(
        meta=Path("data/embeddings/jobs/meta.jsonl"),
        descriptions=Path("data/descriptions"),
        jobs=Path("data/jobs"),
        unauthoritative_boards=Path("data/state/unauthoritative_boards.json"),
        ledger=Path("data/state/board_description_gap.csv"),
        **over,
    )


def _ledger_gap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`update_ledgers gap` over a stored corpus holding all four classes of row at once.

    The header's six counts are the whole point of the line, and only one input moves each:
    `held` needs the description store to hold the Job, `on a disabled ATS` needs its `ats` to be
    one the registry has switched off, `gone from a Board this run scraped in full` needs a Board
    that emitted lines this run without re-emitting that id (#185), and `unsettled` is what is
    left. All six are formatted `{n:,}`, so all six clear 999.
    """
    import gzip

    from headstart.ingest import update_ledgers
    from headstart.scrapers.registry import DISABLED_ATS

    monkeypatch.chdir(tmp_path)
    # Taken from the registry rather than named, because which ATS is switched off is its call;
    # `min` rather than an arbitrary member, so the count this fixture asserts cannot depend on
    # the frozenset's iteration order.
    disabled = min(DISABLED_ATS)
    held = [f"lever:held-{n}:1" for n in range(1204)]
    ats_dir = Path("data/descriptions/lever")
    ats_dir.mkdir(parents=True)
    with gzip.open(ats_dir / "0001.jsonl.gz", "wt", encoding="utf-8") as fh:
        for job_id in held:
            fh.write(json.dumps({"id": job_id, "description": "held text"}) + "\n")
    # One Board this run scraped authoritatively, re-emitting a single id: every *other* stored
    # id on it has expired off the Board, so no future scrape can settle it.
    _jsonl(Path("data/jobs/greenhouse.jsonl"), [{"id": "greenhouse:full:kept"}])
    Path("data/state").mkdir(parents=True)
    Path("data/state/unauthoritative_boards.json").write_text("{}", encoding="utf-8")
    _jsonl(
        Path("data/embeddings/jobs/meta.jsonl"),
        [{"id": i, "ats": "lever"} for i in held]
        + [{"id": f"{disabled}:off-{n}:1", "ats": disabled} for n in range(1010)]
        + [{"id": f"greenhouse:full:{n}", "ats": "greenhouse"} for n in range(1005)]
        + [{"id": f"lever:gap-{n}:1", "ats": "lever"} for n in range(1204)]
        + [{"id": f"workday:acme/External:{n}", "ats": "workday"} for n in range(1204)],
    )
    update_ledgers.gap(_gap_args())


def _ledger_gap_no_meta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing has been embedded yet, so there is no stored corpus to count a gap against."""
    from headstart.ingest import update_ledgers

    monkeypatch.chdir(tmp_path)
    update_ledgers.gap(_gap_args())


def _ledger_gap_empty_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The store downloaded empty — distinct from the no-store case above, and it must be.

    The join fetches the description store on a warn-only fallback, so an empty one here means a
    lost download. Writing the ledger from it would mark every embedded Board gap-ful.
    """
    from headstart.ingest import update_ledgers

    monkeypatch.chdir(tmp_path)
    _jsonl(
        Path("data/embeddings/jobs/meta.jsonl"),
        [{"id": "lever:beta:1", "ats": "lever"}],
    )
    Path("data/descriptions").mkdir(parents=True)
    update_ledgers.gap(_gap_args())


# One stored metadata row per ADR-0066 direction bucket, as `(stored derivation, title,
# description)`. The stored answer is wrong in a different way in each, so re-deriving it against
# the text moves it in a different direction — which is the whole point of the split the
# `experience derivations:` line reports, and the one thing a single "N changed" count cannot say.
_DERIVATION_CASES = {
    "gained": (
        {"min_years": None, "max_years": None, "experience_source": None},
        "Backend Engineer",
        "We want 5+ years of experience.",
    ),
    "lost": (
        {"min_years": 5, "max_years": None, "experience_source": "regex"},
        "Backend Engineer",
        "No numbers here at all, just prose about the team.",
    ),
    "retiered": (
        {"min_years": 3, "max_years": None, "experience_source": "seniority"},
        "Senior Backend Engineer",
        "8+ years of experience required.",
    ),
    "moved": (
        {"min_years": 3, "max_years": None, "experience_source": "regex"},
        "Backend Engineer",
        "Requires 5+ years of experience.",
    ),
    # The control: already right, so the sweep re-derives it and changes nothing. Without it
    # `refreshed N rows` and `N with changed derivations` would be one number wearing two names,
    # and neither could be wrong on its own.
    "steady": (
        {"min_years": 5, "max_years": None, "experience_source": "regex"},
        "Backend Engineer",
        "We want 5+ years of experience.",
    ),
}
_DERIVATION_COUNTS = {
    "gained": 1004,
    "lost": 1002,
    "retiered": 1003,
    "moved": 1001,
    "steady": 1006,
}


def _meta_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, Path]:
    """The three stores `update_meta.refresh` reads, seeded so every count it prints is distinct.

    The watermark is left to the caller: it is the input that picks the sweep branch, and the two
    fixtures below differ in nothing else.

    `location` is the one fact this run's corpus disagrees with, because it is the only fact that
    is not also a cascade input: changing `title` or `experience` would re-derive the row through
    `inputs_moved` as well, and then `N with changed facts` and `N with changed derivations` could
    no longer be wrong independently.
    """
    import gzip

    monkeypatch.chdir(tmp_path)
    store = Path("data/embeddings/jobs")
    store.mkdir(parents=True)
    ats_dir = Path("data/descriptions/lever")
    ats_dir.mkdir(parents=True)
    rows: list[dict] = []
    corpus: list[dict] = []
    with gzip.open(ats_dir / "0001.jsonl.gz", "wt", encoding="utf-8") as fh:
        for kind, count in _DERIVATION_COUNTS.items():
            derived, title, description = _DERIVATION_CASES[kind]
            for n in range(count):
                job_id = f"lever:beta:{kind}-{n}"
                fh.write(json.dumps({"id": job_id, "description": description}) + "\n")
                row = {
                    "id": job_id,
                    "ats": "lever",
                    "title": title,
                    "location": "Remote",
                    **derived,
                }
                # `has_description` is written once, on the rows that never had it — so only the
                # rows deliberately missing it move `N given a has_description they never had`.
                if kind != "lost":
                    row["has_description"] = True
                rows.append(row)
                if kind == "steady":
                    corpus.append(
                        {"id": job_id, "title": title, "location": "Bengaluru, India"}
                    )
    _jsonl(store / "meta.jsonl", rows)
    _jsonl(Path("data/jobs/tech/lever.jsonl"), corpus)
    return store, Path("data/jobs/tech"), Path("data/descriptions")


def _meta_sweep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A DERIVATIONS_VERSION sweep: every stored row re-derived against its held description.

    `DERIVATIONS_VERSION` is pinned rather than read, and this is the one fixture value that is
    not simply a count. The constant moves whenever a fix to `experience.py` or `salary.py`
    reaches already-indexed rows (CLAUDE.md requires it), and a `body` carrying its live value
    would make every one of those bumps fail this file for no drift at all. What is under
    contract here is the sentence around the number — `vN stored, vM in code`, the SWEEPING
    branch, and the optional held-descriptions clause — not which version the repo is on today.
    """
    from headstart.ingest import update_meta

    store, jobs, descriptions = _meta_store(tmp_path, monkeypatch)
    monkeypatch.setattr(update_meta, "DERIVATIONS_VERSION", 15)
    watermark = Path("data/state/derivations_version.json")
    watermark.parent.mkdir(parents=True)
    watermark.write_text(json.dumps({"version": 14}), encoding="utf-8")
    pending = Path("data/state/pending_rederive.txt")
    pending.write_text(
        "".join(f"lever:beta:steady-{n}\n" for n in range(1204)), encoding="utf-8"
    )
    update_meta.refresh(store, jobs, descriptions, watermark, pending)


def _meta_no_sweep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The stored watermark already matches the code, and nothing is queued — no sweep.

    Both optional pieces of the line are gone at once: `SWEEPING` becomes `no sweep`, and with no
    queue there is nothing to load descriptions for, so the `; N held descriptions` clause is
    omitted. A pattern requiring either drops the line rather than erroring.
    """
    from headstart.ingest import update_meta

    store, jobs, descriptions = _meta_store(tmp_path, monkeypatch)
    monkeypatch.setattr(update_meta, "DERIVATIONS_VERSION", 15)
    watermark = Path("data/state/derivations_version.json")
    watermark.parent.mkdir(parents=True)
    watermark.write_text(json.dumps({"version": 15}), encoding="utf-8")
    update_meta.refresh(store, jobs, descriptions, watermark, None)


def _plan_args(*over: str) -> tuple[str, ...]:
    """`scrape_plan main()`'s paths, all relative under a `chdir`ed tmp dir.

    Every one is passed even when the fixture leaves the file absent, because absent is a branch:
    no cost ledger is the cold start, no failures ledger quarantines nothing. Defaulting them
    would point the planner at the real checkout's `data/state/`.
    """
    return (
        "--ledger",
        "data/validate/liveness",
        "--priority",
        "data/state/board_priority.csv",
        "--cost",
        "data/state/board_cost.csv",
        "--failures",
        "data/state/board_failures.csv",
        "--gap",
        "data/state/board_description_gap.csv",
        "--speedup-ledger",
        "data/state/shard_speedup.csv",
        "--held-details",
        "data/state/held_details.txt.gz",
        "--out-dir",
        "data/scrape/plan",
        *over,
    )


def _plan_coldstart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The first run of all: no cost ledger, so the pack is in unitless cost units.

    This is the branch that motivated this whole file. `fanout_timing.PLAN_SHARD` and
    `fanout_plan.MAKESPAN` both required a clause `scrape_plan` omits here, so both tools printed
    nothing on exactly the runs whose plan is least trustworthy — a silent zero, not an error.

    `load_active_companies` is stubbed, as `tests/test_scrape_plan.py` stubs it: the real one
    reads the committed liveness ledger, whose ~20k Boards would make every count in this entry
    drift with a data file that has nothing to do with the log's wording.

    A full 15 shards of four-figure Boards apiece, because the per-shard *board count* is one
    format string shared by both branches of the line below — so this branch clearing 999 is what
    keeps a `{n:,}` on it catchable, whatever slice the measured fixture happens to plan.
    """
    from headstart.config import CompanyRef
    from headstart.ingest import scrape_plan

    monkeypatch.chdir(tmp_path)
    companies = [
        CompanyRef("greenhouse", f"cold-{n}", f"Cold {n}") for n in range(15000)
    ]
    monkeypatch.setattr(
        scrape_plan, "load_active_companies", lambda ledger, min_jobs=0: companies
    )
    _run_main(scrape_plan, monkeypatch, *_plan_args("--target-boards", "1000"))


def _plan_measured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A measured plan carrying every branch the planner reports on at once.

    One Board (`greenhouse:giant`, 65 min) outweighs an even share of a slice that is otherwise
    all sub-second Boards, which is what makes the makespan floor and the budget warning fire
    together — that is not contrived, it is the shape of this pipeline's real cost distribution
    (a handful of giants against ~20k Boards that answer in under a second).

    Determinism is bought with distinct costs, not with a seed: `pick_boards` shuffles, and LPT
    then deals items heaviest-first, so equal costs would make which Board lands on which shard —
    and therefore the per-shard counts — depend on the shuffle.
    """
    from datetime import UTC, datetime

    from headstart import board_cost, board_description_gap, board_priority
    from headstart.config import CompanyRef
    from headstart.ingest import board_failures, scrape_plan

    monkeypatch.chdir(tmp_path)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    quarantined = [f"gone-{n}" for n in range(1104)]
    gated = [f"gate-{n}" for n in range(7)]
    measured = [f"measured-{n}" for n in range(1204)]
    explore = [f"explore-{n}" for n in range(1102)]
    companies = [
        CompanyRef("greenhouse", slug, slug.title())
        for slug in [*quarantined, *gated, *measured, *explore, "giant"]
    ]
    monkeypatch.setattr(
        scrape_plan, "load_active_companies", lambda ledger, min_jobs=0: companies
    )

    board_failures.save(
        Path("data/state/board_failures.csv"),
        {
            f"greenhouse:{slug}": board_failures.Failure(
                strikes=board_failures.QUARANTINE_AT,
                last_reason="HTTPError: HTTP Error 404: Not Found",
                last_seen_gone=f"{today}T00:00:00+00:00",
            )
            for slug in quarantined
        },
    )
    # Distinct seconds per Board: LPT deals heaviest-first, so ties would leave the per-shard
    # counts at the mercy of `pick_boards`' shuffle.
    cost_rows = {
        f"greenhouse:{slug}": board_cost.BoardCost(0.5 + n / 10000, 12, today)
        for n, slug in enumerate(measured)
    }
    # The value gate's own inputs: over 15 min of measured scrape (`_GATE_FLOOR_S`) for a score
    # that works out under 2 tech jobs a minute, and a measurement recent enough not to have
    # expired into a re-check.
    cost_rows.update(
        {
            f"greenhouse:{slug}": board_cost.BoardCost(1000.0 + 100 * n, 1204, today)
            for n, slug in enumerate(gated)
        }
    )
    cost_rows["greenhouse:giant"] = board_cost.BoardCost(3900.0, 1204, today)
    board_cost.save(Path("data/state/board_cost.csv"), cost_rows)

    scores = {
        f"greenhouse:{slug}": board_priority.BoardPriority(1.0 + n / 100, 12, today)
        for n, slug in enumerate(measured)
    }
    scores.update(
        {
            f"greenhouse:{slug}": board_priority.BoardPriority(
                0.5 + n / 10, 1204, today
            )
            for n, slug in enumerate(gated)
        }
    )
    # Above 2 tech jobs per minute of its 65, so the giant survives the gate it would otherwise
    # be the first Board through.
    scores["greenhouse:giant"] = board_priority.BoardPriority(200.0, 1204, today)
    board_priority.save(Path("data/state/board_priority.csv"), scores)

    board_description_gap.save(
        Path("data/state/board_description_gap.csv"),
        {f"greenhouse:{slug}": 98 for slug in measured},
        today=today,
    )
    _run_main(scrape_plan, monkeypatch, *_plan_args())


# --------------------------------------------------------------------------------------------
# Emitters behind an import CI does not install. `pytest.importorskip` is the gate, and the two
# rules that make it honest are in the module docstring: these entries keep check 2 (a source
# read, which needs no import), and they gain checks 3 and 4 wherever the `[embed]` extra is
# installed — which is every machine a person edits these modules on.
# --------------------------------------------------------------------------------------------

_INDEX_DIM = 4  # the served vector's width; 4 keeps a five-figure fixture table cheap

#: Every served metadata column `index._schema` names, so a fixture row is a real table row.
_INDEX_META = {
    "company": "acme",
    "title": "backend engineer",
    "location": "remote",
    "remote": True,
    "employment_type": None,
    "experience": None,
    "min_years": None,
    "max_years": None,
    "experience_source": None,
    "salary": None,
    "min_salary_annual": None,
    "max_salary_annual": None,
    "salary_currency": None,
    "salary_source": None,
    "department": None,
    "posted_at": None,
}


def _index_meta(job_id: str) -> dict:
    """One store metadata row. `has_description` is planner-only (ADR-0050) and is dropped by
    `sync` before the row reaches the table, so it belongs here and not in the schema above."""
    return {
        "id": job_id,
        "ats": job_id.split(":", 1)[0],
        "url": f"https://example.test/{job_id}",
        **_INDEX_META,
        "has_description": True,
    }


def _index_store(store: Path, ids: list[str]) -> None:
    """The committed embedding store `sync` reads: row-aligned metadata, vectors, manifest."""
    import numpy as np

    store.mkdir(parents=True, exist_ok=True)
    (store / "meta.jsonl").write_text(
        "".join(json.dumps(_index_meta(i)) + "\n" for i in ids), encoding="utf-8"
    )
    np.zeros((len(ids), _INDEX_DIM), dtype="float32").tofile(store / "embeddings.f32")
    (store / "manifest.json").write_text(
        json.dumps({"dim": _INDEX_DIM}), encoding="utf-8"
    )


def _served_row(job_id: str, dim: int, **over: object) -> dict:
    """One row of the served `jobs` table, carrying every column `index._schema` names."""
    return {
        "id": job_id,
        "ats": job_id.split(":", 1)[0],
        "url": f"https://example.test/{job_id}",
        "description": None,
        "first_seen": "2026-09-01T00:00:00+00:00",
        "vector": [0.0] * dim,
        **_INDEX_META,
        **over,
    }


def _served_table(db: Path, rows: list[dict], dim: int) -> None:
    """The table as a *previous* run left it — built directly rather than by a warm-up `sync`.

    Prior table state is this fixture's input, not the emitter's output, and driving it through a
    real `sync` would put that run's several thousand log lines into `caplog` alongside the ones
    under contract: check 4 would still pass, but every failure in this file would print two runs
    instead of one.
    """
    import lancedb

    from headstart.ingest import index

    table = lancedb.connect(str(db)).create_table(
        index.PROD_TABLE, schema=index._schema(dim)
    )
    if (
        rows
    ):  # `add([])` raises; an empty served table is a branch, not a broken fixture
        table.add(rows)


def _index_table(db: Path, ids: list[str]) -> None:
    """The prior served table for the `index` fixtures: one plain row per id."""
    _served_table(db, [_served_row(job_id, _INDEX_DIM) for job_id in ids], _INDEX_DIM)


def _index_paths(**over: object) -> argparse.Namespace:
    """`index sync`'s arguments, every path relative under a `chdir`ed tmp dir.

    Relative for the reason `_ledger_failures` gives: `done:` prints `args.db`, and a `body`
    check 4 compares character for character cannot carry this machine's temp directory. A real
    run prints exactly these names under the checkout's own root.
    """
    return argparse.Namespace(
        source="data/jobs/tech",
        scraped="data/jobs",
        db="data/lancedb",
        ledger="data/validate/liveness",
        upgrades="data/state/pending_upgrades.txt",
        unauthoritative_boards="data/state/unauthoritative_boards.json",
        unconfirmed="data/state/unconfirmed_ids.txt",
        backfill_descriptions=False,
        **over,
    )


def _index_ledger(monkeypatch: pytest.MonkeyPatch, boards: list[str]) -> None:
    """Stub the liveness ledger `live_keep_set` reads, as `_plan_coldstart` stubs it for the
    planner: the committed one holds ~20k Boards, and every count below would then drift with a
    data file that has nothing to do with the log's wording."""
    from headstart.config import CompanyRef
    from headstart.ingest import index_plan

    monkeypatch.setattr(
        index_plan,
        "load_active_companies",
        lambda ledger, min_jobs=0: [
            CompanyRef(*board.split(":", 1), board) for board in boards
        ],
    )


def _index_jsonl(path: Path, ids: list[str]) -> None:
    _jsonl(path, ({"id": job_id, "description": "held text"} for job_id in ids))


def _index_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    excluded_hold_rows: bool = True,
    growing: bool = True,
) -> None:
    """One `index sync` over a table a previous run left behind — nine patterns read this call.

    Every count is four-figure, including the Boards ADR-0053 excludes: a run excluding a
    thousand Boards is one ATS-wide outage, not a contrivance, and the count is formatted `{n}`
    today — exactly the case a `{n:,}` would be added to.

    `excluded_hold_rows` picks which branch the `scope exclusion keeps N ...` line takes: with it
    the excluded Boards carry eviction candidates and the line ends in a `; worst: ...` sample;
    without it every one of their rows came back this run, so the sample is empty and the whole
    clause is omitted. `growing` drops the new listings, so `plan:` reports a net loss.
    """
    pytest.importorskip("lancedb")
    pytest.importorskip("numpy")
    pytest.importorskip("pyarrow")
    from headstart.ingest import index

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(index, "_STORE", Path("data/embeddings/jobs"))
    Path("data/state").mkdir(parents=True)

    short = [f"lever:short-{n:04d}" for n in range(1012)]
    _index_ledger(monkeypatch, [*short, "lever:ok", "lever:sat-out"])

    # Held by both the table and this run's scrape: they neither move nor are counted anywhere.
    stay = [f"lever:ok:stay-{n}" for n in range(1001)]
    # Re-embedded (ADR-0050): deleted and re-added, so they are adds that are not new listings.
    up = [f"lever:ok:up-{n}" for n in range(1204)]
    # Absent, and already carried in as Unconfirmed — the only ids this run may evict.
    closed = [f"lever:ok:closed-{n}" for n in range(1005)]
    # Absent for the FIRST time: unconfirmed this run, evictable on the next.
    gone = [f"lever:ok:gone-{n}" for n in range(1204)]
    # Carried in Unconfirmed and back in this scrape — the `reappeared` half of the grace line.
    back = [f"lever:ok:back-{n}" for n in range(1002)]
    # Carried in on a Board this run did not scrape at all: unconfirmed again, the `still` half.
    wait = [f"lever:sat-out:wait-{n}" for n in range(1003)]
    # Rows on the Boards ADR-0053 excludes. One Board carries a four-figure share of them, which
    # is what the per-Board line and the `worst:` sample are for.
    stale = [f"{short[0]}:stale-{n}" for n in range(1105)] + [
        f"{board}:stale" for board in short[1:]
    ]
    # One live id per excluded Board, so the Board is in this run's scrape at all.
    kept = [f"{board}:keep" for board in short]
    new = [f"lever:ok:new-{n}" for n in range(1200)] if growing else []

    _index_table(
        Path("data/lancedb"),
        [
            *stay,
            *up,
            *closed,
            *gone,
            *back,
            *wait,
            *(stale if excluded_hold_rows else []),
            *kept,
        ],
    )
    fresh = [*stay, *up, *back, *new, *kept]
    _index_store(Path("data/embeddings/jobs"), fresh)
    _index_jsonl(Path("data/jobs/lever.jsonl"), fresh)
    _index_jsonl(Path("data/jobs/tech/lever.jsonl"), fresh)
    Path("data/state/pending_upgrades.txt").write_text(
        "".join(f"{i}\n" for i in up), encoding="utf-8"
    )
    Path("data/state/unconfirmed_ids.txt").write_text(
        "".join(f"{i}\n" for i in [*closed, *back, *wait]), encoding="utf-8"
    )
    Path("data/state/unauthoritative_boards.json").write_text(
        json.dumps(
            {b.lower(): "HTTPError: HTTP Error 429: Too Many Requests" for b in short}
        ),
        encoding="utf-8",
    )
    assert index.sync(_index_paths()) == 0


def _index_sync_all_returned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The excluded Boards held nothing this run missed, so the `; worst: ...` clause is gone."""
    _index_sync(tmp_path, monkeypatch, excluded_hold_rows=False)


def _index_sync_shrinking(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A run whose only adds are re-embeds — `plan:`'s net carries a leading `-`."""
    _index_sync(tmp_path, monkeypatch, growing=False)


#: `(ATS, Boards, duplicate pairs)` for the prune fixture's case-variant rows. Nine ATSes so the
#: breakdown line's own top-5 ranking overflows into its `, +N more` tail, and a four-figure total
#: because that count is formatted `{n}` — the case a `{n:,}` would be added to.
_PRUNE_DUPES = (
    ("keka", 400),
    ("ashby", 300),
    ("icims", 200),
    ("smartrecruiters", 150),
    ("freshteam", 100),
    ("trakstar", 40),
    ("rippling", 30),
    ("greenhouse", 20),
    ("recruitee", 10),
)

#: `(ATS, rows)` for Boards the ledger no longer lists. Exactly three, so the same breakdown line
#: is pinned in its other shape — a ranking that fits inside the top 5 and has no tail at all.
_PRUNE_OFF_BOARD = (("personio", 1000), ("zoho", 200), ("workable", 4))


def _index_prune(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`index prune --apply` over a table holding both of ADR-0023's classes at once.

    The keep-set is four-figure because `_MIN_KEEP_BOARDS` refuses to prune below 1,000 Boards —
    so this is the one count in this file the emitter itself will not let a fixture shrink.

    A duplicate is one job under two Board casings, and which row survives is the ledger's
    casing (`plan_prune`), so the pairs are seeded `{ats}:DupCo:n` / `{ats}:dupco:n` against a
    ledger holding `DupCo`. Seeding them the other way round would still produce a duplicate,
    but the *kept* row would be the fossil — the bug that rule exists to fix.
    """
    pytest.importorskip("lancedb")
    pytest.importorskip("numpy")
    pytest.importorskip("pyarrow")
    from headstart.ingest import index

    monkeypatch.chdir(tmp_path)
    live = [f"lever:keep-{n}" for n in range(1003)] + [
        f"{ats}:DupCo" for ats, _ in _PRUNE_DUPES
    ]
    _index_ledger(monkeypatch, live)

    rows = [f"lever:keep-0:live-{n}" for n in range(1005)]
    for ats, count in _PRUNE_OFF_BOARD:
        rows += [f"{ats}:left-the-ledger:{n}" for n in range(count)]
    for ats, count in _PRUNE_DUPES:
        rows += [f"{ats}:DupCo:{n}" for n in range(count)]
        rows += [f"{ats}:dupco:{n}" for n in range(count)]
    _index_table(Path("data/lancedb"), rows)

    assert (
        index.prune(
            argparse.Namespace(
                db="data/lancedb", ledger="data/validate/liveness", apply=True
            )
        )
        == 0
    )


# The frozen fit the trends fixtures run against. `k` and the family count are curated numbers
# the pipeline keeps small by design (`config/role_families.json`), so unlike a row count they
# stay at their real order of magnitude rather than being pushed over 999.
_TRENDS_K = 120
_TRENDS_VERSION = 7
#: `min_years` -> the band `roles.band` derives from it. Five bands, so a family's rows spread
#: across every one of them and the group count the `appended` line reports is a real product.
_TRENDS_BANDS = (
    (None, "unspecified"),
    (0, "entry"),
    (3, "mid"),
    (5, "senior"),
    (9, "staff"),
)
_TRENDS_ATSES = ("workday", "lever", "greenhouse", "ashby", "icims")
#: Curated family names, one per cluster below `len(_TRENDS_FAMILIES)`; every higher cluster is
#: non-tech. The first five are real families because they are the ones the `top:` sample names.
_TRENDS_FAMILIES = (
    "software-engineering",
    "data",
    "ml",
    "devops",
    "security",
    *(f"family-{n:02d}" for n in range(5, 41)),
)
#: `(family index, ats index, extra rows)` — the groups that outweigh the one-row-per-group base,
#: so `stock_top`'s ranking is decided by the counts rather than by a tie-break. The first clears
#: 999 because that count is formatted `{c}`: the case a `{c:,}` would be added to.
_TRENDS_HEAVY = ((0, 0, 1203), (1, 1, 52), (2, 2, 41), (3, 3, 30), (4, 4, 19))
_TRENDS_NON_TECH = 1102


class _PinnedClock:
    """Stands in for `datetime` inside an emitter whose line carries `now()`.

    A `body` check 4 compares character for character cannot hold a real clock. Pinned rather
    than matched loosely, for the reason check 4's own docstring gives: a `body` matched by
    prefix is back to being documentation nobody checked.
    """

    def __init__(self, moment: str) -> None:
        self._moment = datetime.fromisoformat(moment)

    def now(self, tz: object = None) -> datetime:
        return self._moment

    # Only `now()` is pinned; everything else the emitter reaches for on `datetime` is the real
    # thing. `role_trends` gained a `fromisoformat` call when the trends ledger moved to parquet,
    # and a stub that pins the clock by replacing the whole module answers that with an
    # AttributeError — the emitter stops before its line, and check 4 reports a body mismatch for
    # what is really a fixture that went stale under a change to the code it stands in for.
    @staticmethod
    def fromisoformat(value: str) -> datetime:
        return datetime.fromisoformat(value)


def _trends_rows() -> tuple[list[dict], dict[str, str]]:
    """The served rows the trends fixtures count, and the `id -> family` they must produce.

    Vectors are one-hot and the centroid store is the identity matrix, so a row's cluster is
    stated rather than hoped for: `roles.assign` is a plain `argmax` of `vectors @ centroids.T`.
    """
    rows: list[dict] = []
    assigned: dict[str, str] = {}

    def add(
        cluster: int, band_index: int, ats_index: int, count: int, tag: str = ""
    ) -> None:
        years, _ = _TRENDS_BANDS[band_index]
        ats = _TRENDS_ATSES[ats_index]
        vector = [0.0] * _TRENDS_K
        vector[cluster] = 1.0
        for n in range(count):
            # `tag` keeps the heavy groups' ids off the base grid's: a collision would put two
            # rows in the table under one id, which the served table never holds and which
            # would silently shrink `assigned` below the row count.
            job_id = f"{ats}:acme:{cluster}-{band_index}-{ats_index}-{tag}{n}"
            rows.append(
                _served_row(
                    job_id,
                    _TRENDS_K,
                    vector=list(vector),
                    min_years=years,
                    title="backend engineer",
                    # Inside the ADR-0051 window the pinned clock puts this run in, so every
                    # served row is also a `new` row and both metrics carry a count.
                    first_seen="2026-09-07T00:00:00+00:00",
                )
            )
            if cluster < len(_TRENDS_FAMILIES):
                assigned[job_id] = _TRENDS_FAMILIES[cluster]

    for cluster in range(len(_TRENDS_FAMILIES)):
        for band_index in range(len(_TRENDS_BANDS)):
            for ats_index in range(len(_TRENDS_ATSES)):
                add(cluster, band_index, ats_index, 1)
    for cluster, ats_index, extra in _TRENDS_HEAVY:
        add(cluster, 2, ats_index, extra, "h")  # band 2 == mid
    add(_TRENDS_K - 1, 0, 0, _TRENDS_NON_TECH)  # the tech filter's creep (ADR-0017)
    return rows, assigned


def _trends_taxonomy(tmp_path: Path, *, unmapped: bool = False) -> None:
    """Write the centroid store and the curated family map, in the paths a real run reads."""
    import numpy as np

    centroids = Path("data/state/role_centroids")
    centroids.mkdir(parents=True, exist_ok=True)
    np.eye(_TRENDS_K, dtype="float32").tofile(centroids / "centroids.f32")
    (centroids / "manifest.json").write_text(
        json.dumps({"k": _TRENDS_K, "dim": _TRENDS_K, "version": _TRENDS_VERSION}),
        encoding="utf-8",
    )
    families = Path("config/role_families.json")
    families.parent.mkdir(parents=True, exist_ok=True)
    non_tech = list(range(len(_TRENDS_FAMILIES), _TRENDS_K - int(unmapped)))
    families.write_text(
        json.dumps(
            {
                "centroid_version": _TRENDS_VERSION,
                "families": [
                    {"name": name, "clusters": [n]}
                    for n, name in enumerate(_TRENDS_FAMILIES)
                ],
                "non_tech": {"clusters": non_tech},
            }
        ),
        encoding="utf-8",
    )


def _trends_argv() -> tuple[str, ...]:
    """`role_trends main()`'s paths, all relative under a `chdir`ed tmp dir — two of them are
    printed into lines this file pins, and a `body` cannot carry this machine's temp directory."""
    return (
        "--db",
        "data/lancedb",
        "--centroids",
        "data/state/role_centroids",
        "--families",
        "config/role_families.json",
        "--watchlist",
        "config/role_watchlist.json",
        "--ledger",
        "data/state/role_trends.parquet",
        "--assignments",
        "data/state/role_assignments.parquet",
        "--reassignments",
        "data/state/role_reassignments.csv",
    )


def _trends(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    previous: str = "none",
    rows: bool = True,
) -> None:
    """One `role_trends` tick over a served table whose every row's cluster is stated, not hoped.

    `previous` picks which shape the `assignments:` line takes — `"none"` leaves no snapshot (the
    first-snapshot branch), `"same"` writes this tick's own assignment back (nothing moved), and
    `"moved"` re-files three groups so the transition ranking has something in it.
    """
    pytest.importorskip("lancedb")
    pytest.importorskip("numpy")
    pytest.importorskip("pyarrow")
    from headstart.ingest import role_assignments, role_trends

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        role_trends, "datetime", _PinnedClock("2026-09-08T00:00:00+00:00")
    )
    _trends_taxonomy(tmp_path)
    served, assigned = _trends_rows()
    _served_table(Path("data/lancedb"), served if rows else [], _TRENDS_K)

    if previous != "none":
        snapshot = dict(assigned)
        if previous == "moved":
            # One whole group re-filed per pair, so each transition's count is the group's own
            # size and the `top:` ranking cannot depend on dict order.
            was = {
                "software-engineering": "data",
                "data": "ml",
                "ml": "devops",
            }
            for job_id, family in assigned.items():
                if family in was and job_id.split(":")[-1].startswith(
                    f"{_TRENDS_FAMILIES.index(family)}-2-"
                ):
                    snapshot[job_id] = was[family]
        role_assignments.save(
            Path("data/state/role_assignments.parquet"), snapshot, _TRENDS_VERSION
        )
    _run_main(role_trends, monkeypatch, *_trends_argv())


def _trends_first_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No snapshot to diff against, so transitions start next run."""
    _trends(tmp_path, monkeypatch)


def _trends_moved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Three groups re-filed since the last tick — the `| top:` tail is present."""
    _trends(tmp_path, monkeypatch, previous="moved")


def _trends_unmoved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The snapshot matches this tick exactly, so the whole `| top:` clause is omitted."""
    _trends(tmp_path, monkeypatch, previous="same")


def _trends_all_non_tech(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every served row is the tech filter's creep, so no stock family has a row at all."""
    pytest.importorskip("lancedb")
    pytest.importorskip("numpy")
    pytest.importorskip("pyarrow")
    from headstart.ingest import role_trends

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        role_trends, "datetime", _PinnedClock("2026-09-08T00:00:00+00:00")
    )
    _trends_taxonomy(tmp_path)
    served, _ = _trends_rows()
    non_tech = [r for r in served if r["vector"][_TRENDS_K - 1] == 1.0]
    _served_table(Path("data/lancedb"), non_tech, _TRENDS_K)
    _run_main(role_trends, monkeypatch, *_trends_argv())


def _trends_diff_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The reassignment diff raises — a diagnostic that must never sink a run that counted fine."""
    pytest.importorskip("pyarrow")
    from headstart.ingest import role_assignments

    def _no_space(*_args: object, **_kwargs: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(role_assignments, "load_previous", _no_space)
    _trends(tmp_path, monkeypatch)


def _trends_empty_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The served table exists and holds nothing — `np.stack` has no empty case."""
    _trends(tmp_path, monkeypatch, rows=False)


def _trends_no_centroids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither the fit nor the curated map is on disk — the pre-run skip, at WARNING."""
    pytest.importorskip("numpy")
    from headstart.ingest import role_trends

    monkeypatch.chdir(tmp_path)
    _run_main(role_trends, monkeypatch, *_trends_argv())


def _trends_bad_taxonomy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A refit shipped without re-curating the map: one cluster lands in no family at all."""
    pytest.importorskip("lancedb")
    pytest.importorskip("numpy")
    pytest.importorskip("pyarrow")
    from headstart.ingest import role_trends

    monkeypatch.chdir(tmp_path)
    _trends_taxonomy(tmp_path, unmapped=True)
    _served_table(Path("data/lancedb"), _trends_rows()[0], _TRENDS_K)
    monkeypatch.setattr(sys, "argv", [role_trends.__name__, *_trends_argv()])
    assert (
        role_trends.main() == 1
    )  # an unusable taxonomy is a defect, not a prerequisite


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
    # -- scrape_join corpus volume (emitter-verified: `main()` over 15 fragment dirs) ----------
    Line(
        consumer="fanout_corpus.ATS_LINES",
        emitter=_SCRAPE_JOIN,
        body="workday.jsonl: 2010 lines from 15 shard(s)",
        why="raw per-ATS scrape volume, the denominator every later stage divides",
        emit=_join_snapshot,
    ),
    Line(
        consumer="fanout_corpus.JOIN_TOTAL",
        emitter=_SCRAPE_JOIN,
        body="wrote 4010 lines across 21 ATS files -> data/jobs",
        why="the corpus total the tech gate's percentage is taken against",
        emit=_join_snapshot,
    ),
    # -- filter_tech (emitter-verified: `main()` over a scraped dir and three empty ones) ------
    Line(
        consumer="fanout_corpus.TECH",
        emitter=_FILTER_TECH,
        body="workday              1204     4816   25.0%",
        why=(
            "a fixed-width table row: the pattern reads columns, so `\\s+` must stay greedy. The "
            "documented row was a space narrower than `{ats:<16}` renders until it was emitted"
        ),
        emit=_tech_gate,
    ),
    Line(
        consumer="fanout_corpus.TECH_TOTAL",
        emitter=_FILTER_TECH,
        body="TOTAL                1204     4816   25.0%  (dropped 3612 non-tech) -> data/jobs/tech",
        why="the TOTAL row, which carries a tail the per-ATS rows do not",
        emit=_tech_gate,
    ),
    Line(
        consumer="fanout_corpus.TECH_EMPTY",
        emitter=_FILTER_TECH,
        body=(
            "3 ATS(es) were in this run's slice but contributed zero rows: jazzhr, jobvite, "
            "sensehq — their boards failed, were deferred, or are genuinely empty"
        ),
        why="an ATS that scraped nothing used to be simply absent from the table above",
        emit=_tech_gate,
    ),
    Line(
        consumer="fanout_corpus.TECH_ZERO_TOTAL",
        emitter=_FILTER_TECH,
        body="no rows at all reached the tech filter -> data/jobs/tech is empty",
        why="the corpus-wide zero, which otherwise printed a header and stopped",
        emit=_tech_gate_nothing,
    ),
    # -- update_descriptions (emitter-verified: `main()` over a seeded store and corpus) -------
    Line(
        consumer="fanout_corpus.STORE",
        emitter=_DESCRIPTIONS,
        body="prior store: 4,418 already-embedded ids",
        why="thousands separators: the group must be `[\\d,]+`, not `\\d+`",
        emit=_descriptions,
    ),
    Line(
        consumer="fanout_corpus.DESC",
        emitter=_DESCRIPTIONS,
        body="lever: filled 1,204 from the store, learned 1,005, queued 1,005 to re-derive",
        why=(
            "the plain form. This pattern once also required a `settled N as having none` clause "
            "ADR-0089 deleted, so every ATS printed 0 while the log said `filled 6,012`"
        ),
        emit=_descriptions,
    ),
    Line(
        consumer="fanout_corpus.DESC",
        emitter=_DESCRIPTIONS,
        body=(
            "workday: filled 1,204 from the store, learned 1,005, queued 1,005 to re-derive, "
            "1,002 still unrecorded"
        ),
        why="with the optional `, N still unrecorded` tail — the pattern must not require it",
        emit=_descriptions,
    ),
    Line(
        consumer="fanout_corpus.SKIP",
        emitter=_DESCRIPTIONS,
        body="skip-list: 4,418 Jobs held",
        why="the ADR-0048 detail skip-list size",
        emit=_descriptions,
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
    # -- update_meta (emitter-verified: `refresh` takes four paths and a pinned version) -------
    Line(
        consumer="fanout_merge.META_LINE",
        emitter=_META,
        body=(
            "derivations v14 stored, v15 in code — SWEEPING; corpus facts for 1006 Jobs; "
            "1204 queued to re-derive; 5016 held descriptions"
        ),
        why="a sweep, with the optional `; N held descriptions` clause present",
        emit=_meta_sweep,
    ),
    Line(
        consumer="fanout_merge.META_LINE",
        emitter=_META,
        body=(
            "derivations v15 stored, v15 in code — no sweep; corpus facts for 1006 Jobs; "
            "0 queued to re-derive"
        ),
        why="the no-sweep branch, and the held-descriptions clause omitted",
        emit=_meta_no_sweep,
    ),
    Line(
        consumer="fanout_merge.META_REFRESHED",
        emitter=_META,
        body=(
            "refreshed 5016 rows: 1006 with changed facts, 4010 with changed derivations, "
            "1002 given a has_description they never had"
        ),
        why="what the sweep touched; direction-blind, hence the next line",
        emit=_meta_sweep,
    ),
    Line(
        consumer="fanout_merge.META_DIRECTION",
        emitter=_META,
        body=(
            "experience derivations: 1004 gained, 1002 lost, 1003 retiered, 1001 moved (same "
            "tier, new value) (ADR-0066)"
        ),
        why="ADR-0066's direction split — `lost` is the number worth an alarm",
        emit=_meta_sweep,
    ),
    Line(
        consumer="fanout_merge.META_WATERMARK",
        emitter=_META,
        body="watermark -> v15",
        why="the stamp that stops the next run re-sweeping; absent when the store was lost",
        emit=_meta_sweep,
    ),
    # -- index sync / prune (emitter-verified behind `pytest.importorskip`, see `heavy`) -------
    Line(
        consumer="fanout_merge.SCOPE_OUTCOME",
        emitter=_INDEX,
        body=(
            "scrape outcome: 1012 Board(s) returned a list that is not authoritative (truncated, "
            "or the scrape raised) and are excluded from the eviction scope — their missing rows "
            "are unscraped, not closed: lever:short-0000, lever:short-0001, lever:short-0002, "
            "lever:short-0003, lever:short-0004, lever:short-0005, lever:short-0006, "
            "lever:short-0007, lever:short-0008, lever:short-0009, +1002 more"
        ),
        why=(
            "ADR-0053's per-run headline; the pattern prefix-searches so the sample tail may grow. "
            "The `body` here was FICTION until this entry gained its `emit`: it named ONE Board "
            "and then claimed `+95 more`, a shape `log.named_sample` cannot produce for any count "
            "— it shows ten and counts the rest, so 96 Boards render as ten names and `+86 more`"
        ),
        emit=_index_sync,
        heavy=True,
    ),
    Line(
        consumer="fanout_merge.SCOPE_EXCLUDED",
        emitter=_INDEX,
        body="scope-excluded Board: lever:short-0000 — HTTPError: HTTP Error 429: Too Many Requests",
        why=(
            "one line per excluded Board with its reason, now INFO rather than WARNING (a GitHub "
            "annotation is a quota, not a level). `_log_reasons` builds it from a label argument, "
            "so the `label: ` join is not in any one format string"
        ),
        emit=_index_sync,
        heavy=True,
        waived=("scope-excluded Board: ",),
    ),
    Line(
        consumer="fanout_merge.SCOPE_ROWS",
        emitter=_INDEX,
        body=(
            "scope exclusion keeps 2116 eviction-candidate row(s) out of scope across 1012 "
            "Board(s) — ADR-0053 has no drain, so a Board short on every run never re-enters "
            "scope; watch this number across runs, not within one; worst: lever:short-0000 "
            "(1105), lever:short-0001 (1), lever:short-0002 (1), lever:short-0003 (1), "
            "lever:short-0004 (1), lever:short-0005 (1), lever:short-0006 (1), "
            "lever:short-0007 (1), lever:short-0008 (1), lever:short-0009 (1), +1002 more"
        ),
        why=(
            "with the `; worst: ...` sample present. FICTION until this entry gained its `emit`, "
            "the same way as `SCOPE_OUTCOME` above: one named Board and a `+95 more` tail that "
            "`named_sample`'s cap of ten cannot render"
        ),
        emit=_index_sync,
        heavy=True,
    ),
    Line(
        consumer="fanout_merge.SCOPE_ROWS",
        emitter=_INDEX,
        body=(
            "scope exclusion keeps 0 eviction-candidate row(s) out of scope across 1012 Board(s) "
            "— ADR-0053 has no drain, so a Board short on every run never re-enters scope; watch "
            "this number across runs, not within one"
        ),
        why=(
            "the `; worst: ...` clause is omitted when the sample is empty — the omitted-when-zero "
            "shape. The 0 is the branch, not a small fixture: every row on every excluded Board "
            "came back in this scrape, which is the only input that empties the ranking"
        ),
        emit=_index_sync_all_returned,
        heavy=True,
    ),
    Line(
        consumer="fanout_merge.SCOPE_ROW_BOARD",
        emitter=_INDEX,
        body="  1105 eviction-candidate row(s) kept out of scope on lever:short-0000",
        why=(
            "the per-Board row cost. No longer capped at a top-N and no longer WARNING, so every "
            "excluded Board now carries one"
        ),
        emit=_index_sync,
        heavy=True,
    ),
    Line(
        consumer="fanout_merge.GRACE",
        emitter=_INDEX,
        body=(
            "grace period: 2207 id(s) unconfirmed, awaiting a second look before eviction; of the "
            "3010 carried in, 1002 reappeared in this scrape and 1003 are unconfirmed again "
            "(their Board sat out this run's slice, was Unauthoritative, or emitted nothing at "
            "all — all three leave the eviction scope) (ADR-0083)"
        ),
        why=(
            "ADR-0083's per-Job grace period — the only mechanism left that withholds in-scope. "
            "The documented tail was FICTION: it read `(their Board sat out this run's slice, or "
            "came back unauthoritative)`, naming two causes where the emitter names three and "
            "says all three leave the scope. The pattern stops at `unconfirmed again`, so nothing "
            "here ever read the half a person actually learns the mechanism from"
        ),
        emit=_index_sync,
        heavy=True,
    ),
    Line(
        consumer="fanout_merge.SYNC_PLAN",
        emitter=_INDEX,
        body="plan: add 2404 (1200 new listings + 1204 re-embedded), evict 1005 -> net +195 rows",
        why="the one line saying whether the served index grew; `+d` on a gain",
        emit=_index_sync,
        heavy=True,
    ),
    Line(
        consumer="fanout_merge.SYNC_PLAN",
        emitter=_INDEX,
        body="plan: add 1204 (0 new listings + 1204 re-embedded), evict 1005 -> net -1005 rows",
        why="a shrinking run: the net carries a leading `-`, so the group must be `[+-]\\d+`",
        emit=_index_sync_shrinking,
        heavy=True,
    ),
    Line(
        consumer="fanout_merge.SYNC_DONE",
        emitter=_INDEX,
        body="done: table 'jobs' now holds 9742 rows at data/lancedb",
        why=(
            "the served row count after sync, before prune touches it. The path is `args.db` and "
            "is relative because the fixture chdirs — a real run prints this exact string"
        ),
        emit=_index_sync,
        heavy=True,
    ),
    Line(
        consumer="fanout_merge.ID_BATCH",
        emitter=_INDEX,
        body=(
            "add [2401-2404 of 2404]: lever:ok:up-996 lever:ok:up-997 lever:ok:up-998 "
            "lever:ok:up-999"
        ),
        why=(
            "the add side of the id batches the per-ATS churn table is built from. FICTION until "
            "this entry gained its `emit`: it documented `add [1-3 of 12004]`, and `_log_ids` "
            "steps by `_IDS_PER_LINE` (100) — so the FIRST batch of 12,004 ids is `[1-100 …]` and "
            "only the LAST one is ever short. That is why this body is the tail of the run"
        ),
        emit=_index_sync,
        heavy=True,
    ),
    Line(
        consumer="fanout_merge.ID_BATCH",
        emitter=_INDEX,
        body=(
            "evict [1001-1005 of 1005]: lever:ok:closed-995 lever:ok:closed-996 "
            "lever:ok:closed-997 lever:ok:closed-998 lever:ok:closed-999"
        ),
        why=(
            "the evict side; both labels come from `_log_ids` call sites, not a format string. "
            "FICTION too, and worse than its neighbour: `evict [101-103 of 605]` is a *middle* "
            "batch three ids wide, which the 100-id step cannot produce at any position"
        ),
        emit=_index_sync,
        heavy=True,
    ),
    Line(
        consumer="fanout_merge.KEEP_SET",
        emitter=_INDEX,
        body="keep-set: 1012 Scrapable Boards (enabled ATSes)",
        why=(
            "CONTEXT.md's counting vocabulary. This pattern said `live Boards` — a phrase CLAUDE.md "
            "forbids — and matched nothing. `index_plan` also emits a `keep-set:` line, but under "
            "the `[index_plan]` tag and saying `Scrapable Board(s)`, so it cannot collide. The "
            "count is four-figure because `_MIN_KEEP_BOARDS` aborts the prune below 1,000"
        ),
        emit=_index_prune,
        heavy=True,
    ),
    Line(
        consumer="fanout_merge.PRUNE_SUMMARY",
        emitter=_INDEX,
        body="index: 4709 rows | evict 2454 (1204 off-Board + 1250 duplicate) -> 2255 remain",
        why="prune's two reasons split out; they have different fixes and must not be summed",
        emit=_index_prune,
        heavy=True,
    ),
    Line(
        consumer="fanout_merge.PRUNE_BREAKDOWN",
        emitter=_INDEX,
        body="evict off-Board: 1204 rows across 3 ATSes (personio 1000, zoho 200, workable 4)",
        why="the emitter's own top-5 ranking, with no overflow tail",
        emit=_index_prune,
        heavy=True,
    ),
    Line(
        consumer="fanout_merge.PRUNE_BREAKDOWN",
        emitter=_INDEX,
        body=(
            "evict duplicate: 1250 rows across 9 ATSes (keka 400, ashby 300, icims 200, "
            "smartrecruiters 150, freshteam 100, +4 more)"
        ),
        why="with the `, +N more` overflow inside the parens — `[^)]*` must reach past the commas",
        emit=_index_prune,
        heavy=True,
    ),
    Line(
        consumer="fanout_merge.PRUNE_DONE",
        emitter=_INDEX,
        body="done: pruned 2454 rows; table 'jobs' now holds 2255",
        why="the final served count; distinct wording from sync's own `done:` line",
        emit=_index_prune,
        heavy=True,
    ),
    # -- role_trends (emitter-verified behind `pytest.importorskip`, see `heavy`) --------------
    Line(
        consumer="fanout_merge.TRENDS_ASSIGNING",
        emitter=_TRENDS,
        body="assigning 3472 served rows to 41 families via 120 clusters (centroid version 7)",
        why=(
            "logged before the slow vector read, so a stalled step is not unnarrated. The family "
            "and cluster counts are curated (`config/role_families.json`) and stay at their real "
            "order of magnitude; the row count is the one a `{n:,}` could be added to"
        ),
        emit=_trends_first_snapshot,
        heavy=True,
    ),
    Line(
        consumer="fanout_merge.TRENDS_APPENDED",
        emitter=_TRENDS,
        body=(
            "appended 2051 rows @ 2026-09-08T00:00:00+00:00 -> data/state/role_trends.parquet | top: "
            "software-engineering/mid/workday 1204, data/mid/lever 53, ml/mid/greenhouse 42, "
            "devops/mid/ashby 31, security/mid/icims 20 | new in 7d: 2370"
        ),
        why="the trends ledger tick; `new` is a 7-day LEVEL, never inflow (CONTEXT.md)",
        emit=_trends_moved,
        heavy=True,
    ),
    Line(
        consumer="fanout_merge.TRENDS_APPENDED",
        emitter=_TRENDS,
        body=(
            "appended 1 rows @ 2026-09-08T00:00:00+00:00 -> data/state/role_trends.parquet | top:  "
            "| new in 7d: 0"
        ),
        why=(
            "the empty-`top` form: `stock_top` is a slice of a filtered comprehension, so a run "
            "with no stock-family row joins to '' and renders `| top:  |`. A `(.+)` group dropped "
            "this line outright — no trends tick reported, no error raised. The count was FICTION: "
            "it read `appended 0 rows`, and `append_ledger` returns `len(counts) + 1` — the "
            "non-tech diagnostic row is written every run, so 0 is the one value it cannot return"
        ),
        emit=_trends_all_non_tech,
        heavy=True,
    ),
    Line(
        consumer="fanout_merge.TRENDS_NONTECH",
        emitter=_TRENDS,
        body=(
            "non-tech: 1102 of 3472 served rows (31.7% — the ADR-0017 filter's creep) excluded "
            "from the chart"
        ),
        why=(
            "the measured creep of the recall-biased tech gate into the served table. The share "
            "is not the pipeline's (~1.4%): a four-figure non-tech count at that ratio needs a "
            "70,000-row fixture table, so this body is verified, not representative"
        ),
        emit=_trends_first_snapshot,
        heavy=True,
    ),
    Line(
        consumer="fanout_merge.TRENDS_ASSIGNMENTS",
        emitter=_TRENDS,
        body=(
            "assignments: 1311 of 2370 rows changed family (55.32%), 3 transition rows | top: "
            "data->software-engineering 1208, ml->data 57, devops->ml 46"
        ),
        why=(
            "reassignment vs closure — with the optional `| top:` tail present. The share is a "
            "fixture's, not a run's: three whole groups are re-filed so the ranking cannot depend "
            "on dict order, which no real refit would do"
        ),
        emit=_trends_moved,
        heavy=True,
    ),
    Line(
        consumer="fanout_merge.TRENDS_ASSIGNMENTS",
        emitter=_TRENDS,
        body="assignments: 0 of 2370 rows changed family (0.00%), 0 transition rows",
        why="nothing moved, so `top` is empty and the whole `| top:` clause is omitted",
        emit=_trends_unmoved,
        heavy=True,
    ),
    Line(
        consumer="fanout_merge.TRENDS_FIRST_SNAPSHOT",
        emitter=_TRENDS,
        body=(
            "assignments: first snapshot — wrote 2370 rows to "
            "data/state/role_assignments.parquet; transitions start next run"
        ),
        why=(
            "reachable on any run, not just the first: a centroid refit re-bases the snapshot. "
            "The path was FICTION — the body named `role_assignments.csv`, but the line prints "
            "`args.assignments`, and that snapshot is the `.parquet` `role_assignments.save` "
            "writes. The `.csv` beside it is the *transition* ledger, a different file"
        ),
        emit=_trends_first_snapshot,
        heavy=True,
    ),
    Line(
        consumer="fanout_merge.TRENDS_DIFF_SKIPPED",
        emitter=_TRENDS,
        body="assignment diff skipped: OSError: [Errno 28] No space left on device",
        why="the diff is `except Exception` on purpose — a diagnostic must not sink a good run",
        emit=_trends_diff_skipped,
        heavy=True,
    ),
    Line(
        consumer="fanout_merge.TRENDS_SKIP_MISSING",
        emitter=_TRENDS,
        body=(
            "skipping trends this run — missing data/state/role_centroids/manifest.json, "
            "data/state/role_centroids/centroids.f32, config/role_families.json (fit centroids "
            "with the cluster-roles workflow; the family map ships in git, ADR-0040)"
        ),
        why=(
            "one of three distinct skip paths, matched on its own line rather than a substring. "
            "FICTION: the body named `data/state/role_centroids.npz`, a file this project does "
            "not have. `--centroids` is a *directory* and the guard names the three real paths it "
            "stats — two inside that dir, plus the curated map, which go missing for different "
            "reasons (one rides the state artifact, one ships in git)"
        ),
        emit=_trends_no_centroids,
        heavy=True,
    ),
    Line(
        consumer="fanout_merge.TRENDS_SKIP_EMPTY",
        emitter=_TRENDS,
        body="served table 'jobs' is empty — no trend rows this run",
        why="the second skip path; `np.stack` has no empty case, so this returns before it",
        emit=_trends_empty_table,
        heavy=True,
    ),
    Line(
        consumer="fanout_merge.TRENDS_SKIP_TAXONOMY",
        emitter=_TRENDS,
        body=(
            "role taxonomy unusable, no trends this run: config/role_families.json leaves "
            "cluster(s) [119] unmapped — every cluster must land in a family or in non_tech, or "
            "its rows vanish from the chart"
        ),
        why=(
            "the third: a real defect (a refit shipped without re-curating the map), so ERROR. "
            "FICTION: the body read `3 families have no centroid`, which is backwards and is not "
            "one of the five sentences `roles.load_families`/`load_watchlist` can raise — the "
            "validated direction is a *cluster* with no family, because that is what silently "
            "drops rows off the chart"
        ),
        emit=_trends_bad_taxonomy,
        heavy=True,
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
    # -- scrape_plan (emitter-verified: `main()` over a stubbed active list and four ledgers) --
    Line(
        consumer="fanout_plan.QUARANTINE_SKIP",
        emitter=_SCRAPE_PLAN,
        body="quarantine: skipped 1104 of 1104 confirmed-gone board(s)",
        why="ADR-0058 quarantine acting on the plan; the ledger itself is untouched",
        emit=_plan_measured,
    ),
    Line(
        consumer="fanout_plan.VALUE_GATE",
        emitter=_SCRAPE_PLAN,
        body=(
            "value gate: skipped 7 Board(s) costing over 15 min for under 2 tech jobs/min — "
            "greenhouse:gate-0 (0.03/min), greenhouse:gate-1 (0.03/min), "
            "greenhouse:gate-2 (0.03/min), greenhouse:gate-3 (0.04/min), "
            "greenhouse:gate-4 (0.04/min), greenhouse:gate-5 (0.04/min), "
            "greenhouse:gate-6 (0.04/min)"
        ),
        why=(
            "ADR-0064 removing work before packing. `named_sample`'s cap is 10, so seven gated "
            "Boards are all named — the documented body had seven with a `+5 more` tail, which "
            "that function cannot produce for any count under eleven"
        ),
        emit=_plan_measured,
    ),
    Line(
        consumer="fanout_plan.GATE_BOARD",
        emitter=_SCRAPE_PLAN,
        body="workday:dollartree/dollartreeus (0.03/min)",
        why=(
            "one item inside the gate's sample above — a fragment pattern, not a whole line, "
            "which is also why it stays source-verified: check 4 asks whether `body` is one of "
            "the messages the emitter logged, and no run logs this fragment on its own"
        ),
    ),
    Line(
        consumer="fanout_plan.SLICE",
        emitter=_SCRAPE_PLAN,
        body=(
            "slice: 2307 boards (1205 priority + 1102 exploration); 1204 hold unsettled "
            "descriptions, out of 1,204 gap boards (117,992 jobs) still to drain"
        ),
        why="a thin per-ATS scrape is often this run's exploration draw, not a regression",
        emit=_plan_measured,
    ),
    Line(
        consumer="fanout_plan.COST_COVERAGE",
        emitter=_SCRAPE_PLAN,
        body=(
            "cost: measured seconds for 1205/2307 boards (1212 in ledger); rest estimated from "
            "their ATS median"
        ),
        why="low coverage means the pack is sized on medians and a straggler can hide",
        emit=_plan_measured,
    ),
    Line(
        consumer="fanout_plan.COST_COLDSTART",
        emitter=_SCRAPE_PLAN,
        body=(
            "cost: no measurements yet — cold-start heuristic (ADR-0026); the join writes "
            "data/state/board_cost.csv and the next run packs on seconds"
        ),
        why="the cold-start branch, which is what makes the two forms below reachable",
        emit=_plan_coldstart,
    ),
    Line(
        consumer="fanout_plan.MAKESPAN",
        emitter=_SCRAPE_PLAN,
        body="2307 boards across 9 shards; predicted makespan ~67.1 min (total work Σ 86.5 min)",
        why="the measured form, with the makespan tail",
        emit=_plan_measured,
    ),
    Line(
        consumer="fanout_plan.MAKESPAN",
        emitter=_SCRAPE_PLAN,
        body="15000 boards across 15 shards (cold-start cost units)",
        why=(
            "the cold-start form. The makespan-only pattern matched nothing here and printed no "
            "`predicted:` line at all, on exactly the runs whose plan is least trustworthy"
        ),
        emit=_plan_coldstart,
    ),
    Line(
        consumer="fanout_timing.PLAN_SHARD",
        emitter=_SCRAPE_PLAN,
        body="shard 0: 251 boards (~67.1 min)",
        why="the measured per-shard serial estimate — NOT a wall estimate",
        emit=_plan_measured,
    ),
    Line(
        consumer="fanout_timing.PLAN_SHARD",
        emitter=_SCRAPE_PLAN,
        body="shard 0: 1000 boards (cost ~5000)",
        why="the cold-start form: unitless pack weights, deliberately not written as fake minutes",
        emit=_plan_coldstart,
    ),
    Line(
        consumer="fanout_plan.SPREAD",
        emitter=_SCRAPE_PLAN,
        body=(
            "predicted spread: min 2.4 / mean 9.6 / max 67.1 min (6.98x mean); "
            "single-board floor 65.0 min"
        ),
        why="the planner naming its own straggler, in the units floor_table reports after the fact",
        emit=_plan_measured,
    ),
    Line(
        consumer="fanout_plan.FLOOR_WARN",
        emitter=_SCRAPE_PLAN,
        body=(
            "one board costs 65.0 min, above the 9.6 min even share — the makespan floor is this "
            "board, not the packing"
        ),
        why="fires only when it applies; a better packer cannot help a floor-bound shard",
        emit=_plan_measured,
    ),
    Line(
        consumer="fanout_plan.BUDGET_WARN",
        emitter=_SCRAPE_PLAN,
        body=(
            "predicted makespan ~67.1 min exceeds the 60 min shard budget — shards matching their "
            "prediction will bank partials"
        ),
        why="an advance warning of budget kills, printed before any shard has run",
        emit=_plan_measured,
    ),
    # -- update_ledgers' other three subcommands (emitter-verified: seeded ledgers on disk) ----
    Line(
        consumer="fanout_ledgers.PRIORITY_HEADER",
        emitter=_LEDGERS,
        body=(
            "priority: 2215 boards in snapshot | 2225 ledger rows (1205 new, 1010 pruned, "
            "1020 carried) -> data/state/board_priority.csv"
        ),
        why="the measured-tech-yield ledger the plan ranks Boards by",
        emit=_ledger_priority,
    ),
    Line(
        consumer="fanout_ledgers.PRIORITY_TOP",
        emitter=_LEDGERS,
        body="      842.8  greenhouse:top (1204 tech jobs)",
        why=(
            "a right-aligned sample row, so the pattern reads it through `\\s+` not fixed columns "
            "— which is also why the documented row could carry the wrong column width unnoticed"
        ),
        emit=_ledger_priority,
    ),
    Line(
        consumer="fanout_ledgers.COST_HEADER",
        emitter=_LEDGERS,
        body=(
            "cost: 1200 boards timed across 15 shard(s) | 2210 ledger rows (1200 new) | "
            "Σ 88142 board-minutes -> data/state/board_cost.csv"
        ),
        why="the cost ledger that decides whether the next plan is measured or a cold start",
        emit=_ledger_cost,
    ),
    Line(
        consumer="fanout_ledgers.COST_MEDIAN",
        emitter=_LEDGERS,
        body="    2393.0s median  greenhouse",
        why=(
            "the per-**ATS** median, which is what `costs_for` prices an unmeasured Board of that "
            "ATS at. The documented body named a Board (`workday:dollartree/dollartreeus`) until "
            "this entry was emitted: `ats_medians` keys by `_ats_of`, so no run can print one"
        ),
        emit=_ledger_cost,
    ),
    Line(
        consumer="fanout_ledgers.GAP_HEADER",
        emitter=_LEDGERS,
        body=(
            "gap: 5,627 stored rows | 1,204 held | 2,408 unsettled across 1,205 boards "
            "(1,010 on a disabled ATS, 1,005 gone from a Board this run scraped in full — both "
            "unreachable) -> data/state/board_description_gap.csv"
        ),
        why=(
            "`held`, not `settled`: the emitter's wording moved and this pattern did not, so every "
            "run printed `no gap summary line found` while the line was right there"
        ),
        emit=_ledger_gap,
    ),
    Line(
        consumer="fanout_ledgers.GAP_TOP",
        emitter=_LEDGERS,
        body="   1,204 unsettled  workday:acme/external",
        why=(
            "thousands separators again — `[\\d,]+`, and `\\s+` for the alignment. The Board is "
            "**lowercased** (ADR-0049), which the documented body had as `.../External`"
        ),
        emit=_ledger_gap,
    ),
    Line(
        consumer="fanout_ledgers.GAP_NO_STORE",
        emitter=_LEDGERS,
        body=(
            "gap: no data/embeddings/jobs/meta.jsonl yet — nothing embedded, so no gap to record"
        ),
        why=(
            "nothing embedded yet, so there is no stored corpus to count a gap against. This "
            "names the **embedding store's metadata**, not the description store the line below "
            "names — the documented body said `data/descriptions` and made the two read as one "
            "file in two states"
        ),
        emit=_ledger_gap_no_meta,
    ),
    Line(
        consumer="fanout_ledgers.GAP_EMPTY_STORE",
        emitter=_LEDGERS,
        body=(
            "gap: data/descriptions holds nothing — the store is missing, not empty; leaving the "
            "ledger as it is"
        ),
        why="the store was lost in transit — writing a gap from it would erase real progress",
        emit=_ledger_gap_empty_store,
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


def _records(
    entry: Line, caplog: pytest.LogCaptureFixture, *args: object
) -> list[logging.LogRecord]:
    """Call the emitter and hand back the records it logged, unrendered.

    Two checks want these and want them differently: check 3 reads the CI *rendering*, check 4
    reads `record.getMessage()` — the emitter's own words, with no clock, tag, level or
    annotation prefix wrapped around them, which is exactly what a `body` claims to be.
    """
    logging.getLogger("headstart").setLevel(logging.INFO)
    with caplog.at_level(logging.DEBUG, logger="headstart"):
        entry.emit(*args)  # type: ignore[misc]
    return list(caplog.records)


def _emitted(entry: Line, caplog: pytest.LogCaptureFixture, *args: object) -> list[str]:
    """Call the emitter and render its records the way a CI log carries them.

    Rendered under `GITHUB_ACTIONS`, because that is the only log these analysers ever read. It
    matters: off Actions a WARNING renders `HH:MM:SS [tag] WARNING: message`, with the level name
    sitting between the tag and the text, and a pattern anchored on `[tag] message` would fail
    against a line CI never produces.
    """
    formatter = log._Formatter()
    return [formatter.format(record) for record in _records(entry, caplog, *args)]


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


_SOURCE_VERIFIED = [entry for entry in CONTRACT if entry.emit is None or entry.heavy]


@pytest.mark.parametrize(
    "entry", _SOURCE_VERIFIED, ids=[_id(entry) for entry in _SOURCE_VERIFIED]
)
def test_the_emitter_still_says_it(entry: Line) -> None:
    """Check 2 — every literal the pattern demands is still in the emitter's source.

    This is the check that catches a rewording. It is a source read, not a call, so it holds for
    the stages CI cannot import; what it cannot see is a change in the *values* a line carries.

    Run for every entry with no `emit` — and for every `heavy` one too, whose `emit` skips
    wherever the `[embed]` extra is missing. Dropping it there would leave those entries on
    check 1 alone in CI, which is *less* than they had before they were converted.
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


@pytest.mark.parametrize(
    "entry",
    [entry for entry in CONTRACT if entry.emit is not None],
    ids=[_id(entry) for entry in CONTRACT if entry.emit is not None],
)
def test_the_documented_body_is_a_line_that_was_logged(
    entry: Line,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Check 4 — `body` is the emitter's own words, character for character.

    Checks 1 and 3 both match the consumer's *pattern*: check 1 against `body`, check 3 against
    the records the emitter really produced. Until this check existed nothing compared those two
    to each other, so a `body` could be pure invention and every test still passed. That is not
    a worry, it is measured: an audit replaced `run_logs.DONE`'s `body` with `done: 111 jobs
    from 222 boards in 333s (444 board errors) | board seconds {'p50': 9.9, ...} | predicted
    99.9 min, actual/predicted 9.99x` — numbers no emitter can produce — and got a full green
    run. `body` is the one place a person reads to learn what a run log actually says, and a
    fabricated one teaches them a line that does not exist.

    Exact equality, deliberately. Every `emit=` fixture is driven by fixed inputs — an elapsed
    of 3603.0, a stubbed `retry_stats`, a seeded ledger — so no clock and no ordering reaches a
    body; the one line that prints a path (`update_ledgers`' `failures:`) is handed a relative
    one by a fixture that chdirs, rather than this machine's temp directory. If an entry ever
    does gain a genuinely volatile stretch, narrow it in that entry rather than loosening the
    comparison for all of them — a `body` matched by prefix or by regex is back to being
    documentation nobody checked.
    """
    messages = [
        record.getMessage() for record in _records(entry, caplog, tmp_path, monkeypatch)
    ]
    assert entry.body in messages, (
        f"the `body` documented for {entry.consumer} is not a line {entry.emitter} emits.\n"
        f"  documented: {entry.body}\n"
        f"  why this entry exists: {entry.why}\n"
        "  emitted:\n    " + "\n    ".join(messages)
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
    """Check 5 — this file is what makes the contract self-maintaining.

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
