"""Load the Wellfound embedding store into a local LanceDB table (ADR-0008).

Reads ``data/embeddings/wellfound/`` (``embeddings.f32`` + ``meta.jsonl`` + ``manifest.json``), joins
the extracted years-of-experience numbers from ``data/enrich/wellfound_experience.jsonl`` (ADR-0009)
by ``id``, and writes a LanceDB table at ``data/lancedb/`` with one row per Job: the 768-d vector,
the canonical typed metadata (ADR-0007), and the numeric ``min_years`` / ``max_years`` /
``experience_source``. That lets a query filter on structured fields (including ``min_years``) and
rank by vector similarity in one call — filter-then-rank. Local + embedded for now.
"""

from __future__ import annotations

import json
from pathlib import Path

import lancedb
import numpy as np
import pyarrow as pa

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "data" / "embeddings" / "wellfound"
_EXPERIENCE = _ROOT / "data" / "enrich" / "wellfound_experience.jsonl"
_DB = _ROOT / "data" / "lancedb"
_TABLE = "wellfound"


def _load_experience() -> dict[str, dict]:
    """id -> {min_years, max_years, source}; empty if the enrichment hasn't been run yet."""
    if not _EXPERIENCE.exists():
        return {}
    return {
        r["id"]: r
        for r in (json.loads(line) for line in _EXPERIENCE.open(encoding="utf-8"))
    }


def main() -> None:
    manifest = json.loads((_SRC / "manifest.json").read_text())
    dim = manifest["dim"]
    vectors = np.fromfile(_SRC / "embeddings.f32", dtype="float32").reshape(-1, dim)
    metas = [json.loads(line) for line in (_SRC / "meta.jsonl").open(encoding="utf-8")]
    assert len(vectors) == len(metas) == manifest["count"], (
        "store is inconsistent — rebuild it first"
    )
    experience = _load_experience()

    # Explicit schema: canonical typed metadata (ADR-0007) + parsed experience numbers (ADR-0009) +
    # the vector. min_years/max_years are nullable ints — null for the Jobs no number was found for.
    schema = pa.schema(
        [
            pa.field("id", pa.string()),
            pa.field("ats", pa.string()),
            pa.field("company", pa.string()),
            pa.field("title", pa.string()),
            pa.field("location", pa.string()),
            pa.field("remote", pa.bool_()),
            pa.field("employment_type", pa.string()),
            pa.field("experience", pa.string()),  # raw string for display ("5+")
            pa.field("min_years", pa.int32()),  # parsed, filterable
            pa.field("max_years", pa.int32()),
            pa.field("experience_source", pa.string()),  # "field" | "regex" | null
            pa.field("salary", pa.string()),
            pa.field("department", pa.string()),
            pa.field("url", pa.string()),
            pa.field("posted_at", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), dim)),
        ]
    )

    columns: dict[str, list] = {f.name: [] for f in schema if f.name != "vector"}
    for meta in metas:
        exp = experience.get(meta["id"], {})
        for name in columns:
            if name == "min_years":
                columns[name].append(exp.get("min_years"))
            elif name == "max_years":
                columns[name].append(exp.get("max_years"))
            elif name == "experience_source":
                columns[name].append(exp.get("source"))
            else:
                columns[name].append(meta.get(name))

    arrays = [
        pa.array(columns[f.name], type=f.type) for f in schema if f.name != "vector"
    ]
    vec_col = pa.FixedSizeListArray.from_arrays(
        pa.array(vectors.reshape(-1), pa.float32()), dim
    )
    table_data = pa.Table.from_arrays(arrays + [vec_col], schema=schema)

    db = lancedb.connect(_DB)
    table = db.create_table(_TABLE, data=table_data, mode="overwrite")
    have_years = sum(1 for m in columns["min_years"] if m is not None)
    print(
        f"wrote {table.count_rows()} rows to LanceDB table '{_TABLE}' "
        f"({have_years} with a min_years number) at {_DB}"
    )


if __name__ == "__main__":
    main()
