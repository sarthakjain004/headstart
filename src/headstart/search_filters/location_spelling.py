"""How the ``location`` Search filter reads a term: accents folded, a city's other spellings tried
(ADR-0344).

The filter was ``lower(location) LIKE '%term%'``: the letters typed, nothing else. Measured on
the served table (v18, 500,167 rows, 2026-09-29) "Zurich" reached 474 of 777 Zurich rows, "Krakow"
709 of 1,302, "Bangalore" 15,035 of 29,224 Bengaluru rows, "Gurugram" 2,251 of 3,645, and
"istanbul" missed the 130 rows spelled "İstanbul", whose lowercase is two characters. A term the
rules below leave as typed still compiles to that ``LIKE``. One they change compiles to one
``regexp_like(location, …)`` that reads the term three ways:

1. **Folded.** The term loses its accents (:func:`fold`) and each letter of a word the table
   spells with accents becomes a class of every spelling of that letter, so "zurich" and "zürich"
   are the same pattern (``z[uüúù…]rich``) and match "Zurich" and "Zürich" alike, whichever was
   typed. The folding is in the pattern, not the data: no column, and no per-row work. Measured
   warm on the table, ``translate(lower(location), …)`` over the rows cost 250 ms a count where
   ``LIKE`` costs 33; the classes cost 30.
2. **As the city's other names** (:data:`GROUPS`, :data:`ONE_WAY`). A whole word of the term that
   is one spelling of a renamed or re-spelled place ("Bangalore", "München", "St.") is also
   tried as its others, each a whole word of its own in the row, so "Madras" for Chennai is not
   the "Madrasa" of a street. Only the term's own text stays a substring, as it always was.
3. **Literally.** ``%``, ``_``, ``\\`` and every regex metacharacter mean themselves, as they did
   under ``LIKE`` once :func:`headstart.search_filters.compiler._like` escaped them.

**Why a term can stay a ``LIKE``.** On a ranked page, where the engine filters before it ranks,
a ``regexp_like`` costs more than a ``LIKE``: measured warm on the table (CPU of the query, 15
runs interleaved), "london" 78 ms as ``LIKE`` and 100 as ``(?i)`` regex, "bangalore" 78 and 159.
Counting the same rows costs the same either way, so this is only the page. Most terms need
neither rule, so :func:`pattern` answers None for a term that has no accent, names no renamed
place and can meet no accent the table's words carry, and the compiler keeps its ``LIKE``.
Those words are :attr:`IndexCapabilities.accented_words
<headstart.search_filters.compiler.IndexCapabilities>`: the 868 words spelled with an accented
letter in the table's 11,032 rows above, read once by ``JobSearch`` as it reads the ATS list
(:func:`accented_words`), so they cannot go stale. A term word meets an accent only where it lines
up with one (:func:`_spans_an_accent`): "san" of "san francisco" is not the "sán" of "Sánchez".

The same regex string drives both sides, as in :mod:`headstart.search_filters.country_gazetteer`:
:func:`pattern` is what DataFusion runs and :func:`matches` what ``re`` runs, so they can
disagree only where the two engines do; ``test_the_compiled_clause_agrees_with_matches_on_a_real_table`` runs
them against each other.

Every spelling below was read off the served table's own ``location`` values (2026-09-29),
whole-word and accent-folded; the counts are in ADR-0344. A group is two-way when neither of its
spellings names another place in more than about one row in fifty, and one-way when one does:
"Vienna" is asked for "Wien", but "Wien" is not asked for the Vienna of Virginia.
"""

from __future__ import annotations

import itertools
import re
import unicodedata
from collections.abc import Collection, Iterable
from functools import cache

#: A term is cut to this many characters of what the user typed, before anything reads it.
MAX_TERM = 60

#: The most other spellings one term is tried as. A term holding three renamed cities has
#: 2 x 2 x 2 spellings; the cap keeps a clause a few KB whatever the user types.
MAX_VARIANTS = 8

#: Letters that fold to more or fewer letters than one accent-strip leaves, or to none: each
#: read off the served table (ø 201 rows, ł 454, ı 20, İ 157, æ 33, ß 59).
_SPELT_OUT = {
    "ø": "o", "ł": "l", "đ": "d", "ð": "d", "ħ": "h", "ı": "i", "İ": "i",
    "æ": "ae", "œ": "oe",
}  # fmt: skip

