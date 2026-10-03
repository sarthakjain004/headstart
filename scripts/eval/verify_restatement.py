"""Independent, local-only restatement publication gate (ADR-0330).

Run under the selected frozen policy's PYTHONPATH. --metadata is replay.json,
with the approved publication files/inputs inventories and bounds. Optional
verification_ticks maps {tick, reference_tick, reference: {path, sha256},
universe?: {path, sha256}} using dataset-relative paths from the inputs inventory.
Without that mapping, shared captured run IDs establish the correspondence.
Reference files are the complete baseline followed by its exact parent chain,
never nearest-time pairs. Candidate files retain their normal ``ts`` metadata.

Normally, pinned raw facts/reads are reduced directly into the expected universe.
Independent checkpoint/source history supplies historical text and math inputs.
Optional universe files are FULL independently reconstructed pre-dedup served
source snapshots, including grace/Dormancy decisions. Their metadata must contain
source_kind=raw-facts-served-inputs, complete=true, ts=<candidate tick>,
rules_fingerprint, input_revision and source_files=[{path, sha256}]. Source files
must include job-facts and Board-read Parquets. These snapshots are evidence from
a separate raw-facts oracle, NOT placements or candidate served intervals. This
Sidecar independently checks membership/raw fields from fact/read events as well
as downstream policy; descriptions and math inputs require immutable sources.
Without source evidence, new coverage is unverified and publication stays incomplete.
The CLI binds candidate hashes through replay.json's inventory. Direct verify()
callers instead need rules_fingerprint/input_revision stamped on candidate files.

Full reference rows supply historical description/vector and observed min_years.
Missing text never borrows today's text. Matching classifier_input_fingerprint
permits captured float32 logits; otherwise current title cache plus historical
vector is used and float16 approximation is reported. No discrepancy is waived.
Legacy ticks preceding the complete baseline are explicitly nonreplayable.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import inspect
import itertools
import json
import subprocess
import tempfile
import time
import zipfile
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from headstart.boards import eightfold_backing
from headstart.boards.board_identity import ats_of
from headstart.ingest import (
    board_dormancy,
    board_failures,
    derived_meta,
    description_facts,
    doc_prep,
    index_plan,
    job_facts,
    role_trends,
)
from headstart.ingest import role_family_classifier as rfc
from headstart.ingest.update_descriptions import read_store
from headstart.jobs import tech_filter
from headstart.trends import role_taxonomy
from headstart.trends.trend_history import DELTAS, TICK_COLUMNS

TURNOVER = {"opened": 1, "closed": -1, "recounted_in": 1, "recounted_out": -1}
SOURCE_COLUMNS = {
    "title",
    "department",
    "requisition",
    "description",
    "experience",
    "employment_type",
    "vector",
    "min_years",
}
MATH_SOURCE_COLUMNS = (
    "description",
    "vector",
    "min_years",
    "experience_source",
    "title_logits",
    "row_logits",
)


def sha256(path):
    """Hash incrementally, including large locally cached evidence."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def rules_fingerprint(root):
    """Read-only counterpart of trend_reference.freeze_rules's content digest."""
    paths = sorted(
        p
        for directory in ("src/headstart", "config", "data/validate")
        for p in (root / directory).rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    )
    paths.append(root / "pyproject.toml")
    digest = hashlib.sha256()
    for path in paths:
        name = path.relative_to(root).as_posix().encode()
        digest.update(len(name).to_bytes(8, "big") + name)
        digest.update(bytes.fromhex(sha256(path)))
    return digest.hexdigest()


def captured_fingerprint(method, inputs):
    """Recover old math-input identity from its pinned rule archive, without executing it."""
    fingerprint = method.get("classifier_inputs_fingerprint") or method.get(
        "classifier_input_fingerprint"
    )
    if fingerprint:
        return fingerprint
    rules = method.get("rules_fingerprint")
    item = inputs.get("pinned_inputs", {}).get(
        f"data/facts/reference_rules/{rules}.zip"
    )
    if item is None:
        return None
    with zipfile.ZipFile(item["path"]) as archive:
        source = archive.read("src/headstart/ingest/role_family_classifier.py").decode()

        def mathematics(text):
            found = {}
            for node in ast.parse(text).body:
                nodes = (
                    node.body
                    if isinstance(node, ast.ClassDef) and node.name == "Head"
                    else [node]
                )
                for function in nodes:
                    if isinstance(function, ast.FunctionDef) and function.name in {
                        "normalise",
                        "title_logits",
                        "row_logits",
                    }:
                        if ast.get_docstring(function) is not None:
                            del function.body[0]
                        found[function.name] = ast.dump(
                            function, include_attributes=False
                        )
            return found

        if mathematics(source) != mathematics(inspect.getsource(rfc)):
            return None
        with tempfile.TemporaryDirectory(prefix="oracle-head-inputs-") as directory:
            root = Path(directory)
            for name in ("manifest.json", "head.npz"):
                (root / name).write_bytes(
                    archive.read(f"config/role_family_classifier/{name}")
                )
            return rfc.Head(root).inputs_fingerprint


