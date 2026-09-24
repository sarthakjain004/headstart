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

from headstart import company_name
from headstart.company_name import (
    brand_first,
    curated,
    from_field,
    from_title,
    humanised,
    looks_like_slug,
    settled,
    title_cased,
    title_of,
)


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
        # taleo_enterprise's own two wrappers, both live: a double space around the pipe
        # (D.R. Horton) and a dash before "Careers" (Valero) — `_CAREERS_WRAPPER`'s two
        # shapes are already covered by the eightfold/keka cases above.
        (
            "taleo_enterprise",
            "Careers  |  D.R. Horton",
            "https://drhorton.taleo.net/careersection/2",
            "D.R. Horton",
        ),
        ("taleo_enterprise", "Valero - Careers", "https://valero.taleo.net", "Valero"),
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
        # a URL, which is not a name (lever served this one as a title, 2026-09-24)
        ("lever", "https://www.azuga.com/", "azuga"),
        ("lever", "www.webfx.com", "webfx"),
        # exactly the slug: nothing gained
        ("lever", "cargo-partner", "cargo-partner"),
        ("ashby", "telli Jobs", "telli"),
        # An ATS with no patterns resolves nothing — the central decision of ADR-0114. These
        # names are ordinary and clean: no separator, no label, no hostname, so nothing else in
        # `from_title` would refuse them and only the pattern gate can be what does. Without
        # such a row, deleting that gate left the whole suite green.
        ("recruitee", "Careers at Red Baton", "redbaton"),
        ("oracle", "Bachem", "sap"),
        ("workable", "Tata Motors", "tatamotors"),
        ("greenhouse", "Stripe", "stripe"),
        ("icims", "Careers at Anything", "pwc"),
        # nothing to read
        ("lever", None, "acme"),
        ("lever", "", "acme"),
        # exactly the slug once the wrapper is stripped: nothing gained
        ("taleo_enterprise", "Careers | acme", "acme"),
    ],
)
def test_an_unreadable_title_leaves_the_board_on_its_slug(ats, title, slug):
    assert from_title(ats, title, slug) is None


def test_taleo_enterprise_has_no_catch_all_unlike_lever():
    """A bare, unwrapped title is not trustworthy on this ATS (see the module docstring):
    both a real employer's own name and the vendor's default branding show up unwrapped,
    and nothing here can tell them apart — so, unlike lever, neither gets a pattern."""
    assert from_title("taleo_enterprise", "TTEC", "ttec") is None
    assert from_title("taleo_enterprise", "Oracle Taleo", "cfopitt") is None
    assert (
        from_title("taleo_enterprise", "Careers at Hospital Authority", "ha")
        == "Hospital Authority"
    )


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        # the name part ends where a wrapper begins: the refusal reads the name, not the rest
        ("SAM | Careers", "SAM"),
        ("Intelligent Waves Apply", "Intelligent Waves"),
        ("Karsun Solutions LLC Apply", "Karsun Solutions LLC"),
        # "company" is a word in legal names; only a title that *is* the word is a page label
        ("Factory Mutual Insurance Company", "Factory Mutual Insurance Company"),
        ("Company", None),
        # still refused: text naming a page rather than an employer
        ("Reyes Holdings Talent Community Apply", None),
        ("LLA Talent Community", None),
        ("Careers Home Apply", None),
        ("Home Apply", None),
    ],
)
def test_jibe_titles(title, expected):
    # client `/jobs` titles seen on the 2026-09-24 census
    assert from_title("jibe", title, "demo") == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Job Listings at Peraton", "Peraton"),
        (
            (
                "Find a Job - General Dynamics Mission Systems Job Listings at General "
                "Dynamics Mission Systems"
            ),
            "General Dynamics Mission Systems",
        ),
        ("Job Openings at TekSynap", "TekSynap"),
        ("Job Opportunities at Latham &amp; Watkins LLP", "Latham & Watkins LLP"),
        # a lowercase "the" is the template's; a capitalised one is the name's
        (
            "Job Listings at the Law School Admission Council",
            "Law School Admission Council",
        ),
        ("Job Listings at The Squires Group", "The Squires Group"),
        ("Offerte di lavoro presso Merlin Entertainments", None),
        ("Docusign Careers", None),
        ("Job Listings", None),
    ],
)
def test_icims_listing_titles(title, expected):
    # `/jobs/search?ss=1&in_iframe=1` titles on 52 Boards, 2026-09-24
    assert from_title("icims", title, "careers-demo.icims.com") == expected


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