#: The letters a term folds to two of, and the one letter each is also written as.
_DIGRAPHS = {"ss": "ß", "ae": "æ", "oe": "œ"}

#: A word: a run of letters, whatever the script. The vocabulary and the term are cut alike.
_WORD = re.compile(r"[^\W\d_]+")


def _plain(char: str) -> str:
    """``char`` without its accents when that leaves ASCII letters, else ``char`` itself: "é" is
    "e", but "й" and a Thai vowel mark stay what they are."""
    stripped = "".join(
        c for c in unicodedata.normalize("NFKD", char) if not unicodedata.combining(c)
    )
    return stripped if stripped and stripped.isascii() else char


def fold(text: str) -> str:
    """``text`` lowercased with every Latin letter's accents gone: "Zürich" is "zurich",
    "Łódź" "lodz", "Straße" "strasse", "İSTANBUL" "istanbul". Other scripts are only lowercased."""
    folded = []
    for char in unicodedata.normalize("NFC", text):
        if char in _SPELT_OUT:
            folded.append(_SPELT_OUT[char])
            continue
        for lower in char.casefold():
            folded.append(_SPELT_OUT.get(lower) or _plain(lower))
    return "".join(folded)


def _marked(word: str) -> str:
    """``word`` folded, with each letter an accent (or a ligature) made written in capitals:
    "Zürich" is "zUrich", "Straße" "straSS"."""
    marked = []
    for char in unicodedata.normalize("NFC", word):
        plain = fold(char)
        marked.append(plain.upper() if plain != char.lower() else plain)
    return "".join(marked)


def accented_words(locations: Iterable[str | None]) -> set[str]:
    """Every word in ``locations`` that is spelled with an accented Latin letter, as
    :func:`_marked` writes it: the words a term is folded against, read off the table's
    ``location`` column. "Zürich" and "ZÜRICH" are the one word "zUrich"."""
    found = set()
    for location in locations:
        for word in _WORD.findall(location or ""):
            marked = _marked(word)
            if marked != marked.lower() and marked.isascii():
                found.add(marked)
    return found


def _classes() -> dict[str, str]:
    """Each plain letter with every accented Latin letter that folds to it, as a regex class:
    'u' is ``[uùúûüūů…]``. Read from Unicode's Latin blocks; the letters the served table
    carries are all in it (``test_fold_reads_every_accented_letter…``)."""
    members: dict[str, set[str]] = {}
    for code in (*range(0xC0, 0x250), *range(0x1E00, 0x1F00)):
        lower = chr(code).lower()
        plain = fold(lower)
        if len(plain) == 1 and plain != lower and plain.isascii() and plain.isalpha():
            members.setdefault(plain, set()).add(lower)
    for spelt, plain in _SPELT_OUT.items():
        if len(plain) == 1:
            members.setdefault(plain, set()).add(spelt)
    return {
        plain: "[" + plain + "".join(sorted(others)) + "]"
        for plain, others in members.items()
    }


_CLASS_OF = _classes()

#: Every group is one place written more than one way; each spelling is folded, whole-word.
#: Rows are counted whole-word on the served table (2026-09-29), the smaller spelling first
#: where a pair is uneven: Bombay 35 of 5,720, Calcutta 2 of 745, Poona 0 of 11,580.
GROUPS: tuple[tuple[str, ...], ...] = (
    ("bengaluru", "bangalore"),  # 14,370 and 15,035
    ("gurugram", "gurgaon"),  # 2,251 and 1,415
    ("mumbai", "bombay"),
    ("chennai", "madras"),
    ("kolkata", "calcutta"),
    ("pune", "poona"),
    ("kochi", "cochin"),
    ("thiruvananthapuram", "trivandrum"),  # 120 and 322
    ("vadodara", "baroda"),
    ("prayagraj", "allahabad"),
    ("visakhapatnam", "vishakhapatnam", "vizag"),
    ("bhubaneswar", "bhubaneshwar"),
    ("ho chi minh city", "ho chi minh", "saigon", "hcmc"),
    ("hanoi", "ha noi"),  # 368 and 231
    ("kyiv", "kiev"),
    ("xi'an", "xian"),
    ("munich", "munchen", "muenchen"),  # 1,474 and 554 and 5
    ("cologne", "koln", "koeln"),
    ("prague", "praha"),  # 843 and 131
    ("nuremberg", "nurnberg", "nuernberg"),
    ("lisbon", "lisboa"),  # 1,369 and 655
    ("milan", "milano"),
    ("turin", "torino"),
    ("geneva", "geneve"),
    ("brussels", "bruxelles", "brussel"),
    ("antwerp", "antwerpen"),
    ("the hague", "den haag"),
    ("gothenburg", "goteborg"),  # 93 and 169
    ("copenhagen", "kobenhavn"),  # 401 and 125
    ("bucharest", "bucuresti"),  # 1,404 and 262
    ("belgrade", "beograd"),
    ("krakow", "cracow"),
    ("ghent", "gent"),
    ("czechia", "czech republic"),
    ("turkey", "turkiye"),
    ("mexico city", "ciudad de mexico", "cdmx"),  # 1,127 and 358 and 152
    ("new york city", "nyc", "new york, ny"),
    ("washington dc", "washington d.c.", "washington, dc", "washington, d.c."),  # 276, 211, 2,521
    ("saint", "st.", "st"),  # "St. Louis" 751, "Saint Louis" 186, "St Louis" 108
    ("fort", "ft.", "ft"),  # "Fort Meade" 993, "Ft. Meade" 117
)  # fmt: skip

