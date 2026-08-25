"""India place gazetteer for the location filter (query-time LIKE expansion, ADR-0024).

Why this exists: the location filter is a raw ``lower(location) LIKE '%term%'``, and the
live-index inventory (``experiment/india-location-filter/``, 2026-07-20: 24,964 India rows)
showed **47% of India jobs never contain the word "india"** — zoho/keka/ripplehire write
city-only strings ("Bangalore North", "Pune City"), and Bengaluru/Bangalore is a ~50/50
spelling split. This module expands a canonical place ("india" or a city) into the OR-chain
of every observed alias, so the filter stops lying.

Aliases are lowercase substrings matched with ``LIKE '%alias%'`` — every alias must be
unambiguous *as a substring of any world location string*. Traps vetted OUT of the
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

# Per-city NOT-LIKE guards for aliases that collide with a specific other place.
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
# served table: these two rules alone recover 432 India rows the city map could never reach,
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

# ISO-3166-2 subdivision codes, as workday writes them: "Vemagal, KA, IN". Anchored to the
# ", {code}, in" tail so the two-letter codes can never match loose text.
SUBDIVISIONS: tuple[str, ...] = (
    "ap",
    "ar",
    "as",
    "br",
    "cg",
    "ga",
    "gj",
    "hr",
    "hp",
    "jh",
    "ka",
    "kl",
    "mp",
    "mh",
    "mn",
    "ml",
    "mz",
    "nl",
    "od",
    "pb",
    "rj",
    "sk",
    "tn",
    "ts",
    "tr",
    "uk",
    "up",
    "wb",
    "dl",
    "ch",
    "py",
    "jk",
    "la",
    "an",
    "dh",
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


def _like_any(aliases: tuple[str, ...]) -> str:
    return " OR ".join(f"lower(location) LIKE '%{a}%'" for a in aliases)


def _city_where(city: str) -> str | None:
    aliases = CITIES.get(city)
    if not aliases:
        return None
    clause = f"({_like_any(aliases)})"
    for bad in EXCLUDE.get(city, ()):
        clause = f"({clause} AND lower(location) NOT LIKE '%{bad}%')"
    return clause


def _country_where() -> str:
    """The word "india" itself, minus the US places whose names contain it."""
    guards = "".join(f" AND lower(location) NOT LIKE '%{b}%'" for b in INDIA_EXCLUDE)
    return f"(lower(location) LIKE '%india%'{guards})"


def _ind_where() -> str:
    """ISO alpha-3 "IND", in the positions where it is the country tag rather than a substring."""
    forms = " OR ".join(f"lower(location) LIKE '{f}'" for f in IND_FORMS)
    guards = "".join(f" AND lower(location) NOT LIKE '%{b}%'" for b in IND_EXCLUDE)
    return f"((lower(location) = 'ind' OR {forms}){guards})"


def _subdivision_where() -> str:
    """Workday's "City, KA, IN" tail - the subdivision code plus the country, anchored to the end."""
    return (
        "("
        + " OR ".join(f"lower(location) LIKE '%, {c}, in'" for c in SUBDIVISIONS)
        + ")"
    )


def where(place: str) -> str | None:
    """The where-fragment for a canonical place, or None if the place is unknown.

    ``place`` is "india" (country-level: the word "india" minus the indiana/indianapolis
    trap, plus every city alias and state name), a :data:`REGIONS` key, or a
    :data:`CITIES` key. Aliases are trusted constants — callers must never pass free text
    through this into SQL beyond the dict lookups here.
    """
    if place == "india":
        parts = [_country_where(), _ind_where(), _subdivision_where()]
        parts += [_city_where(c) for c in CITIES]  # keys exist: no Nones
        parts.append(f"({_like_any(STATES)})")
        return "(" + " OR ".join(p for p in parts if p) + ")"
    if place in REGIONS:
        return "(" + " OR ".join(_city_where(c) for c in REGIONS[place]) + ")"
    return _city_where(place)
