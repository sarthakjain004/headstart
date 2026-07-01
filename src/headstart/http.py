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

import threading
import time
from typing import Any

from curl_cffi import requests as _requests
from curl_cffi.requests import RequestsError  # re-exported for callers' except blocks

__all__ = ["fetch", "session", "RequestsError"]

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
            time.sleep(1.5 * (attempt + 1))
            continue
        if response.status_code in _TRANSIENT and attempt < attempts - 1:
            time.sleep(1.5 * (attempt + 1))
            continue
        return response
    raise AssertionError(
        "unreachable: the final attempt returns or raises"
    )  # pragma: no cover
