"""Which Taleo Enterprise career sections are buried as a subset of another (ADR-0186).

The script is `scripts/validate/taleo_enterprise_subset_sections.py`. Its election, `burials`, is
pure, so every rule is tested here without a network: a section is buried when its requisitions
are a non-empty subset of another section of the same tenant, onto a maximal section.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

HDR = "https://hdr.taleo.net/careersection"
BAE = "https://baesystems.taleo.net/careersection"


@pytest.fixture(scope="module")
def mod():
    """Import the script by path — `scripts/` is not a package, and it pulls in
    `headstart.http`, so this is skipped wherever that import cannot be satisfied."""
    pytest.importorskip("curl_cffi")
    spec = importlib.util.spec_from_file_location(
        "taleo_enterprise_subset_sections",
        ROOT / "scripts" / "validate" / "taleo_enterprise_subset_sections.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mirrors_keep_one_section_whatever_order_they_arrive_in(mod):
    """HDR serves the same 2,282 reqs on 15 sections. One is kept, and the same one every time."""
    reqs = {"1", "2", "3"}
    sections = [f"{HDR}/ex", f"{HDR}/2", f"{HDR}/int"]
    forward = mod.burials({s: reqs for s in sections})
    backward = mod.burials({s: reqs for s in reversed(sections)})
    assert forward == backward == {f"{HDR}/ex": f"{HDR}/2", f"{HDR}/int": f"{HDR}/2"}


def test_a_chain_buries_every_link_onto_the_top_section(mod):
    """A ⊂ B ⊂ C buries A and B onto C — never A onto B, which is itself buried."""
    buried = mod.burials(
        {
            f"{HDR}/a": {"1"},
            f"{HDR}/b": {"1", "2"},
            f"{HDR}/c": {"1", "2", "3"},
        }
    )
    assert buried == {f"{HDR}/a": f"{HDR}/c", f"{HDR}/b": f"{HDR}/c"}


def test_a_partial_overlap_keeps_both_sections(mod):
    """BAE's sections overlap without either containing the other (209 union vs 176 largest).
    Both stay; burying either would hide the reqs only it lists."""
    assert mod.burials({f"{BAE}/us": {"1", "2"}, f"{BAE}/uk": {"2", "3"}}) == {}


def test_a_section_under_two_overlapping_sections_goes_to_the_larger(mod):
    buried = mod.burials(
        {
            f"{BAE}/us": {"1", "2", "3"},
            f"{BAE}/uk": {"2", "4"},
            f"{BAE}/shared": {"2"},
        }
    )
    assert buried == {f"{BAE}/shared": f"{BAE}/us"}


def test_an_empty_section_is_never_buried(mod):
    """The empty set is a subset of everything, so containment says nothing about it. A section
    with nothing open today may post a req no other section lists tomorrow."""
    assert mod.burials({f"{HDR}/ex": {"1"}, f"{HDR}/campus": set()}) == {}
    assert mod.burials({f"{HDR}/ex": set(), f"{HDR}/int": set()}) == {}


def test_the_same_reqs_on_two_tenants_are_not_a_subset(mod):
    """A tenant is the section URL's host, and ids are only ever compared within one."""
    assert (
        mod.burials(
            {f"{HDR}/ex": {"1"}, "https://ttec.taleo.net/careersection/2": {"1"}}
        )
        == {}
    )


def _liveness_dir(root: Path, sections: dict[str, str]) -> Path:
    """A liveness dir holding one taleo_enterprise row per ``{section: status}``."""
    liveness = root / "liveness"
    liveness.mkdir(parents=True)
    rows = [f"taleo_enterprise,{s},{s},{st},5,2026-09-13" for s, st in sections.items()]
    (liveness / "taleo_enterprise.csv").write_text(
        "ats,tenant,url,status,jobs,checked_at\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    return liveness


def test_a_buried_section_that_gains_its_own_req_is_unburied(mod, tmp_path):
    """The run re-reads the sections the last run buried — they are still live rows — and
    rewrites the ledger from what it reads now, so nothing a past run concluded survives."""
    from headstart import board_aliases

    liveness = _liveness_dir(tmp_path, {f"{HDR}/ex": "live", f"{HDR}/int": "live"})
    reqs = {f"{HDR}/ex": {"1", "2"}, f"{HDR}/int": {"1"}}
    mod.write_aliases(liveness, reqs.get, "2026-09-24")
    assert board_aliases.load_for(liveness, "taleo_enterprise") == {
        f"{HDR}/int": f"{HDR}/ex"
    }

    reqs[f"{HDR}/int"] = {"1", "3"}  # int now lists a req ex does not
    mod.write_aliases(liveness, reqs.get, "2026-09-25")
    assert board_aliases.load_for(liveness, "taleo_enterprise") == {}


def test_only_sections_on_live_rows_are_read_and_oracles_demo_tenant_is_not(
    mod, tmp_path
):
    """`pmg` is Oracle's own demo tenant ("Director of Finance (DEMO)", "TEST 2 EPredix
    Assessment"), excluded in `config.EXCLUDED_BOARDS`. Its two sections mirror each other, so
    reading them would write an alias row for a Board that is never scraped anyway."""
    pmg = "https://pmg.taleo.net/careersection"
    liveness = _liveness_dir(
        tmp_path,
        {
            f"{HDR}/ex": "live",
            f"{HDR}/gone": "dead",
            f"{pmg}/m1": "live",
            f"{pmg}/brandtss_faceted": "live",
        },
    )
    read: list[str] = []

    def reqs_of(section):
        read.append(section)
        return {"1"}

    assert mod.write_aliases(liveness, reqs_of, "2026-09-24") == []
    assert read == [f"{HDR}/ex"]


def test_an_unreadable_section_is_neither_buried_nor_a_kept_section(mod, tmp_path):
    """edmonton's shells served no `portalNo` in one run and did 3.5 h later. A section with no
    known set is no evidence either way: it is not buried, and nothing is buried onto it."""
    liveness = _liveness_dir(
        tmp_path, {f"{HDR}/all": "live", f"{HDR}/ex": "live", f"{HDR}/int": "live"}
    )

    def reqs_of(section):
        if section.endswith("/all"):
            raise ValueError("Career Section shell has no portalNo")
        return {"1", "2"} if section.endswith("/ex") else {"1"}

    aliases = mod.write_aliases(liveness, reqs_of, "2026-09-24")
    assert {a.duplicate: a.canonical for a in aliases} == {f"{HDR}/int": f"{HDR}/ex"}


def test_a_bug_in_the_read_is_not_taken_for_an_unreadable_section(mod, tmp_path):
    """Only a failed request or a malformed page counts as unreadable. Anything else is a bug,
    and filing it as one more unreadable section would hide it."""
    liveness = _liveness_dir(tmp_path, {f"{HDR}/ex": "live"})

    def reqs_of(section):
        raise KeyError("id")

    with pytest.raises(KeyError):
        mod.write_aliases(liveness, reqs_of, "2026-09-24")
