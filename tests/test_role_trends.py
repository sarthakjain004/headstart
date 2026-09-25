"""Tests for the Trends tick step (headstart.ingest.role_trends, ADR-0040/ADR-0051/ADR-0230).

Contracts: served rows are counted into (metric, family, band) groups with non-tech held
apart; `new` counts only rows first seen inside the flow window; watched roles are counted by
title in addition to their family; each run records one tick of the history, and the history
accumulates tick over tick; and every degenerate input (missing classifier head, missing family
list, empty table) exits without writing garbage — trends must never sink a run that already
scraped and embedded, nor silently look healthy while accruing nothing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("lancedb")  # [embed] extra — not installed in CI's quality job
np = pytest.importorskip("numpy")
pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

from datetime import UTC

import lancedb

from headstart import roles, trend_history
from headstart.embedding_conventions import MODEL as EMBED_MODEL
from headstart.embedding_conventions import PROD_TABLE
from headstart.ingest import RUN_TS_ENV, index_plan, role_family_classifier, role_trends
from headstart.ingest.doc_prep import DERIVATIONS_VERSION
from headstart.jobs import tech_filter
from headstart.trend_history import TrendHistory

_DIM = 4


def _table(db_dir: Path, rows: list[dict]) -> None:
    fields = [
        pa.field("id", pa.string()),
        pa.field("title", pa.string()),
        pa.field("employment_type", pa.string()),
        pa.field("min_years", pa.int32()),
        pa.field("vector", pa.list_(pa.float32(), _DIM)),
        pa.field("ats", pa.string()),
    ]
    if any("first_seen" in r for r in rows):
        fields.append(pa.field("first_seen", pa.string()))
    schema = pa.schema(fields)
    # Every served row carries an ats; tests that don't care which get one shared default so
    # their (family, band) assertions still map to exactly one row (ADR-0075).
    rows = [{"ats": "greenhouse", **r} for r in rows]
    # A served id names its Board (`{ats}:{board}:{native}`), whose prefix is the row's ATS; a
    # test that names a bare id gets one Board of its row's ATS (ADR-0230 reads the ATS off it).
    rows = [
        {**r, "id": r["id"] if ":" in r["id"] else f"{r['ats']}:tests:{r['id']}"}
        for r in rows
    ]
    for r in rows:
        if r.get("vector") is not None and r.get("title"):
            family = _HEAD_FAMILIES[int(np.argmax(r["vector"][: len(_HEAD_FAMILIES)]))]
            _FAMILY_OF_TITLE[role_family_classifier.normalise(r["title"])] = family
    lancedb.connect(db_dir).create_table(
        PROD_TABLE, pa.Table.from_pylist(rows, schema=schema)
    )


# The classifier head the tests run (ADR-0220): three trained families, confident on a one-hot
# title vector, with a row part that reads nothing unless a test gives it weights (ADR-0224).
# Tests still state each row's family through its `vector`, as they did when a nearest centroid
# decided it: `_table` records that choice against the row's title, and the stub encoder below
# hands the head the matching one-hot vector.
_HEAD_FAMILIES = ("software-engineering", "ai-ml-data-science", roles.NON_TECH)
_FAMILY_OF_TITLE: dict[str, str] = {}
_AMBIGUOUS = "ambiguous"  # a title the head cannot place: it lands in unclassified-tech


@pytest.fixture(autouse=True)
def _stub_title_encoder(monkeypatch):
    """JobBERT is never downloaded in tests: a title encodes to its family's one-hot vector."""
    _FAMILY_OF_TITLE.clear()

    def encode(titles, model, revision):
        rows = []
        for title in titles:
            family = _FAMILY_OF_TITLE.get(title, "software-engineering")
            if family == _AMBIGUOUS:
                rows.append([1.0] * len(_HEAD_FAMILIES))
            else:
                rows.append([float(family == f) for f in _HEAD_FAMILIES])
        return np.array(rows, dtype=np.float32)

    monkeypatch.setattr(role_family_classifier, "encode", encode)


def _taxonomy(
    head: Path,
    families_path: Path,
    extra_families: tuple[str, ...] = (),
    row_weights: np.ndarray | None = None,
    row_model: str = EMBED_MODEL,
    row_dim: int = _DIM,
) -> None:
    """The head (cutoff 0.6, so a one-hot title is placed and an ambiguous one is not) and the
    curated family list it must agree with. The row part is all zeros unless ``row_weights``."""
    head.mkdir(parents=True, exist_ok=True)
    np.savez(
        head / "head.npz",
        title_weights=np.eye(len(_HEAD_FAMILIES), dtype=np.float32) * 10,
        row_weights=(
            np.zeros((len(_HEAD_FAMILIES), row_dim), dtype=np.float32)
            if row_weights is None
            else row_weights
        ),
        bias=np.zeros(len(_HEAD_FAMILIES), dtype=np.float32),
    )
    (head / "manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "model": "stub",
                "model_revision": "stub",
                "row_vector": {"model": row_model, "dim": row_dim},
                "families": list(_HEAD_FAMILIES),
                "cutoff": 0.6,
            }
        ),
        encoding="utf-8",
    )
    families_path.parent.mkdir(parents=True, exist_ok=True)
    listed = [
        "software-engineering",
        "ai-ml-data-science",
        *extra_families,
        "unclassified-tech",
    ]
    families_path.write_text(
        json.dumps({"families": [{"name": name} for name in listed]}), encoding="utf-8"
    )


