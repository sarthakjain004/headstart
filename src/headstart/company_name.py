"""The company name a Board states in its page ``<title>``, when it states one at all.

`BaseScraper.__init__` does ``self.company = company or slug``, so a Board whose ledger row
carries no name serves its **slug** as the company. Measured on the served table 2026-09-07:
150,626 of 318,003 rows are *literally* the slug (47.4%), and **186,798 (58.7%) are slug-shaped**
once the Boards whose ledger "name" is itself an identifier are counted — the ledger holds "wipro"
and "gamuda", Workday's holds "citi" and "dick-s-sporting-goods". Users see "1password",
"jobs.vodafone.com", "nttltd" where a company name belongs. The wider figure is the honest one.

Four ATSes put the real name in their board page's ``<title>``, each wrapped differently, and one
request per Board recovers it. Which four is a measurement, not a guess: 30 live Boards were
sampled per ATS (`experiment/company-display-name/`, gitignored), and only those where the wrapper is uniform
enough to strip safely are here.

===============  ==========================================  =====================
ATS              title shape                                 yields a name
===============  ==========================================  =====================
ashby            ``{Name} Jobs``                             28/30
eightfold        ``Careers at {Name}`` / ``{Name} Careers``  28/30
ripplehire       ``{Name} Careers | Latest jobs at …``       28/30
lever            ``{Name}`` — no wrapper at all              25/30
===============  ==========================================  =====================

**Absent, and why — stated more carefully than the first draft, which overclaimed.**
successfactors, keka, darwinbox and freshteam score **0/30** *against the patterns registered
here*. That is not the same as having nothing to read: a later sweep found roughly one keka board
in eight serving a title already in eightfold's shape ("Entropik Careers"), and successfactors
serves parseable ones too ("Careers at Bachem"). They are excluded because a hit rate that low
buys a request on every Board of the ATS for a name on few of them — a cost decision, not an
absence of data, and one worth revisiting with its own measurement.

**Workday** is excluded on stronger evidence. Its listing and detail responses carry no name —
verified by driving the real scraper — and its board page is a client-rendered SPA. It does serve
an ``og:title``, but sampled live it is correct on well under half of the boards that have one and
otherwise junk this module's rules would happily accept ("Careers", "Job Opportunities", "Team
Member Jobs"). The public job page's JSON-LD ``hiringOrganization`` is worse still: it is the
*per-posting* legal entity and varies **within a single Board** — nvidia alone returns "IL00
Mellanox Technologies, Ltd.", "IN01 NVIDIA Graphics Bengaluru" and "2100 NVIDIA USA" across three
postings. A name we invent is worse than a slug we admit to.

Every rule below rejects a shape that was actually observed. A title this cannot read leaves the
Board on its slug, which is exactly today's behaviour — this only ever replaces a slug with
something better, never with something worse.
"""

from __future__ import annotations

import html
import re

__all__ = ["from_title", "looks_like_slug", "title_of"]

#: Per ATS, the wrapper its board title puts around the company name. Anchored, so a title
#: without the expected shape falls through to ``None`` rather than being mangled into one.
PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "ashby": (re.compile(r"^(?P<name>.+?)\s+Jobs$", re.IGNORECASE),),
    "eightfold": (
        re.compile(r"^Careers?\s+at\s+(?P<name>.+?)$", re.IGNORECASE),
        re.compile(r"^(?P<name>.+?)\s+Careers$", re.IGNORECASE),
    ),
    "ripplehire": (re.compile(r"^(?P<name>.+?)\s+Careers\s*\|", re.IGNORECASE),),
    "lever": (re.compile(r"^(?P<name>.+)$"),),
}

#: A separator still present after the wrapper came off means the title had a shape this does not
#: model, and half a slogan is a worse company name than the slug. It bites on **lever**, whose
#: pattern matches anything, so "Acme | Careers" reaches here and is refused. (An earlier version
#: of this comment cited eightfold's "Kraft Heinz Careers – Explore Careers…", which never gets
#: this far: no eightfold pattern matches it, so the loop below rejects it first.)
_SEPARATORS = ("|", "—", "–", " - ", "::")

#: Long enough for "Financial Software & Systems (P) Ltd.", short enough to reject a sentence.
_MAX_LEN = 60

#: The ATS vendors themselves. A Board whose title names its *vendor* is a demo, a parked tenant,
#: or a page that fell back to the platform's own branding — `ripplehire:trampolinetech` really
#: does title itself "RippleHire Careers | …", and shipping that puts the ATS's name in front of
#: a user as the employer. ADR-0034 already blocklists the Boards it knows are vendor-owned; this
#: catches the ones that only reveal it in their title.
_VENDORS = frozenset(
    {
        "ashby",
        "ashbyhq",
        "darwinbox",
        "eightfold",
        "freshteam",
        "freshworks",
        "greenhouse",
        "keka",
        "lever",
        "ripplehire",
        "successfactors",
        "workday",
    }
)


def looks_like_slug(name: str | None) -> bool:
    """Whether ``name`` reads as an identifier rather than something a person would write.

    A Board can arrive already carrying a "name" that is itself a slug — the liveness ledger
    holds "wipro" and "gamuda", and Workday's own ledger rows hold "citi" and
    "dick-s-sporting-goods". Treating those as real names is what made the first draft of
    :meth:`~headstart.scrapers.base.BaseScraper.resolve_company` refuse to improve precisely the
    rows this exists to fix.
    """
    if not name:
        return True
    return " " not in name.strip() and bool(
        re.fullmatch(r"[a-z0-9][a-z0-9._/-]*", name)
    )


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
    if not text or len(text) > _MAX_LEN:
        return None
    if any(separator in text for separator in _SEPARATORS):
        return None
    # A hostname — "webfx.com" — but only when written like one. The regex is deliberately
    # case-sensitive, which alone spares "Character.AI"; the lowercase test earns its place on
    # names with a lowercase TLD, where "Sprout.ai" would otherwise be read as a domain.
    if text == text.lower() and re.fullmatch(r"[\w.-]+\.[a-z]{2,}", text):
        return None
    # Only an EXACT echo is worthless. Case and spacing are the whole point — "aida" becomes
    # "Aida", "1password" becomes "1Password" — so normalising before this comparison rejects
    # precisely the improvement being sought. It did: ashby scored 0/12 until this was narrowed.
    if text == slug:
        return None
    if re.sub(r"[^a-z]", "", text.lower()) in _VENDORS:
        return None
    return text
