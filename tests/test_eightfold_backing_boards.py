"""Which Eightfold Boards are aliased onto the Board(s) of the ATS behind them (ADR-0205).

The script is `scripts/validate/eightfold_backing_boards.py`. Its election, `aliases`, is pure, so
every rule is tested here without a network: an Eightfold Board is aliased when its backing
Boards list its postings and would serve every tech one of them.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

NVIDIA = "eightfold:jobs.nvidia.com"
WD = "workday:nvidia/nvidiaexternalcareersite"


@pytest.fixture(scope="module")
def mod():
    """Import the script by path — `scripts/` is not a package, and it pulls in
    `headstart.http`, so this is skipped wherever that import cannot be satisfied."""
    pytest.importorskip("curl_cffi")
    spec = importlib.util.spec_from_file_location(
        "eightfold_backing_boards",
        ROOT / "scripts" / "validate" / "eightfold_backing_boards.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = (
        module  # a dataclass resolves its module through sys.modules
    )
    spec.loader.exec_module(module)
    return module


def _posts(mod, *specs: str) -> list:
    """``"JR1"`` is a tech posting keyed JR1, ``"JR1-"`` a non-tech one."""
    return [
        mod.Posting(frozenset({s.rstrip("-")}), tech=not s.endswith("-")) for s in specs
    ]


def test_a_board_whose_backing_board_lists_every_posting_is_aliased_onto_it(mod):
    listings = {
        NVIDIA: _posts(mod, "JR1", "JR2-"),
        WD: _posts(mod, "JR1", "JR2-", "JR3"),
    }
    assert mod.aliases({NVIDIA: (WD,)}, listings, {NVIDIA, WD}) == {NVIDIA: (WD,)}


def test_one_tech_posting_the_backing_board_does_not_list_keeps_the_board(mod):
    """Zero tolerance for tech: aliasing would take that posting out of search."""
    listings = {NVIDIA: _posts(mod, "JR1", "JR9"), WD: _posts(mod, "JR1")}
    assert mod.aliases({NVIDIA: (WD,)}, listings, {NVIDIA, WD}) == {}


def test_non_tech_postings_nowhere_else_are_tolerated_up_to_one_percent(mod):
    """Listings are read minutes apart and postings propagate between them (micron's unmatched
    count read 2, 20 and 0 on one day), so a strict bar flaps. The margin is for non-tech only:
    those are never served, so aliasing loses nothing a search can show."""
    board = _posts(mod, *[f"R{i}-" for i in range(200)])
    backing = _posts(mod, *[f"R{i}-" for i in range(198)])
    assert mod.aliases({NVIDIA: (WD,)}, {NVIDIA: board, WD: backing}, {NVIDIA, WD}) == {
        NVIDIA: (WD,)
    }
    backing = _posts(mod, *[f"R{i}-" for i in range(197)])  # 3 of 200 is 1.5%
    assert (
        mod.aliases({NVIDIA: (WD,)}, {NVIDIA: board, WD: backing}, {NVIDIA, WD}) == {}
    )


def test_a_tech_posting_whose_backing_copy_the_tech_gate_drops_keeps_the_board(mod):
    """Listed is not served. The backing Board's copy carries its own department, and the tech
    gate reads that: citi's "Applications Development" requisitions are tech under Eightfold's
    department and not under Workday's, so aliasing would take them out of search."""
    listings = {NVIDIA: _posts(mod, "JR1"), WD: _posts(mod, "JR1-")}
    assert mod.aliases({NVIDIA: (WD,)}, listings, {NVIDIA, WD}) == {}


HP = "eightfold:hp.eightfold.ai"
HP_US = "workday:hp/externalcareersite"
HP_EU = "workday:hp/exteu-ac-careersite"


def test_a_board_backed_by_several_boards_needs_all_of_them_scrapable(mod):
    """hp's postings are split over two Workday sites. Aliased onto both while both are
    Scrapable; when either drops out, the Eightfold Board comes back."""
    listings = {
        HP: _posts(mod, "R1", "R2"),
        HP_US: _posts(mod, "R1"),
        HP_EU: _posts(mod, "R2"),
    }
    everything = {HP, HP_US, HP_EU}
    assert mod.aliases({HP: (HP_US, HP_EU)}, listings, everything) == {
        HP: (HP_US, HP_EU)
    }
    assert mod.aliases({HP: (HP_US, HP_EU)}, listings, everything - {HP_EU}) == {}


