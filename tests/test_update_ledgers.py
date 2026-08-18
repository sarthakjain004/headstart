"""Tests for the failures subcommand (headstart.ingest.update_ledgers).

The seam these cover is the one the ledger's own unit tests cannot: `board_failures.update` is
pure and already pinned, but it only behaves correctly if `failures` hands it the two sets in the
*same key space*. Shard reports key `{ats}:{slug}` — and a Workday slug is a whole careers URL —
while the corpus keys `board_key()`. Pair them wrongly and nothing raises: gone-verdicts simply
never meet the successes that should clear them, and a live Board accrues strikes forever.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from headstart.ingest import board_failures as bf
from headstart.ingest.update_ledgers import failures

_WORKDAY_URL = "https://x.wd1.myworkdayjobs.com/Careers"
_WORKDAY_BOARD = "workday:x/Careers"


def _run(tmp_path: Path, *, errors=None, boards_ok=None, jobs=(), ledger=None):
    frag = tmp_path / "fragments" / "shard-0"
    frag.mkdir(parents=True, exist_ok=True)
    (frag / "_shard_report.json").write_text(
        json.dumps({"errors": errors or {}, "boards_ok": boards_ok or []}),
        encoding="utf-8",
    )
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir(exist_ok=True)
    (jobs_dir / "greenhouse.jsonl").write_text(
        "".join(json.dumps({"id": i}) + "\n" for i in jobs), encoding="utf-8"
    )
    path = ledger or tmp_path / "board_failures.csv"
    failures(
        argparse.Namespace(fragments=tmp_path / "fragments", jobs=jobs_dir, ledger=path)
    )
    return bf.load(path)


def test_a_gone_error_is_keyed_the_way_the_corpus_keys_it(tmp_path):
    """The Workday case: the report says `workday:https://…/Careers`, the corpus says
    `workday:x/Careers`. Without normalisation the strike lands on a key no success can reach."""
    rows = _run(
        tmp_path, errors={f"workday:{_WORKDAY_URL}": "HTTPError: HTTP Error 404: "}
    )
    assert list(rows) == [_WORKDAY_BOARD]
    assert rows[_WORKDAY_BOARD].strikes == 1


def test_only_the_gone_class_takes_a_strike(tmp_path):
    rows = _run(
        tmp_path,
        errors={
            "greenhouse:dead": "HTTPError: HTTP Error 404: ",
            "greenhouse:limited": "HTTPError: HTTP Error 429: ",
            "greenhouse:slow": "Timeout: timed out",
        },
    )
    assert list(rows) == ["greenhouse:dead"]


def test_a_zero_job_success_clears_a_streak(tmp_path):
    """boards_ok is the whole point: this Board produced no corpus lines, so without it the run
    is indistinguishable from one that never scraped the Board, and its strikes would persist."""
    ledger = tmp_path / "board_failures.csv"
    bf.save(
        ledger, {"greenhouse:quiet": bf.Failure(4, "HTTPError: HTTP Error 404: ", "t")}
    )
    rows = _run(tmp_path, boards_ok=["greenhouse:quiet"], ledger=ledger)
    assert rows == {}, "an alive-but-empty scrape must clear, not carry, the streak"


def test_corpus_lines_also_clear_a_streak(tmp_path):
    """Belt and braces for reports written before boards_ok existed."""
    ledger = tmp_path / "board_failures.csv"
    bf.save(
        ledger, {"greenhouse:busy": bf.Failure(4, "HTTPError: HTTP Error 404: ", "t")}
    )
    rows = _run(tmp_path, jobs=["greenhouse:busy:1"], ledger=ledger)
    assert rows == {}


def test_an_untouched_board_keeps_its_row(tmp_path):
    ledger = tmp_path / "board_failures.csv"
    before = {"greenhouse:elsewhere": bf.Failure(3, "HTTPError: HTTP Error 404: ", "t")}
    bf.save(ledger, before)
    assert _run(tmp_path, ledger=ledger) == before


def test_a_board_reaches_quarantine_only_after_five_consecutive_runs(tmp_path):
    ledger = tmp_path / "board_failures.csv"
    for n in range(1, bf.QUARANTINE_AT + 1):
        rows = _run(
            tmp_path,
            errors={"greenhouse:dead": "HTTPError: HTTP Error 404: "},
            ledger=ledger,
        )
        assert bool(bf.quarantined(rows)) == (n >= bf.QUARANTINE_AT), (
            f"quarantined after {n} run(s); must take {bf.QUARANTINE_AT}"
        )
