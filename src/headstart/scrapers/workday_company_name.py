"""A Workday Board's company name, voted from its postings' legal entities (ADR-0210).

Workday names no company in its listing, and its board page is a client-rendered shell whose
``og:title`` is the company on well under half the Boards that carry one ("Careers", "Job
Opportunities"). The posting **detail** does name one: a top-level ``hiringOrganization`` beside
``jobPostingInfo``, present on 140 of 140 Boards sampled live on 2026-09-24 and non-empty on 91.5%
of their details. It is the *legal entity* that posts the requisition, not the brand, and it varies
within a Board: nvidia states "2100 NVIDIA USA", "IN01 NVIDIA Graphics Bengaluru" and "IL00 Mellanox
Technologies, Ltd."; Airbus states nine entities; Northrop Grumman states division codes ("0090
CORP-Corporate Office", "0909 SP - Orbital Space Systems") and never the company.

So no single value is served. Three steps turn the Board's values into one name, or into none:

1. **Clean** each value: drop a "dba" prefix, a leading entity code ("2100", "QLYS_IN",
   "LE30006", "ADUS-"), a leading "The", and any trailing legal forms and country. A value that
   names an office rather than an employer ("Corporate Office", "Default") is dropped whole.
2. **Check** each cleaned value's longest leading run of words against what the Board says of
   itself: the run must appear as a proper noun in the board page's ``og:title`` or
   ``og:description`` (whose casing is kept, so "NVIDIA USA" is served as the page writes it), or
   have four or more of its letters inside the Board's own ``{tenant}/{site}``. A one-word run
   must not be generic ("Bank", "Health"), and no run ends on "of", "and", "&" or "the".
3. **Vote**: the top checked run wins if it covers 40% of the named postings, or is the only
   checked run there is. Airbus's nine entities all check to "Airbus"; Northrop's division codes
   check to nothing, and the Board gets no name here.

A Board the vote cannot name falls back to its page: an ``og:title`` in the "Careers at X" / "X
Careers" wrapper, then an ``og:description`` that opens "X is a…", "At X," or "About X". Every
candidate then passes `company_name.from_title`'s guards — no hostname, no page label, no
separator, no 60-character sentence.

Measured live over all 4,175 affected Boards (2026-09-24): 86% named. Of a seeded random 120 read
by hand, 89.5% of the named ones were correct, 9.5% partial ("CHG" for CHG Healthcare) and 1% wrong
(ADR-0210).

**Per site, never per tenant.** A tenant's sites often host different companies —
``volarisgroup`` runs a site per acquired business, ``humana``'s ``centerwell`` site hires for
CenterWell — so a name is resolved and cached for a ``{tenant}/{site}`` Board, never shared across
the tenant's sites.
"""

from __future__ import annotations

import csv
import functools
import html
import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from headstart import company_name

__all__ = ["RESOLVED_NAMES", "board_name", "clean", "resolved_name"]

#: The cascade's own answers, one ``board_key,name,source,checked_at`` row per Board it named,
#: written by ``scripts/validate/workday_company_names.py`` and committed. This is the per-Board
#: cache: a Board on file keeps its name from run to run and costs no board-page request.
RESOLVED_NAMES = Path("data/validate/company_names/workday.csv")

#: A leading entity code: digits (with hyphens: "20-2450790", or before one: "0090-"), an
#: uppercase code carrying digits ("US101", "IL00", "LE30006", ".IN1", "FR15450216965"), or
#: ``UPPER_UPPER`` ("QLYS_US"). A brand that starts with a digit is not a code: "3M" carries no
#: following space, and "7-Eleven" is a single digit hard against its hyphen.
_CODE = re.compile(
    r"^(?:(?:\d{2,}\s*|\d\s+)-\s*(?=[A-Za-z])"
    r"|(?:\d[\d\-.]*|[.A-Z_]*[A-Z_]\d+[A-Z0-9_.]*|[A-Z]{2,}_[A-Z]{2,})\s+)"
)
#: A short uppercase code hyphened onto the name: "ADUS-Adobe", "CFC- Chatham Financial",
#: "TLSM - TRUMPF Lasersystems".
_HYPHEN_CODE = re.compile(r"^[A-Z]{2,6}\s*-\s*(?=[A-Z])")
#: A word that is itself a code, left in front of the name: "AMC OU Ambarella", "SII Saulsbury",
#: "embIND Embitel". The run a Board vouches for may start after any leading run of these.
_CODE_WORD = re.compile(r"[A-Z0-9_.&]{2,}|[a-z]{2,4}[A-Z]{2,4}")
_LEGAL = re.compile(
    r"(?:[\s,]+(?:inc|incorporated|llc|l\.l\.c|llp|ltd|limited|plc|corp|corporation|co|company"
    r"|gmbh|ag|sa|s\.a|sas|sau|s\.a\.u|s\.p\.a|spa|s\.r\.l|srl|bv|b\.v|nv|n\.v|ab|aps|as|a/s|oy"
    r"|kft|pty|pvt|private|pte|ulc|lp|l\.p|sdn\.? bhd|bhd|sociedad de responsabilidad limitada"
    r"|s\.? de r\.?l\.? de c\.?v|de c\.?v|s de rl de cv|sp\.? z o\.? ?o|k\.?k|gk|ltda|s\.a\.s"
    r"|legal entity|holdings?|group holdings)\.?)+\s*$",
    re.IGNORECASE,
)
#: A trailing country, set off by a comma, a bracket, a spaced hyphen or a space — never a hyphen
#: inside a name ("CBC/Radio-Canada") or a country "of" names ("Royal Bank of Canada").
_COUNTRY = re.compile(
    r"(?<!\sof)(?:(?:\s*[_,(]\s*|\s+-\s+|\s+)(?:united states|usa|u\.s\.?|us|uk|india|canada|italy"
    r"|germany|france|singapore|china|japan|mexico|ireland|australia|philippines)\)?)+\s*$",
    re.IGNORECASE,
)
#: A value naming an office or a placeholder, not an employer.
_NOT_AN_EMPLOYER = re.compile(
    r"\b(?:default|corporate office|legal entity|headquarters|inactive|dba)\b|^corp\b",
    re.IGNORECASE,
)

