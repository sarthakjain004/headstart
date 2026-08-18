"""The **Spare egress**: a second network origin for a shard that has spent an ATS's budget.

ADR-0047 established that an ATS metering **per origin** gives each parallel Actions shard its own
budget, because the shards get distinct egress IPs — and that the way to spend those budgets evenly
is to cap how many of one ATS's Boards a single shard may take. That cap is *relative*
(``ceil(n/m)``), so it distributes load without bounding it: when the slice grew, per-shard load
grew with it and the budget went from comfortable to spent. This module is the other half of the
answer (ADR-0063) — when a shard *does* spend a budget, it can pick up a second one instead of
losing every Board it has left.

Cloudflare WARP is the implementation, not the concept: the module is named for what it provides so
that swapping the provider is an edit here rather than a rename everywhere.

WARP runs in **proxy mode**, never VPN mode. VPN mode routes the whole machine through Cloudflare,
which on a runner means the artifact upload, the HF push and the GitHub API calls too; proxy mode
touches only the clients that point at the SOCKS5 port, so the scrape moves and nothing else does.
That distinction is also what answers the objection recorded in ``docs/darwinbox/cloudflare-wall.md``
— that WARP "re-routes every ATS on the runner, not just darwinbox" — since ``http`` routes only the
ATS that actually walled.

**Connecting is lazy and one-shot per process.** :func:`proxy_url` is called only once an origin has
actually walled us (``http`` decides that), so a run where nothing walls never starts WARP at all.
The outcome is cached either way — including failure, so a runner with no ``warp-cli`` pays the
probe once and every later caller degrades in a lock-and-return.

**It degrades, it does not raise.** A missing binary, an unregistered client, a refused connection:
all of them return None and leave the caller on its direct route, which is exactly the behaviour we
have today. The fallback is worth having only if its absence costs nothing.

Registration is deliberately *not* done here — ``pipeline.yml`` installs and registers ``warp-cli``
before the scrape, so the in-process path is the three cheap calls below rather than a licence
negotiation on the critical path. An unregistered client simply fails to connect and degrades.
"""

from __future__ import annotations

import subprocess
import threading
import time
from collections import Counter, defaultdict

from headstart import log

__all__ = [
    "proxy_url",
    "proxy_for",
    "mark_walled",
    "note_routed",
    "walled_groups",
    "traffic",
    "report",
    "reset",
]

_log = log.get(__name__)

#: SOCKS5 port WARP is asked to listen on. Arbitrary but fixed: the value only has to agree with
#: itself, and a constant keeps the probe in `resilience.md` copy-pasteable against a live run.
_PORT = 40000

#: Per state-changing ``warp-cli`` call. Generous because ``connect`` does real network setup, but
#: bounded because this sits on the scrape's critical path.
_CALL_TIMEOUT = 30.0

#: Per ``status`` call. Much shorter than :data:`_CALL_TIMEOUT` because this one is polled: at the
#: state-changing timeout a few unlucky polls could outlast the connect deadline they are meant to
#: enforce, and every walled worker waits behind it.
_STATUS_TIMEOUT = 5.0

#: How long to wait for ``status`` to report Connected after ``connect`` returns.
_CONNECT_TIMEOUT = 20.0
_POLL = 1.0

_lock = threading.Lock()
_resolved = False
_proxy: str | None = None