class Policy:
    """Established production rules; never calls the candidate replay implementation."""

    def __init__(self, root, title_cache, state=None, encode_budget_seconds=1800):
        # A changed PYTHONPATH must not silently load rules from another checkout.
        import sys

        for name, module in tuple(sys.modules.items()):
            source = getattr(module, "__file__", None)
            if name.startswith("headstart.") and source and source.endswith(".py"):
                package = (root / "src/headstart").resolve()
                resolved = Path(source).resolve()
                if not resolved.is_relative_to(package):
                    raise ValueError(f"loaded rule comes from another checkout: {name}")
                frozen = package / resolved.relative_to(package)
                if not frozen.exists() or sha256(Path(source)) != sha256(frozen):
                    raise ValueError(f"loaded rule differs from frozen policy: {name}")
        head_dir = root / "config/role_family_classifier"
        self.head = rfc.Head(head_dir)
        families = role_taxonomy.load_families(root / "config/role_families.json")
        self.head.check_families(families)
        self.input_fingerprint = self.head.inputs_fingerprint
        self.encode_budget_seconds = encode_budget_seconds
        # No fingerprint argument: production migration would rewrite pinned evidence.
        self.cache = rfc.load_cache(title_cache, self.head.version)
        if self.cache.inputs_fingerprint not in {None, self.input_fingerprint}:
            self.cache = rfc.Cache(self.head.version, {}, self.input_fingerprint)
        self.keep = index_plan.live_keep_set(root / "data/validate/liveness")
        self.live = index_plan.boards_by_canon(self.keep)
        self.keep_set = job_facts.RunScope.of(
            set(),
            set(),
            self.live,
            board_failures.load((state or root / "data/state") / "board_failures.csv"),
        ).keep_set
        self.site_jobs = index_plan.workday_site_jobs(root / "data/validate/liveness")
        self.backing = eightfold_backing.load(
            root / "data/validate/eightfold_backing.csv"
        )
        self.winners = set()
        self.watchlist = role_taxonomy.load_watchlist(
            root / "config/role_watchlist.json",
            set(families),
        )

    def transform(self, rows, captured_fingerprint=None):
        """Sources to placements with production sync/prune and current classification."""
        quality = Counter()
        reasons = Counter()
        admitted = {}
        for n, (job_id, row) in enumerate(rows.items(), 1):
            if n % 40960 == 0:
                print(f"Oracle admission: {n:,}/{len(rows):,} source rows", flush=True)
            if (
                getattr(self, "keep_set", None) is not None
                and index_plan.resolve_board(job_id, self.live).casefold()
                not in self.keep_set
            ):
                reasons["off_board"] += 1
                continue
            if not tech_filter.is_tech(row.get("title"), row.get("department")):
                reasons["tech_rejected"] += 1
                continue
            text = row.get("description")
            if text is None:
                # A served checkpoint proves prior English admission, not its text.
                english = row.get("observed_english")
                if english is None and row.get("title_only_recovery"):
                    english = doc_prep.is_english(row.get("title") or "", "")
                    quality["title_only_language_judgement"] += 1
                if english is None and row.get("kind") == "present":
                    english = True
                if english is None:
                    raise ValueError(f"no historical English evidence for {job_id}")
                quality["observed_english_without_text"] += 1
            else:
                english = doc_prep.is_english(row.get("title") or "", text)
            if not english:
                reasons["english_rejected"] += 1
                continue
            admitted[job_id] = row
        requisitions = {
            i: r["requisition"] for i, r in admitted.items() if r.get("requisition")
        }
        ranks = index_plan.duplicate_ranks(
            admitted,
            self.keep,
            site_jobs=self.site_jobs,
            requisitions=requisitions,
            backing=self.backing,
        )
        self.groups = {i: ranks.get(i, ((i, ""), ()))[0] for i in admitted}
        self.ages, self.years = {}, {}
        for job_id, row in admitted.items():
            group, seen = self.groups[job_id], row.get("first_seen")
            if group not in self.ages:
                self.ages[group] = seen
            elif not seen or not self.ages[group]:
                self.ages[group] = None
            else:
                self.ages[group] = min(seen, self.ages[group])
        incumbents = self.winners & admitted.keys()
        # Absence/grace is already represented by independently reconstructed source
        # eligibility, so this call uses no absence scope. Sync chooses incumbents.
        plan = index_plan.plan_sync(
            incumbents,
            admitted,
            (),
            self.live,
            site_jobs=self.site_jobs,
            requisitions=requisitions,
            backing=self.backing,
        )
        eligible = incumbents | plan.add
        off, duplicates = index_plan.plan_prune(
            eligible,
            self.keep,
            site_jobs=self.site_jobs,
            requisitions=requisitions,
            backing=self.backing,
        )
        if hasattr(self, "keep_set") and self.keep_set is None:
            off = []
        self.winners = eligible - set(off) - duplicates.keys()
        reasons.update(
            {"off_board": len(off), "duplicate": len(duplicates) + len(plan.refused)}
        )
        needed = []
        for job_id in self.winners:
            row = admitted[job_id]
            exact = (
                row.get("classifier_input_fingerprint", captured_fingerprint)
                == self.input_fingerprint
            )
            if (not exact or row.get("title_logits") is None) and rfc.normalise(
                row.get("title")
            ) not in self.cache.title_logits:
                needed.append(row.get("title"))
        if needed:
            budget = getattr(self, "encode_budget_seconds", 1800)
            if budget <= 0:
                raise ValueError(
                    f"independent title coverage incomplete: {len(set(needed))} titles; encoding budget exhausted"
                )
            started = time.monotonic()
            added = rfc.fill(self.cache, self.head, needed, budget, lambda _: None)
            self.encode_budget_seconds = max(0, budget - (time.monotonic() - started))
            quality["independently_encoded_titles"] += added
            missing = {
                rfc.normalise(title) for title in needed
            } - self.cache.title_logits.keys()
            if missing:
                raise ValueError(
                    f"independent title coverage incomplete: {len(missing)} titles after finite encoding budget"
                )
        result = {}
        for n, job_id in enumerate(sorted(self.winners), 1):
            if n % 40960 == 0:
                print(
                    f"Oracle placement: {n:,}/{len(self.winners):,} winners", flush=True
                )
            row = admitted[job_id]
            exact = (
                row.get("classifier_input_fingerprint", captured_fingerprint)
                == self.input_fingerprint
            )
            title = row.get("title")
            title_logits = (
                row.get("title_logits")
                if exact
                else self.cache.title_logits.get(rfc.normalise(title))
            )
            if title_logits is None:
                title_logits = self.cache.title_logits.get(rfc.normalise(title))
            row_logits = row.get("row_logits") if exact else None
            if row_logits is None:
                vector = row.get("vector")
                if vector is None:
                    vector = np.zeros(self.head.row_vector_dim, np.float32)
                    quality["title_only_without_vector"] += 1
                vector = np.asarray(vector)
                if (
                    vector.dtype == np.float16
                    or row.get("vector_precision") != "float32"
                ):
                    quality["float16_classifier_approximation"] += 1
                matrix = np.asarray([vector], np.float32)
                if (
                    matrix.shape != (1, self.head.row_vector_dim)
                    or not np.isfinite(matrix).all()
                ):
                    raise ValueError(
                        f"invalid historical classifier vector for {job_id}"
                    )
                row_logits = self.head.row_logits(matrix)[0]
            else:
                quality["captured_float32_logits"] += 1
            for logits in (title_logits, row_logits):
                if logits is not None and (
                    np.asarray(logits).shape != (len(self.head.families),)
                    or not np.isfinite(logits).all()
                ):
                    raise ValueError(f"invalid historical logits for {job_id}")
            cache = rfc.Cache(self.head.version, {})
            if title_logits is not None:
                cache.title_logits[rfc.normalise(title)] = np.asarray(
                    title_logits, np.float32
                )
            family = rfc.decide_rows_scored(
                cache, self.head, [title], np.asarray([row_logits], np.float32)
            )[0][0]
            if family == role_taxonomy.NON_TECH:
                reasons["classifier_non_tech"] += 1
                continue
            derived = derived_meta.derive(row | {"ats": ats_of(job_id)})
            years = derived["min_years"]
            if (
                row.get("description") is None
                and "min_years" in row
                and row.get("observed_min_years_available", True)
                and derived["experience_source"] != "field"
                and row.get("experience_source") != "seniority"
            ):
                years = row.get("min_years")
                quality["observed_min_years_without_text"] += 1
            result[job_id] = (
                index_plan.resolve_board(job_id, self.live),
                family,
                role_taxonomy.band(years, title, row.get("employment_type")),
            )
            self.years[job_id] = years
        return result, quality, reasons


def evidence(item, base, candidate_root):
    path = (base / item["path"]).resolve()
    if path.is_relative_to(candidate_root.resolve()):
        raise ValueError("oracle evidence must not come from candidate output")
    if sha256(path) != item["sha256"]:
        raise ValueError(f"evidence digest mismatch: {path}")
    return path


def rows_by_id(table):
    rows = {}
    for batch in table.to_batches(max_chunksize=4096):
        for row in batch.to_pylist():
            if not row.get("id") or row["id"] in rows:
                raise ValueError("missing or repeated source id")
            for name in ("vector", "title_logits", "row_logits"):
                if row.get(name) is not None:
                    precision = (
                        np.float32
                        if name != "vector" or row.get("vector_precision") == "float32"
                        else np.float16
                    )
                    row[name] = np.asarray(row[name], dtype=precision)
            rows[row["id"]] = row
    return rows


def candidate_ticks(root, fingerprint, revision, diagnostics=None):
    """Read narrow deltas/placements only. Duplicate stamps fail rather than overwrite."""
    out, bound = {}, True
    for path in (root / DELTAS).glob("*.parquet"):
        table = pq.read_table(path)
        expected_schema = pa.schema(
            [(c, pa.int64() if c == "delta" else pa.string()) for c in TICK_COLUMNS]
        )
        if not table.schema.equals(expected_schema, check_metadata=False):
            raise ValueError(f"candidate tick schema mismatch: {path.name}")
        stamp = (table.schema.metadata or {})[b"ts"].decode()
        metadata = table.schema.metadata or {}
        bound &= (
            metadata.get(b"rules_fingerprint") == fingerprint.encode()
            and metadata.get(b"input_revision") == revision.encode()
        )
        datetime.fromisoformat(stamp)
        if stamp in out:
            raise ValueError(f"repeated candidate tick {stamp}")
        out[stamp] = table
    placements = {}
    for path in (root / "placements").glob("*.parquet"):
        columns = ["id", "board", "family", "band"]
        if diagnostics is not None:
            columns += [
                c
                for c in (
                    "first_seen",
                    "input_quality",
                    "recount_cause",
                    "recountcause",
                )
                if c in pq.read_schema(path).names
            ]
        table = pq.read_table(path, columns=columns)
        stamp = (table.schema.metadata or {})[b"ts"].decode()
        metadata = table.schema.metadata or {}
        bound &= (
            metadata.get(b"rules_fingerprint") == fingerprint.encode()
            and metadata.get(b"input_revision") == revision.encode()
        )
        if stamp in placements:
            raise ValueError(f"repeated placement tick {stamp}")
        rows = rows_by_id(table)
        placements[stamp] = {
            i: (r["board"], r["family"], r["band"]) for i, r in rows.items()
        }
        if diagnostics is not None:
            diagnostics[stamp] = rows
    return out, placements, bound


