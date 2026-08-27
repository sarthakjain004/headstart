"""Tests for the non-prod board gate (scripts/validate/check_liveness.py, ADR-0034).

The gate is three irreducible parts — a token-bounded pattern (recall), an exact-tenant
exception list (real companies whose names collide), and an exact-tenant blocklist
(concatenated markers the tokens cannot see). Every case here is a real ledger row or a
measured collision, not an invented string; the token-bounding cases are the load-bearing
ones, because widening the pattern to catch `stldemo` would re-admit `sandboxvr`.
"""

from __future__ import annotations

import importlib.util
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "validate" / "check_liveness.py"
)


@pytest.fixture(scope="module")
def cl():
    spec = importlib.util.spec_from_file_location("check_liveness", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(
    ("tenant", "url", "nonprod"),
    [
        # measured true positives — fabricated-data boards from the 2026-08-02 sweep
        ("amdocs-sandbox.eightfold.ai", "", True),
        ("citigroup-qa-sandbox.eightfold.ai", "", True),
        ("uat-atsl-app", "", True),
        ("qa4-demo", "", True),
        ("demo.uipath.com", "", True),
        ("careers-uat.morganstanley.com.cn", "", True),
        ("acme", "https://demo.acme.com/board", True),  # marker in the url, not tenant
        # token-bounding: one-word names containing a marker must NOT match
        ("sandboxvr", "", False),
        ("thesandbox", "", False),
        ("testlio", "", False),
        # exact-tenant exceptions: real companies whose names collide with the tokens
        ("sandbox-interactive-gmbh", "", False),
        ("demo-duck", "", False),
        # exact-tenant blocklist: concatenated markers the token rule cannot see
        ("stldemo", "https://stldemo.ripplehire.com", True),
        ("stl", "", False),  # the real company the blocklist must not bleed onto
    ],
)
def test_is_nonprod(cl, tenant, url, nonprod):
    assert cl.is_nonprod(tenant, url) is nonprod


# --- eightfold alias losers (#157) ----------------------------------------------------------
# The same pre-probe shape, for a different reason: these six are alive, so only a skip that
# never asks the host keeps #154's dedupe from being undone by the 90-day dead-TTL re-probe.


@pytest.mark.parametrize(
    ("ats", "tenant", "loser"),
    [
        # the six buried by dedupe_eightfold_aliases.py's 2026-08-16 run
        ("eightfold", "nvidia.eightfold.ai", True),
        ("eightfold", "qualcomm.eightfold.ai", True),
        ("eightfold", "micron.eightfold.ai", True),
        ("eightfold", "hsbc.eightfold.ai", True),
        ("eightfold", "vodafone.eightfold.ai", True),
        ("eightfold", "dsm.eightfold.ai", True),
        # the winners each one duplicates must stay probeable — burying a cluster's only
        # surviving board would cost the jobs outright, not just the duplicate copy
        ("eightfold", "jobs.nvidia.com", False),
        ("eightfold", "dsm-firmenich.eightfold.ai", False),
        # no cross-ATS bleed: the set is exact hostnames, and hostnames are not ATS-unique
        ("workday", "nvidia.eightfold.ai", False),
    ],
)
def test_is_eightfold_alias_loser(cl, ats, tenant, loser):
    assert cl._is_eightfold_alias_loser(ats, tenant) is loser


# --- rate-limit gating ----------------------------------------------------------------------
# A gate paces the boards that share one rate limit. Getting its *span* wrong is the hazard these
# cover: too narrow and the breaker never trips (each tenant keeps its own strike counter); too
# wide and one tenant's 429 silences thousands of unrelated boards. Both were live at some point
# on 2026-08-14, and the wide one is far more expensive — it turns live boards UNKNOWN.


#: The host every gate test asks for. A response defaults to having settled here, so a test only
#: says otherwise when the redirect is the thing under test.
_URL = "https://apply.workable.com/api/v1/widget/accounts/acme"


class _FakeEgress:
    """Stands in for `headstart.spare_egress` — no daemon restarted, no socket opened.

    Defaults to *unavailable*, which is a machine with no WARP and therefore the ladder exactly as
    it behaved before rotation existed. Tests that want an egress opt in, so the ones that do not
    keep measuring what they were written to measure.
    """

    def __init__(self):
        self.available = False
        self.rotates = True
        self.walled = []
        self.rotated = []
        self.repeats = False  # when True, a restart comes back on the SAME address
        self._addresses = []  # distinct addresses handed out, in order
        self._rotate_lock = threading.Lock()

    def proxy_url(self):
        if not self.available:
            return None
        if not self._addresses:
            self._addresses.append("104.28.220.169")
        return "socks5://127.0.0.1:40000"

    def proxy_for(self, key):
        if self.available and key in self.walled:
            return "socks5://127.0.0.1:40000"
        return None

    def mark_walled(self, key, status):
        if key not in self.walled:
            self.walled.append(key)

    def rotate(self, key):
        """Coalesced, as the real one is: a thread finding the address moved while it queued rides
        the peer's rather than bouncing the daemon again. A restart need not land a new address —
        `repeats` is the measured case where it does not."""
        seen = len(self._addresses)
        with self._rotate_lock:
            if len(self._addresses) != seen:
                return True
            self.rotated.append(key)
            if self.rotates and not self.repeats:
                self._addresses.append(f"104.28.220.{169 + len(self._addresses)}")
            return self.rotates

    def rotations(self):
        return Counter()

    def egress_ips(self):
        return Counter({f"ip:{a}": 1 for a in self._addresses})


@pytest.fixture(autouse=True)
def egress(cl, monkeypatch):
    fake = _FakeEgress()
    monkeypatch.setattr(cl, "spare_egress", fake)
    return fake


class _Resp:
    """Just enough of a response for the gate code."""

    def __init__(self, code, headers=None, content=b"", url=_URL):
        self.status_code, self.headers, self.content = code, headers or {}, content
        self.url = url  # the settled hop — what `_redirected_off_host` reads


def test_a_listed_shared_domain_gates_every_board_under_it(cl):
    """Personio rate-limits the whole domain, so every board on it must share one gate —
    otherwise each backs off alone and the shared limit is never actually respected."""
    a = cl._gate_for("acme.jobs.personio.de")
    b = cl._gate_for("other.jobs.personio.de")
    assert a is not None and a is b


def test_one_workday_board_never_gates_another(cl):
    """The regression: `{tenant}.wdN.myworkdayjobs.com` is a per-customer instance, so inferring
    a shared domain by stripping the leading label let one board's bot-wall 429 ban an entire
    datacenter — `interoute` alone took out wd10, wd102, wd103, wd109, wd115, wd117, wd503, wd504.
    """
    victim = "innocent.wd10.myworkdayjobs.com"
    assert cl._gate_for(victim) is None, "must start ungated"

    offender = cl._ensure_gate("interoute.wd10.myworkdayjobs.com", "429")
    offender.trip(1800, "429, bot-wall challenge")

    assert cl._gate_for(victim) is None, "one board's 429 must not gate its neighbours"
    assert cl._gate_for("interoute.wd10.myworkdayjobs.com") is offender


def test_an_auto_gate_keys_on_the_exact_host(cl):
    gate = cl._ensure_gate("surprise.example.com", "429")
    assert gate.key == "surprise.example.com"
    assert cl._gate_for("sibling.example.com") is None


def test_ease_halves_the_rate_and_reports_when_it_hits_the_floor(cl):
    gate = cl._HostGate(4, 0.05, "t.example")
    assert gate.ease() is True
    assert gate.spacing == pytest.approx(0.1)
    for _ in range(20):
        gate.ease()
    assert gate.spacing == cl._MAX_SPACING, "must not ease indefinitely toward a stall"
    assert gate.ease() is False, (
        "at the floor it must say so, so _on_429 can trip instead"
    )


def test_ease_lifts_a_zero_spacing_gate_off_zero(cl):
    """lever/join start at 0.0; doubling zero is zero, so the floor has to catch it."""
    gate = cl._HostGate(4, 0.0, "t.example")
    gate.ease()
    assert gate.spacing >= cl._MIN_SPACING


def test_a_gate_refusing_at_the_floor_trips_instead_of_easing_forever(cl):
    """Replaces the consecutive-strikes trip, which could not work on a shared gate: any one of
    hundreds of threads getting a 200 reset the counter, so 1,800 rejections in 2,000 requests
    left it at 5. Easing to the floor is the signal that slowing down has stopped helping."""
    gate = cl._HostGate(4, cl._MAX_SPACING, "t.example")
    assert not gate.blocked()
    cl._on_429(gate, _Resp(429), _URL)
    assert gate.blocked(), "already as slow as it goes and still refused -> stop asking"


def test_a_longer_ban_is_never_shortened_by_a_later_one(cl):
    gate = cl._HostGate(4, 0.05, "t.example")
    gate.trip(1800, "long")
    gate.trip(5, "short")
    assert gate.blocked()
    # the 5s ban must not have replaced the 1800s one
    assert gate._banned_until > cl.time.monotonic() + 60


def test_a_short_ban_arriving_first_does_not_win_over_a_concurrent_long_one(cl):
    """The regression a review round caught in an earlier version of trip(): comparing only
    current status ("am I banned right now?") resolves two candidates racing from the same
    unbanned instant by "whichever thread wins the lock" rather than by magnitude — a 5s
    Retry-After trip and a concurrent 1800s challenge trip on the same host could settle on 5s,
    silently dropping the stronger signal. Order must never matter, only magnitude."""
    gate = cl._HostGate(4, 0.05, "t.example")
    gate.trip(5, "short")  # arrives first this time — the reverse of the test above
    gate.trip(1800, "long")
    assert gate._banned_until > cl.time.monotonic() + 60, (
        "the long ban must win regardless of which one the lock let through first"
    )


def test_trip_prints_once_per_ban_episode_not_once_per_call(cl, capsys):
    """A gate already banned still absorbs further same-strength trips (real traffic: requests
    already in flight before the gate existed all land around the same moment) without
    re-announcing each one — that flood was the original bug this method was rewritten to fix."""
    gate = cl._HostGate(4, 0.05, "t.example")
    for _ in range(50):
        gate.trip(1800, "429")
    assert capsys.readouterr().out.count("banned us") == 1

    # once the episode ends, a fresh trip is a new episode and does announce again
    gate._banned_until = cl.time.monotonic() - 1
    gate.trip(1800, "429")
    assert capsys.readouterr().out.count("banned us") == 1


def test_a_bot_wall_behind_403_is_recognised(cl):
    """Cloudflare serves "Just a moment..." behind 403 and 503, and only its 429s carry the
    cf-mitigated header — so a 429-only check misses two of the three markers."""
    assert cl._is_challenge(
        _Resp(403, content=b"<html><title>Just a moment...</title>")
    )
    assert cl._is_challenge(_Resp(429, headers={"cf-mitigated": "challenge"}))
    assert cl._is_challenge(_Resp(429, content=b"...Vercel Security Checkpoint..."))
    assert not cl._is_challenge(_Resp(403, content=b'{"error":"forbidden"}'))


# --- a ban is the last resort, not the first ---------------------------------------------------
# A ban costs every board behind the gate for the rest of the run: 21,428 of workable's 22,547
# ledger rows sit at `unknown` from exactly that, and workable's 429 is a Cloudflare per-IP block
# with a ~20h Retry-After — a limit the address is the whole of. So each branch that would ban
# now asks for a different address first. The cases below are the three where it must not.


def test_a_ban_length_retry_after_moves_egress_instead_of_banning(cl, egress):
    """The workable shape: a Retry-After far past the ban threshold, from a host that meters per
    IP. Banning loses every remaining board; a second address costs seconds."""
    egress.available = True
    gate = cl._HostGate(8, 0.25, "apply.workable.com")

    cl._on_429(gate, _Resp(429, {"Retry-After": "72000"}), _URL)

    assert not gate.blocked(), "a fresh address makes the ban pointless — lift it"
    assert egress.walled == ["apply.workable.com"], (
        "the gate's boards must be routed under the same key the gate is filed under"
    )


def test_recovery_returns_the_seeded_pace_not_the_eased_one(cl, egress):
    """Easing was tuned against the address that was refusing us. Carrying it onto a new one
    throttles against a quota that no longer exists — 22k boards at the 1 req/s floor is ~6h
    against ~1.5h at workable's seeded 4 req/s."""
    egress.available = True
    gate = cl._HostGate(8, 0.25, "apply.workable.com")
    while gate.ease():
        pass
    assert gate.spacing == cl._MAX_SPACING

    cl._on_429(gate, _Resp(429, {"Retry-After": "72000"}), _URL)

    assert gate.spacing == pytest.approx(0.25)


def test_the_next_refusal_rotates_rather_than_re_dialling(cl, egress):
    """Two rungs, not one. The first wall moves the gate onto the spare egress — already a
    different address. Being refused *there* is what asks for another one."""
    egress.available = True
    gate = cl._HostGate(8, 0.25, "apply.workable.com")

    cl._on_429(gate, _Resp(429, {"Retry-After": "72000"}), _URL)
    assert egress.rotated == [], "moving onto the spare egress is not itself a rotation"

    cl._on_429(gate, _Resp(429, {"Retry-After": "72000"}), _URL)
    assert egress.rotated == ["apply.workable.com"]


def test_a_429_from_a_redirect_off_host_never_rotates(cl, egress):
    """Personio's tombstone, and the premise that got PR #312 reverted. A departed tenant's
    `/xml` 307s to `personio.com`, whose wall answers 429 from every address — measured
    2026-08-27 over the direct route and a WARP one alike. 5,178 personio rows sit at `unknown`,
    so rotating on these would spend a `--force` pass restarting the tunnel and rescue nothing.
    """
    egress.available = True
    gate = cl._HostGate(16, 0.05, "jobs.personio.de")
    asked = "https://13c-venture.jobs.personio.de/xml"
    walled_elsewhere = _Resp(429, {"Retry-After": "72000"}, url="https://personio.com/")

    cl._on_429(gate, walled_elsewhere, asked)

    assert gate.blocked(), "no address reaches it, so the ban is the honest verdict"
    assert egress.walled == [] and egress.rotated == []


def test_without_warp_the_ladder_bans_exactly_as_before(cl, egress):
    """The default fixture state. `spare_egress` returns None for everything on a machine with no
    daemon, and the checker must be byte-for-byte what it was: one bounded attempt, then the ban.
    """
    gate = cl._HostGate(8, 0.25, "apply.workable.com")

    cl._on_429(gate, _Resp(429, {"Retry-After": "72000"}), _URL)

    assert gate.blocked()
    assert egress.rotated == []


def test_an_ordinary_429_still_eases_and_never_spends_an_address(cl, egress):
    """Rotation is the bottom rung, not the first. A plain 429 with no Retry-After is what easing
    was measured for, and a rotation there would bounce the daemon on every rate limit we meet."""
    egress.available = True
    gate = cl._HostGate(8, 0.25, "apply.workable.com")

    cl._on_429(gate, _Resp(429), _URL)

    assert gate.spacing == pytest.approx(0.5), "eased, not rotated"
    assert egress.walled == [] and egress.rotated == []


def test_the_egress_group_is_the_key_the_gate_is_filed_under(cl):
    """The gate and the boards routed with it must name the same thing. Personio's gate spans the
    whole domain, so a per-host egress group would wall one spelling and route another."""
    assert cl._gate_key("acme.jobs.personio.de") == "jobs.personio.de"
    assert cl._gate_key("surprise.example.com") == "surprise.example.com"
    assert cl._gate_for("acme.jobs.personio.de").key == cl._gate_key(
        "acme.jobs.personio.de"
    )


# --- the allowance must count addresses, not callers -------------------------------------------
# Every test above is single-threaded, and that is exactly how the first version shipped a broken
# bound: `_on_429` runs OUTSIDE the gate's semaphore, so a whole burst of workers reaches it at
# once. `spare_egress` coalesces them onto one daemon restart, so they arrive holding one address
# between them — and charging each caller spent the entire allowance on the first wall, from zero
# refused addresses. These run concurrently on purpose.


def test_a_burst_landing_on_one_address_is_charged_once(cl):
    """The measured regression: 16 threads, about one real rotation, and the count read 16.

    `spare_egress` coalesces a herd onto a single restart, so they arrive holding one address
    between them. How many restarts a given herd produces is that module's business and varies
    with timing; what this file owns is that one address costs one of the allowance. Asserted at
    `recover` rather than through `_on_429` so the subject is that rule and not the scheduler.
    """
    gate = cl._HostGate(8, 0.25, "apply.workable.com")
    barrier = threading.Barrier(16)

    def rode_the_same_rotation():
        barrier.wait()
        gate.recover("429, retry-after 72000s", address=7, since=time.monotonic())

    with ThreadPoolExecutor(max_workers=16) as ex:
        list(ex.map(lambda _: rode_the_same_rotation(), range(16)))

    spent, banned = gate.egress_account()
    assert spent == 1, (
        f"one address was handed out, so one may be charged — charged {spent}"
    )
    assert not banned


def test_a_gate_refused_from_every_address_keeps_moving_and_never_bans(cl, egress):
    """No allowance, and this is the measurement that removed it.

    An earlier version stopped after three addresses and banned. The first real workable sweep,
    2026-08-27, spent those three inside the first ~300 boards; the ban then short-circuited
    20,916 of the remaining 21,228 without sending a request. Rotating on is degraded throughput.
    Banning is total loss of everything behind the gate. The bound is `spare_egress`'s own
    rotation cooldown, not a counter here.
    """
    egress.available = True
    gate = cl._HostGate(8, 0.25, "apply.workable.com")

    for _ in range(40):  # far past any allowance the ladder used to carry
        cl._on_429(gate, _Resp(429, {"Retry-After": "72000"}), _URL)
        assert not gate.blocked(), (
            "a refused address is a reason to move, never to give up"
        )

    assert len(egress.rotated) >= 30, (
        "it must keep asking for as long as it keeps being refused"
    )


def test_a_rotation_that_lands_the_same_address_is_not_double_counted(cl, egress):
    """Measured on that same sweep: two rotations, `1 moved, 1 repeated`.

    A daemon restart is not an address. The count exists to report how far the run actually got,
    so a restart that comes back on the IP we were already using must not inflate it — and it
    must not re-announce a move that did not happen.
    """
    egress.available = True
    egress.repeats = True  # every restart comes back on the same IP
    gate = cl._HostGate(8, 0.25, "apply.workable.com")

    for _ in range(5):
        cl._on_429(gate, _Resp(429, {"Retry-After": "72000"}), _URL)

    spent, banned = gate.egress_account()
    assert spent == 1, f"one address was ever handed out; counted {spent}"
    assert not banned
    assert len(egress.rotated) >= 1, (
        "it did keep asking — it just never got anywhere new"
    )


def test_a_ban_set_after_the_rotation_began_survives_it(cl, egress, monkeypatch):
    """`recover` used to clear the ban outright, defended by per-thread reasoning about an object
    dozens of threads share. On `jobs.personio.de` — one gate for every tenant — a departed
    tenant's off-host trip lands concurrently with a live tenant's recovery, and the redirect
    guard's ban was silently dropped."""
    egress.available = True
    gate = cl._HostGate(16, 0.05, "jobs.personio.de")

    def trip_midway():
        gate.trip(1800, "429, from a redirect off-host")  # a peer, while we rotate
        return "socks5://127.0.0.1:40000"

    monkeypatch.setattr(egress, "proxy_url", trip_midway)
    cl._on_429(gate, _Resp(429, {"Retry-After": "72000"}), _URL)

    assert gate.blocked(), (
        "a ban that arrived after the rotation started knows more than it does"
    )


def test_the_first_wall_dials_the_spare_egress_and_does_not_bounce_the_daemon(
    cl, egress
):
    """A peer arriving just after the first thread dialled must not read "a proxy exists" as "this
    address refused me" — that rotates the tunnel, severing its own in-flight requests, against an
    address that has not yet carried a single request. The rung comes from the gate's own address.
    """
    egress.available = True
    gate = cl._HostGate(8, 0.25, "apply.workable.com")

    cl._on_429(gate, _Resp(429, {"Retry-After": "72000"}), _URL)

    assert egress.walled == ["apply.workable.com"]
    assert egress.rotated == [], "moving onto the spare egress is not a rotation"
    assert gate.address() is not None


def test_no_warp_is_never_recorded_as_having_spent_an_egress(cl, egress):
    """`mark_walled` before knowing WARP exists made a run that never left the direct route report
    its gates as walled with boards lost. Dial first, mark second."""
    gate = cl._HostGate(8, 0.25, "apply.workable.com")

    cl._on_429(gate, _Resp(429, {"Retry-After": "72000"}), _URL)

    assert egress.walled == [], "nothing was walled, because nothing was ever routed"
    assert gate.blocked()
    assert gate.egress_account() == (0, True)


def test_an_unseeded_spanning_host_gets_one_gate_not_one_per_tenant(cl):
    """`_ensure_gate` looked up `_gate_key` but stored under `netloc`, so a listed domain with no
    seeded gate rebuilt a fresh one per request — pacing, ban and egress state lost every time —
    and routed under a key its gate never matched."""
    cl._SPANNING = (*cl._SPANNING, "unseeded.example")
    try:
        first = cl._ensure_gate("a.unseeded.example", "429")
        second = cl._ensure_gate("b.unseeded.example", "429")
        assert first is second
        assert first.key == cl._gate_key("a.unseeded.example") == "unseeded.example"
    finally:
        cl._SPANNING = tuple(d for d in cl._SPANNING if d != "unseeded.example")
        cl._GATES.pop("unseeded.example", None)
