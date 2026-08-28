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

Retries wait as long as the Hub says to, within a fixed total budget. HF meters **fixed 5-minute
windows** and reports the remaining seconds in a ``RateLimit`` header, so
``reset_after`` reads it and :func:`wait_before`'s exponential ladder (ADR-0033) is only the
fallback for failures that advise nothing. When the budget cannot cover the window the Hub named,
the fetch stops rather than retry early, because an early retry spends another request inside the
window it is waiting on — which is how all 10 retries across the two runs lost on 2026-08-11 failed.

Exit: 0 once every expected file is on disk, 1 when the state could not be fetched (ADR-0030).
"""

from __future__ import annotations

import argparse
import os
import re
import time
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from headstart import log
from headstart.ingest import REPO_ROOT

_log = log.get(__name__, __spec__)

# Up to five attempts, inside a total sleeping budget of 450s (ADR-0033 + its 2026-08-11
# amendment). Five is the ceiling, not a promise: the budget binds first whenever the Hub advises
# long windows, so a rate-limited fetch may abort after three.
# The exponential ladder — 30s → 60s → 120s → 240s — is now only the *fallback*, for failures the
# Hub does not put a reset time on; a 429 carries its own window and `reset_after` reads it.
# The budget bounds the *sum* of every wait, not any single one, so honouring a long Hub window
# cannot cost more wall time than the ladder already did. It does not make every caller safe:
# `scrape-plan` has a 10-minute job timeout that was measured against a ~40s job and never sized
# against 450s of waiting, so a full-budget outage can still kill it mid-sleep. This keeps that no
# worse than before rather than fixing it — see ADR-0033's amendment.
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
    ladder would answer it with 30–240 s). Clamped to what is left of ``_WAIT_BUDGET``, which
    bounds total sleeping at what the ladder already cost — not a guarantee against any particular
    job timeout (see the note on ``scrape-plan`` above). A ``0`` result is a delay, never a signal:
    whether any budget remains is the caller's question, asked of ``spent``.
    """
    wait = wait_before(attempt) if advised is None else advised
    return max(0, min(wait, _WAIT_BUDGET - spent))


def _response(exc: Exception) -> Any:
    """The HTTP response a Hub error carries — ``None`` unless it is an HTTP error at all."""
    return getattr(exc, "response", None)


def _headers(exc: Exception) -> dict[str, str]:
    """The error's response headers, keys folded to lower case.

    HF sends these lower-cased. The real response matches case-insensitively, but a plain mapping
    would not, so fold rather than depend on the caller's header type.
    """
    return {
        str(k).lower(): v
        for k, v in dict(getattr(_response(exc), "headers", None) or {}).items()
    }


def limiter_note(exc: Exception) -> str:
    """What the ``RateLimit`` header said on a 429 — including that there wasn't one.

    This reports the *fact*, not a verdict, because the inference behind it is weaker than it
    looks and the log is the wrong place to bury that. What is measured: on 2026-08-28 a plain
    200 from the datasets API carried ``ratelimit: "api";r=999;t=291`` beside
    ``ratelimit-policy: "fixed window";"api";q=1000;w=300``, and run 33159268268's five 429s
    carried a CloudFront request id and no ``RateLimit`` at all — which is why ``reset_after``
    found nothing and the ladder ran instead of a real window. What is NOT measured: whether a
    429 the documented limiter *does* send always carries the header. That is one 200 response,
    not a sample of limiter-sent 429s, so treat a missing header as "look upstream", never as
    proof the quota was fine.

    Why it earns a line: the two cases want opposite responses — a quota 429 is answered by
    waiting the window out, an edge refusal by retrying at all — and without it both render
    identically. Scoped to 429 so a 401 or a 503 does not collect a rate-limit verdict it has no
    bearing on, and whitespace-collapsed because ``reason_for`` folds the exception text *before*
    appending this, so a newline in a header value would otherwise split the annotation (ADR-0039).
    """
    response = _response(exc)
    if getattr(response, "status_code", None) != 429:
        return ""
    limit = " ".join(str(_headers(exc).get("ratelimit", "")).split())
    return f"; limiter said {limit}" if limit else "; no RateLimit header on the 429"


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
    return f"{type(exc).__name__}: {prefix}{detail}{limiter_note(exc)}"


