"""Which Radancy fronts are buried as a twin of another (ADR-0265).

The script is `scripts/validate/radancy_subset_fronts.py`; its election, `burials`, is pure. Ids are
real TalentBrew job ids read off the fronts' sitemaps on 2026-09-28.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# Two of the 1,954 postings jobs.jabil.cn and jobs.jabil.com both list.
JABIL = {"100005241744", "100005243536"}
# jobs.carnival.com's 91 all sit on jobs.carnivalcorp.com (232), which also lists 100032244752.
CARNIVAL = {"100019674288", "100043824512"}


@pytest.fixture(scope="module")
def mod():
    pytest.importorskip("curl_cffi")
    spec = importlib.util.spec_from_file_location(
        "radancy_subset_fronts",
        ROOT / "scripts" / "validate" / "radancy_subset_fronts.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_translated_twin_is_buried_onto_the_preferred_front(mod):
    assert mod.burials({"jobs.jabil.cn": JABIL, "jobs.jabil.com": JABIL}) == {
        "jobs.jabil.cn": "jobs.jabil.com"
    }


def test_a_preferred_front_is_never_buried_onto_its_twin(mod):
    """jobs.mt.com.cn listed its 532 shared postings plus 101256191296 (2026-09-28)."""
    shared = {"100010935536", "100012717216"}
    fronts = {"jobs.mt.com": shared, "jobs.mt.com.cn": shared | {"101256191296"}}
    assert mod.burials(fronts) == {}


def test_a_brand_front_inside_a_group_front_is_buried(mod):
    corp = CARNIVAL | {"100032244752"}
    assert mod.burials(
        {"jobs.carnival.com": CARNIVAL, "jobs.carnivalcorp.com": corp}
    ) == {"jobs.carnival.com": "jobs.carnivalcorp.com"}


def test_overlapping_fronts_both_stay(mod):
    """careers.astrazeneca.com and careers.alexion.com share 73 postings, each lists its own."""
    alexion = {"100093434736", "100093432416"}
    astrazeneca = {"100093434736", "100002123952"}
    fronts = {"careers.alexion.com": alexion, "careers.astrazeneca.com": astrazeneca}
    assert mod.burials(fronts) == {}