def test_a_name_the_company_writes_as_a_domain_is_a_name():
    """ADR-0212 reversed the hostname guard: a startup whose brand is its domain states it.

    Gem titles itself "11x.ai Careers" and "agenta.ai Careers" (2026-09-16 sample), and the
    field ATSes carry "incident.io" and "tails.com". A URL is still refused, scheme or `www.`.
    """
    assert from_title("gem", "11x.ai Careers", "11x") == "11x.ai"
    assert from_title("ashby", "Sprout.ai Jobs", "sprout-ai") == "Sprout.ai"
    assert from_title("ashby", "Character.AI Jobs", "character") == "Character.AI"
    assert from_title("lever", "webfx.com", "webfx") == "webfx.com"
    assert from_title("lever", "https://www.azuga.com/", "azuga") is None
    assert from_title("lever", "www.acme.io", "acme") is None


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


@pytest.mark.parametrize(
    ("stated", "expected"),
    [
        # Each is a name a keka, zwayam or darwinbox Board states (2026-09-24 research census).
        ("IMPRONICS DIGITECH PRIVATE LIMITED", "Impronics Digitech Private Limited"),
        ("SS SUPPLY CHAIN SOLUTION PVT. LTD.", "SS Supply Chain Solution Pvt. Ltd."),
        ("TRINITY TOUCH PVT LTD", "Trinity Touch Pvt Ltd"),
        ("LEMON YELLOW LLP", "Lemon Yellow LLP"),
        ("NOVENTIQ HOLDINGS PLC", "Noventiq Holdings PLC"),
        ("BERKOWITS HAIR AND SKIN CLINIC", "Berkowits Hair and Skin Clinic"),
        ("THE HI-TECH ROBOTIC SYSTEMZ LIMITED", "The Hi-Tech Robotic Systemz Limited"),
        (
            "EQUIPPED ANALYTICAL INTELLIGENCE (INDIA) LLP",
            "Equipped Analytical Intelligence (India) LLP",
        ),
        ("IIFL FINANCE LIMITED", "IIFL Finance Limited"),
        ("INDUSIND BANK LTD", "Indusind Bank Ltd"),
        ("SI HOUSE OF MODELS PRIVATE LIMITED", "SI House of Models Private Limited"),
        # constructed: a possessive keeps its tail lowercase
        ("MACY'S RETAIL HOLDINGS", "Macy's Retail Holdings"),
        # kept: no word longer than four letters, a single word, or not all caps
        ("BIG OH TECH", "BIG OH TECH"),
        ("HCL", "HCL"),
        ("CRISIL", "CRISIL"),
        ("EPAM", "EPAM"),
        ("KPMG", "KPMG"),
        ("Tata Consultancy Services", "Tata Consultancy Services"),
        ("NVIDIA Corporation", "NVIDIA Corporation"),
        # a caseless script carries no capitals to lower: pyjamahr served this title
        ("【QR】クオリティー エンジニア", "【QR】クオリティー エンジニア"),
    ],
)
def test_an_all_caps_legal_name_is_title_cased(stated, expected):
    assert title_cased(stated) == expected


def test_title_casing_reaches_both_name_paths():
    assert (
        from_title("keka", "Careers at IMPRONICS DIGITECH PRIVATE LIMITED", "impronics")
        == "Impronics Digitech Private Limited"
    )
    assert from_field("zwayam", "IIFL FINANCE LIMITED") == "IIFL Finance Limited"


