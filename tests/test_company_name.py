"""The Board's company name, read from its board page instead of standing in as the slug.

Every case that *drives* a rule is a real title observed while sampling live Boards
(`experiment/company-display-name/`, gitignored) — including the two that talked the first draft
of `from_title` out of a rule it had wrong. Sample sizes differ by ATS and are recorded in
`headstart.company_name`, which is the single source for them — the first pass was 30 Boards each,
and every row has since been re-measured larger.

Some *counter*-cases are invented ("Acme | Careers", "acme.io", "Lever Industries Jobs"). That is
deliberate and the distinction matters: a rule must fire on a shape someone really serves, but it
may be pinned against any shape it must not eat.
"""

from __future__ import annotations

import pytest

from headstart.company_name import from_title, looks_like_slug, title_of


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
        # An ATS with no patterns resolves nothing — the central decision of ADR-0112. These
        # names are ordinary and clean: no separator, no label, no hostname, so nothing else in
        # `from_title` would refuse them and only the pattern gate can be what does. Without
        # such a row, deleting that gate left the whole suite green.
        ("freshteam", "Careers at Red Baton", "redbaton"),
        ("workday", "Bachem", "sap"),
        ("darwinbox", "Tata Motors", "tatamotors"),
        ("greenhouse", "Stripe", "stripe"),
        ("workday", "Careers at Anything", "pwc"),
        # nothing to read
        ("lever", None, "acme"),
        ("lever", "", "acme"),
    ],
)
def test_an_unreadable_title_leaves_the_board_on_its_slug(ats, title, slug):
    assert from_title(ats, title, slug) is None


def test_keka_reads_the_wrapper_it_shares_with_eightfold():
    """Both titles are live keka boards, and neither shape was covered by any test.

    Keka is the ATS with the most to gain — all 819 of its hiring Boards serve a slug today —
    and deleting its patterns left the whole suite green until this existed.
    """
    assert (
        from_title("keka", "Careers at Skylark Drones", "skylarkdrones")
        == "Skylark Drones"
    )
    assert from_title("keka", "Entropik Careers", "entropik") == "Entropik"
    assert from_title("keka", "", "minfy") is None


def test_capitalisation_alone_is_worth_taking():
    """The rule this pins talked the first draft out of its own purpose.

    An earlier `from_title` compared the title to the slug with case and punctuation stripped,
    so "Aida Jobs" -> "Aida" was rejected as "the same as the slug". That is the whole
    improvement: ashby scored 0 of 12 until the comparison was narrowed to an exact match.
    """
    assert from_title("ashby", "Aida Jobs", "aida") == "Aida"
    assert from_title("ashby", "HiringCafe Jobs", "hiring-cafe") == "HiringCafe"


def test_a_board_that_calls_itself_a_demo_is_refused():
    """A demo tenant ADR-0034 has not caught yet often admits itself in its title.

    It cannot catch one that titles itself after the company it imitates — `tenant1-mph` served
    "Mphasis" — which is why that Board went to the blocklist instead.
    """
    assert (
        from_title("ripplehire", "ITC Infotech Demo Careers | x", "itcinfotech") is None
    )
    assert from_title("ripplehire", "Your Company Careers | x", "prodtest") is None
    # a trailing "Sandbox" — dropped once as "never observed", then found on two live ashby
    # Boards, one of which was serving three template postings
    assert from_title("ashby", "Kraken Sandbox Jobs", "krakensandbox") is None
    assert from_title("ashby", "Bento Setup Sandbox Jobs", "bento") is None
    # The marker must be *trailing*: dropping the rule's `$` refused this, and dropping its
    # leading `\s` refused any name whose final syllable merely ends in the marker.
    assert (
        from_title("ashby", "Acme Demo Solutions Jobs", "acme") == "Acme Demo Solutions"
    )
    assert from_title("ashby", "Videmo Jobs", "videmo") == "Videmo"
    assert (
        from_title("ripplehire", "Tata Steel Ltd Careers | x", "tatasteel")
        == "Tata Steel Ltd"
    )


def test_a_real_company_is_not_mistaken_for_a_placeholder():
    """The marker has to be trailing. "Sandbox VR" and "Test Rite Group" are real employers, and
    a word-boundary rule matching those words anywhere refused both."""
    assert from_title("ashby", "Sandbox VR Jobs", "sandboxvr") == "Sandbox VR"
    assert from_title("lever", "Test Rite Group", "testrite") == "Test Rite Group"


def test_a_padded_slug_is_still_a_slug():
    """`looks_like_slug` judges the stripped string, not two different ones."""
    assert looks_like_slug(" wipro ")
    assert not looks_like_slug(" Tata Steel ")
    # a ledger name that is only padding is no more a name than an empty one
    assert looks_like_slug("   ")