def _rows(state: Path) -> list[dict]:
    """The index-wide counts at every tick the history under ``state`` holds, one row a group,
    as the aggregate ledger held them before ADR-0230."""
    history = TrendHistory.load(state, Path(__file__).parent / "no-trends-config")
    return [
        {
            "ts": ts,
            "metric": metric,
            "family": family,
            "band": band,
            "ats": ats,
            "count": n,
        }
        for ts in history.ticks
        for (metric, family, band, ats), n in history.index_counts(ts).items()
    ]


def _run(tmp_path: Path, monkeypatch, expect: int = 0) -> Path:
    state = tmp_path / "state"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "role_trends",
            "--db",
            str(tmp_path / "db"),
            "--classifier",
            str(tmp_path / "head"),
            "--title-cache",
            str(tmp_path / "title_cache.parquet"),
            "--families",
            str(tmp_path / "families.json"),
            "--state",
            str(state),
            "--board-ledger",
            str(tmp_path / "liveness"),
            # Pinned into tmp_path: it defaults to the repo's real config/role_watchlist.json,
            # and these tests must control exactly which roles are watched.
            "--watchlist",
            str(tmp_path / "watchlist.json"),
            # Pinned too (ADR-0057): these default to the repo's real data/state files, and a
            # test run must not write the production snapshot or append to its ledger.
            "--assignments",
            str(tmp_path / "role_assignments.parquet"),
            "--reassignments",
            str(tmp_path / "role_reassignments.csv"),
            # Pinned too (ADR-0227): these default to the real eviction queue and scrape outcome.
            "--eviction-queue",
            str(tmp_path / "eviction_queue.tsv"),
            "--unauthoritative-boards",
            str(tmp_path / "unauthoritative_boards.json"),
        ],
    )
    assert role_trends.main() == expect
    return state


def test_counts_rows_by_family_and_band_and_isolates_non_tech(tmp_path, monkeypatch):
    _taxonomy(tmp_path / "head", tmp_path / "families.json")
    x = [1.0, 0.0, 0.0, 0.0]  # -> software-engineering
    y = [0.0, 1.0, 0.0, 0.0]  # -> ai-ml-data-science
    z = [0.0, 0.0, 1.0, 0.0]  # -> non-tech
    _table(
        tmp_path / "db",
        [
            {
                "id": "a",
                "title": "Backend Dev",
                "employment_type": "full_time",
                "min_years": 5,
                "vector": x,
            },
            {
                "id": "b",
                "title": "Backend Dev",
                "employment_type": None,
                "min_years": 6,
                "vector": x,
            },
            {
                "id": "c",
                "title": "Data Sci Intern",
                "employment_type": None,
                "min_years": None,
                "vector": y,
            },
            {
                "id": "d",
                "title": "Data Entry Clerk",
                "employment_type": None,
                "min_years": 2,
                "vector": z,
            },
        ],
    )
    ledger = _run(tmp_path, monkeypatch)

    rows = {(r["family"], r["band"]): r["count"] for r in _rows(ledger)}
    assert rows[("software-engineering", "senior")] == 2  # 5 and 6 years band together
    assert rows[("ai-ml-data-science", "intern")] == 1
    # the non-tech row is the diagnostic: one unbanded number, never a chart series
    assert rows[("non-tech", "all")] == 1
    assert ("ai-ml-data-science", "mid") not in rows  # only non-empty groups


def test_a_tick_is_stamped_with_the_run_stamp_prune_used(tmp_path, monkeypatch):
    """ADR-0210: the dedup eviction ledger `index prune` writes and this ledger carry the same
    `ts`, so Trends joins a removal to the tick it happened in."""
    from headstart.ingest import RUN_TS_ENV

    _taxonomy(tmp_path / "head", tmp_path / "families.json")
    _table(
        tmp_path / "db",
        [
            {
                "id": "a",
                "title": "Backend Dev",
                "employment_type": None,
                "min_years": 5,
                "vector": [1.0, 0.0, 0.0, 0.0],
            }
        ],
    )
    monkeypatch.setenv(RUN_TS_ENV, "2026-09-25T06:00:00+00:00")
    state = _run(tmp_path, monkeypatch)
    assert {r["ts"] for r in _rows(state)} == {"2026-09-25T06:00:00+00:00"}


