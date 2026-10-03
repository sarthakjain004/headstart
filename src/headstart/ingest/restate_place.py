"""Give each served interval its role family and band under today's classifier and derivations:
the placement step of a Restatement (ADR-0330).

The family is decided as ``role_trends`` decides it (ADR-0220, ADR-0224): the head's cached title
logits plus its row part over the Job's description vector, the served one for a Job still served
and the archived one (``data/facts/job_vectors/``) for one the store dropped. A Job with neither
is decided from its title alone, the row part reduced to the head's bias: a posting a widened
tech filter admits into the past was never embedded (ADR-0330, the owner's decision of
2026-09-29). A title no run has encoded under the head is ``unclassified-tech``, as it is there.

The band is ``role_taxonomy.band`` over the ``min_years`` that ``derived_meta.derive`` reads out of
the version's raw fields and its description, the one composition ``doc_prep`` and ``update_meta``
also use, so a derivations change reaches the restated past as it reaches the served table.

Runs inside a Restatement; it is not a pipeline stage.
"""

from __future__ import annotations

from collections.abc import Mapping

from headstart.boards.board_identity import ats_of
from headstart.ingest import derived_meta, role_family_classifier
from headstart.trends import role_taxonomy


def placements(
    served,
    head: role_family_classifier.Head,
    cache: role_family_classifier.Cache,
    vectors: Mapping[str, object],
    descriptions: Mapping[str, str],
    *,
    version_sources: Mapping | None = None,
    batch_size: int = 4096,
):
    """``served`` with a ``family`` column (None where the head places the Job outside tech) and a
    ``band`` column. ``vectors`` maps an id to its description vector, ``descriptions`` to its
    text."""
    import pyarrow as pa

    batches = [
        _placement_batch(
            pa.Table.from_batches([batch]),
            head,
            cache,
            vectors,
            descriptions,
            version_sources,
        )
        for batch in served.to_batches(max_chunksize=batch_size)
    ]
    if not batches:
        return (
            served.append_column("family", pa.nulls(0, pa.string()))
            .append_column("band", pa.nulls(0, pa.string()))
            .append_column("restated_min_years", pa.nulls(0, pa.int32()))
            .append_column("input_quality", pa.nulls(0, pa.list_(pa.string())))
        )
    return pa.concat_tables(batches)


def _placement_batch(served, head, cache, vectors, descriptions, version_sources):
    import numpy as np
    import pyarrow as pa

    rows = served.to_pylist()
    version_sources = version_sources or {}
    keys = [(row["id"], row.get("input_from", row.get("valid_from"))) for row in rows]
    width = head.row_vector_dim
    missing = np.zeros(width, dtype=np.float32)
    matrix = (
        np.stack(
            [
                np.asarray(
                    version_sources.get(
                        (row["id"], row.get("input_from", row.get("valid_from"))),
                        (vectors.get(row["id"], missing), None),
                    )[0],
                    dtype=np.float32,
                )
                for row in rows
            ]
        )
        if rows
        else np.zeros((0, width), dtype=np.float32)
    )
    row_parts = head.row_logits(matrix)
    effective_cache = role_family_classifier.Cache(
        cache.version, dict(cache.title_logits), cache.inputs_fingerprint
    )
    preserved = []
    fingerprint = head.inputs_fingerprint
    for index, (row, key) in enumerate(zip(rows, keys, strict=True)):
        title_part, row_part = (
            version_sources.logits(key, fingerprint, row["title"])
            if hasattr(version_sources, "logits")
            else (None, None)
        )
        if row_part is not None:
            row_parts[index] = row_part
        if title_part is not None:
            effective_cache.title_logits[
                role_family_classifier.normalise(row["title"])
            ] = title_part
        preserved.append(row_part is not None)
    scored = role_family_classifier.decide_rows_scored(
        effective_cache, head, [row["title"] for row in rows], row_parts
    )
    families = [None if f == role_taxonomy.NON_TECH else f for f, _ in scored]
    bands = []
    years, qualities = [], []
    for index, (row, key) in enumerate(zip(rows, keys, strict=True)):
        text = version_sources.get(key, (None, descriptions.get(row["id"])))[1]
        observed = (
            version_sources.observed(key)
            if hasattr(version_sources, "observed")
            else {}
        )
        derived = derived_meta.derive(
            row
            | {
                "ats": ats_of(row["id"]),
                "description": text,
            }
        )
        quality = []
        if text is None:
            quality.append("historical_description_unavailable")
        if (
            text is None
            and "min_years" in observed
            and derived.get("experience_source") != "field"
            and observed.get("experience_source") != "seniority"
        ):
            derived["min_years"] = observed["min_years"]
            quality.append("observed_experience_retained")
        if row.get("baseline_language_retained"):
            quality.append("observed_language_eligibility_retained")
        if not preserved[index] and observed.get("vector_identity"):
            quality.append("rounded_or_missing_historical_vector")
        years.append(derived.get("min_years"))
        qualities.append(quality)
        bands.append(
            role_taxonomy.band(
                derived.get("min_years"), row["title"], row["employment_type"]
            )
        )
    return (
        served.append_column("family", pa.array(families, pa.string()))
        .append_column("band", pa.array(bands, pa.string()))
        .append_column("restated_min_years", pa.array(years, pa.int32()))
        .append_column("input_quality", pa.array(qualities, pa.list_(pa.string())))
    )


def place_of(row: Mapping) -> tuple[str, str] | None:
    """The ``place`` a Restatement counts with, once :func:`placements` has run."""
    return None if row["family"] is None else (row["family"], row["band"])
