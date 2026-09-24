"""Naming a Board: a stated company or alias wins, and a slug is tidied without inventing."""

from __future__ import annotations

import pytest

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
        ("", "taleo_enterprise:https://hdr.taleo.net/careersection/ex", "Hdr"),
        # SuccessFactors rows carry the host's first label as the company; it names the host.
        ("www", "successfactors:www.afuturewithus.com", "Afuturewithus"),
        ("apply", "successfactors:apply.careers.hsbc.com", "Hsbc"),
        ("join", "successfactors:join.cnh.com", "Cnh"),
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
        ("sap", "successfactors:jobs.sap.com", "Sap"),
        ("six-group", "successfactors:careers.six-group.com", "Six Group"),
        # Taleo Business Edition: the ledger spelling is not a name, and the pod is not a company.
        (
            "GATEWAYVENT:77@phg.tbe.taleo.net/phg01",
            "taleo_be:https://phg.tbe.taleo.net/phg01/ats/careers/v2/searchResults?org=GATEWAYVENT&cws=77",
            "Gatewayvent",
        ),
        (
            "",
            "taleo_be:https://phh.tbe.taleo.net/phh03/ats/careers/v2/searchResults?org=ACME&cws=1",
            "Acme",
        ),
        # A stated, cased name is never re-cased or trimmed.
        ("CI&T", "lever:ciandt", "CI&T"),
        ("Qantas Group", "smartrecruiters:QantasGroup", "Qantas Group"),
    ],
)
def test_display_name(company: str, board: str, expected: str) -> None:
    assert board_naming.display_name(company, board) == expected


def test_derivation_never_invents_an_expansion() -> None:
    """The *derivation* only tidies. An unaliased `swa` stays "Swa", never "Southwest Airlines".

    Expanding an abbreviation is human input, and it arrives as an explicit DISPLAY_ALIASES
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


def test_aliases_unify_one_company_across_two_atses() -> None:
    """Lockheed Martin reached Expansion on Eightfold *and* SuccessFactors, ranking 1 and 2."""
    both = {
        "eightfold:lockheedmartin.eightfold.ai",
        "successfactors:lockheed.jobs.hr.cloud.sap",
    }
    assert {board_naming.display_name("", b) for b in both} == {"Lockheed Martin"}
