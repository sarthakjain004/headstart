"""Tests for the guarded state fetch (headstart.ingest.state_fetch, ADR-0030).

The property under test is the one whose absence cost run 30304173982 its state: when the Hub
lists files that the fetch then fails to deliver, the caller must be told, not left with an empty
dir that reads as a legitimate first run. The remote listing is what makes that decidable, so the
two pure halves — which remote files a pattern set asks for, and which of those failed to land —
are tested directly; the fetch itself is I/O.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import headstart.ingest.state_fetch as sf

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


def test_reset_after_reads_the_hubs_own_window(monkeypatch) -> None:
    """HF answers a 429 with `RateLimit: "api";r=<left>;t=<seconds to reset>`. Quotas are fixed
    5-minute windows, so `t` is the only wait that actually clears one — guessing at it is what
    made all 10 retries of 2026-08-11 fail."""
    exc = _hub_error(_HF_429, 429)
    exc.response.headers = {"RateLimit": '"api";r=0;t=137'}  # type: ignore[attr-defined]
    assert sf.reset_after(exc) == 137


def test_reset_after_falls_back_to_retry_after() -> None:
    exc = _hub_error(_HF_429, 429)
    exc.response.headers = {"Retry-After": "90"}  # type: ignore[attr-defined]
    assert sf.reset_after(exc) == 90


def test_reset_after_is_none_when_the_hub_advises_nothing() -> None:
    # a timeout or a non-HTTP failure has no window to report — the ladder still applies
    assert sf.reset_after(_hub_error("connection reset", None)) is None
    assert sf.reset_after(_hub_error(_HF_429, 429)) is None


def test_remote_files_fails_closed_when_the_hub_omits_siblings(monkeypatch) -> None:
    """`siblings` is None whenever the Hub doesn't return it, and an empty listing would make
    `absent_locally` report nothing missing — the empty-state-reads-as-first-run bug ADR-0030
    exists to prevent. It must raise so the attempt retries instead."""
    import huggingface_hub

    monkeypatch.setattr(
        huggingface_hub,
        "repo_info",
        lambda *a, **k: type("I", (), {"siblings": None})(),
    )
    with pytest.raises(RuntimeError, match="siblings"):
        sf.remote_files("some/repo", token=None)


def test_remote_files_returns_every_repo_path(monkeypatch) -> None:
    import huggingface_hub

    siblings = [type("S", (), {"rfilename": f})() for f in _REMOTE]
    monkeypatch.setattr(
        huggingface_hub,
        "repo_info",
        lambda *a, **k: type("I", (), {"siblings": siblings})(),
    )
    assert sf.remote_files("some/repo", token=None) == _REMOTE


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
    assert sf.absent_locally(wanted, tmp_path) == [sorted(wanted)[0]]
