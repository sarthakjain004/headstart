"""Tests for the Recruitee offers-API sieve (scripts/discover/mine_recruitee_offers_sieve.py).

No network: every request goes through a fake fetcher, and `fetch` itself is tested against a fake
`subprocess.run`. It is a script under `scripts/discover`, so we put that directory on the path and import
it by name, the way `test_mine_lever.py` does.
"""

import csv
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "discover"))

import mine_recruitee_offers_sieve as sieve


def answer(status="200", host=None, body=None, retry_after=0, label="x"):
    """What a fetcher returns: (status, landed host, body, Retry-After)."""
    if body is None:
        body = json.dumps({"offers": []})
    return status, host or f"{label}.recruitee.com", body, retry_after


def offers_body(n):
    return json.dumps({"offers": [{"id": i} for i in range(n)]})


def test_classify_reads_a_200_offers_list_on_the_labels_own_host_as_live():
    verdict = sieve.classify("conflux", "200", "conflux.recruitee.com", offers_body(12))
    assert verdict == {"status": "live", "offers": 12, "note": "200"}
    # a tenant with nothing open still answers `{"offers": []}` and is a Board
    assert (
        sieve.classify("empty", "200", "empty.recruitee.com", offers_body(0))["status"]
        == "live"
    )


def test_classify_reads_a_redirect_to_another_label_as_moved_never_live():
    """A renamed account, and the throwaway phishing tenants, answer 200 from a different label."""
    verdict = sieve.classify(
        "login", "200", "loginsoftware.recruitee.com", offers_body(0)
    )
    assert verdict["status"] == "moved"
    assert verdict["note"] == "lands on loginsoftware.recruitee.com"


def test_classify_404_is_absent_and_everything_else_is_unreachable_not_dead():
    assert (
        sieve.classify("nope", "404", "nope.recruitee.com", "{}")["status"] == "absent"
    )
    for status in ("429", "500", "502", "503", "403", "000", "301"):
        verdict = sieve.classify("x", status, "x.recruitee.com", "")
        assert verdict["status"] == "unreachable", status
        assert verdict["note"] == f"http {status}"


def test_classify_a_200_that_is_not_an_offers_list_is_unreachable():
    """A vendor host (`blog`) answers 200 with HTML; a 200 without a list must not read as a Board."""
    for body in (
        "<html>blog</html>",
        "{}",
        '{"offers": null}',
        '{"offers": "x"}',
        "[]",
    ):
        verdict = sieve.classify("blog", "200", "blog.recruitee.com", body)
        assert verdict["status"] == "unreachable", body
        assert verdict["note"] == "200 without an offers list"


def test_verify_retries_a_429_after_the_wait_it_names_and_settles_on_the_next_answer():
    calls = iter(
        [answer("429", retry_after=7), answer("200", body=offers_body(3), label="a")]
    )
    slept = []
    verdict = sieve.verify("a", lambda label: next(calls), slept.append)
    assert (verdict["status"], verdict["offers"]) == ("live", 3)
    assert slept == [7]


def test_verify_gives_up_as_unreachable_after_three_tries_and_caps_the_wait():
    slept = []
    verdict = sieve.verify(
        "a", lambda label: answer("429", retry_after=500), slept.append
    )
    assert verdict["status"] == "unreachable"
    assert slept == [
        60,
        60,
    ]  # a wait between tries, none after the last; Retry-After capped at 60
    slept.clear()
    sieve.verify("a", lambda label: answer("000"), slept.append)
    assert slept == [4, 8]  # no Retry-After: 4 s x the attempt


def test_verify_asks_a_403_once_because_public_api_disabled_does_not_change():
    calls = []

    def fetcher(label):
        calls.append(label)
        return answer("403", body='{"error":"Public API disabled"}')

    verdict = sieve.verify(
        "adidas", fetcher, lambda s: pytest.fail("a 403 must not wait")
    )
    assert verdict["status"] == "unreachable"
    assert calls == ["adidas"]


def test_verify_does_not_retry_a_settled_verdict():
    calls = []

    def fetcher(label):
        calls.append(label)
        return answer("404")

    assert (
        sieve.verify("gone", fetcher, lambda s: pytest.fail("no wait"))["status"]
        == "absent"
    )
    assert calls == ["gone"]


