"""India place gazetteer for the location filter (query-time alias expansion, ADR-0024).

Why this exists: the location filter is a raw ``lower(location) LIKE '%term%'``, and the
live-index inventory (``experiment/india-location-filter/``, 2026-07-20: 24,964 India rows)
showed **47% of India jobs never contain the word "india"** — zoho/keka/ripplehire write
city-only strings ("Bangalore North", "Pune City"), and Bengaluru/Bangalore is a ~50/50
spelling split. This module expands a canonical place ("india" or a city) into a match over
every observed alias, so the filter stops lying.

Aliases are lowercase substrings — every alias must be unambiguous *as a substring of any world
location string*. They are matched by one ``regexp_like`` alternation rather than one ``LIKE
'%alias%'`` per alias: same substring semantics, same rows, one pass instead of 267 (see
:func:`_any`, and ADR-0024's 2026-09-06 amendment). Traps vetted OUT of the
inventory's raw map (do not re-add without a guard): "salt lake" (Salt Lake City, UT — it
contaminated the raw inventory), "wai" (inside taiwan/kuwait/hawaii), "salem" (US city),
"punjab" (Pakistan has one), "verna" (inside Governador Valadares), "whitefield"
(Manchester, UK), "supa" (inside Supai, AZ; its rows carry "india" anyway), "vadod"
(inside vadodara), "hisar" (inside Turkish Hisarönü/Rumelihisarı). Known residual collisions accepted as negligible for a tech-jobs
corpus: kochi (Japan), thane (Thanet, UK), a bare
"IN" (Indiana, on a US state field — see IN_EXACT).

ADR-0347 (2026-09-29 audit of the served ``country`` column) added three things to the substring
aliases above. The word "india" now needs a non-letter on both sides (a substring claimed
"Xindian, Taiwan"; the underscore in "IN_India_WFH" is not a letter, so that row stays). TOWNS are
Indian towns and plants matched as whole words, each read off a country-less row and verified
against its posting, so a short name cannot hide inside another place. And the ISO country code
is read from the shapes only India writes (see :func:`_subdivision_pattern`), because a bare
"Town, IN" is Indiana about fifteen times for every India row.

This file is deployed standalone into the Space image (deploy-space.yml copies it next to
app.py), so it must stay dependency-free. Regenerate the inventory before extending.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# Canonical city -> observed alias substrings (spelling variants, real typos seen in the
# data, and metro localities that appear WITHOUT the metro's name). Ordered by observed
# India-row frequency. "hyderaba" is deliberate — the prefix matches the typo and the city.
CITIES: dict[str, tuple[str, ...]] = {
    "bengaluru": (
        "bengaluru",
        "bangalore",
        "banagalore",
        "banglore",
        "bengalore",
        "banaglore",
        "bengalooru",
        "benguluru",
        "bengalura",
        "koramangala",
        "madiwala",
        "madivala",
        "electronic city",
        "marathahalli",
        "hebbal",
        "yelahanka",
        "sarjapur",
        "attibele",
    ),
    "hyderabad": (
        "hyderaba",
        "hyderabd",
        "secunderabad",
        "secundrabad",
        "cyberabad",
        "gachibowli",
        "hitec city",
        "hitech city",
        "madhapur",
        "kondapur",
        "nanakramguda",
        "hafeezpet",
        "serilingampally",
        "raidurg",
        "puppalaguda",
        "mallapur",
        "kukatpally",
        "begumpet",
        "uppal",
    ),
    "pune": (
        "pune",
        "hinjewadi",
        "hinjawadi",
        "kharadi",
        "khadki",
        "kothrud",
        "baner",
        "balewadi",
        "magarpatta",
        "pimpri",
        "chinchwad",
        "wakad",
        "aundh",
        "viman nagar",
        "kalyani nagar",
        "bhosari",
    ),
    "chennai": (
        "chennai",
        "madras",
        "sholinganallur",
        "ambattur",
        "siruseri",
        "sriperumbudur",
        "porur",
        "guindy",
        "egmore",
        "taramani",
        "oragadam",
    ),
    "mumbai": (
        "mumbai",
        "mumbia",
        "bombay",
        "andheri",
        "powai",
        "goregaon",
        "chembur",
        "kandivali",
        "malad",
        "vashi",
        "bandra",
        "bkc",
        "airoli",
        "sakinaka",
    ),
    "gurgaon": ("gurgaon", "gurugram", "manesar", "gurugarm", "gugugarm", "grugram"),
    "noida": ("noida", "gautam buddha nagar"),
    "kolkata": ("kolkata", "calcutta", "rajarhat"),
    "delhi": ("delhi", "new delihi"),  # the typo lacks the 'delhi' substring
    "ahmedabad": ("ahmedabad", "ahemdabad", "amdavad"),
    "coimbatore": ("coimbatore", "singanallur"),
    "vadodara": ("vadodara", "vadoddara", "baroda", "maneja"),
    "indore": ("indore",),
    "chandigarh": (
        "chandigarh",
        "mohali",
        "sas nagar",
        "sahibzada ajit singh nagar",
        "panchkula",
    ),
    "thane": ("thane", "mira bhayandar", "mumbra", "kalyan"),
    "jaipur": ("jaipur", "sitapura"),
    "kochi": ("kochi", "cochin", "ernakulam", "infopark"),
    "bhubaneswar": ("bhubaneswar", "bhubaneshwar"),
    "thiruvananthapuram": (
        "thiruvananthapuram",
        "thiruvanathapuram",
        "trivandrum",
        "technopark",
    ),
    "surat": ("surat",),  # guarded below: "Surat Thani, Thailand"
    "faridabad": ("faridabad",),
    "nagpur": ("nagpur", "saoner"),
    "visakhapatnam": ("visakhapatnam", "vishakhapatnam", "vizag"),
    "vijayawada": ("vijayawada",),
    "mysuru": ("mysuru", "mysore"),
    "nashik": ("nashik", "nasik"),
    "goa": ("goa", "panaji", "porvorim", "taleigao", "dabolim"),
    "ghaziabad": ("ghaziabad", "gaziabad"),
    "anand": ("anand",),
    "jajpur": ("jajpur",),
    "meerut": ("meerut",),
    "savli": ("savli",),
    "guntur": ("guntur", "mangalagiri", "amaravati"),
    "jammu": ("jammu", "udhampur"),
    "madurai": ("madurai",),
    "bhopal": ("bhopal",),
    "jamshedpur": ("jamshedpur",),
    "hosur": ("hosur",),
    "bharuch": ("bharuch", "jhagadia"),
    "shahjahanpur": ("shahjahanpur",),
    "lucknow": ("lucknow", "lacknow"),
    "sikkim": ("sikkim", "gangtok"),
    "tiruchirappalli": ("tiruchirappalli", "tiruchirapalli", "trichy"),
    "gandhinagar": ("gandhinagar", "gift city"),
    "halol": ("halol",),
    "palghar": ("palghar", "umbergaon", "valsad"),
    "aurangabad": ("aurangabad", "sambhaji nagar", "sambhajinagar"),
    "jalandhar": ("jalandhar", "jalander"),
    "vellore": ("vellore",),
    "rajkot": ("rajkot", "jetpur"),
    "mangaluru": ("mangaluru", "mangalore", "manglore"),
    "kozhikode": ("kozhikode", "calicut"),
    "jodhpur": ("jodhpur",),
    "patna": ("patna",),
    "ludhiana": ("ludhiana",),
    "raipur": ("raipur",),
    "anakapalli": ("anakapalli",),
    "belagavi": ("belagavi", "belgaum"),
    "puducherry": ("puducherry", "pondicherry"),
    "kanpur": ("kanpur",),
    "ranchi": ("ranchi",),
    "dehradun": ("dehradun",),
    "amritsar": ("amritsar",),
    "tirupati": ("tirupati",),
    "udaipur": ("udaipur",),
    "guwahati": ("guwahati",),
    "srinagar": ("srinagar",),
    "baddi": ("baddi",),
}

# Per-city (or per-state) exclusion guards for aliases that collide with a specific other place.
EXCLUDE: dict[str, tuple[str, ...]] = {
    "surat": ("surat thani",),  # Thailand
    # 'kalyan' is inside Pune's Kalyani Nagar; 'thane' is inside Istanbul's Kagithane
    "thane": ("kalyani", "kagithane"),
    "goa": ("lagoa",),  # Brazil: "lagoa" (lagoon) is inside Alagoas, Lagoa Santa
    # Brazil / New York, US / a typo of Canada
    "anand": ("sananduva", "canandaigua", "cananda"),
    "hyderabad": ("pakistan", "sindh"),  # Hyderabad, Sindh, Pakistan (ADR-0322)
    "nashik": ("inashiki",),  # Inashiki-gun, Ibaraki, Japan
    "mumbai": ("maladzyechna",),  # 'malad' is inside Maladzyechna, Belarus
    # Delhi, New York and Delhi, Louisiana. The last carries its "(" so Delhi's own "Delhi, Laxmi
    # Nagar" is not vetoed with it.
    "delhi": ("delhi new york", "delhi, ny", "delhi, new york", "delhi, la ("),
    "chennai": ("madras, or",),  # Madras, Oregon
    # A state name is guarded the same way: Hungary's Hajdú-Bihar county (ADR-0347).
    "bihar": ("hajdu", "hajdú"),
}

# State/UT names that are unambiguous, bar "bihar" (it carries an EXCLUDE guard) — country-level match
# only (catches "Karnataka, IN" residue).
# Diacritic variants are the ones actually observed in workday strings. "punjab" is
# deliberately absent (Pakistan). "goa" is already a city entry.
STATES: tuple[str, ...] = (
    "karnataka",
    "karnātaka",
    "maharashtra",
    "tamil nadu",
    "tamil nādu",
    "telangana",
    "kerala",
    "haryana",
    "uttar pradesh",
    "west bengal",
    "gujarat",
    "rajasthan",
    "odisha",
    "madhya pradesh",
    "andhra pradesh",
    "jharkhand",
    "chhattisgarh",
    "uttarakhand",
    "himachal pradesh",
    "arunachal pradesh",
    "bihar",
    "kerela",  # a common misspelling of Kerala
)

# Country-level signals that carry no city name at all. Measured 2026-08-25 on the 317,421-row
# served table: these two rules alone recover 429 India rows the city map could never reach,
# because the string names a plant, a tower, or a town too small to gazetteer.
#
# ISO alpha-3 "IND". Matched only in positions where it is the country tag, never as a bare
# substring: "ind" sits inside Indore, Indianapolis and a hundred ordinary words. Forms observed:
# "IND", "IND-BLR-Divyasree Technopolis", "IND BNGL FL2-3 TWR 3", "IND, Remote", "IND - Remote",
# "Remote (IND)", "Remote - IND".
#
# ISO alpha-2 "IN" too, but only as the whole string. Measured on the served table 2026-09-25:
# 885 rows read exactly "IN"/"In". 876 are India — SuccessFactors' feed (847) and iCIMS tenants
# (29, their descriptions naming Delhi, Pune or Hyderabad). 9 are Indiana, a US state field on
# JazzHR and Zoho (Harrison Consulting Solutions; one description names Indianapolis). Case does
# not separate them and this rule sees only the string, so those 9 are an accepted collision,
# like the residual ones listed in the module docstring. Anywhere else "in" is a word.
# Re-measured 2026-09-29 (ADR-0347): 230 rows read exactly "IN", 228 of them from feeds that write
# a bare ISO country code as the whole location (SuccessFactors 175, iCIMS 29, ADP 20, Cornerstone
# 4 — the same feeds write "US", "ES" and "DE" that way); the 2 that are Indiana are JazzHR.
IN_EXACT = "in"
# "Remote, IN": the same feeds write a remote posting as "Remote, <ISO country>" ("Remote, US" on 43
# of one employer's rows, "Remote, CR" on another's). Measured 2026-09-29 (ADR-0347): 23 rows on 6
# employers, 10 of whose postings name India and none Indiana. The whole string only: "Remote, IN,
# US" or a state list is not it.
IN_REMOTE = "remote, in"
IND_FORMS: tuple[str, ...] = (
    "ind-%",  # IND-BLR-..., IND-Remote
    "ind %",  # IND BNGL ..., IND Karle Tech Park
    "ind,%",  # IND, Alwaye-South 1 ; IND, Remote  (RippleHire, ADR-0347)
    "%(ind)%",  # Remote (IND)
    "% - ind",  # Remote - IND   (NOT '% ind': that also takes "Grayslake, Ind", Illinois)
)

# **IND is also Indianapolis's IATA code**, and airport-code strings are how that bites:
# "IND U; CVG SD; United States, PA, Philadelphia - Remote; MKE W; MSP" is a US row that
# "ind %" would otherwise claim. Guarded on the one token that settles it.
IND_EXCLUDE: tuple[str, ...] = ("united states",)

# Subdivision codes, as workday writes them: "Vemagal, KA, IN". The real ISO 3166-2:IN set,
# PLUS the four vehicle-registration abbreviations ATSes also use for the same states
# (CT/CG Chhattisgarh, OR/OD Odisha, TG/TS Telangana, UT/UK Uttarakhand) — the data uses both
# schemes, so shipping one set alone loses rows. Dadra & Nagar Haveli's vehicle codes (DD/DN)
# are deliberately absent: neither appears in the live table and both are two letters of very
# common English.
# Measured 2026-08-25: "tg" (ISO, Telangana) has 55 tails in the live table while "ts" (the
# vehicle code) has 0 — an earlier pass shipped only the vehicle codes and would have missed a
# Telangana tail town entirely. Anchored to the ", {code}, in" tail so two letters can never
# match loose text; since ADR-0347 the code may also open the string ("KA, IN") and an Indian PIN
# may follow the country (see :func:`_subdivision_pattern`).
SUBDIVISIONS: tuple[str, ...] = (
    # ISO 3166-2:IN
    "an",
    "ap",
    "ar",
    "as",
    "br",
    "ch",
    "ct",
    "dh",
    "dl",
    "ga",
    "gj",
    "hp",
    "hr",
    "jh",
    "jk",
    "ka",
    "kl",
    "la",
    "ld",
    "mh",
    "ml",
    "mn",
    "mp",
    "mz",
    "nl",
    "or",
    "pb",
    "py",
    "rj",
    "sk",
    "tg",
    "tn",
    "tr",
    "up",
    "ut",
    "wb",
    # vehicle-registration variants seen in ATS strings for the same states
    "cg",
    "od",
    "ts",
    "uk",
)

# Places named "India" that are not the country. The country term needs a non-letter on both
# sides (see :func:`_india_pattern`), which by itself drops every US place whose name merely
# contains it: "Indiana", "Indian Head", "Indialantic", "Indianola", "Indian Springs", "Indian
# Ocean", "Indiantown", "Xindian". What is left is a whole word "India" that is a neighbourhood:
# Singapore's Little India. Note the boundary is a *letter* test, not ``\b``: "IN_India_WFH" has an
# underscore around india, which ``\b`` reads as a word character and would LOSE a real row.
INDIA_EXCLUDE: tuple[str, ...] = ("little india",)

# Indian towns and plants that appear on country-less rows and that no city alias covers. Each was
# read off a location no gazetteer rule reached (ADR-0347, 2026-09-29 table: 172 rows on these
# names beyond the shapes above) and checked against the posting or its employer's other rows.
# Matched as whole words (:func:`_towns_pattern`), unlike the substring city aliases: a short name
# must not hide inside another place ("Korbach", Germany). Country-level only — a town is not a
# place the filter offers. Names that are also a place, a person or a word elsewhere stay out
# (kota: Kota Kinabalu; kalina: a Polish village; parsa: Nepal; shalimar: Florida; patan: Nepal;
# mirzapur: Bangladesh; hassan: a given name; blore: an English village).
TOWNS: tuple[str, ...] = (
    "mundra",
    "sahnewal",
    "siliguri",
    "dadra",
    "panthnagar",
    "pantnagar",
    "talegaon",
    "shirwal",
    "korba",
    "rourkela",
    "parwanoo",
    "kutchh",
    "kutch",
    "jharsuguda",
    "zirakpur",
    "malda",
    "kolhapur",
    "kanchipuram",
    "bhuj",
    "alathur",
    "waluj",
    "raigarh",
    "patiala",
    "paonta sahib",
    "nodia",  # a typo of Noida
    "lower parel",
    "kurla",
    "khurja",
    "jalna",
    "hubballi",
    "hubli",
    "dolvi",
    "dahanu",
    "coachin",  # a typo of Cochin
    "amravati",
    "ambala",
    "akurdi",
    "vikhroli",
    "vapi",
    "udupi",
    "tumkur",
    "toansa",
    "tirupur",
    "thrissur",
    "thiruvallur",
    "taloja",
    "sangli",
    "sambalpur",
    "rajpura",
    "ottapidaram",
    "nellore",
    "khambhalia",
    "kattupalli",
    "karaikal",
    "howrah",
    "gwalior",
    "gorakhpur",
    "gandikota",
    "farukhnagar",
    "dombivali",
    "dombivli",
    "dholka",
    "dahej",
    "cuddalore",
    "bhugaon",
    "bhandara",
    "bavla",
    "barrackpore",
    "balasore",
    "mehsana",
    "pithampur",
    "lonand",
    "shillong",
    "miraroad",
    "sri city",
)

# Regions a job seeker treats as one market: virtual entries expanding to member cities.
REGIONS: dict[str, tuple[str, ...]] = {
    "delhi ncr": ("delhi", "gurgaon", "noida", "faridabad", "ghaziabad"),
}

# The UI dropdown: canonicals with meaningful volume (>=~35 live rows), by frequency.
# Everything else still participates in the country-level "all india" match.
DROPDOWN: tuple[str, ...] = (
    "bengaluru",
    "hyderabad",
    "pune",
    "chennai",
    "mumbai",
    "delhi ncr",
    "gurgaon",
    "noida",
    "kolkata",
    "delhi",
    "ahmedabad",
    "coimbatore",
    "vadodara",
    "indore",
    "chandigarh",
    "thane",
    "jaipur",
    "kochi",
    "bhubaneswar",
    "thiruvananthapuram",
    "surat",
    "nagpur",
    "visakhapatnam",
    "mysuru",
)


def dropdown_options() -> list[tuple[str, str]]:
    """(value, label) pairs for the UI's India dropdown — the display side of DROPDOWN."""
    return [(c, c.title().replace("Ncr", "NCR")) for c in DROPDOWN]