def test_ats_becomes_its_own_column_and_splits_same_family_band_rows(
    tmp_path, monkeypatch
):
    """ADR-0075: two rows in the same (family, band) but different ats each get their own
    ledger row, and the non-tech diagnostic is always ats='all' — never split, same as it's
    never banded."""
    _taxonomy(tmp_path / "head", tmp_path / "families.json")
    x = [1.0, 0.0, 0.0, 0.0]  # -> software-engineering
    z = [0.0, 0.0, 1.0, 0.0]  # -> NON-TECH
    _table(
        tmp_path / "db",
        [
            {
                "id": "a",
                "title": "Backend Dev",
                "employment_type": "full_time",
                "min_years": 5,
                "vector": x,
                "ats": "greenhouse",
            },
            {
                "id": "b",
                "title": "Backend Dev",
                "employment_type": "full_time",
                "min_years": 5,
                "vector": x,
                "ats": "lever",
            },
            {
                "id": "c",
                "title": "Data Entry Clerk",
                "employment_type": None,
                "min_years": 2,
                "vector": z,
                "ats": "lever",
            },
        ],
    )
    ledger = _run(tmp_path, monkeypatch)

    rows = {(r["family"], r["band"], r["ats"]): r["count"] for r in _rows(ledger)}
    assert rows[("software-engineering", "senior", "greenhouse")] == 1
    assert rows[("software-engineering", "senior", "lever")] == 1
    assert rows[("non-tech", "all", "all")] == 1  # never split by ats


def test_ledger_accumulates_rows_across_runs(tmp_path, monkeypatch):
    _taxonomy(tmp_path / "head", tmp_path / "families.json")
    _table(
        tmp_path / "db",
        [
            {
                "id": "a",
                "title": "Dev",
                "employment_type": None,
                "min_years": 3,
                "vector": [1.0, 0.0, 0.0, 0.0],
            }
        ],
    )
    monkeypatch.setenv(RUN_TS_ENV, "2026-09-25T05:00:00+00:00")
    ledger = _run(tmp_path, monkeypatch)
    monkeypatch.setenv(RUN_TS_ENV, "2026-09-25T06:00:00+00:00")
    _run(tmp_path, monkeypatch)  # second run appends

    rows = _rows(ledger)
    # The second run's tick is ADDED to the first's rather than replacing it.
    assert len(rows) == 4  # (one stock group + the non-tech diagnostic) per tick
    assert {r["ts"] for r in rows} == {
        "2026-09-25T05:00:00+00:00",
        "2026-09-25T06:00:00+00:00",
    }


def test_missing_classifier_head_degrades_to_noop(tmp_path, monkeypatch, caplog):
    import logging

    caplog.set_level(logging.WARNING, logger="headstart.ingest.role_trends")
    ledger = _run(tmp_path, monkeypatch)  # no _taxonomy(), no table — must not matter
    assert not ledger.exists()
    assert any("skipping trends" in r.getMessage() for r in caplog.records)


def test_missing_family_list_degrades_to_noop(tmp_path, monkeypatch, caplog):
    """The workflow step is continue-on-error, which would turn an unguarded FileNotFoundError
    into a green run that never accrues a row."""
    import logging

    _taxonomy(tmp_path / "head", tmp_path / "families.json")
    (tmp_path / "families.json").unlink()
    _table(
        tmp_path / "db",
        [
            {
                "id": "a",
                "title": "Dev",
                "employment_type": None,
                "min_years": 3,
                "vector": [1.0, 0.0, 0.0, 0.0],
            }
        ],
    )
    caplog.set_level(logging.WARNING, logger="headstart.ingest.role_trends")
    ledger = _run(tmp_path, monkeypatch)
    assert not ledger.exists()
    assert any("families.json" in r.getMessage() for r in caplog.records)


def test_empty_served_table_degrades_to_noop(tmp_path, monkeypatch, caplog):
    import logging

    _taxonomy(tmp_path / "head", tmp_path / "families.json")
    _table(tmp_path / "db", [])
    caplog.set_level(logging.WARNING, logger="headstart.ingest.role_trends")
    ledger = _run(tmp_path, monkeypatch)
    assert not ledger.exists()
    assert any("is empty" in r.getMessage() for r in caplog.records)


