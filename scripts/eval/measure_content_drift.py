#!/usr/bin/env python3
"""Compare a bounded live sample with the freshest pinned HF description store.

Measures description differences, not an employer-edit rate: parser/normalization changes
can differ too. Only inexpensive, inline-description ATSes are sampled. No embedding or
production writes; reference text stays in the gitignored experiment artifacts directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download

from headstart import scrapable_boards
from headstart.board_identity import board_key
from headstart.ingest.update_descriptions import read_store
from headstart.scrapers.registry import get_scraper

ROOT = Path(__file__).resolve().parents[2]
ATS_CHOICES = ("lever", "ashby", "greenhouse")


def fingerprint(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode()).hexdigest()


def compare(reference: dict[str, str], current: dict[str, str | None]) -> list[dict]:
    rows = []
    for job_id, before in reference.items():
        after = current.get(job_id)
        status = (
            "not_returned"
            if job_id not in current
            else "no_current_description"
            if not after or not after.strip()
            else "unchanged"
            if fingerprint(before) == fingerprint(after)
            else "different"
        )
        rows.append(
            {
                "id": job_id,
                "status": status,
                "before_hash": fingerprint(before),
                "after_hash": fingerprint(after) if after else None,
                "before_chars": len(before),
                "after_chars": len(after) if after else 0,
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ats", choices=ATS_CHOICES, default="lever")
    parser.add_argument("--boards", type=int, default=3)
    parser.add_argument("--jobs-per-board", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20260912)
    parser.add_argument("--max-download-mb", type=float, default=64)
    args = parser.parse_args()
    if args.boards < 1 or args.jobs_per_board < 1 or args.max_download_mb <= 0:
        parser.error("sample sizes and download budget must be positive")
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S.%fZ")
    out = ROOT / "experiment" / "critique-content-drift" / "artifacts" / stamp
    # Refuse to put the private reference corpus anywhere Git could accidentally publish it.
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", str(out / "reference" / "sample.gz")],
        cwd=ROOT,
        check=False,
    )
    if ignored.returncode:
        parser.error("reference artifact directory must be gitignored")
    out.mkdir(parents=True)
    repo = "imPoseidon/headstart-index"
    info = HfApi().repo_info(repo, repo_type="dataset", files_metadata=True)
    prefix = f"data/descriptions/{args.ats}/"
    files = [f for f in info.siblings if f.rfilename.startswith(prefix)]
    if any(f.size is None for f in files):
        parser.error("reference sizes are unknown; cannot enforce the download budget")
    size = sum(f.size or 0 for f in files)
    if not files or size > args.max_download_mb * 1024**2:
        parser.error(
            f"reference needs {size / 1024**2:.1f} MiB in {len(files)} files; download budget {args.max_download_mb:g} MiB"
        )
    print(
        f"reference {info.sha}: {len(files)} files, {size / 1024**2:.1f} MiB",
        flush=True,
    )
    cache = out / "reference"
    snapshot_download(
        repo,
        repo_type="dataset",
        revision=info.sha,
        local_dir=cache,
        allow_patterns=[f.rfilename for f in files],
    )
    held = read_store(cache / prefix)
    by_board: dict[str, dict[str, str]] = defaultdict(dict)
    # These three ATSes use a bare Board slug followed by a numeric/UUID native Job id.
    for job_id, text in held.items():
        ats, slug, _ = job_id.split(":", 2)
        by_board[f"{ats}:{slug}".lower()][job_id] = text
    companies = [
        c
        for c in scrapable_boards.load(ROOT / "data/validate/liveness", min_jobs=0)
        if c.ats == args.ats and board_key(c).lower() in by_board
    ]
    companies.sort(key=board_key)
    rng = random.Random(args.seed)
    picked = rng.sample(companies, min(args.boards, len(companies)))
    samples = {}
    for company in picked:
        board = board_key(company)
        ids = sorted(by_board[board.lower()])
        samples[board] = {
            i: held[i] for i in rng.sample(ids, min(args.jobs_per_board, len(ids)))
        }
    metadata = {
        "reference_repo": repo,
        "reference_revision": info.sha,
        "measured_at": stamp,
        "ats": args.ats,
        "seed": args.seed,
        "sample_ids": {board: list(rows) for board, rows in samples.items()},
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "source_dirty": bool(
            subprocess.check_output(
                ["git", "status", "--porcelain", "--", "src/headstart"],
                cwd=ROOT,
                text=True,
            ).strip()
        ),
    }
    (out / "reference-provenance.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )

    def probe(company):
        board = board_key(company)
        started = time.monotonic()
        scraper = get_scraper(company.ats, company.slug, company.name)
        jobs = scraper.fetch()
        rows = compare(samples[board], {job.id: job.description for job in jobs})
        return {
            "board": board,
            "seconds": round(time.monotonic() - started, 2),
            "returned_jobs": len(jobs),
            "truncated": scraper.truncated,
            "rows": rows,
        }

    counts: Counter[str] = Counter()
    with (
        (out / "board-comparisons.jsonl").open("w", encoding="utf-8") as target,
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        futures = {
            pool.submit(probe, company): board_key(company) for company in picked
        }
        for future in as_completed(futures):
            try:
                result = future.result()
                counts.update(row["status"] for row in result["rows"])
            except Exception as exc:  # noqa: BLE001 — surface each failed probe separately
                result = {
                    "board": futures[future],
                    "error": f"{type(exc).__name__}: {exc}",
                }
                counts["board_errors"] += 1
            target.write(json.dumps(result) + "\n")
            target.flush()
            print(
                f"{result['board']}: "
                + (
                    result.get("error")
                    or str(Counter(row["status"] for row in result["rows"]))
                ),
                flush=True,
            )
    summary = {
        **metadata,
        "counts": dict(counts),
        "output": str(out),
        "limits": "Description differences include parser changes. Not returned is not confirmed closed; truncated/error probes are not negative evidence. Sampled ATSes have inline descriptions; no full-ATS or organic-edit-rate claim.",
    }
    (out / "measurement-summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"counts": dict(counts), "output": str(out)}), flush=True)
    return 0 if counts["unchanged"] + counts["different"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
