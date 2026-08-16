"""Tests for the eightfold alias deduplicator (scripts/validate/dedupe_eightfold_aliases.py, #154).

The property under test is which hostname survives a cluster: a company's own branded domain
should win over eightfold's generic `*.eightfold.ai` subdomain, a curated `--prefer` always wins
over the automatic rule, and a genuine tie needs a deterministic (not order-dependent) fallback.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "validate"))


@pytest.fixture(scope="module")
def mod():
    """Import the script by path — `scripts/` is not a package, and it pulls in
    `headstart.http`, so this is skipped wherever that import cannot be satisfied."""
    pytest.importorskip("curl_cffi")
    spec = importlib.util.spec_from_file_location(
        "dedupe_eightfold_aliases",
        ROOT / "scripts" / "validate" / "dedupe_eightfold_aliases.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pick_winner_prefers_the_branded_domain_over_eightfold_ai(mod):
    assert (
        mod.pick_winner(["nvidia.eightfold.ai", "jobs.nvidia.com"], {})
        == "jobs.nvidia.com"
    )
    # order in the input list must not matter
    assert (
        mod.pick_winner(["jobs.nvidia.com", "nvidia.eightfold.ai"], {})
        == "jobs.nvidia.com"
    )


def test_pick_winner_falls_back_to_lexicographic_when_both_are_eightfold_ai(mod):
    """dsm-firmenich.eightfold.ai vs dsm.eightfold.ai — neither is a branded domain, so the
    automatic rule can't disambiguate by that signal and needs a deterministic fallback."""
    assert (
        mod.pick_winner(["dsm-firmenich.eightfold.ai", "dsm.eightfold.ai"], {})
        == "dsm-firmenich.eightfold.ai"
    )


def test_pick_winner_honours_a_curated_override(mod):
    """A curated --prefer line always wins, even against the automatic branded-domain rule."""
    assert (
        mod.pick_winner(
            ["jobs.nvidia.com", "nvidia.eightfold.ai"],
            {"nvidia.eightfold.ai": "nvidia.eightfold.ai"},
        )
        == "nvidia.eightfold.ai"
    )


def test_read_prefer_accepts_comments_and_blank_lines(mod, tmp_path):
    path = tmp_path / "prefer.txt"
    path.write_text(
        "# a comment\n\neightfold:nvidia.eightfold.ai  # trailing comment\n"
    )
    assert mod.read_prefer(path) == {"nvidia.eightfold.ai": "nvidia.eightfold.ai"}


def test_read_prefer_rejects_a_malformed_line(mod, tmp_path):
    path = tmp_path / "prefer.txt"
    path.write_text("not-a-valid-line\n")
    with pytest.raises(SystemExit):
        mod.read_prefer(path)