def test_a_head_deciding_an_unlisted_family_errors_visibly_instead_of_silently(
    tmp_path, monkeypatch, caplog
):
    """A head trained for families the curated list no longer names (ADR-0220). The workflow
    step is continue-on-error, so an unguarded ValueError would crash into a green run with no
    annotation — it must surface as ERROR (an ::error:: under Actions) and exit non-zero."""
    import logging

    _taxonomy(tmp_path / "head", tmp_path / "families.json")
    (tmp_path / "families.json").write_text(
        json.dumps(
            {
                "families": [
                    {"name": "software-engineering"},
                    {"name": "unclassified-tech"},
                ]
            }
        ),
        encoding="utf-8",
    )
    _table(
        tmp_path / "db",
        [
            {
                "id": "a",
                "title": "Dev",
                "employment_type": None,
                "min_years": 3,
                "vector": [1.0, 0.0, 0.0, 0.0],
            }
        ],
    )
    ledger = tmp_path / "state"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "role_trends",
            "--db",
            str(tmp_path / "db"),
            "--classifier",
            str(tmp_path / "head"),
            "--title-cache",
            str(tmp_path / "title_cache.parquet"),
            "--families",
            str(tmp_path / "families.json"),
            "--state",
            str(ledger),
            # Pinned even though this path errors before writing (ADR-0057): the isolation
            # must not depend on the error path staying an error path.
            "--assignments",
            str(tmp_path / "role_assignments.parquet"),
            "--reassignments",
            str(tmp_path / "role_reassignments.csv"),
        ],
    )
    caplog.set_level(logging.ERROR, logger="headstart.ingest.role_trends")
    assert role_trends.main() == 1
    assert not ledger.exists()
    assert any("taxonomy unusable" in r.getMessage() for r in caplog.records)


def test_half_landed_classifier_head_degrades_to_noop(tmp_path, monkeypatch, caplog):
    # manifest without weights: Head() would crash on the missing file
    import logging

    _taxonomy(tmp_path / "head", tmp_path / "families.json")
    (tmp_path / "head" / "head.npz").unlink()
    caplog.set_level(logging.WARNING, logger="headstart.ingest.role_trends")
    ledger = _run(tmp_path, monkeypatch)
    assert not ledger.exists()
    assert any("head.npz" in r.getMessage() for r in caplog.records)


def _watchlist(tmp_path: Path, roles_spec: list[dict]) -> None:
    (tmp_path / "watchlist.json").write_text(
        json.dumps({"roles": roles_spec}), encoding="utf-8"
    )


def test_new_metric_counts_only_rows_first_seen_inside_the_window(
    tmp_path, monkeypatch
):
    """Stock answers "how big is this field"; new answers "is it hiring this week" (ADR-0051).
    A row without a stamp (pre-ADR-0031) is stock but never new — absence of evidence."""
    from datetime import datetime, timedelta

    _taxonomy(tmp_path / "head", tmp_path / "families.json")
    now = datetime.now(UTC)
    fresh = (now - timedelta(days=1)).isoformat(timespec="seconds")
    stale = (now - timedelta(days=30)).isoformat(timespec="seconds")
    x = [1.0, 0.0, 0.0, 0.0]
    _table(
        tmp_path / "db",
        [
            {
                "id": "a",
                "title": "Dev",
                "employment_type": None,
                "min_years": 3,
                "vector": x,
                "first_seen": fresh,
            },
            {
                "id": "b",
                "title": "Dev",
                "employment_type": None,
                "min_years": 3,
                "vector": x,
                "first_seen": stale,
            },
            {
                "id": "c",
                "title": "Dev",
                "employment_type": None,
                "min_years": 3,
                "vector": x,
                "first_seen": None,
            },
        ],
    )
    ledger = _run(tmp_path, monkeypatch)

    rows = {(r["metric"], r["family"], r["band"]): r["count"] for r in _rows(ledger)}
    assert rows[("stock", "software-engineering", "mid")] == 3
    assert rows[("new", "software-engineering", "mid")] == 1  # only the 1-day-old row


def test_watch_role_counts_by_title_regardless_of_family(tmp_path, monkeypatch):
    """The pattern is the definition (ADR-0051): an FDE posting counts under watch:fde whatever
    family the classifier gave it."""
    _taxonomy(tmp_path / "head", tmp_path / "families.json")
    _watchlist(
        tmp_path,
        [
            {
                "name": "fde",
                "label": "Forward Deployed Engineer",
                "parent": "software-engineering",
                "match": ["forward[ -]deployed", "\\bFDE\\b"],
            }
        ],
    )
    x = [1.0, 0.0, 0.0, 0.0]  # -> software-engineering
    y = [0.0, 1.0, 0.0, 0.0]  # -> ai-ml-data-science: an FDE filed there still counts
    _table(
        tmp_path / "db",
        [
            {
                "id": "a",
                "title": "Forward Deployed Engineer",
                "employment_type": None,
                "min_years": 3,
                "vector": x,
            },
            {
                "id": "b",
                "title": "Senior FDE, Enterprise",
                "employment_type": None,
                "min_years": 6,
                "vector": y,
            },
            {
                "id": "c",
                "title": "Backend Engineer",
                "employment_type": None,
                "min_years": 3,
                "vector": x,
            },
        ],
    )
    ledger = _run(tmp_path, monkeypatch)

    rows = {(r["metric"], r["family"], r["band"]): r["count"] for r in _rows(ledger)}
    assert rows[("stock", "watch:fde", "mid")] == 1
    assert rows[("stock", "watch:fde", "senior")] == 1
    # the watched rows still count in their assigned families — the watchlist observes, never moves
    assert rows[("stock", "software-engineering", "mid")] == 2
    assert rows[("stock", "ai-ml-data-science", "senior")] == 1