def test_the_length_cap_is_pinned_at_its_boundary():
    """60 and 61 characters, either side of `_MAX_LEN`.

    The other length test straddles the cap with 34 and 70, which bounds the value without
    pinning it: `>` could become `>=`, or the constant could move by several, with nothing red.
    """
    sixty = "A" * 60
    assert from_title("lever", sixty, "x") == sixty
    assert from_title("lever", "A" * 61, "x") is None


def test_a_placeholder_must_be_the_whole_name_not_its_tail():
    r"""`^your\s+company` is anchored at the front, and nothing pinned that.

    A constructed counter-case rather than an observed one: the rule must not fire on a name that
    merely *ends* with the phrase, and no live Board serves such a title to test against.
    """
    assert from_title("lever", "Acme Is Your Company", "acme") == "Acme Is Your Company"
    assert from_title("lever", "Your Company", "x") is None


def test_title_of_reads_and_tidies_the_tag():
    assert title_of("<html><head><title>  Acme\n  Jobs </title></head>") == "Acme Jobs"
    # markup *inside* the tag is stripped, which is what "tags stripped" in the docstring means
    assert title_of("<title>Acme <b>Corp</b></title>") == "Acme Corp"
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


def test_a_page_label_is_not_a_company_name():
    """A title that wore the wrapper twice leaves a page label behind after one strip.

    Three shapes, live, found one at a time — each after the previous fix had shipped. Trailing:
    `lever:destinationknot` serves "Destination Careers", reachable because lever's pattern
    matches anything. Leading: `keka:enpro` serves "Careers at Careers at Enpro Industries" — the
    pattern strips one wrapper, and while the rule was anchored to the tail only, the survivor
    "Careers at Enpro Industries" was served to users as the employer. And the whole string:
    `lever:schmidt-entities` serves "jobs", which reached 16 real Jobs as their company. These are
    the three shapes *observed*, not an exhaustive set — a medial token and other leading
    phrasings pass, and are left alone until one is seen live. A single-wrapper title must still
    resolve, so this must not fire on the ordinary case.
    """
    assert from_title("keka", "Careers at Careers at Enpro Industries", "enpro") is None
    assert from_title("lever", "jobs", "schmidt-entities") is None
    assert from_title("eightfold", "Careers at Foo Careers", "foo") is None
    assert from_title("lever", "Destination Careers", "destinationknot") is None
    assert from_title("eightfold", "Sephora Careers", "sephora") == "Sephora"
    # anchored to the ends, so a real name that merely contains the word survives
    assert from_title("lever", "Jobsoid", "jobsoid") == "Jobsoid"
    assert from_title("lever", "Careers24 Group", "careers24") == "Careers24 Group"
    assert from_title("keka", "Entropik Careers", "entropik") == "Entropik"


def test_a_lowercase_name_with_a_tld_is_read_as_a_hostname():
    """The guard the hostname rule really needs.

    `Character.AI` is spared by the regex being case-sensitive, not by this test — deleting the
    lowercase check left the suite green. What it actually decides is a mixed-case name with a
    lowercase TLD, which a live ashby board serves: "Sprout.ai Jobs".
    """
    assert from_title("ashby", "Sprout.ai Jobs", "sprout-ai") == "Sprout.ai"
    assert from_title("ashby", "Character.AI Jobs", "character") == "Character.AI"
    assert from_title("lever", "webfx.com", "webfx") is None
    assert from_title("lever", "acme.io", "acme") is None


def test_a_vendor_name_is_refused_only_on_that_vendors_own_boards():
    """A Board whose title names its *own* platform has fallen back to that platform's branding.

    `ripplehire:trampolinetech` really does serve "RippleHire Careers | Latest jobs at RippleHire"
    (live, 15 jobs, not in `EXCLUDED_BOARDS`), which shipped as the employer until this rule
    existed — the failure ADR-0034 blocklists Boards for, reaching us through a title instead.

    The second assert is the one that pins *keying on the ATS*: "Ashby" on a **lever** Board is a
    company, not a fallback, so a rule refusing every vendor name everywhere would wrongly drop
    it. An earlier version used `lever:freshworks` — a real employer — but freshworks is in no
    alias set at all, so it discriminated nothing and the flat-set defect survived it.
    """
    title = "RippleHire Careers | Latest jobs at RippleHire - Ripplehire.com"
    assert from_title("ripplehire", title, "trampolinetech") is None
    assert from_title("ashby", "Ashby Jobs", "ashby-demo") is None
    assert from_title("lever", "Ashby", "ashby-co") == "Ashby"
    # a real employer that merely contains a vendor-ish word is unaffected
    assert (
        from_title("ashby", "Lever Industries Jobs", "lever-ind") == "Lever Industries"
    )


def test_a_ledger_name_that_is_itself_a_slug_is_not_a_real_name():
    """`looks_like_slug` is what stops `resolve_company` skipping the rows it exists to fix: the
    liveness ledger holds "wipro" and "gamuda", and Workday's holds "dick-s-sporting-goods"."""
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
