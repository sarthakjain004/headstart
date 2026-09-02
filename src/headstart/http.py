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
and 403/405/429/5xx responses — with backoff, honouring a ``Retry-After`` when the host sends one.
It does *not* retry a DNS failure (the host doesn't exist; that's definitive). It returns whatever
response finally settles, **including a 4xx/5xx**, so the caller classifies the outcome (lever
branches on 404, the detail-fetchers map non-200 to None, etc.). It raises ``RequestsError`` only
when a transient network failure never settles, or immediately on DNS. Retry lives here once;
classification stays with the callers.

Retry is not the last rung. An ATS that meters per origin can wall a shard outright, and no number
of attempts from the same IP recovers that — so a scraper may opt a request into the **spare-egress
fallback** (``egress_group``), which escalates from "try again" to "try from somewhere else"
(:mod:`headstart.spare_egress`). Only the opted-in ATS moves; everything else keeps its direct route.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import Counter
from typing import Any

from curl_cffi import requests as _requests
from curl_cffi.requests import RequestsError  # re-exported for callers' except blocks

from headstart import log, spare_egress

__all__ = ["TRANSIENT", "RequestsError", "fetch", "fetch_async", "session"]

_log = log.get(__name__)

_local = threading.local()
#: Statuses worth another attempt for *any* caller: a bot-wall blip (403/405, ADR-0047), an
#: explicit rate limit, or a server-side failure. Public and overridable per caller via
#: ``retry_on``: a host that needs a different status retried extends this set rather than
#: replacing it. (Workday's 400 was the one live extension, ADR-0098; ADR-0103 reverted it once
#: the 400 turned out to be a stale session cookie, not a throttle — so the seam has no live
#: extender today, but stays because it is not host-specific.)
TRANSIENT: frozenset[int] = frozenset({403, 405, 429, 500, 502, 503, 504})
_ATTEMPTS = 3

#: Extra attempts a request may earn back after a rotation wait cost it one. `spare_egress.rotate`
#: now waits for a fresh IP instead of handing back the spent one, and that wait is only worth
#: having if the attempt it was queueing to spend survives it — otherwise a request burns its whole
#: budget waiting and never tries a working route. Named for what it caps: *earned attempts*, not
#: waits. A request may wait more often than this and earn nothing, which is the intended shape —
#: a Board that every IP refuses must run out of budget rather than retry forever.
_MAX_EARNED_ATTEMPTS = 2
# Cap on an honoured Retry-After: past this, waiting costs more than the request buys, and a
# shard's whole budget is 60 minutes.
_MAX_RETRY_AFTER = 30.0
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
# starting to rate-limit or bot-wall us produced NO signal until boards failed outright. The wall
# statuses (403/405) and 429 are the ones most worth watching for that reason. Counted, not
# per-board attributed — a plain lock beats threading state through both fetch paths, and the
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


# --- spare egress ------------------------------------------------------------------------------
# An ATS that meters per origin hands each shard its own budget (ADR-0047). When a shard spends
# one, every remaining Board of that ATS on that shard is lost for the run — so the wall statuses
# escalate one step further than a retry: onto a second egress IP. The registry of which groups are
# walled, and the dial itself, live in `spare_egress`; `fetch` only decides when to consult them.
#
# The two knobs are deliberately **orthogonal**, because Eightfold needs exactly one of each:
#   egress_group -> "route this request with that group, once the group is walled"
#   egress_on    -> "these statuses, seen here, are what marks the group walled"
# A request naming a group with an empty `egress_on` therefore rides the spare egress but can never
# trigger it — which is what the API-availability probe wants (ADR-0063).
#
# There are two routes, not three: **direct, then a rotating spare egress.** The spare egress is
# not a fixed second IP you fall onto and stay on — it is a supply of IPs. A wall on the direct
# route moves the group onto it; a wall seen *through* it means that IP is spent too, so it moves
# again. It keeps moving for as long as it keeps being refused.
#
# Rotation is bounded by a cooldown rather than by an attempt number, because "keep rotating" and
# "restart the daemon every few seconds" are the same instruction without one: each rotation is a
# `systemctl restart warp-svc` costing seconds, and a shard meeting 429s continuously would spend
# its budget bouncing the tunnel instead of scraping. Concurrent workers coalesce onto a single
# rotation (a generation counter in `spare_egress`); the cooldown is what bounds *successive* ones,
# and it also buys each new IP a fair trial before we give up on it.