#: The column every clause here matches on, lowercased once per predicate rather than per alias.
_LOC = "lower(location)"


def _rx(literal: str) -> str:
    """One alias as a regex fragment: regex-escaped, then made safe for a SQL string literal.

    Both escapes are needed and they are not the same escape. ``re.escape`` stops an alias's own
    punctuation being read as regex syntax — ``(ind)``'s parentheses would otherwise be a capture
    group — and doubling the quote is what keeps the literal closed. Neither is dormant:
    ``test_alias_hygiene`` vets the aliases for case, quotes and ``%`` but says nothing about
    regex metacharacters, and 28 of the constants already change under ``re.escape`` (every one
    containing a space).
    """
    return re.escape(literal).replace("'", "''")


def _regexp_like(alternation: str, column: str = _LOC) -> str:
    """One ``regexp_like`` over ``alternation`` — the only place this module emits a predicate.
    ``column`` is the lowercased location unless a rule has to read the letters' case.

    Guards the empty case here rather than at each caller, because an empty alternation matches
    *every* row: exactly backwards from the empty set it reads as. Unreachable today, since every
    caller builds from a non-empty constant, and cheap to make impossible rather than to rely on
    that staying true — a branch that silently returns the whole table is the wrong one to leave
    to convention.
    """
    if not alternation.strip("|"):
        raise ValueError("refusing to build a match on no aliases")
    return f"regexp_like({column}, '{alternation}')"


