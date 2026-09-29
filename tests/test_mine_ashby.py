"""Tests for the Ashby Wayback and Common Crawl miner's slug reading (scripts/discover/mine_ashby.py).

It is a script under `scripts/discover`, so we put that directory on the path and import it by
name, the way `test_mine_lever.py` does.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "discover"))

import mine_ashby


def test_a_slug_is_read_as_the_scraper_says_a_link_writes_it():
    """The miner unquoted each capture and then cut the slug at the space: Wayback writes
    `Flock%20Safety`, and it was read as `flock`. A `+` is not a space on Ashby, and a link it
    cannot read whole names no Board rather than a prefix (ADR-0280)."""
    cdx = (
        "https://jobs.ashbyhq.com/Flock%20Safety/000b225a-f4af-469e-8b50-691538e882ff\n"
        "https://jobs.ashbyhq.com/ambient.ai?utm_source=x\n"
        "https://api.ashbyhq.com/posting-api/job-board/Elveo%20\n"
        "https://jobs.ashbyhq.com/Blackpoint+Cyber\n"
        "https://jobs.ashbyhq.com/robots.txt\n"
    )
    assert mine_ashby.slugs_from(cdx) == {"flock safety", "ambient.ai", "elveo"}
