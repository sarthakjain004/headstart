"""Tests for the careers-page ATS fingerprinter (scripts/resolve/fingerprint.py).

A script, not an installed module, so it is loaded from its path.
"""

import importlib.util
from pathlib import Path

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


def test_an_ashby_board_name_with_a_space_is_kept_whole():
    """#864: `Blackpoint%20Cyber` was cut to `blackpoint`, a Board that 404s."""
    html = '<a href="https://jobs.ashbyhq.com/Blackpoint%20Cyber">Jobs</a>'
    assert ("ashby", "blackpoint cyber") in fingerprint.detect(html)
    assert ("ashby", "blackpoint") not in fingerprint.detect(html)