def check_tick(table, levels, placements):
    """Independent arithmetic: no candidate tick_counts/unbalanced helpers."""
    delta, turnover = Counter(), Counter()
    errors = []
    for row in table.to_pylist():
        metric, amount = row["metric"], row["delta"]
        if not isinstance(amount, int):
            raise TypeError("noninteger delta")
        key = (row["board"], row["family"], row["band"])
        if metric in {"stock", "new"}:
            levels[(metric, *key)] += amount
            if metric == "stock":
                delta[key] += amount
        elif metric in TURNOVER:
            if amount < 0:
                errors.append("negative_turnover")
            turnover[key] += TURNOVER[metric] * amount
        else:
            errors.append("unsupported_metric")
    if any(n < 0 for n in levels.values()):
        errors.append("negative_level")
    keys = {k[1:] for k in levels}
    if any(levels[("new", *k)] > levels[("stock", *k)] for k in keys):
        errors.append("new_exceeds_stock")
    tech_keys = {
        k for k in delta.keys() | turnover.keys() if k[1] != role_taxonomy.NON_TECH
    }
    if any(delta[k] != turnover[k] for k in tech_keys):
        errors.append("turnover_identity")
    actual = Counter(placements.values())
    stored = Counter(
        {
            k[1:]: n
            for k, n in levels.items()
            if k[0] == "stock"
            and k[2] != role_taxonomy.NON_TECH
            and not k[2].startswith(role_taxonomy.WATCH_PREFIX)
            and n
        }
    )
    if actual != stored:
        errors.append("placement_stock_reconciliation")
    return sorted(set(errors))


def raw_sources(proofs, baseline, baseline_tick, tick, policy, context=None):
    """Check independent universe membership/raw fields against actual facts and reads.

    This reducer does not call restate_replay/served/count. It independently applies
    the two-read absence rule and production PostedDates over the WHOLE listed set.
    Historical description/math inputs still need their own immutable provenance.
    """
    listed, serving, grace, dormant_since, started = {}, {}, {}, {}, {}
    first_seen = {i: r.get("first_seen") for i, r in baseline.items()}
    first_reads, removed, listed_at = {}, {}, {}
    by_board = {}
    seeded = False
    stamped = sorted((pq.read_schema(p).metadata[b"stamp"].decode(), p) for p in proofs)

    def seed():
        for job_id, row in baseline.items():
            serving[job_id] = row
            started[job_id] = baseline_tick

    for stamp, group in itertools.groupby(stamped, key=lambda pair: pair[0]):
        if stamp > tick:
            break
        if not seeded and stamp > baseline_tick:
            seed()
            seeded = True
        authoritative = set()
        observed = set()
        for _, path in group:
            print(f"Oracle raw evidence: {path.name} at {stamp}", flush=True)
            schema = pq.read_schema(path)
            if {"id", "kind", "title", "department"} <= set(schema.names):
                columns = [
                    c
                    for c in ("id", "kind", "board", *job_facts.RAW_FIELDS)
                    if c in schema.names
                ]
                for batch in pq.ParquetFile(path).iter_batches(
                    batch_size=4096, columns=columns
                ):
                    for row in batch.to_pylist():
                        job_id = row["id"]
                        if job_id in observed:
                            raise ValueError("repeated raw Job observation")
                        observed.add(job_id)
                        board = index_plan.resolve_board(job_id, policy.live).casefold()
                        members = by_board.setdefault(board, set())
                        if row["kind"] in {"listed", "changed"}:
                            if row["kind"] == "listed":
                                listed_at[job_id] = stamp
                            first_seen.setdefault(job_id, stamp)
                            row["first_seen"] = first_seen[job_id]
                            if not tech_filter.is_tech(
                                row.get("title"), row.get("department")
                            ):
                                row = {
                                    k: row.get(k)
                                    for k in (
                                        "id",
                                        "kind",
                                        "board",
                                        "title",
                                        "department",
                                        "posted_at",
                                    )
                                }
                                row["first_seen"] = first_seen[job_id]
                            listed[job_id] = row
                            serving[job_id] = row
                            members.add(job_id)
                            started[job_id] = stamp
                            grace.pop(job_id, None)
                        elif row["kind"] in {"unlisted", "off_board"}:
                            listed.pop(job_id, None)
                            members.discard(job_id)
                            if row["kind"] == "off_board":
                                serving.pop(job_id, None)
                                grace.pop(job_id, None)
                                removed[job_id] = (stamp, "off_board")
                            elif job_id in serving:
                                grace[job_id] = stamp
                        else:
                            raise ValueError("unknown raw Job observation")
            elif {"board", "in_scope", "outcome"} <= set(schema.names):
                for batch in pq.ParquetFile(path).iter_batches(
                    batch_size=4096, columns=["board", "in_scope", "outcome"]
                ):
                    for row in batch.to_pylist():
                        board = row["board"].casefold()
                        if row["outcome"] != "error":
                            first_reads.setdefault(board, stamp)
                        if row["in_scope"]:
                            authoritative.add(board)
        for job_id, absent_at in list(grace.items()):
            board = index_plan.resolve_board(job_id, policy.live).casefold()
            if board in authoritative and stamp > absent_at:
                serving.pop(job_id, None)
                removed[job_id] = (stamp, "unlisted")
                del grace[job_id]
        served_by_board = {}
        for job_id in serving:
            board = index_plan.resolve_board(job_id, policy.live).casefold()
            served_by_board.setdefault(board, set()).add(job_id)
        for board in authoritative:
            dates = board_dormancy.PostedDates()
            for job_id in by_board.get(board, ()):
                dates.see(board, listed[job_id].get("posted_at"))
            dormant = board in dates.dormant(datetime.fromisoformat(stamp).date(), ())
            if dormant:
                onset = dormant_since.setdefault(board, stamp)
                for job_id in served_by_board.get(board, ()):
                    if stamp > onset or started[job_id] >= onset:
                        del serving[job_id]
                        removed[job_id] = (stamp, "dormant")
            elif board in dormant_since:
                del dormant_since[board]
                for job_id in by_board.get(board, ()):
                    serving[job_id] = listed[job_id]
                    started[job_id] = stamp
        if not seeded and stamp == baseline_tick:
            seed()
            seeded = True
    if not seeded:
        seed()
    if context is not None:
        context.update(
            {
                "first_reads": first_reads,
                "removed": removed,
                "physical_ids": set(serving),
                "listed_at": listed_at,
            }
        )
    return serving


def check_raw_truth(
    source, proofs, baseline, baseline_tick, tick, policy, context=None
):
    """Check supplied source snapshots against the separate raw-fact reducer."""
    expected = raw_sources(proofs, baseline, baseline_tick, tick, policy, context)
    if expected.keys() != source.keys():
        raise ValueError(
            f"raw universe membership differs from independent facts: {len(expected.keys() - source.keys())} missing, {len(source.keys() - expected.keys())} extra"
        )
    for job_id, raw in expected.items():
        if any(
            source[job_id].get(k) != raw[k] for k in job_facts.RAW_FIELDS if k in raw
        ):
            raise ValueError(f"raw universe changed observed fields for {job_id}")
        if source[job_id].get("first_seen") != raw.get("first_seen"):
            raise ValueError(f"raw universe changed historical age for {job_id}")


