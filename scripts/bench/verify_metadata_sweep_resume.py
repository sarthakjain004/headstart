"""Replay a held-description sample through a budgeted sweep and its successor.

Run with PYTHONPATH=src using an environment containing HeadStart dependencies.
Pass a downloaded description-store .jsonl.gz; no network or production writes.
"""

from __future__ import annotations

import argparse
import gzip
import json
import tempfile
from pathlib import Path
from time import monotonic

from headstart.ingest import update_meta as um


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sample", type=Path)
    args = parser.parse_args()
    samples = []
    with gzip.open(args.sample, "rt") as handle:
        for line in handle:
            item = json.loads(line)
            if item.get("description"):
                samples.append(item["description"])
            if len(samples) == 1000:
                break
    assert samples, "sample contains no descriptions"
    um.os.cpu_count = lambda: 2
    with tempfile.TemporaryDirectory(prefix="headstart-sweep-replay-") as directory:
        root = Path(directory)
        store = root / "store"
        store.mkdir()
        desc = root / "descriptions" / "workday"
        desc.mkdir(parents=True)
        rows = []
        texts = {}
        for i in range(5000):
            row = {
                "id": f"workday:replay:{i}",
                "ats": "workday",
                "title": "Engineer",
                "has_description": True,
                "min_years": 99,
            }
            rows.append(row)
            texts[row["id"]] = samples[i % len(samples)]
        path = store / "meta.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        with gzip.open(desc / "base.jsonl.gz", "wt") as handle:
            for job_id, text in texts.items():
                handle.write(json.dumps({"id": job_id, "description": text}) + "\n")
        watermark = root / "watermark.json"
        um.write_watermark(watermark, um.DERIVATIONS_VERSION - 1)
        for budget in (0.1, 600):
            start = monotonic()
            um.refresh(
                store,
                root / "no-corpus",
                desc.parent,
                watermark,
                sweep_budget_seconds=budget,
            )
            actual = [json.loads(line) for line in path.read_text().splitlines()]
            completed = sum(
                row.get("_derivations_version") == um.DERIVATIONS_VERSION
                for row in actual
            )
            print(
                json.dumps(
                    {
                        "budget_seconds": budget,
                        "elapsed_seconds": round(monotonic() - start, 3),
                        "rows": len(actual),
                        "completed": completed,
                        "watermark": um.read_watermark(watermark),
                    }
                ),
                flush=True,
            )
            if budget == 0.1:
                assert 0 < completed < len(rows)
                assert um.read_watermark(watermark) == um.DERIVATIONS_VERSION - 1
        assert um.read_watermark(watermark) == um.DERIVATIONS_VERSION
        for before, after in zip(rows, actual, strict=True):
            expected = um.refresh_row(before, None, texts, True)[0]
            expected["_derivations_version"] = um.DERIVATIONS_VERSION
            assert after == expected, before["id"]
        print(
            "PASS: all 5000 rows match an uninterrupted cascade in exact vector order",
            flush=True,
        )


if __name__ == "__main__":
    main()
