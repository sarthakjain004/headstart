"""Tests for the jobs-table CLI (headstart.ingest.index) — the ``first_seen`` stamp (ADR-0031).

``first_seen`` records when *we* first indexed a Job, which `posted_at` cannot: that is the
company's posting date, and boards are scraped on a rotating schedule, so indexing can lag posting
by days. Two properties carry the feature. The stamp must be written exactly once, when the row
enters the table and never again — otherwise "new in the last 2 hours" drifts every run. And the
column must reach a table created before it existed, because `_schema()` only applies at
`create_table` and `apply_sync` rejects rows that don't match the table's frozen schema.
"""

from __future__ import annotations

import argparse
import gzip
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

pytest.importorskip("lancedb")  # [embed] extra — not installed in CI's quality job
pytest.importorskip("numpy")
pytest.importorskip("pyarrow")

import lancedb
import numpy as np

import headstart.ingest.index as idx

_DIM = 4


def _write_store(store: Path, ids: list[str]) -> None:
    """A committed embedding store: one meta row per id, row-aligned vectors, and a manifest."""
    store.mkdir(parents=True, exist_ok=True)
    (store / "meta.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "id": job_id,
                    "ats": job_id.split(":", 1)[0],
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
                    "department": None,
                    "url": f"https://example.test/{job_id}",
                    "posted_at": None,
                    # Planner-only state (ADR-0050): it belongs in the store's meta, never in the
                    # served table, whose schema would reject the unknown column.
                    "has_description": True,
                }
            )
            + "\n"
            for job_id in ids
        ),
        encoding="utf-8",
    )
    np.zeros((len(ids), _DIM), dtype="float32").tofile(store / "embeddings.f32")
    (store / "manifest.json").write_text(json.dumps({"dim": _DIM}), encoding="utf-8")


def _write_corpus(
    source: Path, ids: list[str], descriptions: dict[str, str] | None = None
) -> None:
    """The corpus rows sync reads — with, since ADR-0104, the description text
    `update_descriptions` filled into them, which is where the served column is sourced from."""
    source.mkdir(parents=True, exist_ok=True)
    (source / "greenhouse.jsonl").write_text(
        "".join(
            json.dumps({"id": job_id, "description": (descriptions or {}).get(job_id)})
            + "\n"
            for job_id in ids
        ),
        encoding="utf-8",
    )


def _upgrade_list(tmp_path: Path, ids: list[str] | None) -> Path:
    """The ADR-0050 upgrade list, always pinned into tmp_path — it defaults to the repo's real
    data/state/, and a test must never read or write there."""
    path = tmp_path / "pending_upgrades.txt"
    path.write_text("".join(f"{i}\n" for i in ids or []), encoding="utf-8")
    return path


def _sync(
    tmp_path: Path,
    monkeypatch,
    ids: list[str],
    upgrades: list[str] | None = None,
    meta_over: dict | None = None,
    descriptions: dict[str, str] | None = None,
    backfill: bool = False,
) -> int:
    """Run one `index sync` cycle over ``ids`` — store, corpus, and scrape scope all agree.

    ``meta_over`` restates every store row's metadata after it is written, which is how a run that
    follows an ``update_meta`` refresh looks to sync (ADR-0061).
    """
    store, source, db = tmp_path / "store", tmp_path / "corpus", tmp_path / "db"
    _write_store(store, ids)
    if meta_over:
        path = store / "meta.jsonl"
        rows = [
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        ]
        for row in rows:
            row.update(meta_over)
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    _write_corpus(source, ids, descriptions)
    monkeypatch.setattr(idx, "_STORE", store)
    # An empty ledger dir: no live Boards, so board resolution falls back to `board_of` — the
    # rule these tests were written against, and what a first run genuinely sees.
    ledger = tmp_path / "ledger"
    ledger.mkdir(exist_ok=True)
    return idx.sync(
        argparse.Namespace(
            source=str(source),
            scraped=str(source),
            db=str(db),
            ledger=str(ledger),
            # Pinned into tmp_path like every other output: it defaults to the repo's real
            # data/state/, and a test must never read or write there.
            upgrades=str(_upgrade_list(tmp_path, upgrades)),
            # Same reason, and absent on purpose: every Board's list is authoritative, so the
            # scope is the infer-from-lines one these tests were written against (ADR-0053).
            unauthoritative_boards=str(tmp_path / "unauthoritative_boards.json"),
            # Pinned into tmp_path for the same reason as `upgrades`. The grace period is left
            # ON so these tests exercise the real production path; the file starts absent, which
            # reads as an empty set — so a first absence is withheld here exactly as it would be
            # in a real cold start. Tests that need an eviction to land run sync twice.
            unconfirmed=str(tmp_path / "unconfirmed_ids.txt"),
            backfill_descriptions=backfill,
        )
    )


