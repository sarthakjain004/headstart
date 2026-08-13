"""Tests for the scrape-shard join (headstart.ingest.scrape_join, ADR-0026).

The union invariant: every shard's ``{ats}.jsonl`` is concatenated per ATS into one snapshot, so
sync sees the full scraped-Board set. Streaming concat; downstream dedups by id.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import headstart.ingest.scrape_join as js


def _shard(frags: Path, k: int, files: dict[str, list[str]]) -> None:
    d = frags / f"shard-{k}"
    d.mkdir(parents=True, exist_ok=True)
    for name, lines in files.items():
        (d / name).write_text("".join(line + "\n" for line in lines), encoding="utf-8")


def _run(shards: Path, out: Path) -> None:
    old = sys.argv
    # `--unauthoritative-boards` pinned under the test's dir: it defaults to the repo's real
    # data/state/, and a test must never write there.
    sys.argv = [
        "scrape_join",
        "--shards",
        str(shards),
        "--out",
        str(out),
        "--unauthoritative-boards",
        str(out.parent / "unauthoritative_boards.json"),
    ]
    try:
        assert js.main() == 0
    finally:
        sys.argv = old


def test_join_unions_per_ats_across_shards(tmp_path):
    frags = tmp_path / "frags"
    _shard(
        frags,
        0,
        {
            "lever.jsonl": ['{"id":"lever:a:1"}', '{"id":"lever:a:2"}'],
            "greenhouse.jsonl": ['{"id":"gh:x:1"}'],
        },
    )
    _shard(frags, 1, {"lever.jsonl": ['{"id":"lever:b:1"}']})
    out = tmp_path / "jobs"

    _run(frags, out)

    lever = out.joinpath("lever.jsonl").read_text().splitlines()
    assert len(lever) == 3  # 2 from shard-0 + 1 from shard-1
    assert set(lever) == {
        '{"id":"lever:a:1"}',
        '{"id":"lever:a:2"}',
        '{"id":"lever:b:1"}',
    }
    assert out.joinpath("greenhouse.jsonl").read_text().splitlines() == [
        '{"id":"gh:x:1"}'
    ]


def test_join_no_shards_is_empty(tmp_path):
    out = tmp_path / "jobs"
    _run(tmp_path / "absent", out)
    assert list(out.glob("*.jsonl")) == []


def test_unauthoritative_boards_are_keyed_the_way_the_index_keys_boards(tmp_path):
    """The shard reports key outcomes `{ats}:{slug}`, but eviction scope is keyed by `board_key()`
    (ADR-0049). Those are the same string for greenhouse and eightfold and NOT for workday, whose
    slug is the whole careers URL — so writing the raw key through would look like protection
    while never matching anything."""
    reports = [
        {"errors": {"greenhouse:acme": "HTTP 429"}},
        {
            "errors": {
                "workday:https://x.wd1.myworkdayjobs.com/Careers": "RequestsError: 429",
                "eightfold:nvidia.eightfold.ai": "HTTP 500",
            }
        },
    ]
    out = tmp_path / "unauthoritative_boards.json"
    written = js.write_unauthoritative_boards(reports, out)

    assert written == {
        "greenhouse:acme": "HTTP 429",
        "workday:x/Careers": "RequestsError: 429",
        "eightfold:nvidia.eightfold.ai": "HTTP 500",
    }
    assert json.loads(out.read_text(encoding="utf-8")) == written


def test_the_file_is_rewritten_even_when_every_list_was_authoritative(tmp_path):
    """`data/state` round-trips through the HF dataset, so skipping the write on a clean run would
    leave the PREVIOUS run's Boards in place and protect Boards that scraped fine this time."""
    out = tmp_path / "unauthoritative_boards.json"
    out.write_text('{"greenhouse:stale": "from a previous run"}', encoding="utf-8")

    assert js.write_unauthoritative_boards([{"errors": {}}], out) == {}
    assert json.loads(out.read_text(encoding="utf-8")) == {}


def test_an_unresolvable_key_is_dropped_not_written_through(tmp_path):
    """A key no scraper can turn into a board_key is dropped with a warning. Writing it through
    unconverted would sit in the file looking effective while matching no Board."""
    out = tmp_path / "unauthoritative_boards.json"
    written = js.write_unauthoritative_boards(
        [{"errors": {"notanats:whatever": "boom", "greenhouse:real": "HTTP 500"}}], out
    )
    assert written == {"greenhouse:real": "HTTP 500"}


def test_the_written_keys_are_what_the_index_actually_looks_up(tmp_path):
    """The seam between the two halves: `write_unauthoritative_boards` emits `board_key()` casing
    and `unauthoritative_boards` lowercases, because `index sync` matches
    `board.lower() in unauthoritative`. Each half is tested alone; this pins the contract BETWEEN
    them, which is where a rename or a casing change would silently stop protecting anything."""
    from headstart.ingest.index_plan import unauthoritative_boards

    out = tmp_path / "unauthoritative_boards.json"
    js.write_unauthoritative_boards(
        [{"errors": {"workday:https://x.wd1.myworkdayjobs.com/Careers": "429"}}], out
    )
    unauthoritative = unauthoritative_boards(out)

    # What `_scraped_boards` would produce for a Job id on that Board, via `board_key()`.
    scope_entry = "workday:x/Careers"
    assert scope_entry.lower() in unauthoritative, (
        f"{scope_entry} would NOT be protected: file holds {sorted(unauthoritative)}"
    )


def test_a_scraper_that_truncates_without_raising_still_reaches_the_file(tmp_path):
    """The flap's actual shape, and the one an `errors`-only file could never catch.

    eightfold gives up mid-pagination and returns what it has, so `harvest` records no error and
    the Board emits job lines — it looks fully scraped. Before ADR-0053 that made its missing
    postings indistinguishable from delistings. `truncated` is the channel that carries it, and
    the join folds both into the one question sync asks: is this Board's list authoritative?"""
    report = {
        "errors": {},
        "truncated": {
            "eightfold:nvidia.eightfold.ai": "HTTP 429 on page 4 — got 300 of 850"
        },
    }
    written = js.write_unauthoritative_boards([report], tmp_path / "e.json")
    assert "eightfold:nvidia.eightfold.ai" in written


def test_a_raise_and_a_truncation_both_land(tmp_path):
    """Both mean 'do not evict against this list'. A Board that raised writes no lines and was
    already out of scope; the truncated one is the case that actually flapped."""
    report = {
        "errors": {"workday:https://x.wd1.myworkdayjobs.com/Careers": "429"},
        "truncated": {"eightfold:nvidia.eightfold.ai": "cut short"},
    }
    written = js.write_unauthoritative_boards([report], tmp_path / "e.json")
    assert set(written) == {"workday:x/Careers", "eightfold:nvidia.eightfold.ai"}
