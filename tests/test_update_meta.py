"""Tests for the metadata refresh (ADR-0061).

The policy lives in :func:`update_meta.refresh_row`, which is pure — so what gets rewritten, what
is left alone, and *why* are all testable without a store on disk. The file-level test covers the
one invariant that would corrupt the store if broken: ``meta.jsonl`` stays row-aligned with
``embeddings.f32``, so the rewrite must preserve row order and count.
"""

from __future__ import annotations

import json

from headstart.ingest import update_meta as um


def _meta(**over):
    base = {
        "id": "greenhouse:acme:1",
        "ats": "greenhouse",
        "company": "Acme",
        "title": "Backend Engineer",
        "location": "Berlin",
        "remote": False,
        "employment_type": "Full-time",
        "experience": None,
        "salary": None,
        "department": "Engineering",
        "url": "https://example.com/1",
        "posted_at": "2026-01-01",
        "has_description": True,
        "min_years": 4,
        "max_years": None,
        "experience_source": "regex",
    }
    base.update(over)
    return base


# --- facts: re-observed every run ----------------------------------------------------------------


def test_facts_are_overwritten_from_the_corpus():
    meta = _meta(salary=None, location="Berlin")
    facts = {f: meta.get(f) for f in um.FACT_FIELDS}
    facts["salary"] = "€90k"
    facts["location"] = "Remote — EU"
    row, facts_changed, _ = um.refresh_row(meta, facts, {}, sweep=False)
    assert facts_changed
    assert row["salary"] == "€90k"
    assert row["location"] == "Remote — EU"


def test_untouched_row_reports_no_change():
    meta = _meta()
    facts = {f: meta.get(f) for f in um.FACT_FIELDS}
    row, facts_changed, derived_changed = um.refresh_row(meta, facts, {}, sweep=False)
    assert (facts_changed, derived_changed) == (False, False)
    assert row == meta


def test_vector_facts_are_never_refreshed():
    # has_description describes the embedded doc, not the store. Refreshing it would hide a
    # title-only vector from the upgrade path meant to repair it.
    meta = _meta(has_description=False)
    facts = {f: meta.get(f) for f in um.FACT_FIELDS}
    row, _, _ = um.refresh_row(
        meta, facts, {"greenhouse:acme:1": "5+ years"}, sweep=True
    )
    assert row["has_description"] is False
    assert "vector" not in row


# --- derivations: re-derived on a version bump ---------------------------------------------------


def test_sweep_redereives_from_held_description():
    # Deliberately independent of any particular extractor fix: the stored value simply disagrees
    # with what the current cascade makes of the held text, and the sweep must adopt the latter.
    meta = _meta(min_years=9, max_years=None, experience_source="regex")
    row, _, derived_changed = um.refresh_row(
        meta,
        None,
        {"greenhouse:acme:1": "we want 3 to 5 years of experience"},
        sweep=True,
    )
    assert derived_changed
    assert (row["min_years"], row["max_years"]) == (3, 5)


def test_sweep_can_rescue_a_row_that_had_no_number():
    # The `unspecified` bucket in the trends UI is exactly `min_years is None`.
    meta = _meta(min_years=None, max_years=None, experience_source=None)
    row, _, derived_changed = um.refresh_row(
        meta,
        None,
        {"greenhouse:acme:1": "7+ years of professional experience"},
        sweep=True,
    )
    assert derived_changed
    assert (row["min_years"], row["experience_source"]) == (7, "regex")


def test_sweep_leaves_rows_whose_description_is_not_held():
    # #162's 127,501 pre-ADR-0050 rows: re-deriving without the text they came from can only lose.
    meta = _meta(min_years=4, experience_source="regex")
    row, _, derived_changed = um.refresh_row(meta, None, {}, sweep=True)
    assert not derived_changed
    assert row["min_years"] == 4


def test_no_sweep_leaves_derivations_alone():
    meta = _meta(min_years=99, experience_source="regex")
    facts = {f: meta.get(f) for f in um.FACT_FIELDS}
    row, _, derived_changed = um.refresh_row(
        meta, facts, {"greenhouse:acme:1": "3 years of experience"}, sweep=False
    )
    assert not derived_changed
    assert row["min_years"] == 99  # only a version bump may touch it


def test_a_cosmetic_title_edit_never_wipes_a_description_derived_floor():
    """The regression this module could most easily cause. Descriptions are only loaded during a
    sweep, so an ordinary run that re-derives on a title edit would run the cascade with no text
    and null a floor that came from the description — growing the `experience_source: none` share
    the whole design exists to shrink."""
    meta = _meta(title="Backend Engineer", min_years=7, experience_source="regex")
    facts = {f: meta.get(f) for f in um.FACT_FIELDS}
    facts["title"] = "Backend Engineer (Remote)"  # a Board tidying its listing
    row, facts_changed, derived_changed = um.refresh_row(meta, facts, {}, sweep=False)
    assert facts_changed  # the new title is served
    assert not derived_changed
    assert (row["min_years"], row["experience_source"]) == (
        7,
        "regex",
    )  # floor survives


