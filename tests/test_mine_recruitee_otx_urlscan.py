"""Tests for the Recruitee OTX and urlscan miner (scripts/discover/mine_recruitee_otx_urlscan.py).

No network: the OTX and urlscan answers are small fixtures shaped like the real ones read on 2026-09-29,
`get_json` runs against a fake `subprocess.run`, and the paging walk against a scripted `get_json`. It is
a script under `scripts/discover`, so we put that directory on the path and import it by name, the way
`test_mine_lever.py` does.
"""

import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "discover"))

import mine_recruitee_otx_urlscan as mine


def hit(domain: str, sort: tuple = (1, "id")) -> dict:
    return {"page": {"domain": domain}, "sort": list(sort)}


def test_label_of_keeps_only_a_single_label_under_the_apex():
    assert mine.label_of("Conflux.Recruitee.com") == "conflux"
    assert mine.label_of("10xcrew.recruitee.com") == "10xcrew"
    assert mine.label_of("recruitee.com") is None
    assert (
        mine.label_of("api.s3.recruitee.com") is None
    )  # a deeper name is not a tenant
    assert mine.label_of("api.tellent.com") is None
    assert mine.label_of("63.220.186.35.bc.googleusercontent.com") is None
    assert mine.label_of("") is None


def test_the_glued_white_label_forms_are_not_read_as_labels():
    """HackerTarget glued a customer domain to the CNAME target, `40-a.ru` + `crazygames`. Those
    names carry a dot in front of the tenant, so they never key as a label of their own."""
    assert mine.label_of("40-a.rucrazygames.recruitee.com") is None
    assert mine.label_of("193motors.comcrazygames.recruitee.com") is None


def test_labels_from_otx_reads_hostnames_and_skips_everything_else():
    payload = {
        "count": 4,
        "passive_dns": [
            {"hostname": "medmehealth.recruitee.com", "address": "35.186.220.63"},
            {"hostname": "NINJASINPYJAMAS.recruitee.com"},
            {"hostname": "api.tellent.com"},
            {"address": "35.186.220.63"},  # a record with no hostname
        ],
    }
    assert mine.labels_from_otx(payload) == {"medmehealth", "ninjasinpyjamas"}
    assert mine.labels_from_otx({}) == set()


def test_labels_from_hits_reads_the_scanned_page_domain():
    hits = [
        hit("droppie.recruitee.com"),
        hit("droppie.recruitee.com"),
        hit("recruitee.com"),
        hit("swisscom.com"),
        {"page": {}},  # a scan that recorded no page domain
        {},
    ]
    assert mine.labels_from_hits(hits) == {"droppie"}


def test_dominant_domain_names_a_host_that_fills_the_page():
    hits = [hit("peripass.recruitee.com")] * 85 + [
        hit(f"t{i}.recruitee.com") for i in range(15)
    ]
    assert mine.dominant_domain(hits, []) == "peripass.recruitee.com"


def test_dominant_domain_ignores_a_host_already_excluded_the_apex_and_a_mixed_page():
    full = [hit("peripass.recruitee.com")] * 100
    assert mine.dominant_domain(full, ["peripass.recruitee.com"]) is None
    # `page.domain` matches subdomains, so excluding the apex would empty the result set
    assert mine.dominant_domain([hit("recruitee.com")] * 100, []) is None
    mixed = [hit(f"t{i}.recruitee.com") for i in range(100)]
    assert mine.dominant_domain(mixed, []) is None
    assert mine.dominant_domain([], []) is None
    just_under = [hit("a.recruitee.com")] * (mine.DOMINATED - 1) + [
        hit("b.recruitee.com")
    ] * 60
    assert mine.dominant_domain(just_under, []) == "b.recruitee.com"


def test_urlscan_query_appends_one_exclusion_per_excluded_host():
    assert mine.urlscan_query([]) == mine.URLSCAN_QUERY
    query = mine.urlscan_query(["peripass.recruitee.com", "x.recruitee.com"])
    assert query.startswith(mine.URLSCAN_QUERY)
    assert query.endswith(
        ' AND NOT page.domain:"peripass.recruitee.com" AND NOT page.domain:"x.recruitee.com"'
    )
    assert (
        'NOT page.url:"https://recruitee.com/"' in query
    )  # the apex page, scanned ~2,470 times


def test_next_cursor_is_the_last_hits_sort_pair():
    hits = [
        hit("a.recruitee.com", (1709066464148, "aaa")),
        hit("b.recruitee.com", (1709066464100, "bbb")),
    ]
    assert mine.next_cursor(hits) == "1709066464100,bbb"


