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


@pytest.mark.parametrize(
    ("entity", "cleaned"),
    [
        ("001_BCBSA Blue Cross and Blue Shield Association",
         "BCBSA Blue Cross and Blue Shield Association"),
        ("800_ilani Cowlitz Tribal Gaming Authority",
         "ilani Cowlitz Tribal Gaming Authority"),
    ],
)  # fmt: skip
def test_clean_strips_a_code_joined_to_the_name_by_an_underscore(entity, cleaned):
    """Both values as bcbsa and cowlitz served them live on 2026-09-29; the cache had kept
    their codes as "001 Bcbsa" and "800 ilani"."""
    assert clean(entity) == cleaned


def test_a_run_never_starts_on_a_joiner_or_a_bare_number():
    """Woodward's values are code lists ("01 & 04"); the cache served "& 04 Woodward"."""
    entities = [
        "47 Woodward Aken GmbH",
        "11 & A1 Woodward HRT, Inc.",
        "01 & 04 Woodward, Inc.",
        "01 & 04 Woodward, Inc.",
        "01 & 04 Woodward, Inc.",
        "86 Woodward Canada Inc.",
    ]
    page = _page(
        None, "What does it mean to be part of Woodward? It means contributing."
    )
    assert board_name(entities, page, "woodward/woodward") == (
        "Woodward",
        "hiringOrganization",
    )


def test_a_code_hyphened_onto_the_name_is_skipped():
    """Zoetis's "6J6 - Zoetis LLC" is not a `_HYPHEN_CODE`; the cache served "- Zoetis"."""
    entities = [
        "110 - Zoetis US LLC",
        "6J2 - Zoetis Services LLC",
        "6J6 - Zoetis LLC",
        "6J6 - Zoetis LLC",
        "6J6 - Zoetis LLC",
    ]
    page = _page(
        None, "Join Zoetis – and build your career. Why Zoetis Zoetis has more"
    )
    assert board_name(entities, page, "zoetis/broadbean_external") == (
        "Zoetis",
        "hiringOrganization",
    )


def test_a_double_escaped_og_tag_still_vouches():
    """Core & Main's page writes ``Core &amp;amp; Main``; read once, the vote fell to "& MAIN"."""
    page = _page(None, "Based in St. Louis, Core &amp;amp; Main is a leader in water.")
    assert board_name(["CORE & MAIN LP"] * 8, page, "coreandmain/coreandmain") == (
        "Core & Main",
        "hiringOrganization",
    )


def test_a_legal_form_that_ends_the_brand_is_kept():
    """Cohen & Co's page, read once unescaped, names "Cohen & Co"; dropping "Co" as a legal
    form would serve "Cohen &"."""
    page = _page(
        "Careers",
        "Ask your connection at Cohen &amp;amp; Co about our referral process!",
    )
    entities = ["LE0008 Cohen & Co Advisory, LLC"] * 8
    assert board_name(entities, page, "cohenco/CC") == (
        "Cohen & Co",
        "hiringOrganization",
    )


def test_a_wrapped_og_title_drops_the_separator_before_careers():
    """lsu's page titles itself "Louisiana State University - Careers"; the cache served the
    name with its " -" (2026-09-24)."""
    page = _page("Louisiana State University - Careers")
    assert board_name([], page, "lsu/lsu") == ("Louisiana State University", "og:title")


# Each rule `_checked_run` gained in #862, pinned on its own: the mixed-entity cases above still
# pass with any one of them removed, because the Board's other postings out-vote the bad run.


@pytest.mark.parametrize(
    ("entity", "board", "expected"),
    [
        # "01 & 04" is two codes and a "&" between them: skipping only codes left "&" at the front,
        # and a run starting on "04" or "&" was vouched for by the Board's own letters
        ("01 & 04 Woodward, Inc.", "woodward/woodward", "Woodward"),
        # "6J6" is a code word and "-" joins it to the name
        ("6J6 - Zoetis LLC", "zoetis/broadbean_external", "Zoetis"),
    ],
)
def test_a_board_whose_every_posting_leads_with_codes_and_joiners(
    entity, board, expected
):
    page = _page(None, f"What does it mean to be part of {expected}? It means more.")
    assert board_name([entity] * 8, page, board) == (expected, "hiringOrganization")


@pytest.mark.parametrize(
    ("entity", "board", "prose", "expected"),
    [
        # each value as its Board states it on every posting read live on 2026-09-29; the cache
        # had served the name with the joiner after it
        (
            "Chukchansi Gold - Resort & Casino",
            "chukchansigold/cgrccareers",
            "Chukchansi Gold Resort & Casino invites guests",
            "Chukchansi Gold",
        ),
        (
            "100 DAC Group / Canada Ltd.",
            "dacgroup/EXT",
            "DAC is a leading international media agency",
            "DAC Group",
        ),
        (
            "CCB iHeartMedia + Entertainment, Inc. | MPG",
            "iheartmedia/External_iHM",
            "future roles at iHeartMedia, we invite you",
            "iHeartMedia",
        ),
    ],
)
def test_a_run_never_ends_on_a_joiner(entity, board, prose, expected):
    assert board_name([entity] * 8, _page(None, prose), board) == (
        expected,
        "hiringOrganization",
    )
