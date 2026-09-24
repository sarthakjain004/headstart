"""Suggest directory companies for what a person types into the Trends company picker.

A suggestion is only ever a candidate: the person picks each company, so a typed string never
resolves to a company on its own (ADR-0185). That is why the match can be loose — prefixes and
one typo — where ADR-0171's operator labels, which nobody checks, must match exactly.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

#: Legal-form words that tell two spellings of one company apart without naming it.
_LEGAL = frozenset(
    {
        "inc",
        "incorporated",
        "llc",
        "llp",
        "lp",
        "ltd",
        "limited",
        "plc",
        "corp",
        "corporation",
        "co",
        "company",
        "gmbh",
        "ag",
        "sa",
        "srl",
        "bv",
        "nv",
        "pvt",
        "private",
        "pty",
    }
)
_NOT_WORD = re.compile(r"[^0-9a-z]+")
#: A typo is only forgiven in a word this long; a short word one edit away is another word.
_TYPO_MIN = 5


def normalize(text: str) -> list[str]:
    """``text`` as comparable words: no case, accents, punctuation or trailing legal form."""
    folded = unicodedata.normalize("NFKD", text.casefold().replace("&", " and "))
    # Dots dropped rather than split on, so "S.A." and "D.R. Horton" read as the words they are.
    plain = "".join(ch for ch in folded if not unicodedata.combining(ch) and ch != ".")
    words = [word for word in _NOT_WORD.split(plain) if word]
    # Only a trailing legal form goes: "SA Power Networks" and "Co-op" keep their first word.
    kept = list(words)
    while kept and kept[-1] in _LEGAL:
        kept.pop()
    return kept or words  # "Private Limited" alone is still something to match


@dataclass(frozen=True)
class Candidate:
    """One directory company as the matcher sees it."""

    key: str  # the company's id: its first board_key
    name: str
    words: tuple[str, ...]
    openings: int


def tier(query: list[str], words: tuple[str, ...]) -> int | None:
    """How well ``query`` matches a company's words, best first: 0 exact, 1 name prefix,
    2 every query word starts a name word, 3 the same allowing one typo per long word."""
    if not query:
        return None
    if list(words) == query:
        return 0
    joined, typed = " ".join(words), " ".join(query)
    if joined.startswith(typed):
        return 1
    if all(any(w.startswith(q) for w in words) for q in query):
        return 2
    if all(any(w.startswith(q) or _near(q, w) for w in words) for q in query):
        return 3
    return None


def _near(typed: str, word: str) -> bool:
    """One edit from ``word`` or from its same-length prefix."""
    if len(typed) < _TYPO_MIN:
        return False
    return _one_edit(typed, word) or _one_edit(typed, word[: len(typed)])


def _one_edit(a: str, b: str) -> bool:
    """One substitution, insertion, deletion, or swap of two neighbouring letters ("googel")."""
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diffs = [k for k, (x, y) in enumerate(zip(a, b, strict=True)) if x != y]
        if len(diffs) == 1:
            return True
        return (
            len(diffs) == 2
            and diffs[1] == diffs[0] + 1
            and a[diffs[0]] == b[diffs[1]]
            and a[diffs[1]] == b[diffs[0]]
        )
    short, long_ = sorted((a, b), key=len)
    i = next((k for k, (x, y) in enumerate(zip(short, long_)) if x != y), len(short))
    return short[i:] == long_[i + 1 :]


#: Trailing words one employer's names differ by across its ATSes ("Micron" on Workday,
#: "Micron Technology" on Eightfold, 1,949 and 1,887 openings on 2026-09-24). Dropped only to
#: decide which suggestions are one name, never to match: typing "micron tech" still has to find
#: the entry that says it.
_TWIN_TAIL = frozenset({"technology", "technologies", "group", "holdings"})


def _twin_key(words: tuple[str, ...]) -> tuple[str, ...]:
    """``words`` with trailing `_TWIN_TAIL` words dropped, never below one word."""
    end = len(words)
    while end > 1 and words[end - 1] in _TWIN_TAIL:
        end -= 1
    return words[:end]


#: A word naming an ATS customer's test copy of its own site ("Jpmc Dev1", "Nvidia Sandbox2").
_TEST_TENANT = re.compile(r"(dev|test|uat|sandbox|staging|demo|preprod)\d*")


def _is_test_tenant(candidate: Candidate) -> bool:
    """A test copy of a real site, which no job seeker is looking for.

    Only one with no openings: "Dev Partners" is an employer, and the pattern alone would catch
    it. JPMorgan's four Oracle test tenants read 0 beside its real 1,716 (2026-09-24 critique),
    and "Nvidia Sandbox2" charted every category at −100%.
    """
    return candidate.openings == 0 and any(
        _TEST_TENANT.fullmatch(word) for word in candidate.words
    )


def suggest(query: str, candidates: list[Candidate], limit: int) -> list[Candidate]:
    """The best ``limit`` companies for ``query``: by tier, then most openings, then name.

    A test tenant is never offered (see :func:`_is_test_tenant`). One suggestion per name: of
    the entries whose names normalize alike, only the one with the
    most openings is offered. Most such twins are one employer's Boards on two ATSes, one
    mirroring the other ("NVIDIA Corporation" on Eightfold, "Nvidia" on Workday), and the
    directory cannot merge them because nothing but the name proves them one (ADR-0185). The
    cost is a same-named different employer the list no longer offers.
    """
    typed = normalize(query)
    ranked = []
    for candidate in candidates:
        rank = tier(typed, candidate.words)
        if rank is not None and not _is_test_tenant(candidate):
            ranked.append(
                (rank, -candidate.openings, candidate.name, candidate.key, candidate)
            )
    ranked.sort(key=lambda item: item[:4])
    # Names alike match at a close tier and the ranking puts more openings first within one, so
    # the entry kept for a name is its largest unless a twin matched the query better.
    seen: set[tuple[str, ...]] = set()
    kept = []
    for *_, candidate in ranked:
        key = _twin_key(candidate.words)
        if key not in seen:
            seen.add(key)
            kept.append(candidate)
    return kept[:limit]