def test_an_unread_or_empty_board_is_never_aliased(mod):
    """No read earns no verdict, and the empty set is contained in anything."""
    everything = {HP, HP_US, HP_EU}
    backing = {HP: (HP_US, HP_EU)}
    full = {HP: _posts(mod, "R1"), HP_US: _posts(mod, "R1"), HP_EU: _posts(mod, "R2")}
    assert mod.aliases(backing, {**full, HP: None}, everything) == {}
    assert mod.aliases(backing, {**full, HP_EU: None}, everything) == {}
    assert mod.aliases(backing, {**full, HP: []}, everything) == {}


LOSER = "eightfold:nvidia.eightfold.ai"


def test_a_second_eightfold_site_follows_its_winner_onto_the_winners_backing_board(mod):
    """`nvidia.eightfold.ai` serves `jobs.nvidia.com`'s postings. When the winner is itself
    aliased onto Workday, the second site is aliased onto Workday too — never onto a Board that
    is no longer scraped. When the winner stays, the second site is aliased onto the winner."""
    backing = {LOSER: (NVIDIA,), NVIDIA: (WD,)}
    listings = {
        LOSER: _posts(mod, "JR1"),
        NVIDIA: _posts(mod, "JR1"),
        WD: _posts(mod, "JR1"),
    }
    everything = {LOSER, NVIDIA, WD}
    assert mod.aliases(backing, listings, everything) == {NVIDIA: (WD,), LOSER: (WD,)}
    assert mod.aliases(backing, {**listings, WD: None}, everything) == {
        LOSER: (NVIDIA,)
    }


def _ledgers(root: Path) -> Path:
    """jobs.nvidia.com live on Eightfold and its Workday site live, nvidia.eightfold.ai dead."""
    liveness = root / "liveness"
    liveness.mkdir(parents=True)
    head = "ats,tenant,url,status,jobs,checked_at\n"
    (liveness / "eightfold.csv").write_text(
        head
        + "eightfold,jobs.nvidia.com,jobs.nvidia.com,live,2,2026-07-21\n"
        + "eightfold,nvidia.eightfold.ai,nvidia.eightfold.ai,dead,,2026-08-16\n",
        encoding="utf-8",
    )
    wd = "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"
    (liveness / "workday.csv").write_text(
        head + f"workday,nvidia/nvidiaexternalcareersite,{wd},live,3,2026-09-01\n",
        encoding="utf-8",
    )
    return liveness


def test_the_ledger_is_rewritten_each_run_and_a_winner_it_buried_can_come_back(
    mod, tmp_path
):
    """Run 1 buries both Eightfold sites onto Workday. Run 2 cannot read Workday, so
    `jobs.nvidia.com` comes back — and its second site, whose dead row the prober must keep
    skipping, is buried onto it instead, although run 1's ledger is what kept it off the scrape
    list."""
    from headstart import board_aliases

    liveness = _ledgers(tmp_path)
    backing = {"jobs.nvidia.com": (WD,), "nvidia.eightfold.ai": (NVIDIA,)}
    board = _posts(mod, "JR1", "JR2-")
    reads = {"jobs.nvidia.com": board, "nvidia.eightfold.ai": board}
    wd_listing = _posts(mod, "JR1", "JR2-")

    def read(ats, slug):
        return wd_listing if ats == "workday" else reads[slug]

    mod.write_aliases(liveness, read, "2026-09-24", backing)
    path = board_aliases.path_for(liveness, "eightfold")
    assert path.read_text(encoding="utf-8").splitlines()[1:] == [
        f"eightfold,jobs.nvidia.com,{WD},backing-reqs,{WD},2026-09-24",
        f"eightfold,nvidia.eightfold.ai,{WD},backing-reqs,{WD},2026-09-24",
    ]

    wd_listing = None
    mod.write_aliases(liveness, read, "2026-09-25", backing)
    assert board_aliases.load_for(liveness, "eightfold") == {
        "nvidia.eightfold.ai": NVIDIA
    }


def test_lumen_is_not_a_candidate(mod):
    """The user kept Lumen (its backing site is an internal careers site). International SOS is
    a candidate since ADR-0210's 2026-09-25 amendment: the postings it lists alone keep it off the
    alias ledger (rule 1), and the ones its backing Board serves are served once."""
    assert "lumen.eightfold.ai" not in mod.BACKING


def test_a_backing_board_on_an_ats_with_no_reader_is_unread(mod):
    """Lever and Jibe back a pair (Tinder, AARP) but have no reader here. An unread backing Board
    earns no verdict (`_refusal`), so their Eightfold sites stay off the alias ledger; the row-level
    rule (ADR-0210) still serves each shared posting once."""
    assert mod.read_board("lever", "matchgroup") is None
    assert mod.read_board("jibe", "aarp") is None
