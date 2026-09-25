#!/usr/bin/env python3
"""Fetch pipeline state from the HF dataset, failing loudly when it does not arrive.

    python -m headstart.ingest.state_fetch 'data/embeddings/jobs/*' 'data/lancedb/*'

Originally built around ``huggingface_hub.snapshot_download(local_dir=…)``, which does **not**
raise when the Hub is unreachable: it warns (``Returning existing local_dir … as remote repo
cannot be accessed``) and returns the local path. Every state dir is gitignored, so on a CI runner
that fallback yields an *empty* state — which is indistinguishable, to everything downstream, from
a legitimate first run.

That is how run 30304173982 (2026-07-27) lost its prior state to a transient ``429 Too Many
Requests``: the fetch step logged success in 1s (20s and 14s either side), ``embed_merge`` wrote a
fresh manifest over the 11,558 vectors it had just embedded instead of 324,485, and ``index sync``
bootstrapped an empty table. Only a *second* 429, on the upload 80s later, stopped a 95%-empty index
from replacing the served one. Nothing in the chain was wrong on its own; no step ever asked whether
the state it was building on had actually been fetched.

So ask, independent of whether the download itself would have told the truth. The remote listing is
the missing fact, and it fails closed where a silent download would not: :func:`_dataset_info`
raises on a 429 rather than falling back, and raises again if the Hub answers without a
``siblings`` list at all. Requiring exactly what the Hub reports also needs no bootstrap opt-out — a first run matches
nothing, requires nothing, and proceeds. ``snapshot_download`` itself is gone from the download path
as of 2026-09-07 (:func:`_download`, ADR-0085) — a single HTTP stream per file stalled hard on
whichever file in a pattern set was largest, 68-84% of ``merge``'s wall time in three measured runs
— but this listen-then-verify design does not depend on which download mechanism is under it.

What the listing cannot rule on is an empty *match*: a first run and an emptied or mistyped
``HF_DATASET`` look identical to it. ADR-0095 gives the dataset a witness that survives a failed
fetch — see :mod:`headstart.ingest.state_witness` — and it is consulted only in that one case.

Retries wait as long as the Hub says to, within a fixed total budget. HF meters **fixed 5-minute
windows** and reports the remaining seconds in a ``RateLimit`` header, so
``reset_after`` reads it and :func:`wait_before`'s exponential ladder (ADR-0033) is only the
fallback for failures that advise nothing. When the budget cannot cover the window the Hub named,
the fetch stops rather than retry early, because an early retry spends another request inside the
window it is waiting on — which is how all 10 retries across the two runs lost on 2026-08-11 failed.

Say which commit of the dataset the listing came from, too. It arrives in the same response, so
:func:`commit_note` costs no extra Hub request — and it is the one thing no stage reported. Run
35063022985's merge died on three HF 500s and published nothing, so run 35067130555 re-planned
from run 35058831217's state and *nothing in its logs said so*; it had to be reconstructed
afterwards from four independent signals.

**Read it in one direction only**, because CONTEXT.md §Write guard is right that a commit id
identifies no state here (ADR-0129): merge publishes four commits per run and the reclaim step
squashes the whole history about every 20 runs — the live dataset is one ``Super-squash`` commit
as of 2026-09-16. So a commit that *moved*, or a fresh publish time, proves nothing: a squash is a
commit, and a run that lands three of its four uploads and dies before ``data/state`` leaves a
minutes-old head over generation-old ledgers. A commit that has **not** moved, or a publish time a
cycle or more behind the run reading it, does prove nothing was committed in between — and that is
the originating incident's own shape, since a merge that published nothing stalls both. The
pipeline chains its own successor roughly hourly (ADR-0093), so that reading is available in one
run's log instead of a diff of two.

Deliberately not the *run that wrote it*: nothing under ``data/state/`` carries a run id, and the
one file that does — ``data/lancedb/_index_base.json`` (``index.write_base``) — describes the
**table**, not the ledgers. That is the whole reason, stated narrowly: the commit reported here is
no better against a partial publish, it is only cheaper and repo-wide.

Exit: 0 once every expected file is on disk, 1 when the state could not be fetched (ADR-0030).
"""

