"""The company name a Board is served under: one it states, a curated one, or its humanised tenant.

`BaseScraper.__init__` does ``self.company = company or slug``, so a Board whose ledger row
carries no name serves its **slug** as the company. Measured on the served table 2026-09-07:
150,626 of 318,003 rows are *literally* the slug (47.4%), and **186,798 (58.7%) are slug-shaped**
once the Boards whose ledger "name" is itself an identifier are counted — the ledger holds "wipro"
and "gamuda", Workday's holds "citi" and "dick-s-sporting-goods". Users see "1password",
"jobs.vodafone.com", "nttltd" where a company name belongs. The wider figure is the honest one.

Eight ATSes put the real name in their board page's ``<title>``, each wrapped differently, and one
request per Board recovers it. Which eight is a measurement, not a guess: live Boards were sampled
per ATS (`experiment/company-display-name/`, gitignored), and only those whose wrapper is uniform
enough to strip safely are here. Sample sizes differ on purpose: the first pass was 30 Boards per
ATS, and each row was re-measured larger wherever 30 proved too few to trust. Lever needed it most
— two 30-Board samples disagreed (25 and 21) before 400 settled it near 88% — and keka's row is a
full census rather than a sample.

================  =================================================  =================
ATS               title shape                                        yields a name
================  =================================================  =================
ashby             ``{Name} Jobs``                                    ~92% (n=120)
eightfold         ``Careers at {Name}`` / ``{Name} Careers``         ~93% (n=100)
ripplehire        ``{Name} Careers | Latest jobs at …``              ~96% (all 51)
lever             ``{Name}`` — no wrapper at all                     ~88% (352/400)
taleo_enterprise  four ``Careers``-wrappers (see below)              20% (30/150)
gem               ``{Name} Careers`` and five more (see its pattern)  63% (36/57)
jobvite           ``{Name} Careers``, and its localized forms        424 of 434
phenom            ``Careers``-wrappers ending at ``|`` or ``:``       11 of 16 boards
pinpoint          ``Jobs at {Name} | {Name} Careers``                 38 of 40
================  =================================================  =================

**gem** is the lowest-yield row of the wrapper-matching ATSes, and the gap between "matches the
wrapper" (~95%) and "yields a name" (63%) was almost entirely a hostname guard, not a bad pattern:
many Gem tenants are early-stage startups whose brand IS their domain (``agenta.ai Careers``,
``11x.ai Careers``, ``basalt.health Careers``). ADR-0212 dropped that guard, because a domain the
company states is its name; only a URL (a scheme, or a leading ``www.``) is still refused. The 63%
predates that change.

Keka is no longer a title row. Only about one Board in eight served a ``<title>`` (92 of an
819-Board census), so since 2026-09-24 `KekaScraper` reads the ``name`` the tenant typed into its
career-portal record instead: 494 of the 516 affected Boards resolve through the guards below.

**taleo_enterprise is a different mechanism, not just a different wrapper.** Its shell serves
*two* ``<title>`` tags — a fixed chrome placeholder first ("Job Search", literally, on all 150 of
150 sampled Boards), then, only when the tenant has themed the Career Section, a second tag with
the real content. `title_of` reads the first ``<title>`` it finds, so this ATS cannot go through
the shared `resolve_company` path at all — `TaleoEnterpriseScraper._company` reads the *last* tag
itself before calling `from_title`. That second tag is far less uniform than the other five ATSes:
alongside the four wrappers below it, the same 150-Board sample also served vendor branding
("Oracle Taleo", four unrelated tenants), a staging label ("PHP Staging Mobile Taleo"), and
marketing copy ("I work for NSW", "Prosegur Ofertas Empleo") with no shared shape — so, unlike
lever, an unwrapped bare title is *not* trustworthy here and gets no pattern. Only four wrappers
are: ``Careers | {Name}`` (D.R. Horton — note the observed double space around the pipe, which
``\\s*`` absorbs), ``{Name} - Careers`` (Valero, IEEE), and eightfold/keka's own
``Careers at {Name}`` / ``{Name} Careers`` (Hospital Authority, Burns & McDonnell, City of Hope,
Wichita Public Schools USD 259, …). 30 of the 150 sampled Boards' second title tag matched one of
these four and passed the safety checks below, with zero observed false positives; the rest —
generic, vendor, unwrapped, or a shape none of the four models — correctly resolve to `None`.
Two more wrappers came later, from the 43 Boards serving their URL on 2026-09-24 ("Job Search |
HDR", "Find a Career - Textron"); the census still found no case for a bare catch-all.

**Not titles.** darwinbox, zwayam, keka and bamboohr render their boards client-side, but each
SPA loads a record that names the tenant: `from_field` reads darwinbox's, zwayam's and bamboohr's,
and keka's pattern reads its own, because keka tenants typed page labels into it. freshteam's
``<title>`` is "Careers" everywhere, but its ``og:title`` names the company.

**Absent, and why.** successfactors' *board* titles stay excluded: they are marketing copy in
several languages with no shared wrapper — "Life@MOHH - people, culture, and values | MOHH",
"Trabaja en Volaris", "Careers at Bachem" — so a pattern wide enough to catch the third mangles the
first two. Its *job* pages turned out to be the source instead: each ends its title "| {Company}",
and its entry below reads that (ADR-0217).

**Workday** reads no title. Its board page is a client-rendered SPA whose ``og:title`` is correct
on well under half of the boards that have one and otherwise junk this module's rules would happily
accept ("Careers", "Job Opportunities", "Team Member Jobs"). Its name comes from the posting
detail's ``hiringOrganization`` instead, beside ``jobPostingInfo`` on 140 of 140 Boards sampled
2026-09-24. That
value is the *per-posting* legal entity and varies **within a single Board** — nvidia alone returns
"IL00 Mellanox Technologies, Ltd.", "IN01 NVIDIA Graphics Bengaluru" and "2100 NVIDIA USA" — so
`headstart.scrapers.workday_company_name` cleans, checks and votes the values into one name, and only
then calls `from_title` for the guards below (ADR-0216).

Every rule below rejects a shape that was actually observed. A title this cannot read leaves the
Board unnamed, and `settled` then serves its `humanised` tenant rather than the raw slug (ADR-0212).

**ADR-0212 makes this module the whole naming policy, not only the title reader.** In order: a
hand-curated name (`curated`, from ``config/company_names.csv``) overrides every source; a page
title's brand outranks a structured legal name (`brand_first`); a field's name is taken as the
company typed it (`from_field`); an all-caps legal name is title-cased (`title_cased`); and a Board
no source names is served under its humanised tenant (`humanised`), never its slug, and under no
name at all where that tenant is only a code (Oracle's pods, ADP's GUIDs).

**The floor is narrower than "never worse", and saying so matters.** What these rules guarantee is
that a slug is never replaced by a *non-name* — a slogan fragment, a URL, the ATS vendor, a
demo placeholder. They cannot guarantee the name a Board states is the one a user would search
for: `ripplehire:ltimindtree` titles itself "LTM Careers | …" and becomes **"LTM"**, and a parent
or acquiring entity can displace a familiar brand (`keka:abcoffee` -> "Brewbay Innovations",
`lever:silhouette` -> "DNAM Brands", `lever:developintelligence` -> "Pluralsight"). Each of those
is the company's own claim about itself, which is the best source available here; an earlier draft
of this paragraph asserted no Board could end up worse, and a 452-Board sweep found otherwise.
The narrower floor — never a *non-name* — has itself been falsified twice by wider sweeps and
repaired twice (see `_PAGE_LABEL`), so treat it as a claim under test, not a proof.
"""