def _rows(tmp_path: Path) -> dict[str, str | None]:
    table = lancedb.connect(str(tmp_path / "db")).open_table(idx.PROD_TABLE)
    return {r["id"]: r["first_seen"] for r in table.search().limit(100).to_list()}


def test_sync_stamps_every_row_it_adds(tmp_path, monkeypatch):
    assert _sync(tmp_path, monkeypatch, ["greenhouse:a:1", "greenhouse:a:2"]) == 0
    stamps = _rows(tmp_path)
    assert set(stamps) == {"greenhouse:a:1", "greenhouse:a:2"}
    assert all(s and s.startswith("20") for s in stamps.values())
    assert len(set(stamps.values())) == 1  # one stamp per run, not per row


def test_sync_does_not_restamp_rows_it_already_holds(tmp_path, monkeypatch):
    """The load-bearing property: a row's stamp is its *first* sighting, so a later run must leave
    it alone even though that run re-syncs the same Board."""
    _sync(tmp_path, monkeypatch, ["greenhouse:a:1"])
    first = _rows(tmp_path)["greenhouse:a:1"]

    _sync(tmp_path, monkeypatch, ["greenhouse:a:1", "greenhouse:a:2"])
    after = _rows(tmp_path)

    assert after["greenhouse:a:1"] == first  # untouched
    assert after["greenhouse:a:2"] is not None  # the new arrival is stamped


class _PinnedClock:
    """Stands in for `datetime` inside index.py so a stamp is a fact, not a race with the clock."""

    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def now(self, tz=None) -> datetime:  # mirrors datetime.now(tz)
        return self.moment


def test_each_run_stamps_with_its_own_time(tmp_path, monkeypatch):
    """Two runs, two times: a later run's arrivals must sort above an earlier run's, which is what
    makes "new in the last 2 hours" mean anything. Pinned rather than slept, so it can't flake on
    the one-second resolution of the stamp."""
    monkeypatch.setattr(
        idx, "datetime", _PinnedClock(datetime(2026, 1, 1, 9, 0, tzinfo=UTC))
    )
    _sync(tmp_path, monkeypatch, ["greenhouse:a:1"])

    monkeypatch.setattr(
        idx, "datetime", _PinnedClock(datetime(2026, 1, 1, 11, 0, tzinfo=UTC))
    )
    _sync(tmp_path, monkeypatch, ["greenhouse:a:1", "greenhouse:a:2"])

    stamps = _rows(tmp_path)
    assert stamps["greenhouse:a:1"] == "2026-01-01T09:00:00+00:00"
    assert stamps["greenhouse:a:2"] == "2026-01-01T11:00:00+00:00"
    assert stamps["greenhouse:a:2"] > stamps["greenhouse:a:1"]


def test_new_table_carries_the_column(tmp_path, monkeypatch):
    _sync(tmp_path, monkeypatch, ["greenhouse:a:1"])
    table = lancedb.connect(str(tmp_path / "db")).open_table(idx.PROD_TABLE)
    assert "first_seen" in table.schema.names


