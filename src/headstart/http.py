"""Shared pooled HTTP client.

One ``curl_cffi`` Session per thread: keep-alive connections are reused across same-host
requests (5–7x faster than the stdlib's connect-per-request on the burst-heavy paths — every
board on ``boards-api.greenhouse.io``, every detail fetch on one tenant's host), and Chrome
impersonation lets the *same* client handle both plain JSON APIs and the TLS-fingerprinted
boards (Cloudflare/DataDome) — no second HTTP stack.

Sessions are thread-local because a libcurl session isn't safe to share across threads. The
scrapers run under thread pools (the pipeline per company, the detail passes per posting), so
each worker thread transparently gets and reuses its own pooled session.
"""

from __future__ import annotations

import threading
from typing import Any

from curl_cffi import requests as _requests
from curl_cffi.requests import RequestsError  # re-exported for callers' retry/except blocks

__all__ = ["get", "post", "session", "RequestsError"]

_local = threading.local()


def session() -> _requests.Session:
    """This thread's pooled session (created on first use)."""
    existing = getattr(_local, "session", None)
    if existing is None:
        existing = _requests.Session(impersonate="chrome")
        _local.session = existing
    return existing


def get(url: str, **kwargs: Any):
    return session().get(url, **kwargs)


def post(url: str, **kwargs: Any):
    return session().post(url, **kwargs)