_RETRY_CLASS = {
    403: "403-wall",
    405: "405-wall",  # kept apart from 403: this is the shape Eightfold's edge returns
    429: "429-ratelimit",
}


def _retry_reason(status: int | None) -> str:
    """The coarse class a retry belongs to: what you would act on, not the exact message.

    Keyed on the **status**, never on the log text. It was a substring test until 2026-08-31, and
    libcurl writes numbers into its errors, so a *network* failure could land in a status bucket:
    verified by replaying the old classifier, `Operation timed out after 30405 milliseconds` was
    counted as a 405 bot-wall and `...30502...` as a 5xx. Small, but these buckets are the signal
    for "is this ATS degrading", so it has to count the thing it names — and a caller that extends
    ``retry_on`` past `TRANSIENT` gets an honest `http-{status}` line rather than someone else's.
    """
    if status is None:
        return "network"
    if status in _RETRY_CLASS:
        return _RETRY_CLASS[status]
    if 500 <= status < 600:
        return "5xx"
    # Unreachable today - the classes above cover `TRANSIENT` plus workday's 400 -
    # but a future `retry_on` extension must show up as its own line rather than being
    # filed under someone else's, which is the failure this function was just fixed for.
    return f"http-{status}"


def _rotate_for(board: str | None, earned: int, deadline: float | None = None) -> int:
    """Move to a fresh egress IP; return the extra attempts this request thereby earned (0 or 1).

    Sync, and called from the async path through ``asyncio.to_thread``, so the two retry loops
    share one policy rather than drifting apart.

    An attempt is earned only when a **fresh** IP came back. `rotate` returns exactly that, and it
    doubles as "this call cost the caller time": no path reaches a fresh IP without paying for it
    (see `rotate`). A caller that waited the cap out and is still on the spent route gets nothing —
    it has no new route to retry on, and crediting it would turn a hard wall into a retry loop.
    """
    fresh = spare_egress.rotate(board, deadline=deadline)
    return 1 if fresh and earned < _MAX_EARNED_ATTEMPTS else 0


def _retry_after(response: Any) -> float | None:
    """The response's ``Retry-After`` in seconds, when it gives one as a delta and it is sane.

    Only the delta-seconds form is read: the HTTP-date form is rare in practice and parsing it
    buys nothing a capped backoff doesn't already give. Clamped to :data:`_MAX_RETRY_AFTER`,
    because a host asking for several minutes is asking for longer than the shard's whole budget.

    ``isdecimal`` rather than ``isdigit``: the latter is true for characters like ``²`` that
    ``float()`` then rejects, which would raise out of ``fetch`` on a malformed header.

    A literal ``0`` falls back to the local curve rather than being honoured. Taken at face value
    it means "retry immediately", which on a rate-limit wall is three attempts back-to-back — the
    opposite of what reading the header is for.
    """
    raw = (response.headers.get("Retry-After") or "").strip()
    if not raw.isdecimal():
        return None
    seconds = float(raw)
    if seconds <= 0:
        return None
    return min(seconds, _MAX_RETRY_AFTER)


def _note_retry(
    method: str,
    url: str,
    attempt: int,
    attempts: int,
    why: str,
    retry_after: float | None,
    status: int | None,
) -> float:
    """Count one retry, log it at DEBUG, and return the backoff delay for the caller to sleep.

    A rate-limited host that says how long to wait is believed over the local backoff curve — it
    knows its own window, and guessing shorter just burns another attempt against the wall.
    """
    with _retries_lock:
        _retries[_retry_reason(status)] += 1
    delay = retry_after if retry_after is not None else 1.5 * (attempt + 1)
    _log.debug(
        f"{method} {url} attempt {attempt + 1}/{attempts} {why}; "
        f"retrying in {delay:.1f}s"
    )
    return delay