#: Words that name no company on their own, so a run made only of them is refused.
_GENERIC = frozenset(
    {
        "and", "of", "the", "&", "group", "international", "us", "services", "solutions",
        "technologies", "technology", "global", "company", "systems", "holdings", "management",
        "operations", "inc", "llc", "ltd", "corp", "careers", "jobs", "america", "north",
    }
)  # fmt: skip
#: Words that are a real part of many names but never a name alone ("First" Bank, "State" Farm).
_GENERIC_ALONE = frozenset(
    {
        "state", "bank", "university", "college", "health", "healthcare", "care", "city",
        "county", "department", "hospital", "medical", "energy", "media", "electric",
        "construction", "school", "first", "national", "american", "general", "united",
    }
)  # fmt: skip
#: A run ending on one of these is half a name ("Bank of", "Johnson &").
_DANGLING = frozenset({"of", "and", "&", "for", "the", "de"})

#: The share of named postings the winning run must cover when others compete.
_MIN_SHARE = 0.4

#: "X is a…", "At X,", "About X" — an ``og:description`` that opens by naming its company.
_DESCRIPTION_OPENERS = (
    re.compile(
        r"^(?:(?i:About(?: Us)?):?\s+)?(?P<n>[A-Z][\w&.'\-]*(?:\s+[A-Z&][\w&.'\-]*){0,4}?)"
        r"(?:,? Inc\.?| \([^)]*\))?\s+(?:is|are|has been|was)\s+"
        r"(?:a|an|the|one|committed|proud|transforming|defining|bringing)\b"
    ),
    re.compile(r"^At\s+(?P<n>[A-Z][\w&.'\-]*(?:\s+[A-Z&][\w&.'\-]*){0,4}?)\s*,"),
)
#: What an opener catches when the description is about the reader, not the company.
_NOT_A_SUBJECT = frozenset({"We", "Our", "This", "It"})


def resolved_name(board_key: str) -> str | None:
    """This Board's name in the committed cache, or None.

    Matched case-insensitively, because the ledger spells one Workday site more than one way
    (``/External`` and ``/external``) and both are the same Board. A curated name
    (`company_name.curated`) is not read here: `BaseScraper.fetch` applies it before
    `resolve_company` runs, and skips that call altogether.
    """
    return _names_in(RESOLVED_NAMES).get(board_key.lower())


@functools.cache
def _names_in(path: Path) -> dict[str, str]:
    """``board_key -> name`` from the cache file, read once; empty when it is absent.

    A relative path resolves against the repo root, which every pipeline job installs editable.
    """
    full = Path(__file__).resolve().parents[3] / path
    try:
        with full.open(newline="", encoding="utf-8") as handle:
            return {
                row["board_key"].lower(): row["name"].strip()
                for row in csv.DictReader(handle)
                if (row.get("name") or "").strip()
            }
    except OSError:
        return {}


def _og(page: str | None, prop: str) -> str | None:
    """The ``content`` of the page's ``<meta property="og:{prop}">``, entity-decoded, or None."""
    match = re.search(
        r'<meta[^>]*property="og:' + re.escape(prop) + r'"[^>]*content="([^"]*)"',
        page or "",
    )
    if not match:
        return None
    return html.unescape(match.group(1)).strip() or None


