"""What a job description says about visa sponsorship and relocation, read by rules (ADR-0333).

A job seeker who needs a visa, or is moving country, asks "which jobs sponsor visas" and "which
offer relocation". A keyword search cannot answer it: a description that says "sponsorship" says
so mostly to refuse it. Of 40 descriptions matching the keyword "sponsorship" over US software
roles (round-3 critique, 2026-09-29), about 36 refused it and 2 offered it. The polarity is in
the sentence, next to the word:

* "Visa sponsorship is not available", "without the need for sponsorship now or in the future",
  "VISA Sponsorship: No", "U.S. citizenship is required": a job a visa holder cannot get.
* "Visa sponsorship available", "we sponsor visas", "H-1B sponsorship is available for eligible
  candidates": a job that offers it.
* "executive sponsor", "company-sponsored benefits", "sponsor for a security clearance", "Visa"
  the card network, "willing to relocate" (a demand on the candidate, not an offer), "water main
  relocation": not about either.

:func:`stances` reads each mention in a window cut to its own clause, and names the text-derived
**stances** it finds (:data:`STANCES`). A description that both offers and refuses sponsorship
(one location sponsors, another does not) is read as refusing it: a job is only said to offer
sponsorship when nothing in its text refuses it. :func:`mentions` quotes the sentences a reader
would need to judge it themselves.

An offer is read against the job it is posted on (ADR-0353). One hedged ("Sponsorship for this
role is not guaranteed", "may be available … on a case-by-case basis") is the weaker stance
:data:`MAY_OFFER_SPONSORSHIP`. One scoped to a named country ("We can sponsor visas to Germany")
or to named levels ("offered exclusively for Principal-level roles and above") is judged against
the job's ``location`` and ``title``: in scope it stands; out of scope it offers nothing, and
refuses when it says the scope is the only one ("only", "exclusively"); where the job's country or
level cannot be read, it may offer.

These are rules over scraped text, with the error rates ADR-0333 measured on a hand-labelled
sample; they are not the employer's structured answer, and an answer that serves them says so.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from enum import Enum, StrEnum

from headstart.search_filters import country_gazetteer

#: The stances :func:`stances` can find, in the order an answer names them. A description holds
#: at most one of the three sponsorship stances.
OFFERS_SPONSORSHIP = "offers_sponsorship"
MAY_OFFER_SPONSORSHIP = "may_offer_sponsorship"
REFUSES_SPONSORSHIP = "refuses_sponsorship"
OFFERS_RELOCATION = "offers_relocation"
STANCES = (
    OFFERS_SPONSORSHIP,
    MAY_OFFER_SPONSORSHIP,
    REFUSES_SPONSORSHIP,
    OFFERS_RELOCATION,
)

#: What every sentence a rule or :func:`mentions` reads contains. The serving side reads only
#: the descriptions this matches (Rust's regex engine runs it, so it keeps to syntax both
#: engines share), and every sentence read is found by it, so no rule can see past it.
PREFILTER = (
    r"(?i)sponsor|visa|reloca|citizen|immigra|work authori[sz]|work permit|"
    r"authori[sz]ed to work|eligible to work|right to work|h-?1-?b|permanent resident|"
    r"(?:u\.?\s?s\.?|united states|american|british|uk|canadian|australian)\s+"
    r"(?:nationals?|persons?)"
)
_TOPIC = re.compile(PREFILTER)
# A literal inside every PREFILTER match, lower-cased.
_TOPIC_LITERALS = (
    "sponsor",
    "visa",
    "reloca",
    "citizen",
    "immigra",
    "authori",
    "permit",
    "eligible to work",
    "right to work",
    "h1b",
    "h-1b",
    "h1-b",
    "h-1-b",
    "permanent resident",
    "person",
    "national",
)

# Where one sentence or list item ends. Descriptions arrive with their markup stripped, so a
# bullet list is often one long run of text; a bullet mark ends an item as a full stop would.
# A full stop after an initial ("U.S. citizen", "e.g. H-1B") ends nothing, and neither does a
# question a form answers in the next word ("Is Sponsorship Available? No").
_SENTENCE_END = re.compile(
    r"(?:(?<=[.!;])(?<!\b[A-Za-z]\.)(?<!\.[A-Za-z]\.)|(?<=\?)(?!\s*(?:yes|no|n/a|none)\b))\s+"
    r"|\n+|\s[•·▪●■◦]\s|\s[-–—]\s",
    re.IGNORECASE,
)

# What ends a mention's window before the sentence does: another field of a run-on key-value
# block ("Relocation: Domestic VISA Sponsorship: No Travel: Yes") or the other topic.
_FIELD_BREAK = re.compile(
    r"(?i)\b(?:travel|shift|schedule|telework|clearance|position type|referral|location|"
    r"employee (?:type|status)|job posting|time type|hazardous|valid driving|salary|"
    r"flexible work|remote working|posting end)\b"
)

# A field label's end ("Education: ", "Available? "), and the next label a run-on block starts.
_LABEL_END = re.compile(r"[:?]\s")
_OWN_LABEL = re.compile(r"(?:\s+[A-Za-z-]+){0,2}?\s*[:?](?=\s|$)")
_FIRST_WORD = re.compile(r"\s*\S+")
_NEXT_LABEL = re.compile(r"(?:\b[A-Z][\w/()&-]*\s+){0,4}[A-Z][\w/()&-]*\s?[:?](?=\s)")

# How far a window reaches either side of its word, in characters, within its clause.
_REACH = 70

_NEGATION = re.compile(
    r"(?i)(?:\b(?:not|no|non|none|never|neither|nor|without|unable|cannot|can ?not|"
    r"unavailable|ineligible|n/a|disqualif\w*)\b|n['’]t\b)"
)

# -- sponsorship -----------------------------------------------------------------------------

# A mention of visa sponsorship: a sponsor word, or visa or work-authorisation help. "Visa" in
# title case is the card network ("support for Visa staff"), so help with one is read only
# in lower or upper case.
_VISA_HELPED = r"(?:(?-i:\b(?:visas?|VISAS?)\b)|\bwork authori[sz]ation|\bwork permits?|\bimmigration)"
_SPONSOR = re.compile(
    r"(?i)\bsponsor(?:ship|ing|ed|s)?\b|"
    + _VISA_HELPED
    + r"\s+(?:\w+\s+){0,2}?(?:support|assistance|help|transfers?|applications?|costs?|fees?)\b|"
    r"\b(?:support|assistance|help|assist)\s+(?:\w+\s+){0,4}?"
    + _VISA_HELPED
    # Immigration as a product's domain ("support immigration and border security operations").
    + r"(?!\s+(?:eligibility|requirements?|status|information|rules|categor|and border|"
    r"processing|systems?|operations))"
)

# A sponsor verb ("sponsor", "sponsoring") means a visa only when one of these is near it; the
# noun "sponsorship" and visa help need nothing more.
_VISA_CONTEXT = re.compile(
    r"(?i)\bvisas?\b|immigra|\bh-?1-?b\b|\bopt\b|\bcpt\b|green card|permanent resident|"
    r"\bwork(?:ing)? (?:permit|authori[sz]ation|status|eligibility|pass|visa)|"
    r"employment (?:authori[sz]ation|visa|based|pass|eligibility)|authori[sz]ed to work|"
    r"eligib(?:le|ility) to work|right to work|legally|in the future|\bcitizen|residency|"
    r"sponsor(?:ing)? (?:candidates|applicants|new applicants|a new|individuals|anyone|foreign|"
    r"international)|sponsorship\b|(?:visa|work authori[sz]ation|work permits?|immigration)\b"
)

# A sponsor word that is about something else, whatever is near it.
_NOT_A_VISA = re.compile(
    r"(?i)(?:executive|business|project|program|agency|platform|clinical|trial|study|"
    r"corporate|event|brand|media|title|stakeholder|senior|key|internal)\s+sponsors?\b|"
    r"sponsored\s+(?:benefits|health|medical|insurance|plans?|401|retirement|programs?|"
    r"events?|training|certifications?|education|coursework|wellness|group|life|"
    r"childcare|pension|activities|social|hackathons?|teams?|clubs?|english)|"
    r"company[- ]sponsored|employer[- ]sponsored|sponsorships\b|"
    r"sponsorship (?:for|of|towards?) (?:\w+ )?(?:industry|certif|training|education|events?|"
    r"conferences?|professional|courses?|exams?|degrees?|an export)|"
    r"build(?:s|ing)? sponsorship|sponsorship (?:and|&) (?:influence|buy-in|alignment)|"
    r"(?:training|funded|study|education|certification|sales|partnership|clearance|athletic|"
    r"sports?|assistance|charity|community|tuition|scholarship)\s+sponsorship|"
    r"sponsor(?:ship)? (?:for|of) (?:a |an |your )?(?:security )?clearance"
)

# A clearance's sponsor is not a visa's, unless a visa is named too.
_CLEARANCE = re.compile(r"(?i)clearance|polygraph")
_VISA_NAMED = re.compile(
    r"(?i)\bvisas?\b|immigra|\bh-?1-?b\b|work (?:permit|authori[sz]ation)|"
    r"employment (?:authori[sz]ation|visa)"
)

# Words that make a sponsorship mention a refusal even without a negation: whoever will need it
# "now or in the future" is the candidate being turned away.
_REFUSAL = re.compile(
    r"(?i)now or in the future|or in the future|current or future|future (?:\S+ )?sponsorship|"
    r"at any time in the future"
)
# Work authorisation demanded, not helped with: "to support essential … therefore EU work
# authorisation and eligibility required" is no offer, whatever "support" is near it.
_AUTHORIZATION_REQUIRED = re.compile(
    r"(?i)work authori[sz]ation(?: and eligibility)? (?:is )?required"
)

# A job that asks for work authorisation the candidate already holds refuses one who needs a
# visa, sponsor word or not: "for this role, applicants must be currently authorized to work",
# "You must currently possess valid and unrestricted U.S. work authorization" (ADR-0353). Without
# "currently" or "already" the demand is the usual one an offer sits beside ("must be legally
# authorized to work in the United States. Visa sponsorship is available").
_ALREADY_AUTHORIZED = re.compile(
    r"(?i)\b(?:must|needs? to|should|required to)\s+(?:be\s+)?(?:currently|already)\s+"
    r"(?:be\s+)?(?:legally\s+)?(?:authori[sz]ed to work|(?:hold|have|possess)\b[^;]{0,40}?"
    r"(?:work authori[sz]ation|right to work|work permit))|"
    # "You must have a valid NZ work visa for your application to be considered."
    r"\bmust (?:have|hold|possess) (?:a |an )?(?:valid|current)\b[^;]{0,30}?"
    r"(?:visa|work permit|work authori[sz]ation)"
)

# "Sponsorship is not guaranteed", "decided case by case": offered to some, a negation that is not
# a refusal. Read before the negation is.
_HEDGE = re.compile(
    r"(?i)\b(?:is not|isn't|not) guaranteed|\bcase[- ]by[- ]case\b|"
    r"\b(?:should|must) not be assumed|\bnot (?:all|every) (?:positions?|roles?|jobs?)\b|"
    r"\bnot (?:always|typically)\b|\bfor every (?:role|position|candidate)\b"
)
# An offer said with a hedge: it may be made, not that it is ("Visa sponsorship may be available
# for select positions", "we may sponsor").
_MAY = re.compile(
    r"(?i)\bmay (?:be |also )?(?:available|offered|provided|possible|considered|consider|"
    r"sponsor|support|offer|provide)|\bmight\b|\bcould be\b|\bpotential(?:ly)?\b|"
    r"\b(?:select|certain|some|limited) (?:positions|roles|cases|circumstances)\b|"
    r"\bdepend(?:s|ing|ent)? (?:on|upon)\b|\bdiscretion\b|"
    r"\bin (?:some|rare|exceptional) cases\b|\bexceptional (?:cases|circumstances)\b|"
    # An offer for a move the job does not need, or one made later ("If you wish to relocate, we
    # are happy to help you obtain a visa", "for a move to NYC (after 2 years' tenure)").
    r"\bif you (?:later )?(?:wish|choose|decide|want|opt) to relocate\b|"
    r"\bafter (?:\d+|one|two|three) (?:years?|months?)\b"
)

# -- an offer's scope (ADR-0353) --

# What makes a scope the only one: out of it, the job is refused, not merely not offered.
_ONLY = re.compile(
    r"(?i)\b(?:only|exclusively|solely|limited to|restricted to|reserved for)\b"
)

# A country an offer is scoped to, named in prose: every gazetteer country's English name, and
# the abbreviations a sentence writes. A two-letter abbreviation counts only in capitals ("US",
# never "us"). Names that are also a US state or a person's name (Georgia, Jordan) are left out.
_PROSE_COUNTRY_NAMES = {
    code: country.name
    for code, country in country_gazetteer.COUNTRIES.items()
    if country.name not in {"Georgia", "Jordan"}
} | {"IN": "India"}
_PROSE_COUNTRY_ALIASES = {
    "the Netherlands": "NL",
    "Holland": "NL",
    "Britain": "GB",
    "Great Britain": "GB",
    "England": "GB",
    "Scotland": "GB",
    "Deutschland": "DE",
    "America": "US",
    "UAE": "AE",
}
# The European Union as an offer's scope ("candidates already located in a UK/EU country").
# fmt: off
_EU = frozenset({
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT",
    "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
})
# fmt: on
_PROSE_COUNTRY = re.compile(
    r"(?i:\b(?:"
    + "|".join(
        re.escape(name)
        for name in sorted(
            {*_PROSE_COUNTRY_NAMES.values(), *_PROSE_COUNTRY_ALIASES, "European Union"},
            key=len,
            reverse=True,
        )
    )
    + r")\b)|\b(?:U\.S\.A?\.?|U\.K\.|USA|US|UK|EU|EEA)(?![A-Za-z])"
)
_CODES_BY_NAME = (
    {name.lower(): frozenset({code}) for code, name in _PROSE_COUNTRY_NAMES.items()}
    | {
        alias.lower(): frozenset({code})
        for alias, code in _PROSE_COUNTRY_ALIASES.items()
    }
    | {
        "us": frozenset({"US"}),
        "usa": frozenset({"US"}),
        "uk": frozenset({"GB"}),
        "eu": _EU,
        "eea": _EU | {"NO"},
        "european union": _EU,
    }
)


# A country named as where the candidate is or comes from, not where the job is: "If you are
# outside Canada, we support … immigration", "citizens of the EU".
_NOT_THE_SCOPE = re.compile(
    r"(?i)(?:\b(?:outside|from|beyond|except|other than|citizens? of|nationals? of|"
    r"residents? of|based outside)(?:\s+(?:of|the))*\s*)$"
)


def _named_countries(window: str) -> frozenset[str]:
    """The countries ``window`` names as an offer's scope, as gazetteer codes."""
    codes = set()
    for match in _PROSE_COUNTRY.finditer(window):
        if _NOT_THE_SCOPE.search(window, 0, match.start()):
            continue
        codes |= _CODES_BY_NAME[match.group().replace(".", "").lower()]
    return frozenset(codes)


