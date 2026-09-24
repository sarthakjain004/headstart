"""The Job derivation cascade (ADR-0061, ADR-0146): the four field-extractors —
``headstart.experience``, ``headstart.salary``, ``headstart.geo``, ``headstart.remote`` — composed
into the *derived* subset of a Job's served meta columns: ``remote``, ``country``,
``min_years``/``max_years``/``experience_source``, and
``min_salary_annual``/``max_salary_annual``/``salary_currency``/``salary_source``.

Two callers must agree on this composition byte-for-byte: ``doc_prep.to_meta`` (cold-start, at
embed time — the Job's current facts, including a ``description`` that may genuinely be ``None``)
and ``update_meta.refresh_row`` (repair, at sweep/rederive time — a held description substituted
for the row's own, the rest read off the stored row). Before ADR-0146 each wrote this composition
out independently, so a change to what an extractor's result maps to could silently update one
without the other. This module is the one place it is written.

**What stays out of here, deliberately:** *whether* to (re-)derive at all — ``DERIVATIONS_VERSION``,
the sweep/rederive/inputs-moved guards, the ``_KEEP`` sentinel, ``_FACT_WITH_OVERLAY`` — is
``update_meta``'s policy, not this module's. ``update_meta`` also keeps its own no-held-description
fallback (:func:`update_meta._rederive_without_text` / ``_rederive_salary_without_text``), which
this module cannot run for it: passing ``description=None`` to :func:`experience_meta` or
:func:`salary_meta` would read as "nothing stated" and null a floor the row was originally derived
from a description it no longer has re-readable.
"""

from __future__ import annotations

from headstart import india_filter
from headstart.experience import ExperienceSpan
from headstart.experience import extract as extract_experience
from headstart.remote import extract as extract_remote
from headstart.salary import SalarySpan
from headstart.salary import extract as extract_salary


def experience_fields(span: ExperienceSpan | None) -> dict:
    """An already-computed :class:`ExperienceSpan` (or ``None``) as its three served meta keys.

    Split out from :func:`experience_meta` so ``update_meta``'s no-held-text fallback — which
    computes a span without running the full cascade — can still share this assembly.
    """
    return {
        "min_years": span.min_years if span else None,
        "max_years": span.max_years if span else None,
        "experience_source": span.source if span else None,
    }


def experience_meta(
    experience: str | None, description: str | None, title: str | None
) -> dict:
    """The experience cascade (``headstart.experience``) as its three served meta keys."""
    return experience_fields(extract_experience(experience, description, title))


def salary_fields(span: SalarySpan | None) -> dict:
    """An already-computed :class:`SalarySpan` (or ``None``) as its four served meta keys — see
    :func:`experience_fields`."""
    return {
        "min_salary_annual": span.min_annual if span else None,
        "max_salary_annual": span.max_annual if span else None,
        "salary_currency": span.currency if span else None,
        "salary_source": span.source if span else None,
    }


def salary_meta(salary: str | None, description: str | None, ats: str | None) -> dict:
    """The salary cascade (``headstart.salary``) as its four served meta keys."""
    return salary_fields(extract_salary(salary, description, ats))


def country_meta(location: str | None) -> dict:
    """``headstart.geo.classify`` as its one served meta key (ADR-0138), named and valued by
    :mod:`headstart.india_filter`, the Search filter that reads it (ADR-0193)."""
    return {india_filter.COLUMN: india_filter.country(location)}


def remote_meta(remote: bool | None, description: str | None) -> dict:
    """``headstart.remote.extract`` as its one served meta key (ADR-0061 v8 / ADR-0118)."""
    return {"remote": extract_remote(remote, description)}


def derive(job: dict) -> dict:
    """All four families at once, reading a job-shaped dict.

    Reads ``remote``, ``location``, ``experience``, ``title``, ``salary``, ``ats`` and
    ``description`` — the same keys a scraped Job carries — and returns the nine derived meta
    keys above. This is what ``doc_prep.to_meta`` calls directly; ``update_meta.refresh_row``
    calls the per-family functions above instead, because its four families are re-derived under
    independent trigger conditions (see this module's docstring).
    """
    meta: dict = {}
    meta.update(remote_meta(job.get("remote"), job.get("description")))
    meta.update(country_meta(job.get("location")))
    meta.update(
        experience_meta(job.get("experience"), job.get("description"), job.get("title"))
    )
    meta.update(salary_meta(job.get("salary"), job.get("description"), job.get("ats")))
    return meta