def _any(aliases: Iterable[str]) -> str:
    """One ``regexp_like`` matching any of ``aliases`` as a substring.

    **This is the whole optimisation.** These were one ``lower(location) LIKE '%alias%'`` per
    alias, OR'd — 267 predicates and a 10,307-character clause for "india", each one its own pass
    over the column. One alternation is a single pass over a single automaton: measured on the
    served table (318,003 rows), a count went from 2,669 ms to 357 ms and `/facets` with All India
    from 8,670 ms to 1,279 ms (medians, n=7 on this code). Those two ratios differ because
    ADR-0084 runs its ~46 counts in a thread pool, so the strip costs roughly its slowest count
    rather than their sum — the clause is inside all of them, but not 46 times over. The rows are identical, verified by set equality
    of matched ids across all 70 places the filter accepts rather than by count.
    """
    return _regexp_like("|".join(_rx(a) for a in aliases))


def _none(terms: tuple[str, ...]) -> str:
    """The ``AND NOT`` guard that protects a clause from its known collisions, or nothing."""
    return f" AND NOT {_any(terms)}" if terms else ""


def _anchored(pattern: str) -> str:
    """A LIKE pattern from :data:`IND_FORMS` as an equivalent regex fragment.

    Only these patterns need it, and only because their anchoring *is* the rule: ``'ind-%'``
    means "starts with", ``'%(ind)%'`` means "contains", and the difference between them is what
    stops ``ind`` claiming Indore and every "Industrial Area" (``test_ind_is_never_a_bare_substring``
    asserts that shape on the constants). A leading or trailing ``%`` becomes "unanchored at that
    end"; its absence becomes a ``^`` or ``$``.

    Two assumptions about :data:`IND_FORMS`, both asserted by
    ``test_ind_forms_carry_no_interior_wildcard``: no pattern contains ``_``, so ``%`` is the only
    wildcard to read, and no ``%`` appears anywhere but the ends — an interior one would be
    stripped by neither branch and pass through as a literal ``%``.
    """
    starts, ends = pattern.startswith("%"), pattern.endswith("%")
    return ("" if starts else "^") + _rx(pattern.strip("%")) + ("" if ends else "$")