def check_source_inputs(source, reference, paths, tick, inputs):
    """Check original text/math inputs independently, including newly admitted sources.

    Source proofs carry source_kind=historical-job-inputs and metadata stamp.
    Per-row valid_from (else file stamp) identifies the observed version.
    """
    original, starts = dict(reference), {}
    for path in paths:
        schema = pq.read_schema(path)
        metadata = schema.metadata or {}
        if metadata.get(b"source_kind") != b"historical-job-inputs":
            continue
        stamp = metadata[b"stamp"].decode()
        method = json.loads(metadata.get(b"methodology", b"{}"))
        fingerprint = captured_fingerprint(method, inputs)
        for batch in pq.ParquetFile(path).iter_batches(batch_size=4096):
            for row in rows_by_id(pa.Table.from_batches([batch])).values():
                start = row.get("valid_from") or stamp
                if start <= tick and start >= starts.get(row["id"], ""):
                    original[row["id"]] = row | {
                        "classifier_input_fingerprint": fingerprint
                    }
                    starts[row["id"]] = start
    unknown = 0
    for job_id, row in source.items():
        if not tech_filter.is_tech(row.get("title"), row.get("department")):
            continue
        anchor = original.get(job_id)
        if anchor is None or any(
            row.get(k) != anchor.get(k)
            for k in ("title", "department", "experience", "employment_type")
        ):
            unknown += 1
            continue
        row["classifier_input_fingerprint"] = anchor.get("classifier_input_fingerprint")
        if row.get("description") is None:
            english = anchor.get("observed_english")
            if english is None and anchor.get("kind") == "present":
                english = True
            if english is None:
                unknown += 1
            elif (
                row.get("observed_english") is not None
                and row["observed_english"] != english
            ):
                raise ValueError(f"historical English observation mismatch: {job_id}")
            else:
                row["observed_english"] = english
        for key in MATH_SOURCE_COLUMNS:
            a, b = row.get(key), anchor.get(key)
            if key in {"vector", "title_logits", "row_logits"}:
                same = (a is None and b is None) or (
                    a is not None and b is not None and np.array_equal(a, b)
                )
            else:
                same = a == b
            if not same:
                raise ValueError(f"historical source input mismatch: {job_id}/{key}")
    return unknown


def native_description_inputs(inputs, tick, wanted):
    """Read pinned native identities independently; never select text by latest id alone."""
    pinned = inputs.get("pinned_inputs", {})
    facts = Path(inputs["facts_root"]) if inputs.get("facts_root") else None
    if facts is None:
        return {}
    run_stamps = {}
    for name, item in pinned.items():
        if name.startswith(("data/facts/job_facts/", "data/facts/board_reads/")):
            metadata = pq.read_schema(item["path"]).metadata or {}
            key = (
                metadata.get(b"run_id", b"").decode(),
                metadata.get(b"run_attempt", b"").decode(),
            )
            if all(key):
                stamp = metadata[b"stamp"].decode()
                if key in run_stamps and run_stamps[key] != stamp:
                    raise ValueError("ambiguous logical description run/attempt")
                run_stamps[key] = stamp
    selected = {}
    atses = {
        Path(name).parts[3]
        for name in pinned
        if name.startswith("data/facts/description_facts/")
    }
    store = facts.parent / "descriptions"
    for ats in sorted(atses):
        directories = (
            (
                facts / "description_facts" / ats,
                "data/facts/description_facts/" + ats + "/",
                "*.parquet",
            ),
            (
                facts / "description_archive" / ats,
                "data/facts/description_archive/" + ats + "/",
                "*.parquet",
            ),
            (store / ats, "data/descriptions/" + ats + "/", "*.jsonl.gz"),
        )
        for directory, prefix, pattern in directories:
            actual = {p.resolve() for p in directory.glob(pattern)}
            expected = {
                Path(item["path"]).resolve()
                for name, item in pinned.items()
                if name.startswith(prefix)
            }
            if actual != expected:
                raise ValueError(
                    f"native description inputs contain unpinned/missing files: {prefix}"
                )
        current = read_store(store / ats)
        for observation in description_facts.iter_observations(facts, ats):
            job_id = observation["id"]
            if job_id not in wanted:
                continue
            key = (observation["run_id"], observation["run_attempt"])
            logical = run_stamps.get(key, observation["observed_at"])
            if logical > tick:
                continue
            text = description_facts.read_description(
                facts, current, job_id, observation["description_hash"]
            )
            value = {
                "description": text,
                "description_hash": observation["description_hash"],
                "observed_at": logical,
                "actual_observed_at": observation["observed_at"],
            }
            prior = selected.get(job_id)
            if (
                prior
                and prior["observed_at"] == logical
                and prior["description_hash"] != value["description_hash"]
            ):
                raise ValueError("conflicting same-time description identities")
            if prior is None or logical > prior["observed_at"]:
                selected[job_id] = value
    return selected


def materialize_raw_inputs(raw, reference, paths, tick, inputs):
    """Attach independently observed bodies; unknown text is never latest-store text."""
    observed = {
        i: row
        for i, row in reference.items()
        if row.get("_source_observed_at", "") <= tick
    }
    starts = {}
    for path in paths:
        schema = pq.read_schema(path)
        metadata = schema.metadata or {}
        if metadata.get(b"source_kind") != b"historical-job-inputs":
            continue
        fingerprint = captured_fingerprint(
            json.loads(metadata.get(b"methodology", b"{}")), inputs
        )
        for batch in pq.ParquetFile(path).iter_batches(batch_size=4096):
            for row in rows_by_id(pa.Table.from_batches([batch])).values():
                at = row.get("valid_from") or metadata[b"stamp"].decode()
                if at <= tick and at >= starts.get(row["id"], ""):
                    observed[row["id"]] = row | {
                        "classifier_input_fingerprint": fingerprint
                    }
                    starts[row["id"]] = at
    native = native_description_inputs(inputs, tick, raw)
    for job_id, descriptor in native.items():
        anchor = observed.get(job_id)
        current_at = observed.get(job_id, {}).get(
            "_source_observed_at", starts.get(job_id, "")
        )
        if current_at > descriptor["observed_at"]:
            continue
        observed[job_id] = (anchor or {}) | {
            "description": descriptor["description"],
            "description_hash": descriptor["description_hash"],
            "native_description_identity": True,
            "_source_observed_at": descriptor["observed_at"],
        }
    result, unknown = {}, set()
    for job_id, row in raw.items():
        # Rejected non-tech Jobs still supply Dormancy evidence, but read no body.
        if not tech_filter.is_tech(row.get("title"), row.get("department")):
            result[job_id] = row | {k: None for k in SOURCE_COLUMNS - row.keys()}
            continue
        anchor = observed.get(job_id)
        if anchor is None:
            # Membership and fields come from the independent raw-fact reducer,
            # not a candidate or an assertion in its private placements.
            result[job_id] = (
                row
                | {k: None for k in MATH_SOURCE_COLUMNS}
                | {"title_only_recovery": True, "observed_min_years_available": False}
            )
            continue
        result[job_id] = row | {k: anchor.get(k) for k in MATH_SOURCE_COLUMNS}
        result[job_id]["observed_min_years_available"] = "min_years" in anchor
        if anchor.get("title") != row.get("title"):
            result[job_id]["title_logits"] = None
        result[job_id]["classifier_input_fingerprint"] = anchor.get(
            "classifier_input_fingerprint"
        )
        english = anchor.get("observed_english")
        if english is None and anchor.get("kind") == "present":
            english = True
        result[job_id]["observed_english"] = english
        if result[job_id]["description"] is None and english is None:
            result[job_id]["title_only_recovery"] = True
    return result, unknown


