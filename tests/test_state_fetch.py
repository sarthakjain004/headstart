"""Tests for the guarded state fetch (headstart.ingest.state_fetch, ADR-0030).

The property under test is the one whose absence cost run 30304173982 its state: when the Hub
lists files that the fetch then fails to deliver, the caller must be told, not left with an empty
dir that reads as a legitimate first run. The remote listing is what makes that decidable, so the
two pure halves — which remote files a pattern set asks for, and which of those failed to land —
are tested directly; the download itself is I/O.

The rest is what the Hub tells us and how we answer it (ADR-0033's amendment): the one-line failure
`reason_for` publishes to an annotation, the window `reset_after` reads out of a 429, and
`remote_files` refusing to read a missing `siblings` list as an empty repo.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

import headstart.ingest.state_fetch as sf


@pytest.fixture
def hub(monkeypatch):
    """A stand-in `huggingface_hub`, since it lives in the [alerts] extra and CI's quality job
    installs only [dev] — the same stubbing `test_space_app.py` uses to keep such tests running
    in CI rather than silently skipping."""
    module = types.ModuleType("huggingface_hub")
    monkeypatch.setitem(sys.modules, "huggingface_hub", module)
    return module


_REMOTE = [
    "README.md",
    "data/embeddings/jobs/manifest.json",
    "data/embeddings/jobs/meta.jsonl",
    "data/embeddings/jobs/embeddings.f32",
    "data/lancedb/jobs.lance/_versions/1.manifest",
    "data/lancedb/jobs.lance/data/abc.lance",
    "data/state/board_priority.csv",
]


def test_backoff_schedule_is_exponential_and_capped() -> None:
    """ADR-0033: waits of 30/60/120/240 between five attempts — 7.5 min total, enough to ride
    out the measured multi-minute 429 windows that a 90s budget lost 6 of 40 runs to. The cap
    keeps a would-be attempt 6+ wait bounded if _ATTEMPTS ever grows."""
    assert [sf.wait_before(n) for n in range(1, sf._ATTEMPTS)] == [30, 60, 120, 240]
    assert sf.wait_before(10) == 300  # capped, not 30 * 2**9
    assert sum(sf.wait_before(n) for n in range(1, sf._ATTEMPTS)) == 450


class _Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def _hub_error(message: str, status: int | None) -> Exception:
    exc = type("HfHubHTTPError", (Exception,), {})(message)
    if status is not None:
        exc.response = _Response(status)  # type: ignore[attr-defined]
    return exc


# verbatim shape of the real exception (runs 31454850705, 31473400252)
_HF_429 = (
    "(Amz CF ID: VrcvSjPK1lhRYmlWTmTHTEF_JJ7eQGpSQ43dZwvsvWKU1Z-yUMOiWw==)\n"
    "\n"
    "429 Too Many Requests for url: https://huggingface.co/api/datasets/"
    "imPoseidon/headstart-index/tree/main?recursive=true&expand=false."
)


def test_reason_is_one_line_so_an_annotation_shows_it_whole() -> None:
    """A GitHub ::warning::/::error:: renders only the first line (ADR-0039), and
    HfHubHTTPError stringifies with the CF request id first and the status on line 3 — which hid
    the only diagnostic that mattered in both runs lost on 2026-08-11."""
    assert "\n" not in sf.reason_for(_hub_error(_HF_429, 429))


def test_reason_leads_with_the_status_code() -> None:
    reason = sf.reason_for(_hub_error(_HF_429, 429))
    assert reason.startswith("HfHubHTTPError: HTTP 429")


def test_reason_survives_an_error_carrying_no_response() -> None:
    # not every failure is an HTTP one — a timeout or a DNS error has no status to report
    assert sf.reason_for(_hub_error("connection reset", None)) == (
        "HfHubHTTPError: connection reset"
    )


def test_reset_after_reads_the_hubs_own_window() -> None:
    """HF answers a 429 with `RateLimit: "api";r=<left>;t=<seconds to reset>`. Quotas are fixed
    5-minute windows, so `t` is the only wait that actually clears one — guessing at it is what
    made all 10 retries of 2026-08-11 fail."""
    exc = _hub_error(_HF_429, 429)
    exc.response.headers = {"RateLimit": '"api";r=0;t=137'}  # type: ignore[attr-defined]
    assert sf.reset_after(exc) == 137


def test_reset_after_takes_the_longest_of_several_policies() -> None:
    """One header can carry several buckets and doesn't say which we blew. Taking the first would
    retry inside a window that hasn't cleared — the very failure this reads the header to avoid."""
    exc = _hub_error(_HF_429, 429)
    exc.response.headers = {"RateLimit": '"default";r=50;t=30, "api";r=0;t=137'}  # type: ignore[attr-defined]
    assert sf.reset_after(exc) == 137


