"""Tests for the Darwinbox DNS-sieve miner's pure parts (scripts/discover/mine_darwinbox_dns.py).
No network: DNS answers are hand-built packets and the UDP client is a fake."""

from __future__ import annotations

import asyncio
import socket
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "discover"))

import mine_darwinbox_dns as md

WILDCARD = frozenset({"10.0.0.1", "10.0.0.2"})
QNAME = "acme.darwinbox.in"


def _name(dotted: str) -> bytes:
    return (
        b"".join(bytes([len(p)]) + p.encode() for p in dotted.split(".") if p) + b"\x00"
    )


def _cname(target: str) -> tuple[int, bytes]:
    return md._QTYPE_CNAME, _name(target)


def _a(address: str) -> tuple[int, bytes]:
    return md._QTYPE_A, socket.inet_aton(address)


def _packet(
    answers: list[tuple[int, bytes]], *, rcode: int = 0, qname: str = QNAME
) -> bytes:
    """A DNS response for one question with ``answers`` as (type, rdata) records, each named by a
    compression pointer back to the question (offset 12), as real resolvers write them."""
    header = struct.pack(">HHHHHH", 0x1234, 0x8180 | rcode, 1, len(answers), 0, 0)
    question = _name(qname) + struct.pack(">HH", md._QTYPE_A, 1)
    records = b"".join(
        b"\xc0\x0c" + struct.pack(">HHIH", rtype, 1, 60, len(rdata)) + rdata
        for rtype, rdata in answers
    )
    return header + question + records


def _verdict(packet: bytes, wildcard: frozenset[str] = WILDCARD) -> tuple[str, str]:
    answer = md.parse_answer(packet)
    assert answer is not None
    return md.verdict_of(answer, wildcard)


# --- parse_answer ------------------------------------------------------------------------------


def test_cname_to_cloudflare_edge_is_a_tenant():
    packet = _packet([_cname("acme.darwinbox.in.cdn.cloudflare.net"), _a("104.16.0.1")])

    assert md.parse_answer(packet) == (
        "ok",
        ["acme.darwinbox.in.cdn.cloudflare.net"],
        ["104.16.0.1"],
    )
    assert _verdict(packet) == ("cf", "acme.darwinbox.in.cdn.cloudflare.net")


def test_cname_target_is_lowercased_before_the_suffix_test():
    packet = _packet([_cname("ACME.Darwinbox.In.CDN.Cloudflare.NET")])

    assert _verdict(packet) == ("cf", "acme.darwinbox.in.cdn.cloudflare.net")


def test_explicit_address_outside_the_wildcard_set_is_a_tenant():
    packet = _packet([_a("203.0.113.9"), _a("10.0.0.1"), _a("203.0.113.7")])

    assert _verdict(packet) == ("a", "203.0.113.7,203.0.113.9")


def test_wildcard_only_answer_is_no_tenant():
    packet = _packet([_a("10.0.0.2"), _a("10.0.0.1")])

    assert _verdict(packet) == ("wild", "")


def test_cname_elsewhere_is_reported_as_other_service():
    packet = _packet([_cname("shops.example.com"), _a("198.51.100.4")])

    assert _verdict(packet) == ("other", "shops.example.com")


def test_cloudflare_cname_wins_over_an_address_and_a_second_cname():
    packet = _packet(
        [
            _cname("shops.example.com"),
            _cname("acme.darwinbox.in.cdn.cloudflare.net"),
            _a("10.0.0.1"),
        ]
    )

    assert _verdict(packet) == ("cf", "acme.darwinbox.in.cdn.cloudflare.net")


def test_nxdomain_is_a_conclusive_no():
    packet = _packet([], rcode=md._RCODE_NXDOMAIN)

    assert md.parse_answer(packet) == ("nx", [], [])
    assert _verdict(packet) == ("nx", "")


@pytest.mark.parametrize("rcode", [2, 4, 5])  # SERVFAIL, NOTIMP, REFUSED
def test_a_failed_reply_is_unsettled_not_no_tenant(rcode):
    assert md.parse_answer(_packet([_a("10.0.0.1")], rcode=rcode)) is None


def test_noerror_with_no_records_is_unsettled():
    # a resolver that lost the lookup answers NOERROR and nothing, which is not NXDOMAIN
    assert md.parse_answer(_packet([])) is None


def test_truncated_and_malformed_packets_are_unsettled():
    good = _packet([_cname("acme.darwinbox.in.cdn.cloudflare.net"), _a("104.16.0.1")])

    assert md.parse_answer(b"") is None
    assert md.parse_answer(good[:11]) is None  # shorter than a header
    assert md.parse_answer(good[:30]) is None  # cut inside the question
    assert md.parse_answer(good[:45]) is None  # cut inside the first record
    assert md.parse_answer(good[:-2]) is None  # cut inside the last A record's address


