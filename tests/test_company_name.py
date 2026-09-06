"""The Board's company name, read from its board page instead of standing in as the slug.

Every case here is a real title observed while sampling 30 live Boards per ATS
(`experiment/company-display-name/`), not an invented one — including the two that talked the
first draft of `from_title` out of a rule it had wrong.
"""

from __future__ import annotations

import pytest

from headstart.company_name import from_title, title_of


@pytest.mark.parametrize(
    ("ats", "title", "slug", "expected"),
    [
        # the four shapes, one observed title each
        ("ashby", "1Password Jobs", "1password", "1Password"),
        (
            "eightfold",
            "Careers at Lockheed Martin",
            "lockheedmartin.eightfold.ai",
            "Lockheed Martin",
        ),
        ("eightfold", "Sephora Careers", "sephora.eightfold.ai", "Sephora"),
        (
            "ripplehire",
            "Tata Steel Ltd Careers | Latest jobs at Tata Steel Ltd - Ripplehire.com",
            "tatasteel",
            "Tata Steel Ltd",
        ),
        ("lever", "Pickle Robot Company", "picklerobot", "Pickle Robot Company"),
        # entities decoded: the title really is served as "Canopy A&amp;D"
        ("lever", "Canopy A&amp;D", "canopy-ad", "Canopy A&D"),
    ],
)
def test_a_board_title_yields_the_company_name(ats, title, slug, expected):
    assert from_title(ats, title, slug) == expected


@pytest.mark.parametrize(
    ("ats", "title", "slug"),
    [
        # a wrapper the patterns do not model — half a slogan is worse than the slug
        (
            "eightfold",
            "Kraft Heinz Careers – Explore Careers. We're growing greatness.",
            "kraftheinz.eightfold.ai",
        ),
        (
            "successfactors",
            "Life@MOHH - people, culture, and values | MOHH",
            "careers.mohh.com.sg",
        ),
        # a hostname, written like one
        ("lever", "webfx.com", "webfx"),
        # exactly the slug: nothing gained
        ("lever", "cargo-partner", "cargo-partner"),
        ("ashby", "telli Jobs", "telli"),
        # an ATS with no evidence behind it has no patterns at all
        ("keka", "Careers at Red Baton", "redbaton"),
        ("workday", "Careers at Anything", "pwc"),
        # nothing to read
        ("lever", None, "acme"),
        ("lever", "", "acme"),
    ],
)
def test_an_unreadable_title_leaves_the_board_on_its_slug(ats, title, slug):
    assert from_title(ats, title, slug) is None


def test_capitalisation_alone_is_worth_taking():
    """The rule this pins talked the first draft out of its own purpose.

    An earlier `from_title` compared the title to the slug with case and punctuation stripped,
    so "Aida Jobs" -> "Aida" was rejected as "the same as the slug". That is the whole
    improvement: ashby scored 0 of 12 until the comparison was narrowed to an exact match.
    """
    assert from_title("ashby", "Aida Jobs", "aida") == "Aida"
    assert from_title("ashby", "HiringCafe Jobs", "hiring-cafe") == "HiringCafe"


def test_a_dotted_name_is_not_mistaken_for_a_hostname():
    """ "Character.AI" is a company; "webfx.com" is a hostname. Matching the hostname shape
    case-insensitively rejected the first, so the rule requires a lowercase string."""
    assert from_title("ashby", "Character.AI Jobs", "character") == "Character.AI"
    assert from_title("lever", "webfx.com", "webfx") is None


def test_title_of_reads_and_tidies_the_tag():
    assert title_of("<html><head><title>  Acme\n  Jobs </title></head>") == "Acme Jobs"
    assert title_of("<TITLE>Acme</TITLE>") == "Acme"
    assert title_of("<html><head></head></html>") is None
    assert title_of("<title>   </title>") is None
    assert title_of(None) is None
