#!/usr/bin/env python3
"""Fetch pipeline state from the HF dataset, failing loudly when it does not arrive.

    python -m headstart.ingest.state_fetch 'data/embeddings/jobs/*' 'data/lancedb/*'

``snapshot_download(local_dir=…)`` does **not** raise when the Hub is unreachable: it warns
(``Returning existing local_dir … as remote repo cannot be accessed``) and returns the local path.
Every state dir is gitignored, so on a CI runner that fallback yields an *empty* state — which is
indistinguishable, to everything downstream, from a legitimate first run.

That is how run 30304173982 (2026-07-27) lost its prior state to a transient ``429 Too Many
Requests``: the fetch step logged success in 1s (20s and 14s either side), ``embed_merge`` wrote a
fresh manifest over the 11,558 vectors it had just embedded instead of 324,485, and ``index sync``
bootstrapped an empty table. Only a *second* 429, on the upload 80s later, stopped a 95%-empty index
from replacing the served one. Nothing in the chain was wrong on its own; no step ever asked whether
the state it was building on had actually been fetched.

So ask. The remote listing is the missing fact, and it fails closed where the download does not:
``remote_files`` raises on a 429 rather than falling back, and raises again if the Hub answers
without a ``siblings`` list at all. Requiring exactly what the Hub reports also needs no bootstrap
opt-out — a first run matches nothing, requires nothing, and proceeds.

Retries wait as long as the Hub says to. HF meters **fixed 5-minute windows** and reports the
remaining seconds in the ``RateLimit`` header of every 429, so ``reset_after`` reads it and
:func:`wait_before`'s exponential ladder (ADR-0033) is only the fallback for failures that advise
nothing. Guessing was actively harmful: an early retry spends another request inside the window it
is waiting on, which is how all 10 retries across the two runs lost on 2026-08-11 failed.

Exit: 0 once every expected file is on disk, 1 when the state could not be fetched (ADR-0030).
"""

from __future__ import annotations

import argparse
import os
import re
import time
from fnmatch import fnmatch
from pathlib import Path

from headstart import log
from headstart.ingest import REPO_ROOT

_log = log.get(__name__, __spec__)

# Up to five attempts, inside a total sleeping budget of 450s (ADR-0033 + its 2026-08-11
# amendment). Five is the ceiling, not a promise: the budget binds first whenever the Hub advises
# long windows, so a rate-limited fetch may abort after three.
# The exponential ladder — 30s → 60s → 120s → 240s — is now only the *fallback*, for failures the
# Hub does not put a reset time on; a 429 carries its own window and `reset_after` reads it.
# The budget is what the pipeline's job timeouts are sized against, so it is a ceiling on the sum
# of every wait, not on any single one: `state_fetch` runs in `scrape-plan` too, whose job timeout
# is 10 minutes. Spending it as the Hub directs rather than as we guess costs no extra wall time.
_ATTEMPTS = 5
_BACKOFF = 30
_BACKOFF_CAP = 300
_WAIT_BUDGET = 450


def wait_before(attempt: int) -> int:
    """Seconds to wait after failed ``attempt`` (1-based): exponential from ``_BACKOFF``, capped."""
    return min(_BACKOFF * 2 ** (attempt - 1), _BACKOFF_CAP)


def retry_delay(attempt: int, advised: int | None, spent: int) -> int:
    """Seconds to sleep before the next attempt.

    Prefers ``advised`` — what the Hub said its window needs — over the guessed ladder, including
    an advised ``0``, which means the window is open *now* and a wait would be pure loss (the
    ladder would answer it with 30–240 s). Clamped to what is left of ``_WAIT_BUDGET`` so no caller
    can outlive its job timeout. A ``0`` result is a delay, never a signal: whether any budget
    remains is the caller's question, asked of ``spent``.
    """
    wait = wait_before(attempt) if advised is None else advised
    return max(0, min(wait, _WAIT_BUDGET - spent))


def _response(exc: Exception) -> object | None:
    """The HTTP response a Hub error carries, if it is an HTTP error at all."""
    return getattr(exc, "response", None)


def reason_for(exc: Exception) -> str:
    """Why a fetch attempt failed, on **one line**, leading with the HTTP status when there is one.

    Both halves matter for the annotation. ``HfHubHTTPError`` stringifies over several lines with
    the CloudFront request id first and the status on line 3, and a GitHub ``::warning::`` renders
    only the first line (ADR-0039) — so plain ``{type}: {exc}`` published the one useless part and
    hid the ``429`` that named the fault. The status comes off ``exc.response``, not off the text.
    """
    status = getattr(_response(exc), "status_code", None)
    # every whitespace run collapsed to one space — an annotation stops at the first newline
    detail = " ".join(str(exc).split())
    prefix = f"HTTP {status} " if status else ""
    return f"{type(exc).__name__}: {prefix}{detail}"


def reset_after(exc: Exception) -> int | None:
    """Seconds until the rate-limit window reopens, as the Hub itself reports it — or ``None``.

    HF answers a 429 with ``RateLimit: "api";r=<remaining>;t=<seconds to reset>`` (the
    ``draft-ietf-httpapi-ratelimit-headers`` scheme) and enforces quotas over **fixed 5-minute
    windows**. So ``t`` is the only wait that actually clears one, and an exponential guess is worse
    than useless: each early attempt spends another request inside the very window it is waiting on.
    That is why not one of the 10 retries on 2026-08-11 recovered. Non-HTTP failures advise nothing
    and fall back to :func:`wait_before`.
    """
    headers = getattr(_response(exc), "headers", None) or {}
    # One header can carry several policies ('"default";r=50;t=30, "api";r=0;t=137') and it does
    # not say which bucket we blew, so take the longest reset: it is the only one guaranteed to
    # have cleared. Over-waiting is bounded by `retry_delay`'s budget; under-waiting is the bug.
    resets = [
        int(t) for t in re.findall(r"\bt=(\d+)", str(headers.get("RateLimit", "")))
    ]
    if resets:
        return max(resets)
    retry_after = str(headers.get("Retry-After", "")).strip()
    # only the delay-seconds form; the HTTP-date form falls through to the ladder
    return int(retry_after) if retry_after.isdigit() else None


