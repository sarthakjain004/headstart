"""Grouping, classification and election for the alias ledger (ADR-0111).

Every case here is drawn from the 2026-09-06 SuccessFactors scan, so a regression breaks a test
named after the Board that would suffer it. No network: `resolve` is pure by design, which is the
whole reason the fetching lives in `scripts/validate/dedupe_boards.py` instead.
"""

from __future__ import annotations

from headstart import board_aliases


def resolve(keys, live=None, **kw):
    """`board_aliases.resolve` with the ceremony every case shares."""
    kw.setdefault("signal", "redirect")
    return board_aliases.resolve(keys, live if live is not None else list(keys), **kw)


def test_two_boards_resolving_to_one_live_board_are_a_cluster():
    # basf-se.jobs2web.com -> basf.jobs
    r = resolve({"basf.jobs": "basf.jobs", "basf-se.jobs2web.com": "basf.jobs"})
    assert len(r.clusters) == 1
    assert r.clusters[0].canonical == "basf.jobs"
    assert r.clusters[0].duplicates == ("basf-se.jobs2web.com",)
    assert r.moved == ()


def test_the_canonical_may_be_the_vendor_host():
    """Colas points its vanity name at its SAP-hosted one — the opposite of BASF.

    A "prefer the branded domain" rule would bury the real Board and keep the redirect."""
    r = resolve(
        {
            "colas.jobs.hr.cloud.sap": "colas.jobs.hr.cloud.sap",
            "careers.colasjobs.com": "colas.jobs.hr.cloud.sap",
        }
    )
    assert r.clusters[0].canonical == "colas.jobs.hr.cloud.sap"
    assert r.clusters[0].duplicates == ("careers.colasjobs.com",)


def test_a_cluster_can_hold_more_than_two_boards():
    # Paramount: cbscorporation.jobs and viacomcbs.careers both -> careers.paramount.com
    r = resolve(
        {
            "careers.paramount.com": "careers.paramount.com",
            "cbscorporation.jobs": "careers.paramount.com",
            "viacomcbs.careers": "careers.paramount.com",
        }
    )
    assert len(r.clusters) == 1
    assert r.clusters[0].duplicates == ("cbscorporation.jobs", "viacomcbs.careers")


def test_two_dead_tenants_on_the_vendors_marketing_page_are_not_duplicates():
    """The one that matters: careers.toagroup.com and jobs.bhs-world.com both land on SAP's
    marketing page because both tenants were decommissioned. Grouping on the resolved host alone
    would declare two unrelated companies each other's duplicate."""
    r = resolve(
        {"careers.toagroup.com": "www.sap.com", "jobs.bhs-world.com": "www.sap.com"},
        vendor_hosts={"www.sap.com"},
    )
    assert r.clusters == ()
    assert {m.slug for m in r.moved} == {"careers.toagroup.com", "jobs.bhs-world.com"}
    assert {m.reason for m in r.moved} == {board_aliases.TOMBSTONE}


def test_an_unknown_target_is_migrated_not_a_duplicate():
    r = resolve({"careers.hagergroup.com": "careers.hager.com"})
    assert r.clusters == ()
    assert r.moved[0].reason == board_aliases.MIGRATED
    assert r.moved[0].resolved_to == "careers.hager.com"


def test_a_www_form_is_labelled_separately_from_a_real_migration():
    """Both are "target not in the ledger", but one is a ledger typo and the other is a move."""
    r = resolve({"optimumcareers.com": "www.optimumcareers.com"})
    assert r.moved[0].reason == board_aliases.WWW_VARIANT


def test_a_failed_probe_earns_no_verdict():
    r = resolve({"careers.beyti.eg": None})
    assert r.clusters == ()
    assert r.moved[0].reason == board_aliases.UNREACHABLE


def test_a_canonical_this_scan_never_reached_is_reported_not_elected():
    """The canonical is live but its own probe failed, so the only members present are the ones
    pointing away. Electing among those would promote a Board we know is a duplicate."""
    r = resolve(
        {"a.example": "canon.example", "b.example": "canon.example"},
        live=["a.example", "b.example", "canon.example"],
    )
    assert r.clusters == ()
    assert {m.reason for m in r.moved} == {board_aliases.CANONICAL_UNCONFIRMED}


def test_a_board_nothing_points_at_is_left_alone():
    r = resolve({"jobs.solo.example": "jobs.solo.example"})
    assert r.clusters == ()
    assert r.moved == ()


def test_prefer_overrides_the_redirects_own_answer():
    r = resolve(
        {"basf.jobs": "basf.jobs", "basf-se.jobs2web.com": "basf.jobs"},
        prefer={"basf-se.jobs2web.com"},
    )
    assert r.clusters[0].canonical == "basf-se.jobs2web.com"
    assert r.clusters[0].duplicates == ("basf.jobs",)


def test_ledger_round_trips(tmp_path):
    r = resolve({"basf.jobs": "basf.jobs", "basf-se.jobs2web.com": "basf.jobs"})
    path = tmp_path / "successfactors.csv"
    board_aliases.write(
        path, board_aliases.aliases_of(r, "successfactors", "2026-09-06")
    )
    assert board_aliases.load(path) == {"basf-se.jobs2web.com": "basf.jobs"}


def test_the_ledger_is_looked_up_case_insensitively(tmp_path):
    """A ledger holds one Board under several casings (ADR-0023), and an exact-case miss looks
    exactly like "not a duplicate" — so it would scrape the duplicate anyway, silently."""
    path = tmp_path / "successfactors.csv"
    board_aliases.write(
        path,
        [
            board_aliases.Alias(
                "successfactors",
                "Careers.Example.COM",
                "jobs.example.com",
                "redirect",
                "jobs.example.com",
                "2026-09-06",
            )
        ],
    )
    loaded = board_aliases.load(path)
    assert "careers.example.com" in loaded
    assert loaded["careers.example.com"] == "jobs.example.com"  # value keeps its casing


def test_a_missing_ledger_reads_as_empty(tmp_path):
    """Every ATS reads this on the scrape path; only the scanned ones have a file."""
    assert board_aliases.load(tmp_path / "nothing-here.csv") == {}


def test_path_sits_beside_the_liveness_ledger(tmp_path):
    liveness_dir = tmp_path / "data" / "validate" / "liveness"
    assert (
        board_aliases.path_for(liveness_dir, "successfactors")
        == tmp_path / "data" / "validate" / "aliases" / "successfactors.csv"
    )
