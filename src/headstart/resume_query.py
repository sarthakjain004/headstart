"""Turn pasted Résumé text into one search Query (ADR-0032).

``query_for(resume, ask)`` is the whole interface. Behind it: input validation, the prompt,
reply cleanup (models wrap answers in quotes, fences, and preamble), and a deterministic
scrub of the one way this feature could quietly break the product's search design.

That design (`CLAUDE.md`, CONTEXT.md **Query**) splits search explicitly: structured
constraints live in **Search filters**, and the Query names *only a role*. A Résumé is
saturated with exactly what a Query must not carry — years of experience, salary — and a
drifting prompt would smuggle them into the embedding where no filter shows them. So the
prompt forbids them *and* the scrub enforces it in code. (Locations are prompt-forbidden
only: detecting them deterministically needs a gazetteer, and a stray one is visible to the
user in the editable Query box.)

The ``ask`` seam is what makes this testable without a tunnel: production passes
``llm_router.ask``, tests pass a stub. The Résumé text goes into the prompt and nowhere
else — never stored, never logged (ADR-0032).
"""

from __future__ import annotations

import re
from collections.abc import Callable

MAX_RESUME_CHARS = (
    20_000  # a real résumé is ~3-6 KB of text; anything bigger is not one
)
_MAX_QUERY_CHARS = 200  # the search box is one line; a paragraph is the model rambling

_PROMPT = """You are helping a job seeker search a semantic index of software-engineering jobs.

Read the resume below and write ONE line describing the role they should search for: the kind of
engineer, their domains, and their main technologies. Write it like a search, not a sentence about
the person — e.g. "backend engineer, distributed systems, Python/Go, fintech".

Rules:
- Output the single line only — no quotes, no markdown, no explanation.
- Name the role and stack ONLY. Do NOT mention years of experience, salary, compensation,
  location, or company names: the search has separate filters for those.

Resume:
{resume}"""

# Years-of-experience and salary phrasings, matched case-insensitively against the LLM's reply.
# Deliberately narrow: this runs on a one-line role description the user sees and edits, so a
# false negative costs one visible keystroke. Known leaks, accepted: bare "150k" (the money
# branch requires a currency sign — which is also why "401k" can never false-positive),
# hyphenated "8-year", and acronyms like "8 YOE".
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


class EmptyQuery(ResumeError):
    """The model's reply had nothing left after cleanup — nothing to fill the box with."""


def _clean(reply: str) -> str:
    """The first real line of the model's reply, unwrapped and de-decorated.

    Fence markers are dropped *before* backtick-stripping — the other order turns a
    ```` ```python ```` marker into the "query" ``python``."""
    for line in reply.splitlines():
        line = line.strip()
        if not line or line.startswith(
            "```"
        ):  # fence markers, with or without a language tag
            continue
        line = line.strip("`\"'“”‘’ ").rstrip(".")
        if line:
            return line
    return ""


def query_for(resume: str, ask: Callable[[str], str]) -> str:
    """One Résumé in, one role-only Query out.

    Raises :class:`EmptyResume` / :class:`ResumeTooLong` before spending an LLM call,
    :class:`EmptyQuery` when the reply cleans down to nothing, and passes through whatever
    ``ask`` raises (the router client raises ``RouterUnavailable``).
    """
    text = resume.strip()
    if not text:
        raise EmptyResume("paste the text of your résumé first")
    if len(text) > MAX_RESUME_CHARS:
        raise ResumeTooLong(
            f"that is {len(text):,} characters — a résumé is a few thousand; "
            f"paste the résumé itself, not a document dump"
        )

    query = _clean(ask(_PROMPT.format(resume=text)))
    # Enforce the Query contract in code, not hope: strip any years/salary phrasing the
    # prompt failed to suppress, then tidy the punctuation the removal leaves behind.
    query = _FORBIDDEN.sub("", query)
    query = re.sub(r"\s*,\s*(?:,\s*)+", ", ", query)
    query = re.sub(r"\s{2,}", " ", query).strip(" ,;-")
    if not query:
        raise EmptyQuery(
            "couldn't derive a role from that text — type a search instead"
        )
    return query[:_MAX_QUERY_CHARS].rstrip(" ,;-")
