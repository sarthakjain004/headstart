import importlib.util
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parents[1] / "scripts/eval/verify_filters.py"


def _load_harness():
    spec = importlib.util.spec_from_file_location("verify_filters", _HARNESS)
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    return harness


def _row(job_id: str) -> dict:
    board, native = job_id.rsplit(":", 1)
    slug = board.split(":", 1)[1]
    return {"id": job_id, "url": f"https://jobs.lever.co/{slug}/{native}", "title": "T"}


def test_lever_links_are_sampled_one_per_board_across_every_query(monkeypatch):
    """ADR-0281: a Lever Board with its hosted pages off serves only dead links, and the top 3
    rows of one query reach one or two Boards. Lever reads the first row each Board ranks for,
    across the whole query battery; every other ATS still reads its top 3."""
    harness = _load_harness()
    ranked = {
        harness.QUERIES[0]: ["lever:a:1", "lever:a:2", "lever:b:1"],
        harness.QUERIES[1]: ["lever:b:2", "lever:c:1"],
    }

    def fake_get(base, params):
        if params["ats"] == "lever":
            return [_row(i) for i in ranked.get(params["q"], [])]
        return [_row(f"x:a:{n}") for n in range(5)]

    monkeypatch.setattr(harness, "_get", fake_get)
    checks = harness.run_url_checks("https://space", ["lever", "x"], http=False)
    assert [(c["ats"], c["url"]) for c in checks] == [
        ("lever", "https://jobs.lever.co/a/1"),
        ("lever", "https://jobs.lever.co/b/1"),
        ("lever", "https://jobs.lever.co/c/1"),
        ("x", "https://jobs.lever.co/a/0"),
        ("x", "https://jobs.lever.co/a/1"),
        ("x", "https://jobs.lever.co/a/2"),
    ]


def _facets_probe(default_total, shown_total, left_out):
    """A Space's `/facets` answers: the default leaves `left_out` out of `default_total`."""

    def probe(base, path, params):
        assert path == "/facets" and params["strict"] == "1"
        if params.get("include_non_tech"):
            return 200, {"total": shown_total}
        body = {"total": default_total}
        if left_out is not None:
            body["non_tech_left_out"] = left_out
        return 200, body

    return probe


def test_the_non_tech_switch_must_add_exactly_what_the_default_says_it_left_out(
    monkeypatch,
):
    """ADR-0349: showing more never narrows, and `non_tech_left_out` is what the switch adds."""
    harness = _load_harness()
    monkeypatch.setattr(harness, "_probe", _facets_probe(440, 500, 60))
    assert {
        c["n_violations"] for c in harness.run_non_tech_checks("https://space")
    } == {0}

    monkeypatch.setattr(harness, "_probe", _facets_probe(440, 500, 59))
    wrong = harness.run_non_tech_checks("https://space")
    assert all(c["n_violations"] == 1 for c in wrong)

    monkeypatch.setattr(harness, "_probe", _facets_probe(500, 440, 0))
    narrowed = harness.run_non_tech_checks("https://space")
    assert all(c["n_violations"] >= 1 for c in narrowed)


def test_a_table_without_the_stamp_says_nothing_left_out_and_is_not_a_violation(
    monkeypatch,
):
    harness = _load_harness()
    monkeypatch.setattr(harness, "_probe", _facets_probe(500, 500, None))
    assert {
        c["n_violations"] for c in harness.run_non_tech_checks("https://space")
    } == {0}


def test_a_space_that_refuses_the_switch_is_a_violation(monkeypatch):
    """An app older than agent-api 18 answers a strict request naming it with 400."""
    harness = _load_harness()
    monkeypatch.setattr(
        harness, "_probe", lambda base, path, params: (400, {"error": "invalid filter"})
    )
    assert all(
        c["n_violations"] == 1 for c in harness.run_non_tech_checks("https://space")
    )


def _http_error(code: int, retry_after: str | None = None):
    import email.message
    import urllib.error

    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://space/search", code, "x", headers, None)


def test_a_rate_limited_read_waits_out_its_retry_after_and_is_asked_again(monkeypatch):
    """The Space limits reads per address; a 429 is it pacing the harness, not an answer."""
    harness = _load_harness()
    answers = [_http_error(429, "7"), _http_error(429, "3"), "ok"]
    slept: list[float] = []

    def fake_urlopen(url, timeout):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(harness.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(harness.time, "sleep", slept.append)
    assert harness._open("https://space/search") == "ok"
    assert slept == [7, 3]


def test_any_other_status_is_not_retried(monkeypatch):
    harness = _load_harness()
    calls: list[str] = []

    def fake_urlopen(url, timeout):
        calls.append(url)
        raise _http_error(400)

    monkeypatch.setattr(harness.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(harness.urllib.error.HTTPError, match="HTTP Error 400"):
        harness._open("https://space/search")
    assert calls == ["https://space/search"]
