"""The pure parts of `scripts/discover/mine_taleo_enterprise_sections.py`, with no network.

What matters most: a probe that never settled (timeout, reset, 5xx, 429) is never read as "the section does not
exist", and a host is only called decommissioned when every probe came back 404/410.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

SHELL = "<title>Job Search</title><script>portalNo: '101430233'</script>"
UNAVAILABLE = "<title>Career Section Unavailable</title>"


@pytest.fixture(scope="module")
def mod():
    """Import the script by path (`scripts/` is not a package); it pulls in `headstart.network.http`."""
    pytest.importorskip("curl_cffi")
    spec = importlib.util.spec_from_file_location(
        "mine_taleo_enterprise_sections",
        ROOT / "scripts" / "discover" / "mine_taleo_enterprise_sections.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_shell_with_a_portal_number_is_a_section(mod):
    assert mod.classify_probe(200, SHELL) == mod.SECTION


def test_a_section_unavailable_page_is_an_absent_section_not_a_dead_host(mod):
    assert mod.classify_probe(200, UNAVAILABLE) == mod.ABSENT


def test_a_404_is_gone(mod):
    assert mod.classify_probe(404, "") == mod.GONE
    assert mod.classify_probe(410, "") == mod.GONE


def test_a_redirect_is_not_a_section(mod):
    assert mod.classify_probe(302, "", "https://stantec.jobs/") == mod.REDIRECT


@pytest.mark.parametrize("status", [None, 0, 429, 500, 502, 503])
def test_an_unsettled_answer_is_never_read_as_absent(mod, status):
    """A timeout has no status; a 429 or 5xx says ask again. None of them says the section is not there."""
    assert mod.classify_probe(status, UNAVAILABLE) == mod.UNSETTLED


def test_a_200_that_is_neither_shell_nor_unavailable_page_is_unsettled(mod):
    """Drhorton's `/careersection/1` answered 200 with neither: not proof of a section, not proof of none."""
    assert mod.classify_probe(200, "<html>something else</html>") == mod.UNSETTLED


def test_a_host_is_decommissioned_only_when_every_probe_was_gone(mod):
    assert mod.host_state([mod.GONE] * 5) == "decommissioned"
    assert mod.host_state([mod.GONE, mod.GONE, mod.UNSETTLED]) == "active"
    assert mod.host_state([mod.GONE, mod.ABSENT]) == "active"


def test_a_host_where_nothing_settled_is_unreachable_not_decommissioned(mod):
    assert mod.host_state([mod.UNSETTLED] * 4) == "unreachable"
    assert mod.host_state([]) == "unreachable"


def test_cdx_lines_yield_section_names_and_skip_taleo_asset_folders(mod):
    text = (
        "https://x.taleo.net/careersection/ex/jobsearch.ftl?lang=en\n"
        "https://x.taleo.net/careersection/2024PRD.2.0.50.3.0/theme/css/a.css/jobsearch.ftl\n"
        "https://x.taleo.net/careersection/rest/jobboard/searchjobs/jobdetail.ftl\n"
        "https://x.taleo.net/careersection/jea+-+external/jobdetail.ftl?job=1\n"
        "https://x.taleo.net/careersection/iam/accessmanagement/jobapply.ftl\n"
    )
    assert mod.cdx_section_names(text) == {"ex", "jea+-+external"}


def test_candidates_are_core_first_then_ledger_then_archive_each_once(mod):
    got = mod.candidate_sections({"ex", "swd_gdp"}, {"swd_gdp", "alny_ext"})
    assert got[: len(mod.CORE)] == mod.CORE
    assert got[len(mod.CORE) :] == ["swd_gdp", "alny_ext"]
    assert len(got) == len(set(got))
