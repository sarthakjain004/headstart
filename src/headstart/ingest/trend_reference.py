"""Preserve the inputs and independent placements of each served Trends tick.

The first checkpoint is a baseline of every served Job, including Jobs a scrape cannot
reach. Later checkpoints contain only changed or removed ids. Source text and vectors
travel with changed rows, so edits are not silently applied to earlier measurements.
The small current-state file and the tick files must be published together.
"""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from headstart.embedding_conventions import MODEL, MODEL_REVISION
from headstart.ingest.job_facts import RAW_FIELDS, file_name

STATE = "reference_state.parquet"
DIRECTORY = "trend_reference"
_STATE_COLUMNS = ("id", "digest", "board", "family", "band")


def freeze_rules(root: Path, facts: Path) -> str:
    """Keep the exact rule code, model head and Board ledgers by content fingerprint."""
    paths = sorted(
        p
        for directory in ("src/headstart", "config", "data/validate")
        for p in (root / directory).rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    )
    paths += [root / "pyproject.toml"]
    digest = hashlib.sha256()
    for path in paths:
        name = path.relative_to(root).as_posix().encode()
        digest.update(len(name).to_bytes(8, "big") + name)
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    fingerprint = digest.hexdigest()
    path = facts / "reference_rules" / f"{fingerprint}.zip"
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(".zip.tmp")
    try:
        with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for source in paths:
                archive.write(source, source.relative_to(root).as_posix())
        staged.replace(path)
    finally:
        staged.unlink(missing_ok=True)
    return fingerprint