def test_sync_adds_the_column_to_a_table_that_predates_it(tmp_path, monkeypatch):
    """The migration. A table built before `first_seen` existed keeps its frozen schema, and
    `apply_sync` rejects rows that don't match it — so sync must widen the table before writing.
    The pre-existing row keeps a null stamp: we genuinely don't know when we first saw it."""
    import pyarrow as pa

    old_schema = pa.schema(
        [f for f in idx._schema(_DIM) if f.name != "first_seen"]
    )  # the table as it was
    db = lancedb.connect(str(tmp_path / "db"))
    table = db.create_table(idx.PROD_TABLE, schema=old_schema)
    # On a Board this run does not scrape, so the sync leaves it alone (ADR-0014 only evicts
    # within scraped Boards) and we observe the migration rather than an eviction.
    table.add([{"id": "greenhouse:untouched:0", "vector": [0.0] * _DIM}])
    assert "first_seen" not in table.schema.names

    assert _sync(tmp_path, monkeypatch, ["greenhouse:a:1"]) == 0

    stamps = _rows(tmp_path)
    assert stamps["greenhouse:untouched:0"] is None  # migrated, first sighting unknown
    assert stamps["greenhouse:a:1"] is not None  # newly added row, stamped


def test_migration_is_idempotent(tmp_path, monkeypatch):
    """It runs on every sync forever, so the second call must be a no-op rather than an error."""
    assert _sync(tmp_path, monkeypatch, ["greenhouse:a:1"]) == 0
    assert _sync(tmp_path, monkeypatch, ["greenhouse:a:1", "greenhouse:a:2"]) == 0
    assert "first_seen" in (
        lancedb.connect(str(tmp_path / "db")).open_table(idx.PROD_TABLE).schema.names
    )


def test_log_ids_batches_and_labels(caplog):
    import logging

    caplog.set_level(logging.INFO, logger="headstart.ingest.index")
    idx._log_ids("evict", [f"lever:acme:{n}" for n in range(250)])

    lines = [r.getMessage() for r in caplog.records if r.message.startswith("evict")]
    assert len(lines) == 3  # 250 ids at 100/line
    assert lines[0].startswith("evict [1-100 of 250]: lever:acme:0 ")
    assert lines[2].startswith("evict [201-250 of 250]: ")
    assert lines[2].endswith("lever:acme:249")


def test_log_ids_silent_on_empty(caplog):
    import logging

    caplog.set_level(logging.INFO, logger="headstart.ingest.index")
    idx._log_ids("add", [])
    assert not caplog.records


def test_an_upgraded_row_keeps_its_original_first_seen(tmp_path, monkeypatch):
    """ADR-0050 re-embeds a vector built without a description, which means deleting the row so
    `add = fresh - index` can re-add it. The Job never left the corpus though — only its vector
    improved — so re-stamping it would surface an old listing as new. `first_seen` is served and
    filterable (`seen_within`, and the alerts watermark), so on the first run that would re-notify
    subscribers about tens of thousands of jobs they have already seen."""
    monkeypatch.setattr(
        idx, "datetime", _PinnedClock(datetime(2026, 1, 1, 9, 0, tzinfo=UTC))
    )
    _sync(tmp_path, monkeypatch, ["greenhouse:a:1", "greenhouse:a:2"])

    monkeypatch.setattr(
        idx, "datetime", _PinnedClock(datetime(2026, 1, 2, 9, 0, tzinfo=UTC))
    )
    _sync(
        tmp_path,
        monkeypatch,
        ["greenhouse:a:1", "greenhouse:a:2"],
        upgrades=["greenhouse:a:1"],
    )

    stamps = _rows(tmp_path)
    assert (
        stamps["greenhouse:a:1"] == "2026-01-01T09:00:00+00:00"
    )  # replaced, not re-dated
    assert stamps["greenhouse:a:2"] == "2026-01-01T09:00:00+00:00"  # untouched


