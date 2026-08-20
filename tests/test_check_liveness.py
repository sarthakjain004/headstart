"""Tests for the non-prod board gate (scripts/validate/check_liveness.py, ADR-0034).

The gate is three irreducible parts — a token-bounded pattern (recall), an exact-tenant
exception list (real companies whose names collide), and an exact-tenant blocklist
(concatenated markers the tokens cannot see). Every case here is a real ledger row or a
measured collision, not an invented string; the token-bounding cases are the load-bearing
ones, because widening the pattern to catch `stldemo` would re-admit `sandboxvr`.
"""

from __future__ import annotations

import importlib.util
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


class _Resp:
    """Just enough of a response for the gate code."""

    def __init__(self, code, headers=None, content=b""):
        self.status_code, self.headers, self.content = code, headers or {}, content


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
    cl._on_429(gate, _Resp(429))
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
