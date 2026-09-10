"""Tests for the compare-and-swap guard on HF state writes (headstart.ingest.state_guard, ADR-0129).

The property under test is the one whose absence cost run 34450830376 its entire index write on
2026-09-10: two workflows read ``data/lancedb``, spend minutes changing it, and upload, and
nothing between the read and the upload asks whether the base moved. The pipeline wrote 410,516
rows at 08:29:49; a compaction that had read 409,810 four minutes earlier uploaded over it at
08:30:27 with ``--delete "*"``, and the next run opened on 409,810 with no error anywhere.

So the tests are shaped as that incident: record a fingerprint, let *another writer* change the
prefix, and require ``verify`` to refuse. The remote listing is faked at ``_siblings`` — the one
Hub call this module makes — because what is being tested is the decision, not the transport.

Deliberately free of the heavy extras: `state_guard` reaches `huggingface_hub` only inside
`_siblings`, so these run in CI, where numpy/torch/lancedb are not installed.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import headstart.ingest.state_guard as sg

REPO = "imPoseidon/headstart-index"


def _listing(
    files: dict[str, str], extra: dict[str, str] | None = None
) -> list[SimpleNamespace]:
    """A fake `repo_info(files_metadata=True).siblings`. ``extra`` sits outside the guarded prefix."""
    rows = [SimpleNamespace(rfilename=p, blob_id=b) for p, b in files.items()]
    rows += [SimpleNamespace(rfilename=p, blob_id=b) for p, b in (extra or {}).items()]
    return rows


def _serve(monkeypatch, files, extra=None):
    monkeypatch.setattr(sg, "_siblings", lambda repo, token: _listing(files, extra))


BASE = {
    "data/lancedb/jobs.lance/_versions/1.manifest": "aaa1",
    "data/lancedb/jobs.lance/data/0.lance": "bbb2",
    "data/lancedb/_index_base.json": "ccc3",
}


def test_unchanged_prefix_verifies(tmp_path, monkeypatch):
    _serve(monkeypatch, BASE)
    guard = tmp_path / "guard.json"
    assert sg.record(guard, REPO, "data/lancedb", None) == 0
    assert sg.verify(guard, REPO, "data/lancedb", None) == 0


def test_a_concurrent_delete_is_refused(tmp_path, monkeypatch):
    """The 2026-09-10 shape: we read the base, another writer replaced files, we must not upload.

    A compaction's ``--delete "*"`` presents as files disappearing between record and verify.
    """
    _serve(monkeypatch, BASE)
    guard = tmp_path / "guard.json"
    sg.record(guard, REPO, "data/lancedb", None)
    survivor = {"data/lancedb/jobs.lance/_versions/1.manifest": "aaa1"}
    _serve(monkeypatch, survivor)
    assert sg.verify(guard, REPO, "data/lancedb", None) == 1


def test_a_concurrent_append_is_refused(tmp_path, monkeypatch):
    """The mirror direction: the pipeline's additive upload landing under a compaction's feet."""
    _serve(monkeypatch, BASE)
    guard = tmp_path / "guard.json"
    sg.record(guard, REPO, "data/lancedb", None)
    _serve(monkeypatch, BASE | {"data/lancedb/jobs.lance/data/1.lance": "ddd4"})
    assert sg.verify(guard, REPO, "data/lancedb", None) == 1


def test_modified_content_at_the_same_path_is_refused(tmp_path, monkeypatch):
    """Names alone cannot carry this: a rebuild can reuse a path with different content."""
    _serve(monkeypatch, BASE)
    guard = tmp_path / "guard.json"
    sg.record(guard, REPO, "data/lancedb", None)
    _serve(monkeypatch, BASE | {"data/lancedb/jobs.lance/data/0.lance": "REBUILT"})
    assert sg.verify(guard, REPO, "data/lancedb", None) == 1


def test_writes_outside_the_prefix_are_ignored(tmp_path, monkeypatch):
    """The pipeline publishes four commits per run. Only ``data/lancedb`` may move this verdict —
    this is why the guard is a path-scoped content fingerprint and not the repo's commit sha."""
    _serve(monkeypatch, BASE, extra={"data/state/board_priority.csv": "old"})
    guard = tmp_path / "guard.json"
    sg.record(guard, REPO, "data/lancedb", None)
    _serve(
        monkeypatch,
        BASE,
        extra={"data/state/board_priority.csv": "NEW", "data/descriptions/x": "z"},
    )
    assert sg.verify(guard, REPO, "data/lancedb", None) == 0


def test_an_empty_prefix_is_a_verdict_not_an_error(tmp_path, monkeypatch):
    """A genuine first run has nothing under the prefix, and must be able to record and upload."""
    _serve(monkeypatch, {}, extra={"README.md": "r1"})
    guard = tmp_path / "guard.json"
    assert sg.record(guard, REPO, "data/lancedb", None) == 0
    assert sg.verify(guard, REPO, "data/lancedb", None) == 0


def test_first_write_into_an_empty_prefix_is_still_guarded(tmp_path, monkeypatch):
    """...but if someone else populates it in the meantime, that is still a collision."""
    _serve(monkeypatch, {}, extra={"README.md": "r1"})
    guard = tmp_path / "guard.json"
    sg.record(guard, REPO, "data/lancedb", None)
    _serve(monkeypatch, BASE)
    assert sg.verify(guard, REPO, "data/lancedb", None) == 1


def test_a_missing_blob_id_fails_closed(tmp_path, monkeypatch):
    """A file whose content the Hub will not identify is a change we cannot detect. Skipping it
    would leave the digest stable across exactly the edit the guard exists to catch."""
    monkeypatch.setattr(
        sg,
        "_siblings",
        lambda repo, token: [
            SimpleNamespace(
                rfilename="data/lancedb/jobs.lance/data/0.lance", blob_id=None
            )
        ],
    )
    with pytest.raises(RuntimeError, match="blob_id"):
        sg.fingerprint(REPO, "data/lancedb", None)


def test_a_missing_record_refuses_rather_than_waves_through(tmp_path, monkeypatch):
    """No recorded base means `record` never ran or its file was lost — which is the unguarded
    write this module replaces, so it must not be the quiet path."""
    _serve(monkeypatch, BASE)
    assert sg.verify(tmp_path / "absent.json", REPO, "data/lancedb", None) == 1


def test_digest_ignores_listing_order():
    a = {"b/2": "x", "a/1": "y"}
    b = {"a/1": "y", "b/2": "x"}
    assert sg.digest_of(a) == sg.digest_of(b)


def test_changes_names_what_the_other_writer_did():
    before = {"p/keep": "1", "p/gone": "2", "p/edit": "3"}
    after = {"p/keep": "1", "p/edit": "9", "p/new": "4"}
    added, removed, modified = sg.changes(before, after)
    assert (added, removed, modified) == (["p/new"], ["p/gone"], ["p/edit"])


def test_record_is_readable_json_carrying_the_prefix(tmp_path, monkeypatch):
    _serve(monkeypatch, BASE)
    guard = tmp_path / "guard.json"
    sg.record(guard, REPO, "data/lancedb", None)
    saved = json.loads(Path(guard).read_text())
    assert saved["prefix"] == "data/lancedb/"
    assert saved["count"] == len(BASE)
    assert saved["files"] == BASE