def test_the_classifier_decides_each_family_and_watch_roles_count_tech_only(
    tmp_path, monkeypatch
):
    """ADR-0220: a row's family is its title's verdict under the classifier head, a title the
    head cannot place is counted as unclassified-tech, a watch role skips a row the head called
    non-tech, every ledger row carries the head's series version, and the next run reuses the
    title cache instead of encoding again."""
    _taxonomy(tmp_path / "head", tmp_path / "families.json")
    _watchlist(
        tmp_path,
        [
            {
                "name": "frontend",
                "label": "Frontend",
                "parent": "software-engineering",
                "match": ["\\bfront[\\s-]?end\\b"],
            }
        ],
    )
    x = [1.0, 0.0, 0.0, 0.0]  # -> software-engineering
    y = [0.0, 1.0, 0.0, 0.0]  # -> ai-ml-data-science
    z = [0.0, 0.0, 1.0, 0.0]  # -> non-tech
    _table(
        tmp_path / "db",
        [
            {"id": "a", "title": "Frontend Engineer", "vector": x},
            {"id": "b", "title": "ML Engineer", "vector": y},
            {"id": "c", "title": "Front End Clerk", "vector": z},
            # a vector like every served row; the head is told below it cannot place this title
            {"id": "d", "title": "Vague Title", "vector": [0.0, 0.0, 0.0, 1.0]},
        ],
    )
    _FAMILY_OF_TITLE["vague title"] = _AMBIGUOUS
    monkeypatch.setenv(RUN_TS_ENV, "2026-09-25T05:00:00+00:00")
    ledger = _run(tmp_path, monkeypatch)

    written = _rows(ledger)
    rows = {(r["metric"], r["family"]): r["count"] for r in written}
    assert rows[("stock", "software-engineering")] == 1
    assert rows[("stock", "ai-ml-data-science")] == 1
    assert rows[("stock", "unclassified-tech")] == 1  # the head could not place it
    assert rows[("stock", "non-tech")] == 1  # the clerk
    assert rows[("stock", "watch:frontend")] == 1  # the engineer; the clerk is not tech

    cache = role_family_classifier.load_cache(tmp_path / "title_cache.parquet", 1)
    assert len(cache.title_logits) == 4
    encoded = []
    monkeypatch.setattr(
        role_family_classifier, "encode", lambda t, m, r: encoded.append(t)
    )
    monkeypatch.setenv(RUN_TS_ENV, "2026-09-25T06:00:00+00:00")
    _run(tmp_path, monkeypatch)
    assert encoded == []  # every title was already encoded under this head


def test_a_rows_description_vector_can_move_it_off_its_titles_family(
    tmp_path, monkeypatch
):
    """ADR-0224: two copies of one title, one whose served vector reads as non-tech. The title
    part is shared; the row part decides the second, and only the first is assigned a family."""
    row_weights = np.zeros((len(_HEAD_FAMILIES), _DIM), dtype=np.float32)
    row_weights[_HEAD_FAMILIES.index(roles.NON_TECH), 3] = 30.0
    _taxonomy(tmp_path / "head", tmp_path / "families.json", row_weights=row_weights)
    _table(
        tmp_path / "db",
        [
            {"id": "it", "title": "Systems Engineer", "vector": [1.0, 0.0, 0.0, 0.0]},
            {"id": "grid", "title": "Systems Engineer", "vector": [1.0, 0.0, 0.0, 1.0]},
        ],
    )
    ledger = _run(tmp_path, monkeypatch)

    rows = {(r["metric"], r["family"]): r["count"] for r in _rows(ledger)}
    assert rows[("stock", "software-engineering")] == 1
    assert rows[("stock", roles.NON_TECH)] == 1
    snapshot = pq.read_table(tmp_path / "role_assignments.parquet").to_pylist()
    assert {r["id"] for r in snapshot} == {"greenhouse:tests:it"}


def test_a_head_trained_on_another_embedder_errors_visibly(
    tmp_path, monkeypatch, caplog
):
    """The row part learned one embedder's vectors; fed another's it would still answer
    confidently, so the run refuses instead (ADR-0224)."""
    import logging

    _taxonomy(tmp_path / "head", tmp_path / "families.json", row_model="other/embedder")
    _table(
        tmp_path / "db",
        [{"id": "a", "title": "Backend Dev", "vector": [1.0, 0.0, 0.0, 0.0]}],
    )
    caplog.set_level(logging.ERROR, logger="headstart.ingest.role_trends")
    ledger = _run(tmp_path, monkeypatch, expect=1)
    assert not ledger.exists()
    assert any("retrain the head" in r.getMessage() for r in caplog.records)


