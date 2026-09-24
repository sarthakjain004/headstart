"""Tests for the Jibe client miner's pure parts (scripts/discover/mine_jibe.py). No network: the
DNS resolvers and the HTTP getter are fakes."""

from __future__ import annotations

import csv
import json
import sys
from itertools import pairwise
from pathlib import Path

import dns.resolver
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "discover"))

import mine_jibe as mj


def test_icims_tenants_yield_their_hyphen_forms():
    assert mj.labels_from_tenant("icims", "careers-celanese.icims.com") == {
        "careers",
        "celanese",
        "careerscelanese",
    }
    assert mj.labels_from_tenant("icims", "jobs-cadence-education.icims.com") == {
        "jobs",
        "cadence",
        "education",
        "cadence-education",
        "jobscadenceeducation",
    }
    # a stray leading hyphen is dropped, a deeper subdomain is not a tenant label
    assert mj.labels_from_tenant("icims", "-teague.icims.com") == {"teague"}
    assert mj.labels_from_tenant("icims", "admin.social.icims.com") == set()


@pytest.mark.parametrize(
    "ats, tenant, expected",
    [
        ("workday", "160over90", {"160over90"}),
        ("zoho", "01da.zohorecruit.eu", {"01da"}),
        ("oracle", "chevron.fa.us2.oraclecloud.com", {"chevron"}),
        ("greenhouse", "*nozominetworks", set()),  # not a DNS label
        ("taleo_be", "AAA:39@chu.tbe.taleo.net/chu02", set()),
        ("ashby", "x", set()),  # one character
    ],
)
def test_other_ledgers_yield_the_bare_label(ats, tenant, expected):
    assert mj.labels_from_tenant(ats, tenant) == expected


def test_candidate_labels_union_every_ledger(tmp_path):
    (tmp_path / "icims.csv").write_text(
        "ats,tenant,url,status,jobs,checked_at\n"
        "icims,careers-uhs.icims.com,https://careers-uhs.icims.com,live,1,2026-09-08\n"
    )
    (tmp_path / "workday.csv").write_text(
        "ats,tenant,url,status,jobs,checked_at\n"
        "workday,uhs,https://uhs.wd1.myworkdayjobs.com/x,dead,,2026-08-14\n"
    )
    assert mj.candidate_labels(tmp_path) == ["careers", "careersuhs", "uhs"]


class _Answer:
    def __init__(self, address):
        self.address = address


class _Resolver:
    """Raises or answers from a script, one entry per query."""

    def __init__(self, *script):
        self.script = list(script)
        self.asked = 0

    def resolve(self, name, rdtype, search=False):
        assert name.endswith(".jibeapply.com") and rdtype == "A" and search is False
        self.asked += 1
        outcome = self.script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return [_Answer(a) for a in outcome]


def test_an_a_record_is_a_client():
    assert mj.resolve("costco", [_Resolver(["3.213.45.220"])], pause=0) == (
        "client",
        "3.213.45.220",
    )


@pytest.mark.parametrize("exc", [dns.resolver.NXDOMAIN(), dns.resolver.NoAnswer()])
def test_no_a_record_settles_as_none_on_the_first_answer(exc):
    resolver = _Resolver(exc)
    assert mj.resolve("zzzzqqq", [resolver], pause=0)[0] == "none"
    assert resolver.asked == 1


def test_a_failed_query_moves_to_another_resolver_and_is_never_a_miss():
    flaky = _Resolver(*[dns.resolver.LifetimeTimeout(timeout=6.0, errors=[])] * 3)
    servfail = _Resolver(*[dns.resolver.NoNameservers()] * 3)
    assert mj.resolve("uhs", [flaky, servfail], pause=0)[0] == "unresolved"
    assert flaky.asked and servfail.asked
    # a later definitive answer from the other resolver wins
    good = _Resolver(["44.215.165.217"])
    verdict, _ = mj.resolve(
        "uhs", [_Resolver(dns.resolver.NoNameservers()), good], pause=0
    )
    assert verdict == "client"