# Levels by rank, as a title or a scoped offer names them. A title naming none of these ranks as
# an engineer with no level word (2); a manager's rank is not read, since ladders place it apart.
_LEVELS = (
    (r"intern(?:ship)?s?|co-?op", 0),
    (r"junior|jr\.?|entry|graduate|grad|associate", 1),
    (r"mid|intermediate", 2),
    (r"senior|sr\.?", 3),
    (r"staff|lead", 4),
    (r"principal", 5),
    (r"director|distinguished|fellow|vp|vice president|head|chief|executive", 6),
)
_LEVEL_WORD = re.compile(
    r"(?i)\b(?:"
    + "|".join(f"(?P<l{rank}>{words})" for words, rank in _LEVELS)
    + r")(?!\w)"
)
_MANAGER = re.compile(r"(?i)\bmanag(?:er|ement)\b")
# A level an offer is limited to: "Principal-level roles and above", "senior positions only",
# "for roles at the Staff level or higher".
_LEVEL_SCOPE = re.compile(
    r"(?i)\b(?:"
    + "|".join(f"(?P<l{rank}>{words})" for words, rank in _LEVELS)
    + r")(?:[- ]level)?\s+(?:level\s+)?(?:(?:and|or) (?:above|higher)|\+|"
    r"(?:roles?|positions?|jobs?|hires|candidates|employees)(?:\s+(?:and|or) (?:above|higher))?)"
)


