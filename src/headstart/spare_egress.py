"""The **Spare egress**: a second network origin for a shard that has spent an ATS's budget.

ADR-0047 established that an ATS metering **per origin** gives each parallel Actions shard its own
budget, because the shards get distinct egress IPs — and that the way to spend those budgets evenly
is to cap how many of one ATS's Boards a single shard may take. That cap is *relative*
(``ceil(n/m)``), so it distributes load without bounding it: when the slice grew, per-shard load
grew with it and the budget went from comfortable to spent. This module is the other half of the
answer (ADR-0063) — when a shard *does* spend a budget, it can pick up a second one instead of
losing every Board it has left.

Think of it as **direct, then a rotating spare egress** — two routes, not a ladder of three. The
spare egress is a *supply* of IPs rather than one fallback address: :func:`proxy_for` puts a walled
group onto it, and :func:`rotate` moves it again each time that IP is refused too, for as long as
the refusals continue (bounded by :data:`_ROTATION_COOLDOWN`).

Cloudflare WARP is the implementation, not the concept: the module is named for what it provides so
that swapping the provider is an edit here rather than a rename everywhere.

A second route is not the only answer to a spent budget, so this module also owns **how hard to
push** once one is spent: :func:`stream_width` narrows a caller's fan-out for a walled group,
reasoning that rotation buys one address at a time rather than a wider allowance and so cannot
rescue a shard that is simply too wide (#195). The pool that address is drawn from was measured by
ADR-0067 at "one to three" per colo; ADR-0081 corrected that to a deep pool with a ~100% rescue
rate on 150 shard-runs of real traffic, which weakens this module's stated reason for narrowing —
see :func:`stream_width`.

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

import socket
import subprocess
import threading
import time
from collections import Counter, defaultdict

from curl_cffi import requests as _rq

from headstart import log

__all__ = [
    "egress_ips",
    "mark_walled",
    "note_routed",
    "note_settled",
    "proxy_for",
    "proxy_url",
    "report",
    "reset",
    "rotate",
    "rotation_causes",
    "rotations",
    "stream_width",
    "traffic",
    "wait_deadline",
    "walled_groups",
]

_log = log.get(__name__)

#: SOCKS5 port WARP is asked to listen on. Arbitrary but fixed: the value only has to agree with
#: itself, and a constant keeps the probe in `resilience.md` copy-pasteable against a live run.
_PORT = 40000

#: Per state-changing ``warp-cli`` call. Generous because ``connect`` does real network setup, but
#: bounded because this sits on the scrape's critical path.
_CALL_TIMEOUT = 30.0

#: How long to wait for ``status`` to report Connected after ``connect`` returns.
#: One SOCKS5 handshake attempt.
_HANDSHAKE_TIMEOUT = 1.0

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
            check=False,  # the caller reads returncode; a raise here would defeat "never raises"
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


def _socks5_ready() -> bool:
    """Whether a working no-auth SOCKS5 proxy actually answers on the port.

    A real handshake (RFC 1928: send ``05 01 00``, expect ``05 00``) rather than ``warp-cli
    status``, because "Connected" is a statement about the tunnel, not about the listener. Two
    ways that gap bites: the daemon reports Connected a beat before the SOCKS5 port is accepting,
    and *any* stale listener on the port — an old WARP, a dev tool — would otherwise be adopted
    and then fail every request during negotiation. Borrowed from a sibling project that hit
    exactly that.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(_HANDSHAKE_TIMEOUT)
            sock.connect(("127.0.0.1", _PORT))
            sock.sendall(b"\x05\x01\x00")
            return sock.recv(2) == b"\x05\x00"
    except OSError:
        return False


def _await_socks5() -> bool:
    """Wait for the SOCKS5 listener to answer, up to :data:`_CONNECT_TIMEOUT`."""
    deadline = time.monotonic() + _CONNECT_TIMEOUT
    while time.monotonic() < deadline:
        if _socks5_ready():
            return True
        time.sleep(_POLL)
    return False


