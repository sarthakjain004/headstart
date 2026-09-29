"""What a Job's ``location`` says: tidied where it is stated, read from the description where it is not.

Two jobs, both about the served ``location`` column and neither about what a place *means* (that is
:mod:`headstart.search_filters.country_gazetteer`, which decides them both):

:func:`tidy` cleans a location an ATS stated. ``Job.__post_init__`` calls it, so every scraper's
Job passes through it once. It drops the template token ``BLANK`` (greenhouse writes
``BLANK,BLANK,Multiple Locations``: 17 rows on the audited table, 2026-09-29), says a repeated
neighbouring token once (``Mumbai, Mumbai, India`` is ``Mumbai, India``: 15,371 rows) and lists a
place once when it is listed twice (268 rows). Nothing else moves: a country shared by two places
of a list (``Boston, Massachusetts, USA; Irvine, California, USA``) is not a repeat, and neither is
a token that recurs without touching itself, so no place is ever lost, only the stutter.

:func:`from_description` reads a location from the description of a Job whose ATS stated none: an
explicit ``Location: <place>`` line, and only that. It is a *fact* fallback, not a derivation:
``doc_prep.stored_facts`` calls it every run, on the corpus row the description store has already
been written back into, so a row gains its place when its Board is next scraped, with no
``DERIVATIONS_VERSION`` sweep. A wrong place is worse than none, so the reader takes a value only
when the whole of it is a place the country gazetteer resolves, and refuses the readings the
gazetteer cannot settle (ADR-0345):

* the value must be nothing but places: each piece is at most four words, every word capitalised,
  and no word of it may be spare (``Cairo Type`` is ``Cairo`` plus a label word, not a place);
* a bare name several countries share (``Melbourne``, ``Perth``, ``Camden``) is refused unless the
  value carries an anchor: a country, a two-letter code (``Melbourne, FL``) or an Indian place,
  which the India gazetteer settles;
* a bare code is refused (``PAN`` is Panama, ``IT`` Italy, ``US or`` a sentence);
* a list that stops at a name the gazetteers do not know is refused, not cut short: ``Japan, Guam,
  S. Korea`` would otherwise serve ``Japan``.
"""

from __future__ import annotations

import re

from headstart.search_filters import country_gazetteer, india_gazetteer

#: Tokens a template writes for a part it has no value for. Greenhouse's ``BLANK`` is the only one
#: measured (17 rows, 2026-09-29); ``N/A`` and ``NA`` are not here because SuccessFactors writes
#: ISO country codes and ``NA`` is Namibia (23 rows).
_TEMPLATE_TOKENS = frozenset({"blank"})

_ENTRY_SEPARATOR = re.compile(r"(\s*;\s*)")
_TOKEN_SEPARATOR = re.compile(r"(\s*,\s*)")
_WS = re.compile(r"\s+")


def _key(text: str) -> str:
    return _WS.sub(" ", text).strip().casefold()


def _tidy_entry(entry: str) -> str:
    """One place without its template tokens and neighbouring repeats, the separators around
    what stays as written."""
    parts = _TOKEN_SEPARATOR.split(entry)
    kept: list[str] = []  # separator before each token, then the token
    last = None
    for index in range(0, len(parts), 2):
        token = parts[index].strip()
        if not token or _key(token) in _TEMPLATE_TOKENS or _key(token) == last:
            continue
        kept.append((parts[index - 1] if kept and index else "") + token)
        last = _key(token)
    return "".join(kept)


def tidy(location: str | None) -> str | None:
    """``location`` without template tokens, neighbouring repeats or a repeated place; None when
    nothing is left. What stays is written as it was stated: separators and spacing are kept."""
    if not location or not location.strip():
        return None
    parts = _ENTRY_SEPARATOR.split(location.strip())
    kept: list[str] = []  # separator before each place, then the place
    seen: set[str] = set()
    for index in range(0, len(parts), 2):
        entry = _tidy_entry(parts[index])
        if not entry or _key(entry) in seen:
            continue
        seen.add(_key(entry))
        kept.append((parts[index - 1] if kept and index else "") + entry)
    return "".join(kept) or None


# --- reading a place ---------------------------------------------------------------------------

#: Lower-case words that sit inside a place name ("Rio de Janeiro", "Stratford upon Avon").
_PARTICLES = frozenset(
    {"de", "del", "da", "do", "of", "la", "le", "el", "al", "van", "von", "den"}
    | {"der", "du", "des", "di", "the", "on", "upon", "am", "im", "bei", "en", "y", "e"}
)
#: A direction that names a part of a place ("Andheri East"), so it is not a spare word.
_DIRECTIONS = frozenset({"east", "west", "north", "south", "central"})
#: Words the gazetteers resolve that name no place on their own.
_NOT_PLACES = frozenset({"indian", "american", "european", "asian", "african"})
_COUNTRY_NAMES = frozenset(
    {country.name.lower() for country in country_gazetteer.COUNTRIES.values()}
    | {"india", "usa", "uk", "us", "u.s.", "u.s.a.", "uae"}
)
_PIECE_SEPARATOR = re.compile(
    r"(\s*(?:,\s*(?:and|or)\b|,|/|;|&|\band\b|\bor\b)\s*)", re.IGNORECASE
)
_TITLE_CASE_PHRASE = re.compile(r"[A-Z][\w.'\-]*(?: [A-Z][\w.'\-]*){0,2}")