def _severed_by_our_rotation(proxied: bool, before: int, earned: int) -> int:
    """Extra attempts a connection error earns back when *we* caused it (0 or 1).

    A rotation is a ``systemctl restart`` of the tunnel, so requests **riding that tunnel** when it
    goes die with a connection error — 27 of them in run 32249345870, against zero in the run
    before pagination fanned out. Those retries were already happening; what was missing is that
    they cost an attempt. A wall earns one back through :func:`_rotate_for` because the origin
    refused us; a socket *we* tore down should too, because the origin never got a say.

    ``proxied`` is load-bearing, not a belt-and-braces check. WARP runs in **proxy** mode — a
    SOCKS5 listener on one port — so restarting it cannot sever a connection that never went
    through it, while the rotation counter is process-global and moves for every ATS at once.
    Without this the overwhelmingly common direct request would claim a free attempt off an
    unrelated ATS's rotation.

    The generation brackets the request but not exactly: one landing between the snapshot and the
    socket opening grants a refund for an error it did not cause. That is a false *positive*, not
    a missed refund, and :data:`_MAX_EARNED_ATTEMPTS` bounds it — the same cap as a wall-earned
    attempt, so a shard whose rotations keep landing mid-request still runs out of budget rather
    than retrying forever.
    """
    if not proxied:
        return 0
    return (
        1
        if spare_egress.generation() != before and earned < _MAX_EARNED_ATTEMPTS
        else 0
    )


def fetch(
    method: str,
    url: str,
    *,
    attempts: int = _ATTEMPTS,
    egress_group: str | None = None,
    egress_on: frozenset[int] = frozenset(),
    egress_board: str | None = None,
    retry_on: frozenset[int] = TRANSIENT,
    **kwargs: Any,
):
    """Make a request over the pooled session, retrying transient failures with backoff.

    Returns the settled response — any status, including 4xx/5xx — for the caller to classify.
    Retries 403/405/429/5xx and transient network errors (timeout, connection reset), honouring a
    ``Retry-After`` delta over the local backoff curve; does *not* retry a DNS failure. Raises
    ``RequestsError`` if a transient network error never settles (or immediately on DNS).

    ``egress_group`` opts this request into the spare-egress fallback: a response in ``egress_on``
    marks that group walled, and this and every later request naming it are routed through the
    spare egress for the rest of the process (see the block above). Omitting it — every caller that
    has not opted in — leaves behaviour byte-for-byte unchanged. Passing a group with an empty
    ``egress_on`` is *not* the same thing: that request still rides the spare egress once something
    else has walled the group, it just can never do the walling itself.

    ``retry_on`` overrides which statuses earn another attempt, defaulting to :data:`TRANSIENT`.
    Pass a *superset* to opt one host into retrying a status nobody else should — the only live
    case is workday's 400, which is a throttle there and a malformed request almost everywhere
    else (ADR-0098). Retries are counted by class, so `retry_stats()` says whether it paid.

    ``egress_on`` is expected to be a subset of ``retry_on``. A status outside it would be
    marked but never retried, so this request would settle on the wall it just reported — the mark
    would still help the *next* Board, but the caller should not expect a second attempt.

    Marking is deliberately **not** conditional on retry budget. A wall seen on the final attempt
    still fails *this* request, but it is exactly as informative about the origin as one seen on
    the first, and recording it is what spares every subsequent Board of that ATS the same three
    attempts.
    """
    budget, attempt, proxied = attempts, 0, False
    while attempt < budget:
        proxy = spare_egress.proxy_for(egress_group)
        proxied = proxied or proxy is not None
        routed = (
            {**kwargs, "proxies": {"http": proxy, "https": proxy}} if proxy else kwargs
        )
        generation = spare_egress.generation()
        try:
            response = session().request(method, url, **routed)
        except RequestsError as exc:
            budget += _severed_by_our_rotation(
                proxy is not None, generation, budget - attempts
            )
            if getattr(exc, "code", None) == _DNS or attempt == budget - 1:
                if proxied and egress_group is not None:
                    spare_egress.note_settled(egress_group, None, egress_on)
                raise
            time.sleep(
                _note_retry(method, url, attempt, budget, f"failed ({exc})", None, None)
            )
            attempt += 1
            continue
        if proxy and egress_group is not None:
            spare_egress.note_routed(egress_group)
        if egress_group is not None and response.status_code in egress_on:
            spare_egress.mark_walled(egress_group, response.status_code)
        if response.status_code in retry_on and attempt < budget - 1:
            # Already riding the spare egress and still walled: the second IP is spent too, so the
            # last rung moves again rather than spending a third attempt on a known-bad route.
            if proxy and egress_group is not None and response.status_code in egress_on:
                budget += _rotate_for(egress_board, budget - attempts)
            time.sleep(
                _note_retry(
                    method,
                    url,
                    attempt,
                    budget,
                    f"-> {response.status_code}",
                    _retry_after(response),
                    response.status_code,
                )
            )
            attempt += 1
            continue
        if proxied and egress_group is not None:
            spare_egress.note_settled(egress_group, response.status_code, egress_on)
        return response
    raise AssertionError(
        "unreachable: the final attempt returns or raises"
    )  # pragma: no cover


