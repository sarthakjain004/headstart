"""Tests for the guarded state fetch (headstart.ingest.state_fetch, ADR-0030).

The property under test is the one whose absence cost run 30304173982 its state: when the Hub
lists files that the fetch then fails to deliver, the caller must be told, not left with an empty
dir that reads as a legitimate first run. The remote listing is what makes that decidable, so the
two pure halves — which remote files a pattern set asks for, and which of those failed to land —
are tested directly; the fetch itself is I/O.
"""

from __future__ import annotations

from pathlib import Path

import headstart.ingest.state_fetch as sf

_REMOTE = [
    "README.md",
    "data/embeddings/jobs/manifest.json",
    "data/embeddings/jobs/meta.jsonl",
    "data/embeddings/jobs/embeddings.f32",
    "data/lancedb/jobs.lance/_versions/1.manifest",
    "data/lancedb/jobs.lance/data/abc.lance",
    "data/state/board_priority.csv",
]


def test_backoff_schedule_is_exponential_and_capped() -> None:
    """ADR-0033: waits of 30/60/120/240 between five attempts — 7.5 min total, enough to ride
    out the measured multi-minute 429 windows that a 90s budget lost 6 of 40 runs to. The cap
    keeps a would-be attempt 6+ wait bounded if _ATTEMPTS ever grows."""
    assert [sf.wait_before(n) for n in range(1, sf._ATTEMPTS)] == [30, 60, 120, 240]
    assert sf.wait_before(10) == 300  # capped, not 30 * 2**9
    assert sum(sf.wait_before(n) for n in range(1, sf._ATTEMPTS)) == 450


def test_remote_matches_selects_only_matching_files() -> None:
    """A pattern set asks for a subset of what the repo holds — including nested paths, since
    `data/lancedb/*` must reach the table's fragment files, not just its top level."""
    assert sf.remote_matches(_REMOTE, ["data/embeddings/jobs/*", "data/lancedb/*"]) == {
        "data/embeddings/jobs/manifest.json",
        "data/embeddings/jobs/meta.jsonl",
        "data/embeddings/jobs/embeddings.f32",
        "data/lancedb/jobs.lance/_versions/1.manifest",
        "data/lancedb/jobs.lance/data/abc.lance",
    }
    assert sf.remote_matches(_REMOTE, ["data/state/*"]) == {
        "data/state/board_priority.csv"
    }


def test_remote_matches_is_empty_when_the_repo_has_no_such_state() -> None:
    """The genuine first run: nothing on the Hub matches, so nothing is required and the caller
    proceeds. This is why the guard needs no bootstrap opt-out flag."""
    assert sf.remote_matches(["README.md"], ["data/embeddings/jobs/*"]) == set()


def test_absent_locally_flags_state_that_did_not_land(tmp_path: Path) -> None:
    """The regression: the Hub listed a store, the fetch delivered nothing (the offline fallback
    returns the empty local dir without raising), so every wanted file is absent."""
    wanted = sf.remote_matches(_REMOTE, ["data/embeddings/jobs/*"])
    assert sf.absent_locally(wanted, tmp_path) == sorted(wanted)


def test_absent_locally_is_empty_once_every_file_landed(tmp_path: Path) -> None:
    wanted = sf.remote_matches(_REMOTE, ["data/embeddings/jobs/*"])
    for rel in wanted:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x", encoding="utf-8")
    assert sf.absent_locally(wanted, tmp_path) == []


def test_absent_locally_catches_a_partial_fetch(tmp_path: Path) -> None:
    """A half-delivered store is as unpublishable as an empty one — one file short still aborts."""
    wanted = sf.remote_matches(_REMOTE, ["data/embeddings/jobs/*"])
    landed = sorted(wanted)[1:]
    for rel in landed:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x", encoding="utf-8")
    assert sf.absent_locally(wanted, tmp_path) == [sorted(wanted)[0]]
