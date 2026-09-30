"""Tests for the Hugging Face open-apply-jobs miner's spellings
(scripts/discover/mine_hf_open_apply_jobs.py)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "discover"))

import mine_hf_open_apply_jobs as miner


def test_rows_follow_each_ledgers_own_spelling():
    assert miner.rows_for("gem", {"b-co", "a-co"}) == [
        ("a-co", "https://jobs.gem.com/a-co"),
        ("b-co", "https://jobs.gem.com/b-co"),
    ]
    assert miner.rows_for("rippling", {"acme"}) == [("acme", "ats.rippling.com/acme")]


def test_an_ashby_slug_with_a_space_keeps_it_in_the_tenant_and_writes_it_percent_encoded():
    """The 30 spaced rows already in the ledger are tenant `Blackpoint Cyber`, url `.../Blackpoint%20Cyber`."""
    assert miner.rows_for("ashby", {"Sine Engineering", "ambient.ai"}) == [
        ("Sine Engineering", "https://jobs.ashbyhq.com/Sine%20Engineering"),
        ("ambient.ai", "https://jobs.ashbyhq.com/ambient.ai"),
    ]