def test_held_keys_by_the_scrapers_identity_lowercased(tmp_path):
    """The ledger's own spelling wins the row, but the Board is the lowercased slug: a candidate that
    differs only in case is held, one that differs by a letter is not."""
    ledger = tmp_path / "recruitee.csv"
    ledger.write_text(
        "ats,tenant,url,status,jobs,checked_at\n"
        "recruitee,Conflux,https://conflux.recruitee.com,live,12,2026-09-29\n"
        "recruitee,leanbox,https://leanbox.recruitee.com,dead,,2026-07-03\n",
        encoding="utf-8",
    )
    keys = mine.held(ledger)
    assert keys == {"conflux", "leanbox"}
    assert "conflux" in keys and "confluxx" not in keys


class SpyFile:
    """An output file that records writes, flushes and the close."""

    def __init__(self):
        self.lines: list[str] = []
        self.flushes = 0
        self.closed = False
        self.flushed_lines = 0

    def write(self, text):
        self.lines.append(text)

    def flush(self):
        self.flushes += 1
        self.flushed_lines = len(self.lines)

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def test_main_flushes_each_unheld_label_and_closes_the_file_when_a_source_fails(
    monkeypatch,
):
    spy = SpyFile()
    monkeypatch.setattr(mine, "open", lambda *a, **k: spy, raising=False)
    monkeypatch.setattr(mine, "held", lambda: {"conflux"})

    def sources():
        yield "otx ip", {"conflux", "newone", "alsonew"}
        yield (
            "otx domain",
            {"newone", "third"},
        )  # newone was already written by the first source
        raise RuntimeError("link dropped")

    monkeypatch.setattr(mine, "sources", sources)
    with pytest.raises(RuntimeError, match="link dropped"):
        mine.main(["prog", "out.txt"])
    assert spy.lines == ["alsonew\n", "newone\n", "third\n"]
    assert spy.flushed_lines == 3  # every row was flushed, not left in a buffer
    assert spy.closed


def test_main_keeps_going_past_an_unreachable_source_but_exits_1(monkeypatch, capsys):
    spy = SpyFile()
    monkeypatch.setattr(mine, "open", lambda *a, **k: spy, raising=False)
    monkeypatch.setattr(mine, "held", lambda: set())
    monkeypatch.setattr(
        mine, "sources", lambda: iter([("otx ip", None), ("urlscan", {"abc"})])
    )
    assert (
        mine.main(["prog", "out.txt"]) == 1
    )  # a caller must re-run, not trust the file
    assert spy.lines == ["abc\n"]  # what the reachable source found is still written
    out = capsys.readouterr().out
    assert "otx ip: UNREACHABLE" in out
    assert spy.closed


def test_main_exits_0_when_every_source_answered(monkeypatch):
    spy = SpyFile()
    monkeypatch.setattr(mine, "open", lambda *a, **k: spy, raising=False)
    monkeypatch.setattr(mine, "held", lambda: set())
    monkeypatch.setattr(
        mine, "sources", lambda: iter([("otx ip", set()), ("urlscan", {"abc"})])
    )
    assert mine.main(["prog", "out.txt"]) == 0


def test_main_without_an_output_path_prints_usage(capsys):
    assert mine.main(["prog"]) == 2
    assert "Recruitee labels" in capsys.readouterr().out


def curl_says(*answers):
    """A fake `subprocess.run` for get_json: each call pops the next (status, body); counts the calls."""
    queue = list(answers)
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        code, body = queue.pop(0)
        return subprocess.CompletedProcess(cmd, 0, stdout=f"{body}\n{code}", stderr="")

    return run, calls


def test_get_json_retries_a_failed_try_and_returns_the_next_good_answer(monkeypatch):
    run, calls = curl_says(("000", ""), ("200", "not json"), ("200", '{"ok": 1}'))
    slept = []
    monkeypatch.setattr(mine.subprocess, "run", run)
    monkeypatch.setattr(mine.time, "sleep", slept.append)
    assert mine.get_json("https://otx.alienvault.com/x") == {"ok": 1}
    assert len(calls) == 3
    assert slept == [10, 20]  # a longer wait after each failed try
    assert calls[0][:3] == ["curl", "-sS", "-m"]  # the timeout covers DNS


def test_get_json_returns_none_after_three_failed_tries(monkeypatch):
    run, calls = curl_says(("429", ""), ("503", ""), ("000", ""))
    monkeypatch.setattr(mine.subprocess, "run", run)
    monkeypatch.setattr(mine.time, "sleep", lambda s: None)
    assert mine.get_json("https://urlscan.io/api/v1/search/") is None
    assert len(calls) == 3


