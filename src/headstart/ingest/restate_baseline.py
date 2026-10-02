"""Anchor a facts replay at a complete served Reference baseline.

Earlier observations are retained separately; the exact replay cannot pretend an
unread pre-existing Job was absent. Baseline membership also identifies duplicate
incumbents. Future raw Job facts still supply non-tech Jobs a widened filter may admit.
"""

from __future__ import annotations

from pathlib import Path


def committed_baseline(facts: Path, state: Path) -> Path | None:
    """Find the baseline on the committed checkpoint chain, ignoring orphan captures."""
    import pyarrow.parquet as pq

    checkpoint = state / "reference_state.parquet"
    if not checkpoint.exists():
        return None
    cursor = pq.read_schema(checkpoint).metadata[b"ts"].decode()
    paths = {
        pq.read_schema(p).metadata[b"ts"].decode(): p
        for p in (facts / "trend_reference").glob("*.parquet")
    }
    seen = set()
    while cursor:
        if cursor in seen or cursor not in paths:
            raise ValueError("broken committed baseline chain")
        seen.add(cursor)
        path = paths[cursor]
        metadata = pq.read_schema(path).metadata
        if metadata.get(b"baseline") == b"true":
            return path
        cursor = metadata[b"previous_tick"].decode()
    raise ValueError("committed reference has no baseline")


def seed_versions(versions, baseline: dict, stamp: str, future_facts):
    """Start versions at ``stamp``, inheriting served Jobs until subsequent facts replace them.

    For previously observed but unserved Jobs, carry the raw facts open at the baseline.
    For served Jobs, the baseline's observed source fields take precedence initially.
    A future changed/unlisted/off_board fact ends an inherited version, including an
    unlisted first future fact for a Job that never had an earlier listing fact.
    """
    import pyarrow as pa

    schema = versions.schema.append(pa.field("baseline_incumbent", pa.bool_()))
    out = []
    batches = []

    def append(row):
        out.append(row)
        if len(out) == 8192:
            batches.append(pa.Table.from_pylist(out, schema=schema))
            out.clear()

    inherited_end = {}
    for fact in sorted(future_facts, key=lambda r: r["run"]):
        if fact["run"] > stamp and fact["id"] in baseline:
            inherited_end.setdefault(fact["id"], (fact["run"], fact["kind"]))
    for batch in versions.to_batches(max_chunksize=8192):
        for row in batch.to_pylist():
            start, end = row["valid_from"], row["valid_to"]
            if end is not None and end <= stamp:
                continue
            if start <= stamp:
                if row["id"] in baseline:
                    inherited_end.setdefault(row["id"], (end, row["ended_as"]))
                    continue
                row = row | {"valid_from": stamp}
            elif row["id"] in baseline and row["id"] not in inherited_end:
                inherited_end[row["id"]] = (start, "changed")
            append(row | {"baseline_incumbent": False})
    for job_id, source in baseline.items():
        end, ended_as = inherited_end.get(job_id, (None, None))
        row = {name: source.get(name) for name in versions.schema.names}
        row.update(
            id=job_id,
            board=source["reference_board"] or source.get("board"),
            valid_from=stamp,
            valid_to=end,
            ended_as=ended_as,
            baseline_incumbent=True,
        )
        append(row)
    batches.append(pa.Table.from_pylist(out, schema=schema))
    return pa.concat_tables(batches).sort_by(
        [("id", "ascending"), ("valid_from", "ascending")]
    )
