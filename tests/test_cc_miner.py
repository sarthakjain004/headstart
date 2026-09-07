"""Tests for the Common Crawl tenant miner's Workday capture (scripts/discover/cc_miner.py).

Only the Workday branch is covered, because it is the one that reads a *path segment* rather than
a subdomain label, and that is where both known defects lived:

1. `[a-zA-Z0-9_-]+` cannot match a dot, so `.../robots.txt` was captured as the Board
   `.../robots` — 1,975 such rows in the ledger alone. The guard is a trailing lookahead that
   refuses to stop before a dot, so it rejects the open set of well-known files rather than a
   list someone must keep extending; a closed list shipped first and already missed
   `apple-touch-icon.png`, `crossdomain.xml` and `sw.js`.
2. The obvious fix for the *other* half — widening the locale prefix to consume a bare `es/` as
   well as `en-US/` — silently broke every two-letter Board. A Workday deep link is
   `{host}/{site}/job/{...}`, so the wider pattern ate the site and captured the path marker:
   `howard.../hu/job/...` went from `hu` to `job` (then dropped by `BLOCK`, making the Board
   undiscoverable) and `browardcollege.../pt/details/...` minted a phantom Board `details`.
   `load_active_companies(min_jobs=0)` counts 100 Scrapable Boards with a two-letter Workday
   site, 7 of them an ISO-639-1 code.

Both were found by review rather than by a test, because this module had none. The second is the
reason the locale prefix here stays narrow, and the deep-link cases below are what pin it.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re

import pytest

_SRC = (
    pathlib.Path(__file__).resolve().parents[1] / "scripts" / "discover" / "cc_miner.py"
)


@pytest.fixture(scope="module")
def miner():
    """`cc_miner` is a script, not an installed module, so load it from its path."""
    spec = importlib.util.spec_from_file_location("cc_miner", _SRC)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _site(miner, url: str) -> str | None:
    """What the miner would record as this URL's Board, or None if it drops it.

    A URL the pattern does not match at all and one `tenant_from` rejects both read as None here,
    and conflating them would let the well-known-file cases pass vacuously if the pattern were
    ever broken outright. `test_the_pattern_still_matches_what_it_is_meant_to_judge` pins that.
    """
    pattern = re.compile(miner.ATS_PATTERNS["workday"]["patterns"][0])
    match = pattern.search(url)
    if not match:
        return None
    got = miner.tenant_from("workday", match)
    return got[0] if got else None


H = "https://acme.wd1.myworkdayjobs.com"


@pytest.mark.parametrize(
    "filename",
    [
        "robots.txt",
        "llms.txt",
        "llms-full.txt",
        "sitemap.xml",
        "security.txt",
        "ads.txt",
        # the three a closed list missed, and the reason the guard is a lookahead
        "apple-touch-icon.png",
        "crossdomain.xml",
        "sw.js",
    ],
)
def test_a_well_known_file_at_the_root_is_not_a_board(miner, filename):
    """The regex sees `robots` and cannot see the `.txt`, so the *name* has to be rejected."""
    assert _site(miner, f"{H}/{filename}") is None


def test_a_two_letter_site_survives_its_own_deep_link(miner):
    """The regression that a wider locale prefix causes, on the Board it was measured on.

    `hu` is Howard University's site, not Hungarian. Consuming it as a locale captures `job`
    instead, which `BLOCK` then drops — so the Board disappears from discovery entirely, and
    Common Crawl's captures are overwhelmingly deep links like this one.
    """
    board = "https://howard.wd1.myworkdayjobs.com/hu"
    assert _site(miner, f"{board}/job/Washington/Research-Scientist_JR1") == board
    assert _site(miner, board) == board


def test_a_two_letter_site_does_not_mint_a_phantom_board_from_its_path(miner):
    """The same bug's other half: `pt` eaten as a locale captured the path marker `details`,
    which is in neither block set and so would have been recorded as a real Board."""
    board = "https://browardcollege.wd1.myworkdayjobs.com/pt"
    assert _site(miner, f"{board}/details/X_R-2") == board


def test_the_hyphenated_locale_is_still_skipped(miner):
    """`/en-US/Site` is unambiguous — a bare language code is not, which is the whole point.

    Deliberately not `Careers` as the site: `careers` is in `BLOCK`, so the miner drops it. That
    is a **pre-existing** defect, not this change's — the live ledger holds 1,032 rows whose
    Workday site is exactly `careers`, every one found by another feeder. Out of scope here, but
    it means `cc_miner` is blind to one of Workday's commonest site names.
    """
    assert _site(miner, f"{H}/en-US/Global_Openings") == f"{H}/Global_Openings"


def test_an_ordinary_board_and_its_deep_link_agree(miner):
    """Both spellings must collapse to one Board or discovery double-counts it."""
    assert _site(miner, f"{H}/My_Site-Name") == f"{H}/My_Site-Name"
    assert (
        _site(miner, f"{H}/My_Site-Name/job/Madrid/Driver_R-9") == f"{H}/My_Site-Name"
    )


def test_an_infra_segment_is_still_dropped(miner):
    """`BLOCK` predates this change and must keep working — `cxs` and `wday` are API paths."""
    assert _site(miner, f"{H}/wday") is None
    assert _site(miner, f"{H}/cxs") is None


def test_the_pattern_still_matches_what_it_is_meant_to_judge(miner):
    """Guards the vacuity `_site` could otherwise hide.

    Every well-known-file case above asserts None, which a pattern that matched *nothing* would
    also satisfy. So assert the pattern really does engage with those URLs — the rejection has to
    come from the capture stopping at the dot, not from the host half failing to match.
    """
    pattern = re.compile(miner.ATS_PATTERNS["workday"]["patterns"][0])
    assert pattern.search(f"{H}/Careers_Site") is not None
    # It engages with the host, then declines to capture a dotted filename as the site.
    assert re.search(
        r"https?://([a-z0-9-]+\.wd\d+\.myworkdayjobs\.com)", f"{H}/robots.txt"
    )
    assert pattern.search(f"{H}/robots.txt") is None