def _rank(match: re.Match[str]) -> int:
    """The rank of the level word ``match`` (of :data:`_LEVEL_WORD` or :data:`_LEVEL_SCOPE`)
    found: the one ``l{rank}`` group of it that matched."""
    return min(
        int(name[1:])
        for name, value in match.groupdict().items()
        if value and name[1:].isdigit()
    )


def _title_rank(title: str | None) -> int | None:
    """The rank of the highest level ``title`` names, 2 when it names none, None for a manager's
    title or no title."""
    if not title:
        return None
    ranks = [_rank(m) for m in _LEVEL_WORD.finditer(title)]
    if _MANAGER.search(title) and not any(rank >= 5 for rank in ranks):
        return None
    return max(ranks, default=2)


class _Scope(StrEnum):
    """Whether an offer covers the job (:func:`_scoped`)."""

    IN = "in"
    #: It offers nothing here.
    OUT = "out"
    #: It is the only scope, and the job is outside it.
    REFUSED = "refused"
    #: A scope is named but the job's country or level cannot be read.
    UNKNOWN = "unknown"


class _Tier(Enum):
    """How firmly a mention offers sponsorship."""

    OFFERS = "offers"
    MAY = "may"


def _scoped(window: str, *, title: str | None, location: str | None) -> _Scope:
    """Whether an offer read in ``window`` covers the job titled ``title`` at ``location``. An
    offer naming no scope is :attr:`_Scope.IN`."""
    verdicts = []
    if named := _named_countries(window):
        countries = country_gazetteer.classify(location)
        verdicts.append(
            _Scope.UNKNOWN
            if not countries
            else _Scope.IN
            if named & countries
            else _Scope.OUT
        )
    if level := _LEVEL_SCOPE.search(window):
        rank = _title_rank(title)
        # A level named is a limit whether or not "only" says so: why else name it.
        if rank is not None and rank < _rank(level):
            return _Scope.REFUSED
        verdicts.append(_Scope.UNKNOWN if rank is None else _Scope.IN)
    if _Scope.OUT in verdicts:
        return _Scope.REFUSED if _ONLY.search(window) else _Scope.OUT
    return _Scope.UNKNOWN if _Scope.UNKNOWN in verdicts else _Scope.IN


