"""Turn pasted Résumé text into the stored Profile — one sentence plus facts (ADR-0041).

``extract(resume, ask)`` is the whole interface: one LLM call returns the role sentence
(the Résumé query) and the structured facts — current title, years, skills, past roles,
education, location. Behind it: input validation, the prompt, JSON reply cleanup, and a
deterministic scrub of the one way this feature could quietly break the product's search
design. Grew out of ``resume_query.query_for`` (ADR-0032), which produced the sentence
alone; the password gate that guarded it is retired for the per-Account cap the routes
enforce.

That design (`CLAUDE.md`, CONTEXT.md **Query**) splits search explicitly: structured
constraints live in **Search filters**, and the Query names *only a role*. A Résumé is
saturated with exactly what a Query must not carry — years of experience, salary — and a
drifting prompt would smuggle them into the embedding where no filter shows them. So the
prompt forbids them in the sentence *and* the scrub enforces it in code; the facts are
where those constraints belong, each in its own field.

Contact details are never part of the extraction: the prompt excludes them, and the fixed
key set means an unasked-for field has nowhere to land. The ``ask`` seam keeps this
testable without a tunnel: production passes ``llm_router.ask``, tests pass a stub. The
Résumé text goes into the prompt and nowhere else — never stored, never logged (ADR-0032,
ADR-0041).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

MAX_RESUME_CHARS = (
    20_000  # a real résumé is ~3-6 KB of text; anything bigger is not one
)
_MAX_QUERY_CHARS = 200  # the search box is one line; a paragraph is the model rambling
_MAX_FACT_CHARS = 200  # each fact renders as one form field

_PROMPT = """You are helping a job seeker set up their profile on a semantic search index of \
software-engineering jobs.

Read the resume below and answer with ONE JSON object, exactly these keys:

{{"query": "...", "title": "...", "years": 0, "skills": ["..."], "roles": ["..."], \
"education": "...", "location": "..."}}

- "query": one line describing the role to search for — the kind of engineer, their domains,
  their main technologies, e.g. "backend engineer, distributed systems, Python/Go, fintech".
  Role and stack ONLY: no years of experience, no salary, no location, no company names.
- "title": their current or most recent job title.
- "years": total years of professional experience, as an integer (null if unclear).
- "skills": up to 10 main technologies/skills.
- "roles": up to 5 past roles as "title at company" strings, most recent first.
- "education": highest degree and field, one short line ("" if none stated).
- "location": their current city and country as stated ("" if none stated).
- Never include contact details — no names, emails, phone numbers, links, or addresses.
- Output the JSON object only — no markdown fences, no explanation.

Resume:
{resume}"""

# Years-of-experience and salary phrasings, matched case-insensitively against the model's
# "query" sentence. Deliberately narrow: this runs on a one-line role description the user
# sees and edits, so a false negative costs one visible keystroke. Known leaks, accepted:
# bare "150k" (the money branch requires a currency sign — which is also why "401k" can
# never false-positive), hyphenated "8-year", and acronyms like "8 YOE".
_FORBIDDEN = re.compile(
    r"""
    \d+\s*\+?\s*(?:years?|yrs?)(?:\s+of\s+experience)?   # 7 years / 10+ yrs of experience
    | (?:₹|\$|€|£)\s*\d[\d,.]*\s*(?:k|m|lpa|lakhs?|cr)?  # $150k / ₹30 LPA
    | \b\d+\s*(?:lpa|lakhs?)\b                           # 30 LPA without a currency sign
    | \bsalary\b | \bcompensation\b | \bctc\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


class ResumeError(ValueError):
    """Base for everything this module refuses; subclasses say what the caller should answer."""


class EmptyResume(ResumeError):
    """No usable text was pasted."""


class ResumeTooLong(ResumeError):
    """Pasted text exceeds MAX_RESUME_CHARS — not a résumé."""


class EmptyExtraction(ResumeError):
    """The model's reply held no usable Profile — nothing to store."""


def _reply_json(reply: str) -> dict[str, Any]:
    """The one JSON object in the model's reply, fences and preamble tolerated."""
    start, end = reply.find("{"), reply.rfind("}")
    if start == -1 or end <= start:
        raise EmptyExtraction("couldn't read a profile from that text — try again")
    try:
        data = json.loads(reply[start : end + 1])
    except ValueError as exc:
        raise EmptyExtraction(
            "couldn't read a profile from that text — try again"
        ) from exc
    if not isinstance(data, dict):
        raise EmptyExtraction("couldn't read a profile from that text — try again")
    return data


def _scrub_query(raw: str) -> str:
    """The Query contract enforced in code, not hope: strip any years/salary phrasing the
    prompt failed to suppress, then tidy the punctuation the removal leaves behind."""
    query = _FORBIDDEN.sub("", raw.strip().strip("`\"'“”‘’ ").rstrip("."))
    query = re.sub(r"\s*,\s*(?:,\s*)+", ", ", query)
    query = re.sub(r"\s{2,}", " ", query).strip(" ,;-")
    return query[:_MAX_QUERY_CHARS].rstrip(" ,;-")


def _fact(value: Any) -> str:
    """A fact as one bounded line; lists (skills, roles) join into the line."""
    if isinstance(value, list):
        value = ", ".join(str(v).strip() for v in value if str(v).strip())
    return str(value or "").strip()[:_MAX_FACT_CHARS]


def _years(value: Any) -> int | None:
    try:
        years = int(value)
    except (TypeError, ValueError):
        return None
    return years if 0 <= years <= 60 else None


def extract(resume: str, ask: Callable[[str], str]) -> dict[str, Any]:
    """One Résumé in, one Profile extraction out: ``query`` plus the fact fields.

    Raises :class:`EmptyResume` / :class:`ResumeTooLong` before spending an LLM call,
    :class:`EmptyExtraction` when the reply yields no usable sentence, and passes through
    whatever ``ask`` raises (the router client raises ``RouterUnavailable``)."""
    text = resume.strip()
    if not text:
        raise EmptyResume("paste the text of your résumé first")
    if len(text) > MAX_RESUME_CHARS:
        raise ResumeTooLong(
            f"that is {len(text):,} characters — a résumé is a few thousand; "
            f"paste the résumé itself, not a document dump"
        )

    data = _reply_json(ask(_PROMPT.format(resume=text)))
    query = _scrub_query(_fact(data.get("query")))
    if not query:
        raise EmptyExtraction(
            "couldn't derive a role from that text — fill the profile in by hand instead"
        )
    return {
        "query": query,
        "title": _fact(data.get("title")),
        "years": _years(data.get("years")),
        "skills": _fact(data.get("skills")),
        "roles": _fact(data.get("roles")),
        "education": _fact(data.get("education")),
        "location": _fact(data.get("location")),
    }
