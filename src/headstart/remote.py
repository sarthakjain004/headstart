"""Let the JD say "remote" even when the ATS's own field doesn't (enrichment).

Every scraper already derives ``Job.remote`` from an ATS-native field (workplaceType,
remoteType, ...) or, failing that, a substring check on the location string — see each
scraper's own ``_remote``/``_remote_from`` helper. None of them read the job description.

:func:`extract` adds one more, one-directional cascade tier on top of that: if the JD text
confidently says the role is remote, that wins, regardless of what the field said. It never
does the reverse — a JD that reads as onsite or hybrid never overrides an existing field value,
even a wrong one, because that direction has no positive-only escape hatch (a bad onsite call
would permanently suppress a job with nothing to recover it) and was scoped out of this pass.
This makes the cascade idempotent and safe to re-run every time the field itself refreshes: it
can only ever move ``False``/``None`` -> ``True``, never take a ``True`` away, so applying it
twice or applying it to an already-derived value is a no-op rather than compounding drift.

**Built by reading ~115 real job descriptions sampled across all 19 ATSes in the description
store**, not by guessing patterns from memory — a naive ``"remote" in text.lower()`` check is
dominated by false positives, because "remote" and "hybrid" are heavily overloaded as technical
jargon having nothing to do with work location:

* "remote" also means: remote procedure call, remote desktop, remote config (Firebase), remote
  attestation, remote API/data/network, remote hands (a datacenter *service*, not a work
  arrangement), remote sensing, a git remote.
* "hybrid" also means: hybrid retrieval (BM25 + embeddings), hybrid cloud/infrastructure/
  identity/architecture, hybrid powertrain (EV/automotive), hybrid search, hybrid encryption.
* "on-site" routinely names an office AMENITY ("on-site parking", "on-site gym", "on-site
  health centers") rather than a statement about where the role is performed.
* Negation sits right next to the word it looks like it's confirming: "Remote or hybrid work is
  not supported for this position", "Onsite (No remote positions available)".
* "work from anywhere" means two different things in real postings: a permanent policy
  ("this role is remote-first, work from anywhere") and a bounded annual perk ("Work From
  Anywhere Month", "up to 10 days per year") — the latter is common enough at companies that are
  NOT otherwise remote that it needs its own guard, not just a bare substring match.

Two controlled-vocabulary signals are checked first and trusted unconditionally, because they
are recruiting metadata rather than prose an LLM or a human wrote loosely: the LinkedIn ``#LI-``
tag (``#LI-Remote`` / ``#LI-Hybrid`` / ``#LI-Onsite``) and Workday's own "Position Role Type:"
field-in-text label, which uses the identical three-value vocabulary. The Position Role Type
pattern alone was worth adding: Workday supplies 27% of the whole description corpus and had the
lowest hit rate of any ATS before it existed.

Measured 2026-09-07 against the live served table (335,543 rows) joined to the full ADR-0050
description store (493,629 JDs, 98.1% coverage of served rows): of jobs where the field is
``False`` or ``None``, the JD confidently says remote for 7,439 of them — 818 where the field
had nothing at all (94% on Ashby, where recruiters routinely leave ``workplaceType`` unset even
on companies, like ClickHouse and Redis, that describe themselves as remote-first in the JD
text), and 6,621 where the field says ``False`` outright (concentrated on greenhouse and zoho).
Full methodology, the reading notes behind every pattern, and known remaining false-positive
modes: ``experiment/jd-remote-detection/LOG.md``.
"""

from __future__ import annotations

import re

# LinkedIn recruiting tags: controlled vocabulary, highest precision when present.
_LI_TAG = re.compile(r"#LI-(Remote|Hybrid|Onsite|OnSite)\b", re.IGNORECASE)

# Workday's own standardized field-in-text label ("Position Role Type: Onsite/Hybrid/Remote"),
# the same three-value controlled vocabulary as #LI- but native to Workday's posting template.
_ROLE_TYPE_TAG = re.compile(
    r"Position\s+(?:Role\s+)?Type\s*:?\s*(Remote|Hybrid|Onsite|OnSite)\b", re.IGNORECASE
)

# Technical/non-work-location jargon that reuses "remote"/"hybrid"/"on-site" — checked FIRST,
# and a jargon hit suppresses that occurrence from consideration entirely.
_JARGON = re.compile(
    r"remote\s+(procedure\s+call|desktop|config|hands|attestation|api|data\s+via|network\s+api|"
    r"server|host|repositor|branch|sensing|database)"
    r"|hybrid\s+(retrieval|cloud|infrastructure|identity|powertrain|architecture|approach|"
    r"deployment|environment|vehicle|car|mesh|search|encryption|engine)"
    r"|on[\s-]?site\s+(parking|gym|fitness|health|cafeteria|daycare|amenit)",
    re.IGNORECASE,
)

