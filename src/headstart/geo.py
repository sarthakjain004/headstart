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
corpus: hyderabad (Pakistan), kochi (Japan), thane (Thanet, UK), madras (Madras, OR).

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
    "gurgaon": ("gurgaon", "gurugram", "manesar"),
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
    "thiruvananthapuram": ("thiruvananthapuram", "trivandrum", "technopark"),
    "surat": ("surat",),  # guarded below: "Surat Thani, Thailand"
    "faridabad": ("faridabad",),
    "nagpur": ("nagpur", "saoner"),
    "visakhapatnam": ("visakhapatnam", "vishakhapatnam", "vizag"),
    "vijayawada": ("vijayawada",),
    "mysuru": ("mysuru", "mysore"),
    "nashik": ("nashik", "nasik"),
    "goa": ("goa", "panaji", "porvorim", "taleigao", "dabolim"),
    "ghaziabad": ("ghaziabad",),
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
    "lucknow": ("lucknow",),
    "sikkim": ("sikkim", "gangtok"),
    "tiruchirappalli": ("tiruchirappalli", "tiruchirapalli", "trichy"),
    "gandhinagar": ("gandhinagar", "gift city"),
    "halol": ("halol",),
    "palghar": ("palghar", "umbergaon", "valsad"),
    "aurangabad": ("aurangabad", "sambhaji nagar"),
    "jalandhar": ("jalandhar", "jalander"),
    "vellore": ("vellore",),
    "rajkot": ("rajkot", "jetpur"),
    "mangaluru": ("mangaluru", "mangalore"),
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

# Per-city exclusion guards for aliases that collide with a specific other place.
EXCLUDE: dict[str, tuple[str, ...]] = {
    "surat": ("surat thani",),  # Thailand
    "thane": ("kalyani",),  # 'kalyan' is inside Pune's Kalyani Nagar
}

# Unambiguous state/UT names — country-level match only (catches "Karnataka, IN" residue).
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
    "bihar",
)

# Country-level signals that carry no city name at all. Measured 2026-08-25 on the 317,421-row
# served table: these two rules alone recover 429 India rows the city map could never reach,
# because the string names a plant, a tower, or a town too small to gazetteer.
#
# ISO alpha-3 "IND". Matched only in positions where it is the country tag, never as a bare
# substring: "ind" sits inside Indore, Indianapolis and a hundred ordinary words. Forms observed:
# "IND", "IND-BLR-Divyasree Technopolis", "IND BNGL FL2-3 TWR 3", "IND - Remote", "Remote (IND)",
# "Remote - IND".
IND_FORMS: tuple[str, ...] = (
    "ind-%",  # IND-BLR-..., IND-Remote
    "ind %",  # IND BNGL ..., IND Karle Tech Park
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
# match loose text.
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

# US places whose names contain "india" but are not India. The country term is a SUBSTRING
# match, so without these it claims "Indian Head, MD" and Diego Garcia's "British Indian Ocean
# Territory" - 17 rows on the 2026-08-25 table. Note the term stays a substring on purpose:
# "IN_India_WFH" has no word boundary around india, so a \bindia\b test would LOSE a real row.
INDIA_EXCLUDE: tuple[str, ...] = (
    "indiana",
    "indian head",
    "indialantic",
    "indianola",
    "indian springs",
    "indian ocean",
    "indian trail",
    "indian land",
    "indian river",
    "indian wells",
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


def _matches(alternation: str) -> str:
    """One ``regexp_like`` over ``alternation`` — the only place this module emits a predicate.

    Guards the empty case here rather than at each caller, because an empty alternation matches
    *every* row: exactly backwards from the empty set it reads as. Unreachable today, since every
    caller builds from a non-empty constant, and cheap to make impossible rather than to rely on
    that staying true — a branch that silently returns the whole table is the wrong one to leave
    to convention.
    """
    if not alternation.strip("|"):
        raise ValueError("refusing to build a match on no aliases")
    return f"regexp_like({_LOC}, '{alternation}')"


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
    return _matches("|".join(_rx(a) for a in aliases))


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


def _country_where() -> str:
    """The word "india" itself, minus the US places whose names contain it."""
    return f"({_any(('india',))}{_none(INDIA_EXCLUDE)})"


def _ind_where() -> str:
    """ISO alpha-3 "IND", in the positions where it is the country tag rather than a substring."""
    forms = _matches("|".join(_anchored(f) for f in IND_FORMS))
    return f"(({_LOC} = 'ind' OR {forms}){_none(IND_EXCLUDE)})"


def _subdivision_where() -> str:
    """Workday's "City, KA, IN" tail - the subdivision code plus the country, anchored to the end."""
    return _matches("|".join(_rx(f", {c}, in") + "$" for c in SUBDIVISIONS))


def where(place: str) -> str | None:
    """The where-fragment for a canonical place, or None if the place is unknown.

    ``place`` is "india", a :data:`REGIONS` key, or a :data:`CITIES` key.

    The country-level "india" rule is five things OR'd together (ADR-0024, extended by
    ADR-0086): the substring "india" minus :data:`INDIA_EXCLUDE`; ISO alpha-3 "IND" in its
    :data:`IND_FORMS` positions minus :data:`IND_EXCLUDE`; the ", {code}, in" subdivision tail;
    every city alias; and every state name. That is how the rule is *written*; the clause it
    compiles to has fewer parts, because every city without an :data:`EXCLUDE` guard shares one
    alternation with the states.

    Aliases are trusted constants — callers must never pass free text through this into SQL
    beyond the dict lookups here.
    """
    if place == "india":
        parts = [_country_where(), _ind_where(), _subdivision_where()]
        # Every city whose aliases carry no collision guard shares ONE alternation with the
        # states, because a guard is the only reason an alias needs a term of its own — and only
        # two of 68 cities have one. That is where the predicate count actually falls: 267 LIKEs
        # become 10 `regexp_like`s, of which this is the largest by far.
        plain: list[str] = []
        for city, aliases in CITIES.items():
            if EXCLUDE.get(city):
                parts.append(_city_where(city))
            else:
                plain.extend(aliases)
        parts.append(_any([*plain, *STATES]))
        return "(" + " OR ".join(p for p in parts if p) + ")"
    if place in REGIONS:
        return "(" + " OR ".join(_city_where(c) for c in REGIONS[place]) + ")"
    return _city_where(place)
