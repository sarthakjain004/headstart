"""Tests for headstart.ingest.derived_meta — the one composition of the four field-extractors
(experience/salary/geo/remote) into a Job's derived meta keys (ADR-0146).

Before ADR-0146, ``doc_prep.to_meta`` (the cold-start embed path) and
``update_meta.refresh_row`` (the repair path) each wrote this composition out independently, with
no test crossing them. This file's regression is exactly that gap: ``to_meta(job)`` and
``refresh_row``'s cold-start sweep (no prior stored values, the held description substituted for
the row's own) must agree on all nine derived keys for the same job, across the tiers that
matter — a field-stated salary, a description-stated experience floor, a seniority-fallback
floor, and a job with nothing extractable.
"""

from __future__ import annotations

from headstart.ingest import doc_prep
from headstart.ingest import update_meta as um

DERIVED_KEYS = (
    "remote",
    "country",
    "min_years",
    "max_years",
    "experience_source",
    "min_salary_annual",
    "max_salary_annual",
    "salary_currency",
    "salary_source",
)


def _job(**overrides) -> dict:
    job = {
        "id": "greenhouse:acme:1",
        "ats": "greenhouse",
        "company": "Acme",
        "title": "Software Engineer",
        "location": "Berlin",
        "remote": None,
        "department": "Engineering",
        "employment_type": "Full-time",
        "experience": None,
        "salary": None,
        "url": "https://example.com/1",
        "posted_at": "2026-01-01",
        "description": "Build backend services.",
    }
    job.update(overrides)
    return job


def _assert_paths_agree(job: dict) -> None:
    """``to_meta(job)`` and ``refresh_row``'s cold-start sweep must derive the same nine keys."""
    expected = doc_prep.to_meta(job)

    # The cold-start case: a row with no prior derived values at all.
    meta = {"id": job["id"], "ats": job["ats"]}
    meta.update(dict.fromkeys(DERIVED_KEYS))
    facts = {f: job.get(f) for f in um.FACT_FIELDS}
    facts["remote"] = job.get(
        "remote"
    )  # corpus_facts() carries this too — see _FACT_WITH_OVERLAY

    row, _, _ = um.refresh_row(meta, facts, {job["id"]: job["description"]}, sweep=True)

    for key in DERIVED_KEYS:
        assert row[key] == expected[key], (
            f"{key}: refresh_row={row[key]!r} to_meta={expected[key]!r}"
        )


def test_field_stated_salary_only():
    _assert_paths_agree(_job(salary="100000-120000 USD", description=""))


def test_description_stated_experience_only():
    _assert_paths_agree(
        _job(description="You have 4+ years of experience with Python.")
    )


def test_seniority_fallback_floor():
    _assert_paths_agree(_job(title="Senior Software Engineer", description=""))


def test_nothing_extractable():
    _assert_paths_agree(_job(description="", salary=None, experience=None))
