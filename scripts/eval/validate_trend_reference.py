"""Check committed reference coverage, every placement, and live count reconciliation.

This certifies reproduction of observed inputs, not raw-scrape admission replay.
Rule-changing restatement is a separate comparison. Any missing evidence fails.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import zipfile
from collections import Counter
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from headstart.ingest import role_family_classifier as rfc
from headstart.ingest import trend_reference
from headstart.ingest.index_plan import boards_by_canon, live_keep_set, resolve_board
from headstart.trends import role_taxonomy
from headstart.trends.trend_history import DELTAS


def decide(rows, manifest, live):
    """Recompute placements from preserved model inputs, without reference labels."""
    result = {}
    for job_id, row in rows.items():
        if row["row_logits"] is None:
            raise ValueError(f"missing classifier inputs for {job_id}")
        if row["title_logits"] is None:
            family = rfc.UNCLASSIFIED
        else:
            probability = rfc.softmax(
                np.asarray([row["title_logits"]], np.float32)
                + np.asarray([row["row_logits"]], np.float32)
            )
            family = rfc.choose_families(
                probability, manifest["families"], manifest["cutoff"]
            )[0][0]
            if family == rfc.UNCLASSIFIED and rfc.names_a_software_developer(
                row["title"]
            ):
                family = rfc.SOFTWARE_ENGINEERING
        if family != role_taxonomy.NON_TECH:
            result[job_id] = (
                resolve_board(job_id, live),
                family,
                role_taxonomy.band(
                    row["min_years"], row.get("title"), row.get("employment_type")
                ),
            )
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--facts", type=Path, default=Path("data/facts"))
    ap.add_argument("--state", type=Path, default=Path("data/state"))
    ap.add_argument(
        "--report", type=Path, default=Path("data/reference-validation.json")
    )
    args = ap.parse_args()
    live = {}
    levels = Counter()
    for path in sorted((args.state / DELTAS).glob("*.parquet")):
        table = pq.read_table(path)
        stamp = table.schema.metadata[b"ts"].decode()
        for row in table.to_pylist():
            if (
                row["metric"] == "stock"
                and row["family"] != role_taxonomy.NON_TECH
                and not row["family"].startswith("watch:")
            ):
                levels[(row["board"], row["family"], row["band"])] += row["delta"]
        live[stamp] = +levels
    results = []
    failures = 0
    args.report.parent.mkdir(parents=True, exist_ok=True)
    scope = "observed tech stock placements only"
    args.report.write_text(json.dumps({"scope": scope, "complete": False, "ticks": []}))
    for metadata, rows in trend_reference.checkpoints(
        args.facts, live, state_dir=args.state
    ):
        stamp = metadata[b"ts"].decode()
        method = json.loads(metadata[b"methodology"])
        archive = args.facts / "reference_rules" / f"{method['rules_fingerprint']}.zip"
        with zipfile.ZipFile(archive) as z:
            # Refuse to interpret historical rule inputs with a different classifier/band implementation.
            required = [
                "src/headstart/ingest/role_family_classifier.py",
                "src/headstart/trends/role_taxonomy.py",
                "src/headstart/ingest/index_plan.py",
                "src/headstart/scrapers/registry.py",
            ]
            required += [
                name
                for name in z.namelist()
                if name.startswith(("src/headstart/boards/", "src/headstart/scrapers/"))
                and name.endswith(".py")
            ]
            for name in required:
                if z.read(name) != Path(name).read_bytes():
                    raise ValueError(f"frozen rule differs from validator: {name}")
            manifest = json.loads(z.read("config/role_family_classifier/manifest.json"))
            with tempfile.TemporaryDirectory(prefix="trend-reference-ledgers-") as tmp:
                for name in z.namelist():
                    if name.startswith("data/validate/"):
                        if ".." in Path(name).parts:
                            raise ValueError("unsafe rule archive member")
                        z.extract(name, tmp)
                live_boards = boards_by_canon(
                    live_keep_set(Path(tmp) / "data/validate/liveness")
                )
        candidate = decide(rows, manifest, live_boards)
        expected = {
            i: (r["reference_board"], r["reference_family"], r["reference_band"])
            for i, r in rows.items()
            if r["reference_family"] is not None
        }
        wrong = [
            i
            for i in sorted(candidate.keys() | expected.keys())
            if candidate.get(i) != expected.get(i)
        ]
        counted = Counter(candidate.values())
        group_wrong = [
            k
            for k in sorted(counted.keys() | live[stamp].keys())
            if counted[k] != live[stamp][k]
        ]
        failures += bool(wrong or group_wrong)
        results.append(
            {
                "tick": stamp,
                "run_id": metadata[b"run_id"].decode(),
                "placement_differences": wrong,
                "group_differences": [
                    {"key": k, "candidate": counted[k], "live": live[stamp][k]}
                    for k in group_wrong
                ],
            }
        )
        args.report.write_text(
            json.dumps({"scope": scope, "complete": False, "ticks": results}, indent=2)
        )
        print(
            f"{stamp}: {len(candidate)} tech ids; {len(wrong)} placement differences; {len(group_wrong)} count differences",
            flush=True,
        )
    args.report.write_text(
        json.dumps({"scope": scope, "complete": True, "ticks": results}, indent=2)
    )
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
