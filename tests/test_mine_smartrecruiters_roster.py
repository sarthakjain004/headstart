"""Tests for the SmartRecruiters roster miner's identifier reading
(scripts/discover/mine_smartrecruiters_roster.py). A script under `scripts/discover`."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "discover"))

import mine_smartrecruiters_roster as miner


def test_identifiers_keep_the_case_sensitive_slug_and_drop_the_vendors_own_test_clients():
    roster = (
        '- company: "01 Systems"\n'
        '  company_identifier: "01Systems"\n'
        "- company: + TALENTO GUATEMALA\n"
        "  company_identifier: TALENTOGUATEMALA\n"
        "- company: SmartRecruiters test\n"
        "  company_identifier: SRTestPersonalPlan123\n"
        "- company: no id\n"
    )
    assert miner.identifiers(roster) == ["01Systems", "TALENTOGUATEMALA"]