def test_a_stated_field_name_is_taken_as_the_company_typed_it():
    """ADR-0212: a field name is not refused for equalling the slug or being lowercase.

    Greenhouse's `commercetools` and Teamtailor's `sunday` state exactly their slug, and Workable,
    JazzHR and Breezy state names written as domains; all are what the company typed.
    """
    assert from_field("greenhouse", "commercetools") == "commercetools"
    assert from_field("teamtailor", "sunday") == "sunday"
    assert from_field("jazzhr", "h2o.ai") == "h2o.ai"
    assert from_field("greenhouse", "impact.com") == "impact.com"
    assert from_field("greenhouse", " VML/WPP Enterprise Solutions  ") == (
        "VML/WPP Enterprise Solutions"
    )
    assert from_field("pinpoint", "Jobs &amp; Co") == "Jobs & Co"


def test_a_field_that_states_nothing_usable_is_none():
    """So ``from_field(...) or fallback`` falls back. ``value or fallback`` did not on padding:
    rippling's `agora` states "   ", which is truthy, and a Job then carried an empty company."""
    assert from_field("rippling", "   ") is None
    assert from_field("rippling", None) is None
    assert from_field("jazzhr", "www.wingbrace.com") is None
    assert from_field("lever", "https://www.azuga.com/") is None
    # the ATS's own name, where that ATS has vendor aliases
    assert from_field("adp", "ADP") is None


def test_the_brand_a_page_states_outranks_a_legal_name():
    assert brand_first("Klipboard", "KERRIDGE COMMERCIAL SYSTEMS CORP") == "Klipboard"
    assert brand_first(None, "Kerridge Commercial Systems Corp") == (
        "Kerridge Commercial Systems Corp"
    )
    assert brand_first(None, None) is None


def test_the_curated_map_names_boards_that_state_nothing():
    """Seeded from pages read by hand (`config/company_names.csv`'s evidence column)."""
    assert curated("cornerstone:gmv") == "GMV"
    assert curated("cornerstone:aswatsonph") == "Watsons"
    assert curated("cornerstone:uis") == "University of Illinois at Springfield"
    # a ledger's casing and a fresh board_key need not agree (ADR-0049)
    assert curated("WORKDAY:bah/bah_jobs") == "Booz Allen Hamilton"
    assert curated("cornerstone:not-curated") is None


@pytest.mark.parametrize(
    ("board_key", "expected"),
    [
        ("workday:nvidia/NVIDIAExternalCareerSite", "Nvidia"),
        ("workday:hpe/ACJobSite", "HPE"),
        ("icims:careers-gd-ais.icims.com", "GD AIS"),
        ("icims:careers-peraton.icims.com", "Peraton"),
        ("zwayam:careers.persistent.com", "Persistent"),
        ("eightfold:careers.qualcomm.com", "Qualcomm"),
        ("successfactors:careers.hcltech.com", "Hcltech"),
        ("phenom:jobs.baesystems.com", "Baesystems"),
        ("personio:q-ant-gmbh", "Q ANT GmbH"),
        ("pyjamahr:careers-at-aifa-labs", "Aifa Labs"),
        ("rippling:apexanalytix-careers", "Apexanalytix"),
        ("freshteam:codvo-team", "Codvo"),
        ("pyjamahr:werecruiters-1", "Werecruiters"),
        ("teamtailor:evrocab-1692891239", "Evrocab"),
        ("smartrecruiters:TecTammina", "TecTammina"),
        (
            "taleo_be:https://phg.tbe.taleo.net/phg01/ats/careers/v2/searchResults?org=GATEWAYVENT&cws=42",
            "Gatewayvent",
        ),
        ("taleo_enterprise:https://hdr.taleo.net/careersection/austin_tx", "HDR"),
        # only a code: no name at all rather than one (ADR-0212)
        ("breezy:1001", None),
        ("oracle:eeho.fa.us2.oraclecloud.com", None),
        ("oracle:utulsa-ibvjjb.fa.ocs.oraclecloud.com", None),
        ("adp:37053934-22c6-4362-aa6a-1fee41c0cca3/19000101_000001", None),
        (
            "taleo_be:https://lde.tbe.taleo.net/lde01/ats/careers/v2/searchResults?org=G94W9A&cws=37",
            None,
        ),
        # a digit-led slug is cased too, never served verbatim
        ("lever:1password", "1Password"),
        (
            "taleo_be:https://phf.tbe.taleo.net/phf01/ats/careers/v2/searchResults?org=COVESTIC2&cws=40",
            "Covestic2",
        ),
        ("clearcompany:good2grow", "Good2grow"),
    ],
)
def test_a_board_with_no_stated_name_is_shown_under_its_humanised_tenant(
    board_key, expected
):
    """Never the raw slug (ADR-0212). Each key is a residue Board from the 2026-09-24 served
    table, bar `careers-gd-ais`'s neighbours, which are the shapes the tidy rules exist for."""
    assert humanised(board_key) == expected