def test_log_reasons_pairs_each_board_with_why_on_its_own_line(caplog):
    """The scope-exclusion warning names a count and the Boards, but the cause it hints at
    ("truncated, or the scrape raised") was never recorded per Board — so a Board excluded on
    every run for 19 runs read the same whether it was rate-limited or served a short page.
    One line each, because the reason is what a `grep` has to be able to land on."""
    caplog.set_level("INFO")
    idx._log_reasons(
        "scope-excluded Board",
        {
            "eightfold:caci.eightfold.ai": "truncated: 500 of 1200",
            "workday:x/Careers": "HTTPError: HTTP Error 429: ",
        },
    )
    lines = [r.getMessage() for r in caplog.records]
    assert lines == [
        "scope-excluded Board: eightfold:caci.eightfold.ai — truncated: 500 of 1200",
        "scope-excluded Board: workday:x/Careers — HTTPError: HTTP Error 429:",
    ]


def test_log_reasons_flattens_and_clips_so_one_board_stays_one_line(caplog):
    """A reason carries arbitrary scraper text. A newline would split one Board across two lines
    and break the grep contract; an unbounded traceback would bury the rest of the log."""
    caplog.set_level("INFO")
    idx._log_reasons(
        "scope-excluded Board",
        {"greenhouse:a": "boom\n  File 'x.py', line 1\n" + "y" * 400, "lever:b": "  "},
    )
    lines = [r.getMessage() for r in caplog.records]
    assert len(lines) == 2
    assert "\n" not in lines[0]
    assert lines[0].endswith("…")
    assert len(lines[0]) < 260
    assert lines[1] == "scope-excluded Board: lever:b — no reason recorded"


# --- ADR-0061: refreshed store metadata reaches rows already indexed -----------------------------


def test_sync_pushes_refreshed_metadata_into_rows_it_already_holds(
    tmp_path, monkeypatch
):
    """The propagation half of ADR-0061. Without it, `update_meta`'s corrections sit in the store
    unread, because `plan_sync` only ever adds ids the table lacks."""
    ids = ["greenhouse:a:1"]
    _sync(tmp_path, monkeypatch, ids)
    first = _rows(tmp_path)["greenhouse:a:1"]

    # The store now carries a corrected floor and an edited salary, as update_meta would leave
    # them. The corpus is unchanged, so plan_sync has nothing to add.
    _sync(
        tmp_path,
        monkeypatch,
        ids,
        meta_over={"min_years": 3, "experience_source": "regex", "salary": "EUR 90k"},
    )

    table = lancedb.connect(str(tmp_path / "db")).open_table(idx.PROD_TABLE)
    rows = table.search().limit(10).to_list()
    assert len(rows) == 1  # a refresh replaces a row, it does not duplicate it
    assert (rows[0]["min_years"], rows[0]["experience_source"]) == (3, "regex")
    assert rows[0]["salary"] == "EUR 90k"
    # first_seen must survive: re-stamping would resurface every refreshed Job to the alerts
    # watermark as a brand-new listing (ADR-0031).
    assert rows[0]["first_seen"] == first


def test_sync_refresh_writes_nothing_when_the_table_already_matches(
    tmp_path, monkeypatch, caplog
):
    ids = ["greenhouse:a:1", "greenhouse:a:2"]
    _sync(tmp_path, monkeypatch, ids)
    caplog.set_level("INFO")
    _sync(tmp_path, monkeypatch, ids)
    assert any("already matches the store" in r.getMessage() for r in caplog.records)


# ---- the description column (ADR-0104) ----


def _descriptions(tmp_path: Path) -> dict[str, str | None]:
    table = lancedb.connect(str(tmp_path / "db")).open_table(idx.PROD_TABLE)
    return {r["id"]: r["description"] for r in table.search().limit(100).to_list()}