def _city_where(city: str) -> str | None:
    aliases = CITIES.get(city)
    if not aliases:
        return None
    return f"({_any(aliases)}{_none(EXCLUDE.get(city, ()))})"


def _sql(pattern: str) -> str:
    """A regex as the body of a SQL string literal: the quote is the only character to double."""
    return pattern.replace("'", "''")


#: A whole word: a non-letter, or the string's own edge, on each side. It is a *letter* test, not
#: ``\\b``, so an underscore or a digit counts as a boundary ("IN_India_WFH"). Written with no
#: lookaround because DataFusion's regex engine has none; Python's ``re`` runs the same text.
_WORD_LEFT, _WORD_RIGHT = "(^|[^a-z])", "($|[^a-z])"


def _india_pattern() -> str:
    """The country's name as a whole word ("Xindian" and "Indian Harbour Beach" are not it)."""
    return f"{_WORD_LEFT}india{_WORD_RIGHT}"


def _towns_pattern() -> str:
    """Every :data:`TOWNS` name as a whole word."""
    return f"{_WORD_LEFT}(?:{'|'.join(re.escape(t) for t in TOWNS)}){_WORD_RIGHT}"


def _subdivision_pattern() -> str:
    """The ISO country code read next to an Indian subdivision code or an Indian PIN.

    Two shapes, both on the lowercased string. ``KA, IN`` — Workday's "City, KA, IN" tail and
    the same two parts opening a string ("KA, IN"), where the code stands for a whole state. And
    ``IN, 410208`` — the country followed by a six-digit PIN, on its own or after a subdivision
    ("Jamnagar, GJ, IN, 361004"); a US ZIP has five digits, so "Whitestown, IN, 46077" is Indiana.
    """
    codes = "|".join(SUBDIVISIONS)
    return f"(^|, )(?:{codes}), in$|(^|, )in, [0-9]{{6}}$"


