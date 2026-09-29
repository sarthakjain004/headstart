"""Tests for the Avature DNS miner's reply reader (scripts/discover/mine_avature_dns.py). No network:
the replies are hand-built DNS packets."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "discover"))

import mine_avature_dns as mad

_QNAME = b"\x06sodexo\x07avature\x03net\x00"


def _reply(flags: int, answers: bytes = b"", ancount: int = 0) -> bytes:
    header = struct.pack(">HHHHHH", 1, flags, 1, ancount, 0, 0)
    return header + _QNAME + struct.pack(">HH", 1, 1) + answers


def _cname_record(target: bytes) -> bytes:
    # name = pointer to the question at offset 12, type CNAME, class IN, ttl 60
    return b"\xc0\x0c" + struct.pack(">HHIH", 5, 1, 60, len(target)) + target


def _a_record() -> bytes:
    return b"\xc0\x0c" + struct.pack(">HHIH", 1, 1, 60, 4) + bytes([74, 217, 56, 219])


def test_a_made_up_label_answers_noerror_with_nothing_and_reads_as_absent():
    assert mad._verdict(_reply(0x8180)) == (False, "")


def test_nxdomain_reads_as_absent():
    assert mad._verdict(_reply(0x8183)) == (False, "")


def test_a_cname_answer_reads_as_present_with_its_target():
    target = b"\x0fiatsapp-prod-en\x07avature\x03net\x00"
    answers = _cname_record(target) + _a_record()
    assert mad._verdict(_reply(0x8180, answers, ancount=2)) == (
        True,
        "iatsapp-prod-en.avature.net",
    )


def test_a_bare_a_answer_reads_as_present_with_no_target():
    assert mad._verdict(_reply(0x8180, _a_record(), ancount=1)) == (True, "")


def test_servfail_refused_and_truncated_replies_are_unsettled_never_absent():
    assert mad._verdict(_reply(0x8182)) == (None, "")  # SERVFAIL
    assert mad._verdict(_reply(0x8185)) == (None, "")  # REFUSED
    assert mad._verdict(_reply(0x8380)) == (None, "")  # TC bit, no answers
    assert mad._verdict(b"\x00\x01") == (None, "")  # shorter than a header