from __future__ import annotations

import argparse
import os
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from headstart import log
from headstart.ingest import REPO_ROOT, state_witness

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


def _age_label(seconds: float) -> str:
    """``14m`` / ``1h52m``. Minutes, because the run cadence is ~50-60 min (ADR-0093) and the
    difference worth seeing is one cycle; hours once that would read as an unscannable
    four-figure minute count."""
    minutes = max(0, int(seconds)) // 60
    return f"{minutes // 60}h{minutes % 60:02d}m" if minutes >= 60 else f"{minutes}m"


def commit_note(sha: str | None, published: datetime | None, now: datetime) -> str:
    """The dataset commit this listing came from, and how old it is.

    Sound in one direction only — the module docstring says which, and why a commit id is not a
    state identity here. The reading that holds is the incident's own: a commit that has not
    moved, or a publish time a cycle behind the run reading it, means nothing was committed in
    between.

    Both fields are optional on ``DatasetInfo`` and a fresh fork's repo has never been written
    (ADR-0095); ``last_modified`` is tz-aware on every live response but is normalised anyway,
    because this line reports what a run read and must never be what fails that run closed.
    """
    commit = f"at {sha[:12]}" if sha else "at an unnamed commit"
    if published is None:
        return f"{commit}, never published"
    if published.tzinfo is None:
        published = published.replace(tzinfo=UTC)
    stamp = published.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    age = _age_label((now - published).total_seconds())
    return f"{commit}, published {stamp} ({age} old)"


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


def retry_hub[T](what: str, call: Callable[[], T]) -> T:
    """Run one Hub call under ADR-0033's retry policy, and return its result.

    Extracted for callers that make a Hub request *outside* :func:`fetch_state` — currently
    ``state_guard``, whose ``record``/``verify`` bracket the fetch and sit on the critical path of
    the job that publishes everything. Before this, that guard made a single unretried
    ``repo_info`` call: a transient 429 or 5xx the fetch two lines later would have absorbed would
    instead kill the merge job outright, which is a reliability regression in the one stage that
    must not fail for a reason the Hub already told us how to wait out.

    :func:`fetch_state` deliberately keeps its own loop rather than calling this. Its retry covers
    the *download* as well as the listing, and its failure reasons are not all exceptions — a file
    that silently did not land is a retryable failure with nothing raised — so the two loops share
    the policy (:data:`_ATTEMPTS`, :data:`_WAIT_BUDGET`, :func:`retry_delay`, :func:`reason_for`,
    :func:`reset_after`) without sharing a shape one of them would have to be bent into.

    Waits are INFO, not WARNING. A retry can fire up to four times per call and twice per job, and
    ``log.py``'s rule is that WARNING is a budget spent against GitHub's 50 annotations per run —
    an exhausted ladder raises, and a failed step is already red without spending one.
    """
    spent = 0  # seconds slept so far, against _WAIT_BUDGET
    failure: Exception | None = None
    reason = ""
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 — any Hub failure is retried the same way
            failure, reason = exc, reason_for(exc)
            advised = reset_after(exc)
        if attempt == _ATTEMPTS:
            reason = f"{reason}; {_ATTEMPTS} attempts exhausted"
            break
        # Budget checked on `spent`, never on the wait — an advised 0 means "the window is open
        # now", which is a wait of 0 and not an exhausted budget. Same reading as `fetch_state`;
        # getting it backwards there is what ADR-0033's amendment fixed.
        if spent >= _WAIT_BUDGET:
            reason = f"{reason}; {_WAIT_BUDGET}s retry budget exhausted"
            break
        wait = retry_delay(attempt, advised, spent)
        if advised is not None and wait < advised:
            # Retrying before the Hub's window reopens is a request we already know will 429 —
            # the habit ADR-0033 was written to stop.
            reason = (
                f"{reason}; {_WAIT_BUDGET}s retry budget cannot cover "
                f"the Hub's {advised}s window"
            )
            break
        spent += wait
        _log.info(
            f"{what}: {reason} — attempt {attempt} of {_ATTEMPTS}, "
            f"waiting {wait}s ({spent}s of {_WAIT_BUDGET}s budget)"
        )
        time.sleep(wait)

    # Reported here rather than at each `break`, so it is one line per exhausted ladder instead of
    # one per attempt, and so `test_log_levels`' looped-annotation rule holds by construction
    # rather than by an allowlist entry. It has to exist at all because this function re-raises
    # and `state_guard.main` hands its exit code straight to `SystemExit`: without it, giving up
    # reaches the operator as a bare traceback with no annotation, indistinguishable from a guard
    # that never retried — the very thing this function was added to change. Reason first, because
    # an `::error::` is read left-to-right and truncated (ADR-0039).
    _log.error(f"ABORT: {reason} — giving up on {what}")
    assert (
        failure is not None
    )  # the loop only leaves via `return` or after `except` set this
    raise failure


