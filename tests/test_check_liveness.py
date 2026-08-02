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
