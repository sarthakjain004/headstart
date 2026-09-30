"""Tests for the hostname -> ledger row mapping the host-list miners share
(scripts/discover/board_hosts.py). A script under `scripts/discover`, imported by name."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "discover"))

import board_hosts


def test_a_sitemap_names_each_host_of_its_links_once():
    sitemap = (
        "<urlset>"
        "<url><loc>https://easeinc.applytojob.com/apply/AbC123/Engineer?source=GS</loc></url>"
        "<url><loc>https://easeinc.applytojob.com/apply/Zzz999/Nurse</loc></url>"
        "<url><loc> https://pitchup.applytojob.com/apply/Q1/Cook </loc></url>"
        "</urlset>"
    )
    assert board_hosts.sitemap_hosts(sitemap) == {
        "easeinc.applytojob.com",
        "pitchup.applytojob.com",
    }


def test_a_board_host_is_a_row_in_the_ledgers_own_spelling():
    assert board_hosts.row_for_host("Acme.BambooHR.com") == (
        "bamboohr",
        "acme",
        "https://acme.bamboohr.com",
    )
    assert board_hosts.row_for_host("x.applytojob.com") == (
        "jazzhr",
        "x",
        "https://x.applytojob.com",
    )
    assert board_hosts.row_for_host("1force.hire.trakstar.com") == (
        "trakstar",
        "1force",
        "https://1force.hire.trakstar.com",
    )


def test_teamtailors_regional_pod_is_a_board_of_its_own():
    """`{slug}.na.teamtailor.com` is tenant `{slug}.na`, as the six rows already in the ledger are;
    `wayback_feeder.extract` drops any dotted label, which is why the pod was never mined."""
    assert board_hosts.row_for_host("robot.na.teamtailor.com") == (
        "teamtailor",
        "robot.na",
        "https://robot.na.teamtailor.com",
    )
    assert board_hosts.row_for_host("robot.teamtailor.com") == (
        "teamtailor",
        "robot",
        "https://robot.teamtailor.com",
    )


def test_personio_keeps_the_tld_it_was_seen_on():
    assert (
        board_hosts.row_for_host("foo.jobs.personio.de")[2]
        == "https://foo.jobs.personio.de"
    )
    assert (
        board_hosts.row_for_host("foo.jobs.personio.com")[2]
        == "https://foo.jobs.personio.com"
    )


def test_vendor_infrastructure_and_deeper_hosts_are_not_tenants():
    for host in (
        "www.bamboohr.com",
        "app.recruitee.com",
        "assets.freshteam.com",
        "careers.acme.bamboohr.com",
        "hire.trakstar.com",
        "20110401165954_tkaecehexnbpi04p.applytojob.com",
        "example.com",
    ):
        assert board_hosts.row_for_host(host) is None, host


def test_reversed_prefixes_name_each_family_for_a_reverse_sorted_list():
    prefixes = board_hosts.reversed_prefixes()
    assert "com.bamboohr." in prefixes
    assert "com.teamtailor.na." in prefixes
    assert "de.personio.jobs." in prefixes
    assert "com.trakstar.hire." in prefixes
