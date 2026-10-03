"""Immutable observed input versions for replay, separate from listing lifecycles.

Description/vector edits can occur without a raw Job-fact field change. Inputs are
selected as of each interval, never from the latest mutable store. Disk-backed
content deduplication and a bounded cache keep full history out of Python memory.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import sqlite3
import tempfile
import zipfile
from collections import OrderedDict
from pathlib import Path

import numpy as np


class VersionSources:
    """Observed sources and exact replay-piece bindings, with a 256-record cache."""

    def __init__(self, width: int):
        self.width = width
        self._temporary = tempfile.TemporaryDirectory(prefix="trends-version-inputs-")
        self._db = sqlite3.connect(Path(self._temporary.name) / "sources.sqlite")
        self._db.executescript("""
            CREATE TABLE bodies (key TEXT PRIMARY KEY, vector BLOB, description TEXT);
            CREATE TABLE observations (id TEXT, stamp TEXT, body TEXT, observed TEXT,
                PRIMARY KEY(id, stamp));
            CREATE TABLE row_parts (body TEXT, fingerprint TEXT, logits BLOB,
                PRIMARY KEY(body, fingerprint));
            CREATE TABLE title_parts (title TEXT, fingerprint TEXT, logits BLOB,
                PRIMARY KEY(title, fingerprint));
            CREATE TABLE bindings (id TEXT, stamp TEXT, observed_at TEXT,
                PRIMARY KEY(id, stamp));
        """)
        self._cache = OrderedDict()
        self._missing = np.zeros(width, dtype=np.float32)

    def add_batch(self, stamp, rows, fingerprint=None):
        """Record source observations; deduplicate large bodies, not observed times."""
        for row in rows:
            vector = row.get("vector")
            raw_vector = (
                None if vector is None else np.asarray(vector, dtype="<f2").tobytes()
            )
            vector_hash = row.get("vector_fingerprint") or (
                "half:" + hashlib.sha256(raw_vector).hexdigest()
                if raw_vector is not None
                else "missing"
            )
            text = row.get("description")
            text_hash = row.get("description_hash") or (
                hashlib.sha256(text.encode()).hexdigest()
                if text is not None
                else "unknown"
            )
            body = hashlib.sha256(
                json.dumps([row["id"], vector_hash, text_hash]).encode()
            ).hexdigest()
            observed = {
                name: row[name]
                for name in (
                    "min_years",
                    "max_years",
                    "experience_source",
                    "first_seen",
                    "title",
                )
                if name in row
            }
            observed["vector_identity"] = vector_hash
            observed["description_identity"] = text_hash
            self._db.execute(
                "INSERT OR IGNORE INTO bodies VALUES (?, ?, ?)",
                (body, raw_vector, text),
            )
            self._db.execute(
                "INSERT OR REPLACE INTO observations VALUES (?, ?, ?, ?)",
                (row["id"], stamp, body, json.dumps(observed)),
            )
            if fingerprint:
                for table, key, field in (
                    ("row_parts", body, "row_logits"),
                    ("title_parts", row.get("title"), "title_logits"),
                ):
                    if row.get(field) is not None and key is not None:
                        self._db.execute(
                            f"INSERT OR IGNORE INTO {table} VALUES (?, ?, ?)",
                            (
                                key,
                                fingerprint,
                                np.asarray(row[field], dtype="<f4").tobytes(),
                            ),
                        )
        self._db.commit()
        self._cache.clear()

    def add_description(self, stamp, job_id, content_hash, text):
        """Attach a text observation without backdating a later vector."""
        exact = self._db.execute(
            """SELECT o.body, b.vector, o.observed
            FROM observations o JOIN bodies b ON b.key=o.body WHERE o.id=? AND o.stamp=?""",
            (job_id, stamp),
        ).fetchone()
        if exact is None:
            self.add_batch(
                stamp,
                [{"id": job_id, "description": text, "description_hash": content_hash}],
            )
            return
        observed = json.loads(exact[2])
        row = observed | {
            "id": job_id,
            "description": text,
            "description_hash": content_hash,
            "vector": np.frombuffer(exact[1], dtype="<f2")
            if exact[1] is not None
            else None,
            "vector_fingerprint": observed["vector_identity"],
        }
        self.add_batch(stamp, [row])
        body = self._db.execute(
            "SELECT body FROM observations WHERE id=? AND stamp=?", (job_id, stamp)
        ).fetchone()[0]
        self._db.execute(
            "INSERT OR IGNORE INTO row_parts SELECT ?, fingerprint, logits FROM row_parts WHERE body=?",
            (body, exact[0]),
        )
        self._db.commit()

    def events(self):
        """Only observed content changes, ordered by Job and time; score noise is not input."""
        previous_id, previous_inputs = None, None
        for job_id, stamp, body, observed in self._db.execute(
            "SELECT id, stamp, body, observed FROM observations ORDER BY id, stamp"
        ):
            identity = (body, observed)
            if job_id != previous_id or identity != previous_inputs:
                yield job_id, stamp
            previous_id, previous_inputs = job_id, identity

    def bind(self, job_id, stamp):
        """Bind a replay piece to the latest observation no later than its start."""
        source = self._db.execute(
            "SELECT stamp FROM observations WHERE id=? AND stamp<=? ORDER BY stamp DESC LIMIT 1",
            (job_id, stamp),
        ).fetchone()
        self._db.execute(
            "INSERT OR REPLACE INTO bindings VALUES (?, ?, ?)",
            (job_id, stamp, source[0] if source else None),
        )

    def finish_bindings(self):
        self._db.commit()

    def __contains__(self, key):
        return (
            self._db.execute(
                "SELECT 1 FROM bindings WHERE id=? AND stamp=?", key
            ).fetchone()
            is not None
        )

    def _record(self, key):
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        row = self._db.execute(
            """SELECT o.body, b.vector, b.description, o.observed
            FROM bindings a JOIN observations o ON o.id=a.id AND o.stamp=a.observed_at
            JOIN bodies b ON b.key=o.body WHERE a.id=? AND a.stamp=?""",
            key,
        ).fetchone()
        if row is None:
            return None
        record = (
            row[0],
            self._missing if row[1] is None else np.frombuffer(row[1], dtype="<f2"),
            row[2],
            json.loads(row[3]),
        )
        self._cache[key] = record
        if len(self._cache) > 256:
            self._cache.popitem(last=False)
        return record

    def get(self, key, default=None):
        record = self._record(key)
        if record is not None:
            return record[1:3]
        # Registered unknowns deliberately block a caller's latest-store fallback.
        return (self._missing, None) if key in self else default

    def observed(self, key):
        record = self._record(key)
        return record[3] if record else {}

    def logits(self, key, fingerprint, title):
        """Preserved numerical inputs only for the exact mathematical model fingerprint."""
        record = self._record(key)
        if record is None or not fingerprint:
            return None, None
        row = self._db.execute(
            "SELECT logits FROM row_parts WHERE body=? AND fingerprint=?",
            (record[0], fingerprint),
        ).fetchone()
        word = self._db.execute(
            "SELECT logits FROM title_parts WHERE title=? AND fingerprint=?",
            (title, fingerprint),
        ).fetchone()
        return tuple(
            np.frombuffer(value[0], dtype="<f4") if value else None
            for value in (word, row)
        )

    def close(self):
        self._cache.clear()
        self._db.close()
        self._temporary.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def bind_intervals(served, sources):
    """Split input edits without changing raw lifecycle dates or fabricating listings."""
    import pyarrow as pa

    events = {}
    for job_id, stamp in sources.events():
        events.setdefault(job_id, []).append(stamp)
    schema = served.schema.append(pa.field("input_from", pa.string()))
    out, batches = [], []
    for batch in served.to_batches(max_chunksize=8192):
        for row in batch.to_pylist():
            start, end = row["served_from"], row["served_to"]
            bounds = [start] + [
                stamp
                for stamp in events.get(row["id"], ())
                if stamp > start and (end is None or stamp < end)
            ]
            for index, at in enumerate(bounds):
                to = bounds[index + 1] if index + 1 < len(bounds) else end
                sources.bind(row["id"], at)
                out.append(
                    row
                    | {
                        "input_from": at,
                        "served_from": at,
                        "served_to": to,
                        "ended_as": row["ended_as"] if to == end else "changed",
                        "starts_as": row.get("starts_as") if at == start else "changed",
                    }
                )
                if len(out) == 8192:
                    batches.append(pa.Table.from_pylist(out, schema=schema))
                    out.clear()
    sources.finish_bindings()
    batches.append(pa.Table.from_pylist(out, schema=schema))
    return pa.concat_tables(batches)


def recorded_fingerprint(metadata, facts):
    """Recover a legacy mathematical fingerprint only when its archived math agrees."""
    from headstart.ingest import role_family_classifier as classifier

    method = json.loads(metadata.get(b"methodology", b"{}"))
    if method.get("classifier_inputs_fingerprint"):
        return method["classifier_inputs_fingerprint"]
    archive = facts / "reference_rules" / f"{method.get('rules_fingerprint', '')}.zip"
    if not archive.exists() or not hasattr(classifier.Head, "inputs_fingerprint"):
        return None

    def mathematics(source):
        tree = ast.parse(source)
        functions = {}
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "normalise":
                functions[node.name] = node
            if isinstance(node, ast.ClassDef) and node.name == "Head":
                functions.update(
                    {
                        n.name: n
                        for n in node.body
                        if isinstance(n, ast.FunctionDef)
                        and n.name in {"title_logits", "row_logits"}
                    }
                )
        for node in functions.values():
            if ast.get_docstring(node) is not None:
                del node.body[0]
        return {
            key: ast.dump(node, include_attributes=False)
            for key, node in functions.items()
        }

    current = inspect.getsource(classifier)
    with zipfile.ZipFile(archive) as bundle:
        archived = bundle.read(
            "src/headstart/ingest/role_family_classifier.py"
        ).decode()
        if mathematics(current) != mathematics(archived):
            return None
        with tempfile.TemporaryDirectory(prefix="archived-head-inputs-") as tmp:
            root = Path(tmp)
            for name in ("manifest.json", "head.npz"):
                (root / name).write_bytes(
                    bundle.read(f"config/role_family_classifier/{name}")
                )
            return classifier.Head(root).inputs_fingerprint


def load_reference_sources(sources, facts, baseline, state):
    """Read committed reference observations, aligning their run IDs to raw-fact ticks."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from headstart.ingest import job_facts

    run_stamps = {}
    for path in (facts / job_facts.JOB_FACTS).glob("*.parquet"):
        metadata = pq.read_schema(path).metadata
        if metadata.get(b"run_id"):
            run_stamps[metadata[b"run_id"].decode()] = metadata[b"stamp"].decode()
    paths = {
        pq.read_schema(path).metadata[b"ts"].decode(): path
        for path in (facts / "trend_reference").glob("*.parquet")
    }
    base_stamp = pq.read_schema(baseline).metadata[b"ts"].decode()
    paths[base_stamp] = baseline
    committed = state / "reference_state.parquet"
    if committed.exists():
        chain, cursor, seen = (
            [],
            pq.read_schema(committed).metadata[b"ts"].decode(),
            set(),
        )
        while cursor:
            if cursor in seen or cursor not in paths:
                raise ValueError("broken committed input-observation chain")
            seen.add(cursor)
            path = paths[cursor]
            chain.append(path)
            cursor = (
                (pq.read_schema(path).metadata or {})
                .get(b"previous_tick", b"")
                .decode()
            )
        paths = [
            path
            for path in reversed(chain)
            if pq.read_schema(path).metadata[b"ts"].decode() >= base_stamp
        ]
    else:
        paths = [paths[stamp] for stamp in sorted(paths) if stamp >= base_stamp]
    fingerprints = {}
    for path in paths:
        source = pq.ParquetFile(path)
        metadata = source.schema_arrow.metadata
        stamp = metadata[b"ts"].decode()
        effective = (
            stamp
            if stamp == base_stamp
            else run_stamps.get(metadata.get(b"run_id", b"").decode(), stamp)
        )
        method = metadata.get(b"methodology", b"{}")
        if method not in fingerprints:
            fingerprints[method] = recorded_fingerprint(metadata, facts)
        columns = [
            name
            for name in (
                "id",
                "kind",
                "vector",
                "description",
                "vector_fingerprint",
                "min_years",
                "max_years",
                "experience_source",
                "first_seen",
                "title",
                "title_logits",
                "row_logits",
            )
            if name in source.schema_arrow.names
        ]
        count = 0
        for batch in source.iter_batches(batch_size=4096, columns=columns):
            table = pa.Table.from_batches([batch])
            if "kind" in table.schema.names:
                table = table.filter(pa.compute.equal(table["kind"], "present"))
            if not len(table):
                continue
            vectors = (
                table["vector"]
                .combine_chunks()
                .flatten()
                .to_numpy()
                .reshape(len(table), sources.width)
            )
            records = table.drop_columns(["vector"]).to_pylist()
            sources.add_batch(
                effective,
                (
                    row | {"vector": vector}
                    for row, vector in zip(records, vectors, strict=True)
                ),
                fingerprints[method],
            )
            count += len(table)
        print(
            f"Immutable replay inputs: {path.name}; {count} observed rows", flush=True
        )