from __future__ import annotations

import csv
import functools
import html
import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from headstart import log
from headstart.boards.board_identity import tenant

_log = log.get(__name__)

__all__ = [
    "agreed_name",
    "brand_first",
    "curated",
    "curated_names",
    "echoes_board",
    "from_field",
    "from_title",
    "humanised",
    "humanised_text",
    "is_identifier",
    "looks_like_slug",
    "settled",
    "tidy",
    "title_cased",
    "title_of",
    "without_scheme",
]

#: Per ATS, the wrapper its board title puts around the company name. Anchored, so a title
#: without the expected shape falls through to ``None`` rather than being mangled into one.
#: "Careers at {Name}" or "{Name} Careers" — eightfold and keka wrap their titles identically,
#: so they share one tuple rather than two that must be kept in step by hand.
_CAREERS_WRAPPER = (
    re.compile(r"^Careers?\s+at\s+(?P<name>.+?)$", re.IGNORECASE),
    re.compile(r"^(?P<name>.+?)\s+Careers$", re.IGNORECASE),
)

# A jibe title's name part where nothing else fences it: refuses text naming a page rather than an
# employer ("Careers Home Apply", "Home Apply", "Explore Exciting Career Opportunities | Careers
# Home", "Blackhawk Talent Network"), each observed on the 2026-09-24 client census. The refusal is
# a tempered token, so it reads only the name it captures: an earlier lookahead scanned the whole
# rest of the title, wrapper included, so "SAM | Careers" and "Intelligent Waves Apply" never
# matched. "Company" is refused only as the name's first word ("Factory Mutual Insurance Company"
# is a name; the bare title "Company" is not).
_JIBE_NAME = (
    r"(?P<name>(?!company\b)(?:(?!\b(?:careers?|jobs?|home|apply|about|search|page"
    r"|opportunit\w*|talent|network|welcome)\b).)+?)"
)

# gem's "{Name} Jobs" and "{Name} Opportunities" would otherwise read a page label's one
# qualifier as the name ("Open Jobs" -> "Open"); no gem Board was seen serving one.
_GEM_NOT_A_LABEL = r"^(?!(?:open|search|current|all|our|new|latest|available)\s)"

PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "ashby": (re.compile(r"^(?P<name>.+?)\s+Jobs$", re.IGNORECASE),),
    "eightfold": _CAREERS_WRAPPER,
    # freshteam: not the `<title>` ("Careers" on every Board) but the `/jobs` page's `og:title`,
    # "Careers - {Name}" on 106 of 120 affected Boards (2026-09-24); the rest serve an 889-byte
    # shell with no og: tags at all. `FreshteamScraper.company_from_page` reads it.
    "freshteam": (re.compile(r"^Careers\s+-\s+(?P<name>.+)$", re.IGNORECASE),),
    # gem: sampled 60 live board pages (2026-09-16) — no JS wall, real server-rendered HTML on a
    # bare GET. ~95% follow "{Name} Careers" (case varies: "a16z speedrun careers"), the exact
    # wrapper eightfold/jobvite/keka already use.
    # The wrappers after the first two were each served by an affected Board, 2026-09-24:
    # "Bluesky Jobs", "Jobs @ Formal", "Opportunities @ Haulvana", "SynthBee Opportunities",
    # "Shorr Packaging Open Positions". "Career Opportunities" comes off whole, or "Align
    # Builders Career Opportunities" would read as "Align Builders Career".
    "gem": (
        *_CAREERS_WRAPPER,
        re.compile(
            r"^(?:Jobs|Careers?|Opportunities)\s+(?:at|@)\s+(?P<name>.+)$",
            re.IGNORECASE,
        ),
        re.compile(
            _GEM_NOT_A_LABEL + r"(?P<name>.+?)\s+(?:Career\s+)?Opportunities$",
            re.IGNORECASE,
        ),
        re.compile(r"^(?P<name>.+?)\s+Open\s+Positions$", re.IGNORECASE),
        re.compile(_GEM_NOT_A_LABEL + r"(?P<name>.+?)\s+Jobs$", re.IGNORECASE),
    ),
    # icims: the listing page `/jobs/search?ss=1&in_iframe=1`, whose template titles it "Job
    # Listings at {Name}" — sometimes after the tenant's own prefix ("Find a Job - General
    # Dynamics Mission Systems Job Listings at General Dynamics Mission Systems"), and Openings or
    # Opportunities on some. A lowercase "the" is the template's, a capitalised one the name's.
    # 46 of 52 Boards sampled 2026-09-24 resolve; two of those name a parent or a site rather
    # than the brand the postings state (`careers-abilegroup` "Abile Headquarters",
    # `careers-avancecare` "Deerfield Management Companies"), the cost of brand-first.
    "icims": (
        re.compile(
            r"^.*?\bJob\s+(?:Listings|Openings|Opportunities)\s+at\s+(?:(?-i:the)\s+)?"
            r"(?P<name>.+)$",
            re.IGNORECASE,
        ),
    ),
    # jibe: the client host's `/jobs` page, 1,116 live clients sampled 2026-09-24. The titles are a
    # handful of wrappers — "{Name} Careers" (200), "{Name} Apply", "{Name} Job Search - Jobs",
    # "Home | {Name} Careers", "{Name} | Careers" — or the bare legal name ("CommonSpirit Health",
    # "Andersen Tax LLC"). The bare-name catch-all refuses any title naming a page rather than an
    # employer ("Careers Home", "Career Home Page", "Company", "Blackhawk Talent Network"). 887 of
    # the 1,116 resolve; the rest keep their slug, which on this ATS is a readable word.
    "jibe": (
        re.compile(
            r"^Home\s*\|\s*(?P<name>.+?)\s+Careers(?:\s+Apply)?$", re.IGNORECASE
        ),
        re.compile(r"^(?:Careers?|Working)\s+at\s+(?P<name>.+?)$", re.IGNORECASE),
        re.compile(r"^Careers?\s*\|\s*(?P<name>.+?)$", re.IGNORECASE),
        re.compile(r"^" + _JIBE_NAME + r"\s*\|\s*Careers(?:\s+Home)?$", re.IGNORECASE),
        re.compile(
            r"^(?P<name>.+?)\s+(?:Careers?|Jobs|Job Search - Jobs)(?:\s+Home)?(?:\s+Apply)?$",
            re.IGNORECASE,
        ),
        re.compile(r"^" + _JIBE_NAME + r"\s+Apply$", re.IGNORECASE),
        re.compile(r"^" + _JIBE_NAME + r"$", re.IGNORECASE),
    ),
    # jobvite: every board titles itself "{Name} Careers"; 424 of 434 live boards resolve
    # (2026-09-07). See JobviteScraper.board_page.
    # And the localized boards of the same tenants (2026-09-24): "Carrières Buckman", "Provisur
    # Technologies Karrieren", "Samtec, Inc carreras", "Samtec, Inc 职业".
    "jobvite": (
        *_CAREERS_WRAPPER,
        re.compile(r"^Carri[eè]res\s+(?P<name>.+)$", re.IGNORECASE),
        re.compile(
            r"^(?P<name>.+?)\s+(?:Karrieren|Carri[eè]res|carreras|职业)$", re.IGNORECASE
        ),
    ),
    # keka: the portal record's `name`, typed by the tenant — bare on most, but some typed a page
    # label around it ("Careers at WeDoGood", "Jobs at Olyv", "SecPod Careers"), so the wrappers
    # come off first and a bare value is taken whole.
    "keka": (
        *_CAREERS_WRAPPER,
        re.compile(r"^Jobs\s+at\s+(?P<name>.+)$", re.IGNORECASE),
        re.compile(r"^(?P<name>.+)$"),
    ),
    # oracle: the Candidate Experience root `/hcmUI/CandidateExperience/`, which redirects to the
    # tenant's default site and titles it with the site's name. A census of the 796 Boards
    # serving their host (2026-09-24) found a name wrapped in one of a few prefixes ("Careers at",
    # "Jobs @", "Job Search |") or followed by a site label ("Career Site", "Candidate Experience
    # site", "Talent Acquisition", "Recruitment", "Jobs") plus template noise ("Global",
    # "External", "Minimal", "V2", "_EN"), or else bare ("Allegheny College"). The bare
    # catch-all is safe here only because `OracleScraper.company_from_page` refuses the generic
    # titles first; 724 of the 796 resolve (2026-09-25), and 7 more failed only on a transient
    # TLS error.
    "oracle": (
        re.compile(
            r"^(?:Careers?|Jobs?)\s*(?:@|at|\|)\s*(?P<name>.+?)$", re.IGNORECASE
        ),
        re.compile(r"^(?:Working|Job Listings)\s+at\s+(?P<name>.+?)$", re.IGNORECASE),
        re.compile(r"^Job Search\s*\|\s*(?P<name>.+?)$", re.IGNORECASE),
        re.compile(
            r"^(?P<name>.+?)(?:\s*[-|_]\s*|\s+|_)"
            r"(?:(?:Global|External|Professional|Experienced|Minimal(?:\s+Template)?|Contingent"
            r"|Corporate|Main)[\s_]+)*"
            r"(?:Candidate[\s_]+Experience(?:[\s_]+(?:External[\s_]+)?(?:site|page))?"
            r"|Careers?(?:[\s_]+(?:Site|Sites|Section|Portal|Page|Website|Opportunities"
            r"|Connection))?"
            r"|Talent[\s_]+Acquisition(?:[\s_]+(?:Team|Site))?"
            r"|Recruitment(?:[\s_]+Team)?"
            r"|Job[\s_]+(?:Openings|Opportunities|Listings)|Jobs"
            r"|Experience[\s_]+site|Candidate[\s_]+site|Jobs[\s_]+and[\s_]+Careers"
            r"|Jobs[\s_]+Direct|e-Recruitment[\s_]+System|Contingent[\s_]+Portal"
            r"|Staff[\s_]+Positions|Human[\s_]+Resources"
            r"|Employee|Agents|People|Site|Portal|External|All[\s_]+Open[\s_]+Jobs|\(temp\)|NEW|EN|-)"
            r"(?:(?:\s*-\s*|[\s_]+)(?:Minimal|New|English|EN|V\d+|\d{1,4}))*$",
            re.IGNORECASE,
        ),
        re.compile(r"^(?P<name>.+)$"),
    ),
    "ripplehire": (re.compile(r"^(?P<name>.+?)\s+Careers\s*\|", re.IGNORECASE),),
    "lever": (re.compile(r"^(?P<name>.+)$"),),
    # pyjamahr: the board page's <title> is the bare company name, with no wrapper at all —
    # it equalled the SSR payload's `companyDetails.name` on 757 of 757 live tenants
    # (2026-09-22), so the catch-all is reading a field, not guessing at a slogan. Run through
    # `from_title`, 723 of those 757 resolve. Of the 34 that keep their slug, 25 are titles that
    # *are* the slug ("smallcase", "volopay" — nothing to upgrade), 5 carry a separator
    # ("RealPage | Rexera", "Ana Reis - Headhunter"), 3 are written as hostnames
    # ("aainacareers.com", which ADR-0212 now takes as stated) and one is a page label ("Careers
    # at AiFA Labs"). A refusal leaves the Board to its humanised tenant.
    "pyjamahr": (re.compile(r"^(?P<name>.+)$"),),
    # personio: the board root asked in English (`?language=en`), titled "Jobs at {Name}" — 149
    # of 186 affected Boards, 2026-09-24. Unasked, a German tenant answers "Jobs bei {Name}".
    "personio": (re.compile(r"^Jobs\s+at\s+(?P<name>.+)$", re.IGNORECASE),),
    # pinpoint: every board titles itself "Jobs at {Name} | {Name} Careers" (40 of 40 sampled
    # 2026-09-23). The name is read from the first clause, which ends at the pipe; the spacing
    # before it varies ("Jobs at Reconomy  | Reconomy  Careers"). 38 of the 40 resolve; the two
    # that do not ("elfc", "scandiweb") are titles that are the slug itself.
    "pinpoint": (re.compile(r"^Jobs\s+at\s+(?P<name>.+?)\s*\|", re.IGNORECASE),),
    # taleo_be: not a page title but its RSS servlet's channel title, "{Name} Job Feed", in the
    # tenant's language on some ("Egov Select VZW functiefeed", "Feed lavoro PARFOIS", "Carga de
    # puesto de Wagman, Inc.", "Flux d'offres d'emploi de SEPAQ"). `TaleoBEScraper.
    # resolve_company` reads it. 30 of 37 Boards sampled 2026-09-24 state one; 5 state "Job Not
    # Available", which no pattern matches, and 2 feeds loop on redirects.
    "taleo_be": (
        re.compile(
            r"^(?P<name>.+?)\s+(?:Job Feed|functiefeed|fil d'emploi)$", re.IGNORECASE
        ),
        re.compile(
            r"^(?:Feed lavoro|Feed de Cargo de|Carga de puesto de|Flux d'offres d'emploi de)"
            r"\s+(?P<name>.+)$",
            re.IGNORECASE,
        ),
    ),
    # phenom: `_CAREERS_WRAPPER` cannot be reused, because these titles carry a second clause
    # after a pipe or a colon ("Careers at Zelis | Zelis Jobs", "OmniCable Careers: Play to
    # Win") and its `$`-anchored non-greedy group would swallow the whole tail as the name.
    # Each wrapper therefore ends at `|`, `:` or end-of-string. Measured over 12 live boards
    # 2026-09-16: 11 resolve, and the one that does not (`Home | BAE Systems`) is an unwrapped
    # title this deliberately refuses rather than guess at — it keeps its slug, which is the
    # floor this module promises. No bare catch-all, for taleo_enterprise's reason.
    "phenom": (
        re.compile(r"^Careers?\s+at\s+(?P<name>.+?)\s*(?:[|:]|$)", re.IGNORECASE),
        re.compile(r"^Careers?\s*\|\s*(?P<name>.+?)\s*(?:\||$)", re.IGNORECASE),
        re.compile(r"^(?P<name>.+?)\s+Careers\s*(?:[|:]|$)", re.IGNORECASE),
    ),
    # taleo_enterprise: no catch-all here, unlike lever — a bare, unwrapped second title is
    # frequently vendor branding or marketing copy on this ATS (see the module docstring), so
    # only wrappers actually observed to carry a name are matched. The first two are
    # this ATS's own (D.R. Horton's title has a double space around the pipe, which `\s*`
    # absorbs; Valero/IEEE end "{Name} - Careers"); the last two are `_CAREERS_WRAPPER`.
    "taleo_enterprise": (
        re.compile(r"^Careers?\s*\|\s*(?P<name>.+?)$", re.IGNORECASE),
        re.compile(r"^(?P<name>.+?)\s*-\s*Careers$", re.IGNORECASE),
        *_CAREERS_WRAPPER,
        # Two more wrappers the 43 Boards serving their URL showed on 2026-09-24: "Job Search |
        # HDR", "Student Jobs Search - Agnico Eagle", "Find a Career - Textron". Still no bare
        # catch-all: the same census served "BRAC Bank Recruiting" and "MOXA External Career
        # Section" unwrapped, which it would take as names.
        re.compile(
            r"^(?:Student\s+)?Jobs?\s+Search\s*[-|]\s*(?P<name>.+?)$", re.IGNORECASE
        ),
        re.compile(r"^Find a Career\s*-\s*(?P<name>.+?)$", re.IGNORECASE),
    ),
    # workday: not a title. `workday_company_name.board_name` reads a name out of the postings'
    # `hiringOrganization` values and the board page's og tags, then passes it here for the
    # guards below, so the pattern is adp's bare catch-all (ADR-0216).
    "workday": (re.compile(r"^(?P<name>.+)$"),),
    # zoho: the careers page the scraper already fetches, where `org_info.company_name` is unset
    # (51 affected Boards, 2026-09-24). Tenants title it freely; these three shapes carry a name.
    # No trailing "{Name} Jobs": `eiger` titles its board "Project Management Jobs", a job family.
    "zoho": (
        re.compile(
            r"^(?:Jobs|Careers?|Internships)\s+(?:at|@|by)\s+(?P<name>.+?)\s*(?:\||\s-\s|$)",
            re.IGNORECASE,
        ),
        re.compile(r"^Jobs\s*\|\s*(?P<name>.+)$", re.IGNORECASE),
        re.compile(r"^(?P<name>.+?)\s+Careers$", re.IGNORECASE),
    ),
    # trakstar: the careers page titles itself "{Name} jobs | {Name} openings | {Name} careers";
    # the name is the first clause. `TrakstarScraper` reads the page itself, on the request that
    # also tells an inactive account apart (see there), rather than through `board_page`.
    "trakstar": (re.compile(r"^(?P<name>.+?)\s+jobs\s*\|", re.IGNORECASE),),
    # successfactors: a field, not a board title. Every RMK job page ends its `<title>` with
    # "| {Company}" and most state it again as `hiringOrganization` microdata; the scraper already
    # fetches those pages, cuts the name out, refuses the unconfigured values RMK falls back to
    # (`SuccessFactorsScraper`'s `_page_company`) and passes the Board's modal one here. Some
    # sites write their careers brand there ("Ingersoll Rand Careers", 13 of 1,469 Boards on
    # 2026-09-24), which `_PAGE_LABEL` would refuse whole; the first pattern strips it.
    "successfactors": (
        re.compile(r"^(?P<name>.+?)\s+Careers$", re.IGNORECASE),
        re.compile(r"^(?P<name>.+)$"),
    ),
}

