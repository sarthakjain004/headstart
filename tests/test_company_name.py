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
        # a title no pattern matches: rejected before any rule below runs
        (
            "eightfold",
            "Kraft Heinz Careers – Explore Careers. We're growing greatness.",
            "kraftheinz.eightfold.ai",
        ),
        # an ATS with no measured shape has no patterns at all, so nothing is attempted
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


def test_a_separator_that_survives_the_wrapper_is_refused():
    """Lever's pattern matches anything, so a lever title is where `_SEPARATORS` actually bites.

    Mutation-tested: with the separator check disabled the rest of the suite stayed green, so
    every case that reached it was already being rejected by something else.
    """
    assert from_title("lever", "Acme | Careers", "acme") is None
    assert from_title("lever", "Acme — Join us", "acme") is None
    assert from_title("lever", "Acme - Careers", "acme") is None
    assert from_title("lever", "Acme :: Jobs", "acme") is None
    # ...while an ordinary name with punctuation is untouched
    assert (
        from_title("lever", "Hutker Architects, Inc.", "hutker")
        == "Hutker Architects, Inc."
    )


def test_a_title_long_enough_to_be_prose_is_refused():
    """`_MAX_LEN` was untested: raising it to 500 left the suite green.

    The real case is a lever board whose title is a sentence — observed:
    "Succession Planning for Railroads Investing in the Next Generation LLC".
    """
    sentence = "Succession Planning for Railroads Investing in the Next Generation LLC"
    assert len(sentence) > 60
    assert from_title("lever", sentence, "springrecruits") is None
    # a long-but-plausible name is still accepted, so this is a length rule and not a word count
    assert from_title("lever", "Financial Software and Systems Ltd", "fss")


def test_a_lowercase_name_with_a_tld_is_read_as_a_hostname():
    """The guard the hostname rule really needs.

    `Character.AI` is spared by the regex being case-sensitive, not by this test — deleting the
    lowercase check left the suite green. What it actually decides is a mixed-case name with a
    lowercase TLD, which a live ashby board serves: "Sprout.ai Jobs".
    """
    assert from_title("ashby", "Sprout.ai Jobs", "sprout-ai") == "Sprout.ai"
    assert from_title("lever", "webfx.com", "webfx") is None
    assert from_title("lever", "acme.io", "acme") is None


def test_the_ats_vendors_own_name_is_never_the_company():
    """A Board whose title names its *vendor* is a demo or a parked tenant.

    `ripplehire:trampolinetech` really does serve "RippleHire Careers | Latest jobs at RippleHire",
    which shipped as the employer name until this rule existed — the same failure ADR-0034 already
    blocklists Boards for, reaching us through a title instead.
    """
    title = "RippleHire Careers | Latest jobs at RippleHire - Ripplehire.com"
    assert from_title("ripplehire", title, "trampolinetech") is None
    assert from_title("ashby", "Ashby Jobs", "ashby-demo") is None
    # a real employer that merely contains a vendor-ish word is unaffected
    assert (
        from_title("ashby", "Lever Industries Jobs", "lever-ind") == "Lever Industries"
    )


def test_a_ledger_name_that_is_itself_a_slug_is_not_a_real_name():
    """`looks_like_slug` is what stops `resolve_company` skipping the rows it exists to fix: the
    liveness ledger holds "wipro" and "gamuda", and Workday's holds "dick-s-sporting-goods"."""
    from headstart.company_name import looks_like_slug

    for slug_like in (
        "wipro",
        "gamuda",
        "citi",
        "dick-s-sporting-goods",
        "jobs.vodafone.com",
        "",
        None,
    ):
        assert looks_like_slug(slug_like), slug_like
    for real in ("Tata Steel Ltd", "1Password", "Character.AI", "NVIDIA Corporation"):
        assert not looks_like_slug(real), real