def test_an_added_row_carries_its_corpus_description(tmp_path, monkeypatch):
    _sync(
        tmp_path,
        monkeypatch,
        ["greenhouse:a:1", "greenhouse:a:2"],
        descriptions={"greenhouse:a:1": "We run Kubernetes on AWS."},
    )
    got = _descriptions(tmp_path)
    assert got["greenhouse:a:1"] == "We run Kubernetes on AWS."
    assert got["greenhouse:a:2"] is None  # no text in the corpus -> null, never ""


def test_sync_adds_the_description_column_to_a_table_that_predates_it(
    tmp_path, monkeypatch
):
    _sync(tmp_path, monkeypatch, ["greenhouse:a:1"])
    db = lancedb.connect(str(tmp_path / "db"))
    table = db.open_table(idx.PROD_TABLE)
    table.drop_columns(["description"])  # a table frozen before ADR-0104
    assert "description" not in db.open_table(idx.PROD_TABLE).schema.names
    assert (
        _sync(
            tmp_path,
            monkeypatch,
            ["greenhouse:a:1", "greenhouse:a:2"],
            descriptions={"greenhouse:a:2": "Rust services."},
        )
        == 0
    )
    got = _descriptions(tmp_path)
    assert got["greenhouse:a:1"] is None  # pre-existing row: null, honest
    assert got["greenhouse:a:2"] == "Rust services."


def test_refresh_carries_the_description_across_and_never_clobbers_it(
    tmp_path, monkeypatch
):
    """The trap the ADR names: meta holds no text, so a compared column would read stale on every
    row and be rewritten to null each run. A metadata refresh must keep what the row had."""
    ids = ["greenhouse:a:1"]
    _sync(tmp_path, monkeypatch, ids, descriptions={"greenhouse:a:1": "Go and gRPC."})
    # The store's meta moves (an update_meta correction); the corpus this run carries NO text.
    _sync(tmp_path, monkeypatch, ids, meta_over={"min_years": 3})
    assert _descriptions(tmp_path)["greenhouse:a:1"] == "Go and gRPC."


def test_refresh_fills_a_null_description_on_a_row_it_rewrites_anyway(
    tmp_path, monkeypatch
):
    ids = ["greenhouse:a:1"]
    _sync(tmp_path, monkeypatch, ids)  # indexed without text
    assert _descriptions(tmp_path)["greenhouse:a:1"] is None
    # Meta moved AND the corpus now carries text: the rewrite that was happening anyway fills it.
    _sync(
        tmp_path,
        monkeypatch,
        ids,
        meta_over={"min_years": 3},
        descriptions={"greenhouse:a:1": "Now with a description."},
    )
    assert _descriptions(tmp_path)["greenhouse:a:1"] == "Now with a description."


def test_a_null_description_alone_is_not_a_reason_to_rewrite_unless_backfilling(
    tmp_path, monkeypatch, caplog
):
    ids = ["greenhouse:a:1"]
    _sync(tmp_path, monkeypatch, ids)
    caplog.set_level("INFO")
    # Text available, meta unchanged, backfill OFF: nothing is rewritten — the +690 MB is a
    # deliberate step, not a side effect of an ordinary run.
    _sync(tmp_path, monkeypatch, ids, descriptions={"greenhouse:a:1": "text"})
    assert any("already matches the store" in r.getMessage() for r in caplog.records)
    assert _descriptions(tmp_path)["greenhouse:a:1"] is None
    # Backfill ON: filled, and the log says how many rows were rewritten for that reason.
    caplog.clear()
    _sync(
        tmp_path,
        monkeypatch,
        ids,
        descriptions={"greenhouse:a:1": "text"},
        backfill=True,
    )
    assert _descriptions(tmp_path)["greenhouse:a:1"] == "text"
    assert any(
        "1 of them to backfill a description" in r.getMessage() for r in caplog.records
    )