def test_a_title_edit_may_still_move_a_seniority_derived_floor():
    """The other half: a seniority floor came from the title, so a new title legitimately re-reads
    it — no held description is needed to do that correctly."""
    meta = _meta(title="Engineer", min_years=0, experience_source="seniority")
    facts = {f: meta.get(f) for f in um.FACT_FIELDS}
    facts["title"] = "Staff Engineer"
    row, _, derived_changed = um.refresh_row(meta, facts, {}, sweep=False)
    assert derived_changed
    assert (row["min_years"], row["experience_source"]) == (7, "seniority")


def test_changed_experience_field_rederives_without_a_sweep():
    # The raw field is a cascade input, so a Board editing it must move the numbers even at an
    # unchanged version — otherwise the served floor contradicts the served raw string.
    meta = _meta(experience=None, min_years=4, experience_source="regex")
    facts = {f: meta.get(f) for f in um.FACT_FIELDS}
    facts["experience"] = "2+"
    row, facts_changed, derived_changed = um.refresh_row(meta, facts, {}, sweep=False)
    assert facts_changed and derived_changed
    assert (row["min_years"], row["experience_source"]) == (2, "field")


# --- the watermark -------------------------------------------------------------------------------


def test_watermark_roundtrip_and_unreadable_reads_as_unswept(tmp_path):
    path = tmp_path / "derivations.json"
    assert um.read_watermark(path) == 0  # never stamped
    um.write_watermark(path, 3)
    assert um.read_watermark(path) == 3
    path.write_text("{ truncated", encoding="utf-8")
    # Must re-sweep rather than claim it already ran — the failure that leaves values wrong forever.
    assert um.read_watermark(path) == 0


# --- the file rewrite ----------------------------------------------------------------------------


def test_refresh_preserves_row_order_and_count(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    ids = [f"greenhouse:acme:{n}" for n in range(5)]
    with (store / "meta.jsonl").open("w", encoding="utf-8") as fh:
        for n, job_id in enumerate(ids):
            fh.write(json.dumps(_meta(id=job_id, title=f"Engineer {n}")) + "\n")

    jobs = tmp_path / "jobs"
    jobs.mkdir()
    with (jobs / "greenhouse.jsonl").open("w", encoding="utf-8") as fh:
        # Only the middle row is in this run's corpus, with an edited salary.
        fh.write(
            json.dumps({"id": ids[2], "salary": "€100k", "title": "Engineer 2"}) + "\n"
        )

    assert um.refresh(store, jobs, tmp_path / "none", tmp_path / "wm.json") == 0

    rows = [
        json.loads(line)
        for line in (store / "meta.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [r["id"] for r in rows] == ids  # order and count are the alignment invariant
    assert rows[2]["salary"] == "€100k"
    assert rows[0]["salary"] is None  # rows outside the corpus are untouched


def _store_and_corpus(tmp_path, descriptions: dict[str, str] | None = None):
    """A one-row store, an empty corpus, and optionally a populated description store."""
    import gzip

    store = tmp_path / "store"
    store.mkdir(exist_ok=True)
    (store / "meta.jsonl").write_text(json.dumps(_meta()) + "\n", encoding="utf-8")
    jobs = tmp_path / "jobs"
    jobs.mkdir(exist_ok=True)
    desc_dir = tmp_path / "descriptions"
    if descriptions is not None:
        ats_dir = desc_dir / "greenhouse"
        ats_dir.mkdir(parents=True, exist_ok=True)
        with gzip.open(ats_dir / "base.jsonl.gz", "wt", encoding="utf-8") as fh:
            for job_id, text in descriptions.items():
                fh.write(json.dumps({"id": job_id, "description": text}) + "\n")
    return store, jobs, desc_dir


def test_refresh_stamps_the_watermark_when_it_sweeps(tmp_path):
    store, jobs, desc = _store_and_corpus(
        tmp_path, {"greenhouse:acme:1": "3+ years of experience"}
    )
    watermark = tmp_path / "wm.json"

    um.refresh(store, jobs, desc, watermark)
    assert um.read_watermark(watermark) == um.DERIVATIONS_VERSION

    # Already at version: a second run must not re-sweep, and must leave the stamp put.
    um.refresh(store, jobs, desc, watermark)
    assert um.read_watermark(watermark) == um.DERIVATIONS_VERSION


def test_a_sweep_with_no_held_descriptions_does_not_stamp(tmp_path):
    """The merge job downloads the description store on `continue-on-error`. An empty store there
    means the artifact was lost — stamping would record a sweep that read nothing and leave every
    row unswept permanently."""
    store, jobs, desc = _store_and_corpus(tmp_path)  # no description store at all
    watermark = tmp_path / "wm.json"
    assert um.refresh(store, jobs, desc, watermark) == 0
    assert um.read_watermark(watermark) == 0  # still un-swept, so the next run retries


def test_refresh_no_ops_without_a_store(tmp_path):
    assert (
        um.refresh(tmp_path / "absent", tmp_path, tmp_path, tmp_path / "wm.json") == 0
    )