def test_a_head_trained_on_another_vector_width_errors_visibly(
    tmp_path, monkeypatch, caplog
):
    import logging

    _taxonomy(tmp_path / "head", tmp_path / "families.json", row_dim=_DIM + 1)
    _table(
        tmp_path / "db",
        [{"id": "a", "title": "Backend Dev", "vector": [1.0, 0.0, 0.0, 0.0]}],
    )
    caplog.set_level(logging.ERROR, logger="headstart.ingest.role_trends")
    ledger = _run(tmp_path, monkeypatch, expect=1)
    assert not ledger.exists()
    assert any("retrain the head" in r.getMessage() for r in caplog.records)


def test_repeated_served_ids_error_visibly_instead_of_deciding_garbage(
    tmp_path, monkeypatch, caplog
):
    """Row vectors are matched to rows by id; a repeated id would leave one row's logits unset,
    and that row would be decided from uninitialised memory. The run refuses instead."""
    import logging

    _taxonomy(tmp_path / "head", tmp_path / "families.json")
    _table(
        tmp_path / "db",
        [
            {"id": "a", "title": "Backend Dev", "vector": [1.0, 0.0, 0.0, 0.0]},
            {"id": "a", "title": "Backend Dev", "vector": [1.0, 0.0, 0.0, 0.0]},
        ],
    )
    caplog.set_level(logging.ERROR, logger="headstart.ingest.role_trends")
    ledger = _run(tmp_path, monkeypatch, expect=1)
    assert not ledger.exists()
    assert any("ids repeat" in r.getMessage() for r in caplog.records)


def test_trends_wait_while_a_new_heads_title_cache_warms_up(
    tmp_path, monkeypatch, caplog
):
    """A new head starts with an empty cache. A run whose budget cannot cover the served titles
    fills what it can and counts nothing, rather than charting the backlog as unclassified."""
    import logging

    _taxonomy(tmp_path / "head", tmp_path / "families.json")
    _table(
        tmp_path / "db",
        [{"id": "a", "title": "Backend Dev", "vector": [1.0, 0.0, 0.0, 0.0]}],
    )
    caplog.set_level(logging.WARNING, logger="headstart.ingest.role_trends")
    monkeypatch.setattr(role_trends, "_CLASSIFY_BUDGET_SECONDS", -1.0)
    ledger = _run(tmp_path, monkeypatch)
    assert not ledger.exists()
    assert any("warming up" in r.getMessage() for r in caplog.records)


def test_watchlist_with_unknown_parent_errors_visibly(tmp_path, monkeypatch, caplog):
    _taxonomy(tmp_path / "head", tmp_path / "families.json")
    _watchlist(
        tmp_path,
        [{"name": "fde", "parent": "no-such-family", "match": ["fde"]}],
    )
    x = [1.0, 0.0, 0.0, 0.0]
    _table(
        tmp_path / "db",
        [
            {
                "id": "a",
                "title": "Dev",
                "employment_type": None,
                "min_years": 3,
                "vector": x,
            }
        ],
    )
    ledger = tmp_path / "state"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "role_trends",
            "--db",
            str(tmp_path / "db"),
            "--classifier",
            str(tmp_path / "head"),
            "--title-cache",
            str(tmp_path / "title_cache.parquet"),
            "--families",
            str(tmp_path / "families.json"),
            "--state",
            str(ledger),
            "--watchlist",
            str(tmp_path / "watchlist.json"),
            # Pinned even though this path errors before writing (ADR-0057): the isolation
            # must not depend on the error path staying an error path.
            "--assignments",
            str(tmp_path / "role_assignments.parquet"),
            "--reassignments",
            str(tmp_path / "role_reassignments.csv"),
        ],
    )
    assert (
        role_trends.main() == 1
    )  # visible error, non-fatal to the run (continue-on-error)
    assert not ledger.exists()