def test_backfill_leaves_a_row_the_corpus_cannot_fill_untouched(
    tmp_path, monkeypatch, caplog
):
    """The merge job's corpus is this run's SLICE. A backfill candidate whose text is not in it must
    not be rewritten — that would pay the ~25 KB vector rewrite and fill nothing, and one flagged
    run would churn every null-description row table-wide. It waits, and the log says so."""
    ids = ["greenhouse:a:1", "greenhouse:a:2"]
    _sync(
        tmp_path, monkeypatch, ids
    )  # both indexed without text; meta says both HAVE one
    caplog.set_level("INFO")
    # Backfill ON, but the corpus carries text for :2 only.
    _sync(
        tmp_path,
        monkeypatch,
        ids,
        descriptions={"greenhouse:a:2": "text"},
        backfill=True,
    )
    got = _descriptions(tmp_path)
    assert got["greenhouse:a:2"] == "text"
    assert got["greenhouse:a:1"] is None  # left alone, not rewritten to null
    msgs = [r.getMessage() for r in caplog.records]
    assert any("rewrote 1 rows" in m and "1 of them to backfill" in m for m in msgs)
    assert any("1 backfill candidate(s) left for a later run" in m for m in msgs)


def test_the_grace_period_round_trips_across_two_runs(tmp_path, monkeypatch):
    """End-to-end at the `sync` seam (ADR-0083): a posting that vanishes from one scrape survives
    that run and is only evicted if it is still absent from the next one. The planner tests pin
    the decision; this pins the file actually being written and read back.
    """
    _sync(tmp_path, monkeypatch, ["greenhouse:a:1", "greenhouse:a:2"])
    assert set(_rows(tmp_path)) == {"greenhouse:a:1", "greenhouse:a:2"}

    # Run 2: :2 is absent from the scrape — a first absence, so it must survive.
    _sync(tmp_path, monkeypatch, ["greenhouse:a:1"])
    assert set(_rows(tmp_path)) == {
        "greenhouse:a:1",
        "greenhouse:a:2",
    }, "a single absence must not evict"
    owed = (tmp_path / "unconfirmed_ids.txt").read_text().split()
    assert owed == ["greenhouse:a:2"], "and it is recorded as owing a second look"

    # Run 3: still absent — now it goes.
    _sync(tmp_path, monkeypatch, ["greenhouse:a:1"])
    assert set(_rows(tmp_path)) == {"greenhouse:a:1"}
    assert (tmp_path / "unconfirmed_ids.txt").read_text().split() == []


def test_a_posting_that_reappears_is_never_evicted(tmp_path, monkeypatch):
    """The measured false-eviction shape: absent once, back the next scrape. Under the old
    evict-on-first-absence rule this lost a live posting every time it happened."""
    _sync(tmp_path, monkeypatch, ["greenhouse:a:1", "greenhouse:a:2"])
    _sync(tmp_path, monkeypatch, ["greenhouse:a:1"])  # a short scrape
    _sync(tmp_path, monkeypatch, ["greenhouse:a:1", "greenhouse:a:2"])  # it is back

    assert set(_rows(tmp_path)) == {"greenhouse:a:1", "greenhouse:a:2"}
    assert (tmp_path / "unconfirmed_ids.txt").read_text().split() == []


# ---- `index backfill-descriptions` (ADR-0104): the whole-table pass `sync` cannot do ----


