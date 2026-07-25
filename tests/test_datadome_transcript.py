"""DataDome audio-challenge transcript cleaning.

The live failure this pins: Whisper returned the clip's spoken instruction along with the
digits, and the old cleaner concatenated every token, so the scraper submitted
`pleasetypethenumbersyouhear344946` instead of `344946` and the challenge rejected it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "scrape"))

# The module imports pydoll at module scope; skip cleanly where it isn't installed (CI
# installs base deps only).
pytest.importorskip("pydoll")

from datadome_slider import _clean_transcript  # noqa: E402


@pytest.mark.parametrize(
    "raw, expected",
    [
        # The exact transcript from the 2026-07-25 run that failed the challenge.
        ("Please type the numbers you hear. 3 4 4 9 4 6", "344946"),
        # Digits spoken as words still map through _NUMWORDS.
        ("three four four nine four six", "344946"),
        ("Please type the numbers you hear. one two three", "123"),
        # Mixed word/numeral, and punctuation between digits.
        ("7, 3, two, 9", "7329"),
        # Instruction only, no digits -> empty (caller treats falsy as a failed solve).
        ("Please type the numbers you hear.", ""),
        ("", ""),
    ],
)
def test_clean_transcript_keeps_only_digits(raw, expected):
    assert _clean_transcript(raw) == expected


def test_instruction_words_never_leak_into_the_answer():
    """Regression guard for the live bug: no alphabetic residue, whatever the preamble."""
    out = _clean_transcript("Please type the numbers you hear. 3 4 4 9 4 6")
    assert out.isdigit()
    assert "please" not in out