#: A spelling asked for others that do not ask for it back: the first also names another place
#: ("Vienna" Austria 73 and Virginia 64 of 150 sampled rows; "Warszawa" would pull the 100 rows
#: of Warsaw, Indiana of 2,219), or the target is a shorter name of the same place, so its rows
#: hold the longer one's.
ONE_WAY: dict[str, tuple[str, ...]] = {
    "vienna": ("wien",),
    "warsaw": ("warszawa",),
    "rome": ("roma",),
    "florence": ("firenze",),
    "naples": ("napoli",),
    "tel aviv-yafo": ("tel aviv",),
    "cluj-napoca": ("cluj",),
    "hong kong sar": ("hong kong",),
    "frankfurt am main": ("frankfurt",),
    "new delhi": ("delhi",),
}


def _also() -> dict[str, tuple[str, ...]]:
    also = {
        spelling: tuple(other for other in group if other != spelling)
        for group in GROUPS
        for spelling in group
    }
    return also | ONE_WAY


_ALSO = _also()

#: Every spelling of :data:`GROUPS` and :data:`ONE_WAY`, as the one name it is counted under:
#: its group's first, and a one-way spelling's own. A place counted within one country cannot be
#: the other place a one-way spelling also names ("Wien" under Austria is Vienna).
_PLACE_NAME = {spelling: group[0] for group in GROUPS for spelling in group} | {
    spelling: first
    for first, others in ONE_WAY.items()
    for spelling in (first, *others)
}
_PLACE_SPELLING = re.compile(
    r"(?<![a-z0-9])(?:"
    + "|".join(re.escape(k) for k in sorted(_PLACE_NAME, key=lambda k: (-len(k), k)))
    + r")(?![a-z0-9])"
)


def place_key(place: str) -> str:
    """``place`` as one key for all its spellings that the filter reads alike: folded, each
    renamed or re-spelled word as its group's first. "Bangalore", "Bengaluru" and "BENGALURU" are
    one key, as are "Zürich" and "Zurich", and "Kraków" and "Krakow" (ADR-0367)."""
    folded = " ".join(fold(place).split())
    return _PLACE_SPELLING.sub(lambda m: _PLACE_NAME[m.group()], folded)


#: One whole word (or phrase) of a term that is a key of :data:`_ALSO`, longest first so "ho
#: chi minh city" is read before "ho chi minh".
_KEYS = re.compile(
    r"(?<![a-z0-9])(?:"
    + "|".join(re.escape(k) for k in sorted(_ALSO, key=lambda k: (-len(k), k)))
    + r")(?![a-z0-9])"
)

#: The edges of a spelling tried in place of the term's own: a whole word of the row.
_EDGE_BEFORE = "(?:^|[^a-z0-9])"
_EDGE_AFTER = "(?:[^a-z0-9]|$)"


