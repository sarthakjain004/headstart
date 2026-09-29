"""Tests for the harvested-list consolidator's Ashby URL scan
(scripts/merge/consolidate_harvested_lists.py), loaded from its path because it is a script."""

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "consolidate_harvested_lists",
    Path(__file__).resolve().parents[1]
    / "scripts/merge/consolidate_harvested_lists.py",
)
consolidate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(consolidate)


def test_an_ashby_link_is_read_as_the_scraper_says_a_link_writes_it():
    """`[A-Za-z0-9_.-]+` cut `Blackpoint%20Cyber` to `Blackpoint`, a Board that 404s (ADR-0280)."""
    text = (
        "https://jobs.ashbyhq.com/Blackpoint%20Cyber/0af6c47b "
        "https://jobs.ashbyhq.com/ambient.ai "
        "https://jobs.ashbyhq.com/Blackpoint+Cyber"
    )
    assert {
        slug for ats, slug, _url in consolidate.url_scan(text) if ats == "ashby"
    } == {
        "Blackpoint Cyber",
        "ambient.ai",
    }