def test_held_keys_by_the_scrapers_identity_lowercased(tmp_path):
    ledger = tmp_path / "recruitee.csv"
    ledger.write_text(
        "ats,tenant,url,status,jobs,checked_at\n"
        "recruitee,Conflux,https://conflux.recruitee.com,live,12,2026-09-29\n"
        "recruitee,leanbox,https://leanbox.recruitee.com,dead,,2026-07-03\n",
        encoding="utf-8",
    )
    assert sieve.held(ledger) == {"conflux", "leanbox"}


def test_to_probe_keeps_valid_unheld_unsettled_labels_once_in_order():
    lines = [
        "Conflux",
        " newone ",
        "newone",
        "",
        "a.b",
        "-bad",
        "ünï",
        "held",
        "settled",
        "third",
        "HELD",
    ]
    assert sieve.to_probe(lines, {"held", "conflux"}, {"settled"}) == [
        "newone",
        "third",
    ]


def test_settled_labels_skips_unreachable_so_a_rerun_asks_again(tmp_path):
    results = tmp_path / "results.jsonl"
    assert sieve.settled_labels(results) == set()  # no file yet
    results.write_text(
        "".join(
            json.dumps({"label": label, "status": status}) + "\n"
            for label, status in [
                ("a", "live"),
                ("b", "absent"),
                ("c", "moved"),
                ("d", "unreachable"),
            ]
        ),
        encoding="utf-8",
    )
    assert sieve.settled_labels(results) == {"a", "b", "c"}


def test_fetch_parses_status_landing_host_body_and_retry_after(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        Path(cmd[cmd.index("-D") + 1]).write_text(
            "HTTP/2 429\r\nRetry-After: 7\r\n\r\n", encoding="utf-8"
        )
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout='{"offers": []}\n429 https://zz.recruitee.com/api/offers/',
            stderr="",
        )

    monkeypatch.setattr(sieve.subprocess, "run", fake_run)
    assert sieve.fetch("zz") == ("429", "zz.recruitee.com", '{"offers": []}', 7)
    assert seen["cmd"][0] == "curl" and "-m" in seen["cmd"]  # the timeout covers DNS
    assert seen["cmd"][-1] == "https://zz.recruitee.com/api/offers/"


def test_fetch_reads_a_failed_curl_as_no_answer(monkeypatch):
    monkeypatch.setattr(
        sieve.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(
            cmd, 6, stdout="", stderr="curl: (6) Could not resolve host"
        ),
    )
    assert sieve.fetch("zz") == ("000", "", "", 0)


def fake_site(outcomes, log=None):
    """A fetcher over a dict label -> ("live", n) | "absent" | "unreachable" | ("moved", target)."""

    def fetcher(label):
        if log is not None:
            log.append(label)
        outcome = outcomes[label]
        if outcome == "absent":
            return answer("404", label=label)
        if outcome == "unreachable":
            return answer("429", label=label)
        kind, value = outcome
        if kind == "live":
            return answer("200", body=offers_body(value), label=label)
        return answer("200", host=f"{value}.recruitee.com", label=label)

    return fetcher


def read_rows(path):
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def run_sieve(tmp_path, labels, fetcher, **kwargs):
    params = {"gap": 0, "sleep": lambda s: None, "have": {"held"}, "workers": 1}
    params.update(kwargs)
    return sieve.run(
        labels,
        results_path=tmp_path / "results.jsonl",
        staging_path=tmp_path / "recruitee.csv",
        fetcher=fetcher,
        **params,
    )


def test_run_stages_only_live_unheld_labels_and_records_every_verdict(tmp_path):
    outcomes = {
        "alive": ("live", 4),
        "empty": ("live", 0),
        "gone": "absent",
        "renamed": ("moved", "alive"),
        "held": ("live", 9),
    }
    log = []
    code = run_sieve(tmp_path, list(outcomes), fake_site(outcomes, log))
    assert code == 0
    assert "held" not in log  # held by the ledger: never asked
    assert [r["tenant"] for r in read_rows(tmp_path / "recruitee.csv")] == [
        "alive",
        "empty",
    ]
    row = read_rows(tmp_path / "recruitee.csv")[0]
    assert row == {
        "ats": "recruitee",
        "tenant": "alive",
        "url": "https://alive.recruitee.com",
    }
    verdicts = {
        json.loads(l)["label"]: json.loads(l)["status"]
        for l in (tmp_path / "results.jsonl").read_text().splitlines()
    }
    assert verdicts == {
        "alive": "live",
        "empty": "live",
        "gone": "absent",
        "renamed": "moved",
    }