def test_settled_keeps_a_stated_name_and_humanises_an_identifier(monkeypatch):
    monkeypatch.setattr(company_name, "curated_names", dict)
    nvidia = "workday:nvidia/NVIDIAExternalCareerSite"
    ledger = "nvidia.wd5.myworkdayjobs.com/nvidiaexternalcareersite"
    # nothing ran: the ledger's spelling is an identifier
    assert settled(ledger, ledger, nvidia) == "Nvidia"
    # a source stated a name during the fetch, even one equal to a slug piece
    assert settled("NVIDIA", ledger, nvidia) == "NVIDIA"
    assert settled("commercetools", "x", "greenhouse:commercetools") == "commercetools"
    # the curated feed's own name, or a declared `COMPANY`, is a name already
    assert settled("1Password", "1Password", "ashby:1password") == "1Password"
    # SuccessFactors' constructor leaves a host label, SmartRecruiters the slug itself
    assert settled("hcltech", "hcltech", "successfactors:careers.hcltech.com") == (
        "Hcltech"
    )
    assert settled("TecTammina", "TecTammina", "smartrecruiters:TecTammina") == (
        "TecTammina"
    )
    # padding is no name at all
    assert settled("   ", "   ", "bamboohr:cintel") == "Cintel"


def test_a_curated_name_overrides_every_source():
    assert settled("Some Title", "gmv", "cornerstone:gmv") == "GMV"


@pytest.mark.parametrize(
    ("ats", "stated", "slug", "expected"),
    [
        # Each observed live on an affected Board, 2026-09-24.
        ("freshteam", "Careers - KreditBee", "krazybee", "KreditBee"),
        ("freshteam", "Careers - Jobconversion, LLC", "abnhire", "Jobconversion, LLC"),
        (
            "personio",
            "Jobs at 7Learnings GmbH",
            "7learnings.jobs.personio.de",
            "7Learnings GmbH",
        ),
        (
            "trakstar",
            (
                "Planate Management Group jobs | Planate Management Group openings | "
                "Planate Management Group careers"
            ),
            "planate",
            "Planate Management Group",
        ),
    ],
)
def test_a_field_or_page_the_board_states_yields_its_name(ats, stated, slug, expected):
    assert from_title(ats, stated, slug) == expected