#: A separator still present after the wrapper came off means the title had a shape this does not
#: model, and half a slogan is a worse company name than the slug. Mostly it bites **lever**,
#: whose pattern matches anything, so "Acme | Careers" reaches here and is refused — but not only
#: lever: `ripplehire:7-eleven-gsc` loses a real name to the `" - "` in "7 - Eleven" (ADR-0114
#: §Known misses). (An earlier version
#: of this comment cited eightfold's "Kraft Heinz Careers – Explore Careers…", which never gets
#: this far: no eightfold pattern matches it, so the loop below rejects it first.)
_SEPARATORS = ("|", "—", "–", " - ", "::")

#: Same idea as `_SEPARATORS`, for a wrapper word rather than a wrapper character. Every pattern
#: above strips one wrapper; text that *still* carries one means the title wore it twice, and what
#: is left is a page label, not a name.
#:
#: **Three observed shapes** — not, as an earlier draft of this comment claimed, an exhaustive set
#: of positions. Each was found only after the previous fix had shipped:
#:   * trailing — `lever:destinationknot` serves "Destination Careers"
#:   * "Careers at" leading — `keka:enpro` serves "Careers at Careers at Enpro Industries"
#:   * the whole string — `lever:schmidt-entities` serves "jobs", which reached 16 real Jobs as
#:     their company before this branch caught it
#:
#: Shapes this deliberately does **not** catch, because none has been observed across every
#: lever and keka Hiring Board (2,998) and this module only ever rejects a shape someone really
#: serves: a medial token ("Acme
#: Careers Portal"), a leading token in another phrasing ("Jobs at Acme", "Careers Acme"), and
#: the singular ("Acme Career"). If one shows up, add it — do not pre-empt it.
#:
#: Anchored rather than matching on word boundaries, because "Career Group" and "Job&Talent" are
#: real employers a `\b`-bounded rule would refuse. Measured across every lever and keka Hiring
#: Board (2,998), it fires six times: three page labels it exists for, and three real employers it
#: costs
#: — `lever:pmaconsultants` ("PMA Consultants Careers", 29 postings), `lever:bananajobs` ("Banana
#: Jobs") and `lever:assurance` ("Assurance Careers") — which keep their slug. That is the
#: deliberate trade: stripping the word
#: instead would turn "Destination Careers" into "Destination", a confident wrong name, where
#: refusing costs only a missed upgrade.
#:
#: The trailing alternative omits the optional "s" the whole-string one allows, so "Acme Career"
#: passes where "Career" alone would not. That is on purpose: a trailing singular reads as part
#: of a name far more often than a bare one does.
_PAGE_LABEL = re.compile(
    r"^careers?\s+at\s+|^(?:careers?|jobs?)$|\s(?:careers|jobs)$", re.IGNORECASE
)