def test_a_pointer_loop_in_an_answer_is_unsettled_not_a_crash():
    header = struct.pack(">HHHHHH", 1, 0x8180, 1, 1, 0, 0)
    # the question's own name is a pointer to itself
    assert md.parse_answer(header + b"\xc0\x0c" + b"\x00" * 8) is None


# --- _read_name --------------------------------------------------------------------------------


def test_read_name_reads_a_plain_name_and_the_offset_past_it():
    payload = b"\x00\x00" + _name("acme.darwinbox.in") + b"tail"

    name, end = md._read_name(payload, 2)

    assert name == "acme.darwinbox.in"
    assert payload[end:] == b"tail"


def test_read_name_follows_a_compression_pointer_and_ends_after_its_two_octets():
    base = b"\x00" * 4 + _name("darwinbox.in")  # the shared suffix sits at offset 4
    payload = base + b"\x03www" + b"\xc0\x04" + b"tail"
    start = len(base)

    name, end = md._read_name(payload, start)

    assert name == "www.darwinbox.in"
    assert payload[end:] == b"tail"  # the record's own bytes stop after the pointer


def test_read_name_refuses_a_pointer_loop():
    # offset 0 points at offset 2, which points back at offset 0
    payload = b"\xc0\x02\xc0\x00"

    with pytest.raises(ValueError, match="compression loop"):
        md._read_name(payload, 0)


# --- verdict_of --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (("nx", [], []), ("nx", "")),
        (("ok", ["x.cdn.cloudflare.net"], []), ("cf", "x.cdn.cloudflare.net")),
        (
            ("ok", ["a.example.com", "x.cdn.cloudflare.net"], []),
            ("cf", "x.cdn.cloudflare.net"),
        ),
        (("ok", ["a.example.com"], ["10.0.0.1"]), ("other", "a.example.com")),
        (("ok", [], ["192.0.2.1"]), ("a", "192.0.2.1")),
        (("ok", [], ["10.0.0.1", "10.0.0.2"]), ("wild", "")),
        (("ok", [], ["10.0.0.1", "10.0.0.1"]), ("wild", "")),
    ],
)
def test_verdict_of(answer, expected):
    assert md.verdict_of(answer, WILDCARD) == expected


def test_verdict_of_reads_each_tld_against_its_own_wildcard():
    answer = ("ok", [], ["10.0.0.9"])

    assert md.verdict_of(answer, frozenset({"10.0.0.9"})) == ("wild", "")
    assert md.verdict_of(answer, WILDCARD) == ("a", "10.0.0.9")


# --- _done_pairs / _read_labels ----------------------------------------------------------------


def test_done_pairs_of_a_missing_file_is_empty(tmp_path):
    assert md._done_pairs(tmp_path / "nothing.tsv") == set()


def test_done_pairs_keeps_settled_rows_and_asks_fails_again(tmp_path):
    out = tmp_path / "out.tsv"
    out.write_text(
        "acme\tin\tcf\tacme.darwinbox.in.cdn.cloudflare.net\n"
        "acme\tcom\tnx\t\n"
        "beta\tin\twild\t\n"
        "beta\tcom\tfail\t\n"
        "torn line without tabs\n"
        "\n"
        "gamma\tin\n",  # a row cut short by a crash: no verdict, so not done
        encoding="utf-8",
    )

    assert md._done_pairs(out) == {("acme", "in"), ("acme", "com"), ("beta", "in")}


def test_read_labels_lowercases_strips_dots_and_dedupes_in_order(tmp_path):
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    first.write_text("Acme\nbeta  gamma\n\n.delta.\n", encoding="utf-8")
    second.write_text("BETA\nepsilon\nacme\n", encoding="utf-8")

    assert md._read_labels([str(first), str(second)]) == [
        "acme",
        "beta",
        "gamma",
        "delta",
        "epsilon",
    ]


# --- an unsettled reply is never read as "no tenant" -------------------------------------------


def _run_sieve(monkeypatch, tmp_path, labels, answer_of, *, tlds=("in",)):
    """Run ``sieve`` with the UDP client and wildcard derivation faked. ``answer_of(name)`` gives
    the parsed answer (or None for an unsettled one) each query would have got."""

    async def fake_open(nameserver):
        return md._Client()

    async def fake_ask(client, name, timeout):
        return answer_of(name)

    async def fake_wildcards(clients, tld_list, sem):
        return {tld: WILDCARD for tld in tld_list}

    async def no_sleep(seconds):
        return None

    monkeypatch.setattr(md, "_open", fake_open)
    monkeypatch.setattr(md, "_ask", fake_ask)
    monkeypatch.setattr(md, "derive_wildcards", fake_wildcards)
    monkeypatch.setattr(md.asyncio, "sleep", no_sleep)
    out = tmp_path / "out.tsv"
    counts = asyncio.run(md.sieve(labels, list(tlds), out, 5, ["192.0.2.53"]))
    return counts, out


