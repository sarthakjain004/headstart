"""The company name a Board states in its page ``<title>``, when it states one at all.

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
keka              ``Careers at {Name}`` / ``{Name} Careers``         ~11% (92 of 819)
taleo_enterprise  four ``Careers``-wrappers (see below)              20% (30/150)
gem               ``Careers at {Name}`` / ``{Name} Careers``         63% (36/57)
jobvite           ``{Name} Careers``                                 424 of 434
phenom            ``Careers``-wrappers ending at ``|`` or ``:``       11 of 16 boards
pinpoint          ``Jobs at {Name} | {Name} Careers``                 38 of 40
================  =================================================  =================

**gem** is the lowest-yield row of the wrapper-matching ATSes, and the gap between "matches the
wrapper" (~95%) and "yields a name" (63%) is almost entirely the hostname guard, not a bad pattern:
many Gem tenants are early-stage startups whose brand IS their domain (``agenta.ai Careers``,
``11x.ai Careers``, ``basalt.health Careers``), and `from_title`'s existing rule correctly declines
those rather than serving a bare domain as a company name.

Keka is the odd row and worth reading twice: only about one Board in eight serves a ``<title>`` at
all (the rest render it client-side), but where one exists the wrapper is as uniform as
eightfold's, and *every* keka Board serves a slug today — so that ~11% is pure upside for one
cheap request. The first draft excluded keka on a stated **0/30**, which was simply wrong; the
figure here is a full 819-Board census, not a sample.

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

**Absent, and why.** darwinbox and freshteam render their boards client-side and serve nothing to
read. successfactors is the interesting exclusion: it does serve titles, but they are marketing
copy in several languages with no shared wrapper — "Life@MOHH - people, culture, and values |
MOHH", "Trabaja en Volaris", "Careers at Bachem" — so a pattern wide enough to catch the third
mangles the first two. That is a quality bar, not a cost one, and no measurement will move it;
what it needs is per-tenant evidence this module has no place to keep.

**Workday** is excluded on stronger evidence. Its listing and detail responses carry no name —
verified by driving the real scraper — and its board page is a client-rendered SPA. It does serve
an ``og:title``, but sampled live it is correct on well under half of the boards that have one and
otherwise junk this module's rules would happily accept ("Careers", "Job Opportunities", "Team
Member Jobs"). The public job page's JSON-LD ``hiringOrganization`` is worse still: it is the
*per-posting* legal entity and varies **within a single Board** — nvidia alone returns "IL00
Mellanox Technologies, Ltd.", "IN01 NVIDIA Graphics Bengaluru" and "2100 NVIDIA USA" across three
postings. A name we invent is worse than a slug we admit to.

Every rule below rejects a shape that was actually observed. A title this cannot read leaves the
Board on its slug, which is exactly today's behaviour.

**The floor is narrower than "never worse", and saying so matters.** What these rules guarantee is
that a slug is never replaced by a *non-name* — a slogan fragment, a hostname, the ATS vendor, a
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

import html
import re

__all__ = ["from_title", "looks_like_slug", "title_of"]

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
# Home", "Blackhawk Talent Network"), each observed on the 2026-09-24 client census.
_JIBE_NAME = (
    r"(?P<name>(?!.*\b(?:careers?|jobs?|home|apply|about|search|page|opportunit\w*"
    r"|company|talent|network|welcome)\b).+?)"
)

PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    # adp: not a title at all. Workforce Now's page title is the literal "Recruitment" on every
    # career center, and nothing a browser renders names the employer; its `client-features`
    # JSON does, as `ClientName` (120 of 120 centers sampled 2026-09-23). `ADPScraper` reads that
    # field and passes it through `from_title` for the guards below, so the pattern is the bare
    # catch-all pyjamahr's is: the value is a field, not a wrapped slogan.
    "adp": (re.compile(r"^(?P<name>.+)$"),),
    # adp_recruiting (ADP Recruiting Management, a separate product from ADP Workforce Now's
    # `adp` above): a field again, not a title. The SPA's title is "Career Site" on every site;
    # the site record it fetches for its token states `clientName` (681 of 681 sites,
    # 2026-09-24), which `ADPRecruitingScraper` passes through here for the guards below.
    "adp_recruiting": (re.compile(r"^(?P<name>.+)$"),),
    "ashby": (re.compile(r"^(?P<name>.+?)\s+Jobs$", re.IGNORECASE),),
    "eightfold": _CAREERS_WRAPPER,
    # gem: sampled 60 live board pages (2026-09-16) — no JS wall, real server-rendered HTML on a
    # bare GET. ~95% follow "{Name} Careers" (case varies: "a16z speedrun careers"), the exact
    # wrapper eightfold/jobvite/keka already use.
    "gem": _CAREERS_WRAPPER,
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
    "jobvite": _CAREERS_WRAPPER,
    "keka": _CAREERS_WRAPPER,
    "ripplehire": (re.compile(r"^(?P<name>.+?)\s+Careers\s*\|", re.IGNORECASE),),
    "lever": (re.compile(r"^(?P<name>.+)$"),),
    # pyjamahr: the board page's <title> is the bare company name, with no wrapper at all —
    # it equalled the SSR payload's `companyDetails.name` on 757 of 757 live tenants
    # (2026-09-22), so the catch-all is reading a field, not guessing at a slogan. Run through
    # `from_title`, 723 of those 757 resolve. Of the 34 that keep their slug, 25 are titles that
    # *are* the slug ("smallcase", "volopay" — nothing to upgrade), 5 carry a separator
    # ("RealPage | Rexera", "Ana Reis - Headhunter"), 3 are written as hostnames
    # ("aainacareers.com") and one is a page label ("Careers at AiFA Labs"); the slug is the
    # floor this module promises, and every refusal here lands on it.
    "pyjamahr": (re.compile(r"^(?P<name>.+)$"),),
    # pinpoint: every board titles itself "Jobs at {Name} | {Name} Careers" (40 of 40 sampled
    # 2026-09-23). The name is read from the first clause, which ends at the pipe; the spacing
    # before it varies ("Jobs at Reconomy  | Reconomy  Careers"). 38 of the 40 resolve; the two
    # that do not ("elfc", "scandiweb") are titles that are the slug itself.
    "pinpoint": (re.compile(r"^Jobs\s+at\s+(?P<name>.+?)\s*\|", re.IGNORECASE),),
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
    # only the four wrappers actually observed to carry a name are matched. The first two are
    # this ATS's own (D.R. Horton's title has a double space around the pipe, which `\s*`
    # absorbs; Valero/IEEE end "{Name} - Careers"); the last two are `_CAREERS_WRAPPER`.
    "taleo_enterprise": (
        re.compile(r"^Careers?\s*\|\s*(?P<name>.+?)$", re.IGNORECASE),
        re.compile(r"^(?P<name>.+?)\s*-\s*Careers$", re.IGNORECASE),
        *_CAREERS_WRAPPER,
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
    "eightfold": frozenset({"eightfold", "eightfoldai"}),
    "jibe": frozenset({"jibe", "jibeapply", "icims"}),
    "jobvite": frozenset({"jobvite"}),
    "keka": frozenset({"keka"}),
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
    # No matched-wrapper case reached this in the 150-Board sample — "Oracle Taleo" and
    # "Taleo | Mercedes-Benz Group AG" are both already refused for being unwrapped or not
    # matching any of the four shapes. Kept as a precaution: a themed board could plausibly
    # still leave the vendor's own name in a wrapper this ATS *does* match.
    "taleo_enterprise": frozenset({"taleo", "oracle", "oracletaleo"}),
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
    # A hostname — "webfx.com" — but only when written like one. The `text == text.lower()` guard
    # is what spares "Character.AI": it short-circuits, so nothing mixed-case ever reaches the
    # regex and the regex's own case-sensitivity decides nothing. The guard also earns its place
    # on a lowercase TLD, where "Sprout.ai" would otherwise be read as a domain.
    if text == text.lower() and re.fullmatch(r"[\w.-]+\.[a-z]{2,}", text):
        return None
    # Only an EXACT echo is worthless. Case and spacing are the whole point — "aida" becomes
    # "Aida", "1password" becomes "1Password" — so normalising before this comparison rejects
    # precisely the improvement being sought. It did: ashby scored 0/12 until this was narrowed.
    if text == slug:
        return None
    # Letters only, no legal form dropped: not `company_match.normalize` (see its `_LEGAL`).
    if re.sub(r"[^a-z]", "", text.lower()) in _VENDOR_ALIASES.get(ats, frozenset()):
        return None
    if _PLACEHOLDER.search(text):
        return None
    return text