#: Long enough for "Financial Software and Systems Ltd", short enough to reject a sentence — the
#: test pins both ends, against that name and a 70-character lever title. That title —
#: "Succession Planning for Railroads Investing in the Next Generation LLC" — is a legal
#: entity name, not the sentence an earlier draft called it, so the cap is refusing a real
#: name here rather than prose. Recorded as a known miss in ADR-0114.
_MAX_LEN = 60

#: Per ATS, the names its *own* branding goes by. A board page that fails to render its tenant
#: falls back to the platform's branding, so the vendor a title can wrongly name is always the
#: Board's own — `ripplehire:trampolinetech` really does title itself "RippleHire Careers | …".
#: Keying on the Board's ATS is what keeps a vendor that is also a genuine employer elsewhere:
#: `lever:freshworks` titles itself "Freshworks", and the rule this replaced refused it. Note that
#: flattening the values below would *not* reproduce that — the set it replaced was wider, naming
#: every ATS this repo scrapes (freshteam, greenhouse, successfactors, workday and freshworks
#: among them), and only an ATS with patterns can reach this test at all.
#: ADR-0034 blocklists the Boards already known to be vendor-owned; this catches the rest.
_VENDOR_ALIASES: dict[str, frozenset[str]] = {
    # ADP Workforce Now. No ADP-named client was seen in 125 `ClientName`s; kept as the same
    # precaution as taleo_enterprise's — the vendor runs its own payroll on its own platform.
    "adp": frozenset({"adp", "automaticdataprocessing"}),
    # ADP Recruiting Management. Empty on purpose: `clientName` is ADP's client record, not a
    # page that can fall back to the vendor's branding, and ADP is a real client here — `apply`
    # (909 postings) and `adpinternalcareers` both state "ADP" (2 of 681 sites, 2026-09-24).
    "adp_recruiting": frozenset(),
    "ashby": frozenset({"ashby", "ashbyhq"}),
    # Empty on purpose: the GraphQL organization record is no page that can fall back to the
    # vendor's branding, and Ashby is a real employer on its own board (`ashby:ashby`).
    "ashby:graphql": frozenset(),
    # The field sources below are read by `from_field`, which takes a name as typed and
    # checks it against nothing else: bamboohr's `company-info`, cornerstone's posting JSON-LD,
    # darwinbox's `companyinfo`, ripplehire's `companyVO` and zwayam's config call (2026-09-24).
    "bamboohr": frozenset({"bamboohr"}),
    # Cornerstone OnDemand; "cyberu" is its legacy brand, still on the LMS side of `csod.com`.
    "cornerstone": frozenset({"cornerstone", "cornerstoneondemand", "cyberu"}),
    "freshteam": frozenset({"freshteam"}),
    # Darwinbox's own admin tenant states its product name.
    "darwinbox": frozenset({"darwinbox", "darwinboxadmin"}),
    "eightfold": frozenset({"eightfold", "eightfoldai"}),
    # `unavailable`: what iCIMS writes into an unset field. The scraper already leaves it out of
    # the `hiringOrganization` agreement; this keeps it out of a name too.
    "icims": frozenset({"icims", "unavailable"}),
    "jibe": frozenset({"jibe", "jibeapply", "icims"}),
    "jobvite": frozenset({"jobvite"}),
    "keka": frozenset({"keka"}),
    # Empty on purpose: Oracle hires on its own Recruiting Cloud (`eeho.fa.us2`, title "Oracle").
    "oracle": frozenset(),
    "lever": frozenset({"lever"}),
    "ripplehire": frozenset({"ripplehire"}),
    # No vendor-branded gem board was observed in the 60-board sample — kept as the same
    # precaution taleo_enterprise's own entry below is.
    "gem": frozenset({"gem"}),
    # The vendor runs its own board on this platform (`careers.phenom.com`, title "Careers at
    # Phenom"), which is a wrapper this ATS *does* match — so unlike taleo_enterprise's, this
    # entry is not merely precautionary. `phenompeople` is the legacy brand the CDN and the dead
    # `*.phenompeople.com` host namespace still carry.
    "phenom": frozenset({"phenom", "phenompeople"}),
    # The vendor hires on its own platform (`workwithus.pinpointhq.com`, "Jobs at Pinpoint").
    "pinpoint": frozenset({"pinpoint", "pinpointhq"}),
    # The vendor hires on its own platform (`jobs.pyjamahr.com/pyjamahr`, title "PyjamaHR"), so
    # like phenom's this entry is reached by a real Board, not only by a failed render: that one
    # tenant keeps its slug, which reads the same.
    "pyjamahr": frozenset({"pyjamahr"}),
    "personio": frozenset({"personio"}),
    # No matched-wrapper case reached this in the 150-Board sample — "Oracle Taleo" and
    # "Taleo | Mercedes-Benz Group AG" are both already refused for being unwrapped or not
    # matching any of the four shapes. Kept as a precaution: a themed board could plausibly
    # still leave the vendor's own name in a wrapper this ATS *does* match.
    "taleo_enterprise": frozenset({"taleo", "oracle", "oracletaleo"}),
    # The same precaution as taleo_enterprise's.
    "taleo_be": frozenset({"taleo", "oracle", "oracletaleo"}),
    # Empty on purpose, like adp_recruiting's: the name comes from a posting field, not a page
    # that can fall back to the vendor's branding, and Workday hires on its own platform
    # (`workday.wd5.myworkdayjobs.com/Workday`).
    "workday": frozenset(),
    "zoho": frozenset({"zoho", "zohorecruit"}),
    # Beyond the vendor's names, its own test tenants, each stating itself as a company on a
    # live Board (2026-09-24): `hirematetest1` ("Hiremate Test 1"), `ssttest` ("SST Test"),
    # `talentsst1` ("Talent SST"); and "TechCorp", the openings.co template's placeholder title.
    "zwayam": frozenset(
        {"zwayam", "naukri", "hirematetest", "ssttest", "talentsst", "techcorp"}
    ),
    # Trakstar Hire was Recruiterbox before its rebrand, and a trial tenant still titles itself
    # "Recruiterbox jobs | …" (`trakstar:trial101`, 2026-09-24).
    "trakstar": frozenset({"trakstar", "trakstarhire", "recruiterbox"}),
    # "BestRun" is SAP's demo company, left in the title suffix of unconfigured sites. Not "sap":
    # SAP is a real employer (`jobs.sap.com`).
    "successfactors": frozenset({"successfactors", "bestrun"}),
}


