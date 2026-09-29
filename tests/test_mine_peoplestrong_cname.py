"""Tests for `scripts/discover/mine_peoplestrong_cname.py`: the CNAME sieve's pure parts, the
candidate filter and the listing classifier, with no network."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import dns.flags
import dns.message
import dns.name
import dns.rcode
import dns.rdatatype
import dns.rrset
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "discover"))

import mine_peoplestrong_cname as mine

from headstart.scrapers.peoplestrong import PeopleStrongScraper

NAME = "cholacareers.peoplestrong.com"
DENY_PAGE = b"Request forbidden by administrative rules."


def _answer(name=NAME, *, rcode=dns.rcode.NOERROR, records=()):
    """A DNS response to a CNAME query for ``name`` as wire bytes; ``records`` are
    (rdtype, rdata text) pairs for its answer section."""
    response = dns.message.make_response(dns.message.make_query(name, "CNAME"))
    response.set_rcode(rcode)
    for rdtype, text in records:
        owner = dns.name.from_text(name)  # absolute: a relative owner cannot go to wire
        response.answer.append(dns.rrset.from_text(owner, 60, "IN", rdtype, text))
    return response.to_wire()


# --- _query_packet -------------------------------------------------------------------------


def test_the_query_asks_for_a_cname_with_recursion_desired():
    message = dns.message.from_wire(mine._query_packet(0x1234, NAME))
    (question,) = message.question
    assert message.id == 0x1234
    assert message.flags & dns.flags.RD
    assert question.name == dns.name.from_text(NAME)
    assert question.rdtype == dns.rdatatype.CNAME


def test_a_trailing_dot_names_the_same_question():
    assert mine._query_packet(7, NAME + ".") == mine._query_packet(7, NAME)


def test_a_non_ascii_label_is_asked_in_its_punycode_form():
    message = dns.message.from_wire(mine._query_packet(1, "café.peoplestrong.com"))
    assert message.question[0].name == dns.name.from_text(
        "xn--caf-dma.peoplestrong.com"
    )


# --- _verdict ------------------------------------------------------------------------------


def test_a_cname_into_impervas_edge_is_a_tenant():
    wire = _answer(records=[("CNAME", "lzikfok.ng.impervadns.net.")])
    assert mine._verdict(wire) == ("tenant", "lzikfok.ng.impervadns.net")


def test_the_cname_target_is_read_case_insensitively():
    wire = _answer(records=[("CNAME", "LZIKFOK.NG.ImpervaDNS.NET.")])
    assert mine._verdict(wire) == ("tenant", "lzikfok.ng.impervadns.net")


def test_a_cname_elsewhere_is_not_a_tenant():
    wire = _answer(records=[("CNAME", "portal.example.com.")])
    assert mine._verdict(wire) == ("other-cname", "portal.example.com")


@pytest.mark.parametrize("records", [[], [("A", "34.100.237.70")]])
def test_noerror_without_a_cname_is_the_wildcard(records):
    assert mine._verdict(_answer(records=records)) == ("wildcard", "")


def test_nxdomain_is_a_verdict_of_its_own():
    assert mine._verdict(_answer(rcode=dns.rcode.NXDOMAIN)) == ("nxdomain", "")


@pytest.mark.parametrize("rcode", [dns.rcode.SERVFAIL, dns.rcode.REFUSED])
def test_a_failed_answer_is_no_verdict_and_never_no_cname(rcode):
    assert mine._verdict(_answer(rcode=rcode)) is None


@pytest.mark.parametrize("garbage", [b"", b"\x00", b"not a dns packet at all"])
def test_a_packet_that_does_not_parse_is_no_verdict(garbage):
    assert mine._verdict(garbage) is None


def test_a_truncated_answer_is_no_verdict():
    wire = _answer(records=[("CNAME", "lzikfok.ng.impervadns.net.")])
    assert mine._verdict(wire[:-8]) is None


# --- _ask: a lost query is unsettled, never "no CNAME" ------------------------------------


class _Transport:
    """A datagram transport that answers with ``reply(packet)`` bytes, or stays silent."""

    def __init__(self, client, reply=None, error=None):
        self.client, self.reply, self.error = client, reply, error

    def sendto(self, packet):
        if self.error:
            raise self.error
        if self.reply is not None:
            query = dns.message.from_wire(packet)
            self.client.datagram_received(self.reply(query), None)


def _client(**kwargs):
    client = mine._Client()
    client.transport = _Transport(client, **kwargs)
    return client


def _respond(rcode=dns.rcode.NOERROR, records=()):
    def reply(query):
        response = dns.message.make_response(query)
        response.set_rcode(rcode)
        for rdtype, text in records:
            response.answer.append(
                dns.rrset.from_text(query.question[0].name, 60, "IN", rdtype, text)
            )
        return response.to_wire()

    return reply


def test_an_answered_query_is_read():
    client = _client(reply=_respond(records=[("CNAME", "x.ng.impervadns.net.")]))
    assert asyncio.run(mine._ask(client, NAME, 1.0)) == (
        "tenant",
        "x.ng.impervadns.net",
    )
    assert client.pending == {}


def test_a_query_nobody_answers_is_unsettled_not_a_wildcard():
    client = _client()
    assert asyncio.run(mine._ask(client, NAME, 0.01)) is None
    assert client.pending == {}


def test_a_servfail_is_unsettled_not_a_wildcard():
    client = _client(reply=_respond(rcode=dns.rcode.SERVFAIL))
    assert asyncio.run(mine._ask(client, NAME, 1.0)) is None


def test_a_socket_error_is_unsettled_not_a_wildcard():
    client = _client(error=OSError("network unreachable"))
    assert asyncio.run(mine._ask(client, NAME, 1.0)) is None
    assert client.pending == {}


# --- _load_done / _held_labels / _candidates -----------------------------------------------


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def test_load_done_reads_one_column_and_skips_the_header_and_blank_rows(tmp_path):
    out = _write(
        tmp_path / "out.csv", "label,verdict,target\na,tenant,x\n\nb,wildcard,\n"
    )
    assert mine._load_done(out, 0) == {"a", "b"}
    assert mine._load_done(out, 1) == {"tenant", "wildcard"}


def test_load_done_of_a_file_not_yet_written_is_empty(tmp_path):
    assert mine._load_done(tmp_path / "missing.csv", 0) == set()


def test_held_labels_are_the_scrapers_own_spelling(tmp_path):
    ledger = _write(
        tmp_path / "peoplestrong.csv",
        "ats,tenant,url,status,jobs,checked_at\n"
        "peoplestrong,Careers-BMWTechWorks,https://Careers-BMWTechWorks.peoplestrong.com,live,3,2026-09-25\n"
        "peoplestrong,https://oyo.peoplestrong.com/x,https://oyo.peoplestrong.com,dead,,2026-09-25\n"
        "peoplestrong,plain,,dead,,2026-09-25\n",
    )
    assert mine._held_labels(ledger) == {"careers-bmwtechworks", "oyo", "plain"}
    assert mine._held_labels(tmp_path / "missing.csv") == set()


def test_the_committed_ledger_is_found_and_read_from_the_repo_root():
    held = mine._held_labels()
    assert mine.LEDGER.is_file()
    assert held
    assert all(label == label.lower() and "." not in label for label in held)


def test_candidates_drop_blanks_repeats_done_and_held_labels_lowercased(tmp_path):
    cands = _write(
        tmp_path / "cands.txt",
        "  Alpha\n\nbeta\nALPHA\ngamma\ndelta\nepsilon\n",
    )
    out = _write(
        tmp_path / "out.csv", "ats,tenant,url,status,jobs\npeoplestrong,beta,u,dead,\n"
    )
    assert mine._candidates(cands, out, col=1, held={"gamma"}) == [
        "alpha",
        "delta",
        "epsilon",
    ]


def test_candidates_against_no_output_yet_are_everything_not_held(tmp_path):
    cands = _write(tmp_path / "cands.txt", "a\nb\n")
    assert mine._candidates(cands, tmp_path / "none.csv", col=0, held={"b"}) == ["a"]


# --- classify / verify: the listing response -----------------------------------------------


def _listing(status, body):
    content = body if isinstance(body, bytes) else json.dumps(body).encode()
    return SimpleNamespace(status_code=status, content=content, text=content.decode())


def test_a_total_records_integer_is_a_live_board_with_that_count():
    body = {"totalRecords": 7, "response": [{}], "messageCode": {"code": 200}}
    assert mine.classify(_listing(200, body)) == ("live", 7)


def test_an_empty_portal_is_live_at_zero():
    body = {"totalRecords": 0, "response": None, "messageCode": {"code": 200}}
    assert mine.classify(_listing(200, body)) == ("live", 0)


def test_the_deny_page_is_denied_once_never_dead():
    status, jobs = mine.classify(_listing(403, DENY_PAGE))
    assert (status, jobs) == (mine.DENIED_ONCE, None)
    assert status != "dead"


def test_a_host_that_is_no_registered_portal_is_dead():
    body = {
        "response": None,
        "messageCode": {"code": 201, "messages": "Inside getTpUrl(...)"},
    }
    assert mine.classify(_listing(200, body)) == ("dead", None)


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (403, b"<html>some other wall</html>"),
        (200, DENY_PAGE),  # the deny page is a 403; a 200 carrying it is not one
        (200, b"<html>PeopleStrong LMS</html>"),
        (200, {"totalRecords": "5"}),
        (200, {"response": None, "messageCode": {"code": 500, "messages": "boom"}}),
        (200, [1, 2, 3]),
        (429, b""),
        (500, b"oops"),
        (502, b"bad gateway"),
    ],
)
def test_any_other_answer_is_unknown(status, body):
    assert mine.classify(_listing(status, body)) == ("unknown", None)


class _Pace:
    def __init__(self):
        self.waited, self.rested = 0, []

    def wait(self):
        self.waited += 1

    def rest(self, seconds):
        self.rested.append(seconds)


def _fetching(monkeypatch, *answers):
    """Make `http.fetch` answer each call with the next of ``answers`` (an exception is
    raised); return the list of calls."""
    calls, queue = [], list(answers)

    def fetch(method, url, **kwargs):
        calls.append((method, url, kwargs))
        answer = queue.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(mine.http, "fetch", fetch)
    return calls


def test_verify_posts_the_scrapers_own_limit_one_listing_url(monkeypatch):
    calls = _fetching(monkeypatch, _listing(200, {"totalRecords": 3}))
    assert mine.verify("cholacareers", _Pace()) == ("cholacareers", "live", 3)
    ((method, url, kwargs),) = calls
    assert method == "POST"
    assert url == PeopleStrongScraper("cholacareers").url(limit=1)
    assert "limit=1" in url
    assert kwargs["json"] == {}


def test_verify_writes_a_single_deny_as_denied_once(monkeypatch):
    _fetching(monkeypatch, _listing(403, DENY_PAGE))
    assert mine.verify("gone", _Pace()) == ("gone", mine.DENIED_ONCE, None)


def test_verify_rests_out_a_429_window_and_asks_again(monkeypatch):
    calls = _fetching(
        monkeypatch, _listing(429, b""), _listing(200, {"totalRecords": 1})
    )
    pace = _Pace()
    assert mine.verify("x", pace) == ("x", "live", 1)
    assert pace.rested == [60]
    assert len(calls) == 2


def test_verify_gives_up_unknown_after_three_429s(monkeypatch):
    calls = _fetching(monkeypatch, *[_listing(429, b"")] * 3)
    pace = _Pace()
    assert mine.verify("x", pace) == ("x", "unknown", None)
    assert pace.rested == [60, 60, 60]
    assert len(calls) == 3


def test_verify_reads_a_failed_request_as_unknown(monkeypatch):
    _fetching(monkeypatch, OSError("timed out"))
    assert mine.verify("x", _Pace()) == ("x", "unknown", None)


def test_candidates_drop_a_label_too_long_for_dns(tmp_path):
    cands = tmp_path / "cands.txt"
    cands.write_text(f"ok\n{'a' * 64}\n{'b' * 63}\n")
    assert mine._candidates(cands, tmp_path / "none.csv", col=0, held=set()) == [
        "ok",
        "b" * 63,
    ]
