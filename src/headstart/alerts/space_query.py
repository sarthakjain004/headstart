"""Ask the deployed Space for a Subscription's newly-seen Jobs (ADR-0035).

`newly_seen(base, sub, after)` is the interface. Behind it: the parameter shape, the
`first_seen_after` cutoff, the over-request, and a wake-and-retry budget.

**Why call the Space instead of ranking here.** The scores in a Digest are then the same
numbers the same search shows in the browser, and the ranking rules stay in one place
(`headstart/search.py`, ADR-0005's prefixes included). The cost is that the Space must be
awake — and the merge job restarts it at the end of every pipeline run, so this always
arrives at a cold Space. Hence the retry budget, in the shape ADR-0033 established: waits
sized to a cold start (~1 min), not to a network blip.

**Why k=100.** That is the Space's page cap (`JobSearch.max_k`). Asking for the ceiling costs nothing and
leaves headroom if a lagging deploy ignores `first_seen_after` and `shortlist` has to do
the cut itself.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from .store import Subscription

K = 100  # the Space's page cap (JobSearch.max_k)
_TIMEOUT = 120  # a cold Space reloads the index and the encoder before it answers
_WAITS = (15, 30, 60)  # three retries, sized to a Space cold start
# The Space's own permanent answers: 401 from the sign-in wall, 400 from an invalid
# filter. Waiting turns neither into rows. Deliberately an allowlist rather than "all 4xx"
# — what HF's edge returns while the Space is still waking is not something this side gets
# to assume, and anything unlisted keeps the full cold-start budget.
_PERMANENT_HTTP = {400, 401}


class SearchUnavailable(Exception):
    """The Space could not be reached, or did not answer with rows."""


def auth_headers() -> dict[str, str]:
    """The service credential the Space's wall accepts — env-derived, so it is testable.

    This run has no Google identity, so it presents the shared secret instead — see the
    amendment to ADR-0042, which is where the wall's exception for it is recorded. Unset
    sends no header at all rather than an empty bearer: absent reads as anonymous, where
    ``Bearer `` reads as a malformed credential.
    """
    token = os.environ.get("ALERTS_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _is_permanent_failure(exc: Exception) -> bool:
    """Whether waiting cannot fix this — a rejected credential, an invalid filter.

    The retry ladder is sized to a Space cold start (ADR-0033), so spending it on these
    only delays the same failure: one wrong credential cost 105s per Subscription, on
    every Subscription, for eight consecutive runs.
    """
    return isinstance(exc, urllib.error.HTTPError) and exc.code in _PERMANENT_HTTP


def _fetch(url: str) -> list[dict[str, Any]]:
    req = urllib.request.Request(url, headers=auth_headers())
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as response:
        return json.load(response)


def request_url(base: str, sub: Subscription, after: str) -> str:
    """The `/search` URL for this Subscription's new Jobs — pure, so it is testable."""
    params = {"q": sub.query, "k": str(K), "first_seen_after": after}
    params.update(sub.search_filters)
    return f"{base.rstrip('/')}/search?{urllib.parse.urlencode(params)}"


def newly_seen(
    base: str,
    sub: Subscription,
    after: str,
    fetch: Callable[[str], list[dict[str, Any]]] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    """Rows this Subscription's Query matches that were first seen after `after`.

    Retries a cold or briefly-unreachable Space; raises :class:`SearchUnavailable` once the
    budget is spent, so the run can skip one Subscription without advancing its Watermark.
    """
    url = request_url(base, sub, after)
    call = fetch or _fetch
    for attempt, wait in enumerate((*_WAITS, None), start=1):
        try:
            rows = call(url)
        except Exception as exc:  # noqa: BLE001 — anything but _PERMANENT_HTTP retries
            if wait is None or _is_permanent_failure(exc):
                raise SearchUnavailable(f"{type(exc).__name__}: {exc}") from exc
            print(
                f"[alerts] search attempt {attempt} failed ({type(exc).__name__}); "
                f"retrying in {wait}s",
                flush=True,
            )
            sleep(wait)
            continue
        if not isinstance(rows, list):
            raise SearchUnavailable(f"unexpected reply: {str(rows)[:120]}")
        return rows
    return []  # unreachable: the last attempt either returns or raises