def oracle_levels(expected, source, policy, tick):
    """Production stock/new/watch definitions over independently reconstructed inputs."""
    records, families, boards, by_id = [], [], [], {}
    for job_id, (board, family, band) in expected.items():
        row = source[job_id]
        age = policy.ages[policy.groups[job_id]]
        records.append(
            {
                "id": job_id,
                "title": row.get("title"),
                "employment_type": row.get("employment_type"),
                "ats": ats_of(job_id),
                "min_years": policy.years[job_id],
                "first_seen": age,
            }
        )
        families.append(family)
        boards.append(board)
        keys = {(board, family, band)}
        keys.update(
            (board, role_taxonomy.WATCH_PREFIX + role.name, band)
            for role in policy.watchlist
            if role.matches(row.get("title"))
        )
        by_id[job_id] = keys
    schema = pa.schema(
        [
            (name, pa.int64() if name == "min_years" else pa.string())
            for name in (
                "id",
                "title",
                "employment_type",
                "ats",
                "min_years",
                "first_seen",
            )
        ]
    )
    after = (datetime.fromisoformat(tick) - timedelta(days=7)).isoformat()
    _, _, _, counts = role_trends.count_board_groups(
        pa.Table.from_pylist(records, schema=schema),
        families,
        policy.watchlist,
        after,
        boards,
    )
    return Counter(
        {
            (metric, board, family, band): n
            for (board, metric, family, band), n in counts.items()
        }
    ), by_id


def oracle_turnover(
    before,
    now,
    previous_tick,
    previous_groups,
    policy,
    previous_physical,
    context,
    tick,
):
    """Independent cause attribution; no candidate recountcause or interval flags."""
    result = Counter()
    current_groups = {policy.groups[i] for i in policy.winners}
    old_groups = set(previous_groups.values())
    for job_id in before.keys() | now.keys():
        old, new = before.get(job_id, set()), now.get(job_id, set())
        for key in old - new:
            when, cause = context.get("removed", {}).get(job_id, ("", ""))
            continuity = previous_groups.get(job_id) in current_groups
            closed = (
                job_id not in now
                and cause in {"unlisted", "dormant"}
                and previous_tick is not None
                and previous_tick < when <= tick
                and not continuity
            )
            result[("closed" if closed else "recounted_out", *key)] += 1
        for key in new - old:
            group = policy.groups[job_id]
            known_board = (
                context.get("first_reads", {}).get(key[0].casefold(), tick) < tick
            )
            listed = context.get("listed_at", {}).get(job_id)
            fresh = (
                previous_tick is not None
                and bool(listed)
                and previous_tick < listed <= tick
            )
            opened = (
                job_id not in before
                and job_id not in previous_physical
                and group not in old_groups
                and fresh
                and known_board
            )
            result[("opened" if opened else "recounted_in", *key)] += 1
    return result


def differences(expected, actual):
    wrong = [
        k for k in sorted(expected.keys() | actual.keys()) if expected[k] != actual[k]
    ]
    return {
        "count": len(wrong),
        "examples": [
            {"key": k, "expected": expected[k], "candidate": actual[k]}
            for k in wrong[:20]
        ],
    }


def oracle_boundaries(inputs):
    """Input changes inside committed window, independent of exported snapshots."""
    proofs, source_proofs, boundaries, runs = [], [], set(), {}
    for name, item in inputs.get("pinned_inputs", {}).items():
        path = Path(item["path"])
        if name.startswith(("data/facts/job_facts/", "data/facts/board_reads/")):
            metadata = pq.read_schema(path).metadata or {}
            at = metadata[b"stamp"].decode()
            proofs.append(path)
            boundaries.add(at)
            key = (
                metadata.get(b"run_id", b"").decode(),
                metadata.get(b"run_attempt", b"").decode(),
            )
            if all(key):
                if key in runs and runs[key] != at:
                    raise ValueError("ambiguous oracle boundary run/attempt")
                runs[key] = at
        elif path.suffix == ".parquet":
            schema = pq.read_schema(path)
            metadata = schema.metadata or {}
            if metadata.get(b"source_kind") == b"historical-job-inputs":
                source_proofs.append(path)
                if "valid_from" in schema.names:
                    for batch in pq.ParquetFile(path).iter_batches(
                        columns=["valid_from"]
                    ):
                        boundaries.update(
                            value or metadata[b"stamp"].decode()
                            for value in batch.column(0).to_pylist()
                        )
                else:
                    boundaries.add(metadata[b"stamp"].decode())
    facts = inputs.get("facts_root")
    if facts:
        atses = {
            Path(name).parts[3]
            for name in inputs.get("pinned_inputs", {})
            if name.startswith("data/facts/description_facts/")
        }
        for ats in atses:
            for row in description_facts.iter_observations(Path(facts), ats):
                boundaries.add(
                    runs.get((row["run_id"], row["run_attempt"]), row["observed_at"])
                )
    first, last = inputs["ticks"][0]["tick"], inputs["ticks"][-1]["tick"]
    return proofs, source_proofs, sorted(at for at in boundaries if first < at <= last)