def _write_description_store(root: Path, texts: dict[str, str]) -> Path:
    """The ADR-0050 store as `read_store` reads it: one gzipped fragment per ATS directory."""
    for job_id, text in texts.items():
        ats_dir = root / job_id.split(":", 1)[0]
        ats_dir.mkdir(parents=True, exist_ok=True)
        with gzip.open(ats_dir / "base.jsonl.gz", "at", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": job_id, "description": text}) + "\n")
    return root


def _backfill(tmp_path: Path, store: Path, apply: bool = True) -> int:
    return idx.backfill_descriptions(
        argparse.Namespace(
            db=str(tmp_path / "db"), store=str(store), chunk=2048, apply=apply
        )
    )


def test_backfill_reaches_a_row_no_run_corpus_ever_carried(tmp_path, monkeypatch):
    """The reason this subcommand exists. `sync --backfill-descriptions` reads the run's corpus,
    so a row whose Board sat out the slice is unreachable; the store holds it regardless."""
    _sync(tmp_path, monkeypatch, ["greenhouse:a:1", "greenhouse:a:2"])
    assert _descriptions(tmp_path) == {"greenhouse:a:1": None, "greenhouse:a:2": None}

    store = _write_description_store(
        tmp_path / "descriptions",
        {"greenhouse:a:1": "Rust services.", "greenhouse:a:2": "Go and gRPC."},
    )
    assert _backfill(tmp_path, store) == 0
    assert _descriptions(tmp_path) == {
        "greenhouse:a:1": "Rust services.",
        "greenhouse:a:2": "Go and gRPC.",
    }


def test_backfill_writes_nothing_without_apply(tmp_path, monkeypatch):
    _sync(tmp_path, monkeypatch, ["greenhouse:a:1"])
    store = _write_description_store(
        tmp_path / "descriptions", {"greenhouse:a:1": "Rust services."}
    )
    assert _backfill(tmp_path, store, apply=False) == 0
    assert _descriptions(tmp_path) == {"greenhouse:a:1": None}


def test_backfill_leaves_a_row_the_store_has_no_text_for(tmp_path, monkeypatch):
    _sync(tmp_path, monkeypatch, ["greenhouse:a:1", "greenhouse:a:2"])
    store = _write_description_store(
        tmp_path / "descriptions", {"greenhouse:a:1": "Rust services."}
    )
    _backfill(tmp_path, store)
    got = _descriptions(tmp_path)
    assert got["greenhouse:a:1"] == "Rust services."
    assert got["greenhouse:a:2"] is None  # null, not an empty string


def test_backfill_never_touches_a_description_the_row_already_had(
    tmp_path, monkeypatch
):
    _sync(
        tmp_path,
        monkeypatch,
        ["greenhouse:a:1"],
        descriptions={"greenhouse:a:1": "What the scrape found."},
    )
    store = _write_description_store(
        tmp_path / "descriptions", {"greenhouse:a:1": "Something staler."}
    )
    _backfill(tmp_path, store)
    assert _descriptions(tmp_path)["greenhouse:a:1"] == "What the scrape found."


def test_backfill_preserves_every_other_column_of_a_rewritten_row(
    tmp_path, monkeypatch
):
    """The rewrite is delete-then-add, so a dropped column would silently destroy served data.
    The vector matters most: it is taken from the row itself, not re-embedded."""
    _sync(tmp_path, monkeypatch, ["greenhouse:a:1"])
    table = lancedb.connect(str(tmp_path / "db")).open_table(idx.PROD_TABLE)
    before = table.search().limit(10).to_list()[0]

    store = _write_description_store(
        tmp_path / "descriptions", {"greenhouse:a:1": "Rust services."}
    )
    _backfill(tmp_path, store)

    after = (
        lancedb.connect(str(tmp_path / "db"))
        .open_table(idx.PROD_TABLE)
        .search()
        .limit(10)
        .to_list()[0]
    )
    assert after["description"] == "Rust services."
    assert list(after["vector"]) == list(before["vector"])
    for column in set(before) - {"description", "vector"}:
        assert after[column] == before[column], column


def test_backfill_refuses_a_table_that_predates_the_column(tmp_path, monkeypatch):
    """Fail loudly rather than silently backfilling nothing: the migration runs in `sync`."""
    _sync(tmp_path, monkeypatch, ["greenhouse:a:1"])
    lancedb.connect(str(tmp_path / "db")).open_table(idx.PROD_TABLE).drop_columns(
        ["description"]
    )
    with pytest.raises(SystemExit):
        _backfill(tmp_path, tmp_path / "descriptions")