def capture(
    table,
    placements,
    facts: Path,
    stamp: str,
    methodology: dict,
    *,
    state_dir: Path | None = None,
) -> Path:
    """Capture a complete tick or raise before replacing its prior state.

    ``placements`` is the independently calculated tech placement map. Non-tech
    served rows keep null family/band. ``table`` is the served table counted by
    that map. Checkpoint rows carry source inputs, first_seen, vector and the
    observed derived experience, separately from the reference placement.
    """
    facts.mkdir(parents=True, exist_ok=True)
    # In production this checkpoint rides the same data/state commit as the live tick.
    # Input fragments uploaded before a failed state commit are orphan evidence, never
    # an advanced parent for the next run.
    state_dir = facts if state_dir is None else state_dir
    state_dir.mkdir(parents=True, exist_ok=True)
    prior_path = state_dir / STATE
    prior = {}
    previous_tick = ""
    if prior_path.exists():
        old = pq.read_table(prior_path)
        previous_tick = (old.schema.metadata or {}).get(b"ts", b"").decode()
        if not previous_tick or previous_tick >= stamp:
            raise ValueError("reference tick must advance its stamped parent")
        prior = {r["id"]: r for r in old.to_pylist()}
    columns = ["id", "ats", "first_seen", "description", "min_years", "vector"]
    columns += [c for c in RAW_FIELDS if c in table.schema.names]
    columns = list(dict.fromkeys(columns))
    absent = set(columns) - set(table.schema.names)
    if absent:
        raise ValueError(f"reference source lacks {sorted(absent)}")
    source_schema = pa.schema([table.schema.field(c) for c in columns])
    width = source_schema.field("vector").type.list_size
    fields = [
        pa.field("kind", pa.string()),
        *[
            f if f.name != "vector" else pa.field("vector", pa.list_(pa.float16()))
            for f in source_schema
        ],
        pa.field("reference_board", pa.string()),
        pa.field("reference_family", pa.string()),
        pa.field("reference_band", pa.string()),
    ]
    metadata = {
        b"ts": stamp.encode(),
        b"run_id": os.environ.get("GITHUB_RUN_ID", "").encode(),
        b"run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "").encode(),
        b"code_sha": os.environ.get("GITHUB_SHA", "").encode(),
        b"methodology": json.dumps(methodology, sort_keys=True).encode(),
        b"baseline": str(not prior_path.exists()).lower().encode(),
        b"previous_tick": previous_tick.encode(),
        b"vector_model": MODEL.encode(),
        b"vector_model_revision": MODEL_REVISION.encode(),
        b"source_columns": json.dumps(columns).encode(),
        b"source_kind": b"served-inputs",
    }
    schema = pa.schema(fields, metadata=metadata)
    path = facts / DIRECTORY / file_name(stamp)
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(".parquet.tmp")
    state_tmp = prior_path.with_suffix(".parquet.tmp")
    current = []
    seen = set()
    try:
        with pq.ParquetWriter(staged, schema, compression="zstd") as writer:
            reader = table.search().select(columns).limit(table.count_rows())
            for batch in reader.to_batches(4096):
                changed = []
                for source in batch.to_pylist():
                    job_id = source["id"]
                    if job_id in seen:
                        raise ValueError(f"duplicate reference id {job_id}")
                    seen.add(job_id)
                    placement = placements.get(job_id)
                    where = (
                        (placement.board, placement.family, placement.band)
                        if placement
                        else (None, None, None)
                    )
                    vector = np.asarray(source["vector"], dtype=np.float32)
                    if vector.shape != (width,) or not np.isfinite(vector).all():
                        raise ValueError(f"invalid reference vector for {job_id}")
                    raw = {k: v for k, v in source.items() if k != "vector"}
                    digest = hashlib.sha256(
                        json.dumps([raw, where], sort_keys=True).encode()
                        + vector.tobytes()
                    ).hexdigest()
                    state = dict(
                        zip(_STATE_COLUMNS, (job_id, digest, *where), strict=True)
                    )
                    current.append(state)
                    if prior.get(job_id) != state:
                        changed.append(
                            {
                                "kind": "present",
                                **source,
                                "vector": vector.astype(np.float16).tolist(),
                                "reference_board": where[0],
                                "reference_family": where[1],
                                "reference_band": where[2],
                            }
                        )
                if changed:
                    writer.write_table(pa.Table.from_pylist(changed, schema=schema))
            removed = [{"kind": "removed", "id": i} for i in prior.keys() - seen]
            if removed:
                writer.write_table(pa.Table.from_pylist(removed, schema=schema))
        state_schema = pa.schema(
            [(c, pa.string()) for c in _STATE_COLUMNS], metadata=metadata
        )
        pq.write_table(
            pa.Table.from_pylist(current, schema=state_schema),
            state_tmp,
            compression="zstd",
        )
        staged.replace(path)
        try:
            state_tmp.replace(prior_path)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
    finally:
        staged.unlink(missing_ok=True)
        state_tmp.unlink(missing_ok=True)
    return path


def checkpoints(facts: Path):
    """Yield (metadata, current served inputs) only for a complete baseline/parent chain.

    The dictionaries include reference placements for independent comparison. Consumers
    must calculate candidates from source fields rather than copying those placements.
    """
    current = {}
    previous = ""
    paths = sorted((facts / DIRECTORY).glob("*.parquet"))
    if not paths:
        raise ValueError("no reference baseline recorded")
    for path in paths:
        table = pq.read_table(path)
        metadata = table.schema.metadata or {}
        stamp = metadata.get(b"ts", b"").decode()
        baseline = metadata.get(b"baseline") == b"true"
        if not stamp or metadata.get(b"previous_tick", b"").decode() != previous:
            raise ValueError(f"broken reference parent chain at {path}")
        if baseline != (not previous):
            raise ValueError(f"missing or repeated reference baseline at {path}")
        for row in table.to_pylist():
            if row["kind"] == "removed":
                if row["id"] not in current:
                    raise ValueError(f"removal of unknown reference id {row['id']}")
                del current[row["id"]]
            elif row["kind"] == "present":
                current[row["id"]] = row
            else:
                raise ValueError(f"unknown reference kind {row['kind']}")
        previous = stamp
        yield metadata, current.copy()