def verify(
    inputs, base, candidate_root, policy, *, progress=None, inventory_bound=False
):
    """Return fail-closed manifest; incremental progress always remains incomplete."""
    report = {
        "complete": False,
        "pass": False,
        "rules_fingerprint": inputs["rules_fingerprint"],
        "input_revision": inputs["input_revision"],
        "last_tick": None,
        "checks": {
            "tick_coverage": False,
            "arithmetic": True,
            "per_id": True,
            "raw_universe": True,
            "source_inputs": True,
            "policy_binding": False,
            "semantic_levels": True,
            "semantic_turnover": True,
            "per_id_age": True,
        },
        "quality": {"supported_metrics": []},
        "limitations": [
            "Legacy before complete reference baseline is nonreplayable.",
        ],
        "ticks": [],
    }
    ticks = inputs["ticks"]
    stamps = [t["tick"] for t in ticks]
    if not stamps or stamps != sorted(set(stamps)) or not inputs["input_revision"]:
        raise ValueError(
            "empty, unordered or repeated tick inventory / missing revision"
        )
    diagnostics = {}
    candidate, placements, bound = candidate_ticks(
        candidate_root,
        inputs["rules_fingerprint"],
        inputs["input_revision"],
        diagnostics,
    )
    bound |= inventory_bound
    report["checks"]["policy_binding"] = bound
    report["checks"]["tick_coverage"] = (
        set(stamps) == candidate.keys() == placements.keys()
    )
    report["coverage"] = {
        "missing_ticks": sorted(set(stamps) - candidate.keys()),
        "extra_ticks": sorted(candidate.keys() - set(stamps)),
    }
    current, previous, levels, seen_reference = {}, "", Counter(), set()
    observed_reference = {}
    previous_expected, previous_groups, previous_physical, previous_tick = (
        {},
        {},
        set(),
        None,
    )
    baseline = None
    quality = Counter()
    boundary_proofs, boundary_sources, boundaries = oracle_boundaries(inputs)
    pending_moves = Counter()
    for item in ticks:
        tick = item["tick"]
        print(f"Verifying source tick {tick}", flush=True)
        # Advance before loading this export's reference: its math/text cannot
        # influence earlier intermediate input changes or incumbent selection.
        if baseline is not None:
            for boundary in boundaries:
                if not (previous_tick < boundary < tick):
                    continue
                context = {}
                raw = raw_sources(
                    boundary_proofs,
                    baseline,
                    ticks[0]["tick"],
                    boundary,
                    policy,
                    context,
                )
                source, unknown = materialize_raw_inputs(
                    raw, observed_reference, boundary_sources, boundary, inputs
                )
                if unknown:
                    raise ValueError("intermediate oracle input provenance incomplete")
                expected, boundary_quality, _ = policy.transform(source)
                quality.update(boundary_quality)
                _, by_id = oracle_levels(expected, source, policy, boundary)
                pending_moves.update(
                    oracle_turnover(
                        previous_expected,
                        by_id,
                        previous_tick,
                        previous_groups,
                        policy,
                        previous_physical,
                        context,
                        boundary,
                    )
                )
                previous_expected = by_id
                previous_groups = {i: policy.groups[i] for i in policy.winners}
                previous_physical = context.get("physical_ids", set(source))
                previous_tick = boundary
                quality["intermediate_oracle_boundaries"] += 1
        reference = pq.read_table(evidence(item["reference"], base, candidate_root))
        metadata = reference.schema.metadata or {}
        reference_tick = metadata.get(b"ts", b"").decode()
        if (
            reference_tick != item["reference_tick"]
            or metadata.get(b"previous_tick", b"").decode() != previous
        ):
            raise ValueError("reference tick mapping / parent chain mismatch")
        if (metadata.get(b"baseline") == b"true") != (not previous):
            raise ValueError("reference must begin at complete baseline")
        method = json.loads(metadata.get(b"methodology", b"{}"))
        fingerprint = captured_fingerprint(method, inputs)
        for job_id, row in rows_by_id(reference).items():
            if row["kind"] == "removed":
                if job_id not in current:
                    raise ValueError("removal of unknown reference id")
                del current[job_id]
            elif row["kind"] == "present":
                current[job_id] = row | {
                    "classifier_input_fingerprint": fingerprint,
                    "_source_observed_at": tick,
                }
                observed_reference[job_id] = current[job_id]
                seen_reference.add(job_id)
            else:
                raise ValueError("unknown reference kind")
        previous = reference_tick
        if baseline is None:
            baseline = dict(current)
            # The reference baseline is independent evidence of which copies were
            # incumbent, not a ranking contest among every historical raw copy.
            policy.winners = set(baseline)
        source, source_metadata = current, metadata
        full = "universe" in item
        unknown_source_ids = set()
        context = {}
        if full:
            universe = pq.read_table(evidence(item["universe"], base, candidate_root))
            source_metadata = universe.schema.metadata or {}
            required = {
                b"source_kind": b"raw-facts-served-inputs",
                b"complete": b"true",
                b"ts": tick.encode(),
                b"input_revision": inputs["input_revision"].encode(),
                b"rules_fingerprint": inputs["rules_fingerprint"].encode(),
            }
            if any(source_metadata.get(k) != v for k, v in required.items()):
                raise ValueError("independent raw universe provenance mismatch")
            provenance = json.loads(source_metadata.get(b"source_files", b"[]"))
            kinds, proofs, source_proofs = set(), [], []
            for proof in provenance:
                pinned = inputs.get("pinned_inputs")
                bound_proof = proof
                if pinned is not None:
                    matches = [
                        p
                        for name, p in pinned.items()
                        if proof["path"] == name or proof["path"] == p["path"]
                    ]
                    if len(matches) != 1 or matches[0]["sha256"] != proof["sha256"]:
                        raise ValueError("raw oracle dependency is not a pinned input")
                    bound_proof = matches[0]
                raw = evidence(bound_proof, base, candidate_root)
                source_proofs.append(raw)
                if raw.suffix == ".parquet":
                    schema = pq.read_schema(raw)
                    if not (schema.metadata or {}).get(b"stamp"):
                        raise ValueError("unstamped raw-facts evidence")
                    if (schema.metadata or {}).get(
                        b"source_kind"
                    ) == b"historical-job-inputs":
                        continue
                    proofs.append(raw)
                    if {"id", "kind", "title", "department"} <= set(schema.names):
                        kinds.add("job-facts")
                    if {"board", "in_scope", "outcome"} <= set(schema.names):
                        kinds.add("board-reads")
            if not {"job-facts", "board-reads"} <= kinds:
                raise ValueError("raw universe lacks independent fact/read evidence")
            if inputs.get("pinned_inputs") is not None:
                required_proofs = set()
                for name, item in inputs["pinned_inputs"].items():
                    if name.startswith(
                        ("data/facts/job_facts/", "data/facts/board_reads/")
                    ):
                        path = Path(item["path"])
                        if (pq.read_schema(path).metadata or {})[
                            b"stamp"
                        ].decode() <= tick:
                            required_proofs.add(path.resolve())
                if required_proofs != {p.resolve() for p in proofs}:
                    raise ValueError(
                        "raw oracle dependency inventory omits selected facts/reads"
                    )
            source = rows_by_id(universe)
            check_raw_truth(
                source, proofs, baseline, ticks[0]["tick"], tick, policy, context
            )
            unknown_inputs = check_source_inputs(
                source, current, source_proofs, tick, inputs
            )
            report["checks"]["source_inputs"] &= unknown_inputs == 0
            quality["unverified_historical_source_inputs"] += unknown_inputs
        elif inputs.get("pinned_inputs"):
            proofs, source_proofs = [], []
            for name, item in inputs["pinned_inputs"].items():
                path = Path(item["path"])
                if name.startswith(
                    ("data/facts/job_facts/", "data/facts/board_reads/")
                ):
                    proofs.append(path)
                elif (
                    path.suffix == ".parquet"
                    and (pq.read_schema(path).metadata or {}).get(b"source_kind")
                    == b"historical-job-inputs"
                ):
                    source_proofs.append(path)
            # Both event streams are necessary; an empty change file is evidence.
            if any("/job_facts/" in str(p) for p in proofs) and any(
                "/board_reads/" in str(p) for p in proofs
            ):
                raw = raw_sources(
                    proofs, baseline, ticks[0]["tick"], tick, policy, context
                )
                source, unknown_source_ids = materialize_raw_inputs(
                    raw, observed_reference, source_proofs, tick, inputs
                )
                full = True
                source_metadata = {}
                report["checks"]["source_inputs"] &= not unknown_source_ids
                quality["unverified_historical_source_inputs"] += len(
                    unknown_source_ids
                )
        if not SOURCE_COLUMNS <= set(reference.column_names) or any(
            not SOURCE_COLUMNS <= r.keys() for r in source.values()
        ):
            raise ValueError(
                "narrow reference lacks historical source inputs; coverage only"
            )
        method = json.loads(source_metadata.get(b"methodology", b"{}"))
        expected, tick_quality, reasons = policy.transform(
            source, method.get("classifier_input_fingerprint")
        )
        quality.update(tick_quality)
        actual = placements.get(tick, {})
        age_wrong = [
            i
            for i, row in diagnostics.get(tick, {}).items()
            if i in expected
            and "first_seen" in row
            and row["first_seen"] != source[i].get("first_seen")
        ]
        report["checks"]["per_id_age"] &= not age_wrong
        quality["unknown_historical_ages"] += sum(
            policy.ages[policy.groups[i]] is None for i in expected
        )
        unknown = actual.keys() - source.keys()
        # Reference-only evidence cannot prove additions on newly covered Boards.
        # Full independent universes CAN, and missing/extra ids then fail exactly.
        compared = (
            {i: p for i, p in actual.items() if i not in unknown_source_ids}
            if full
            else {i: p for i, p in actual.items() if i in seen_reference}
        )
        wrong = [
            i
            for i in sorted(expected.keys() | compared.keys())
            if expected.get(i) != compared.get(i)
        ]
        arithmetic = (
            check_tick(candidate[tick], levels, actual)
            if tick in candidate
            else ["missing_tick"]
        )
        report["checks"]["arithmetic"] &= not arithmetic
        report["checks"]["per_id"] &= not wrong
        report["checks"]["raw_universe"] &= full
        level_diff, turnover_diff = None, None
        if full and not unknown_source_ids:
            expected_levels, by_id = oracle_levels(expected, source, policy, tick)
            actual_levels = Counter(
                {
                    k: n
                    for k, n in levels.items()
                    if k[2] != role_taxonomy.NON_TECH and n
                }
            )
            level_diff = differences(expected_levels, actual_levels)
            expected_moves = oracle_turnover(
                previous_expected,
                by_id,
                previous_tick,
                previous_groups,
                policy,
                previous_physical,
                context,
                tick,
            )
            expected_moves.update(pending_moves)
            actual_moves = Counter()
            for row in candidate[tick].to_pylist() if tick in candidate else ():
                if (
                    row["metric"] in TURNOVER
                    and row["family"] != role_taxonomy.NON_TECH
                ):
                    actual_moves[
                        (row["metric"], row["board"], row["family"], row["band"])
                    ] += row["delta"]
            turnover_diff = differences(expected_moves, actual_moves)
            report["checks"]["semantic_levels"] &= level_diff["count"] == 0
            report["checks"]["semantic_turnover"] &= turnover_diff["count"] == 0
            previous_expected = by_id
            pending_moves.clear()
        else:
            report["checks"]["semantic_levels"] = False
            report["checks"]["semantic_turnover"] = False
        previous_groups = {i: policy.groups[i] for i in policy.winners}
        previous_physical = context.get("physical_ids", set(source))
        previous_tick = tick
        report["ticks"].append(
            {
                "tick": tick,
                "reference_tick": reference_tick,
                "scope": "raw-universe" if full else "reference-overlap",
                "expected": len(expected),
                "candidate": len(actual),
                "expected_coverage_additions": len(expected.keys() - current.keys())
                if full
                else 0,
                "unverified_outside_reference": len(unknown) if not full else 0,
                "unverified_raw_source_ids": len(unknown_source_ids),
                "unverified_source_examples": sorted(unknown_source_ids)[:20],
                "placement_differences": len(wrong),
                "age_differences": {
                    "count": len(age_wrong),
                    "examples": age_wrong[:20],
                },
                "arithmetic_errors": arithmetic,
                "level_differences": level_diff,
                "turnover_differences": turnover_diff,
                "attributions": dict(reasons),
                "examples": [
                    {"id": i, "expected": expected.get(i), "candidate": compared.get(i)}
                    for i in wrong[:20]
                ],
            }
        )
        report["last_tick"] = tick
        report["quality"] = {"supported_metrics": [], "input_counts": dict(quality)}
        if progress:
            progress(report)
        print(
            f"{tick}: {len(expected)} expected; {len(wrong)} differences; raw universe {full}",
            flush=True,
        )
    if not report["checks"]["raw_universe"]:
        report["limitations"].append(
            "Reference snapshots cannot prove raw-policy additions or grace/Dormancy replay."
        )
    else:
        report["limitations"].append(
            "Raw source membership/fields are checked against fact/read events; immutable description and math-input provenance remains a source-store dependency."
        )
    if quality["float16_classifier_approximation"]:
        report["limitations"].append(
            "Historical float16 vectors yield approximate changed-head classification; no mismatches waived."
        )
    if quality["title_only_without_vector"]:
        report["limitations"].append(
            "Sources with no preserved vector use title-only classification, explicitly counted in quality."
        )
    if quality["unverified_historical_source_inputs"]:
        report["limitations"].append(
            "Some broader-policy sources lack independently preserved historical text/math inputs; publication is incomplete."
        )
    if (
        quality["observed_english_without_text"]
        or quality["observed_min_years_without_text"]
    ):
        report["limitations"].append(
            "Missing original text retains observed eligibility/experience; historical text replay is partial."
        )
    report["complete"] = (
        report["checks"]["tick_coverage"]
        and report["checks"]["raw_universe"]
        and bound
        and report["checks"]["source_inputs"]
    )
    report["pass"] = all(report["checks"].values())
    if report["complete"] and report["pass"]:
        report["quality"]["supported_metrics"] = [
            "stock",
            "new",
            "turnover",
            "watched_roles",
        ]
        report["quality"]["watched_roles"] = True
    return report


