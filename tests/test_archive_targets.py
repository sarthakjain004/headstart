"""Every registered scraper must have a deliberate archive sweep route."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "discover"))

import archive_targets as targets
import cc_miner
import wayback_feeder

from headstart.scrapers.registry import SCRAPERS


def test_every_scraper_is_swept_or_explicitly_single_source():
    for namespaces in (cc_miner.ATS_PATTERNS, wayback_feeder.ATS_HOSTS):
        assert (
            set(namespaces)
            | targets.COMPANY_DOMAIN_ATS
            | targets.SINGLE_SOURCE_ATS
            | targets.MARKETPLACE_SOURCE_ATS
            == set(SCRAPERS)
        )
        assert not set(namespaces) & targets.COMPANY_DOMAIN_ATS
        assert not set(namespaces) & targets.SINGLE_SOURCE_ATS
        assert not set(namespaces) & targets.MARKETPLACE_SOURCE_ATS


def test_cc_includes_every_measured_wayback_regional_and_legacy_host():
    for ats, hosts in wayback_feeder.ATS_HOSTS.items():
        assert {host for host, _ in hosts} <= set(cc_miner.ATS_PATTERNS[ats]["targets"])


def test_hybrid_providers_keep_their_known_host_audit_route():
    assert {
        "eightfold",
        "successfactors",
        "zwayam",
        "gr8people",
    } <= targets.KNOWN_HOST_ATS