def remote_files(repo: str, token: str | None) -> list[str]:
    """Every file in the dataset repo, from a **single** Hub API request.

    ``list_repo_files`` goes through ``list_repo_tree(recursive=True)``, which pages at ~1,000
    entries — two ``/tree/`` requests at our current 1,601 files, and another every ~1,000 the repo
    grows by. ``repo_info(expand=["siblings"])`` answers from ``/api/datasets/{id}`` in exactly one,
    whatever the file count. A modest saving against a 1,000-request-per-5-minute API budget, but it
    is on the endpoint that 429'd both runs lost on 2026-08-11, and it stops growing.

    Note the trade: this listing is unpaginated, so a Hub-side truncation would be silent where the
    tree walk would have kept paging. At ~1,601 files against a listing that serves far larger repos
    whole, that is not a live risk — but it is the reason to keep the ``siblings is None`` guard
    below strict rather than lenient.

    Fails closed. The Hub *omits* ``siblings`` rather than erroring, and ``DatasetInfo`` then holds
    ``None`` — which would make ``wanted`` empty, ``absent_locally`` report nothing missing, and the
    fetch claim success having downloaded nothing. That is precisely the empty-state-reads-as-a-
    first-run failure this module exists to prevent (ADR-0030), so treat it as a failed attempt.
    """
    from huggingface_hub import repo_info

    siblings = repo_info(
        repo, repo_type="dataset", expand=["siblings"], token=token
    ).siblings
    if siblings is None:
        raise RuntimeError(
            f"Hub returned no `siblings` listing for {repo} — refusing to read that as an empty repo"
        )
    return [s.rfilename for s in siblings]


def remote_matches(repo_files: list[str], patterns: list[str]) -> set[str]:
    """The repo-relative files the Hub reports for these patterns. ``fnmatch`` is what
    ``snapshot_download`` filters ``allow_patterns`` with, so ``*`` spans ``/`` here too — that is
    what lets ``data/lancedb/*`` reach the table's nested fragment files."""
    return {f for f in repo_files if any(fnmatch(f, p) for p in patterns)}


def absent_locally(wanted: set[str], root: str | Path) -> list[str]:
    """Which wanted files are not on disk under ``root`` — empty means the fetch landed."""
    return sorted(f for f in wanted if not (Path(root) / f).exists())


def fetch_state(repo: str, patterns: list[str], token: str | None) -> int:
    from huggingface_hub import snapshot_download

    spent = 0  # seconds slept so far, against _WAIT_BUDGET
    for attempt in range(1, _ATTEMPTS + 1):
        started = time.monotonic()
        advised: int | None = None  # what the Hub says to wait, when it says anything
        try:
            wanted = remote_matches(remote_files(repo, token), patterns)
            snapshot_download(
                repo,
                repo_type="dataset",
                local_dir=str(REPO_ROOT),
                allow_patterns=patterns,
                token=token,
            )
            absent = absent_locally(wanted, REPO_ROOT)
            if not absent:
                # the seconds are the point: HF download variance is what makes the merge
                # job's wall time swing 2-3x run to run (per-attempt, so a retried success
                # reports the attempt that landed, not the waits before it)
                _log.info(
                    f"fetched {len(wanted)} file(s) in "
                    f"{time.monotonic() - started:.0f}s: {' '.join(patterns)}"
                )
                return 0
            reason = (
                f"{len(absent)} of {len(wanted)} expected file(s) did not land, "
                f"e.g. {absent[0]}"
            )
        except Exception as exc:  # noqa: BLE001 — any Hub failure is retried the same way
            reason = reason_for(exc)
            advised = reset_after(exc)
        if attempt < _ATTEMPTS:
            # budget checked on `spent`, never on the wait: an advised 0 means "the window is
            # open now, retry immediately", which is a wait of 0 and not an exhausted budget
            if spent >= _WAIT_BUDGET:
                reason = f"{reason}; {_WAIT_BUDGET}s retry budget exhausted"
                break
            wait = retry_delay(attempt, advised, spent)
            spent += wait
            _log.warning(
                f"state fetch attempt {attempt} failed ({reason}); retrying in {wait}s"
                f"{' (Hub-advised)' if advised is not None else ''}"
            )
            time.sleep(wait)

    _log.error(
        # reason first: this renders as a ::error:: annotation, which is read left-to-right and
        # truncated, so the status has to beat the pattern list to the front (ADR-0039)
        f"ABORT: {reason} — could not fetch {' '.join(patterns)} from {repo}.\n"
        "Refusing to continue: the state dirs are gitignored, so proceeding would rebuild and "
        "publish from an empty store as if this were a first run."
    )
    return 1


def main() -> int:
    log.setup()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "patterns", nargs="+", help="allow_patterns to fetch (repo-relative globs)"
    )
    args = ap.parse_args()
    repo = os.environ.get("HF_DATASET")
    if not repo:
        ap.error("no dataset repo — set HF_DATASET")
    return fetch_state(repo, args.patterns, os.environ.get("HF_TOKEN"))


if __name__ == "__main__":
    raise SystemExit(main())