def inventory_path(item, facts, state):
    """Dataset-relative pinned inputs; arbitrary local paths are not evidence bindings."""
    path = Path(item["path"])
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("unsafe input inventory path")
    for prefix, root in (
        (Path("data/facts"), facts),
        (Path("data/state"), state),
        (Path("data/descriptions"), facts.parent / "descriptions"),
    ):
        if path.is_relative_to(prefix):
            resolved = root / path.relative_to(prefix)
            if not resolved.resolve().is_relative_to(root.resolve()):
                raise ValueError("input inventory symlink escapes selected root")
            return resolved
    raise ValueError(f"unsupported pinned input path: {path}")


def check_inventory(items, resolve):
    names = [r["path"] for r in items]
    if names != sorted(set(names)) or not names:
        raise ValueError("inventory must be nonempty, sorted and unique")
    for item in items:
        path = resolve(item)
        if path.stat().st_size != item["size"] or sha256(path) != item["sha256"]:
            raise ValueError(f"inventory content mismatch: {item['path']}")


def artifact_path(root, item):
    path = Path(item["path"])
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("unsafe artifact inventory path")
    allowed = path.as_posix() in {
        "company_directory.json",
        "config/role_families.json",
        "config/role_watchlist.json",
    }
    allowed |= path.parent == Path(DELTAS) and path.suffix == ".parquet"
    if not allowed:
        raise ValueError(f"unsupported publication artifact: {path}")
    return root / path