def test_answer_for_retries_on_the_next_nameserver_and_gives_up_as_none(monkeypatch):
    asked: list[md._Client] = []
    clients = [md._Client(), md._Client(), md._Client()]

    async def fake_ask(client, name, timeout):
        asked.append(client)
        return None if len(asked) < 3 else ("ok", [], ["10.0.0.1"])

    async def no_sleep(seconds):
        return None

    monkeypatch.setattr(md, "_ask", fake_ask)
    monkeypatch.setattr(md.asyncio, "sleep", no_sleep)

    answer = asyncio.run(md.answer_for(clients, 0, QNAME, asyncio.Semaphore(1)))

    assert answer == ("ok", [], ["10.0.0.1"])
    assert asked == clients  # rotated through three nameservers

    async def never_answers(client, name, timeout):
        return None

    monkeypatch.setattr(md, "_ask", never_answers)

    assert asyncio.run(md.answer_for(clients, 0, QNAME, asyncio.Semaphore(1))) is None


def test_an_unanswered_label_is_a_fail_row_and_is_asked_again(monkeypatch, tmp_path):
    counts, out = _run_sieve(monkeypatch, tmp_path, ["acme"], lambda name: None)

    assert counts == {"fail": 1}
    assert out.read_text(encoding="utf-8") == "acme\tin\tfail\t\n"
    assert md._done_pairs(out) == set()  # so the next run asks it again


def test_only_a_conclusive_reply_settles_a_label(monkeypatch, tmp_path):
    answers = {
        "servfail.darwinbox.in": None,
        "gone.darwinbox.in": ("nx", [], []),
        "made-up.darwinbox.in": ("ok", [], ["10.0.0.1"]),
        "real.darwinbox.in": ("ok", ["real.darwinbox.in.cdn.cloudflare.net"], []),
    }

    counts, out = _run_sieve(
        monkeypatch, tmp_path, ["servfail", "gone", "made-up", "real"], answers.get
    )

    assert counts == {"fail": 1, "nx": 1, "wild": 1, "cf": 1}
    assert md._done_pairs(out) == {("gone", "in"), ("made-up", "in"), ("real", "in")}


def test_resuming_skips_settled_pairs_and_writes_only_new_ones(monkeypatch, tmp_path):
    out = tmp_path / "out.tsv"
    out.write_text("acme\tin\twild\t\n", encoding="utf-8")

    counts, _ = _run_sieve(
        monkeypatch,
        tmp_path,
        ["acme", "beta"],
        lambda name: ("ok", ["x.cdn.cloudflare.net"], []),
    )

    assert counts == {"cf": 1}
    assert out.read_text(encoding="utf-8").splitlines() == [
        "acme\tin\twild\t",
        "beta\tin\tcf\tx.cdn.cloudflare.net",
    ]


# --- streaming: a hit survives an abort ---------------------------------------------------------


def test_a_canary_abort_keeps_every_row_found_so_far(monkeypatch, tmp_path):
    async def canaries_fail(clients, wildcards, sem):
        return False

    monkeypatch.setattr(md, "canaries_hold", canaries_fail)
    monkeypatch.setattr(md, "_CANARY_EVERY", 1)

    with pytest.raises(SystemExit, match="canaries keep failing"):
        _run_sieve(
            monkeypatch,
            tmp_path,
            ["acme", "beta"],
            lambda name: ("ok", ["x.cdn.cloudflare.net"], []),
        )

    rows = (tmp_path / "out.tsv").read_text(encoding="utf-8").splitlines()
    assert rows  # the row that tripped the canary was written and flushed
    assert all(row.split("\t")[2] == "cf" for row in rows)


# --- the query packet --------------------------------------------------------------------------


def test_query_packet_asks_one_a_question_with_recursion_desired():
    packet = md._query_packet(0xBEEF, QNAME)

    qid, flags, qdcount, ancount, _, _ = struct.unpack(">HHHHHH", packet[:12])
    assert (qid, flags, qdcount, ancount) == (0xBEEF, 0x0100, 1, 0)
    assert packet[12:] == _name(QNAME) + struct.pack(">HH", md._QTYPE_A, 1)