def test_reset_after_reads_the_header_hf_actually_sends() -> None:
    """Captured live 2026-08-11: HF sends these lower-cased, alongside
    `ratelimit-policy: "fixed window";"api";q=1000;w=300`. The real response matches keys
    case-insensitively; a plain mapping does not, so the lookup must not depend on the caller's
    header type."""
    exc = _hub_error(_HF_429, 429)
    exc.response.headers = {"ratelimit": '"api";r=994;t=28'}  # type: ignore[attr-defined]
    assert sf.reset_after(exc) == 28


def test_reset_after_falls_back_to_retry_after() -> None:
    exc = _hub_error(_HF_429, 429)
    exc.response.headers = {"Retry-After": "90"}  # type: ignore[attr-defined]
    assert sf.reset_after(exc) == 90


def test_reset_after_is_none_when_the_hub_advises_nothing() -> None:
    # a timeout or a non-HTTP failure has no window to report — the ladder still applies
    assert sf.reset_after(_hub_error("connection reset", None)) is None
    assert sf.reset_after(_hub_error(_HF_429, 429)) is None


def test_remote_files_fails_closed_when_the_hub_omits_siblings(hub) -> None:
    """`siblings` is None whenever the Hub doesn't return it, and an empty listing would make
    `absent_locally` report nothing missing — the empty-state-reads-as-first-run bug ADR-0030
    exists to prevent. It must raise so the attempt retries instead."""
    hub.repo_info = lambda *a, **k: type("I", (), {"siblings": None})()
    with pytest.raises(RuntimeError, match="siblings"):
        sf.remote_files("some/repo", token=None)


def test_remote_files_returns_every_repo_path(hub) -> None:
    siblings = [type("S", (), {"rfilename": f})() for f in _REMOTE]
    hub.repo_info = lambda *a, **k: type("I", (), {"siblings": siblings})()
    assert sf.remote_files("some/repo", token=None) == _REMOTE


def test_retry_delay_prefers_the_hubs_window_over_the_ladder() -> None:
    assert sf.retry_delay(attempt=1, advised=137, spent=0) == 137  # not the ladder's 30
    assert sf.retry_delay(attempt=1, advised=None, spent=0) == 30


def test_retry_delay_honours_an_advised_zero() -> None:
    """`t=0` is a window resetting right now. Treating it as "no advice" (falsy, not None) sent it
    to a needless 30-240s guess — the exact over-waiting this change exists to stop."""
    assert sf.retry_delay(attempt=1, advised=0, spent=0) == 0


def test_retry_delay_clamps_to_the_total_budget() -> None:
    """The budget is what the job timeouts are sized against — `state_fetch` also runs in
    `scrape-plan`, whose job timeout is 10 minutes, so no sequence of waits may exceed it."""
    assert sf.retry_delay(attempt=1, advised=300, spent=sf._WAIT_BUDGET - 100) == 100
    assert sf.retry_delay(attempt=1, advised=300, spent=sf._WAIT_BUDGET) == 0


def _fake_hub(
    hub, monkeypatch, tmp_path, *, fail_first: int, headers: dict
) -> list[int]:
    """Point `fetch_state` at a Hub that 429s `fail_first` times, and record what it sleeps."""
    calls = {"n": 0}
    slept: list[int] = []
    listing = ["data/state/board_priority.csv"]

    def repo_info(*a, **k):
        calls["n"] += 1
        if calls["n"] <= fail_first:
            exc = _hub_error(_HF_429, 429)
            exc.response.headers = headers  # type: ignore[attr-defined]
            raise exc
        return type(
            "I", (), {"siblings": [type("S", (), {"rfilename": listing[0]})()]}
        )()

    def snapshot_download(*a, **k):
        (tmp_path / listing[0]).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / listing[0]).write_text("x", encoding="utf-8")

    hub.repo_info = repo_info
    hub.snapshot_download = snapshot_download
    monkeypatch.setattr(sf, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sf.time, "sleep", slept.append)
    return slept