def _call(*args: str, timeout: float) -> subprocess.CompletedProcess[str] | None:
    """One ``warp-cli`` invocation, or None if it could not be run at all. Never raises."""
    try:
        return subprocess.run(
            ["warp-cli", "--accept-tos", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _log.info(f"warp-cli {' '.join(args)}: unavailable ({type(exc).__name__})")
        return None


def _run(*args: str) -> bool:
    """A state-changing ``warp-cli`` call. True on exit 0; False — logged, never raised — else."""
    proc = _call(*args, timeout=_CALL_TIMEOUT)
    if proc is None:
        return False
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().replace("\n", " ")[:160]
        _log.info(f"warp-cli {' '.join(args)}: exit {proc.returncode} {detail}")
        return False
    return True


def _connected() -> bool:
    """Whether ``warp-cli status`` currently reports a connected tunnel."""
    proc = _call("status", timeout=_STATUS_TIMEOUT)
    return proc is not None and "Connected" in (proc.stdout or "")


def _connect() -> str | None:
    """Put WARP in proxy mode and connect it. The SOCKS5 URL, or None if any step fails.

    Status is polled rather than trusted from ``connect``'s exit code: the call returns as soon as
    the request is accepted, and handing out a proxy that is not carrying traffic yet would turn
    the first Board after the wall into a second failure.
    """
    if not _run("mode", "proxy"):
        return None
    if not _run("proxy", "port", str(_PORT)):
        return None
    if not _run("connect"):
        return None
    deadline = time.monotonic() + _CONNECT_TIMEOUT
    while time.monotonic() < deadline:
        if _connected():
            return f"socks5://127.0.0.1:{_PORT}"
        time.sleep(_POLL)
    _log.warning(
        f"spare egress: dialled but no Connected status within {_CONNECT_TIMEOUT:.0f}s "
        f"— staying on the direct route"
    )
    return None


def proxy_url() -> str | None:
    """This process's spare-egress proxy, connecting it on first call. None when unavailable.

    Thread-safe and idempotent: the scrape calls this from every worker thread that meets a wall,
    and only the first one does any work. The result — including None — is cached for the life of
    the process, so a shard never re-probes a WARP that is not there.

    The lock is held across the connect, so concurrent walled workers wait for the outcome rather
    than racing to dial three tunnels. They lose little by waiting: without a spare egress every
    one of them was going to fail anyway, which is why the timeouts above are what bound this.
    """
    global _resolved, _proxy
    with _lock:
        if _resolved:
            return _proxy
        started = time.monotonic()
        _proxy = _connect()
        _resolved = True
        took = time.monotonic() - started
        if _proxy:
            _log.warning(
                f"spare egress: connected in {took:.1f}s, routing via {_proxy}"
            )
        else:
            _log.warning(
                f"spare egress: unavailable after {took:.1f}s — every walled Board this run "
                f"stays on the spent origin"
            )
        return _proxy


# --- which groups have spent their budget -------------------------------------------------------
# Keyed on the **ATS**, not the host. Eightfold's edge meters across all of a tenant's siblings, so
# per-host marking would make each of a shard's Boards rediscover the same wall in turn, spending
# three attempts each to learn what the first one already proved.
_walled: set[str] = set()
_walled_lock = threading.Lock()


def walled_groups() -> frozenset[str]:
    """The groups that have spent their Origin budget in this process.

    Read by the shard report, so a run says which ATSes cost it their budget rather than leaving
    the fallback's firing rate invisible.
    """
    with _walled_lock:
        return frozenset(_walled)


#: Per group: how many requests the spare egress carried, and how many of those came back 200.
#: Counted for the same reason ``http.retry_stats`` is — without it a shard that routed everything
#: successfully and one whose proxy silently carried nothing log identically, and "did the fallback
#: work?" is the only question this feature has. Recovery *rate* is the number to watch: a high
#: routed count with a low recovery rate means the spare egress is walled too, which is the signal
#: to stop trusting it rather than to route more.
_traffic: defaultdict[str, Counter[str]] = defaultdict(Counter)
_traffic_lock = threading.Lock()


def mark_walled(group: str, status: int) -> None:
    """Record that ``group``'s origin budget is spent, once per process, and say so loudly."""
    with _walled_lock:
        if group in _walled:
            return
        _walled.add(group)
    _log.warning(
        f"{group}: origin returned {status} — spending this shard's spare egress for the "
        f"rest of the run"
    )


def note_routed(group: str, *, recovered: bool) -> None:
    """Count one request carried by the spare egress, and whether it came back 200."""
    with _traffic_lock:
        counts = _traffic[group]
        counts["routed"] += 1
        if recovered:
            counts["recovered"] += 1


def traffic() -> dict[str, Counter[str]]:
    """Per group, ``{"routed": n, "recovered": n}`` for the requests the spare egress carried."""
    with _traffic_lock:
        return {group: Counter(counts) for group, counts in _traffic.items()}


def report() -> list[str]:
    """One human-readable line per group that spent its budget — for the shard report.

    Includes groups that were walled but carried nothing, because that is the *worst* case and the
    one a bare traffic counter would omit: the ATS refused us and no spare egress could be raised,
    so those Boards were lost exactly as they were before this existed.
    """
    counts = traffic()
    lines = []
    for group in sorted(walled_groups()):
        routed = counts.get(group, Counter())["routed"]
        recovered = counts.get(group, Counter())["recovered"]
        if not routed:
            lines.append(
                f"{group}: walled, but no spare egress was available — Boards lost"
            )
            continue
        rate = 100 * recovered / routed
        lines.append(
            f"{group}: walled; spare egress carried {routed} request(s), "
            f"{recovered} recovered ({rate:.0f}%)"
        )
    return lines


def proxy_for(group: str | None) -> str | None:
    """The proxy ``group`` should be routed through now, or None to stay on the direct route.

    None until the group is walled — so the fast path costs one set lookup — and None *after* it is
    walled if no spare egress can be brought up, in which case the caller degrades to the direct
    route it would have used before this existed.
    """
    if group is None:
        return None
    with _walled_lock:
        if group not in _walled:
            return None
    return proxy_url()


def reset() -> None:
    """Forget the cached proxy *and* the walled groups, so the next call probes again (tests)."""
    global _resolved, _proxy
    with _lock:
        _resolved = False
        _proxy = None
    with _walled_lock:
        _walled.clear()
    with _traffic_lock:
        _traffic.clear()