def clean(entity: str | None) -> str:
    """One posting's ``hiringOrganization.name`` with its codes, legal forms and country removed.

    "2100 NVIDIA USA" -> "NVIDIA", "QLYS_IN Qualys Security TechServices Private Ltd." ->
    "Qualys Security TechServices". An empty string means nothing is left to read.
    """
    text = (entity or "").replace("\xa0", " ").strip()
    text = re.sub(r"^.*\bd/?b/?a\b\.?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+Legal Entity$", "", text, flags=re.IGNORECASE)
    text = re.sub(r",?\s+inc\.?-federal$", "", text, flags=re.IGNORECASE)
    text = _CODE.sub("", text, count=1).strip()
    text = _HYPHEN_CODE.sub("", text)  # after `_CODE`: "122 TLSM - TRUMPF" carries both
    text = re.sub(r"^The\s+", "", text.replace("_", " "))
    for _ in range(3):  # "Acme Holdings Ltd. (UK)" peels in more than one pass
        peeled = _LEGAL.sub("", _COUNTRY.sub("", text).strip()).strip(" ,.-_")
        if peeled == text:
            break
        text = peeled
    return text


def _checked_run(name: str, prose: str, board_letters: str) -> str | None:
    """The longest leading run of ``name``'s words the Board itself vouches for, or None.

    Vouching means the prose states the run as a proper noun — a capital or a digit in the prose's
    own spelling ("8x8"), which is what is returned — or
    four or more of the run's letters sit inside the Board's ``{tenant}/{site}``. The run may start
    after words that are themselves codes the cleaning left ("AMC OU Ambarella"). A legal form the
    prose wrote after the name ("U-Haul co") is dropped from what is returned.
    """
    words = name.split()
    codes = 0
    while codes < len(words) - 1 and _CODE_WORD.fullmatch(words[codes]):
        codes += 1
    starts = range(codes + 1)
    for length in range(len(words), 0, -1):
        for start in starts:
            span = words[start : start + length]
            if len(span) < length:
                continue
            lowered = [w.lower().strip(",.") for w in span]
            if length == 1 and lowered[0] in _GENERIC_ALONE | _GENERIC:
                continue
            if all(w in _GENERIC for w in lowered) or lowered[-1] in _DANGLING:
                continue
            phrase = " ".join(span).strip(" ,.")
            if len(phrase) < 2 or not any(c.isupper() or c.isdigit() for c in phrase):
                continue
            for match in re.finditer(
                r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", prose, re.IGNORECASE
            ):
                if any(c.isupper() or c.isdigit() for c in match.group(0)):
                    return _LEGAL.sub("", match.group(0))
            letters = re.sub(r"[^a-z]", "", phrase.lower())
            if len(letters) >= 4 and letters in board_letters:
                return phrase
    return None


def _voted(entities: Iterable[str | None], prose: str, board: str) -> str | None:
    named = [clean(e) for e in entities if e]
    named = [n for n in named if n and not _NOT_AN_EMPLOYER.search(n)]
    if not named:
        return None
    board_letters = re.sub(r"[^a-z]", "", board.lower())
    # Keyed on letters and digits alone, so "Walmart" and "Wal-Mart" are one vote, not two.
    votes: Counter[str] = Counter()
    forms: dict[str, Counter[str]] = {}
    for name in named:
        run = _checked_run(name, prose, board_letters)
        if run and not _NOT_AN_EMPLOYER.search(run):
            key = re.sub(r"[^a-z0-9]", "", run.lower())
            votes[key] += 1
            forms.setdefault(key, Counter())[run] += 1
    if not votes:
        return None
    top, count = votes.most_common(1)[0]
    if count / len(named) >= _MIN_SHARE or len(votes) == 1:
        return forms[top].most_common(1)[0][0]
    return None


def _wrapped_title(title: str | None) -> str | None:
    # The same "Careers at X" / "X Careers" wrapper eightfold, gem, jobvite and keka titles wear.
    for pattern in company_name._CAREERS_WRAPPER:
        match = pattern.match(title or "")
        if match:
            return match.group("name").strip()
    return None


def _description_subject(description: str | None) -> str | None:
    text = (description or "").strip()
    for pattern in _DESCRIPTION_OPENERS:
        match = pattern.search(text)
        if match:
            words = match.group("n").split()
            if words[0] in _NOT_A_SUBJECT:
                return None
            half = len(words) // 2
            if len(words) % 2 == 0 and words[:half] == words[half:]:  # "Leidos Leidos"
                words = words[:half]
            return " ".join(words)
    return None


def board_name(
    entities: Iterable[str | None], page: str | None, board: str
) -> tuple[str | None, str]:
    """This Board's company name and the step that found it, or ``(None, "none")``.

    ``entities`` are its postings' ``hiringOrganization.name`` values (None where a detail had
    none), ``page`` the board page's HTML (None if it was not fetched), and ``board`` the
    Board's ``{tenant}/{site}``. The steps run in the module docstring's order; a candidate the
    `company_name` guards refuse passes to the next step rather than ending the search.
    """
    title, description = _og(page, "title"), _og(page, "description")
    prose = f"{title or ''}\n{description or ''}"
    steps = (
        ("hiringOrganization", lambda: _voted(entities, prose, board)),
        ("og:title", lambda: _wrapped_title(title)),
        ("og:description", lambda: _description_subject(description)),
    )
    for source, step in steps:
        name = company_name.from_title("workday", step(), board)
        if name:
            return name, source
    return None, "none"