def remote_files(repo: str, token: str | None) -> list[str]:
    """Every file in the dataset repo, from a **single** Hub API request.

    ``list_repo_files`` goes through ``list_repo_tree(recursive=True)``, which pages at ~1,000
    entries, so it costs ``ceil(files / 1000)`` ``/tree/`` requests. ``repo_info(files_metadata=True)``
    answers from ``/api/datasets/{id}`` in exactly one, whatever the file count — see
    :func:`_siblings` for why that parameter rather than ``expand=["siblings"]``.

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
    return [s.rfilename for s in _siblings(repo, token)]


def _dataset_info(repo: str, token: str | None) -> Any:
    """The Hub's whole answer about this dataset — the siblings *and* the revision they came
    from — from the one request :func:`remote_files` describes, with its fail-closed guard.

    Separate from :func:`_siblings` only because that answers a narrower question and two
    callers (``remote_files``, ``state_guard``) ask exactly it. :func:`fetch_state` wants the
    ``sha``/``last_modified`` beside the siblings, and they arrive in the same response, so
    reading them here is free where a second ``repo_info`` would be another request.
    """
    from huggingface_hub import repo_info

    info = repo_info(repo, repo_type="dataset", files_metadata=True, token=token)
    if info.siblings is None:
        raise RuntimeError(
            f"Hub returned no `siblings` listing for {repo} — refusing to read that as an empty repo"
        )
    return info


def _siblings(repo: str, token: str | None) -> list[Any]:
    """The listing behind :func:`remote_files`, with sizes — same one request, same fail-closed
    guard. Kept private and separate so callers that only need names (``remote_files``, tested and
    used standalone) don't carry the size-bearing shape, while ``state_guard`` can read sizes off
    this directly without a second Hub request for the same listing.

    ``files_metadata=True`` is what carries size: ``expand=["siblings"]`` alone reports every
    sibling's ``size`` as ``None`` (verified live against this dataset, 2026-09-07) and the two
    are mutually exclusive on ``repo_info``, so this replaces rather than adds to that call.
    """
    return _dataset_info(repo, token).siblings


def remote_matches(repo_files: list[str], patterns: list[str]) -> set[str]:
    """The repo-relative files the Hub reports for these patterns. ``fnmatch`` is what
    ``snapshot_download`` filters ``allow_patterns`` with, so ``*`` spans ``/`` here too — that is
    what lets ``data/lancedb/*`` reach the table's nested fragment files."""
    return {f for f in repo_files if any(fnmatch(f, p) for p in patterns)}


def absent_locally(wanted: set[str], root: str | Path) -> list[str]:
    """Which wanted files are not on disk under ``root`` — empty means the fetch landed."""
    return sorted(f for f in wanted if not (Path(root) / f).exists())


# ADR-0085's finding, applied here rather than only in scripts/fetch/pull_lancedb.py: a single
# HTTP stream stalls hard on a large file (measured 0.16 MB/s there against 2.17 MB/s aggregated
# across four concurrent ranged GETs) while short flows are fine. `snapshot_download` fetches each
# file as one stream, so it paid that stall on whichever file in a `wanted` set was largest —
# 68-84% of the entire `merge` job's wall time across three 2026-09-07 runs
# (docs/pipeline/2026-09-07_four-run-review-post-ua-fix.md §3), always the last one to two files.
# Below this size a single stream is fine (every run's small files landed in seconds); at or above
# it, fetch as concurrent ranged chunks instead.
_BIG_FILE_BYTES = 100_000_000
_CHUNK_BYTES = 32_000_000
_DOWNLOAD_WORKERS = 6
# pull_lancedb.py measured 120 of 2,834 file fetches failing at this same concurrency and all
# recovering on retry — routine 429/network blips, not absent files. A couple of quick inner
# attempts absorb those cheaply; without them, one blip on any single chunk aborts the whole
# ranged fetch and pays fetch_state's much slower 30-300s outer backoff for what a 2s retry here
# would have fixed.
_INNER_ATTEMPTS = 3
_INNER_BACKOFF_S = 2.0
# One entry per inner retry `_get_with_retry` absorbed, so `_download` can say how many blips the
# fetch rode out — invisible otherwise, since a retry that succeeds leaves no other trace. A list
# because `append` is atomic across the pool's threads.
_inner_retries: list[str] = []


def _get_with_retry(
    url: str, headers: dict[str, str], timeout: tuple[float, float]
) -> Any:
    import requests

    last: Exception = RuntimeError(
        "unreachable"
    )  # _INNER_ATTEMPTS >= 1 always overwrites this
    for attempt in range(_INNER_ATTEMPTS):
        try:
            r = requests.get(
                url, headers=headers, timeout=timeout, allow_redirects=True
            )
            r.raise_for_status()
            return r
        except (requests.RequestException, OSError) as exc:
            last = exc
            if attempt + 1 < _INNER_ATTEMPTS:
                _inner_retries.append(type(exc).__name__)
                time.sleep(_INNER_BACKOFF_S * (attempt + 1))
    raise last


def _fetch_whole(url: str, dest: Path, size: int, headers: dict[str, str]) -> None:
    """One small file, whole-body GET, written ``.tmp`` then renamed.

    Skips outright if ``dest`` already landed. ``fetch_state``'s outer retry loop re-calls
    :func:`_download` with the **full** ``wanted`` set on every attempt, so without this a
    transient failure on any one file would re-fetch every file that had already succeeded —
    including a big one :func:`_fetch_ranged` just spent minutes on, which is what this whole
    change exists to make cheap to retry around.

    The rename is the point: a kill mid-write leaves a ``.tmp``, never a short file at the real
    path — which the ``.exists()`` check above would otherwise find and skip, silently landing a
    truncated file as if it were complete. ``size`` closes the same hole for the body itself: a
    body shorter than the Hub reported must not rename into place undetected —
    :func:`absent_locally` only checks existence, never length.
    """
    if dest.exists():
        return
    tmp = dest.with_name(dest.name + ".tmp")
    r = _get_with_retry(url, headers, timeout=(15, 60))
    if size and len(r.content) != size:
        raise OSError(f"{dest.name}: got {len(r.content)} bytes, wanted {size}")
    with open(tmp, "wb") as fh:
        fh.write(r.content)
    tmp.rename(dest)
    # DEBUG: one line per small file is hundreds on a lancedb pull; `_download` reports progress.
    _log.debug(f"  landed {dest.name} ({len(r.content) / 1e6:.1f} MB)")


def _chunk_path(dest: Path, i: int) -> Path:
    return dest.with_name(f"{dest.name}.c{i:04d}")


def _fetch_ranged(url: str, dest: Path, size: int, headers: dict[str, str]) -> None:
    """One large file, fetched as concurrent ranged chunks then concatenated (ADR-0085).

    Skips outright if ``dest`` already landed — see :func:`_fetch_whole`'s docstring for why
    that matters across ``fetch_state``'s outer retries. Chunk files are NOT reused across calls:
    each call is a new outer attempt, and the remote may have been republished (even at the same
    size) during the backoff before it, so a leftover chunk would splice two versions of the file
    into one that still passes the size check. Discarded up front instead.
    """
    if dest.exists():
        return

    plan = [
        (i, off, min(off + _CHUNK_BYTES, size))
        for i, off in enumerate(range(0, size, _CHUNK_BYTES))
    ]
    for i, _, _ in plan:
        _chunk_path(dest, i).unlink(missing_ok=True)
    _log.info(f"  fetching {dest.name} ({size / 1e6:.0f} MB, {len(plan)} chunks)")

    def fetch_chunk(spec: tuple[int, int, int]) -> None:
        i, lo, hi = spec
        cf = _chunk_path(dest, i)
        want = hi - lo
        r = _get_with_retry(
            url, dict(headers, Range=f"bytes={lo}-{hi - 1}"), timeout=(20, 90)
        )
        if len(r.content) != want:
            raise OSError(
                f"chunk {i} of {dest.name}: got {len(r.content)} bytes, wanted {want}"
            )
        cf.write_bytes(r.content)

    with ThreadPoolExecutor(max_workers=_DOWNLOAD_WORKERS) as pool:
        futures = [pool.submit(fetch_chunk, c) for c in plan]
        for done, fut in enumerate(as_completed(futures), start=1):
            fut.result()  # re-raises on the pool's thread — same failure surface as a plain GET
            _log.info(f"    {done}/{len(plan)} chunks ({dest.name})")

    tmp = dest.with_name(dest.name + ".tmp")
    with open(tmp, "wb") as fh:
        for i, _, _ in plan:
            cf = _chunk_path(dest, i)
            fh.write(cf.read_bytes())
            cf.unlink()
    if tmp.stat().st_size != size:
        raise OSError(
            f"{dest.name}: assembled {tmp.stat().st_size} bytes, remote reports {size}"
        )
    tmp.rename(dest)
    _log.info(f"  landed {dest.name} ({size / 1e6:.0f} MB)")


def _download(
    repo: str, siblings: list[Any], wanted: set[str], token: str | None, root: Path
) -> None:
    """Fetch every ``wanted`` file under ``root`` — the download-side twin of
    ``scripts/fetch/pull_lancedb.py``, folded in here so ``join``/``merge``'s big pulls
    (``data/lancedb/*``, ``data/embeddings/jobs/*``) get the same treatment, not just a
    LanceDB-only ops script. Raises on any failure exactly as ``snapshot_download`` did, so
    :func:`fetch_state`'s retry/backoff/rate-limit handling needs no changes to keep working —
    ``requests.HTTPError`` carries ``.response`` the same shape ``reason_for``/``reset_after``
    already read generically off any exception.
    """
    if not wanted:
        return  # a pattern with no remote match — nothing to fetch, no Hub call needed for it

    from huggingface_hub import hf_hub_url

    sizes = {s.rfilename: (getattr(s, "size", None) or 0) for s in siblings}
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    small = [p for p in wanted if sizes.get(p, 0) < _BIG_FILE_BYTES]
    big = [p for p in wanted if sizes.get(p, 0) >= _BIG_FILE_BYTES]

    def fetch_small(path: str) -> None:
        dest = root / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        _fetch_whole(
            hf_hub_url(repo, path, repo_type="dataset"), dest, sizes[path], headers
        )

    _inner_retries.clear()
    step = max(1, len(small) // 10)  # about ten progress lines, however many files
    landed_bytes = 0
    with ThreadPoolExecutor(max_workers=_DOWNLOAD_WORKERS) as pool:
        futures = {pool.submit(fetch_small, p): p for p in small}
        for done, fut in enumerate(as_completed(futures), start=1):
            fut.result()
            landed_bytes += sizes[futures[fut]]
            if done % step == 0 or done == len(small):
                _log.info(
                    f"  {done}/{len(small)} small files, {landed_bytes / 1e6:.1f} MB"
                )

    for path in big:
        dest = root / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        _fetch_ranged(
            hf_hub_url(repo, path, repo_type="dataset"), dest, sizes[path], headers
        )
    if _inner_retries:
        _log.info(f"  {len(_inner_retries)} inner retries absorbed")


def fetch_state(repo: str, patterns: list[str], token: str | None) -> int:
    spent = 0  # seconds slept so far, against _WAIT_BUDGET
    first_run_noted = (
        False  # the bootstrap decision is announced once per fetch, not per attempt
    )
    began = time.monotonic()  # the whole fetch, across every attempt and every wait
    for attempt in range(1, _ATTEMPTS + 1):
        started = time.monotonic()
        advised: int | None = None  # what the Hub says to wait, when it says anything
        try:
            info = _dataset_info(repo, token)
            siblings = info.siblings
            listing = [s.rfilename for s in siblings]
            # Before the download, not after it: the listing is where the commit is known, so a
            # fetch that then dies still reports what it was building on. Inside the retry loop
            # for the same reason — a re-listing may see a commit the first did not, and on the
            # happy path it runs exactly once. INFO, not an annotation: this fires on every
            # healthy fetch and ADR-0039 spends WARNING on the ones that are not.
            _log.info(
                f"dataset commit: {repo} "
                f"{commit_note(info.sha, info.last_modified, datetime.now(UTC))}"
            )
            wanted = remote_matches(listing, patterns)
            # A pattern that matches nothing is the one case the listing cannot rule on: a genuine
            # first run and an emptied or mistyped `HF_DATASET` look identical to it. ADR-0030 says
            # exactly this and leaves the hole open for want of a witness that survives a failed
            # fetch; the dataset now carries one (ADR-0095).
            #
            # Asked **per pattern**, not of `wanted`. `wanted` is the union, so `merge`'s
            # `data/embeddings/jobs/* data/lancedb/*` would stay non-empty with the whole table
            # wiped, and the witness would never be consulted on the root that actually vanished.
            # Per-pattern, each root answers for itself.
            #
            # `break`, not `continue`: the answer is deterministic, so retrying would re-list four
            # more times to be told the same thing, spending the budget that a real rate-limit
            # would need. A witness that cannot be READ is a different case — it raises, lands in
            # the handler below, and is retried like any other Hub failure.
            barren = [p for p in patterns if not remote_matches(listing, [p])]
            witness = "does not cover them"
            if barren and state_witness.speaks_for(barren):
                roots = state_witness.published_roots(repo, token)
                claimed = state_witness.unwitnessed(barren, roots)
                witness = "absent" if roots is None else "does not claim them"
                if claimed:
                    reason = (
                        f"the Hub lists no files under {' '.join(claimed)}, but "
                        f"{state_witness.WITNESS_PATH} says this dataset published "
                        f"{'it' if len(claimed) == 1 else 'them'} — refusing to read that as a "
                        "first run"
                    )
                    break
            if barren and not first_run_noted:
                # The bootstrap decision, said out loud: until this line its only trace was a
                # `fetched 0 file(s)`, which reads the same as a healthy fetch of nothing.
                first_run_noted = True
                _log.warning(
                    f"no remote files match {' '.join(barren)}; witness {witness} — "
                    "proceeding as a first run"
                )
            _download(repo, siblings, wanted, token, REPO_ROOT)
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
            # INFO, like `retry_hub`'s wait: in a Hub outage every attempt restates one fault, and
            # the ABORT below is the one annotation that names it
            _log.info(
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
    log.context("state_fetch")
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
