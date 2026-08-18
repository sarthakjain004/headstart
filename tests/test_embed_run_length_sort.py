"""Tests for the batch length-sorting that cuts padding waste (ADR-0029).

The load-bearing properties: the sort is a permutation (no Doc is lost or duplicated — the
store stays row-aligned with its metadata), it never mixes Docs across windows (so board
priority survives to within one window, ADR-0022), and it actually reduces padded cost.
"""

from __future__ import annotations

import pytest

pytest.importorskip("torch")
pytest.importorskip("sentence_transformers")
pytest.importorskip("langdetect")

# Imported after the gates above, not at the top: the module pulls the ML stack, which
# the quality CI job does not install — this must skip rather than error.
import headstart.ingest.embed_run as er


def _padded_cost(order: list[int], tokens: list[int], batch: int) -> int:
    """What the encoder actually pays: every batch is padded to its longest member."""
    return sum(
        max(tokens[i] for i in order[s : s + batch]) * len(order[s : s + batch])
        for s in range(0, len(order), batch)
    )


def test_length_sort_is_a_permutation():
    idxs = list(range(50))
    tokens = [(i * 37) % 2048 + 1 for i in idxs]  # deterministic, unsorted
    out = er._length_sorted(idxs, tokens, batch=7)
    assert sorted(out) == idxs  # nothing lost, nothing duplicated
    assert len(out) == len(idxs)


def test_length_sort_keeps_docs_inside_their_window():
    """Priority survives to within one window — a Doc never jumps across a window boundary."""
    idxs = list(range(64))
    tokens = [2048 - i for i in idxs]  # perfectly reversed, worst case for sorting
    batch, window = 4, er._SORT_WINDOW
    out = er._length_sorted(idxs, tokens, batch=batch)
    span = batch * window
    for s in range(0, len(idxs), span):
        assert set(out[s : s + span]) == set(idxs[s : s + span])


def test_length_sort_reduces_padded_cost():
    # lengths uncorrelated with position, as board-priority order is with respect to length
    idxs = list(range(240))
    tokens = [1025 + (i * 91) % 1024 for i in idxs]  # the <=2048 bucket's real range
    batch = 7
    before = _padded_cost(idxs, tokens, batch)
    after = _padded_cost(er._length_sorted(idxs, tokens, batch), tokens, batch)
    assert after < before
    true_tokens = sum(tokens)
    assert after / true_tokens < before / true_tokens
