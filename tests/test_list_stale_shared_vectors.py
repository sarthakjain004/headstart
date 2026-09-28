"""Tests for the one-off stale-vector list (``scripts/embed/list_stale_shared_vectors.py``,
ADR-0285): which rows of a shared vector are listed for a re-embed."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "embed"
    / "list_stale_shared_vectors.py"
)
_spec = importlib.util.spec_from_file_location("list_stale_shared_vectors", _PATH)
lister = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lister)


def test_a_vector_shared_across_different_titles_lists_every_row_of_it():
    """Which row the vector truly encodes can't be told apart, and re-embedding the right one
    changes nothing, so the whole group is listed."""
    clone = b"\x01" * 8
    rows = [
        ("workday:amat/external:R2618176", "Process Engineer IV", clone),
        (
            "workday:amat/external:R2624055",
            "New College Grad - Process Engineer",
            clone,
        ),
        ("eightfold:amat:1", "New College Grad - Process Engineer", clone),
    ]
    assert lister.stale_ids(rows) == sorted(r[0] for r in rows)


def test_duplicate_postings_sharing_one_vector_and_title_are_not_listed():
    same = b"\x02" * 8
    rows = [
        ("greenhouse:a:1", "Backend Engineer", same),
        ("greenhouse:a:2", " backend  engineer ", same),  # the same title, padded
        ("greenhouse:a:3", "Frontend Engineer", b"\x03" * 8),
    ]
    assert lister.stale_ids(rows) == []