def variants(folded: str) -> list[str]:
    """The other spellings of ``folded`` (a folded term): each word of it in :data:`_ALSO` swapped
    for its others, at most :data:`MAX_VARIANTS`. A variant holding the term itself is left out,
    since the term as a substring already finds its rows."""
    spans = list(_KEYS.finditer(folded))
    if not spans:
        return []
    swaps = [[span.group(), *_ALSO[span.group()]] for span in spans]
    found: dict[str, None] = {}
    for choice in itertools.product(*swaps):
        text, at = [], 0
        for span, spelling in zip(spans, choice):
            text += [folded[at : span.start()], spelling]
            at = span.end()
        text.append(folded[at:])
        variant = "".join(text)
        if folded not in variant:
            found[variant] = None
        if len(found) == MAX_VARIANTS:
            break
    return list(found)


def _spellings(word: str) -> str:
    """``word`` (folded letters) as a regex: each letter its class of spellings, "ss", "ae" and
    "oe" also the one letter ß, æ, œ."""
    out, at = [], 0
    while at < len(word):
        pair = word[at : at + 2]
        if pair in _DIGRAPHS:
            letters = "".join(_CLASS_OF.get(c, c) for c in pair)
            out.append(f"(?:{letters}|{_DIGRAPHS[pair]})")
            at += 2
        else:
            out.append(_CLASS_OF.get(word[at], re.escape(word[at])))
            at += 1
    return "".join(out)


def _spans_an_accent(
    word: str, left_open: bool, right_open: bool, accented: Collection[str]
) -> bool:
    """Whether ``word`` can meet an accent in one of the table's accented words. A word the term
    starts with can begin inside a row's word (``left_open``), one it ends with can end inside one
    (``right_open``); a word between separators is a whole word of the row. Only a match that
    covers a capital of :func:`_marked` reads a letter the plain ``LIKE`` would miss, so "san"
    of "san francisco" is not "sán" of "Sánchez": the term would need "sán francisco"."""
    size = len(word)
    for known in accented:
        low = known.lower()
        if any(
            known[at : at + size] != low[at : at + size]
            for at in _alignments(low, word, left_open, right_open)
        ):
            return True
    return False


def _alignments(low: str, word: str, left_open: bool, right_open: bool) -> list[int]:
    """The offsets at which ``word`` lines up with ``low`` as its neighbours in the term allow."""
    if left_open and right_open:
        return [m.start() for m in re.finditer(f"(?={re.escape(word)})", low)]
    if left_open:
        return [len(low) - len(word)] if low.endswith(word) else []
    if right_open:
        return [0] if low.startswith(word) else []
    return [0] if low == word else []


def _body(folded: str, accented: Collection[str]) -> tuple[str, bool]:
    """``folded`` as a regex, and whether any letter of it became a class: the words the table
    spells with accents get their classes, everything else is written as it is."""
    out, at, classed = [], 0, False
    for word in _WORD.finditer(folded):
        out.append(re.escape(folded[at : word.start()]))
        if _spans_an_accent(
            word.group(), word.start() == 0, word.end() == len(folded), accented
        ):
            out.append(_spellings(word.group()))
            classed = True
        else:
            out.append(re.escape(word.group()))
        at = word.end()
    out.append(re.escape(folded[at:]))
    return "".join(out), classed


def pattern(term: str, accented: Collection[str]) -> str | None:
    """The regex for ``term``, case-insensitive, that both engines run: the term's own folded
    text anywhere in the row, or one of its other spellings as a whole word. ``accented`` is the
    words the table spells with accents (:func:`accented_words`). None when the term is read as
    typed and nothing else (no accent of its own, no word the table spells with accents, no
    renamed place): it is then the plain substring ``lower(location) LIKE``."""
    text = term[:MAX_TERM]
    folded = fold(text)
    others = variants(folded)
    body, classed = _body(folded, accented)
    if not others and not classed and folded == text.lower():
        return None
    parts = [body]
    for other in others:
        spelled, _ = _body(other, accented)
        parts.append(f"{_EDGE_BEFORE}{spelled}{_EDGE_AFTER}")
    return "(?i)" + "|".join(parts)


@cache
def _compiled(regex: str) -> re.Pattern[str]:
    return re.compile(regex)


def matches(term: str, location: str | None, accented: Collection[str]) -> bool:
    """Whether ``location`` reads as ``term`` by exactly the rule the compiler turns into SQL."""
    if not location:
        return False
    regex = pattern(term, accented)
    if regex is None:
        return term[:MAX_TERM].lower() in location.lower()
    return _compiled(regex).search(location) is not None
