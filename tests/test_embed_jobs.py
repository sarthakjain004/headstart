"""Tests for embed_jobs.py's pure row-shaping: doc building and the inline experience metadata.

The inline ``min_years``/``max_years``/``experience_source`` (ADR-0019 — no separate enrich join)
is the new logic worth locking down: each extraction tier must land in the metadata that rides
next to the vector. embed_jobs.py is a script under scripts/embed, so we load it by path; its
module import pulls the ML stack, which the quality CI job doesn't install — importorskip.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("torch")
pytest.importorskip("sentence_transformers")
pytest.importorskip("langdetect")

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "embed_jobs", _ROOT / "scripts" / "embed" / "embed_jobs.py"
)
ej = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ej)


def _job(**overrides) -> dict:
    job = {
        "id": "lever:acme:123",
        "ats": "lever",
        "company": "Acme",
        "title": "Software Engineer",
        "location": "Bengaluru",
        "remote": True,
        "department": "Engineering",
        "employment_type": "Full Time",
        "experience": None,
        "salary": "INR 20 - 30",
        "url": "https://jobs.lever.co/acme/123",
        "posted_at": "2026-06-01",
        "scraped_at": "2026-07-01",
        "description": "Build backend services.",
    }
    job.update(overrides)
    return job


def test_meta_carries_canonical_fields_not_description():
    meta = ej.to_meta(_job())
    assert meta["id"] == "lever:acme:123"
    assert meta["remote"] is True
    assert meta["employment_type"] == "Full Time"  # raw, display-only (ADR-0019)
    assert meta["salary"] == "INR 20 - 30"  # raw, display-only (ADR-0019)
    assert "description" not in meta  # never embedded as metadata (ADR-0006)
    assert "scraped_at" not in meta


def test_meta_experience_from_field():
    meta = ej.to_meta(_job(experience="3-5"))
    assert (meta["min_years"], meta["max_years"]) == (3, 5)
    assert meta["experience_source"] == "field"
    assert meta["experience"] == "3-5"  # the raw string still rides along for display


def test_meta_experience_from_description():
    meta = ej.to_meta(_job(description="You have 4+ years of experience with Python."))
    assert (meta["min_years"], meta["max_years"]) == (4, None)
    assert meta["experience_source"] == "regex"


def test_meta_experience_seniority_floor_flows_into_filterable_min_years():
    # ADR-0019: the filter trusts seniority-estimated floors — they must land in min_years.
    meta = ej.to_meta(_job(title="Senior Software Engineer"))
    assert meta["min_years"] is not None
    assert meta["experience_source"] == "seniority"


def test_meta_experience_unknown_is_null():
    meta = ej.to_meta(_job())
    assert meta["min_years"] is None
    assert meta["max_years"] is None
    assert meta["experience_source"] is None


def test_build_doc_prefix_and_markdown_stripping():
    job = _job(
        title="Backend Engineer",
        description="**Own** the [platform](https://x.co) # roadmap",
    )
    doc = ej.build_doc(job)
    assert doc.startswith(ej.DOC_PREFIX + "Backend Engineer")
    assert "platform" in doc and "https://x.co" not in doc
    assert "**" not in doc and "#" not in doc


def test_english_gate():
    assert ej.is_english(
        "Software Engineer", "Build and ship backend services in Python."
    )
    assert not ej.is_english(
        "Softwareentwickler",
        "Wir suchen einen erfahrenen Entwickler für unser Team in Berlin.",
    )


def test_bucket_for_boundaries():
    assert ej.bucket_for(1) == ej._BUCKETS[0]
    assert ej.bucket_for(512) == 512
    assert ej.bucket_for(513) == 1024
    assert ej.bucket_for(4096) == 4096
    assert ej.bucket_for(40_000) == 4096  # over-cap docs truncate into the top bucket


def test_batch_sizes_hold_attention_budget():
    # invariant: every bucket's fixed batch keeps n × bucket² within the attention budget
    for bucket in ej._BUCKETS:
        n = ej.batch_size_for(bucket)
        assert 1 <= n <= ej._BATCH_CAP
        assert n == 1 or n * bucket * bucket <= ej._ATTN_BUDGET


def test_batch_size_shrinks_with_bucket():
    sizes = [ej.batch_size_for(b) for b in ej._BUCKETS]
    assert sizes == sorted(sizes, reverse=True)
    assert sizes[-1] < sizes[0]  # long-doc buckets really do get smaller batches
