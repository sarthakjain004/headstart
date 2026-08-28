"""Tests for the published-state witness (headstart.ingest.state_witness, ADR-0095).

The property under test is the asymmetry the whole design rests on: the witness may **under**-claim
freely, and must never over-claim. An omitted root costs nothing — the fetch behaves as it did
before ADR-0095. A root wrongly claimed fails every later fetch closed, which is an outage. So the
tests here are mostly about what `publish` refuses to record.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

import headstart.ingest.state_witness as sw


def _populate(root: Path, *rels: str) -> None:
    for rel in rels:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x", encoding="utf-8")


def test_publish_records_only_roots_that_hold_files(tmp_path: Path) -> None:
    _populate(
        tmp_path, "data/state/board_priority.csv", "data/lancedb/jobs.lance/x.lance"
    )
    (tmp_path / "data/descriptions").mkdir(parents=True)  # exists, empty
    assert sw.publish(tmp_path) == ["data/lancedb", "data/state"]


def test_publish_round_trips_through_the_file_it_writes(tmp_path: Path) -> None:
    _populate(tmp_path, "data/embeddings/jobs/meta.jsonl")
    sw.publish(tmp_path)
    written = json.loads((tmp_path / sw.WITNESS_PATH).read_text(encoding="utf-8"))
    assert written == {"dirs": ["data/embeddings/jobs"]}


def test_publish_on_an_empty_tree_claims_nothing(tmp_path: Path) -> None:
    """A first run publishes a witness that permits the next one to bootstrap too."""
    assert sw.publish(tmp_path) == []


def test_a_nested_file_still_counts_its_root(tmp_path: Path) -> None:
    """`data/lancedb/` is four levels deep in practice — a shallow check would under-claim it
    into uselessness, which is safe but pointless."""
    _populate(tmp_path, "data/lancedb/jobs.lance/_versions/1.manifest")
    assert "data/lancedb" in sw.publish(tmp_path)


def test_the_witness_lives_where_an_existing_upload_already_carries_it() -> None:
    """No new write point: `data/state` is uploaded every run, so the witness rides along."""
    assert sw.WITNESS_PATH.startswith("data/state/")
    assert "data/state" in sw.ROOTS


def test_the_witness_does_not_speak_for_the_centroids(tmp_path: Path) -> None:
    """`cluster-roles.yml` writes `data/state/role_centroids` on its own schedule, so a pipeline
    run that never touches it must not be read as having lost it."""
    assert "data/state/role_centroids" not in sw.ROOTS
    assert sw.unwitnessed(["data/state/role_centroids/*"], {"data/state"}) == []


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("data/state/*", "data/state"),
        ("data/embeddings/jobs/meta.jsonl", "data/embeddings/jobs"),
        ("data/state/role_centroids/*", "data/state/role_centroids"),
        ("README.md", "README.md"),
    ],
)
def test_pattern_root_reads_the_directory_a_pattern_draws_from(
    pattern, expected
) -> None:
    assert sw.pattern_root(pattern) == expected


def test_unwitnessed_is_silent_when_the_dataset_carries_no_witness() -> None:
    """`None` is the first-run answer, and the only one that permits a bootstrap."""
    assert sw.unwitnessed(["data/state/*", "data/lancedb/*"], None) == []


def test_unwitnessed_names_every_claimed_root_the_patterns_touch() -> None:
    claimed = sw.unwitnessed(
        ["data/state/*", "data/lancedb/*", "data/descriptions/*"],
        {"data/state", "data/lancedb"},
    )
    assert claimed == ["data/lancedb", "data/state"]


class _EntryNotFound(Exception):
    pass


def _stub_hub(monkeypatch, download) -> None:
    module = types.ModuleType("huggingface_hub")
    errors = types.ModuleType("huggingface_hub.errors")
    errors.EntryNotFoundError = _EntryNotFound  # type: ignore[attr-defined]
    errors.LocalEntryNotFoundError = type("_Local", (_EntryNotFound,), {})  # type: ignore[attr-defined]
    module.errors = errors  # type: ignore[attr-defined]
    module.hf_hub_download = download  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "huggingface_hub", module)
    monkeypatch.setitem(sys.modules, "huggingface_hub.errors", errors)


def test_published_roots_reads_none_when_the_dataset_has_no_witness(
    monkeypatch,
) -> None:
    def missing(*a, **k):
        raise _EntryNotFound("published_dirs.json")

    _stub_hub(monkeypatch, missing)
    assert sw.published_roots("repo", None) is None


def test_a_connection_failure_is_not_read_as_an_absent_witness(monkeypatch) -> None:
    """The trap `published_roots` is written against, as a regression test.

    `hf_hub_download` raises `LocalEntryNotFoundError` for "most likely a connection issue or Hub
    downtime" — a 429 or 5xx included — and that class **subclasses `EntryNotFoundError`**
    (verified against huggingface_hub 1.21.0). Catching the parent therefore reads an unreachable
    Hub as "this dataset published nothing", reopening ADR-0030's hole on exactly the transient
    failure class that lost run 30304173982. The first version of this module had that bug, and
    `HF_HUB_OFFLINE=1` returned `None`.
    """

    class _Parent(Exception):
        pass

    class _Remote(_Parent):
        pass

    class _Local(_Parent):  # what a connection failure really raises
        pass

    module = types.ModuleType("huggingface_hub")
    errors = types.ModuleType("huggingface_hub.errors")
    errors.EntryNotFoundError = _Parent  # type: ignore[attr-defined]
    errors.RemoteEntryNotFoundError = _Remote  # type: ignore[attr-defined]
    errors.LocalEntryNotFoundError = _Local  # type: ignore[attr-defined]
    module.errors = errors  # type: ignore[attr-defined]

    def unreachable(*a, **k):
        raise _Local("connection issue or Hub downtime")

    module.hf_hub_download = unreachable  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "huggingface_hub", module)
    monkeypatch.setitem(sys.modules, "huggingface_hub.errors", errors)

    with pytest.raises(_Local):
        sw.published_roots("repo", None)


def test_published_roots_propagates_anything_that_is_not_a_missing_file(
    monkeypatch,
) -> None:
    """A witness we could not READ is not a witness that says nothing — that distinction is the
    same fail-closed stance `remote_files` takes, and losing it would reopen ADR-0030's hole from
    the other side."""

    def unreachable(*a, **k):
        raise ConnectionError("hub down")

    _stub_hub(monkeypatch, unreachable)
    with pytest.raises(ConnectionError):
        sw.published_roots("repo", None)


def test_published_roots_parses_what_publish_wrote(monkeypatch, tmp_path: Path) -> None:
    _populate(tmp_path, "data/state/x.csv")
    sw.publish(tmp_path)
    _stub_hub(monkeypatch, lambda *a, **k: str(tmp_path / sw.WITNESS_PATH))
    assert sw.published_roots("repo", None) == {"data/state"}
