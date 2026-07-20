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
_EXCLUDE: dict[str, tuple[str, ...]] = {
    "surat": ("surat thani",),  # Thailand
    "thane": ("kalyani",),  # 'kalyan' is inside Pune's Kalyani Nagar
}

# Unambiguous state/UT names — country-level match only (catches "Karnataka, IN" residue).
# Diacritic variants are the ones actually observed in workday strings. "punjab" is
# deliberately absent (Pakistan). "goa" is already a city entry.
_STATES: tuple[str, ...] = (
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


def _like_any(aliases: tuple[str, ...]) -> str:
    return " OR ".join(f"lower(location) LIKE '%{a}%'" for a in aliases)


def _city_where(city: str) -> str | None:
    aliases = CITIES.get(city)
    if not aliases:
        return None
    clause = f"({_like_any(aliases)})"
    for bad in _EXCLUDE.get(city, ()):
        clause = f"({clause} AND lower(location) NOT LIKE '%{bad}%')"
    return clause


def where(place: str) -> str | None:
    """The where-fragment for a canonical place, or None if the place is unknown.

    ``place`` is "india" (country-level: the word "india" minus the indiana/indianapolis
    trap, plus every city alias and state name), a :data:`REGIONS` key, or a
    :data:`CITIES` key. Aliases are trusted constants — callers must never pass free text
    through this into SQL beyond the dict lookups here.
    """
    if place == "india":
        parts = [
            "(lower(location) LIKE '%india%' AND lower(location) NOT LIKE '%indiana%')"
        ]
        parts += [_city_where(c) for c in CITIES]  # keys exist: no Nones
        parts.append(f"({_like_any(_STATES)})")
        return "(" + " OR ".join(p for p in parts if p) + ")"
    if place in REGIONS:
        return "(" + " OR ".join(_city_where(c) for c in REGIONS[place]) + ")"
    return _city_where(place)
