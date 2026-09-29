"""Which tech skills a job description mentions, by the curated vocabulary in
`config/tech_skills.json` (ADR-0324).

**Why a vocabulary, not a model.** The requirements view counts skills over a few hundred
descriptions per request on the Space's CPU, with no LLM (the MCP server answers only from what the
Space serves). A named list is also what makes a count checkable: every skill the answer names is a
term someone chose and measured, and nothing of the description itself is ever repeated.

**How a description is read.** It is cut into tokens: a word with any dots joining it to more
letters (``Node.js``, ``ASP.NET``, ``.NET``) and any trailing ``+`` or ``#`` (``C++``, ``C#``,
``Security+``), while ``/``, ``-`` and ``&`` are tokens of their own, so "Java/Kotlin" is two
languages and "Python-based" still names Python. A term matches whole tokens, never part of one:
"trust" is not Rust, "HTML" is not ML, "JavaScript" is not Java. At each place the longest term
wins, and matching resumes after it, so "React Native" is not also React. A term's own options:

- ``cased`` matches only its own capitalisation: "Go", "Rust", "Spark", "React", "SAS";
- ``listed`` counts it only beside another counted skill, separated by nothing but commas,
  slashes, brackets and "and"/"or": "Python, R and SQL" names R, "Series C" names no C. A list of
  kinds in its place asks for a neighbour of one of those kinds: "MySQL, Oracle" names the
  database, "SAP/Oracle" does not;
- ``not_before`` and ``not_after`` drop a match that the given text follows or precedes
  ("Excel in", "West Java").

A description written in Title Case ("Ensuring Swift And Efficient Problem Resolution") makes
every word look like a name, so there a ``cased`` term that is a capitalised word ("Swift",
"Spark", "React") counts only in a list, as if it were ``listed``.

A skill whose term is in the employer's own company name is not counted for that job, so
Salesforce's boilerplate is not a Salesforce skill.

**Where the file is.** Found as `role_families.py` finds its list (ADR-0274): the wheel's copy
beside this module (`pyproject.toml` force-includes it), else ``config/`` in the nearest ancestor
directory, which is the repository on a checkout and ``/app`` in the Space's image. This walk is one
of several copies of the same config locator (`space_mcp/role_families`, `search_filters/fx`,
`boards/company_name` and others); making them one helper touches modules outside this view, so it
is left to its own change (ADR-0331).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cache
from itertools import pairwise
from pathlib import Path
from typing import Any

_FILE_NAME = "tech_skills.json"

#: A token: a word whose inner dots join more letters or digits, with an optional leading dot
#: (".NET") and any trailing "+" or "#"; or one of the separators a term may contain.
_TOKEN = re.compile(r"\.?[A-Za-z0-9](?:[A-Za-z0-9_]|\.(?=[A-Za-z0-9]))*[+#]*|[/&-]")

#: What may stand between two skills of one list for a ``listed`` term to count: separators and
#: at most one joining word ("Python, R and SQL", "C/C++", "Go (Golang)").
_LIST_GAP = re.compile(
    r"[\s,;/&|()+\-]*(?:(?:and|or|and/or|plus|as well as)[\s,;/&|()+\-]*)?",
    re.IGNORECASE,
)
_LIST_GAP_MAX_CHARS = 20

#: A description is in Title Case when more than half of its words of four letters or more are
#: capitalised (0.7% of 5,000 sampled descriptions, 2026-09-29), counted once it has this many.
_TITLE_CASE_SHARE = 0.5
_TITLE_CASE_MIN_WORDS = 30
_LONG_WORD = re.compile(r"[A-Za-z]{4,}")


def _candidates() -> tuple[Path, ...]:
    """Where the file might be, nearest first; walked, never indexed (as `role_families`)."""
    here = Path(__file__).resolve()
    return (
        here.parent / _FILE_NAME,
        *(ancestor / "config" / _FILE_NAME for ancestor in here.parents),
    )


def located() -> Path:
    """The first candidate that exists, else the wheel's path (which then fails to open)."""
    candidates = _candidates()
    return next((path for path in candidates if path.is_file()), candidates[0])


