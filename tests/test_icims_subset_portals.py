"""Which iCIMS portals are buried onto another (ADR-0222, ADR-0254).

The script is `scripts/validate/icims_subset_portals.py`. Its election, `burials`, is pure, so each
rule is tested here without a network. Postings are real `(id, title slug)` pairs read off the
portals' sitemaps on 2026-09-28.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

EU = "europecareers-celanese.icims.com"
DE = "germancareers-celanese.icims.com"
RED = "careers-redlobster.icims.com"
RED_ES = "hourly-spanish-redlobster.icims.com"
# germancareers-celanese lists 15 postings, every one also on europecareers-celanese (42).
SHARED = frozenset(
    {
        (
            "23735",
            "maschinenbauingenieur%3ain---spezialist%3ain-maschinentechnik-%28m-w-d%29",
        ),
        ("22680", "industriemechaniker-in-instandhaltung-%28m-w-d%29"),
    }
)
EU_ONLY = frozenset({("21928", "electrical-maintenance-technician.")})
LOBSTER = frozenset({("44954", "cook"), ("44956", "dishwasher"), ("47229", "server")})


@pytest.fixture(scope="module")
def mod():
    """Import the script by path — `scripts/` is not a package."""
    pytest.importorskip("curl_cffi")
    spec = importlib.util.spec_from_file_location(
        "icims_subset_portals",
        ROOT / "scripts" / "validate" / "icims_subset_portals.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _unredirected(postings):
    return {portal: portal for portal in postings}


def test_a_portal_listing_only_what_a_sibling_lists_is_buried(mod):
    postings = {EU: SHARED | EU_ONLY, DE: SHARED}
    assert mod.burials(_unredirected(postings), postings) == {DE: (EU, "subset-reqs")}


def test_mirror_portals_keep_the_lowest_host(mod):
    """careers-redlobster and hourly-spanish-redlobster list the same 2,399 postings."""
    postings = {RED_ES: LOBSTER, RED: LOBSTER}
    assert mod.burials(_unredirected(postings), postings) == {
        RED_ES: (RED, "subset-reqs")
    }


def test_another_customers_portal_is_never_compared(mod):
    """The same pairs on a portal of another customer are no evidence the two are one Board (a
    synthetic host: the rule, not a measurement)."""
    other = "careers-seafood.icims.com"
    postings = {RED: LOBSTER, other: LOBSTER}
    assert mod.burials(_unredirected(postings), postings) == {}


def test_the_same_id_under_another_title_is_another_posting(mod):
    """A synthetic title slug: the rule, not a measurement."""
    postings = {RED: LOBSTER, RED_ES: frozenset({("44954", "cocinero")})}
    assert mod.burials(_unredirected(postings), postings) == {}


def test_a_redirect_buries_onto_its_target(mod):
    """careers-gd-ais answers /sitemap.xml with a redirect to careers-gdms (ADR-0222)."""
    gdms, ais = "careers-gdms.icims.com", "careers-gd-ais.icims.com"
    postings = {gdms: SHARED, ais: SHARED}
    landed = {gdms: gdms, ais: gdms}
    assert mod.burials(landed, postings) == {ais: (gdms, "redirect")}


def test_a_redirect_to_a_buried_portal_lands_on_the_survivor(mod):
    old = "jobs-celanese.icims.com"
    postings = {EU: SHARED | EU_ONLY, DE: SHARED, old: SHARED}
    landed = {EU: EU, DE: DE, old: DE}
    assert mod.burials(landed, postings) == {
        DE: (EU, "subset-reqs"),
        old: (EU, "redirect"),
    }


def test_the_customer_is_the_last_word_of_the_host_label(mod):
    assert mod.customer_of(RED_ES) == "redlobster"
    assert mod.customer_of("careers-gd-ais.icims.com") == "ais"
