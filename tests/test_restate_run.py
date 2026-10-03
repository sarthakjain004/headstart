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


@pytest.mark.parametrize("tag", [None, "matching", "different"])
def test_replay_adopts_and_fills_cache_without_writing_inventory_input(
    tmp_path, monkeypatch, caplog, tag
):
    import pyarrow.parquet as pq

    facts = tmp_path / "facts"
    titles = ["Backend Engineer", "QA Lead", "Platform Engineer"]
    _record(facts, RUNS[0], [(f"{BOARD}:{i}", title) for i, title in enumerate(titles)])
    _describe(
        tmp_path / "descriptions",
        {f"{BOARD}:{i}": "Build and maintain software services." for i in range(3)},
    )
    families, head_dir, original_cache = _config(tmp_path)
    fingerprint = rfc.Head(head_dir).inputs_fingerprint
    if tag is not None:
        loaded = rfc.load_cache(original_cache, 7)
        rfc.save_cache(
            original_cache,
            loaded,
            inputs_fingerprint=fingerprint if tag == "matching" else tag,
        )
    state = tmp_path / "data/state"
    state.mkdir(parents=True)
    cache = original_cache.replace(state / "role_title_families.parquet")
    before = cache.read_bytes()
    encoded = []

    def encode(missing, model, revision):
        encoded.extend(missing)
        return np.tile(np.array([10.0, 0, 0], np.float32), (len(missing), 1))

    monkeypatch.setattr(rfc, "encode", encode)
    monkeypatch.setattr(restate_run, "is_english", lambda title, text: True)
    out = tmp_path / "runner/replay"
    out.mkdir(parents=True)
    (out / "stale-marker").write_text("old output")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "restate",
            "--facts",
            str(facts),
            "--ledger",
            str(tmp_path / "absent-ledger"),
            "--board-failures",
            str(tmp_path / "absent-failures"),
            "--classifier",
            str(head_dir),
            "--families",
            str(families),
            "--title-cache",
            str(cache),
            "--descriptions",
            str(tmp_path / "descriptions"),
            "--db",
            str(tmp_path / "absent-db"),
            "--out",
            str(out),
            "--encode-budget-seconds",
            "30",
        ],
    )
    with caplog.at_level("INFO"):
        assert restate_run.main() == 0
    assert encoded == (
        sorted(rfc.normalise(t) for t in titles)
        if tag == "different"
        else ["platform engineer"]
    )
    assert cache.read_bytes() == before
    scratch = out.with_name(out.name + "-title-cache.parquet")
    assert scratch.is_file() and scratch.parent != state
    assert not (out / "stale-marker").exists()
    metadata = pq.read_schema(scratch).metadata
    assert metadata[b"inputs_fingerprint"].decode() == fingerprint
    assert set(rfc.load_cache(scratch, 7).title_logits) == {
        rfc.normalise(t) for t in titles
    }
    if tag is None:
        assert "legacy" in caplog.text and "in-memory" in caplog.text
    if tag == "different":
        assert "different mathematical inputs" in caplog.text


def test_a_future_requisition_does_not_deduplicate_baseline_jobs(tmp_path, monkeypatch):
    import pyarrow as pa
    import pyarrow.parquet as pq

    front = "eightfold:jobs.acme.com"
    backing = "taleo_enterprise:https://acme.taleo.net/careersection/external"
    front_id, backing_id = front + ":123", backing + ":42"
    facts = tmp_path / "facts"
    for stamp, req in zip(RUNS[:2], (None, "R123"), strict=True):
        lines = jf.ScrapedLines(facts / jf.SCRAPED_LINES)
        for board, job_id, requisition in (
            (front, front_id, "R123"),
            (backing, backing_id, req),
        ):
            lines.see(
                board,
                {
                    "id": job_id,
                    "title": "Backend Engineer",
                    "department": "Engineering",
                    "requisition": requisition,
                },
            )
        scope = jf.RunScope(
            authoritative=frozenset({lower_key(front), lower_key(backing)}),
            keep_set=None,
            live={},
        )
        reads = jf.board_reads(
            [ShardReport(boards_ok=[front, backing])], lines.board_lines, scope
        )
        jf.record_run(lines.close(), facts, stamp, reads, scope)
        (facts / jf.SCRAPED_LINES).unlink(missing_ok=True)
    baseline_stamp = "2026-09-01T00:30:00+00:00"
    baseline = tmp_path / "baseline.parquet"
    rows = [
        {
            "id": job_id,
            "kind": "present",
            "title": "Backend Engineer",
            "department": "Engineering",
            "description": "You will build software services.",
            "vector": [0.0, 0.0],
            "reference_board": board,
            "requisition": req,
        }
        for board, job_id, req in (
            (front, front_id, "R123"),
            (backing, backing_id, None),
        )
    ]
    pq.write_table(
        pa.Table.from_pylist(rows).replace_schema_metadata(
            {b"baseline": b"true", b"ts": baseline_stamp.encode()}
        ),
        baseline,
    )
    families, head, cache = _config(tmp_path)
    out = tmp_path / "restated"
    monkeypatch.setattr(restate_run, "live_keep_set", lambda _: {front, backing})
    monkeypatch.setattr(
        restate_run.eightfold_backing, "load", lambda: {"jobs.acme.com": (backing,)}
    )
    monkeypatch.setattr(restate_run, "is_english", lambda title, text: True)
    monkeypatch.setattr(
        sys,
        "argv",
        [
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
            str(out),
            "--encode-budget-seconds",
            "0",
        ],
    )
    assert restate_run.main() == 0
    files = sorted((out / trend_history.DELTAS).glob("*.parquet"))
    stock_deltas = [
        sum(r["delta"] for r in pq.read_table(p).to_pylist() if r["metric"] == "stock")
        for p in files
    ]
    assert stock_deltas == [2, -1], (
        "later requisition knowledge must not remove a baseline job"
    )