def test_otx_reads_labels_and_treats_a_200_without_passive_dns_as_a_failure(
    monkeypatch,
):
    monkeypatch.setattr(
        mine, "get_json", lambda url: {"passive_dns": [{"hostname": "a.recruitee.com"}]}
    )
    assert mine.otx("IPv4/1.2.3.4") == {"a"}
    monkeypatch.setattr(mine, "get_json", lambda url: {"passive_dns": []})
    assert mine.otx("IPv4/1.2.3.4") == set()  # a genuinely empty list is an answer
    monkeypatch.setattr(mine, "get_json", lambda url: {"error": "quota"})
    assert mine.otx("IPv4/1.2.3.4") is None  # no `passive_dns` key: not an empty answer
    monkeypatch.setattr(mine, "get_json", lambda url: None)
    assert mine.otx("IPv4/1.2.3.4") is None


def scripted_urlscan(monkeypatch, pages):
    """Answer each urlscan call from `pages` in order, recording the query and cursor it was asked with."""
    asked = []
    queue = list(pages)

    def get_json(url):
        params = parse_qs(urlparse(url).query)
        asked.append((params["q"][0], params.get("search_after", [None])[0]))
        return queue.pop(0)

    monkeypatch.setattr(mine, "get_json", get_json)
    monkeypatch.setattr(mine.time, "sleep", lambda s: None)
    return asked


def full_page(prefix, first_sort=1000):
    """A page of PAGE_SIZE hits on distinct tenants, so no single host dominates it."""
    return {
        "results": [
            hit(f"{prefix}{i}.recruitee.com", (first_sort - i, f"id{prefix}{i}"))
            for i in range(mine.PAGE_SIZE)
        ]
    }


def test_urlscan_pages_walks_pages_with_the_last_hits_cursor_until_a_short_page(
    monkeypatch,
):
    page1 = full_page("a", 1000)
    page2 = {"results": [hit("z.recruitee.com", (5, "idz"))]}
    asked = scripted_urlscan(monkeypatch, [page1, page2])
    pages = list(mine.urlscan_pages())
    assert [len(p) for p in pages] == [mine.PAGE_SIZE, 1]
    assert pages[1] == {"z"}
    assert asked[0] == (mine.URLSCAN_QUERY, None)
    assert asked[1] == (
        mine.URLSCAN_QUERY,
        f"{1000 - (mine.PAGE_SIZE - 1)},ida{mine.PAGE_SIZE - 1}",
    )


def test_urlscan_pages_excludes_a_dominating_host_and_rereads_the_page_from_the_same_cursor(
    monkeypatch,
):
    dominated = {
        "results": [hit("peripass.recruitee.com")] * 85
        + [hit(f"t{i}.recruitee.com") for i in range(15)]
    }
    clean = {"results": [hit("real.recruitee.com")]}
    asked = scripted_urlscan(monkeypatch, [dominated, clean])
    pages = list(mine.urlscan_pages())
    assert pages == [
        {"real"}
    ]  # the dominated page's labels were not yielded, it was re-read
    assert (
        asked[1][0]
        == mine.URLSCAN_QUERY + ' AND NOT page.domain:"peripass.recruitee.com"'
    )
    assert (
        asked[1][1] == asked[0][1] is None
    )  # re-read from the same cursor, not the next page


def test_urlscan_pages_ends_with_none_when_a_page_is_unreachable(monkeypatch):
    asked = scripted_urlscan(monkeypatch, [full_page("a"), None])
    pages = list(mine.urlscan_pages())
    assert len(pages) == 2
    assert len(pages[0]) == mine.PAGE_SIZE and pages[1] is None
    assert len(asked) == 2  # the walk stopped at the failure


def test_urlscan_pages_reads_a_200_without_results_as_a_failure_not_an_empty_page(
    monkeypatch,
):
    for payload in ({}, {"status": 429, "message": "rate limited"}, {"results": "x"}):
        scripted_urlscan(monkeypatch, [payload])
        assert list(mine.urlscan_pages()) == [None], payload


def test_urlscan_pages_treats_an_honestly_empty_results_list_as_the_end(monkeypatch):
    scripted_urlscan(monkeypatch, [{"results": []}])
    assert list(mine.urlscan_pages()) == [set()]


def test_sources_yield_otx_then_urlscan_pages(monkeypatch):
    monkeypatch.setattr(mine, "otx", lambda path: {path})
    monkeypatch.setattr(mine, "urlscan_pages", lambda: iter([{"p1"}, None]))
    assert list(mine.sources()) == [
        ("otx ip", {f"IPv4/{mine.IP}"}),
        ("otx domain", {"domain/recruitee.com"}),
        ("urlscan", {"p1"}),
        ("urlscan", None),
    ]


def test_the_miner_makes_no_request_of_its_own_to_a_recruitee_host():
    """It asks OTX and urlscan only; asking Recruitee is the offers sieve's job, at 4 workers."""
    source = (
        ROOT / "scripts" / "discover" / "mine_recruitee_otx_urlscan.py"
    ).read_text(encoding="utf-8")
    assert "recruitee.com/api" not in source