def test_count_board_groups_places_rows_excluding_non_tech_and_watch_roles(tmp_path):
    """The third return value feeds ADR-0057's transition diff, so what it omits is load-bearing.

    Non-tech rows carry no family to compare, and watch roles are title matches layered over the
    taxonomy — a row "moving" between those is a title edit, not a reassignment. Either one
    leaking into the snapshot would manufacture transitions out of nothing.
    """
    families = ["software-engineering", "ai-ml-data-science", None]  # None: non-tech
    _watchlist(
        tmp_path,
        [{"name": "backend", "parent": "software-engineering", "match": ["backend"]}],
    )
    watchlist = roles.load_watchlist(
        tmp_path / "watchlist.json", {"software-engineering", "ai-ml-data-science"}
    )
    assert watchlist, "the watch role this test is about must actually be watched"

    rows = pa.Table.from_pylist(
        [
            {
                "id": "ats:b:tech",
                "title": "Backend Engineer",
                "employment_type": "full-time",
                "min_years": 3,
                "vector": [1.0, 0.0, 0.0, 0.0],
                "ats": "greenhouse",
            },
            {
                "id": "ats:b:ai",
                "title": "ML Engineer",
                "employment_type": "full-time",
                "min_years": 3,
                "vector": [0.0, 1.0, 0.0, 0.0],
                "ats": "greenhouse",
            },
            {
                "id": "ats:b:nontech",
                "title": "Data Entry Clerk",
                "employment_type": "full-time",
                "min_years": 0,
                "vector": [0.0, 0.0, 1.0, 0.0],
                "ats": "greenhouse",
            },
        ],
        schema=pa.schema(
            [
                pa.field("id", pa.string()),
                pa.field("title", pa.string()),
                pa.field("employment_type", pa.string()),
                pa.field("min_years", pa.int32()),
                pa.field("vector", pa.list_(pa.float32(), _DIM)),
                pa.field("ats", pa.string()),
            ]
        ),
    )
    counts, non_tech, placed, _board_counts = role_trends.count_board_groups(
        rows, families, watchlist, "2026-01-01T00:00:00+00:00", ["ats:b"] * 3
    )
    assert non_tech == 1
    assigned = {job_id: placement.family for job_id, placement in placed.items()}
    assert assigned == {
        "ats:b:tech": "software-engineering",
        "ats:b:ai": "ai-ml-data-science",
    }
    assert not any(k.startswith(roles.WATCH_PREFIX) for k in assigned.values())
    # the watch role was counted, just never placed
    assert any(key[1] == roles.WATCH_PREFIX + "backend" for key in counts)


def test_top_line_distinguishes_two_atses_sharing_a_family_and_band(
    tmp_path, monkeypatch, caplog
):
    """The counts are keyed on (metric, family, band, ats) but the log dropped `ats` back out.

    Two ATSes' rows for the same family and band then rendered as the same label with different
    numbers — "software-engineering/senior 7044, software-engineering/senior 6637" appeared in
    every run of the 2026-08-25 review — which reads as a double-count in the one ledger this
    repo has already been burned on double-counting. The ledger was always right (ADR-0075); only
    the label was ambiguous.
    """
    _taxonomy(tmp_path / "head", tmp_path / "families.json")
    x = [1.0, 0.0, 0.0, 0.0]
    _table(
        tmp_path / "db",
        [
            {
                "id": "greenhouse:b:1",
                "title": "Dev",
                "employment_type": None,
                "min_years": 3,
                "vector": x,
                "ats": "greenhouse",
            },
            {
                "id": "workday:b:2",
                "title": "Dev",
                "employment_type": None,
                "min_years": 3,
                "vector": x,
                "ats": "workday",
            },
        ],
    )
    with caplog.at_level("INFO"):
        _run(tmp_path, monkeypatch)

    line = next(r.message for r in caplog.records if " | top: " in r.message)
    top = line.split(" | top: ")[1].split(" | new in ")[0]
    labels = [entry.rsplit(" ", 1)[0] for entry in top.split(", ")]
    assert len(labels) == len(set(labels)), f"top-5 labels are not unique: {top}"
    assert "software-engineering/mid/greenhouse" in top
    assert "software-engineering/mid/workday" in top


def _tick_rows(path: Path) -> list[dict]:
    return pq.read_table(path).to_pylist()