@pytest.mark.parametrize(
    ("ats", "stated", "slug"),
    [
        # freshteam's <title>, which is "Careers" on every Board: not the og:title shape
        ("freshteam", "Careers", "hotelogix"),
        # an untranslated personio page: only the English wrapper is read
        ("personio", "Jobs bei 9elements", "9elements.jobs.personio.de"),
        # a title-less personio page's wrapper with nothing in it
        ("personio", "Jobs at", "aarktech.jobs.personio.com"),
        # Trakstar Hire's pre-rebrand name on a trial tenant (`trakstar:trial101`)
        (
            "trakstar",
            "Recruiterbox jobs | Recruiterbox openings | Recruiterbox careers",
            "trial101",
        ),
        # a trakstar name that carries a separator of its own (`trakstar:elliottlewis`)
        (
            "trakstar",
            "Elliott-Lewis | Sautter Crane | AA Duckett jobs | Elliott-Lewis | …",
            "elliottlewis",
        ),
    ],
)
def test_a_page_or_field_with_no_employer_leaves_the_slug(ats, stated, slug):
    assert from_title(ats, stated, slug) is None


@pytest.mark.parametrize(
    ("ats", "stated", "expected"),
    [
        # Each a field observed live on an affected Board, 2026-09-24.
        ("bamboohr", "Cintel Inc", "Cintel Inc"),
        ("bamboohr", "Falls Technology ", "Falls Technology"),
        (
            "cornerstone",
            "MACOM Technology Solutions Holdings, Inc.",
            "MACOM Technology Solutions Holdings, Inc.",
        ),
        ("darwinbox", "Zydus Hospitals Group", "Zydus Hospitals Group"),
        ("zwayam", "Persistent Systems", "Persistent Systems"),
        # A client record names a unit of its parent with " - ": a field, not a slogan with a
        # tail to cut, so `from_field` keeps it where `from_title` would refuse it.
        (
            "adp",
            "Gold Medal Environmental - Apple Valley Waste Inc",
            "Gold Medal Environmental - Apple Valley Waste Inc",
        ),
        (
            "adp_recruiting",
            "Gold Medal Environmental - Apple Valley Waste Inc",
            "Gold Medal Environmental - Apple Valley Waste Inc",
        ),
        ("ripplehire", "7 - Eleven", "7 - Eleven"),
    ],
)
def test_a_field_source_names_its_board(ats, stated, expected):
    assert from_field(ats, stated) == expected


@pytest.mark.parametrize(
    ("ats", "stated"),
    [
        ("bamboohr", "BambooHR"),
        ("cornerstone", "Cornerstone OnDemand"),
        ("darwinbox", "Darwinbox"),
        # zwayam's own test tenants, and the openings.co template's placeholder
        ("zwayam", "Hiremate Test 1"),
        ("zwayam", "SST Test"),
        ("zwayam", "Talent SST"),
        ("zwayam", "TechCorp"),
        ("ripplehire", "RippleHire"),
    ],
)
def test_a_field_source_refuses_its_vendor_and_test_tenants(ats, stated):
    assert from_field(ats, stated) is None


def test_the_title_path_still_cuts_a_spaced_hyphen():
    name = "Gold Medal Environmental - Apple Valley Waste Inc"
    assert from_title("lever", name, "goldmedal") is None
    assert from_title("ripplehire", "7 - Eleven Careers | Latest jobs", "x") is None


@pytest.mark.parametrize(
    ("ats", "stated", "slug", "expected"),
    [
        # keka's portal `name`, as tenants typed it (2026-09-24 census)
        ("keka", "Zypp Electric", "zypp", "Zypp Electric"),
        ("keka", "Careers at WeDoGood", "wedogood", "WeDoGood"),
        (
            "keka",
            "Career at Coozmoo Digital Solutions",
            "coozmoo",
            "Coozmoo Digital Solutions",
        ),
        ("keka", "Jobs at Olyv", "smartcoin", "Olyv"),
        ("keka", "SecPod Careers", "secpod", "SecPod"),
        # zoho's careers page titles
        (
            "zoho",
            "Jobs at MasonBlue Technologies, LLC",
            "masonbluesecurity",
            "MasonBlue Technologies, LLC",
        ),
        ("zoho", "Careers @ thinkbridge", "thinkbridgeinc", "thinkbridge"),
        (
            "zoho",
            "Careers at Wedded Wonderland | Join Our Team",
            "wedded.wonderland",
            "Wedded Wonderland",
        ),
        ("zoho", "Jobs | Aiones", "aiones", "Aiones"),
        ("zoho", "Rumzer Careers", "rumzer.com", "Rumzer"),
        (
            "zoho",
            "Jobs by Vasudha Business Solutions",
            "vbs",
            "Vasudha Business Solutions",
        ),
    ],
)
def test_an_india_ats_names_its_board(ats, stated, slug, expected):
    assert from_title(ats, stated, slug) == expected