def test_client_code_is_read_from_the_first_row_that_states_one():
    body = json.dumps(
        {"jobs": [{"data": {"client_code": ""}}, {"data": {"client_code": "Costco"}}]}
    )
    assert mj.client_code_in(body) == "costco"
    assert mj.client_code_in(json.dumps({"jobs": [], "totalCount": 0})) is None
    assert mj.client_code_in("<html>not json</html>") is None


def test_cid_is_read_from_the_board_page():
    assert mj.cid_in('<script>_jibe = {"cid":"garmin"}\n_jibe.env = \'prod\'') == (
        "garmin"
    )
    assert mj.cid_in("<html>no jibe here</html>") is None


ROBOTS_OK = (200, "User-agent: *\nAllow: /\ncrawl-delay: 5\n")


class _Host:
    """A fake vanity host: answers by path, records each request and when it was made."""

    def __init__(self, pages):
        self.pages, self.log, self.now = pages, [], 0.0

    def get(self, url):
        path = url.split("careers.acme.com", 1)[1].split("?")[0]
        self.log.append((path, self.now))
        return self.pages.get(path, (404, ""))

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _vanity(host):
    return mj.resolve_vanity("careers.acme.com", host.get, host.clock, host.sleep)


def test_vanity_reads_robots_first_and_paces_every_request():
    api = json.dumps({"jobs": [{"data": {"client_code": "acme"}}], "totalCount": 1})
    host = _Host(
        {
            "/robots.txt": ROBOTS_OK,
            "/api/jobs": (200, api),
            "/jobs": (200, '_jibe = {"cid":"demant"}'),
        }
    )
    assert _vanity(host) == ("client", "acme", "client_code")
    assert [p for p, _ in host.log] == ["/robots.txt", "/api/jobs"]
    times = [t for _, t in host.log]
    assert all(b - a >= mj.CRAWL_DELAY for a, b in pairwise(times))


def test_vanity_falls_back_to_the_page_cid_for_an_empty_board():
    host = _Host(
        {
            "/robots.txt": ROBOTS_OK,
            "/api/jobs": (200, json.dumps({"jobs": [], "totalCount": 0})),
            "/jobs": (200, '_jibe = {"cid":"acme"}'),
        }
    )
    assert _vanity(host) == ("client", "acme", "cid")
    times = [t for _, t in host.log]
    assert all(b - a >= mj.CRAWL_DELAY for a, b in pairwise(times))


def test_vanity_honours_a_disallow_and_makes_no_other_request():
    host = _Host({"/robots.txt": (200, "User-agent: *\nDisallow: /\n")})
    assert _vanity(host)[0] == "disallowed"
    assert [p for p, _ in host.log] == ["/robots.txt"]


@pytest.mark.parametrize("robots", [(None, ""), (503, "")])
def test_vanity_with_unreadable_robots_is_retried_later_not_read(robots):
    host = _Host({"/robots.txt": robots})
    assert _vanity(host)[0] == "unresolved"
    assert [p for p, _ in host.log] == ["/robots.txt"]


def test_vanity_throttled_is_unresolved_but_a_plain_miss_is_settled():
    throttled = _Host({"/robots.txt": ROBOTS_OK, "/api/jobs": (429, "")})
    assert _vanity(throttled)[0] == "unresolved"
    missing = _Host({"/robots.txt": ROBOTS_OK})
    assert _vanity(missing) == ("none", "", "/api/jobs:404 /jobs:404")


def test_sink_resumes_and_never_settles_an_unresolved_item(tmp_path):
    sink = mj._Sink(tmp_path, "dns")
    sink.settle("costco", "client", "costco", "3.213.45.220", "dns")
    sink.settle("zzzzqqq", "none", "", "NoAnswer", "dns")
    sink.settle("uhs", "unresolved", "", "LifetimeTimeout", "dns")
    sink.settle("costco", "client", "costco", "3.213.45.220", "dns")  # re-confirmed
    sink.close()

    again = mj._Sink(tmp_path, "dns")
    again.close()
    assert again.settled == {"costco", "zzzzqqq"}
    with (tmp_path / "candidates.csv").open() as fh:
        assert list(csv.reader(fh)) == [
            ["ats", "tenant", "url", "source"],
            ["jibe", "costco", "https://costco.jibeapply.com", "dns"],
        ]