#: "Town, Plant, IN": the ISO country code as the last of exactly three parts, the middle one at
#: least three letters. Read on the raw string, in capitals only: SuccessFactors cuts every part
#: to four letters, so its "In" is India *or* Indonesia ("Sri City, Andh, In", "Others, Bant, In"),
#: while the code is "IN". Measured on the 2026-09-29 table: 1,425 rows have this shape, 1,357
#: already tagged for another reason and the other 68 all India, with no Indiana among them,
#: because an Indiana row's middle part is a two-letter state ("Indianapolis, IN, IN").
_PLANT_TAIL_PATTERN = "^[^,;]+, [^,;]{3,}, IN$"

_INDIA_RX = re.compile(_india_pattern())
_TOWNS_RX = re.compile(_towns_pattern())
_SUBDIVISION_RX = re.compile(_subdivision_pattern())
_PLANT_TAIL_RX = re.compile(_PLANT_TAIL_PATTERN)


def _state_where(state: str) -> str:
    """A state name that carries a guard of its own, like a guarded city."""
    return f"({_any((state,))}{_none(EXCLUDE[state])})"


def _country_where() -> str:
    """The whole word "india", minus the neighbourhoods named for it."""
    return f"({_regexp_like(_sql(_india_pattern()))}{_none(INDIA_EXCLUDE)})"