def _tokens(text: str) -> list[re.Match[str]]:
    return list(_TOKEN.finditer(text))


def _capitalised(word: str) -> bool:
    return word[:1].isupper() and word[1:].islower()


def _in_title_case(text: str) -> bool:
    words = _LONG_WORD.findall(text)
    return len(words) >= _TITLE_CASE_MIN_WORDS and (
        sum(map(_capitalised, words)) > _TITLE_CASE_SHARE * len(words)
    )


@dataclass(frozen=True)
class Skill:
    name: str
    kind: str


@dataclass(frozen=True)
class _Term:
    skill: int
    #: The term's tokens, lower-cased: what a place in the text is looked up by.
    folded: tuple[str, ...]
    #: The term's tokens as written, compared only when the term is ``cased``.
    written: tuple[str, ...]
    cased: bool
    listed: bool
    #: The kinds a ``listed`` term's neighbour must be of, or empty for any kind.
    listed_with: frozenset[str]
    not_before: tuple[str, ...]
    not_after: tuple[str, ...]


def _read_term(spec: str | dict[str, Any], skill: int) -> _Term:
    if isinstance(spec, str):
        spec = {"text": spec}
    written = tuple(m.group() for m in _tokens(spec["text"]))
    listed = spec.get("listed", False)
    if not written:
        raise ValueError(f"term {spec['text']!r} has no tokens")
    return _Term(
        skill=skill,
        folded=tuple(token.lower() for token in written),
        written=written,
        cased=bool(spec.get("cased")),
        listed=bool(listed),
        listed_with=frozenset(listed if isinstance(listed, list) else ()),
        not_before=tuple(s.strip().lower() for s in spec.get("not_before", ())),
        not_after=tuple(s.strip().lower() for s in spec.get("not_after", ())),
    )


def _starts_with(text: str, prefix: str) -> bool:
    """Whether ``text`` begins with ``prefix`` as a whole word when the prefix ends in one."""
    if not text.startswith(prefix):
        return False
    rest = text[len(prefix) : len(prefix) + 1]
    return not (prefix[-1:].isalnum() and rest.isalnum())


def _ends_with(text: str, suffix: str) -> bool:
    if not text.endswith(suffix):
        return False
    before = text[-len(suffix) - 1 : -len(suffix)]
    return not (suffix[:1].isalnum() and before.isalnum())


@dataclass(frozen=True)
class _Hit:
    term: _Term
    start: int
    end: int