@pytest.mark.parametrize(
    ("ats", "stated", "slug"),
    [
        ("keka", "keka", "dataction"),  # the vendor's name where the tenant's belongs
        ("keka", "Serviqual - Jobs", "serviqual"),
        # a job family, not an employer: why zoho has no trailing "{Name} Jobs" pattern
        ("zoho", "Project Management Jobs", "eiger"),
        ("zoho", "Jobs at Careers", "flydocs"),
        ("zoho", "Jobs at agrocommercialbyliotis", "agrocommercialbyliotis"),
    ],
)
def test_an_india_ats_refuses_a_non_name(ats, stated, slug):
    assert from_title(ats, stated, slug) is None


@pytest.mark.parametrize(
    ("ats", "title", "expected"),
    [
        # Each served by an affected Board, 2026-09-24.
        ("gem", "Bluesky Jobs", "Bluesky"),
        ("gem", "Jobs @ Formal", "Formal"),
        ("gem", "Jobs at Nerdery", "Nerdery"),
        ("gem", "Opportunities @ Haulvana", "Haulvana"),
        ("gem", "SynthBee Opportunities", "SynthBee"),
        ("gem", "Align Builders Career Opportunities", "Align Builders"),
        ("gem", "Shorr Packaging Open Positions", "Shorr Packaging"),
        ("gem", "11x.ai Careers", "11x.ai"),
        ("jobvite", "Carrières Buckman", "Buckman"),
        ("jobvite", "Provisur Technologies Karrieren", "Provisur Technologies"),
        ("jobvite", "Provisur Technologies Carrières", "Provisur Technologies"),
        ("jobvite", "Samtec, Inc carreras", "Samtec, Inc"),
        ("jobvite", "Samtec, Inc 职业", "Samtec, Inc"),
    ],
)
def test_a_title_ats_reads_its_other_wrappers(ats, title, expected):
    assert from_title(ats, title, "x") == expected


@pytest.mark.parametrize(
    ("ats", "title"),
    [
        ("gem", "Gem Jobs"),
        ("gem", "Careers"),
        ("gem", "Join Our Mission"),
        ("gem", "Open Jobs"),
        ("gem", "Current Opportunities"),
        ("jobvite", "the D Las Vegas cares"),
    ],
)
def test_a_title_ats_refuses_a_page_that_names_no_employer(ats, title):
    assert from_title(ats, title, "x") is None


def test_title_fallback_sources_take_the_field_path():
    assert from_field("ashby:graphql", "Ashby") == "Ashby"
    assert from_field("eightfold", "Eightfold") is None
    assert from_field("pinpoint", "Pinpoint") is None
    assert from_field("lever", "Veeva Systems") == "Veeva Systems"


def test_agreed_name_is_the_most_stated_name():
    from headstart.company_name import agreed_name

    assert (
        agreed_name(["FM", None, "FM", "", "Factory Mutual Insurance Company"]) == "FM"
    )
    assert agreed_name([None, " "]) is None


def test_agreed_name_refuses_a_board_whose_postings_disagree():
    # reyesholdings (jibe, 2026-09-24): its subsidiaries, the largest on 43% of rows
    from headstart.company_name import agreed_name

    names = (
        ["Reyes Beverage Group"] * 279
        + ["Reyes Coca-Cola Bottling"] * 182
        + ["Martin Brower"] * 137
    )
    assert agreed_name(names, 0.85) is None
    assert agreed_name(["FM"] * 9 + ["Factory Mutual"], 0.85) == "FM"
