"""Tests for the Cornerstone DNS miner's pure parts (scripts/discover/mine_cornerstone_dns.py).
No network: replies are hand-built packets, the ledger and the pool are tmp files."""

from __future__ import annotations

import asyncio
import csv
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "discover"))

import eightfold_dns_sweep as sweep
import mine_cornerstone_dns as mcd


def _reply(rcode: int, answers: int) -> bytes:
    """A DNS response header: id 1, QR set, the given rcode and answer count."""
    return struct.pack(">HHHHHH", 1, 0x8180 | rcode, 1, answers, 0, 0)


# ---------------------------------------------------------------- reply parsing


def test_an_answer_means_the_host_exists_and_nxdomain_means_it_does_not():
    assert sweep._answered(_reply(0, 1)) is True
    assert sweep._answered(_reply(3, 0)) is False


@pytest.mark.parametrize(
    "payload",
    [
        _reply(0, 0),  # NOERROR with no answer: a name with records but no address
        _reply(2, 0),  # SERVFAIL
        _reply(5, 0),  # REFUSED
        b"",  # nothing came back
        b"\x00\x01",  # a truncated header
    ],
)
def test_an_unsettled_reply_is_never_read_as_absent(payload):
    assert sweep._answered(payload) is None


# ---------------------------------------------------------------- verdicts of a chunk


def test_a_timeout_is_neither_a_hit_nor_absent():
    res = {"pip": True, "nope": False, "slow": None}
    found, unsettled = mcd.split_verdicts(res, ["pip", "nope", "slow"])
    assert found == ["pip.csod.com"]
    assert unsettled == [
        "slow"
    ]  # kept for the `.unresolved` list, not dropped with the absent


def test_controls_must_all_settle_the_way_they_are_known_to():
    live, dead = ["hilton", "abacus"], ["zz1qx"]
    good = {"hilton": True, "abacus": True, "zz1qx": False}
    assert mcd.controls_agree(good, live, dead)
    assert not mcd.controls_agree(
        {**good, "hilton": False}, live, dead
    )  # a real tenant read absent
    assert not mcd.controls_agree(
        {**good, "zz1qx": True}, live, dead
    )  # an invented label resolved
    # an unsettled control is not agreement: a chunk sweeping through a dead resolver is redone
    assert not mcd.controls_agree({**good, "abacus": None}, live, dead)
    assert not mcd.controls_agree({**good, "zz1qx": None}, live, dead)


# ---------------------------------------------------------------- resume reader


def test_a_saved_offset_resumes_there(tmp_path):
    path = tmp_path / "hits.txt.offset"
    mcd.write_offset(path, 4000)
    assert mcd.read_offset(path, total=10_000) == 4000
    assert not list(tmp_path.glob("*.tmp"))  # the replace left no temp file behind


@pytest.mark.parametrize("saved", ["", "  ", "abc", "-5", "12.5"])
def test_an_offset_that_cannot_be_read_restarts_the_list_never_skips_it(
    tmp_path, saved
):
    path = tmp_path / "hits.txt.offset"
    path.write_text(saved)
    assert mcd.read_offset(path, total=10_000) == 0


def test_no_offset_file_starts_at_zero_and_an_offset_past_the_end_means_done(tmp_path):
    path = tmp_path / "hits.txt.offset"
    assert mcd.read_offset(path, total=10_000) == 0
    mcd.write_offset(path, 99_999)
    assert mcd.read_offset(path, total=10_000) == 10_000


# ---------------------------------------------------------------- candidate filter


def test_candidates_drop_held_hosts_by_slug_and_non_production_siblings():
    held = {"colruytgroup", "acme"}
    lines = [
        "colruytgroup.csod.com",  # held
        "ACME.csod.com",  # held, spelt in capitals
        "iam.csod.com",  # new
        "iam.csod.com",  # seen twice
        "abacus-pilot.csod.com",  # non-production sibling
        "abacus-stg.csod.com",  # non-production sibling
        "demo-eu.csod.com",  # non-production token
        "www.example.org",  # not a csod host
        "pilotcorp.csod.com",  # `pilot` inside a word is a customer, not a sibling
        "",
    ]
    kept, seen, n_held, n_nonprod = mcd.select_candidates(lines, held)
    assert kept == ["iam", "pilotcorp"]
    assert (seen, n_held, n_nonprod) == (7, 2, 3)


def test_staging_matches_the_ledger_through_slug_from_not_a_url_parse(
    tmp_path, monkeypatch
):
    ledger = tmp_path / "cornerstone.csv"
    with ledger.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ats", "tenant", "url", "status", "jobs", "checked_at"])
        w.writerow(
            [
                "cornerstone",
                "held-a",
                "https://held-a.csod.com",
                "live",
                3,
                "2026-09-23",
            ]
        )
        w.writerow(
            [
                "cornerstone",
                "",
                "https://Held-B.csod.com/ux/ats/careersite/1/home?c=held-b",
                "dead",
                "",
                "2026-09-23",
            ]
        )
    pool = tmp_path / "wayback-ats" / "cornerstone.csv"
    monkeypatch.setattr(mcd, "LEDGER", ledger)
    monkeypatch.setattr(mcd, "POOL", pool)
    hits = tmp_path / "hits.txt"
    hits.write_text("held-a.csod.com\nheld-b.csod.com\nnewone.csod.com\n")
    assert mcd.stage([hits]) == 0
    rows = list(csv.DictReader(pool.open()))
    assert rows == [
        {"ats": "cornerstone", "tenant": "newone", "url": "https://newone.csod.com"}
    ]


# ---------------------------------------------------------------- output rows


class _Recorder:
    """A file stand-in that records the order of writes and flushes."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.closed = False

    def write(self, text: str) -> None:
        self.events.append(f"write:{text.strip()}")

    def flush(self) -> None:
        self.events.append("flush")


def test_every_hit_row_is_flushed_before_the_next():
    out = _Recorder()
    mcd.write_rows(out, ["a.csod.com", "b.csod.com"])
    assert out.events == ["write:a.csod.com", "flush", "write:b.csod.com", "flush"]


def test_no_rows_writes_nothing():
    out = _Recorder()
    mcd.write_rows(out, [])
    assert out.events == []


def test_the_sweep_closes_its_output_files_when_it_is_cut_short(tmp_path, monkeypatch):
    """A failure inside the sweep loop still closes the hits and unresolved files it opened."""
    words = tmp_path / "words.txt"
    words.write_text("aaa\nbbb\n")
    out = tmp_path / "hits.txt"
    opened: list[object] = []
    real_open = Path.open

    def spy(self, *args, **kwargs):
        f = real_open(self, *args, **kwargs)
        if self.name.startswith("hits.txt"):
            opened.append(f)
        return f

    async def no_socket(_nameserver):
        return object()

    def boom():
        raise RuntimeError("controls unavailable")

    monkeypatch.setattr(Path, "open", spy)
    monkeypatch.setattr(mcd, "_open", no_socket)
    monkeypatch.setattr(mcd, "_controls", boom)
    with pytest.raises(RuntimeError, match="controls unavailable"):
        asyncio.run(mcd.sweep(words, out, concurrency=2))
    assert len(opened) == 2  # the hits file and its `.unresolved` sibling
    assert all(f.closed for f in opened)
