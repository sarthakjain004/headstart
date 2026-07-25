"""Tests for headstart.ingest.scrape_run's scrape-shard (``--assignment``) mode (ADR-0026).

The shard mode must read the planner's board list verbatim and scrape exactly those boards into its
own fragment dir — no slice selection. ``scrape_all`` is faked, so no network / real scraping.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import headstart.ingest.scrape_run as nh


class _Result:
    def __init__(self, n: int) -> None:
        self.unique = n
        self.boards = n
        self.errors: dict[str, str] = {}


def test_assignment_scrapes_exactly_the_listed_boards(tmp_path, monkeypatch):
    captured: dict = {}

    def fake_scrape_all(companies, jobs_dir, progress_every=200):
        captured["companies"] = list(companies)
        captured["jobs_dir"] = jobs_dir
        return _Result(len(companies))

    monkeypatch.setattr(nh, "scrape_all", fake_scrape_all)

    assignment = tmp_path / "shard-0.jsonl"
    rows = [("lever", "acme", "Acme"), ("workday", "big", "Big"), ("keka", "x", None)]
    assignment.write_text(
        "".join(
            json.dumps({"ats": a, "slug": s, "name": n}) + "\n" for a, s, n in rows
        ),
        encoding="utf-8",
    )
    outdir = tmp_path / "frag"
    monkeypatch.setattr(
        sys,
        "argv",
        ["scrape_run", "--assignment", str(assignment), "--outdir", str(outdir)],
    )

    assert nh.main() == 0
    got = [(c.ats, c.slug, c.name) for c in captured["companies"]]
    assert got == rows  # exact board list, in order
    assert (
        Path(captured["jobs_dir"]) == outdir
    )  # scraped into the shard's own fragment dir