# An offer must name what it sponsors: "sponsorship" alone is often a sales or mentoring word
# ("executive sponsorship", "horizontal sponsorship"), and an offer read wrongly is the costly
# error (ADR-0333). A refusal needs no such word: "not eligible for sponsorship" is a visa's.
_OFFER_NAMES_A_VISA = re.compile(
    r"(?i)\bvisas?\b|immigra|\bh-?1-?b\b|\bopt\b|\bcpt\b|green card|work permit|"
    r"work(?:ing)? (?:authori[sz]ation|status|eligibility|pass|visa)|employment "
    r"(?:authori[sz]ation|visa|based|pass)|authori[sz]ed to work|eligible to work|"
    r"right to work|sponsor(?:ing)? (?:a )?(?:new )?(?:qualified )?(?:candidates|applicants|"
    r"applicant|international|foreign)"
)
# A field answering yes ("Sponsorship: Yes", "Is Sponsorship Available? Yes") names its topic.
_FIELD_YES = re.compile(r"(?i)sponsor\w*:\s*(?:yes|available|offered|provided)\b")
# "Sponsorship for this role is not guaranteed" offers it to some; one "for future roles" does not
# speak of this job at all.
_FOR_LATER = re.compile(r"(?i)\bfuture\b")

# Sponsorship stated as available: a word of offering near a sponsor word no negation reaches.
_OFFER = re.compile(
    r"(?i)\b(?:available|offer(?:s|ed|ing)?|provide[sd]?\b(?!\s+(?:proof|documentation|evidence))|"
    r"providing|support(?:s|ed|ing)?|"
    r"assist(?:ance|s)?|help|yes|possible|considered|consider sponsoring|eligible for|"
    r"welcome[sd]?|open to|cover(?:s|ed)?|pay(?:s|ing)?|will sponsor|can sponsor|"
    r"may sponsor|do sponsor|does sponsor|we sponsor|able to sponsor|willing to sponsor|"
    r"happy to sponsor|sponsors? (?:visas?|work|h-?1-?b|international|employment|immigration|"
    r"qualified|eligible))\b"
)

