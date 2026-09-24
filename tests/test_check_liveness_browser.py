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

from headstart.scrapers.registry import company_from_row

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
        # The defect: a bare host — three builders emitted one before ADR-0203, workday still can.
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


def _board_page(clb, ats: str, tenant: str, url: str) -> str:
    """What `_run` navigates for one ledger row: the builder, fed the row's Scraper slug."""
    slug = company_from_row(ats, tenant, url).slug
    return clb._PAGE_PROBES[ats]["board_page"](slug)


def test_nav_url_returns_none_when_nothing_is_navigable(clb):
    """Workday's builder yields ``"/"`` for a row with no stored URL, and a workday tenant has no
    host to derive one from — so the honest answer is "couldn't tell", not a bogus navigation."""
    assert clb._nav_url("/") is None
    assert clb._nav_url(_board_page(clb, "workday", "some-tenant", "")) is None


def test_personio_board_page_is_the_host_the_scraper_reads(clb):
    """A bare host or a stored deep link both reach the board root on the Scraper's own host."""
    for stored in (
        "advancis.jobs.personio.com",
        "https://advancis.jobs.personio.com/job/186062?language=de",
    ):
        assert (
            _board_page(clb, "personio", "advancis", stored)
            == "https://advancis.jobs.personio.com/"
        )


def test_recruitee_board_page_is_the_board_not_a_stored_deep_link(clb):
    """The ledger stores posting links for some Boards (50 rows on 2026-09-24); the browser must
    count the Board the scrape reads, not one posting's page."""
    assert (
        _board_page(clb, "recruitee", "aellia", "https://aellia.recruitee.com/o/sales")
        == "https://aellia.recruitee.com/"
    )


def test_workable_board_page_ignores_the_stored_url(clb):
    """Workable's slug is the bare tenant, so the stored URL never reaches its board page."""
    built = _board_page(clb, "workable", "acme", "acme.workable.com")
    assert built == "https://apply.workable.com/acme/"


def test_probe_navigates_a_bare_host_row(clb, monkeypatch):
    """The regression: before the fix this raised and settled UNKNOWN. Workday's slug is the
    stored URL itself, so a scheme-less one still reaches `_probe` bare."""
    monkeypatch.setattr(clb, "_RENDER_SETTLE_MS", 0)
    tab = _FakeTab(count=3)
    status, jobs = asyncio.run(
        clb._probe(
            tab, clb._PAGE_PROBES["workday"], "acme.wd1.myworkdayjobs.com/External"
        )
    )
    assert tab.navigated == "https://acme.wd1.myworkdayjobs.com/External/"
    assert (status, jobs) == (clb.LIVE, 3)


def test_probe_does_not_navigate_a_row_with_no_url(clb, monkeypatch):
    monkeypatch.setattr(clb, "_RENDER_SETTLE_MS", 0)
    tab = _FakeTab(count=1)
    status, jobs = asyncio.run(clb._probe(tab, clb._PAGE_PROBES["workday"], ""))
    assert tab.navigated is None
    assert (status, jobs) == (clb.UNKNOWN, None)