#: A board that says out loud it is not a real employer. Reading titles is also a way of *finding*
#: the vendor tenants ADR-0034 exists to remove, and three shapes have been observed doing it: a
#: trailing "Demo" (`ripplehire:itcinfotech`), the unfilled placeholder itself (`ripplehire:
#: tenant1` serves "Your Company Careers | …"), and a trailing "Sandbox" (`ashby:krakensandbox`,
#: `ashby:bento`).
#:
#: The sandbox case is a correction worth keeping. An earlier draft carried `sandbox|uat|qa` and
#: they were dropped as "never observed" — which was only true of the ATSes searched at the time,
#: because the fake-tenant hunt had been run against ripplehire alone. A later ashby census found
#: both. "Never observed" is a statement about where you looked.
#:
#: Anchored to a *trailing* marker, and the leading ``\s`` is what carries that: "Sandbox VR" is a
#: real employer, and a rule matching the word anywhere refused it. ("Test Rite Group", cited here
#: in an earlier draft, is spared for a duller reason — "test" is not in this rule at all.)
#: `uat`/`qa` stay out, still unobserved — but on the evidence above, expect them.
#:
#: This does not only guard future Boards: `ripplehire:tenant1` is live and Scrapable today, and
#: is refused here rather than by the blocklist (it serves 0 postings, so ADR-0034's
#: content-confirmation rule has nothing to read). It cannot catch a QA tenant that titles itself
#: after the company it imitates — `ripplehire:tenant1-mph` served "Mphasis" — so that one went to
#: the blocklist, which is the only thing that can.
_PLACEHOLDER = re.compile(r"(?:\s(?:demo|sandbox)|^your\s+company)$", re.IGNORECASE)


def looks_like_slug(name: str | None) -> bool:
    """Whether ``name`` reads as an identifier rather than something a person would write.

    A Board can arrive already carrying a "name" that is itself a slug — the liveness ledger
    holds "wipro" and "gamuda", and Workday's own ledger rows hold "citi" and
    "dick-s-sporting-goods". Treating those as real names is what made the first draft of
    :meth:`~headstart.scrapers.base.BaseScraper.resolve_company` refuse to improve precisely the
    rows this exists to fix.
    """
    text = (name or "").strip()
    if not text:
        return True
    # No separate space test: the character class already excludes whitespace.
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9._/-]*", text))


def title_of(page: str | None) -> str | None:
    """The ``<title>`` of an HTML page, tags stripped and whitespace collapsed, or None.

    Here rather than at the call site so the only regex reading a board page lives beside the
    patterns that consume it, and so both halves are testable without a request.
    """
    match = re.search(
        r"<title[^>]*>(.*?)</title>", page or "", re.DOTALL | re.IGNORECASE
    )
    if not match:
        return None
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", match.group(1))).strip()
    return text or None


def from_title(ats: str, title: str | None, slug: str) -> str | None:
    """The company name ``title`` yields for ``ats``, or None when it yields nothing trustworthy.

    ``slug`` is what the caller will keep using if this returns None, and is also compared
    against: a title that is *exactly* the slug has nothing to add.
    """
    if not title:
        return None
    text = html.unescape(title).strip()
    for pattern in PATTERNS.get(ats, ()):
        match = pattern.match(text)
        if match:
            text = match.group("name").strip()
            break
    else:
        return None
    # No emptiness test: `text` was stripped before matching and every pattern needs a character.
    if len(text) > _MAX_LEN:
        return None
    if any(separator in text for separator in _SEPARATORS) or _PAGE_LABEL.search(text):
        return None
    # A domain-shaped name is a name when the company states it ("11x.ai", "incident.io"), so it
    # is taken (ADR-0212). A URL is not: lever served "https://www.azuga.com/" as a title.
    if _is_url(text):
        return None
    # Only an EXACT echo is worthless. Case and spacing are the whole point — "aida" becomes
    # "Aida", "1password" becomes "1Password" — so normalising before this comparison rejects
    # precisely the improvement being sought. It did: ashby scored 0/12 until this was narrowed.
    if text == slug:
        return None
    # Letters only, no legal form dropped: not `company_suggestions.normalize` (see its `_LEGAL`).
    if re.sub(r"[^a-z]", "", text.lower()) in _VENDOR_ALIASES.get(ats, frozenset()):
        return None
    if _PLACEHOLDER.search(text):
        return None
    return title_cased(text)


def _is_url(text: str) -> bool:
    """A URL rather than a name: it carries a scheme or opens on ``www.`` (ADR-0212)."""
    return bool(_SCHEME.match(text)) or text.lower().startswith("www.")


def from_field(ats: str, value: str | None) -> str | None:
    """The company name a structured field states, or None when it states nothing usable.

    For a name the ATS hands over as data — Greenhouse's ``company_name``, SmartRecruiters'
    ``company.name`` — not a title that has to be unwrapped. Such a name is what the company typed,
    so the guards `from_title` needs against page copy do not apply: "commercetools" and "sunday"
    are refused neither for equalling the slug nor for being lowercase, and "incident.io" is not
    read as a hostname (ADR-0212). Padding is not a name, which is the bug ``value or fallback``
    had: rippling's ``agora`` states "   ", which is truthy.
    """
    text = html.unescape(value or "").strip()
    if not text or _is_url(text):
        return None
    if re.sub(r"[^a-z]", "", text.lower()) in _VENDOR_ALIASES.get(ats, frozenset()):
        return None
    return title_cased(text)


def brand_first(brand: str | None, legal: str | None) -> str | None:
    """The brand a Board's page states, else its structured legal name (ADR-0212).

    Both arrive already checked (`from_title`, `from_field`). A page title carries the name the
    company shows a job seeker ("Klipboard"), where a structured field often carries the entity
    that signs the contract ("KERRIDGE COMMERCIAL SYSTEMS CORP"), so the brand wins whenever both
    exist.
    """
    return brand or legal