# A citizenship requirement: a visa holder cannot meet it. A sentence that names citizenship as
# a protected trait ("without regard to ... citizenship") is not one.
_CITIZENSHIP = re.compile(
    r"(?i)\b(?:u\.?\s?s\.?|united states|american|british|uk|canadian|australian)\s+"
    r"(?:citizen(?:s|ship)?|nationals?|persons?)\b|\bcitizenship\b|\bcitizens\s+only\b"
)
_REQUIRED = re.compile(
    r"(?i)\b(?:must|shall|require[sd]?|requirements?|required|mandatory|necessary|needed|"
    r"only|include[sd]?|restricted to|limited to|eligib\w*)\b"
)
_NOT_REQUIRED = re.compile(
    r"(?i)\b(?:not|isn't|no longer)\s+(?:be\s+)?(?:required|a requirement|necessary)"
)
# A citizenship requirement some other positions carry ("Some positions will require current U.S.
# Citizenship"): not a refusal of this job (ADR-0353).
_SOME_POSITIONS = re.compile(
    r"(?i)\b(?:some|certain|select|specific|many|most) (?:of (?:our|the) )?(?:positions|roles|jobs|"
    r"programs|projects|contracts|customers|assignments)\b|\bmay (?:also )?(?:be )?require|"
    r"\bmay be limited\b"
)
# Citizenship named, not required: an agency ("U.S. Citizenship and Immigration Services"), a field
# label ("U.S. Citizen, U.S. Person, or Immigration Status Requirements: The company will offer
# immigration sponsorship"), a definition ("A U.S. person according to their definition is a U.S.
# citizen").
_CITIZENSHIP_NAMED = re.compile(
    r"(?i)citizenship and immigration services|or immigration status requirements|"
    r"according to (?:their|the|its) definition"
)
_PROTECTED_TRAIT = re.compile(
    r"(?i)regard to|without regard|protected|discriminat|national origin|equal opportunity|"
    r"may be contingent|ability to obtain prior"
)