def _ind_where() -> str:
    """ISO alpha-3 "IND", in the positions where it is the country tag rather than a substring,
    and alpha-2 "IN" as the whole string (:data:`IN_EXACT`, :data:`IN_REMOTE`)."""
    forms = _regexp_like("|".join(_anchored(f) for f in IND_FORMS))
    exact = f"{_LOC} IN ('ind', '{IN_EXACT}', '{IN_REMOTE}')"
    return f"(({exact} OR {forms}){_none(IND_EXCLUDE)})"


def _subdivision_where() -> str:
    """The country code beside a subdivision code or a PIN (:func:`_subdivision_pattern`)."""
    return _regexp_like(_sql(_subdivision_pattern()))


def _plant_tail_where() -> str:
    """The "Town, Plant, IN" tail (:data:`_PLANT_TAIL_PATTERN`), read on the raw column: it needs case."""
    return _regexp_like(_sql(_PLANT_TAIL_PATTERN), "location")


def _towns_where() -> str:
    """Every :data:`TOWNS` name as a whole word (:func:`_towns_pattern`)."""
    return _regexp_like(_sql(_towns_pattern()))


def where(place: str) -> str | None:
    """The where-fragment for a canonical place, or None if the place is unknown.

    ``place`` is "india", a :data:`REGIONS` key, or a :data:`CITIES` key.

    The country-level "india" rule is seven things OR'd together (ADR-0024, extended by
    ADR-0086 and ADR-0347): the whole word "india" minus :data:`INDIA_EXCLUDE`; ISO alpha-3 "IND"
    in its :data:`IND_FORMS` positions, or alpha-2 "IN" as the whole string, minus
    :data:`IND_EXCLUDE`; the country code beside a subdivision code or a PIN; the country code
    closing a "Town, Plant, IN" string; every :data:`TOWNS` name as a whole word; every city alias;
    and every state name. That is how the rule is *written*; the clause it compiles to has fewer
    parts, because every city or state without an :data:`EXCLUDE` guard shares one alternation.

    Aliases are trusted constants — callers must never pass free text through this into SQL
    beyond the dict lookups here.
    """
    if place == "india":
        parts = [
            _country_where(),
            _ind_where(),
            _subdivision_where(),
            _plant_tail_where(),
            _towns_where(),
        ]
        # Every city and state whose aliases carry no collision guard shares ONE alternation,
        # because a guard is the only reason an alias needs a term of its own — and only a few
        # cities and states have one. That is where the predicate count actually falls: 267
        # LIKEs became one alternation per guarded city or state plus a handful of rules, and
        # this shared one is the largest by far.
        plain: list[str] = []
        for city, aliases in CITIES.items():
            if EXCLUDE.get(city):
                parts.append(_city_where(city))
            else:
                plain.extend(aliases)
        for state in STATES:
            if EXCLUDE.get(state):
                parts.append(_state_where(state))
            else:
                plain.append(state)
        parts.append(_any(plain))
        return "(" + " OR ".join(p for p in parts if p) + ")"
    if place in REGIONS:
        return "(" + " OR ".join(_city_where(c) for c in REGIONS[place]) + ")"
    return _city_where(place)