@pytest.mark.parametrize("future_changed", [False, True])
def test_complete_baseline_keeps_a_served_job_absent_from_all_scrapes(
    tmp_path, monkeypatch, future_changed
):
    import pyarrow as pa
    import pyarrow.parquet as pq

    facts = tmp_path / "facts"
    for index, stamp in enumerate(RUNS[:2]):
        title = "QA Lead" if future_changed and index else "Backend Engineer"
        _record(facts, stamp, [(f"{BOARD}:1", title)])
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
            "vector": [n / 4, 0.0],
            "reference_board": BOARD,
            "first_seen": "2026-08-01T00:00:00+00:00"
            if n == 1
            else "2026-08-31T00:00:00+00:00",
        }
        for n in (1, 2)
    ]
    pq.write_table(
        pa.Table.from_pylist(rows).replace_schema_metadata(
            {b"baseline": b"true", b"ts": baseline_stamp.encode()}
        ),
        baseline,
    )
    if future_changed:
        observed = facts / "trend_reference" / "future.parquet"
        observed.parent.mkdir(parents=True)
        pq.write_table(
            pa.Table.from_pylist(
                [
                    rows[0]
                    | {
                        "title": "QA Lead",
                        "vector": [0.0, 0.0],
                        "description": text.replace("3 years", "8 years"),
                    }
                ]
            ).replace_schema_metadata(
                {
                    b"baseline": b"false",
                    b"ts": RUNS[1].encode(),
                    b"previous_tick": baseline_stamp.encode(),
                }
            ),
            observed,
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

    def latest_inputs(_path, wanted):
        assert wanted == set(), "immutable observations must block latest-store guesses"
        return {}

    monkeypatch.setattr(restate_run, "_descriptions", latest_inputs)
    monkeypatch.setattr(
        restate_run,
        "_vectors",
        lambda db, facts, wanted: {
            job_id: np.zeros(2, np.float32) for job_id in latest_inputs(db, wanted)
        },
    )
    observed_vectors = []
    original_logits = rfc.Head.row_logits

    def logits(head, matrix):
        observed_vectors.extend(matrix.tolist())
        return original_logits(head, matrix)

    monkeypatch.setattr(rfc.Head, "row_logits", logits)
    assert restate_run.main() == 0
    expected_vectors = [[0.25, 0.0], [0.5, 0.0]]
    if future_changed:
        expected_vectors.insert(1, [0.0, 0.0])
    assert observed_vectors == expected_vectors
    _, levels = trend_history.board_levels(tmp_path / "restated")
    assert sum(n for k, n in levels.items() if k[1] == "stock") == 2
    assert sum(n for k, n in levels.items() if k[1] == "new") == 1
    import pyarrow.parquet as pq

    first_tick = pq.read_table(
        min((tmp_path / "restated" / trend_history.DELTAS).glob("*.parquet"))
    ).to_pylist()
    assert sum(row["delta"] for row in first_tick if row["metric"] == "opened") == 0
    assert (
        sum(row["delta"] for row in first_tick if row["metric"] == "recounted_in") == 2
    )
    if future_changed:
        assert levels[(BOARD, "stock", "qa-test", "staff")] == 1
        assert levels[(BOARD, "stock", "software-engineering", "mid")] == 1


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


def test_baseline_does_not_revive_a_board_already_observed_dormant(
    tmp_path, monkeypatch
):
    import pyarrow as pa
    import pyarrow.parquet as pq

    facts = tmp_path / "facts"
    for stamp in RUNS[:2]:
        lines = jf.ScrapedLines(facts / jf.SCRAPED_LINES)
        lines.see(
            BOARD,
            {
                "id": f"{BOARD}:1",
                "title": "Backend Engineer",
                "department": "Engineering",
                "posted_at": "2020-01-01",
            },
        )
        scope = jf.RunScope(authoritative=frozenset({BOARD}), keep_set=None, live={})
        reads = jf.board_reads(
            [ShardReport(boards_ok=[BOARD])], lines.board_lines, scope
        )
        jf.record_run(lines.close(), facts, stamp, reads, scope)
    families, head, cache = _config(tmp_path)
    baseline = tmp_path / "baseline.parquet"
    schema = pa.schema(
        [
            ("kind", pa.string()),
            ("id", pa.string()),
            ("vector", pa.list_(pa.float16())),
            ("description", pa.string()),
            ("reference_board", pa.string()),
        ],
        metadata={b"baseline": b"true", b"ts": b"2026-09-02T00:00:00+00:00"},
    )
    pq.write_table(pa.Table.from_pylist([], schema=schema), baseline)
    _describe(
        tmp_path / "descriptions",
        {
            f"{BOARD}:1": "You will design and maintain software systems for our customers."
        },
    )
    args = [
        "restate",
        "--facts",
        str(facts),
        "--baseline",
        str(baseline),
        "--ledger",
        str(tmp_path / "no-ledger"),
        "--board-failures",
        str(tmp_path / "no-failures"),
        "--classifier",
        str(head),
        "--families",
        str(families),
        "--title-cache",
        str(cache),
        "--descriptions",
        str(tmp_path / "descriptions"),
        "--db",
        str(tmp_path / "no-db"),
        "--out",
        str(tmp_path / "restated"),
    ]
    monkeypatch.setattr(sys, "argv", args)
    assert restate_run.main() == 0
    _, levels = trend_history.board_levels(tmp_path / "restated")
    assert sum(n for key, n in levels.items() if key[1] == "stock") == 0