# -- relocation ------------------------------------------------------------------------------

_RELOCATION = re.compile(r"(?i)\breloca\w*")

# A move the candidate must make, or a thing being moved: not an offer to help.
_ASKED_TO_MOVE = re.compile(
    r"(?i)(?:willing|able|open|ready|prepared|need|needs|required|must|wanting|want|intend|"
    r"plan|planning|interested|happy|expected|expect|agree|considering|and/or)\s+(?:to\s+)?"
    r"(?:be\s+)?relocat"
)
_THING_MOVED = re.compile(
    r"(?i)(?:main|mains|equipment|office|site|utility|utilities|data ?cent(?:er|re)|system|server|"
    r"pipe|line|facility|store|asset|installation|plant|lab|service|workload|application|"
    r"road|traffic|tower|meter|cable|move)s?\s+relocations?|"
    r"relocat\w*\s+(?:of|the)\s+(?:equipment|systems?|servers?|utilit|lines?|facilit|"
    r"offices?|assets?|data|infrastructure|workloads?|pipes?|mains?)|"
    r"(?:removals?|installations?)\s+(?:and|or)\s+relocation|"
    r"(?:office|site)\s+(?:build-?out|move)"
)

# Relocation the candidate pays for.
_AT_OWN_COST = re.compile(
    r"(?i)\bliable\b|your own|own (?:cost|expense)|not negotiable"
)

_RELOCATION_OFFER = re.compile(
    r"(?i)reloca\w*\s*[:\-–]?\s*(?:\w+\s+){0,3}?(?:assistance|support|supported|benefits?|package|"
    r"reimburse\w*|stipend|allowance|bonus|expenses?|help|budget|costs?|provided|available|"
    r"offered|authori[sz]ed|eligible|eligibility|paid|covered|program|lump)|"
    r"reloca\w*\s*[:\-–]\s*(?:yes|domestic|national|international|provided|available|"
    r"may be|offered|authori[sz]ed|eligible|supported)|"
    r"(?:offer|offers|offering|provide|provides|providing|include|includes|including|plus|"
    r"eligible for|support|supports|supporting|help|helps|assist|assists|assisting|"
    r"assistance with|"
    r"cover|covers|pay for|paid|generous|full)\s+(?:\w+\s+){0,3}?reloca|"
    r"open to relocating (?:candidates|you)|we (?:will|can) (?:help|assist) (?:you )?relocat|"
    r"who relocate for this position"
)


# How far either side of a topic word its sentence is looked for. A rule reads no more than
# :data:`_REACH` characters either side of its word, so a longer sentence cut here reads the same.
_SENTENCE_REACH = 600


def _topic_words(text: str) -> list[re.Match[str]]:
    """Every :data:`PREFILTER` match in ``text``, in order. Found from :data:`_TOPIC_LITERALS`
    by ``str.find`` and confirmed by the pattern around each: the pattern alone, run over a whole
    description, cost three times what every rule after it does (measured on 300 descriptions)."""
    low = text.lower()
    hits: set[int] = set()
    for literal in _TOPIC_LITERALS:
        at = low.find(literal)
        while at != -1:
            hits.add(at)
            at = low.find(literal, at + 1)
    found: dict[int, re.Match[str]] = {}
    for at in sorted(hits):
        for match in _TOPIC.finditer(text, max(0, at - 30), at + 30):
            if match.start() <= at < match.end():
                found.setdefault(match.start(), match)
    return [found[start] for start in sorted(found)]


