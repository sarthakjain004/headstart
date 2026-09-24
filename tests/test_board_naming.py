"""Naming a Board: a stated company or alias wins, and a slug is humanised without inventing."""

from __future__ import annotations

import pytest

from headstart import company_name
from headstart.ingest import board_naming


@pytest.mark.parametrize(
    ("company", "board", "expected"),
    [
        ("", "amazon:www.amazon.jobs", "Amazon"),
        # The company is the *second* label here, because every part of the first is noise
        # (the real case is `careers-inc.nttdata.com`).
        ("", "successfactors:careers-inc.examplecorp.com", "Examplecorp"),
        # ...but the first label elsewhere, which is why the registrable domain is not used.
        ("", "successfactors:acmeco.jobs.hr.cloud.sap", "Acmeco"),
        ("", "workday:micron/External", "Micron"),
        ("", "icims:jobs-bylight.icims.com", "Bylight"),
        # Taleo Enterprise's slug is a whole URL; it used to tidy to "Https:".
        ("", "taleo_enterprise:https://hdr.taleo.net/careersection/ex", "HDR"),
        # SuccessFactors rows carry the host's first label as the company; it names the host.
        ("www", "successfactors:www.afuturewithus.com", "Afuturewithus"),
        ("apply", "successfactors:apply.careers.hsbc.com", "Hsbc"),
        ("join", "successfactors:join.cnh.com", "CNH"),
        ("opportunities", "successfactors:opportunities.vodafone.com", "Vodafone"),
        # A Workday site name in the company column is cased, but it is not the company.
        ("EXTERNAL_CAREERS", "workday:boeing/EXTERNAL_CAREERS", "Boeing"),
        ("CorporateCareers", "workday:mastercard/CorporateCareers", "Mastercard"),
        # ...but a site worded as a brand is often the best name there is.
        ("JioStar", "workday:jiostar/JioStar", "JioStar"),
        ("g-research", "workday:gresearch/g-research", "G Research"),
        # A cased name equal to the whole slug is still the Board's own spelling.
        ("AbhiBus", "smartrecruiters:AbhiBus", "AbhiBus"),
        # A host label that names the company is not a board word, so it stays.
        ("sap", "successfactors:jobs.sap.com", "SAP"),
        ("six-group", "successfactors:careers.six-group.com", "SIX Group"),
        # Taleo Business Edition: the ledger spelling is not a name, and the pod is not a company.
        (
            "GATEWAYVENT:77@phg.tbe.taleo.net/phg01",
            "taleo_be:https://phg.tbe.taleo.net/phg01/ats/careers/v2/searchResults?org=GATEWAYVENT&cws=77",
            "Gatewayvent",
        ),
        (
            "",
            "taleo_be:https://phh.tbe.taleo.net/phh03/ats/careers/v2/searchResults?org=ACME&cws=1",
            "ACME",
        ),
        # The ATS's own title for a site is not the employer; the host names it instead.
        (
            "Oracle Taleo",
            "taleo_enterprise:https://scripps.taleo.net/careersection/2m",
            "Scripps",
        ),
        ("Successfactors", "successfactors:successfactors.tttech.com", "Tttech"),
        # ...but a vendor that hires on its own product is its own name.
        ("Workday", "workday:workday/Workday", "Workday"),
        ("Greenhouse", "greenhouse:greenhouse", "Greenhouse"),
        # Curated where only a slug names the Board.
        ("", "oracle:jpmc.fa.oraclecloud.com", "JPMorgan Chase"),
        ("", "icims:globalcareers-atlassian.icims.com", "Atlassian"),
        # A stated, cased name is never re-cased or trimmed.
        ("CI&T", "lever:ciandt", "CI&T"),
        ("Qantas Group", "smartrecruiters:QantasGroup", "Qantas Group"),
    ],
)
def test_display_name(company: str, board: str, expected: str) -> None:
    assert board_naming.display_name(company, board) == expected


def test_derivation_never_invents_an_expansion() -> None:
    """The *derivation* only spells. An unaliased `swa` is "SWA", never "Southwest Airlines".

    Expanding an abbreviation is human input, and it arrives as an explicit curated-map
    entry (below) rather than as a guess the code makes from three letters.
    """
    assert (
        board_naming.display_name("", "workday:unknownabbrev/external")
        == "Unknownabbrev"
    )


def test_a_curated_alias_beats_both_the_slug_and_a_stated_name() -> None:
    """The alias map is the one place a name is asserted rather than derived."""
    assert board_naming.display_name("", "workday:swa/external") == "Southwest Airlines"
    assert (
        board_naming.display_name(
            "Lockheed", "successfactors:lockheed.jobs.hr.cloud.sap"
        )
        == "Lockheed Martin"
    )


def test_a_mirrored_pair_is_named_alike_without_sharing_an_alias() -> None:
    """Lockheed's Eightfold Board mirrors its SuccessFactors one, so only the latter is aliased
    (ADR-0185): an alias would sum the two in the company directory. The Eightfold Board states
    its own name, so the Hot tab still collapses the pair into one row."""
    assert company_name.curated("eightfold:lockheedmartin.eightfold.ai") is None
    names = {
        board_naming.display_name(
            "Lockheed Martin", "eightfold:lockheedmartin.eightfold.ai"
        ),
        board_naming.display_name(
            "lockheed", "successfactors:lockheed.jobs.hr.cloud.sap"
        ),
    }
    assert names == {"Lockheed Martin"}


@pytest.mark.parametrize(
    "board",
    [
        "workday:nvidia/NVIDIAExternalCareerSite",
        "icims:careers-gd-ais.icims.com",
        "zwayam:careers.persistent.com",
        "lever:1password",
        "taleo_enterprise:https://hdr.taleo.net/careersection/austin_tx",
        "oracle:eeho.fa.us2.oraclecloud.com",
    ],
)
def test_an_unnamed_board_is_spelled_as_the_scrape_spells_it(board: str) -> None:
    """ADR-0212: the Hot and Trends tabs and the served table name a Board alike."""
    assert board_naming.display_name("", board) == company_name.humanised(board)


def test_a_stated_lowercase_name_survives_unless_it_repeats_the_key() -> None:
    """ADR-0212: the scrape serves "incident.io" as stated, so Hot and Trends must too."""
    assert board_naming.display_name("incident.io", "gem:incident") == "incident.io"
    assert board_naming.display_name("11x.ai", "gem:11x") == "11x.ai"
    # a legacy row still carrying the Board's own host is not a stated name
    assert (
        board_naming.display_name(
            "careers.persistent.com", "zwayam:careers.persistent.com"
        )
        == "Persistent"
    )


def test_a_code_only_tenant_has_no_display_name() -> None:
    assert board_naming.display_name("", "oracle:eeho.fa.us2.oraclecloud.com") is None
