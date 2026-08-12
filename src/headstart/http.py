"""Shared pooled HTTP client + the reliable-fetch seam.

One ``curl_cffi`` Session per thread: keep-alive connections are reused across same-host
requests (5–7x faster than the stdlib's connect-per-request on the burst-heavy paths — every
board on ``boards-api.greenhouse.io``, every detail fetch on one tenant's host), and Chrome
impersonation lets the *same* client handle both plain JSON APIs and the TLS-fingerprinted
boards (Cloudflare/DataDome) — no second HTTP stack.

Sessions are thread-local because a libcurl session isn't safe to share across threads. The
scrapers run under thread pools (the pipeline per company, the detail passes per posting), so
each worker thread transparently gets and reuses its own pooled session.

``fetch`` is the reliable request. It retries transient failures — connection timeouts/resets
and 403/429/5xx responses — with backoff, but it does *not* retry a DNS failure (the host
doesn't exist; that's definitive). It returns whatever response finally settles, **including a
4xx/5xx**, so the caller classifies the outcome (lever branches on 404, the detail-fetchers map
non-200 to None, etc.). It raises ``RequestsError`` only when a transient network failure never
settles, or immediately on DNS. Retry lives here once; classification stays with the callers.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import Counter
from typing import Any

from curl_cffi import requests as _requests
from curl_cffi.requests import RequestsError  # re-exported for callers' except blocks

from headstart import log

__all__ = ["fetch", "fetch_async", "session", "RequestsError"]

_log = log.get(__name__)

_local = threading.local()
_TRANSIENT = {
    403,
    429,
    500,
    502,
    503,
    504,
}  # retryable HTTP statuses (403 = bot-wall blip)
_ATTEMPTS = 3
_DNS = 6  # curl CURLE_COULDNT_RESOLVE_HOST — host doesn't exist, never retried


def session() -> _requests.Session:
    """This thread's pooled session (created on first use)."""
    existing = getattr(_local, "session", None)
    if existing is None:
        existing = _requests.Session(impersonate="chrome")
        _local.session = existing
    return existing


# Retries counted by reason, because at INFO they were previously invisible: a board that
# settled only after five backoffs logged exactly like one that answered first time, so an ATS
# starting to rate-limit or bot-wall us produced NO signal until boards failed outright. 403 is
# the retryable status most worth watching for that reason. Counted, not per-board attributed —
# a plain lock beats threading state through both the sync and the async fetch paths, and the
# question these answer ("is a provider degrading?") is a per-run one.
_retries: Counter[str] = Counter()
_retries_lock = threading.Lock()


def retry_stats() -> Counter[str]:
    """A snapshot of retries by reason since the last reset."""
    with _retries_lock:
        return Counter(_retries)


def reset_retry_stats() -> None:
    """Zero the counters — a stage calls this once so its totals describe its own work."""
    with _retries_lock:
        _retries.clear()


def _retry_reason(why: str) -> str:
    """The coarse class a retry belongs to: what you would act on, not the exact message."""
    if "403" in why:
        return "403-wall"
    if "429" in why:
        return "429-ratelimit"
    if any(code in why for code in ("500", "502", "503", "504")):
        return "5xx"
    return "network"


def _note_retry(method: str, url: str, attempt: int, attempts: int, why: str) -> float:
    """Count one retry, log it at DEBUG, and return the backoff delay for the caller to sleep."""
    with _retries_lock:
        _retries[_retry_reason(why)] += 1
    delay = 1.5 * (attempt + 1)
    _log.debug(
        f"{method} {url} attempt {attempt + 1}/{attempts} {why}; "
        f"retrying in {delay:.1f}s"
    )
    return delay


def fetch(method: str, url: str, *, attempts: int = _ATTEMPTS, **kwargs: Any):
    """Make a request over the pooled session, retrying transient failures with backoff.

    Returns the settled response — any status, including 4xx/5xx — for the caller to classify.
    Retries 403/429/5xx and transient network errors (timeout, connection reset); does *not*
    retry a DNS failure. Raises ``RequestsError`` if a transient network error never settles
    (or immediately on DNS).
    """
    for attempt in range(attempts):
        try:
            response = session().request(method, url, **kwargs)
        except RequestsError as exc:
            if getattr(exc, "code", None) == _DNS or attempt == attempts - 1:
                raise
            time.sleep(_note_retry(method, url, attempt, attempts, f"failed ({exc})"))
            continue
        if response.status_code in _TRANSIENT and attempt < attempts - 1:
            time.sleep(
                _note_retry(
                    method, url, attempt, attempts, f"-> {response.status_code}"
                )
            )
            continue
        return response
    raise AssertionError(
        "unreachable: the final attempt returns or raises"
    )  # pragma: no cover


async def fetch_async(
    session: Any, method: str, url: str, *, attempts: int = _ATTEMPTS, **kwargs: Any
):
    """Async counterpart to :func:`fetch`: the same retry policy over a caller-supplied
    ``AsyncSession``, so concurrent same-host requests ride as multiplexed HTTP/2 streams on one
    connection. Returns the settled response for the caller to classify; retries 403/429/5xx and
    transient network errors with backoff; raises ``RequestsError`` on DNS or if it never settles.
    """
    for attempt in range(attempts):
        try:
            response = await session.request(method, url, **kwargs)
        except RequestsError as exc:
            if getattr(exc, "code", None) == _DNS or attempt == attempts - 1:
                raise
            await asyncio.sleep(
                _note_retry(method, url, attempt, attempts, f"failed ({exc})")
            )
            continue
        if response.status_code in _TRANSIENT and attempt < attempts - 1:
            await asyncio.sleep(
                _note_retry(
                    method, url, attempt, attempts, f"-> {response.status_code}"
                )
            )
            continue
        return response
    raise AssertionError("unreachable")  # pragma: no cover
