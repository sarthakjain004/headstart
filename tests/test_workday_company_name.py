"""The Workday company-name cascade, driven by values real Boards served live on 2026-09-24."""

import pytest

from headstart.scrapers.workday_company_name import board_name, clean


def _page(title: str | None = None, description: str | None = None) -> str:
    """A Workday board page shell carrying only the two og tags the cascade reads."""
    tags = []
    if title is not None:
        tags.append(f'<meta name="title" property="og:title" content="{title}">')
    if description is not None:
        tags.append(
            f'<meta name="description" property="og:description" content="{description}">'
        )
    return f"<html><head>{''.join(tags)}<title></title></head></html>"


@pytest.mark.parametrize(
    ("entity", "cleaned"),
    [
        ("2100 NVIDIA USA", "NVIDIA"),
        ("QLYS_IN Qualys Security TechServices Private Ltd.", "Qualys Security TechServices"),
        ("02 CACI, INC.-FEDERAL", "CACI"),
        ("20-2450790 Thermo Electron Scientific Instruments LLC",
         "Thermo Electron Scientific Instruments"),
        ("BE0442.775.009 PPD International Holdings, LLC Belgium Branch",
         "PPD International Holdings, LLC Belgium Branch"),
        ("ADUS-Adobe Inc.", "Adobe"),
        ("3M Company", "3M"),
        ("7-Eleven, Inc.", "7-Eleven"),
        ("The Walsh Group", "Walsh Group"),
        ("Acme Holdings Ltd. dba Roadrunner Logistics", "Roadrunner Logistics"),
    ],
)  # fmt: skip
def test_clean_strips_codes_legal_forms_and_country_but_keeps_a_brand(entity, cleaned):
    assert clean(entity) == cleaned


def test_nvidia_names_itself_through_its_own_page():
    entities = [
        "2100 NVIDIA USA",
        "IN01 NVIDIA Graphics Bengaluru",
        "IL00 Mellanox Technologies, Ltd.",
        "2100 NVIDIA USA",
    ]
    page = _page(
        "CAREERS AT NVIDIA",
        "NVIDIA pioneered accelerated computing. Learn more about NVIDIA.",
    )
    assert board_name(entities, page, "nvidia/NVIDIAExternalCareerSite") == (
        "NVIDIA",
        "hiringOrganization",
    )


def test_airbus_nine_entities_vote_for_one_name_in_the_page_casing():
    entities = [
        "1466 Airbus Helicopters SAS",
        "2626 Airbus Canada Limited Partnership",
        "3507 Airbus Operations SAS",
        "2164 Airbus Defence and Space SAU",
        "2788 Airbus Defence and Space SAS",
        "3510 AIRBUS SAS",
        "2781 Airbus Atlantic",
        "5404 AIRBUS HELICOPTERS DEUTSCHLAND GmbH",
        "5405 Airbus Operations GmbH",
    ]
    page = _page(
        "Careers",
        "Airbus pioneers sustainable aerospace for a safe and united world.",
    )
    assert board_name(entities, page, "ag/Airbus") == ("Airbus", "hiringOrganization")


def test_northrop_division_codes_name_nothing():
    """Every value is a division or an office; the Board is left for a curated name."""
    entities = [
        "0090 CORP-Corporate Office",
        "0887 DS - Armament Systems\xa0",
        "0909 SP - Orbital Space Systems",
        "0894 DS - MP - ABL - Rocket Center",
        "0904 SP - Space Components",
        "0902 SP - Launch Vehicles",
    ]
    page = _page(
        None,
        "Existing Applicants: Need to update your contact information or email address?",
    )
    assert board_name(entities, page, "ngc/Northrop_Grumman_External_Site") == (
        None,
        "none",
    )


def test_an_entity_code_tenant_votes_past_its_codes():
    entities = ["QLYS_US Qualys, Inc."] * 4 + [
        "QLYS_IN Qualys Security TechServices Private Ltd."
    ] * 8
    page = _page("Qualys Careers", "Join our talent community.")
    assert board_name(entities, page, "qualys/Careers") == (
        "Qualys",
        "hiringOrganization",
    )


def test_the_board_slug_vouches_when_the_page_says_nothing():
    """humana's page carries no og text at all; the tenant label is the only corroboration."""
    entities = [
        "427 CDO 2, LLC",
        "004 Humana Insurance Company",
        "003 Humana Inc.",
        "004 Humana Insurance Company",
    ]
    assert board_name(entities, _page(), "humana/Humana_External_Career_Site") == (
        "Humana",
        "hiringOrganization",
    )


def test_a_brand_spelled_with_digits_is_a_proper_noun():
    """8x8 carries no capital, so a capital-only check never vouched for it (2026-09-24)."""
    entities = [
        "8x8, Inc. (U.S)",
        "8x8 UK Ltd.",
        "8x8 International Philippine Branch Office",
    ]
    page = _page("Careers at 8x8", "what life at 8x8 is like")
    assert board_name(entities, page, "8x8inc/8x8_External_Careers") == (
        "8x8",
        "hiringOrganization",
    )


def test_a_one_word_generic_run_is_never_a_name():
    entities = ["Health Partners Plans LLC"] * 3
    page = _page(None, "Health is our mission.")
    assert board_name(entities, page, "hpp/careers") == (None, "none")


def test_an_unchecked_entity_is_not_served_even_when_unanimous():
    """Holmes Murphy's postings all state a holding company its own page never names."""
    entities = ["HMA Group Holdings, LLC"] * 12
    page = _page(
        "Career Opportunities",
        "There's No Place Like Holmes! You'll love what you do when you join Holmes Murphy!",
    )
    assert board_name(entities, page, "holmesmurphy/HolmesMurphyCareers") == (
        None,
        "none",
    )


def test_a_wrapped_og_title_names_a_board_with_no_entities():
    assert board_name([""] * 4, _page("Careers at Micron"), "micron/External") == (
        "Micron",
        "og:title",
    )


def test_an_og_description_opener_is_the_last_resort():
    page = _page(
        None, "At Micro Focus, we provide our customers with enterprise software."
    )
    assert board_name([], page, "microfocus/ACJobSite") == (
        "Micro Focus",
        "og:description",
    )


def test_a_description_about_the_reader_names_nothing():
    page = _page(None, "We are a global biopharma company with a special purpose.")
    assert board_name([], page, "gsk/gskcareers") == (None, "none")


def test_a_bare_page_label_title_is_not_a_name():
    assert board_name([], _page("Careers"), "acme/External") == (None, "none")