def test_a_second_tick_books_turnover_beside_the_level_changes(tmp_path, monkeypatch):
    """ADR-0227 end to end. The first tick writes the snapshot that turnover diffs. The second
    books a new posting as opened, an evicted one as closed, and a row that left any other way
    (here a prune, as `cleanup-index` makes) as recounted, plus one marker for an Unauthoritative
    Board. All of it goes in the tick's own delta file, the replayed levels carry levels only,
    and the queue keeps only what the new snapshot does not yet cover."""
    from headstart.ingest import RUN_TS_ENV

    _taxonomy(tmp_path / "head", tmp_path / "families.json")

    def row(job_id: str, seen: str) -> dict:
        return {
            "id": f"greenhouse:acme:{job_id}",
            "title": "Backend Dev",
            "employment_type": None,
            "min_years": 5,
            "vector": [1.0, 0.0, 0.0, 0.0],
            "first_seen": seen,
        }

    early = "2026-09-20T00:00:00+00:00"
    _table(
        tmp_path / "db", [row("stays", early), row("closes", early), row("dup", early)]
    )
    monkeypatch.setenv(RUN_TS_ENV, "2026-09-25T05:00:00+00:00")
    _run(tmp_path, monkeypatch)
    first = _tick_rows(
        tmp_path
        / "state"
        / "role_trend_board_deltas"
        / "2026-09-25T05-00-00+00-00.parquet"
    )
    assert {r["metric"] for r in first} == {"stock", "new"}, (
        "no snapshot to diff yet, so the first tick books no turnover"
    )

    lancedb.connect(tmp_path / "db").drop_table(PROD_TABLE)
    _table(
        tmp_path / "db",
        [row("stays", early), row("opens", "2026-09-25T05:30:00+00:00")],
    )
    queue = tmp_path / "eviction_queue.tsv"
    queue.write_text(
        "2026-09-25T04:00:00+00:00\tgreenhouse:acme:long-gone\n"
        "2026-09-25T06:00:00+00:00\tgreenhouse:acme:closes\n",
        encoding="utf-8",
    )
    (tmp_path / "unauthoritative_boards.json").write_text(
        json.dumps({"greenhouse:acme": "truncated"}), encoding="utf-8"
    )
    monkeypatch.setenv(RUN_TS_ENV, "2026-09-25T06:00:00+00:00")
    _run(tmp_path, monkeypatch)

    tick = _tick_rows(
        tmp_path
        / "state"
        / "role_trend_board_deltas"
        / "2026-09-25T06-00-00+00-00.parquet"
    )
    booked = {
        r["metric"]: r["delta"] for r in tick if r["metric"] not in ("stock", "new")
    }
    assert booked == {"opened": 1, "closed": 1, "recounted_out": 1, "unscoped": 1}
    stock = sum(r["delta"] for r in tick if r["metric"] == "stock")
    assert stock == booked["opened"] - booked["closed"] - booked["recounted_out"]
    _, levels = trend_history.board_levels(tmp_path / "state")
    assert {metric for _, metric, *_ in levels} <= {"stock", "new"}
    # The entry the diffed 05:00 snapshot already covered is dropped. This run's stays until a
    # published snapshot covers it: if this run's `data/state` upload failed, the next tick
    # would diff the 05:00 snapshot again and still book it as Closed.
    assert queue.read_text(encoding="utf-8") == (
        "2026-09-25T06:00:00+00:00\tgreenhouse:acme:closes\n"
    )


def test_every_tick_writes_one_file_stamped_with_how_it_was_counted(
    tmp_path, monkeypatch
):
    """ADR-0230: a tick that moved nothing still writes its Board-delta file, empty, so the
    directory holds one file per tick; every file names its tick and its methodology."""
    _taxonomy(tmp_path / "head", tmp_path / "families.json")
    _table(
        tmp_path / "db",
        [
            {
                "id": "greenhouse:acme:1",
                "title": "Backend Dev",
                "vector": [1.0, 0.0, 0.0, 0.0],
            }
        ],
    )
    for ts in ("2026-09-25T05:00:00+00:00", "2026-09-25T06:00:00+00:00"):
        monkeypatch.setenv(RUN_TS_ENV, ts)
        _run(tmp_path, monkeypatch)

    files = sorted((tmp_path / "state" / "role_trend_board_deltas").glob("*.parquet"))
    assert [f.name for f in files] == [
        "2026-09-25T05-00-00+00-00.parquet",
        "2026-09-25T06-00-00+00-00.parquet",
    ]
    unchanged = pq.read_table(files[1])
    assert unchanged.num_rows == 0
    metadata = unchanged.schema.metadata
    assert metadata[b"ts"] == b"2026-09-25T06:00:00+00:00"
    assert b"centroid_version" not in metadata  # no series versions (ADR-0230)
    assert json.loads(metadata[b"methodology"]) == {
        "family_list_fingerprint": roles.family_list_fingerprint(
            tmp_path / "families.json"
        ),
        "family_classifier_version": 1,
        "tech_filter_version": tech_filter.TECH_FILTER_VERSION,
        "derivations_version": DERIVATIONS_VERSION,
        "dedup_version": index_plan.DEDUP_VERSION,
    }


def test_a_failed_snapshot_takes_the_ticks_file_back_out(tmp_path, monkeypatch):
    """The tick's delta file and the snapshot turnover diffs move together (ADR-0227). The file
    without its snapshot would book this tick's turnover again next tick."""
    from headstart.ingest import RUN_TS_ENV, role_assignments

    _taxonomy(tmp_path / "head", tmp_path / "families.json")
    _table(
        tmp_path / "db",
        [
            {
                "id": "greenhouse:acme:1",
                "title": "Backend Dev",
                "employment_type": None,
                "min_years": 5,
                "vector": [1.0, 0.0, 0.0, 0.0],
            }
        ],
    )

    def _unwritable(*_args, **_kwargs):
        raise RuntimeError("a snapshot the writer refused")

    monkeypatch.setattr(role_assignments, "save", _unwritable)
    monkeypatch.setenv(RUN_TS_ENV, "2026-09-25T05:00:00+00:00")
    with pytest.raises(RuntimeError):  # any failure, not only an OSError
        _run(tmp_path, monkeypatch)
    assert not list((tmp_path / "state" / "role_trend_board_deltas").glob("*.parquet"))