def load_description_sources(sources, facts, store):
    """Resolve immutable identities one ATS at a time; missing historical bodies stay unknown."""
    import pyarrow.parquet as pq

    from headstart.ingest import description_facts, job_facts
    from headstart.ingest.update_descriptions import read_store

    run_stamps = {}
    for path in (facts / job_facts.JOB_FACTS).glob("*.parquet"):
        metadata = pq.read_schema(path).metadata
        key = (
            metadata.get(b"run_id", b"").decode(),
            metadata.get(b"run_attempt", b"").decode(),
        )
        if key[0]:
            run_stamps[key] = metadata[b"stamp"].decode()
    for directory in sorted((facts / "description_facts").glob("*")):
        if not directory.is_dir():
            continue
        current = read_store(store / directory.name)
        count = 0
        for observation in description_facts.iter_observations(
            facts, ats=directory.name
        ):
            stamp = run_stamps.get(
                (observation["run_id"], observation["run_attempt"]),
                observation["observed_at"],
            )
            text = description_facts.read_description(
                facts, current, observation["id"], observation["description_hash"]
            )
            sources.add_description(
                stamp, observation["id"], observation["description_hash"], text
            )
            count += 1
        print(
            f"Immutable description inputs: {directory.name}; {count} identities",
            flush=True,
        )