def _matches_like(text: str, pattern: str) -> bool:
    """Python equivalent of the LIKE-pattern semantics :func:`_anchored` compiles to regex for
    :data:`IND_FORMS`: a leading/trailing ``%`` means unanchored at that end, its absence means
    the pattern must start/end the string there. Patterns here never carry an interior wildcard
    or an underscore (``test_ind_forms_carry_no_interior_wildcard`` already asserts this on the
    constants), so start/end/contains checks cover every case exactly."""
    starts, ends = pattern.startswith("%"), pattern.endswith("%")
    core = pattern.strip("%")
    if starts and ends:
        return core in text
    if starts:
        return text.endswith(core)
    if ends:
        return text.startswith(core)
    return text == core


def classify(location: str | None) -> str | None:
    """``"IN"`` if ``location`` matches the country-level India rule :func:`where` compiles to
    SQL for (``where("india")``), else ``None`` (ADR-0138).

    A pure function of ``location`` — :func:`headstart.ingest.derived_meta.country_meta` calls
    this once per Job (``doc_prep.to_meta`` at embed time, ``update_meta``'s sweep to repair a
    stored row) to fill the served ``country`` column, so a filter can test ``country = 'IN'``
    (a plain equality) instead of paying the 3KB ``regexp_like`` alternation :func:`where` builds
    fresh on every request.

    Reads the exact same :data:`CITIES`/:data:`STATES`/:data:`TOWNS`/:data:`IND_FORMS`/
    :data:`SUBDIVISIONS`/:data:`EXCLUDE`/:data:`IND_EXCLUDE`/:data:`INDIA_EXCLUDE` constants
    :func:`where` does, so a
    future edit to any of them (a new alias, a new exclusion) reaches both paths — this function
    never needs its own edit for a data change, only :func:`where` does. The two functions can
    still drift if the *rule's shape* itself changes (a new part added to :func:`where`'s
    composition) rather than its data — ``test_classify_agrees_with_the_country_level_rule_on_
    every_oracle_row`` is what actually guards against that, by checking agreement on every real
    location string in :mod:`tests.test_geo`'s oracle, not by this docstring's promise alone.

    Most alternatives :func:`where` ORs in are a substring/prefix/equality test on a literal
    (``_rx`` is ``re.escape`` plus SQL-quote-doubling — it changes nothing about *what* matches,
    only how the literal is embedded in a regex and a SQL string), so a Python
    ``in``/``startswith``/``==`` check is exactly the same test as the SQL ``regexp_like`` it
    mirrors. The whole-word and tail rules (ADR-0347) are patterns, and this side runs the very
    pattern text the SQL does (``_india_pattern`` and its siblings), so the two engines can
    differ only on a trailing newline, which no served location carries.
    """
    if not location:
        return None
    text = location.lower()

    def has_any(terms: Iterable[str]) -> bool:
        return any(term in text for term in terms)

    if _INDIA_RX.search(text) and not has_any(INDIA_EXCLUDE):  # _country_where
        return "IN"
    if not has_any(IND_EXCLUDE) and (  # _ind_where
        text in ("ind", IN_EXACT, IN_REMOTE)
        or any(_matches_like(text, form) for form in IND_FORMS)
    ):
        return "IN"
    if _SUBDIVISION_RX.search(text):  # _subdivision_where
        return "IN"
    # _plant_tail_where, on the raw string: it needs case
    if _PLANT_TAIL_RX.search(location):
        return "IN"
    if _TOWNS_RX.search(text):  # _towns_where
        return "IN"
    for state in STATES:  # every state, guarded ones included
        if state in text and not has_any(EXCLUDE.get(state, ())):
            return "IN"
    for city, aliases in CITIES.items():  # every city, guarded ones included
        if has_any(aliases) and not has_any(EXCLUDE.get(city, ())):
            return "IN"
    return None
