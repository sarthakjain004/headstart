"""Tests for the browser liveness escalation (scripts/validate/check_liveness_browser.py).

Covers ``_absolute``, which exists because the ledger does not normalise scheme: 4,809 rows
across the four supported ATSes store a bare host rather than a URL. ``go_to`` raises on those,
``_probe`` maps the raise to UNKNOWN, and the run then reads as a wall rather than as an address
that was never navigable. Every string here is a real ledger shape, not an invented one.
"""

from __future__ import annotations

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


@pytest.mark.parametrize(
    "stored,expected",
    [
        # The defect: bare hosts, as 3,292 personio / 1,287 workable / 225 recruitee rows store.
        ("foo.jobs.personio.com", "https://foo.jobs.personio.com"),
        ("advancis.jobs.personio.com/", "https://advancis.jobs.personio.com/"),
        ("acme.recruitee.com/", "https://acme.recruitee.com/"),
        # Already absolute: left exactly alone, including scheme and trailing slash.
        ("https://1qhealth.jobs.personio.de/", "https://1qhealth.jobs.personio.de/"),
        ("http://legacy.example.com/", "http://legacy.example.com/"),
    ],
)
def test_absolute_normalises_only_when_scheme_is_missing(clb, stored, expected):
    assert clb._absolute(stored) == expected


@pytest.mark.parametrize("stored", ["", "   ", None, "/"])
def test_absolute_returns_none_when_nothing_is_navigable(clb, stored):
    """A row with no usable address must not become ``https:///`` or a bare ``/``.

    Workday's ``url`` lambda yields ``"/"`` when the ledger stores no URL, and a workday tenant
    has no derivable host — so the only honest answer is "couldn't tell", not a bogus navigation.
    """
    assert clb._absolute(stored) is None


def test_workday_row_without_a_url_is_not_navigable(clb):
    """The end-to-end shape of the None case, through the real per-ATS url builder."""
    built = clb._PAGE_PROBES["workday"]["url"]("some-tenant", "")
    assert clb._absolute(built) is None


def test_personio_bare_host_survives_the_url_builder(clb):
    """The builder appends a slash but cannot add a scheme; ``_absolute`` is what rescues it."""
    built = clb._PAGE_PROBES["personio"]["url"](
        "advancis", "advancis.jobs.personio.com"
    )
    assert not built.startswith("http")
    assert clb._absolute(built) == "https://advancis.jobs.personio.com/"