def _sentences(text: str) -> list[str]:
    """The sentences of ``text`` holding a :data:`PREFILTER` word, each once, in order: only the
    text around each such word is split, so a long description costs little more than a short
    one."""
    sentences: list[str] = []
    covered = 0
    for match in _topic_words(text):
        if match.start() < covered:
            continue
        low = max(0, match.start() - _SENTENCE_REACH)
        high = min(len(text), match.end() + _SENTENCE_REACH)
        start, end = low, high
        for cut in _SENTENCE_END.finditer(text, low, high):
            if cut.end() <= match.start():
                start = cut.end()
            elif cut.start() >= match.end():
                end = cut.start()
                break
        sentences.append(text[start:end])
        covered = end
    return sentences


def _window(sentence: str, start: int, end: int) -> tuple[str, str]:
    """The clause around ``sentence[start:end]``, and the same clause for reading an offer in.

    The clause reaches at most :data:`_REACH` characters either side, no further back than the
    last field label's colon, and no further on than the next field label. A word that is itself
    a field's label ("VISA Sponsorship: No", "Is Sponsorship Available? No") keeps its label and
    at least the first word of its answer. The clause an offer is read in is then the word, a
    colon and the answer alone, without the label's other words ("Available for Work Visa
    Sponsorship?" left blank offers nothing): a label offers nothing, its answer does."""
    before = sentence[max(0, start - _REACH) : start]
    if colons := list(_LABEL_END.finditer(before)):
        before = before[colons[-1].end() :]
    if breaks := list(_FIELD_BREAK.finditer(before)):
        before = before[breaks[-1].end() :]
    own = _OWN_LABEL.match(sentence, end)
    rest = own.end() if own else end
    after = sentence[rest : rest + _REACH]
    first_word = _FIRST_WORD.match(after) if own else None
    if found := _NEXT_LABEL.search(after, first_word.end() if first_word else 0):
        after = after[: found.start()]
    if found := _FIELD_BREAK.search(after):
        after = after[: found.start()]
    if own:
        return before + sentence[start:rest] + after, sentence[start:end] + ":" + after
    return before + sentence[start:end] + after, before + sentence[start:end] + after


def _sponsorship(
    sentence: str, *, title: str | None, location: str | None
) -> tuple[bool, bool, bool]:
    """Whether ``sentence`` offers, may offer, and refuses visa sponsorship to the job at
    ``location`` titled ``title``."""
    offers = may_offer = refuses = False
    for match in _CITIZENSHIP.finditer(sentence):
        window, _ = _window(sentence, match.start(), match.end())
        if (
            _PROTECTED_TRAIT.search(window)
            or _NOT_REQUIRED.search(window)
            or _SOME_POSITIONS.search(window)
            # Read past the clause: a field label's window stops at its own colon.
            or _CITIZENSHIP_NAMED.search(
                sentence, max(0, match.start() - _REACH), match.end() + _REACH
            )
        ):
            continue
        if _REQUIRED.search(window) or match.group().lower() != "citizenship":
            refuses = True
    refuses |= bool(_ALREADY_AUTHORIZED.search(sentence))
    for match in _SPONSOR.finditer(sentence):
        window, offer_window = _window(sentence, match.start(), match.end())
        if _NOT_A_VISA.search(window) and not _VISA_NAMED.search(window):
            continue
        if not _VISA_CONTEXT.search(window):
            continue
        if _CLEARANCE.search(window) and not _VISA_NAMED.search(window):
            continue
        if _HEDGE.search(window):
            tier = _Tier.MAY if not _FOR_LATER.search(window) else None
        elif _NEGATION.search(window) or _REFUSAL.search(window):
            refuses = True
            continue
        elif (
            _OFFER.search(offer_window)
            and (_OFFER_NAMES_A_VISA.search(window) or _FIELD_YES.search(offer_window))
            and not _AUTHORIZATION_REQUIRED.search(window)
        ):
            tier = _Tier.MAY if _MAY.search(window) else _Tier.OFFERS
        else:
            continue
        if tier is None:
            continue
        scope = _scoped(window, title=title, location=location)
        if scope is _Scope.REFUSED:
            refuses = True
        elif scope is _Scope.UNKNOWN or (scope is _Scope.IN and tier is _Tier.MAY):
            may_offer = True
        elif scope is _Scope.IN:
            offers = True
    return offers, may_offer, refuses


