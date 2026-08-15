"""Tests for the browser liveness escalation (scripts/validate/check_liveness_browser.py).

Covers ``_nav_url`` and the one call site that matters. The counts and the reasoning live in
``_nav_url``'s own docstring; they are deliberately not restated here, so there is one place to
update when the ledger moves.

The load-bearing test is ``test_probe_navigates_a_bare_host_row``: it fails if the normalisation
is removed from ``_probe``, which the unit cases alone do not.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "validate"
    / "check_liveness_browser.py"
)


@pytest.fixture(scope="module")
def clb():
    spec = importlib.util.spec_from_file_location("check_liveness_browser", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeTab:
    """Enough tab to drive ``_probe``, and it raises on a scheme-less URL exactly as pydoll does."""

    def __init__(self, count: int) -> None:
        self.count = count
        self.navigated: str | None = None

    async def go_to(self, url: str, timeout: int | None = None) -> None:
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"not a navigable URL: {url!r}")
        self.navigated = url

    async def find(self, **_kwargs):
        return None

    async def execute_script(self, _js: str):
        return {"result": {"result": {"type": "number", "value": self.count}}}

    async def close(self) -> None:
        return None


@pytest.mark.parametrize(
    "built,expected",
    [
        # The defect: a bare host, which is what three of the four builders can emit.
        ("foo.jobs.personio.com", "https://foo.jobs.personio.com"),
        ("advancis.jobs.personio.com/", "https://advancis.jobs.personio.com/"),
        ("acme.recruitee.com/", "https://acme.recruitee.com/"),
        # Already absolute: left exactly alone, scheme and trailing slash included.
        ("https://1qhealth.jobs.personio.de/", "https://1qhealth.jobs.personio.de/"),
        ("http://legacy.example.com/", "http://legacy.example.com/"),
    ],
)
def test_nav_url_normalises_only_when_scheme_is_missing(clb, built, expected):
    assert clb._nav_url(built) == expected


def test_nav_url_returns_none_when_nothing_is_navigable(clb):
    """Workday's builder yields ``"/"`` for a row with no stored URL, and a workday tenant has no
    host to derive one from — so the honest answer is "couldn't tell", not a bogus navigation."""
    assert clb._nav_url("/") is None
    assert clb._nav_url(clb._PAGE_PROBES["workday"]["url"]("some-tenant", "")) is None


def test_personio_builder_cannot_add_a_scheme(clb):
    """The builder appends a slash but leaves a bare host bare; ``_nav_url`` is what rescues it."""
    built = clb._PAGE_PROBES["personio"]["url"](
        "advancis", "advancis.jobs.personio.com"
    )
    assert not built.startswith("http")
    assert clb._nav_url(built) == "https://advancis.jobs.personio.com/"


def test_workable_builder_ignores_the_stored_url(clb):
    """Why workable is unaffected by the bug at any row count: it derives from the tenant.

    Pinning this stops the fix's documented reach from silently growing to include workable.
    """
    built = clb._PAGE_PROBES["workable"]["url"]("acme", "acme.workable.com")
    assert built == "https://apply.workable.com/acme/"


def test_probe_navigates_a_bare_host_row(clb, monkeypatch):
    """The regression: before the fix this raised and settled UNKNOWN."""
    monkeypatch.setattr(clb, "_RENDER_SETTLE_MS", 0)
    tab = _FakeTab(count=3)
    status, jobs = asyncio.run(
        clb._probe(
            tab, clb._PAGE_PROBES["personio"], "advancis", "advancis.jobs.personio.com"
        )
    )
    assert tab.navigated == "https://advancis.jobs.personio.com/"
    assert (status, jobs) == (clb.LIVE, 3)


def test_probe_does_not_navigate_a_row_with_no_url(clb, monkeypatch):
    monkeypatch.setattr(clb, "_RENDER_SETTLE_MS", 0)
    tab = _FakeTab(count=1)
    status, jobs = asyncio.run(
        clb._probe(tab, clb._PAGE_PROBES["workday"], "some-tenant", "")
    )
    assert tab.navigated is None
    assert (status, jobs) == (clb.UNKNOWN, None)
