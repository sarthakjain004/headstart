"""End-to-end test of a Restatement (headstart.ingest.restate_run, ADR-0330): Job facts in, one
tick file per run out, in the Board-delta shape the Trends history reads."""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("pyarrow")

from headstart.boards.board_identity import lower_key
from headstart.ingest import job_facts as jf
from headstart.ingest import restate_run
from headstart.ingest import role_family_classifier as rfc
from headstart.ingest.observability import ShardReport
from headstart.trends import trend_history

BOARD = "greenhouse:acme"
RUNS = [f"2026-09-{d:02d}T00:00:00+00:00" for d in (1, 3, 6)]


def _config(tmp_path: Path) -> tuple[Path, Path, Path]:
    families = tmp_path / "role_families.json"
    families.write_text(
        json.dumps(
            {
                "families": [
                    {"name": "software-engineering"},
                    {"name": "qa-test"},
                    {"name": "unclassified-tech"},
                ]
            }
        ),
        encoding="utf-8",
    )
    head = tmp_path / "head"
    head.mkdir()
    np.savez(
        head / "head.npz",
        title_weights=np.eye(3, 3, dtype=np.float32),
        row_weights=np.zeros((3, 2), np.float32),
        bias=np.zeros(3, np.float32),
    )
    (head / "manifest.json").write_text(
        json.dumps(
            {
                "version": 7,
                "model": "stub",
                "model_revision": "stub",
                "row_vector": {"model": "stub", "dim": 2},
                "families": ["software-engineering", "qa-test", "non-tech"],
                "cutoff": 0.6,
            }
        ),
        encoding="utf-8",
    )
    cache = tmp_path / "title_cache.parquet"
    rfc.save_cache(
        cache,
        rfc.Cache(
            7,
            {
                rfc.normalise("Backend Engineer"): np.array([10.0, 0, 0], np.float32),
                rfc.normalise("QA Lead"): np.array([0, 10.0, 0], np.float32),
            },
        ),
    )
    return families, head, cache


def _record(facts: Path, stamp: str, jobs: list[tuple[str, str]]) -> None:
    lines = jf.ScrapedLines(facts / jf.SCRAPED_LINES)
    for job_id, title in jobs:
        lines.see(BOARD, {"id": job_id, "title": title, "department": "Engineering"})
    scope = jf.RunScope(
        authoritative=frozenset({lower_key(BOARD)}), keep_set=None, live={}
    )
    reads = jf.board_reads([ShardReport(boards_ok=[BOARD])], lines.board_lines, scope)
    jf.record_run(lines.close(), facts, stamp, reads, scope)
    (facts / jf.SCRAPED_LINES).unlink(missing_ok=True)


def _describe(store: Path, texts: dict[str, str]) -> None:
    """A description store (ADR-0050) holding ``texts`` for the one ATS these tests scrape."""
    (store / "greenhouse").mkdir(parents=True)
    with gzip.open(
        store / "greenhouse" / "base.jsonl.gz", "wt", encoding="utf-8"
    ) as fh:
        for job_id, text in texts.items():
            fh.write(json.dumps({"id": job_id, "description": text}) + "\n")


