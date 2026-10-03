"""Tech-subset description identities and immutable superseded text.

Only ``update_descriptions``' accepted new/changed text produces facts. Current text
stays in the description store; only text about to be superseded is archived.
These are parsed Job descriptions, never raw ATS payloads (deferred to #904).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path


def description_hash(text: str) -> str:
    """SHA-256 of exact UTF-8 text; no stripping or other normalization here."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_immutable(directory: Path, records: list[dict]) -> None:
    """Publish a complete content-named Parquet file, never replace an existing one.

    Single pipeline writer. Retries verify an existing file rather than overwriting
    it; failed writes remove only their own temporary file.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    if not records:
        return
    records = sorted(records, key=lambda r: (r["id"], r["description_hash"]))
    payload = json.dumps(records, sort_keys=True, ensure_ascii=False).encode("utf-8")
    path = directory / (hashlib.sha256(payload).hexdigest() + ".parquet")
    table = pa.Table.from_pylist(records)
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=directory, suffix=".tmp", delete=False) as fh:
        staged = Path(fh.name)
    try:
        pq.write_table(table, staged, compression="zstd")
        try:
            os.link(staged, path)
        except FileExistsError:
            if not pq.read_table(path).equals(table):
                raise ValueError(
                    f"immutable description file differs: {path}"
                ) from None
    finally:
        staged.unlink(missing_ok=True)


def record(
    facts_dir: Path,
    ats: str,
    learned: list[dict],
    superseded: list[dict],
    observed_at: str,
) -> None:
    """Archive superseded text, then record identities before replacing current text.

    Inputs are the stage's accepted changes, not the full corpus. Facts contain
    ``id, description_hash, observed_at, run_id, run_attempt, code_sha``. Archive
    rows contain ``id, description_hash, description``. Any failure propagates so
    the caller must leave its current store untouched. A completed archive may
    survive a later failure; it still holds a real prior version, never future text.
    """
    _write_immutable(
        facts_dir / "description_archive" / ats,
        [
            dict(r, description_hash=description_hash(r["description"]))
            for r in superseded
        ],
    )
    identity = {
        "observed_at": observed_at,
        "run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        "code_sha": os.environ.get("GITHUB_SHA", ""),
    }
    _write_immutable(
        facts_dir / "description_facts" / ats,
        [
            {
                "id": r["id"],
                "description_hash": description_hash(r["description"]),
                **identity,
            }
            for r in learned
        ],
    )


def read_description(
    facts_dir: Path,
    current: Mapping[str, str],
    job_id: str,
    content_hash: str,
) -> str | None:
    """Return this exact (id, hash)'s text from current state or archive, else None.

    ``current`` is an already-loaded description-store mapping (one ATS is enough).
    The caller selects the historical hash using facts' observed time/run identity;
    this helper never selects a newer version for a missing historical one. Corrupt
    archive files raise; a missing version returns unknown (None). Archive lookup
    scans that ATS's fragments with Parquet filters; no mutable lookup state exists.
    """
    import pyarrow.parquet as pq

    text = current.get(job_id)
    if text is not None and description_hash(text) == content_hash:
        return text
    ats = job_id.split(":", 1)[0]
    for path in sorted((facts_dir / "description_archive" / ats).glob("*.parquet")):
        rows = pq.read_table(
            path,
            filters=[("id", "=", job_id), ("description_hash", "=", content_hash)],
        ).to_pylist()
        for row in rows:
            if description_hash(row["description"]) != content_hash:
                raise ValueError(f"description archive hash mismatch: {path}")
            return row["description"]
    return None