def test_run_exits_1_while_a_label_is_unreachable_then_resumes_only_that_label(
    tmp_path,
):
    outcomes = {"a": ("live", 1), "b": "unreachable", "c": "absent"}
    assert run_sieve(tmp_path, list(outcomes), fake_site(outcomes)) == 1
    assert [r["tenant"] for r in read_rows(tmp_path / "recruitee.csv")] == ["a"]
    outcomes["b"] = ("live", 5)  # the rate limit cleared
    log = []
    assert run_sieve(tmp_path, list(outcomes), fake_site(outcomes, log)) == 0
    assert log == ["b"]  # a and c were settled, only the unreachable one is asked again
    rows = read_rows(tmp_path / "recruitee.csv")
    assert [r["tenant"] for r in rows] == [
        "a",
        "b",
    ]  # one header, no duplicate row for a
    assert (tmp_path / "recruitee.csv").read_text().count("ats,tenant,url") == 1


def test_run_never_asks_for_a_label_already_staged_twice(tmp_path):
    (tmp_path / "recruitee.csv").write_text(
        "ats,tenant,url\nrecruitee,a,https://a.recruitee.com\n", encoding="utf-8"
    )
    outcomes = {"a": ("live", 1)}
    run_sieve(tmp_path, ["a"], fake_site(outcomes))
    assert [r["tenant"] for r in read_rows(tmp_path / "recruitee.csv")] == ["a"]


def test_run_flushes_the_hit_before_a_later_label_crashes_it(tmp_path):
    outcomes = {"first": ("live", 2), "second": ("live", 1)}

    def fetcher(label):
        if label == "second":
            raise RuntimeError("link dropped")
        return fake_site(outcomes)(label)

    with pytest.raises(RuntimeError, match="link dropped"):
        run_sieve(tmp_path, ["first", "second"], fetcher)
    assert [r["tenant"] for r in read_rows(tmp_path / "recruitee.csv")] == ["first"]
    assert (
        json.loads((tmp_path / "results.jsonl").read_text().splitlines()[0])["label"]
        == "first"
    )


def test_run_never_uses_more_than_four_workers(tmp_path):
    active = 0
    peak = 0
    lock = threading.Lock()

    def fetcher(label):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.01)
        with lock:
            active -= 1
        return answer("404", label=label)

    labels = [f"label{i}" for i in range(60)]
    assert run_sieve(tmp_path, labels, fetcher, workers=32) == 0
    assert 1 < peak <= sieve.MAX_WORKERS


def test_run_stops_with_exit_3_when_the_control_stops_reading_live(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(sieve, "CONTROL_EVERY", 2)
    outcomes = {f"l{i}": "absent" for i in range(6)}
    outcomes["control"] = "absent"  # the known tenant no longer answers as one
    code = run_sieve(
        tmp_path, list(outcomes)[:-1], fake_site(outcomes), control="control"
    )
    assert code == 3


def test_run_keeps_going_while_the_control_reads_live(tmp_path, monkeypatch):
    monkeypatch.setattr(sieve, "CONTROL_EVERY", 2)
    outcomes = {f"l{i}": "absent" for i in range(6)}
    outcomes["control"] = ("live", 12)
    assert (
        run_sieve(
            tmp_path,
            [f"l{i}" for i in range(6)],
            fake_site(outcomes),
            control="control",
        )
        == 0
    )


def test_main_without_a_labels_file_prints_usage(capsys):
    assert sieve.main(["prog"]) == 2
    assert "offers API" in capsys.readouterr().out


def test_main_passes_the_options_to_run(tmp_path, monkeypatch):
    labels = tmp_path / "labels.txt"
    labels.write_text("a\nb\n", encoding="utf-8")
    seen = {}
    monkeypatch.setattr(
        sieve, "run", lambda lines, **kw: seen.update(lines=lines, **kw) or 0
    )
    code = sieve.main(
        [
            "prog",
            str(labels),
            str(tmp_path / "r.jsonl"),
            "--workers",
            "2",
            "--gap",
            "0.5",
            "--control",
            "conflux",
        ]
    )
    assert code == 0
    assert seen["lines"] == ["a", "b"]
    assert (seen["workers"], seen["gap"], seen["control"]) == (2, 0.5, "conflux")
    assert seen["results_path"] == tmp_path / "r.jsonl"
    assert seen["staging_path"] == sieve.STAGING
