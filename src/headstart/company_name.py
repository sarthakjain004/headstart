"""The Board's company name, read from its board page rather than assumed from its slug.

`BaseScraper.__init__` does ``self.company = company or slug``, so a Board whose ledger row
carries no name serves its **slug** as the company. Measured on the served table 2026-09-07 that
is 150,626 of 318,003 rows — **47.4%** — and 100% of eight ATSes: users see "wipro", "1password",
"jobs.vodafone.com", "nttltd" where a company name belongs.

Four ATSes put the real name in their board page's ``<title>``, each wrapped differently, and one
request per Board recovers it. Which four is a measurement, not a guess: 30 live Boards were
sampled per ATS (`experiment/company-display-name/`), and only those where the wrapper is uniform
enough to strip safely are here.

===============  ==========================================  =====================
ATS              title shape                                 yields a name
===============  ==========================================  =====================
ashby            ``{Name} Jobs``                             28/30
eightfold        ``Careers at {Name}`` / ``{Name} Careers``  28/30
ripplehire       ``{Name} Careers | Latest jobs at …``       28/30
lever            ``{Name}`` — no wrapper at all              25/30
===============  ==========================================  =====================

**Deliberately absent, on the same evidence.** successfactors, keka, darwinbox and freshteam all
score **0/30**: successfactors' titles are heterogeneous marketing copy in several languages
("Life@MOHH - people, culture, and values | MOHH", "Trabaja en Volaris"), and the other three
render their board client-side and serve an empty ``<title>``. Workday is absent too, and for a
sharper reason: its board page is an empty SPA and neither its listing nor its detail response
carries a name at all — verified through the real scraper — while the public job page's JSON-LD
``hiringOrganization`` is the *per-posting legal entity*, so it varies within one Board and is
often worse than the slug ("nc" would become "Adult Correction", "coxhealth" would become "Skaggs
Community Hospital Association"). Guessing a name is worse than admitting we do not have one.

Every rule below rejects a shape that was actually observed. A title this cannot read leaves the
Board on its slug, which is exactly today's behaviour — this only ever replaces a slug with
something better, never with something worse.
"""

from __future__ import annotations

import html
import re

__all__ = ["PATTERNS", "from_title", "title_of"]

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
#: model — "Kraft Heinz Careers – Explore Careers. We're growing greatness." — and half a slogan
#: is a worse company name than the slug.
_SEPARATORS = ("|", "—", "–", " - ", "::")

#: Long enough for "Financial Software & Systems (P) Ltd.", short enough to reject a sentence.
_MAX_LEN = 60


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
    # A hostname — "webfx.com" — but only when written like one. The case test is what keeps
    # "Character.AI", a real company, out of this branch; matching case-insensitively dropped it.
    if text == text.lower() and re.fullmatch(r"[\w.-]+\.[a-z]{2,}", text):
        return None
    # Only an EXACT echo is worthless. Case and spacing are the whole point — "aida" becomes
    # "Aida", "1password" becomes "1Password" — so normalising before this comparison rejects
    # precisely the improvement being sought. It did: ashby scored 0/12 until this was narrowed.
    if text == slug:
        return None
    return text
