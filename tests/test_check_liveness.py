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


# --- rate-limit gating ----------------------------------------------------------------------
# A gate paces the boards that share one rate limit. Getting its *span* wrong is the hazard these
# cover: too narrow and the breaker never trips (each tenant keeps its own strike counter); too
# wide and one tenant's 429 silences thousands of unrelated boards. Both were live at some point
# on 2026-08-14, and the wide one is far more expensive — it turns live boards UNKNOWN.


def test_a_listed_shared_domain_gates_every_tenant_under_it(cl):
    """Personio rate-limits the whole domain, so all its tenants must share one gate — otherwise
    strikes spread one-per-host and _STRIKES_TO_TRIP is never reached."""
    a = cl._gate_for("acme.jobs.personio.de")
    b = cl._gate_for("other.jobs.personio.de")
    assert a is not None and a is b


def test_one_workday_tenant_never_gates_another(cl):
    """The regression: `{tenant}.wdN.myworkdayjobs.com` is a per-customer instance, so inferring
    a shared domain by stripping the tenant label let one tenant's bot-wall 429 ban an entire
    datacenter — `interoute` alone took out wd10, wd102, wd103, wd109, wd115, wd117, wd503, wd504.
    """
    victim = "innocent.wd10.myworkdayjobs.com"
    assert cl._gate_for(victim) is None, "must start ungated"

    offender = cl._ensure_gate("interoute.wd10.myworkdayjobs.com")
    offender.trip(1800, "429, bot-wall challenge")

    assert cl._gate_for(victim) is None, "one tenant's 429 must not gate its neighbours"
    assert cl._gate_for("interoute.wd10.myworkdayjobs.com") is offender


def test_an_auto_gate_keys_on_the_exact_host(cl):
    gate = cl._ensure_gate("surprise.example.com")
    assert gate.key == "surprise.example.com"
    assert cl._gate_for("sibling.example.com") is None


def test_slow_down_halves_the_rate_and_stops_at_the_floor(cl):
    gate = cl._HostGate(4, 0.05, "t.example")
    gate.slow_down()
    assert gate.spacing == pytest.approx(0.1)
    for _ in range(20):
        gate.slow_down()
    assert gate.spacing == cl._MAX_SPACING, "must not ease indefinitely toward a stall"


def test_slow_down_lifts_a_zero_spacing_gate_off_zero(cl):
    """lever/join start at 0.0; doubling zero is zero, so the floor has to catch it."""
    gate = cl._HostGate(4, 0.0, "t.example")
    gate.slow_down()
    assert gate.spacing >= cl._MIN_SPACING