#: How a short token of an all-caps name is spelled once the name is title-cased. Any other token
#: of four letters or fewer stays uppercase, because in an all-caps legal name it is far more often
#: an acronym ("SS", "IIFL", "ZIM", "QA") than a word. Legal forms and joining words are here
#: because they are always words; the rest are the short English words the 186 converted names in
#: the 2026-09-24 research samples (keka, zwayam, darwinbox and the field ATSes) carried.
_SHORT_WORDS: dict[str, str] = {
    **{w: w.capitalize() for w in ("ltd", "pvt", "inc", "co", "corp")},
    **{w: w.upper() for w in ("llc", "llp", "plc")},
    "gmbh": "GmbH",
    **{w: w for w in ("and", "of", "at", "the", "for", "in", "on", "to")},
    **{
        w: w.capitalize()
        for w in (
            "aero", "apps", "avid", "axis", "bank", "deck", "food", "hair", "jobs", "labs",
            "life", "lift", "one", "site", "skin", "web",
        )
    },
}  # fmt: skip

#: A run of letters, keeping an apostrophe's tail with it so "MACY'S" reads "Macy's", not "Macy'S".
_LETTERS = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?")


def _letter_count(word: str) -> int:
    """Letters that have a case: a script without one ("クオリティー") is neither caps nor a word."""
    return sum(char.upper() != char.lower() for char in word)


def title_cased(name: str) -> str:
    """``name`` title-cased when it is an all-caps legal name, else unchanged (ADR-0212).

    Converts only a name that is entirely uppercase, has more than one word, and has a word
    longer than four letters: "IMPRONICS DIGITECH PRIVATE LIMITED" becomes "Impronics Digitech
    Private Limited", while "HCL", "BMW" and "CRISIL" stay as the company writes them. A word of
    four letters or fewer keeps its capitals unless `_SHORT_WORDS` knows it as a word, so
    "SS SUPPLY CHAIN SOLUTION PVT. LTD." reads "SS Supply Chain Solution Pvt. Ltd.".
    """
    words = name.split()
    if name != name.upper() or name == name.lower() or len(words) < 2:
        return name
    if not any(_letter_count(word) > 4 for word in words):
        return name
    out = []
    for position, word in enumerate(words):
        if _letter_count(word) > 4:
            out.append(_LETTERS.sub(lambda m: m.group().capitalize(), word))
            continue
        cased = _LETTERS.sub(
            lambda m: _SHORT_WORDS.get(m.group().lower(), m.group()), word
        )
        # A joining word opens a name capitalised: "THE HI-TECH …" is "The Hi-Tech …".
        out.append(_capitalised(cased) if position == 0 else cased)
    return " ".join(out)


_CURATED_FILE = "company_names.csv"


@functools.cache
def curated_names() -> dict[str, str]:
    """``lowercased board_key -> name`` from the committed map, `config/company_names.csv`.

    Looked for beside this module and then in every ancestor's ``config/``, as `fx` finds its
    rate table: a repo checkout and an installed package lay the file out differently. Lines
    opening with ``#`` are comments. Absent, it is an empty map and every Board is named from
    its own sources. Keys are lowercased once here, because a ledger's casing and a fresh
    ``board_key()`` need not agree (ADR-0049). Read once per process and cached; callers read it
    through this function at call time, and ``curated_names.cache_clear()`` re-reads the file
    (ADR-0212).
    """
    here = Path(__file__).resolve()
    candidates = (
        here.parent / _CURATED_FILE,
        *(ancestor / "config" / _CURATED_FILE for ancestor in here.parents),
    )
    for path in candidates:
        if path.is_file():
            with path.open(encoding="utf-8") as handle:
                rows = csv.DictReader(
                    line for line in handle if not line.startswith("#")
                )
                return {row["board_key"].lower(): row["name"].strip() for row in rows}
    # Once per process, by the cache above.
    _log.info(
        f"{_CURATED_FILE} not found on {len(candidates)} candidate paths — curated names off"
    )
    return {}


def curated(board_key: str) -> str | None:
    """The hand-curated name for this Board, which overrides every other source, else None."""
    return curated_names().get(board_key.lower())


#: Host labels that name the *board* rather than the company, and so are never the answer.
#: Vendor labels and TLDs sit here too: `micron.wd5.myworkdayjobs.com` and
#: `lockheed.jobs.hr.cloud.sap` both have to reduce to their first real word. Its few legal
#: words are not `company_suggestions._LEGAL`, which says why the two lists stay apart.
LABEL_NOISE = frozenset(
    {
        "www",
        "careers",
        "career",
        "jobs",
        "job",
        "apply",
        "join",
        "opportunities",
        "hire",
        "hiring",
        "talent",
        "work",
        "working",
        "recruiting",
        "recruitment",
        "internal",
        "internaljobs",
        "external",
        "search",
        "inc",
        "ltd",
        "llc",
        "corp",
        "group",
        "global",
        "en",
        "us",
        # vendor hosts and the public suffixes behind them
        "myworkdayjobs",
        "icims",
        "eightfold",
        "zohorecruit",
        "openings",
        "taleo",
        "tbe",
        "oraclecloud",
        "ocs",
        "fa",
        "sap",
        "cloud",
        "hr",
        "wd",
        "smartrecruiters",
        "successfactors",
        "com",
        "net",
        "org",
        "io",
        "co",
        "ai",
        "in",
        "eu",
        "uk",
        "de",
        "ca",
    }
)
_WD_POD = re.compile(r"^wd\d+$")  # micron.wd5.myworkdayjobs.com
# Taleo Enterprise's slug is a whole URL.
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)


def without_scheme(text: str) -> str:
    """``text`` with a leading URL scheme removed, so its first segment is a host or tenant."""
    return _SCHEME.sub("", text)


#: Words that name the board at the edge of a hyphenated slug: `apexanalytix-careers`,
#: `codvo-team`, `careers-at-aifa-labs`. Stripped only at the ends, and only while a word remains.
_EDGE_WORDS = frozenset({"careers", "career", "jobs", "job", "team", "hiring", "at"})


def tidy(text: str) -> str | None:
    """The company a slug or host names, or None when every label of a host is noise.

    Picking the *first non-noise label* rather than the registrable domain is deliberate, and
    both conventions appear in the data: `careers-inc.nttdata.com` puts the company second,
    while `lockheed.jobs.hr.cloud.sap` puts it first. Taking the label before the public suffix
    reads the latter as "Cloud"; taking the first label reads the former as "Careers-Inc".
    """
    # Taleo Enterprise's slug is a whole URL; split on "/" first, it tidied to "Https:".
    head = without_scheme(text).split("/", 1)[0]
    if "." not in head:
        return head or None
    labels = [
        label
        for label in head.split(".")
        if label and label not in LABEL_NOISE and not _WD_POD.match(label)
    ]
    # A prefixed label still carries the company after its noise word (`careers-inc` ->
    # `inc`, dropped above; `jobs-bylight` -> `bylight`).
    for label in labels:
        parts = [p for p in label.split("-") if p and p not in LABEL_NOISE]
        if parts:
            return "-".join(parts)
    return labels[0] if labels else None