# Explicit negation near a remote/hybrid mention — checked before accepting any positive match.
_NEGATION = re.compile(
    r"(remote|hybrid)\s+(?:work\s+)?(?:is\s+)?not\s+(?:supported|available|offered|eligible)"
    r"|(?:no|not)\s+(?:longer\s+)?remote\b"
    r"|does\s+not\s+offer\s+(?:telecommuting\s+or\s+)?remote"
    r"|we\s+do\s+not\s+offer\s+telecommuting\s+or\s+remote"
    r"|no\s+remote\s+positions?\s+available"
    r"|not\s+a\s+remote\s+(?:role|position|job)",
    re.IGNORECASE,
)

_ONSITE_EXPLICIT = re.compile(
    r"100%\s*on[\s-]?site"
    r"|fully\s+on[\s-]?site"
    r"|on[\s-]?site\s+only"
    r"|in[\s-]?office\s+only"
    r"|in[\s-]?office\s+company"
    r"|expected\s+to\s+be\s+100%\s+onsite"
    r"|this\s+is\s+an?\s+(?:full-time,?\s+)?on[\s-]?site\s+position"
    r"|designed\s+as\s+['\"]?on[\s-]?site['\"]?",
    re.IGNORECASE,
)

_REMOTE_EXPLICIT = re.compile(
    r"this\s+(?:role|position|job)\s+is\s+(?:fully\s+|100%\s+)?remote"
    r"|this\s+is\s+an?\s+(?:fully\s+|100%\s+)?remote\s+(?:role|position|job)"
    r"|(?:fully|100%|full)\s+remote(?:ly)?\b"
    r"|remote[\s-]first\b"
    r"|remote[\s-]friendly\b"
    # NOT "Work From Anywhere Month" / "...up to 10 days" — a bounded annual perk, not a
    # permanent policy. Found by reading a real hit: Homebase's title said "(Hybrid)" while
    # its JD's "Work From Anywhere Month" alone would have flipped this classifier to remote.
    r"|work\s+from\s+anywhere\b(?!\s*(?:month|week|day|trip|scheme|program|,?\s*up\s+to))"
    r"|we\s+work\s+remotely"
    r"|remote\s+work(?:ing)?\s+(?:arrangement|culture|environment|opportunity)"
    r"|location\s*[:\-]\s*remote\b"
    r"|the\s+role\s+is\s+full\s+remote"
    r"|designed\s+as\s+['\"]?remote['\"]?",
    re.IGNORECASE,
)

_HYBRID_EXPLICIT = re.compile(
    r"hybrid\s+(?:work\s+)?(?:model|schedule|role|setup|policy|working)"
    r"|hybrid[\s-]remote\b"
    r"|\d\s*(?:-\d)?\s*days?\s*(?:/|per\s+|a\s+)?week\s+in\s+(?:the\s+)?office"
    r"|days?\s+in\s+office\s+(?:in\s+)?a\s+week",
    re.IGNORECASE,
)


def _strip_jargon(text: str) -> str:
    return _JARGON.sub(" ", text)


def _jd_signal(text: str | None) -> str | None:
    """One of ``"remote"``, ``"hybrid"``, ``"onsite"``, or ``None`` (no confident signal)."""
    if not text or not text.strip():
        return None

    tag = _LI_TAG.search(text) or _ROLE_TYPE_TAG.search(text)
    if tag:
        value = tag.group(1).lower()
        return "onsite" if value == "onsite" else value

    stripped = _strip_jargon(text)
    if _NEGATION.search(stripped):
        return "onsite"
    if _ONSITE_EXPLICIT.search(stripped):
        return "onsite"
    if _HYBRID_EXPLICIT.search(stripped):
        return "hybrid"
    if _REMOTE_EXPLICIT.search(stripped):
        return "remote"
    return None


def extract(field: bool | None, description: str | None) -> bool | None:
    """The field, unless the JD confidently says remote — then ``True``, regardless of ``field``.

    One-directional only (see the module docstring): a JD read as ``"hybrid"`` or ``"onsite"``
    never changes ``field``, even when ``field`` is ``None``. ``field`` is returned unchanged
    whenever the description carries no confident remote signal, including when it is empty or
    ``None`` — so this is always safe to call, and always idempotent to re-call.
    """
    return True if _jd_signal(description) == "remote" else field