def _kind(piece: str) -> str | None:
    """``"strong"`` for a piece that is a place of four letters or more, ``"weak"`` for a two-letter
    upper-case code (``FL``, ``UK``), None for anything else."""
    words = piece.split()
    if not 1 <= len(words) <= 4 or piece.casefold() in _NOT_PLACES:
        return None
    for word in words:
        if word.casefold() in _PARTICLES:
            continue
        if not (word[0].isupper() or word[0].isdigit()) or word.count("-") >= 2:
            return None
        if word.endswith(":"):
            return None
    countries = country_gazetteer.classify(piece)
    if not countries:
        return None
    if len(words) > 1 and (
        (
            words[-1].casefold() not in _DIRECTIONS
            and country_gazetteer.classify(" ".join(words[:-1])) == countries
        )
        or country_gazetteer.classify(" ".join(words[1:])) == countries
    ):
        return None  # a word of it is spare: a place, then a label or a noun
    letters = re.sub(r"[^A-Za-z]", "", piece)
    if len(letters) >= 4:
        return "strong"
    return "weak" if piece.isupper() and len(letters) == 2 else None


def _anchored(piece: str, kind: str) -> bool:
    """Whether the piece settles which country a name is in."""
    return (
        kind == "weak"
        or piece.casefold() in _COUNTRY_NAMES
        or bool(india_gazetteer.classify(piece))
    )


def _leading_place(piece: str) -> str | None:
    """The longest leading words of ``piece`` that are a place: ``Pune`` of ``Pune Experience``."""
    words = piece.split()
    for count in range(len(words) - 1, 0, -1):
        candidate = " ".join(words[:count])
        if _kind(candidate):
            return candidate
    return None


def _read_place(text: str, *, anchored: bool) -> str | None:
    """The leading run of ``text`` that is a list of places, or None when it is not one.

    Pieces are split on commas, slashes, ``&``, ``and`` and ``or``; the run ends at the first piece
    that is not a place, keeping that piece's leading place (``Pune`` of ``Pune Experience: 5``). A
    piece that is only a short capitalised name the gazetteers do not know is a list element we
    cannot read, so the whole value is refused rather than cut short.
    """
    parts = _PIECE_SEPARATOR.split(text.strip())
    out: list[str] = []
    pieces: list[tuple[str, str]] = []
    for index in range(0, len(parts), 2):
        piece = parts[index].strip()
        if not piece:
            break
        joined = parts[index - 1] if index else ""
        if kind := _kind(piece):
            out.append(joined + piece)
            pieces.append((piece, kind))
            continue
        if lead := _leading_place(piece):
            out.append(joined + lead)
            pieces.append((lead, _kind(lead) or "weak"))
        elif index and _TITLE_CASE_PHRASE.fullmatch(piece):
            return None
        break
    if not pieces or all(kind != "strong" for _, kind in pieces):
        return None
    if anchored and not any(_anchored(piece, kind) for piece, kind in pieces):
        return None
    return _WS.sub(" ", "".join(out)).strip()


def is_place(text: str) -> bool:
    """Whether ``text`` reads as a place a structured field stated: a list of short capitalised
    names of which at least one is a place the country gazetteer resolves.

    Looser than :func:`from_description` by one rule: a name the gazetteer does not know
    (``Columbia`` in ``Columbia, South Carolina``) is allowed beside a known one, because a field
    states the place and the question is only whether this text is one or a subtitle
    (``EA SPORTS NHL``, ``Python, SQL``), which has no known place at all.
    """
    parts = _PIECE_SEPARATOR.split(text.strip())
    pieces = [parts[index].strip() for index in range(0, len(parts), 2)]
    if not all(pieces):
        return False
    known = False
    for piece in pieces:
        if _kind(piece) == "strong":
            known = True
        elif not _TITLE_CASE_PHRASE.fullmatch(piece):
            return False
    return known


# --- reading a location out of a description ---------------------------------------------------

#: How much of the description is searched: the head, where a posting states its details.
_HEAD = 1500
_LABEL = re.compile(
    r"(?<![A-Za-z])(?:(?:job|work|office|primary|preferred|posting)\s+)?locations?"
    r"\s*(?:\(s\))?\s*[:：]\s*",
    re.IGNORECASE,
)
_PARENTHESIS = re.compile(r"\([^)]*\)")
_WORKPLACE_TYPE = re.compile(
    r"\(?\b(?:remote|hybrid|on-?site|wfo|wfh|work from (?:office|home)|in[- ]office|flexible)\b\)?",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"(?<!\bSt)(?<!\bMt)(?<!\bFt)[.!?]\s")
_LINE_END = re.compile(r"\s+[-–—|•·]\s+|\s*[|•·]\s*|[–—]")
_VALUE_WINDOW = 120


def from_description(description: str | None) -> str | None:
    """The place an explicit ``Location: <place>`` line in the description head states, else None.

    The first labelled line decides: a later one is as likely a second office as this job's, and
    ``Location: Remote`` states no place, so it is not skipped for the next line. See the module
    docstring for what is refused.
    """
    if not description:
        return None
    head = description[:_HEAD]
    label = _LABEL.search(head)
    if label is None:
        return None
    value = head[label.end() : label.end() + _VALUE_WINDOW]
    value = _PARENTHESIS.sub(" ", value)
    for boundary in (_SENTENCE_END, _LINE_END):
        if match := boundary.search(value):
            value = value[: match.start()]
    value = _WORKPLACE_TYPE.sub(" ", value)
    value = _WS.sub(" ", value).strip(" ,;:-–—./")
    return tidy(_read_place(value, anchored=True)) if value else None
