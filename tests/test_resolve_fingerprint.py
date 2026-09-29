"""Tests for the careers-page ATS fingerprinter (scripts/resolve/fingerprint.py).

A script, not an installed module, so it is loaded from its path.
"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "resolve_fingerprint", ROOT / "scripts" / "resolve" / "fingerprint.py"
)
fingerprint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fingerprint)


def test_a_lever_slug_keeps_the_casing_the_page_links_it_in():
    """Lever reads a slug case-sensitively: `api.lever.co/v0/postings/CesiumAstro` lists 309
    postings and `.../cesiumastro` answers "Document not found" (measured 2026-09-28)."""
    html = '<a href="https://jobs.lever.co/CesiumAstro">Careers</a>'
    assert ("lever", "CesiumAstro") in fingerprint.detect(html)


def test_an_ashby_slug_with_a_space_is_kept_whole():
    """#864: `Blackpoint%20Cyber` was cut to `blackpoint`, a Board that 404s."""
    html = '<a href="https://jobs.ashbyhq.com/Blackpoint%20Cyber">Jobs</a>'
    assert ("ashby", "blackpoint cyber") in fingerprint.detect(html)
    assert ("ashby", "blackpoint") not in fingerprint.detect(html)


@pytest.mark.parametrize(
    ("link", "slug"),
    [
        # A Company's domain as its slug, 130 Live rows. The prefix names another Board or
        # none: `affinity` answers 200 with 0 postings where `affinity.co` lists 8 (2026-09-29).
        ("https://jobs.ashbyhq.com/affinity.co/0af6c47b", "affinity.co"),
        # `careers` is in BLOCK, but a dotted Ashby slug is a domain, not a host's label
        (
            "https://api.ashbyhq.com/posting-api/job-board/careers.azx.io",
            "careers.azx.io",
        ),
        # a trailing `%20` is not part of the slug: `elveo%20` answers 404
        ("https://jobs.ashbyhq.com/Elveo%20", "elveo"),
        # Ashby reads `+` as itself, so this link names no Board (ADR-0280)
        ("https://jobs.ashbyhq.com/Blackpoint+Cyber", None),
    ],
)
def test_an_ashby_slug_is_read_as_the_scraper_says_a_link_writes_it(link, slug):
    found = {
        tok
        for ats, tok in fingerprint.detect(f'<a href="{link}">Jobs</a>')
        if ats == "ashby"
    }
    assert found == ({slug} if slug else set())