def test_a_restatement_writes_one_tick_per_run_that_replays_to_todays_counts(
    tmp_path, monkeypatch
):
    facts, out = tmp_path / "facts", tmp_path / "restated"
    english = (
        "You will design, build and run the services our customers rely on every day."
    )
    _describe(
        tmp_path / "descriptions",
        {f"{BOARD}:{n}": english for n in (1, 2, 3)},
    )
    _record(
        facts, RUNS[0], [(f"{BOARD}:1", "Backend Engineer"), (f"{BOARD}:2", "QA Lead")]
    )
    _record(facts, RUNS[1], [(f"{BOARD}:1", "Backend Engineer")])
    _record(
        facts, RUNS[2], [(f"{BOARD}:1", "Backend Engineer"), (f"{BOARD}:3", "Cashier")]
    )
    families, head, cache = _config(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "restate_run",
            "--facts",
            str(facts),
            "--ledger",
            str(tmp_path / "no-ledger"),
            "--board-failures",
            str(tmp_path / "no-failures.csv"),
            "--classifier",
            str(head),
            "--families",
            str(families),
            "--title-cache",
            str(cache),
            "--db",
            str(tmp_path / "no-db"),
            "--descriptions",
            str(tmp_path / "descriptions"),
            "--out",
            str(out),
        ],
    )

    assert restate_run.main() == 0

    ticks = sorted((out / trend_history.DELTAS).glob("*.parquet"))
    assert len(ticks) == len(RUNS)
    newest, levels = trend_history.board_levels(out)
    assert newest == RUNS[-1]
    stock = {k: n for k, n in levels.items() if k[1] == "stock"}
    # :2 went at its second absence (RUNS[2]); the cashier never passes today's tech filter.
    assert stock == {(BOARD, "stock", "software-engineering", "unspecified"): 1}


def test_no_facts_is_nothing_to_restate(tmp_path, monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["restate_run", "--facts", str(tmp_path), "--out", str(tmp_path / "out")],
    )

    assert restate_run.main() == 0
    assert not (tmp_path / "out").exists()


def test_complete_baseline_keeps_a_served_job_absent_from_all_scrapes(
    tmp_path, monkeypatch
):
    import pyarrow as pa
    import pyarrow.parquet as pq

    facts = tmp_path / "facts"
    for stamp in RUNS[:2]:
        _record(facts, stamp, [(f"{BOARD}:1", "Backend Engineer")])
    families, head, cache = _config(tmp_path)
    baseline_stamp = "2026-09-01T00:30:00+00:00"
    baseline = tmp_path / "baseline.parquet"
    text = "You will design and maintain software services for our customers. Requires 3 years of software engineering experience."
    rows = [
        {
            "id": f"{BOARD}:{n}",
            "kind": "present",
            "title": "Backend Engineer",
            "department": "Engineering",
            "description": text,
            "vector": [0.0, 0.0],
            "reference_board": BOARD,
        }
        for n in (1, 2)
    ]
    pq.write_table(
        pa.Table.from_pylist(rows).replace_schema_metadata(
            {b"baseline": b"true", b"ts": baseline_stamp.encode()}
        ),
        baseline,
    )
    args = [
        "restate",
        "--facts",
        str(facts),
        "--baseline",
        str(baseline),
        "--ledger",
        str(tmp_path / "absent-ledger"),
        "--board-failures",
        str(tmp_path / "absent-failures"),
        "--classifier",
        str(head),
        "--families",
        str(families),
        "--title-cache",
        str(cache),
        "--descriptions",
        str(tmp_path / "absent-descriptions"),
        "--db",
        str(tmp_path / "absent-db"),
        "--out",
        str(tmp_path / "restated"),
    ]
    monkeypatch.setattr(sys, "argv", args)
    assert restate_run.main() == 0
    _, levels = trend_history.board_levels(tmp_path / "restated")
    assert sum(n for k, n in levels.items() if k[1] == "stock") == 2


def test_selected_description_read_preserves_updates_and_blanks(tmp_path):
    from headstart.ingest.update_descriptions import read_store

    _describe(
        tmp_path, {f"{BOARD}:1": "old", f"{BOARD}:2": "kept", f"{BOARD}:3": "ignored"}
    )
    with gzip.open(tmp_path / "greenhouse" / "0001.jsonl.gz", "wt") as stream:
        for job_id, text in [(f"{BOARD}:1", "new"), (f"{BOARD}:2", " ")]:
            stream.write(json.dumps({"id": job_id, "description": text}) + "\n")
    wanted = {f"{BOARD}:1", f"{BOARD}:2"}
    assert (
        restate_run._descriptions(tmp_path, wanted)
        == {
            job_id: text
            for job_id, text in read_store(tmp_path / "greenhouse").items()
            if job_id in wanted
        }
        == {f"{BOARD}:1": "new"}
    )