class Vocabulary:
    """The skills of one vocabulary file, and :meth:`mentioned`, which finds them in a text."""

    def __init__(self, document: dict[str, Any]):
        self.kinds: dict[str, str] = dict(document["kinds"])
        skills: list[Skill] = []
        by_first: dict[str, list[_Term]] = {}
        self._terms_of: list[list[_Term]] = []
        for number, entry in enumerate(document["skills"]):
            if entry["kind"] not in self.kinds:
                raise ValueError(
                    f"{entry['name']!r} has unknown kind {entry['kind']!r}"
                )
            skills.append(Skill(entry["name"], entry["kind"]))
            terms = [_read_term(spec, number) for spec in entry["terms"]]
            self._terms_of.append(terms)
            for term in terms:
                by_first.setdefault(term.folded[0], []).append(term)
        names = [skill.name for skill in skills]
        if len(set(names)) != len(names):
            raise ValueError("a skill name is listed twice")
        self.skills: tuple[Skill, ...] = tuple(skills)
        # Longest first, so the longest term at a place is the one tried first.
        self._by_first = {
            first: sorted(terms, key=lambda t: -len(t.folded))
            for first, terms in by_first.items()
        }

    def _term_at(
        self, text: str, tokens: list[re.Match[str]], folded: list[str], i: int
    ) -> _Term | None:
        for term in self._by_first.get(folded[i], ()):
            size = len(term.folded)
            if i + size > len(tokens) or tuple(folded[i : i + size]) != term.folded:
                continue
            span = tokens[i : i + size]
            if term.cased and tuple(m.group() for m in span) != term.written:
                continue
            # A term of several words matches them only when nothing but spaces parts them.
            if any(text[a.end() : b.start()].strip() for a, b in pairwise(span)):
                continue
            after = text[span[-1].end() : span[-1].end() + 40].lstrip().lower()
            if any(_starts_with(after, s) for s in term.not_before):
                continue
            before = text[max(0, span[0].start() - 40) : span[0].start()].rstrip()
            if any(_ends_with(before.lower(), s) for s in term.not_after):
                continue
            return term
        return None

    def _hits(self, text: str) -> list[_Hit]:
        tokens = _tokens(text)
        folded = [m.group().lower() for m in tokens]
        hits, i = [], 0
        while i < len(tokens):
            term = self._term_at(text, tokens, folded, i)
            if term is None:
                i += 1
                continue
            size = len(term.folded)
            hits.append(_Hit(term, tokens[i].start(), tokens[i + size - 1].end()))
            i += size
        return hits

    def _beside(self, text: str, hit: _Hit, neighbour: _Hit) -> bool:
        """Whether ``neighbour`` stands next to ``hit`` in one list, and is of a kind ``hit``
        accepts."""
        wanted = hit.term.listed_with
        if wanted and self.skills[neighbour.term.skill].kind not in wanted:
            return False
        first, second = sorted((hit, neighbour), key=lambda h: h.start)
        gap = text[first.end : second.start]
        return len(gap) <= _LIST_GAP_MAX_CHARS and _LIST_GAP.fullmatch(gap) is not None

    def _counted(self, text: str, hits: list[_Hit]) -> list[_Hit]:
        """The hits that count: every term not ``listed``, and each ``listed`` one beside a hit
        that counts (read again until nothing changes, so "C, Go, R" chains from C++ onward). In
        Title Case text a cased capitalised word is read as ``listed`` too."""
        title_case = _in_title_case(text)
        counted = [
            not (
                hit.term.listed
                or (
                    title_case
                    and hit.term.cased
                    and len(hit.term.written) == 1
                    and _capitalised(hit.term.written[0])
                )
            )
            for hit in hits
        ]
        changed = True
        while changed:
            changed = False
            for i, hit in enumerate(hits):
                if counted[i]:
                    continue
                if (
                    i > 0 and counted[i - 1] and self._beside(text, hit, hits[i - 1])
                ) or (
                    i + 1 < len(hits)
                    and counted[i + 1]
                    and self._beside(text, hit, hits[i + 1])
                ):
                    counted[i] = changed = True
        return [hit for hit, keep in zip(hits, counted) if keep]

    def _employer_skills(self, company: str | None) -> set[int]:
        """The skills one of whose terms is in ``company``'s own name."""
        if not company:
            return set()
        words = [m.group().lower() for m in _tokens(company)]
        joined = f" {' '.join(words)} "
        return {
            number
            for number, terms in enumerate(self._terms_of)
            if any(f" {' '.join(term.folded)} " in joined for term in terms)
        }

    def mentioned(self, text: str | None, company: str | None = None) -> set[str]:
        """The names of the skills ``text`` mentions, less any the employer is named after."""
        if not text:
            return set()
        own = self._employer_skills(company)
        return {
            self.skills[hit.term.skill].name
            for hit in self._counted(text, self._hits(text))
            if hit.term.skill not in own
        }

    def kind_of(self, name: str) -> str:
        return next(skill.kind for skill in self.skills if skill.name == name)


@cache
def vocabulary() -> Vocabulary:
    """The vocabulary this install carries (see the module docstring for where it is found)."""
    return Vocabulary(json.loads(located().read_text(encoding="utf-8")))