def reset_after(exc: Exception) -> int | None:
    """Seconds until the rate-limit window reopens, as the Hub itself reports it — or ``None``.

    HF answers a 429 with ``RateLimit: "api";r=<remaining>;t=<seconds to reset>`` (the
    ``draft-ietf-httpapi-ratelimit-headers`` scheme) and enforces quotas over **fixed 5-minute
    windows** — measured live on 2026-08-11: ``ratelimit: "api";r=994;t=28`` alongside
    ``ratelimit-policy: "fixed window";"api";q=1000;w=300``. So ``t`` is the only wait that actually
    clears one, and an exponential guess is worse than useless: each early attempt spends another
    request inside the very window it is waiting on. That is why not one of the 10 retries on
    2026-08-11 recovered. Non-HTTP failures advise nothing and fall back to :func:`wait_before`.
    """
    headers = _headers(exc)
    # One header can carry several policies ('"default";r=50;t=30, "api";r=0;t=137') and it does
    # not say which bucket we blew, so take the longest reset: it is the only one guaranteed to
    # have cleared. Over-waiting is bounded by `retry_delay`'s budget; under-waiting is the bug.
    resets = [
        int(t) for t in re.findall(r"\bt=(\d+)", str(headers.get("ratelimit", "")))
    ]
    if resets:
        return max(resets)
    retry_after = str(headers.get("retry-after", "")).strip()
    # only the delay-seconds form; the HTTP-date form falls through to the ladder
    return int(retry_after) if retry_after.isdigit() else None


def remote_files(repo: str, token: str | None) -> list[str]:
    """Every file in the dataset repo, from a **single** Hub API request.

    ``list_repo_files`` goes through ``list_repo_tree(recursive=True)``, which pages at ~1,000
    entries, so it costs ``ceil(files / 1000)`` ``/tree/`` requests. ``repo_info(expand=["siblings"])``
    answers from ``/api/datasets/{id}`` in exactly one, whatever the file count.

    Be honest about the size of that: the repo's file count sawtooths with compaction — measured
    2026-08-11, 1,601 files before and **42** after — so the saving is 1 request per fetch at the
    top of the sawtooth and *zero* at the bottom. This is not on its own a cure for a
    1,000-per-5-minute budget. It earns its place by being constant rather than growing, and by
    being on the endpoint that 429'd both lost runs. Both listings were verified against the live
    repo on 2026-08-11: identical 42 files, and identical selections for every production pattern.

    Note the trade: this listing is unpaginated, so a Hub-side truncation would be silent where the
    tree walk would have kept paging — the reason to keep the ``siblings is None`` guard below
    strict rather than lenient.

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
    began = time.monotonic()  # the whole fetch, across every attempt and every wait
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
                # job's wall time swing 2-3x run to run. Report the retries and the waits
                # too — a success on attempt 4 costs `spent` seconds of sleeping that the
                # landing attempt's own duration does not include, and reporting only the
                # latter makes a five-minute stall read as a fast fetch.
                took = time.monotonic() - started
                # `started` resets each attempt, so `took` is only the attempt that landed.
                # The true cost also includes every failed attempt's own download time, which
                # `began` is here to capture — reporting `took + spent` would omit exactly that
                # and under-report a slow failure as a fast fetch.
                #
                # Seconds alone cannot tell a slower Hub apart from a bigger fetch, so report
                # the bytes to divide by. Sized from what landed rather than from the listing,
                # which costs no second Hub request — and `absent` being empty is what makes
                # every `wanted` file safe to stat here. Rate omitted when nothing landed: a
                # pattern the repo has no files for is a legitimate first run, not a stall.
                # Why it earns the line: docs/pipeline/2026-08-25_eight-run-log-review.md §2.
                #
                # Deliberately `took`, the same denominator the printed seconds use, so the two
                # numbers on the line agree and a reader can divide one by the other. It does
                # include the `remote_files` listing call, which is one request — timing the
                # download alone would be a truer rate but would print two figures that do not
                # reconcile, and nothing here has measured that request to be worth it.
                landed = sum((REPO_ROOT / f).stat().st_size for f in wanted)
                rate = f" ({landed / took / 1e6:.1f} MB/s)" if landed else ""
                retried = (
                    f" on attempt {attempt} of {_ATTEMPTS}"
                    f" (+{spent}s of it waiting, {time.monotonic() - began:.0f}s total)"
                    if attempt > 1
                    else ""
                )
                _log.info(
                    f"fetched {len(wanted)} file(s), {landed / 1e6:.0f} MB "
                    f"in {took:.0f}s{rate}{retried}: {' '.join(patterns)}"
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
            if advised is not None and wait < advised:
                # we cannot afford the window the Hub named, and retrying before it reopens is a
                # request we already know will 429 — the very habit that lost both runs. Stop.
                reason = (
                    f"{reason}; {_WAIT_BUDGET}s retry budget cannot cover "
                    f"the Hub's {advised}s window"
                )
                break
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