def test_fetch_recovers_once_the_advised_window_passes(
    hub, monkeypatch, tmp_path
) -> None:
    """The 2026-08-11 case end to end: one 429 carrying a reset, then the window reopens."""
    slept = _fake_hub(
        hub,
        monkeypatch,
        tmp_path,
        fail_first=1,
        headers={"RateLimit": '"api";r=0;t=137'},
    )
    assert sf.fetch_state("repo", ["data/state/*"], token=None) == 0
    assert slept == [137]  # the Hub's window, not the ladder's 30


def test_fetch_fails_closed_and_stays_inside_the_budget(
    hub, monkeypatch, tmp_path
) -> None:
    """A window that never reopens must still abort, and must not sleep past the budget.

    It must also not spend its last 150s on a retry it *knows* is early: with a 300s window the
    budget affords one full wait and part of a second, and a truncated wait buys a request that is
    guaranteed to 429 — the habit that lost both runs. So one sleep, then stop."""
    slept = _fake_hub(
        hub,
        monkeypatch,
        tmp_path,
        fail_first=99,
        headers={"RateLimit": '"api";r=0;t=300'},
    )
    assert sf.fetch_state("repo", ["data/state/*"], token=None) == 1
    assert slept == [300]  # not [300, 150]
    assert sum(slept) <= sf._WAIT_BUDGET


def test_fetch_retries_immediately_on_an_advised_zero(
    hub, monkeypatch, tmp_path
) -> None:
    """`t=0` means the window is open now. Reading that 0 as "no budget left" aborted the whole
    fetch after a single attempt — a lost run, and worse than the guess it replaced."""
    slept = _fake_hub(
        hub, monkeypatch, tmp_path, fail_first=1, headers={"RateLimit": '"api";r=0;t=0'}
    )
    assert sf.fetch_state("repo", ["data/state/*"], token=None) == 0
    assert slept == [0]


def test_fetch_falls_back_to_the_ladder_when_unadvised(
    hub, monkeypatch, tmp_path
) -> None:
    slept = _fake_hub(hub, monkeypatch, tmp_path, fail_first=99, headers={})
    assert sf.fetch_state("repo", ["data/state/*"], token=None) == 1
    assert slept == [30, 60, 120, 240]


def test_remote_matches_selects_only_matching_files() -> None:
    """A pattern set asks for a subset of what the repo holds — including nested paths, since
    `data/lancedb/*` must reach the table's fragment files, not just its top level."""
    assert sf.remote_matches(_REMOTE, ["data/embeddings/jobs/*", "data/lancedb/*"]) == {
        "data/embeddings/jobs/manifest.json",
        "data/embeddings/jobs/meta.jsonl",
        "data/embeddings/jobs/embeddings.f32",
        "data/lancedb/jobs.lance/_versions/1.manifest",
        "data/lancedb/jobs.lance/data/abc.lance",
    }
    assert sf.remote_matches(_REMOTE, ["data/state/*"]) == {
        "data/state/board_priority.csv"
    }


def test_remote_matches_is_empty_when_the_repo_has_no_such_state() -> None:
    """The genuine first run: nothing on the Hub matches, so nothing is required and the caller
    proceeds. This is why the guard needs no bootstrap opt-out flag."""
    assert sf.remote_matches(["README.md"], ["data/embeddings/jobs/*"]) == set()


def test_absent_locally_flags_state_that_did_not_land(tmp_path: Path) -> None:
    """The regression: the Hub listed a store, the fetch delivered nothing (the offline fallback
    returns the empty local dir without raising), so every wanted file is absent."""
    wanted = sf.remote_matches(_REMOTE, ["data/embeddings/jobs/*"])
    assert sf.absent_locally(wanted, tmp_path) == sorted(wanted)


def test_absent_locally_is_empty_once_every_file_landed(tmp_path: Path) -> None:
    wanted = sf.remote_matches(_REMOTE, ["data/embeddings/jobs/*"])
    for rel in wanted:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x", encoding="utf-8")
    assert sf.absent_locally(wanted, tmp_path) == []


def test_absent_locally_catches_a_partial_fetch(tmp_path: Path) -> None:
    """A half-delivered store is as unpublishable as an empty one — one file short still aborts."""
    wanted = sf.remote_matches(_REMOTE, ["data/embeddings/jobs/*"])
    landed = sorted(wanted)[1:]
    for rel in landed:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x", encoding="utf-8")
    assert sf.absent_locally(wanted, tmp_path) == [min(wanted)]