def _connect() -> str | None:
    """Put WARP in proxy mode and connect it. The SOCKS5 URL, or None if any step fails.

    Readiness is a real SOCKS5 handshake, not ``connect``'s exit code: the call returns as soon as
    the request is accepted, and handing out a proxy that is not carrying traffic yet would turn
    the first Board after the wall into a second failure.
    """
    if not _run("mode", "proxy"):
        return None
    if not _run("proxy", "port", str(_PORT)):
        return None
    if not _run("connect"):
        return None
    if _await_socks5():
        return f"socks5://127.0.0.1:{_PORT}"
    _log.warning(
        f"spare egress: dialled but the SOCKS5 listener did not answer within "
        f"{_CONNECT_TIMEOUT:.0f}s — staying on the direct route"
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
        connected = _proxy
        if _proxy:
            _log.warning(
                f"spare egress: connected in {took:.1f}s, routing via {_proxy}"
            )
        else:
            _log.warning(
                f"spare egress: unavailable after {took:.1f}s — every walled Board this run "
                f"stays on the spent origin"
            )
    # Outside `_lock`, same reasoning as `rotate`'s tail: this is the FIRST address the shard
    # egresses from, and without it the shard's first rotation has nothing to compare against and
    # reports `(first)` when it should be able to say `(moved)`/`(SAME as before)`.
    if connected:
        _observe_egress_ip()
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


#: How wide a caller may still fan out once its group is walled. **Extrapolated, not measured
#: here.** ADR-0047 benchmarked Eightfold's detail pass against a live origin and put width 12 at
#: 47.9% loss for 17.6 minutes on the worst shard, against 49.9% for 9.7 minutes at 25 — so 12 is
#: where narrowing still buys something and the wall-clock still fits a 60-minute shard. That table
#: is bare GETs against a different ATS, and the pagination this now clamps is a POST with a JSON
#: body; ADR-0047 also says of its own row that "one production run should be read before moving
#: it". A starting point to re-measure with scripts/bench/probe_eightfold_throttle.py's method,
#: then, not a settled number.
_WALLED_STREAM_WIDTH = 12


def stream_width(group: str | None, ceiling: int) -> int:
    """How wide we may fan out against ``group`` right now, at most ``ceiling``.

    ``ceiling`` is the width the caller resolved from its own measurements, and this only ever
    narrows it — a call site that pinned a width for its own reasons keeps it. ``group is None``
    (every scraper that never opted into the fallback) and a group that has not walled both get
    ``ceiling`` back unchanged.

    **Reactive within a run, deliberately.** Nothing tells a shard *before* its first wall whether
    it has a working spare egress: the only eager answer is :func:`proxy_url`, and asking it early
    dials WARP on every shard including the ones that would never have needed it. So the first wall
    is the signal, and the width narrows from there — the requests before it are as wide as they
    were, the ones after the origin has already said no are not.

    It does not ask whether the walled group *has* a spare egress either — that stays true
    regardless of pool depth, since :func:`proxy_url` would have to dial WARP to find out. The
    narrowing itself was justified by ADR-0067's "one to three IPs per colo" measurement; ADR-0081
    corrected that to a deep pool with a ~100% rescue rate on real traffic, which removes that
    specific justification without supplying a re-measured width to replace `12` — see ADR-0081's
    consequences before trusting this constant as tuned.
    """
    if group is None:
        return ceiling
    with _walled_lock:
        walled = group in _walled
    return min(ceiling, _WALLED_STREAM_WIDTH) if walled else ceiling


#: Per group, at two levels, because they answer different questions. ``routed`` counts *attempts*
#: — the traffic the spare egress cost. ``requests``/``rescued``/``walled``/``other`` count settled
#: *requests* — what it bought. Counted for the same reason ``http.retry_stats`` is: without them a
#: shard that routed everything successfully and one whose proxy silently carried nothing log
#: identically, and "did the fallback work?" is the only question this feature has.
#:
#: The number to watch is ``rescued / (rescued + walled)``: of the requests the spare egress was
#: asked to rescue from a wall, how many it actually rescued. A high attempt count against a low
#: rescue rate means the spare egress is walled too — the signal to stop trusting it rather than to
#: route more.
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


def note_routed(group: str) -> None:
    """Count one *attempt* carried by the spare egress — what it cost, not what it bought."""
    with _traffic_lock:
        _traffic[group]["routed"] += 1


def note_settled(group: str, status: int | None, walls: frozenset[int]) -> None:
    """Count one *request* the spare egress carried to a final outcome, bucketed by what that
    outcome says about the egress itself. ``status`` is None for a request that never settled on
    one — a transport failure through the proxy.

    Three buckets, because a single "recovered" counter conflated failures calling for opposite
    responses. It counted per *attempt*, so a request that walled twice before succeeding scored
    1/3 rather than 1/1 and every retry pushed the rate down. And it scored every non-200 as a
    failure to recover, so a Board serving stale URLs that all 404 — `eightfold:nttdata.eightfold.ai`,
    whose tenant migrated off the ATS — read identically to a refused IP range. Both errors argue
    for abandoning a spare egress that is working.

    ``walls`` is **this request's** ``egress_on``, not the group's. A request that opted out of
    marking (Eightfold's API-availability probe, whose steady 403 means "this tenant has no API",
    not "this IP is refused") passes an empty set, so its 403 lands in ``other`` rather than
    indicting an egress that is fine. Classifying against a set accumulated per group would put it
    in ``walled`` — reintroducing, one layer down, exactly the misattribution this fix removes.

    ``walled`` is the only bucket that indicts the egress: a request still refused, after every
    attempt, with a status *it* treats as a wall. Everything else lands in ``other`` and is left
    out of the rate rather than counted against it — the safe way to be wrong.
    """
    with _traffic_lock:
        counts = _traffic[group]
        counts["requests"] += 1
        if status == 200:
            counts["rescued"] += 1
        elif status is not None and status in walls:
            counts["walled"] += 1
        else:
            counts["other"] += 1


def traffic() -> dict[str, Counter[str]]:
    """Per group: ``routed`` attempts, and ``requests``/``rescued``/``walled``/``other`` settles."""
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
    spins = rotations()
    if spins:
        lines.append(
            "spare egress rotations: "
            + ", ".join(
                f"{why} {spins[why]}"
                for why in (
                    "attempted",
                    "succeeded",
                    "failed",
                    "throttled",
                    "abandoned",
                )
                if spins.get(why)
            )
        )
    addresses = egress_ips()
    seen = {k[3:] for k in addresses if k.startswith("ip:")}
    colos = {k[5:] for k in addresses if k.startswith("colo:")}
    if seen or addresses.get("unreadable"):
        # Distinct addresses, not rotations: a rotation count alone can't say whether it landed a
        # fresh IP or a repeat, so "N rotations across M addresses" is the shape to notice and a
        # bare rotation count hides it completely. (ADR-0067 measured M as small — "one to three"
        # per colo; ADR-0081 corrected that to a deep pool on 150 shard-runs of real traffic, but
        # the reason to report distinct addresses rather than a raw count is unchanged either way.)
        # "comparison(s)", not "rotation(s)": the first observation has nothing to compare against,
        # so this is one less than the rotations that actually produced an address.
        lines.append(
            f"egress addresses: {len(seen)} distinct across "
            f"{addresses['moved'] + addresses['repeat']} comparison(s) "
            f"({addresses['moved']} moved, {addresses['repeat']} returned the same IP"
            + (
                f", {addresses['unreadable']} unreadable"
                if addresses.get("unreadable")
                else ""
            )
            + ")"
            + (f" — {', '.join(sorted(seen))}" if seen else "")
            + (f" via colo {', '.join(sorted(colos))}" if colos else "")
        )
    causes = rotation_causes()
    if causes:
        top = ", ".join(f"{board} {n:,}" for board, n in causes.most_common(5))
        rest = len(causes) - 5
        lines.append(
            "rotation demand by board: " + top + (f", +{rest} more" if rest > 0 else "")
        )
    for group in sorted(walled_groups()):
        seen = counts.get(group, Counter())
        routed = seen["routed"]
        if not routed:
            lines.append(
                f"{group}: walled, but no spare egress was available — Boards lost"
            )
            continue
        rescued, still_walled, other = seen["rescued"], seen["walled"], seen["other"]
        asked = rescued + still_walled
        rate = f"{100 * rescued / asked:.0f}%" if asked else "n/a"
        tail = f", {other:,} settled non-wall" if other else ""
        lines.append(
            f"{group}: walled; spare egress rescued {rescued:,}/{asked:,} walled "
            f"request(s) ({rate}); {routed:,} attempt(s) carried{tail}"
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
    # Wait out an in-flight rotation rather than handing back a port the restart has taken away.
    # Bounded: if a rotation overruns, going direct beats blocking the whole shard behind it.
    _gate.wait(timeout=_CONNECT_TIMEOUT)
    return proxy_url()


#: Rotations are coalesced on a generation counter: sixteen worker threads meeting a walled spare
#: egress at once must produce **one** restart, not sixteen. A thread snapshots the generation
#: before queueing on the lock; if it changed while it waited, a peer already rotated and this
#: thread rides that new IP instead of bouncing the daemon again.
_rotation_lock = threading.Lock()
#: Signalled on every successful rotation. This is what lets a throttled caller *wait* for the
#: fresh IP instead of spending its attempt on the spent one: that attempt would have failed, and
#: a failed attempt is worth less than the seconds waiting costs.
_rotated = threading.Condition(_rotation_lock)
_rotation_generation = 0
_rotations: Counter[str] = Counter()
#: Which Board each rotation request came from. Every caller of :func:`rotate` has just been
#: refused *through* the spare egress, so this is the set of Boards actually consuming the IP
#: supply — the attribution the shard report could not make before, and the one that decides
#: whether the answer is more egress capacity (#174) or a Board that should not be scraped at all.
#: Demand rather than grants: a Board asking 20,000 times and getting 40 IPs is the one to look at,
#: and the grant count would show it as unremarkable.
_rotation_causes: Counter[str] = Counter()
_last_rotation = 0.0

#: Which egress addresses this shard was actually given, plus moved/repeat/unreadable tallies.
#: Addresses are keyed `ip:<address>`. The counters beside them are the point: a rotation count
#: says nothing about whether rotation is working and only a comparison of addresses does — a
#: claim that held under ADR-0067's original "~11 times in 30" measurement and still holds now
#: that ADR-0081 corrected the pool it drew that number from.
_egress_ips: Counter[str] = Counter()
_last_egress_ip: str | None = None

#: Cloudflare's own trace endpoint, read *through* the proxy so the address it echoes is the one
#: the shard is actually egressing from. Plain text, a few hundred bytes, and served by the same
#: edge the tunnel already terminates on, so it costs a fraction of a real Board fetch.
_TRACE_URL = "https://www.cloudflare.com/cdn-cgi/trace"
#: Short on purpose. This runs inside the rotation lock, so every peer waiting to rotate waits on
#: it too; a slow trace must cost the shard a missing log line, never a stalled fan-out.
_TRACE_TIMEOUT = 4.0

#: Minimum seconds between successive rotations. Rotation is cyclic — every wall seen *through*
#: the spare egress asks for another IP — so without a floor a shard meeting 429s continuously
#: would spend its 60-minute budget restarting `warp-svc` rather than scraping.
#:
#: **5s, measured.** The 20s this started at came from a sibling project and was never measured
#: here. The first three runs to report rotation counts (`32178532129`, `32189304871`,
#: `32198367156`) put the real cost of a rotation at **2.05s median, 4.06s max** across 2,030 of
#: them — so 20s sat an order of magnitude above the thing it was bounding. It also refused 99.6%
#: of rotation requests (160,360 throttled against 699 granted on one run), which is what collapsed
#: the recovery rate once #172 raised proxied traffic from ~1.4 to ~55 req/s per shard.
#:
#: 5s keeps ~2.4x headroom over the measured median, clears the observed max, caps a waiter's queue
#: delay at roughly what the retry backoff already costs it, and lifts the per-shard ceiling from
#: ~66 rotations to ~264. It does **not** close the supply gap alone: demand is ~3,700
#: rotations/shard, and serial rotation cannot reach that at any cooldown (#174).
_ROTATION_COOLDOWN = 5.0

#: How long one caller will wait for a fresh IP before giving up and riding the current one.
#: A waiter that arrives just after a rotation must be able to sit out a full cooldown *and* the
#: rotation it is waiting for, so this is sized above `_ROTATION_COOLDOWN` plus the measured 4.06s
#: worst case.
#:
#: It bounds the **wait**, not the whole call: a caller that waits the cap out and then finds
#: itself eligible still performs one rotation, so the worst case is the cap plus one rotation
#: round-trip. That is the bound that matters — a Board every IP refuses (a migrated tenant whose
#: stale URLs all 404, say) cannot hold a worker for the shard's budget, only for that.
_ROTATION_WAIT_CAP = 10.0


#: Pause after `systemctl restart` before re-arming proxy mode — the unit is back before the daemon
#: is listening.
_RESTART_SETTLE = 2.0

#: Held closed for the duration of a rotation. Without it, peers keep firing at a SOCKS5 port that
#: the restart has just taken away — each one a RequestsError that burns an attempt and can lose a
#: Board. The sibling project calls this the rotation gate and it is the half of the design that
#: coalescing alone does not cover.
_gate = threading.Event()
_gate.set()


def generation() -> int:
    """How many rotations have succeeded in this process.

    A caller that snapshots this before a request and finds it changed afterwards knows the egress
    moved *underneath* that request — so a connection error it then sees is our own restart tearing
    the tunnel down, not the origin refusing.

    Read without :data:`_rotation_lock` deliberately: that lock is held across the whole
    ``systemctl restart``, so acquiring it here would park every caller for the seconds this exists
    to detect. Only the *change* matters, never the value, and an int read is atomic enough for
    that — a caller that reads a stale generation simply does not claim the refund.
    """
    return _rotation_generation


def rotate(board: str | None = None, *, deadline: float | None = None) -> bool:
    """Move to a different egress IP, waiting out the cooldown if one is in force.

    **True iff a fresh egress IP is now in service** — whether this call produced it or a peer did
    while this one queued. That single bit is also the answer to "did this call cost the caller
    time?", which is what ``http`` needs to decide whether to give an attempt back: every path that
    ends on a fresh IP paid for it, and they all cost about the same. Rotating it yourself is the
    measured 2.05s with the gate closed; waiting out the cooldown is its remainder; queueing on the
    rotation lock is the peer's whole rotation, since that lock is held across the restart. No path
    gets a fresh IP free, and none pays without getting one — so a separate "did you wait" flag
    could only disagree with this one, and an earlier draft's did.

    ``deadline`` bounds the wait; pass :func:`wait_deadline`'s value to start that clock earlier
    than this call (the async path does, so executor-queue time counts against the cap too).

    ``board`` is the Board whose wall prompted this, recorded so the shard report can name what
    spent the IP supply rather than only how much of it went.

    **Why ``systemctl restart warp-svc`` rather than ``warp-cli disconnect`` + ``connect``**: the
    CLI pair is a no-op for rotation. A registration is sticky to its WARP edge node, so
    disconnect/connect returns the *same* egress IP — measured in a sibling project on 2026-05-29
    (``104.28.232.96`` before and after; a daemon restart moved it to ``104.28.200.91``).
    ``resilience.md`` records the symptom ("rotation can be a no-op"); this is the working answer.

    **A throttled caller waits rather than returning.** Every caller here has just been refused
    *through* the spare egress, so the current IP is known-bad; returning it immediately spends an
    attempt that will fail. Waiting out the remaining cooldown costs seconds and buys a route that
    can actually answer. The wait is bounded by :data:`_ROTATION_WAIT_CAP` and ends early if a peer
    rotates first, so no caller queues behind a rotation that is not coming.

    ``sudo -n`` so a runner without passwordless sudo fails immediately rather than blocking on a
    TTY prompt nobody will answer. **Every** exit path re-opens the gate and leaves the process
    able to dial again: a rotation that cannot happen must cost one bounded attempt, never the
    spare egress itself.
    """
    global _rotation_generation, _proxy, _last_rotation, _resolved
    if board:
        with _rotation_lock:
            _rotation_causes[board] += 1
    seen = _rotation_generation
    fresh = False
    if deadline is None:
        deadline = wait_deadline()
    with _rotated:
        if _rotation_generation != seen:
            return True  # a peer rotated while we queued — its IP is fresh, ride it
        throttled = False
        while _last_rotation and time.monotonic() - _last_rotation < _ROTATION_COOLDOWN:
            # Too soon to rotate — but handing back the spent IP is exactly what this call was
            # trying to avoid, and that attempt would have been refused. Wait for the next IP
            # instead. Counted once per caller, not once per wakeup, so `throttled` stays a count
            # of callers made to wait rather than of times the condition woke.
            if not throttled:
                _rotations["throttled"] += 1
                throttled = True
            remaining = min(
                _ROTATION_COOLDOWN - (time.monotonic() - _last_rotation),
                deadline - time.monotonic(),
            )
            if remaining <= 0:
                _rotations["abandoned"] += 1  # cap spent; no fresh IP for this caller
                return False
            generation = _rotation_generation
            _rotated.wait(remaining)
            if _rotation_generation != generation:
                return True  # a peer rotated while we waited; ride its IP
        _last_rotation = time.monotonic()
        _rotations["attempted"] += 1
        if board:
            _log.warning(f"spare egress: {board} walled the current IP — rotating")
        _gate.clear()  # peers stop firing at a port the restart is about to take away
        _log.warning("spare egress: rotating egress IP (systemctl restart warp-svc)")
        try:
            if not _restart_daemon():
                _rotations["failed"] += 1
                return False
            # `systemctl restart` returns once the *unit* is back, but warp-svc needs a moment to
            # come up and reconnect. Re-arming immediately means all three calls land on a daemon
            # that is not listening yet, fail silently, and the handshake wait below then burns its
            # whole deadline for nothing. The sibling project sleeps here for the same reason.
            time.sleep(_RESTART_SETTLE)
            _run("mode", "proxy")
            _run("proxy", "port", str(_PORT))
            _run("connect")
            if not _await_socks5():
                # Do NOT pin the process to the direct route. Clearing `_resolved` alongside
                # `_proxy` is what lets a later caller re-dial; leaving it set would make one bad
                # rotation permanent, which is strictly worse than never having rotated.
                _log.warning(
                    "spare egress: rotated but SOCKS5 did not come back — direct for now, "
                    "will re-dial"
                )
                _rotations["failed"] += 1
                with _lock:
                    _proxy = None
                    _resolved = False
                return False
            _rotation_generation += 1
            _rotations["succeeded"] += 1
            _log.warning(
                f"spare egress: rotated to a fresh egress IP (#{_rotation_generation})"
            )
            fresh = True
        finally:
            _gate.set()  # one place, so no exit path can strand the shard behind a closed gate
            _rotated.notify_all()  # and release the waiters onto whatever this produced
    # Deliberately outside the `with`: the gate is open and the rotation lock released before the
    # trace runs. Inside, its timeout would be added to `_ROTATION_WAIT_CAP` for every queued
    # rotator *and* — because `proxy_for` blocks on the same gate — would stall every proxied
    # worker in the shard, not merely the rotators. Telemetry must not be able to do that.
    if fresh:
        _observe_egress_ip()
    return fresh


def _observe_egress_ip() -> None:
    """Read the address this rotation actually landed on, and say whether it moved.

    ADR-0067 measured (2026-08-19, 18-30 probe jobs) that `systemctl restart warp-svc` returns a
    *different* egress IP only about 11 times in 30, and pinned that to pool depth: a WARP client
    is pinned to its nearest colo, the colo never moves under any rotation verb, and each colo was
    measured to carry a pool of one to three addresses. The colo-pinning half still holds. The
    pool-depth half does not: ADR-0081 (2026-08-21) parsed 150 shard-runs of real pipeline traffic
    and found 12,702 rotations landing 11,007 distinct addresses, with per-shard churn of
    0.93-1.00 and a ~100% rescue rate — a deep pool, not a 1-3 one. Neither ADR changes why this
    function exists, though:

        Rotation counters are not a health signal. `spare_egress.rotations()` counts restarts, and
        a restart that returns the same address counts the same as one that does not. A future
        change that wants to know whether rotation is *working* has to compare egress addresses,
        not count rotations.

    So the returned suffix is the health signal the counters cannot be. `same` means the rotation
    bought nothing — the caller still paid ~2s and a closed gate for the address it already had;
    ADR-0081's measurement says that outcome is now the rare one, not the common one.

    The colo rides along because ADR-0067 found it is *the* determinant of how much rotation can
    ever achieve — a shard is pinned to one colo for its whole life, so whatever that colo's pool
    holds is the ceiling. ADR-0067 measured LAX and SJC at a single address apiece (unable to move
    at all); ADR-0081's data contradicts that specifically, not just the aggregate number — the
    same two colo codes showed the same near-1.0 churn as every other colo on 2026-08-21. Reading
    per-colo pool size still matters, reading either ADR's specific numbers as current does not.
    Costs nothing extra either way, arriving in the same response.

    Called with **no lock held and the gate open**. Bounded and swallowed on every failure: this is
    telemetry attached to the retry path, and a trace endpoint being slow or down must never turn a
    working rotation into a failed one.
    """
    global _last_egress_ip
    with _lock:
        proxy = _proxy
    if not proxy:
        return
    try:
        body = _rq.get(
            _TRACE_URL,
            proxies={"http": proxy, "https": proxy},
            timeout=_TRACE_TIMEOUT,
        ).text
    except Exception:  # noqa: BLE001 — telemetry must never fail a rotation
        with _rotation_lock:
            _egress_ips["unreadable"] += 1
        return
    fields = dict(line.split("=", 1) for line in body.splitlines() if "=" in line)
    ip = fields.get("ip", "")
    if not ip:
        with _rotation_lock:
            _egress_ips["unreadable"] += 1
        return
    colo = fields.get("colo", "?")
    # `warp=off` means the trace did not travel the tunnel, so the address is the direct one and
    # would be a lie in this log line. Say so rather than record it as an egress address.
    if fields.get("warp", "on") == "off":
        _log.warning(
            "spare egress: trace reports warp=off — not recording a direct address"
        )
        with _rotation_lock:
            _egress_ips["unreadable"] += 1
        return
    with _rotation_lock:
        previous = _last_egress_ip
        if previous is None:
            verdict = "first"
        else:
            moved = ip != previous
            verdict = "moved" if moved else "SAME as before"
            _egress_ips["moved" if moved else "repeat"] += 1
        _egress_ips[f"ip:{ip}"] += 1
        _egress_ips[f"colo:{colo}"] += 1
        _last_egress_ip = ip
    _log.warning(f"spare egress: now egressing from {ip} via {colo} ({verdict})")


def egress_ips() -> Counter[str]:
    """Every egress address this shard was given, plus moved/repeat/unreadable tallies.

    Keyed `ip:<address>` for the addresses so one Counter carries both without a second structure;
    the join reads the `ip:` prefix to count *distinct* addresses across shards, which is the
    number that matters for judging pool depth — ADR-0067 put it at 11 WARP IPs shared across 30
    jobs (one across 8); ADR-0081 remeasured it on 150 shard-runs of real traffic at 11,007
    distinct IPs across 12,702 rotations.
    """
    with _rotation_lock:
        return Counter(_egress_ips)


def _restart_daemon() -> bool:
    """``sudo -n systemctl restart warp-svc``. False — logged, never raised — on any failure."""
    try:
        proc = subprocess.run(
            ["sudo", "-n", "systemctl", "restart", "warp-svc"],
            check=False,  # failure is logged and returned as False, never raised
            capture_output=True,
            text=True,
            timeout=_CALL_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning(f"spare egress: rotation unavailable ({type(exc).__name__})")
        return False
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().replace("\n", " ")[:160]
        _log.warning(f"spare egress: rotation failed (exit {proc.returncode}) {detail}")
        return False
    return True


def rotations() -> Counter[str]:
    """Attempted/succeeded/failed rotation counts, for the shard report."""
    with _rotation_lock:
        return Counter(_rotations)


def wait_deadline() -> float:
    """A deadline for :func:`rotate`'s wait, started **now**.

    The async path dispatches ``rotate`` to a worker thread, where it can sit in the executor
    queue before it runs. A deadline taken inside ``rotate`` would not cover that queue time, so
    the documented cap would not be the real bound; taking it on the event loop, before the
    dispatch, makes it one.
    """
    return time.monotonic() + _ROTATION_WAIT_CAP


def rotation_causes() -> Counter[str]:
    """Per Board, how many times it asked the spare egress for a fresh IP."""
    with _rotation_lock:
        return Counter(_rotation_causes)


def reset() -> None:
    """Forget the cached proxy *and* the walled groups, so the next call probes again (tests)."""
    global _resolved, _proxy, _last_rotation, _rotation_generation, _last_egress_ip
    with _lock:
        _resolved = False
        _proxy = None
    with _walled_lock:
        _walled.clear()
    with _traffic_lock:
        _traffic.clear()
    with _rotation_lock:
        _rotations.clear()
        _rotation_causes.clear()
        _egress_ips.clear()
        _last_egress_ip = None
        _last_rotation = 0.0
        _rotation_generation = 0
    _gate.set()
