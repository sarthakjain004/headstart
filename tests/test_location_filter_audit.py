"""Tests for the India location-filter audit (scripts/eval/location_filter_audit.py).

Only `is_placeless` is covered, and deliberately so: it decides the field-health numbers the
audit reports per ATS, so a string it scores wrongly does not fail loudly — it quietly moves the
count that the whole section exists to produce. Every case below is a string measured on a live
Board, not an invented one.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "eval"
    / "location_filter_audit.py"
)


@pytest.fixture(scope="module")
def audit():
    spec = importlib.util.spec_from_file_location("location_filter_audit", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(
    "location",
    [
        # measured on live recruitee Boards, where a localized marker sits in `location`
        # instead of a place on 476 of 1,922 offers across 138 Boards
        "Remote job",
        "Poste à distance",
        "Poste a distance",  # the same marker, unaccented
        "POSTE À DISTANCE",  # and shouting
        "Homeoffice",
        "Home Office",
        "Werken op afstand",
        "Trabajo a distancia",
        "Praca zdalna",
        "Trabalho remoto",
        "Lavoro da remoto",
        # the English tokens that were already here
        "Remote",
        "Anywhere",
        "N/A",
        "",
    ],
)
def test_a_localized_remote_marker_is_placeless(audit, location):
    """Counting a French marker as a valid place under-reports the very defect being measured.

    Before this, `_PLACELESS` held only `remote job`, so the 269 French and 80 German markers in
    that sample were scored as places.
    """
    assert audit.is_placeless(location.lower()) is True


@pytest.mark.parametrize(
    "location",
    [
        # A country tag is coarse, but it is a place and the filter can be taught to read it.
        # Calling it placeless would overstate the problem in the other direction.
        "US",
        "USA",
        "IN",
        "Karnataka, IN",
        # ordinary places, including ones that merely *contain* a placeless word
        "Bangalore",
        "Kuala Lumpur, MY, 50450",
        "Mainz",
        "Remote, Bangalore",
        "Global Technology Centre, Pune",
    ],
)
def test_a_real_place_is_not_placeless(audit, location):
    assert audit.is_placeless(location.lower()) is False
