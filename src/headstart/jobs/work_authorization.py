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

These are rules over scraped text, with the error rates ADR-0333 measured on a hand-labelled
sample; they are not the employer's structured answer, and an answer that serves them says so.
"""

from __future__ import annotations

import re

#: The stances :func:`stances` can find, in the order an answer names them.
OFFERS_SPONSORSHIP = "offers_sponsorship"
REFUSES_SPONSORSHIP = "refuses_sponsorship"
OFFERS_RELOCATION = "offers_relocation"
STANCES = (OFFERS_SPONSORSHIP, REFUSES_SPONSORSHIP, OFFERS_RELOCATION)

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
    r"unavailable|ineligible|n/a|disqualif\w*)\b|n't\b)"
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
    + r"(?!\s+(?:eligibility|requirements?|status|information|rules|categor))"
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

# "Sponsorship is not guaranteed": offered to some, a negation that is not a refusal.
_HEDGE = re.compile(r"(?i)\b(?:is not|isn't|not) guaranteed")

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
    r"(?i)\b(?:available|offer(?:s|ed|ing)?|provide[sd]?|providing|support(?:s|ed|ing)?|"
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


def _sponsorship(sentence: str) -> tuple[bool, bool]:
    """Whether ``sentence`` offers, and whether it refuses, visa sponsorship."""
    offers = refuses = False
    for match in _CITIZENSHIP.finditer(sentence):
        window, _ = _window(sentence, match.start(), match.end())
        if _PROTECTED_TRAIT.search(window) or _NOT_REQUIRED.search(window):
            continue
        if _REQUIRED.search(window) or match.group().lower() != "citizenship":
            refuses = True
    for match in _SPONSOR.finditer(sentence):
        window, offer_window = _window(sentence, match.start(), match.end())
        if _NOT_A_VISA.search(window) and not _VISA_NAMED.search(window):
            continue
        if not _VISA_CONTEXT.search(window):
            continue
        if _CLEARANCE.search(window) and not _VISA_NAMED.search(window):
            continue
        if _HEDGE.search(window):
            offers |= not _FOR_LATER.search(window)
        elif _NEGATION.search(window) or _REFUSAL.search(window):
            refuses = True
        elif _OFFER.search(offer_window) and (
            _OFFER_NAMES_A_VISA.search(window) or _FIELD_YES.search(offer_window)
        ):
            offers = True
    return offers, refuses


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


def stances(description: str | None) -> frozenset[str]:
    """The text-derived stances ``description`` holds, among :data:`STANCES`.

    Sponsorship is offered only when some mention offers it and none refuses it, relocation
    likewise: a text that says both is not said to offer it."""
    found: set[str] = set()
    sponsor_offer = sponsor_refusal = relocation_offer = relocation_refusal = False
    for sentence in _sentences(description or ""):
        offers, refuses = _sponsorship(sentence)
        sponsor_offer |= offers
        sponsor_refusal |= refuses
        offers, refuses = _relocation(sentence)
        relocation_offer |= offers
        relocation_refusal |= refuses
    if sponsor_refusal:
        found.add(REFUSES_SPONSORSHIP)
    elif sponsor_offer:
        found.add(OFFERS_SPONSORSHIP)
    if relocation_offer and not relocation_refusal:
        found.add(OFFERS_RELOCATION)
    return frozenset(found)


# -- mentions --------------------------------------------------------------------------------

_MENTION = re.compile(
    r"(?i)\bsponsor(?:ship|ing|ed)?\b|\bvisas?\b|reloca\w*|work(?:ing)? authori[sz]ation|"
    r"authori[sz]ed to work|eligible to work|right to work|work permit|\bcitizen\w*|"
    r"immigra\w*|\bh-?1-?b\b|permanent resident"
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