def publication_bindings(metadata, facts, state, candidate):
    check_inventory(metadata["inputs"], lambda r: inventory_path(r, facts, state))
    actual_files = [
        {
            "path": path.relative_to(candidate).as_posix(),
            "size": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in sorted(candidate.rglob("*"))
        if path.is_file()
        and (
            path.parent == candidate / DELTAS
            or path.relative_to(candidate).as_posix()
            in {
                "company_directory.json",
                "config/role_families.json",
                "config/role_watchlist.json",
            }
        )
    ]
    if "files" not in metadata:
        metadata["files"] = actual_files
    elif metadata["files"] != actual_files:
        raise ValueError("replay artifact inventory differs from prepared candidate")
    check_inventory(metadata["files"], lambda r: artifact_path(candidate, r))
    paths = {r["path"] for r in metadata["files"]}
    required = {
        "company_directory.json",
        "config/role_families.json",
        "config/role_watchlist.json",
    }
    actual = {f"{DELTAS}/{p.name}" for p in (candidate / DELTAS).glob("*.parquet")}
    if paths != actual | required:
        raise ValueError(
            "publication inventory omits ticks/config/company labels or contains extras"
        )
    directory = json.loads((candidate / "company_directory.json").read_text())
    if not isinstance(directory, dict):
        raise TypeError("invalid pinned Company directory")
    boards = set()
    for path in (candidate / DELTAS).glob("*.parquet"):
        table = pq.read_table(path, columns=["board", "family"])
        boards.update(
            r["board"]
            for r in table.to_pylist()
            if r["family"] != role_taxonomy.NON_TECH
        )
    named = []
    for entry in directory["companies"]:
        if not isinstance(entry.get("name"), str) or not entry["name"].strip():
            raise ValueError("Company directory lacks a name")
        named.extend(entry["boards"])
    if len(named) != len(set(named)) or not set(named) <= boards:
        raise ValueError(
            "Company labels repeat or name Boards outside candidate history"
        )
    return {
        "history_boards": len(boards),
        "named_boards": len(named),
        "unnamed_boards": len(boards - set(named)),
    }


def verification_inputs(metadata, facts, state):
    """Bind reference evidence to pinned inventory and exact shared run identities."""
    pinned = {r["path"]: r for r in metadata["inputs"]}
    references = []
    runs, raw_ticks = {}, set()
    for name, item in pinned.items():
        if not name.endswith(".parquet"):
            continue
        if name.startswith("data/facts/trend_reference/"):
            references.append(
                (pq.read_schema(inventory_path(item, facts, state)).metadata, item)
            )
        if name.startswith(("data/facts/job_facts/", "data/facts/board_reads/")):
            attributes = (
                pq.read_schema(inventory_path(item, facts, state)).metadata or {}
            )
            run = (
                attributes.get(b"run_id", b"").decode(),
                attributes.get(b"run_attempt", b"").decode(),
            )
            stamp = attributes[b"stamp"].decode()
            raw_ticks.add(stamp)
            if all(run):
                if run in runs and runs[run] != stamp:
                    raise ValueError("ambiguous repeated run identity")
                runs[run] = stamp
    checkpoint = pinned.get("data/state/reference_state.parquet")
    if checkpoint is None:
        raise ValueError("no pinned committed reference checkpoint")
    by_stamp = {}
    for attributes, item in references:
        stamp = attributes[b"ts"].decode()
        if stamp in by_stamp:
            raise ValueError("repeated reference stamp")
        by_stamp[stamp] = (attributes, item)
    cursor = (pq.read_schema(inventory_path(checkpoint, facts, state)).metadata or {})[
        b"ts"
    ].decode()
    chain, visited = [], set()
    while cursor:
        if cursor in visited or cursor not in by_stamp:
            raise ValueError("broken committed reference chain")
        visited.add(cursor)
        attributes, item = by_stamp[cursor]
        chain.append((attributes, item))
        cursor = attributes.get(b"previous_tick", b"").decode()
    references = list(reversed(chain))
    if not references or references[0][0].get(b"baseline") != b"true":
        raise ValueError("committed chain lacks complete baseline")
    expected_mapping = []
    for attributes, item in references:
        reference_tick = attributes[b"ts"].decode()
        if attributes.get(b"baseline") == b"true":
            logical = reference_tick
        else:
            key = (
                attributes.get(b"run_id", b"").decode(),
                attributes.get(b"run_attempt", b"").decode(),
            )
            if not all(key) or key not in runs:
                raise ValueError("committed reference lacks exact run/attempt mapping")
            logical = runs[key]
        expected_mapping.append((logical, reference_tick, item["path"]))
    explicit = metadata.get("verification_ticks")
    if explicit is None:
        explicit = []
        for attributes, item in sorted(references, key=lambda pair: pair[0][b"ts"]):
            reference_tick = attributes[b"ts"].decode()
            if attributes.get(b"baseline") == b"true":
                tick = reference_tick
            else:
                run = (
                    attributes.get(b"run_id", b"").decode(),
                    attributes.get(b"run_attempt", b"").decode(),
                )
                if run not in runs:
                    raise ValueError(
                        "exact reference/run mapping unavailable; supply verification_ticks"
                    )
                tick = runs[run]
            explicit.append(
                {
                    "tick": tick,
                    "reference_tick": reference_tick,
                    "reference": {"path": item["path"], "sha256": item["sha256"]},
                }
            )
    ticks = []
    chain_paths = [item["path"] for _, item in references]
    if [entry["reference"]["path"] for entry in explicit] != chain_paths:
        raise ValueError("verification mapping differs from committed reference chain")
    if [
        (entry["tick"], entry["reference_tick"], entry["reference"]["path"])
        for entry in explicit
    ] != expected_mapping:
        raise ValueError(
            "verification mapping differs from independent run/attempt join"
        )
    for entry in explicit:
        result = dict(entry)
        for key in ("reference", "universe"):
            if key not in entry:
                continue
            item = entry[key]
            if (
                item["path"] not in pinned
                or pinned[item["path"]]["sha256"] != item["sha256"]
            ):
                raise ValueError(
                    "oracle evidence is not in selected pinned input inventory"
                )
            result[key] = {
                "path": str(
                    inventory_path(pinned[item["path"]], facts, state).resolve()
                ),
                "sha256": item["sha256"],
            }
        ticks.append(result)
    covered_raw = {
        stamp for stamp in raw_ticks if ticks[0]["tick"] <= stamp <= ticks[-1]["tick"]
    } | {ticks[0]["tick"]}
    return {
        "rules_fingerprint": metadata["rules_fingerprint"],
        "input_revision": metadata["input_revision"],
        "facts_root": str(facts.resolve()),
        "unexported_raw_ticks": sorted(
            covered_raw - {entry["tick"] for entry in ticks}
        ),
        "ticks": ticks,
        "pinned_inputs": {
            name: {
                "path": str(inventory_path(item, facts, state).resolve()),
                "sha256": item["sha256"],
            }
            for name, item in pinned.items()
        },
    }


def reference_anchor(inputs):
    item = inputs["pinned_inputs"].get("data/state/reference_state.parquet")
    if item is None:
        raise ValueError("no pinned committed reference checkpoint")
    metadata = pq.read_schema(item["path"]).metadata or {}
    if metadata.get(b"ts", b"").decode() != inputs["ticks"][-1]["reference_tick"]:
        raise ValueError("reference window does not end at its committed checkpoint")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facts", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--rules-root", type=Path, default=Path.cwd())
    parser.add_argument("--title-cache", type=Path)
    parser.add_argument("--encode-budget-seconds", type=float, default=1800)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    metadata = {}
    args.report.parent.mkdir(parents=True, exist_ok=True)

    def save(report):
        staged = args.report.with_suffix(".json.tmp")
        staged.write_text(json.dumps(report, indent=2) + "\n")
        staged.replace(args.report)

    # A failed/incomplete rerun must never leave a previous passing gate behind.
    save(
        {
            "schema_version": 1,
            "complete": False,
            "pass": False,
            "rules_fingerprint": metadata.get("rules_fingerprint"),
            "input_revision": metadata.get("input_revision"),
            "first_covered_tick": metadata.get("first_covered_tick"),
            "last_covered_tick": metadata.get("last_covered_tick"),
            "files": metadata.get("files", []),
            "inputs": metadata.get("inputs", []),
            "verifier": {
                "name": "verify_restatement",
                "code_sha": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"],
                    cwd=Path(__file__).resolve().parents[2],
                    text=True,
                ).strip(),
            },
            "checks": {},
            "quality": {"supported_metrics": []},
            "limitations": ["Validation has not completed."],
        }
    )
    header = json.loads(args.report.read_text())

    def progress(report):
        save(header | report)

    try:
        metadata = json.loads(args.metadata.read_text())
        if metadata.get("schema_version", 1) != 1:
            raise ValueError("unsupported replay metadata version")
        for key in ("rules_fingerprint", "input_revision"):
            if not isinstance(metadata.get(key), str) or not metadata[key]:
                raise ValueError(f"missing/non-string replay {key}")
        for key in (
            "rules_fingerprint",
            "input_revision",
            "first_covered_tick",
            "last_covered_tick",
            "inputs",
        ):
            header[key] = metadata[key]
        save(header)
        labels = publication_bindings(metadata, args.facts, args.state, args.candidate)
        if (args.state / "board_failures.csv").exists() and not any(
            item["path"] == "data/state/board_failures.csv"
            for item in metadata["inputs"]
        ):
            raise ValueError("current Board-failure scope is not a pinned input")
        header["files"] = metadata["files"]
        save(header)
        inputs = verification_inputs(metadata, args.facts, args.state)
        reference_anchor(inputs)
        if rules_fingerprint(args.rules_root) != inputs["rules_fingerprint"]:
            raise ValueError("current frozen rules fingerprint mismatch")
        for name in ("role_families.json", "role_watchlist.json"):
            if sha256(args.candidate / "config" / name) != sha256(
                args.rules_root / "config" / name
            ):
                raise ValueError("packaged taxonomy differs from frozen policy")
        policy = Policy(
            args.rules_root,
            args.title_cache or args.state / "role_title_families.parquet",
            args.state,
            args.encode_budget_seconds,
        )
        report = verify(
            inputs,
            Path("/"),
            args.candidate,
            policy,
            progress=progress,
            inventory_bound=True,
        )
        report = header | report
        report["quality"]["company_labels"] = labels
        report["quality"]["stock_scope"] = "tech counts per Board/family/band"
        report["limitations"].append(
            "Company labels are checked for unique history membership; label names/grouping are not independently recomputed."
        )
        if (report["ticks"][0]["tick"], report["last_tick"]) != (
            metadata["first_covered_tick"],
            metadata["last_covered_tick"],
        ):
            raise ValueError("metadata coverage bounds mismatch")
        if rules_fingerprint(args.rules_root) != metadata["rules_fingerprint"]:
            raise ValueError("frozen policy changed during verification")
    except (
        ValueError,
        TypeError,
        KeyError,
        OSError,
        SyntaxError,
        zipfile.BadZipFile,
    ) as exc:
        report = json.loads(args.report.read_text())
        report.update({"complete": False, "pass": False, "error": str(exc)})
        save(report)
        print(f"VALIDATION INCOMPLETE: {exc}", flush=True)
        return 1
    save(report)
    return int(not (report["complete"] and report["pass"]))


if __name__ == "__main__":
    raise SystemExit(main())
