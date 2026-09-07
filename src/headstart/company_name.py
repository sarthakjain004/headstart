"""The company name a Board states in its page ``<title>``, when it states one at all.

`BaseScraper.__init__` does ``self.company = company or slug``, so a Board whose ledger row
carries no name serves its **slug** as the company. Measured on the served table 2026-09-07:
150,626 of 318,003 rows are *literally* the slug (47.4%), and **186,798 (58.7%) are slug-shaped**
once the Boards whose ledger "name" is itself an identifier are counted — the ledger holds "wipro"
and "gamuda", Workday's holds "citi" and "dick-s-sporting-goods". Users see "1password",
"jobs.vodafone.com", "nttltd" where a company name belongs. The wider figure is the honest one.

Five ATSes put the real name in their board page's ``<title>``, each wrapped differently, and one
request per Board recovers it. Which five is a measurement, not a guess: live Boards were sampled
per ATS (`experiment/company-display-name/`, gitignored), and only those whose wrapper is uniform
enough to strip safely are here. Sample sizes differ on purpose: the first pass was 30 Boards per
ATS, and each row was re-measured larger wherever 30 proved too few to trust. Lever needed it most
— two 30-Board samples disagreed (25 and 21) before 400 settled it near 88% — and keka's row is a
full census rather than a sample.

===============  ==========================================  =====================
ATS              title shape                                 yields a name
===============  ==========================================  =====================
ashby            ``{Name} Jobs``                             ~92% (n=120)
eightfold        ``Careers at {Name}`` / ``{Name} Careers``  ~93% (n=100)
ripplehire       ``{Name} Careers | Latest jobs at …``       ~94% (all 52)
lever            ``{Name}`` — no wrapper at all              ~88% (352/400)
keka             ``Careers at {Name}`` / ``{Name} Careers``  ~11% (92 of 819)
===============  ==========================================  =====================

Keka is the odd row and worth reading twice: only about one Board in nine serves a ``<title>`` at
all (the rest render it client-side), but where one exists the wrapper is as uniform as
eightfold's, and *every* keka Board serves a slug today — so that ~11% is pure upside for one
cheap request. The first draft excluded keka on a stated **0/30**, which was simply wrong; the
figure here is a full 819-Board census, not a sample.

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

PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "ashby": (re.compile(r"^(?P<name>.+?)\s+Jobs$", re.IGNORECASE),),
    "eightfold": _CAREERS_WRAPPER,
    "keka": _CAREERS_WRAPPER,
    "ripplehire": (re.compile(r"^(?P<name>.+?)\s+Careers\s*\|", re.IGNORECASE),),
    "lever": (re.compile(r"^(?P<name>.+)$"),),
}

#: A separator still present after the wrapper came off means the title had a shape this does not
#: model, and half a slogan is a worse company name than the slug. Mostly it bites **lever**,
#: whose pattern matches anything, so "Acme | Careers" reaches here and is refused — but not only
#: lever: `ripplehire:7-eleven-gsc` loses a real name to the `" - "` in "7 - Eleven" (ADR-0112
#: §Known misses). (An earlier version
#: of this comment cited eightfold's "Kraft Heinz Careers – Explore Careers…", which never gets
#: this far: no eightfold pattern matches it, so the loop below rejects it first.)
_SEPARATORS = ("|", "—", "–", " - ", "::")

#: Same idea as `_SEPARATORS`, for a wrapper word rather than a wrapper character. Every pattern
#: above strips one wrapper; text that *still* carries one means the title wore it twice, and what
#: is left is a page label, not a name.
#:
#: Three live shapes, found one at a time, each after the previous fix shipped:
#:   * trailing — `lever:destinationknot` serves "Destination Careers"
#:   * leading  — `keka:enpro` serves "Careers at Careers at Enpro Industries"
#:   * the whole string — `lever:schmidt-entities` serves "jobs", which reached 16 real Jobs as
#:     their company before this branch caught it
#: Those three exhaust the positions a token can occupy, so unlike the first two fixes this one
#: closes the set rather than adding the next case someone happens to find.
#:
#: Anchored rather than matching anywhere, because "Jobsoid" and "Careers24 Group" are names. It
#: costs recall: two in 1,519 live lever and keka Boards — `lever:pmaconsultants` ("PMA
#: Consultants Careers", 29 postings) and `lever:bananajobs` ("Banana Jobs") — keep their slug.
#: That is the deliberate trade. Stripping the word instead would turn "Destination Careers" into
#: "Destination", a confident wrong name, where refusing costs only a missed upgrade.
_PAGE_LABEL = re.compile(
    r"^careers?\s+at\s+|^(?:careers?|jobs?)$|\s(?:careers|jobs)$", re.IGNORECASE
)

#: Long enough for "Financial Software and Systems Ltd", short enough to reject a sentence — the
#: test pins both ends, against that name and the 70-character lever title that is a whole
#: sentence.
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
    "ashby": frozenset({"ashby", "ashbyhq"}),
    "eightfold": frozenset({"eightfold", "eightfoldai"}),
    "keka": frozenset({"keka"}),
    "lever": frozenset({"lever"}),
    "ripplehire": frozenset({"ripplehire"}),
}


#: A board that says out loud it is not a real employer. Reading titles is also a way of *finding*
#: the vendor tenants ADR-0034 exists to remove, and exactly two shapes were seen doing it: a
#: trailing marker ("ITC Infotech Demo") and the unfilled placeholder itself ("Your Company").
#: Both of those Boards are now blocklisted, so this rule guards the *next* one rather than any
#: Board live today — the blocklist is the real defence and this is the cheaper backstop.
#:
#: Anchored, and holding only the two observed shapes, for two reasons. "Sandbox VR" and "Test
#: Rite Group" are real employers that a rule matching these words anywhere refused. And a
#: trailing "sandbox"/"uat"/"qa" was never observed at all — an earlier draft carried them, plus a
#: comment citing a greenhouse board named literally "Test", which this rule can never see because
#: greenhouse has no ``board_page``.
#:
#: It cannot catch a QA tenant that titles itself after the company it imitates —
#: `ripplehire:tenant1-mph` served "Mphasis" — so that one went to the blocklist, which is the
#: only thing that can.
_PLACEHOLDER = re.compile(r"(?:\sdemo|^your\s+company)$", re.IGNORECASE)


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
    return " " not in text and bool(re.fullmatch(r"[a-z0-9][a-z0-9._/-]*", text))


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
    if any(separator in text for separator in _SEPARATORS) or _PAGE_LABEL.search(text):
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
    if re.sub(r"[^a-z]", "", text.lower()) in _VENDOR_ALIASES.get(ats, frozenset()):
        return None
    if _PLACEHOLDER.search(text):
        return None
    return text