def _relocation(sentence: str) -> tuple[bool, bool]:
    """Whether ``sentence`` offers relocation help, and whether it says there is none."""
    offers = refuses = False
    for match in _RELOCATION.finditer(sentence):
        window, offer_window = _window(sentence, match.start(), match.end())
        if _NEGATION.search(window) or _AT_OWN_COST.search(window):
            if _RELOCATION_OFFER.search(window) or re.search(
                r"(?i)reloca\w*\s*[:\-–]?\s*(?:no|none|not)", window
            ):
                refuses = True
            continue
        if _THING_MOVED.search(window):
            continue
        if _ASKED_TO_MOVE.search(window) and not _RELOCATION_OFFER.search(window):
            continue
        if _RELOCATION_OFFER.search(offer_window):
            offers = True
    return offers, refuses


def stances(
    description: str | None, *, title: str | None = None, location: str | None = None
) -> frozenset[str]:
    """The text-derived stances ``description`` holds for the job titled ``title`` at
    ``location``, among :data:`STANCES`.

    Sponsorship is refused when any mention refuses it; otherwise offered when a mention offers it
    to this job; otherwise possibly offered when one does with a hedge, or names a country or level
    the job's own cannot be matched to (ADR-0353). Relocation is offered only when some mention
    offers it and none refuses it: a text that says both is not said to offer it."""
    found: set[str] = set()
    sponsor_offer = sponsor_may = sponsor_refusal = False
    relocation_offer = relocation_refusal = False
    for sentence in _sentences(description or ""):
        offers, may_offer, refuses = _sponsorship(
            sentence, title=title, location=location
        )
        sponsor_offer |= offers
        sponsor_may |= may_offer
        sponsor_refusal |= refuses
        offers, refuses = _relocation(sentence)
        relocation_offer |= offers
        relocation_refusal |= refuses
    if sponsor_refusal:
        found.add(REFUSES_SPONSORSHIP)
    elif sponsor_offer:
        found.add(OFFERS_SPONSORSHIP)
    elif sponsor_may:
        found.add(MAY_OFFER_SPONSORSHIP)
    if relocation_offer and not relocation_refusal:
        found.add(OFFERS_RELOCATION)
    return frozenset(found)


def filtered_stances(held: Iterable[str]) -> frozenset[str]:
    """The stances the ``work_authorization`` filter keeps a job under, given those it ``held``
    (:func:`stances`): its own, and ``may_offer_sponsorship`` too when it offers sponsorship,
    since that filter keeps every job that at least may offer it (ADR-0353)."""
    held = frozenset(held)
    if OFFERS_SPONSORSHIP in held:
        return held | {MAY_OFFER_SPONSORSHIP}
    return held


# -- mentions --------------------------------------------------------------------------------

_MENTION = re.compile(
    r"(?i)\bsponsor(?:ship|ing|ed)?\b|\bvisas?\b|reloca\w*|work(?:ing)? authori[sz]ation|"
    r"authori[sz]ed to work|eligible to work|right to work|work permit|\bcitizen\w*|"
    r"immigra\w*|\bh-?1-?b\b|permanent resident|\bu\.?\s?s\.?\s+persons?\b"
)

# A citizenship word in an equal-opportunity sentence is a protected trait, not a requirement.
_EQUAL_OPPORTUNITY = re.compile(
    r"(?i)regard to|without regard|protected|discriminat|equal (?:employment )?opportunity|"
    r"national origin|citizenship status|immigration status"
)

#: The most sentences :func:`mentions` quotes, and the most characters of each.
MENTIONS_MAX = 5
MENTION_CHARS = 240


def mentions(description: str | None) -> list[str]:
    """The sentences of ``description`` about visa sponsorship, work authorisation, citizenship
    or relocation, each cut to :data:`MENTION_CHARS` around its first such word, at most
    :data:`MENTIONS_MAX` of them, in the text's order. Equal-opportunity sentences are left out,
    and so are sponsor words about something else ("company-sponsored benefits")."""
    said: list[str] = []
    for sentence in _sentences(description or ""):
        match = _MENTION.search(sentence)
        if match is None:
            continue
        window, _ = _window(sentence, match.start(), match.end())
        if _EQUAL_OPPORTUNITY.search(window):
            continue
        if (
            _SPONSOR.fullmatch(match.group())
            and not _VISA_CONTEXT.search(window)
            and not _MENTION.search(sentence, match.end())
        ):
            continue
        text = " ".join(sentence.split())
        if len(text) > MENTION_CHARS:
            at = max(0, " ".join(sentence[: match.start()].split()).__len__() - 80)
            text = ("…" if at else "") + text[at : at + MENTION_CHARS - 2].strip() + "…"
        said.append(text)
        if len(said) == MENTIONS_MAX:
            break
    return said