async def fetch_async(
    session: Any,
    method: str,
    url: str,
    *,
    attempts: int = _ATTEMPTS,
    egress_group: str | None = None,
    egress_on: frozenset[int] = frozenset(),
    egress_board: str | None = None,
    retry_on: frozenset[int] = TRANSIENT,
    **kwargs: Any,
):
    """Async counterpart to :func:`fetch`: the same retry policy over a caller-supplied
    ``AsyncSession``, so concurrent same-host requests ride as multiplexed HTTP/2 streams on one
    connection. Returns the settled response for the caller to classify; retries 403/405/429/5xx
    and transient network errors with backoff (honouring ``Retry-After``); raises
    ``RequestsError`` on DNS or if it never settles.

    ``retry_on`` behaves exactly as it does on :func:`fetch`, and matters more here: workday's
    400s land overwhelmingly on the detail pass, which is this path (ADR-0098).

    ``egress_group``/``egress_on`` carry the spare-egress fallback (ADR-0063) with :func:`fetch`'s
    exact semantics — one shared wall registry, so a wall the sync listing pass marks routes the
    async detail pass and vice versa. This path is where the seam matters most: the detail passes
    are the many-streams-per-host traffic that spends an Origin budget, and until this existed
    they kept hammering the walled IP while the sync path had already moved (run 32146017194:
    37,688 sync requests carried, every async one still direct).

    Three deliberate blocking choices, safe because ``fan_out_async`` runs one event loop per
    Board inside its own worker thread: ``proxy_for``'s bounded gate-wait may pause this loop
    during a rotation — every stream on it targets the walled origin, so waiting *is* the work;
    when this path is the *first* to see the wall, ``proxy_for`` dials WARP inline on the loop
    thread (bounded by the dial's own timeouts) for the same reason; and ``rotate()`` (a
    ``systemctl`` round-trip) is pushed to a thread so the pause it imposes is the gate's bounded
    wait, not an unbounded subprocess.
    """
    budget, attempt, proxied = attempts, 0, False
    while attempt < budget:
        proxy = spare_egress.proxy_for(egress_group)
        proxied = proxied or proxy is not None
        routed = (
            {**kwargs, "proxies": {"http": proxy, "https": proxy}} if proxy else kwargs
        )
        generation = spare_egress.generation()
        try:
            response = await session.request(method, url, **routed)
        except RequestsError as exc:
            budget += _severed_by_our_rotation(
                proxy is not None, generation, budget - attempts
            )
            if getattr(exc, "code", None) == _DNS or attempt == budget - 1:
                if proxied and egress_group is not None:
                    spare_egress.note_settled(egress_group, None, egress_on)
                raise
            await asyncio.sleep(
                _note_retry(method, url, attempt, budget, f"failed ({exc})", None, None)
            )
            attempt += 1
            continue
        if proxy and egress_group is not None:
            spare_egress.note_routed(egress_group)
        if egress_group is not None and response.status_code in egress_on:
            spare_egress.mark_walled(egress_group, response.status_code)
        if response.status_code in retry_on and attempt < budget - 1:
            # Same last rung as the sync path: walled *through* the spare egress means this IP is
            # spent too, so move again rather than spend the final attempt on a known-bad route.
            if proxy and egress_group is not None and response.status_code in egress_on:
                # The deadline starts here, on the loop, not inside `rotate` — otherwise time
                # spent queueing for an executor thread would not count against the wait cap.
                budget += await asyncio.to_thread(
                    _rotate_for,
                    egress_board,
                    budget - attempts,
                    spare_egress.wait_deadline(),
                )
            await asyncio.sleep(
                _note_retry(
                    method,
                    url,
                    attempt,
                    budget,
                    f"-> {response.status_code}",
                    _retry_after(response),
                    response.status_code,
                )
            )
            attempt += 1
            continue
        if proxied and egress_group is not None:
            spare_egress.note_settled(egress_group, response.status_code, egress_on)
        return response
    raise AssertionError("unreachable")  # pragma: no cover