def humanised_text(text: str) -> str:
    """A slug-shaped ``text`` spelled as a name: words split, edges trimmed, each word cased.

    A lowercase word of three letters or fewer is read as an acronym ("hpe" -> "HPE", "gmv" ->
    "GMV"), and a longer one is capitalised ("nvidia" -> "Nvidia"). A mixed-case word keeps its
    capitals ("TecTammina"), an all-caps one of five letters or more is capitalised, and the
    result goes through `title_cased`. A trailing number
    is a disambiguator, not a name (`werecruiters-1`, `evrocab-1692891239`), so it goes too.
    """
    words = [word for word in re.split(r"[-_\s]+", text) if word]
    while len(words) > 1 and words[0].lower() in _EDGE_WORDS:
        words.pop(0)
    while len(words) > 1 and (words[-1].lower() in _EDGE_WORDS or words[-1].isdigit()):
        words.pop()
    cased = []
    for word in words:
        if word == word.upper() and _letter_count(word) > 4:
            # An identifier's capitals are not the company's spelling: Taleo Business Edition's
            # org `GATEWAYVENT` reads "Gatewayvent".
            cased.append(word.capitalize())
        elif word in _SHORT_WORDS:
            cased.append(_SHORT_WORDS[word])
        elif word == word.lower() and _letter_count(word) <= 3:
            cased.append(word.upper())
        else:
            cased.append(_capitalised(word))
    if cased:
        cased[0] = _capitalised(cased[0])
    return title_cased(" ".join(cased))


_FIRST_LETTER = re.compile(r"[^\W\d_]")


def _capitalised(word: str) -> str:
    """``word`` with its first *letter* uppercased: "1password" is "1Password", not unchanged."""
    return _FIRST_LETTER.sub(lambda m: m.group().upper(), word, count=1)


#: ATSes whose tenant is a code the vendor generated, never the employer's name, and whose host
#: is the vendor's own (`oraclecloud.com`, `workforcenow.adp.com`), so no host label can name the
#: employer either. Oracle's first label is its pod: of the oracle ledger's 1,683 live rows
#: (2026-09-25) 833 are four letters (`eeho`), 209 six (`ibqbjb`), 560 `fa-{pod}-saasfa…prod1`,
#: and of the other 81 about half put a name before the pod (`utulsa-ibvjjb`) and half another
#: code (`ia-erp-iaedkf`, `hcdtgccprod-iayeqy`). Telling those halves apart is a guess, so the
#: whole ATS is unnamed here. ADP's slug is a client GUID and its career-center id.
_CODE_TENANTS = frozenset({"oracle", "adp"})


def _is_code(word: str) -> bool:
    """A generated identifier rather than a word: all digits, or digits in two or more runs.

    One run is still a name ("good2grow", "A3logics", "Covestic2"); two is a code ("G94W9A",
    a GUID's "1fee41c0cca3").
    """
    return word.isdigit() or len(re.findall(r"\d+", word)) >= 2


def humanised(board_key: str) -> str | None:
    """The name a Board with no stated name is shown under: its tenant, humanised (ADR-0212).

    Never the raw slug. `workday:nvidia/NVIDIAExternalCareerSite` is "Nvidia", and
    `icims:careers-gd-ais.icims.com` is "GD AIS": the tenant (`board_identity.tenant`), its board
    and vendor labels dropped (`tidy`), spelled as words (`humanised_text`).

    None where that would only spell a code: an ATS in `_CODE_TENANTS`, or a tenant whose every
    word `_is_code`. No company is better than a code that reads as one (ADR-0212).
    """
    if board_key.split(":", 1)[0] in _CODE_TENANTS:
        return None
    core = tenant(board_key)
    name = humanised_text(tidy(core) or core) or core
    return None if all(_is_code(word) for word in name.split()) else name


def echoes_board(name: str, board_key: str) -> bool:
    """Whether ``name`` repeats this Board's key rather than naming anyone: a URL, anything
    written as a path or an address (Workday's ledger `nvidia.wd5.myworkdayjobs.com/nvidia…`,
    Taleo Business Edition's `COVESTIC2:40@phf…`), or exactly a piece of the key
    (SuccessFactors' `hcltech`, SmartRecruiters' `TecTammina`). "incident.io" on `gem:incident`
    does not, which is how a stated lowercase name survives on the Hot and Trends tabs.
    """
    text = name.strip()
    if _is_url(text) or (not re.search(r"\s", text) and re.search(r"[/@]", text)):
        return True
    slug = board_key.split(":", 1)[-1]
    return text in {slug, tenant(board_key), *re.split(r"[/.:@?=&]", slug)}


def is_identifier(name: str, board_key: str) -> bool:
    """Whether a name the scraper was *constructed* with is an identifier rather than a
    company's name: lowercase identifier text ("wipro", "careers.persistent.com"), or it
    `echoes_board`.
    """
    return looks_like_slug(name) or echoes_board(name, board_key)


def settled(name: str, unresolved: str, board_key: str) -> str | None:
    """The company this Board's Jobs are served under, once its sources have had their say.

    ``unresolved`` is what the scraper held before any source ran: its declared ``COMPANY``, or
    the ledger's name or slug. A curated name overrides everything; a name a source stated during
    the fetch is kept, and so is an unresolved one that is really a name (a declared
    ``COMPANY``, the curated feed's "Stripe"); a Board's own identifier becomes its `humanised`
    tenant, never the raw slug, or None where that tenant is only a code (ADR-0212).
    """
    named = curated(board_key)
    if named:
        return named
    if name.strip() and (name != unresolved or not is_identifier(name, board_key)):
        return name
    return humanised(board_key)


def agreed_name(names: Iterable[str | None], share: float = 0.0) -> str | None:
    """The name most of ``names`` state, when at least ``share`` of the stated ones agree on it.

    For an ATS whose name source is a field each posting states rather than one Board page: the
    Board is named once, by the name its postings agree on, and ``share`` is how much agreement
    that ATS's measurement showed it needs. An empty or None entry states nothing and counts on
    neither side. The result is raw — the caller still passes it through :func:`from_field`.
    """
    stated = Counter(name.strip() for name in names if name and name.strip())
    if not stated:
        return None
    name, count = stated.most_common(1)[0]
    return name if count >= share * stated.total() else None
