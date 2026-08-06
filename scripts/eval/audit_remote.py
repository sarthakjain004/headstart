#!/usr/bin/env python3
"""Audit the ``remote`` flag of one ATS scraper against live-scraped boards.

Scrapers set ``Job.remote`` three different ways (location-string ``is_remote``, an ATS-native
flag, or both), so this samples N random *live* boards for one ATS, scrapes them, and triangulates
each job's shipped ``remote`` against two independent signals — what the location string alone says
(``models.is_remote``) and what the description text says — to surface *candidates* for a human to
read and judge. It does not decide correctness; keyword hits are leads, not verdicts.

Two modes:
  --mode mismatch   flag likely false-negatives (remote missed) and false-positives (over-claimed),
                    plus native-vs-location disagreements. (step 1, default 50 boards)
  --mode empty      count blank-location jobs and, among them, the ones whose description signals
                    remote — the gap a location-only scraper structurally cannot catch. (step 2, 100)

Boards are cached per-board under the artifacts dir, so a 100-board run reuses a prior 50-board
run's scrapes (the sample is a seeded superset) and nothing is re-fetched.

Output (all local; experiment/ is gitignored):
  experiment/remote-detection-audit/artifacts/{ats}/boards/{slug}.json   per-board scrape cache
  experiment/remote-detection-audit/artifacts/{ats}/report-{mode}-*.json  machine report

Run (needs network):
  .venv/bin/python -u scripts/eval/audit_remote.py --ats greenhouse --mode mismatch --boards 50
  .venv/bin/python -u scripts/eval/audit_remote.py --ats greenhouse --mode empty   --boards 100
Exit: 0 normally, 1 if every sampled board errored (a dead sample must be loud, not "0 findings").
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from headstart.config import load_active_companies
from headstart.models import is_remote
from headstart.scrapers.registry import SCRAPERS, get_scraper

_ROOT = Path(__file__).resolve().parents[2]
_LEDGER = _ROOT / "data" / "validate" / "liveness"
_OUT = _ROOT / "experiment" / "remote-detection-audit" / "artifacts"

# Description phrases that strongly imply a remote role — high-precision leads worth reading.
_REMOTE_STRONG = re.compile(
    r"\b(fully[- ]remote|100%\s*remote|remote[- ]first|work(?:ing)? from anywhere|"
    r"work anywhere|fully distributed|distributed team|home[- ]based|telecommut\w*|"
    r"remote[-,\s]*(?:us|usa|uk|eu|emea|apac|global|anywhere|worldwide|india))\b",
    re.I,
)
# Any bare mention of "remote" — noisy (remote servers, remote monitoring…), so only a weak lead.
_REMOTE_ANY = re.compile(r"remote", re.I)
# Counter-signals for false-positive checks: a job flagged remote that reads on-site/hybrid.
_ONSITE = re.compile(
    r"\b(hybrid|on[- ]?site|in[- ]office|in the office|relocation (?:is )?required|must relocate)\b",
    re.I,
)


def _safe(slug: str) -> str:
    """Filesystem-safe, collision-resistant cache name (workday slugs are full URLs)."""
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", slug)[:100]
    return f"{stem}_{hashlib.sha1(slug.encode()).hexdigest()[:8]}"


def _snippet(desc: str, match: re.Match | None, width: int = 90) -> str:
    if not match:
        return ""
    start, end = max(0, match.start() - width), min(len(desc), match.end() + width)
    body = re.sub(r"\s+", " ", desc[start:end]).strip()
    return f"{'…' if start else ''}{body}{'…' if end < len(desc) else ''}"


def sample_boards(ats: str, n: int, seed: int):
    """N boards for one ATS, seed-stable and prefix-stable (a 100 sample ⊇ the 50 sample)."""
    pool = [c for c in load_active_companies(_LEDGER, min_jobs=1) if c.ats == ats]
    pool.sort(key=lambda c: c.slug)
    shuffled = random.Random(seed).sample(
        pool, len(pool)
    )  # full permutation → prefix-stable
    return shuffled[:n]


def scrape_board(company, cache_dir: Path) -> dict:
    """Scrape one board (or load its cache). Per-board failures are captured, never raised."""
    path = cache_dir / f"{_safe(company.slug)}.json"
    if path.exists():
        return json.loads(path.read_text())
    rec: dict = {
        "board": f"{company.ats}:{company.slug}",
        "slug": company.slug,
        "name": company.name,
    }
    try:
        jobs = get_scraper(company.ats, company.slug, company.name).fetch()
        rec["jobs"] = [j.to_dict() for j in jobs]
        rec["error"] = None
    except Exception as exc:  # noqa: BLE001 - a dead board is data, not a crash
        rec["jobs"] = []
        rec["error"] = f"{type(exc).__name__}: {exc}"[:200]
    path.write_text(json.dumps(rec))
    return rec


def scrape_all(boards, cache_dir: Path, workers: int = 8) -> list[dict]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(scrape_board, c, cache_dir): c for c in boards}
        for i, future in enumerate(as_completed(futures), 1):
            rec = future.result()
            records.append(rec)
            tag = rec["error"] or f"{len(rec['jobs'])} jobs"
            print(
                f"[scrape {i}/{len(boards)}] {rec['board']}: {tag}",
                file=sys.stderr,
                flush=True,
            )
    return records


def _row(rec: dict, job: dict, match: re.Match | None) -> dict:
    return {
        "board": rec["board"],
        "title": job.get("title"),
        "location": job.get("location"),
        "stored_remote": job.get("remote"),
        "loc_signal": is_remote(job.get("location")),
        "url": job.get("url"),
        "desc_snippet": _snippet(job.get("description") or "", match),
    }


def analyze_mismatch(records: list[dict]) -> dict:
    false_neg, false_pos, disagree = [], [], []
    agg = {
        "jobs": 0,
        "remote_true": 0,
        "remote_false": 0,
        "remote_none": 0,
        "empty_location": 0,
    }
    for rec in records:
        for job in rec["jobs"]:
            agg["jobs"] += 1
            stored, loc = job.get("remote"), job.get("location")
            agg[
                "remote_true"
                if stored is True
                else "remote_false"
                if stored is False
                else "remote_none"
            ] += 1
            if not (loc or "").strip():
                agg["empty_location"] += 1
            loc_sig = is_remote(loc)
            desc = job.get("description") or ""
            strong, any_m = _REMOTE_STRONG.search(desc), _REMOTE_ANY.search(desc)

            if stored is not True and (
                loc_sig is True or bool(strong) or (any_m and not (loc or "").strip())
            ):
                false_neg.append(_row(rec, job, strong or any_m))
            elif (
                stored is True
                and loc_sig is not True
                and (loc or "").strip()
                and (not any_m or _ONSITE.search(desc))
            ):
                false_pos.append(_row(rec, job, _ONSITE.search(desc) or any_m))
            elif (
                isinstance(stored, bool)
                and isinstance(loc_sig, bool)
                and stored != loc_sig
            ):
                disagree.append(_row(rec, job, any_m))
    return {
        "aggregate": agg,
        "false_negatives": false_neg,
        "false_positives": false_pos,
        "native_vs_location": disagree,
    }


def analyze_empty(records: list[dict]) -> dict:
    empty_strong, empty_any, empty_flagged = [], [], 0
    agg = {"jobs": 0, "empty_location": 0, "empty_remote_true": 0}
    for rec in records:
        for job in rec["jobs"]:
            agg["jobs"] += 1
            if (job.get("location") or "").strip():
                continue
            agg["empty_location"] += 1
            if job.get("remote") is True:
                agg["empty_remote_true"] += 1
                empty_flagged += 1
            desc = job.get("description") or ""
            strong, any_m = _REMOTE_STRONG.search(desc), _REMOTE_ANY.search(desc)
            if strong:
                empty_strong.append(_row(rec, job, strong))
            elif any_m:
                empty_any.append(_row(rec, job, any_m))
    agg["empty_already_flagged_remote"] = empty_flagged
    return {
        "aggregate": agg,
        "empty_desc_strong": empty_strong,
        "empty_desc_any": empty_any,
    }


def _print_bucket(name: str, rows: list[dict], cap: int) -> None:
    print(
        f"\n### {name}: {len(rows)}" + (f" (showing {cap})" if len(rows) > cap else "")
    )
    for r in rows[:cap]:
        print(f"  • [{r['stored_remote']}] loc={r['location']!r}  {r['title']!r}")
        print(f"    {r['url']}")
        if r["desc_snippet"]:
            print(f"    desc: {r['desc_snippet']}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--ats", required=True, choices=sorted(SCRAPERS), help="one ATS at a time"
    )
    ap.add_argument("--mode", choices=("mismatch", "empty"), default="mismatch")
    ap.add_argument(
        "--boards",
        type=int,
        default=50,
        help="50 for step 1 (mismatch), 100 for step 2 (empty)",
    )
    ap.add_argument(
        "--seed", type=int, default=7, help="seed-stable sample; a 100 run ⊇ a 50 run"
    )
    ap.add_argument(
        "--cap",
        type=int,
        default=40,
        help="max rows printed per bucket (all go to the JSON report)",
    )
    args = ap.parse_args()

    boards = sample_boards(args.ats, args.boards, args.seed)
    print(
        f"[audit] {args.ats}: sampled {len(boards)} boards (mode={args.mode}, seed={args.seed})",
        file=sys.stderr,
        flush=True,
    )
    cache_dir = _OUT / args.ats / "boards"
    records = scrape_all(boards, cache_dir)

    ok = [r for r in records if r["error"] is None]
    errored = len(records) - len(ok)
    if records and not ok:
        print(
            f"[audit] every one of {len(records)} boards errored — aborting.",
            file=sys.stderr,
            flush=True,
        )
        return 1

    result = analyze_mismatch(ok) if args.mode == "mismatch" else analyze_empty(ok)
    result["meta"] = {
        "ats": args.ats,
        "mode": args.mode,
        "boards": len(records),
        "boards_errored": errored,
        "seed": args.seed,
        "ran_at": datetime.now(timezone.utc).isoformat(),
    }

    print(
        f"\n{'=' * 70}\n{args.ats}  mode={args.mode}  boards={len(ok)}/{len(records)} ok  "
        f"jobs={result['aggregate']['jobs']}"
    )
    for k, v in result["aggregate"].items():
        if k != "jobs":
            print(f"  {k}: {v}")
    if args.mode == "mismatch":
        _print_bucket(
            "CANDIDATE FALSE NEGATIVES (remote missed?)",
            result["false_negatives"],
            args.cap,
        )
        _print_bucket(
            "CANDIDATE FALSE POSITIVES (remote over-claimed?)",
            result["false_positives"],
            args.cap,
        )
        _print_bucket(
            "NATIVE-vs-LOCATION DISAGREEMENT", result["native_vs_location"], args.cap
        )
    else:
        _print_bucket(
            "EMPTY LOCATION + STRONG remote phrase",
            result["empty_desc_strong"],
            args.cap,
        )
        _print_bucket(
            "EMPTY LOCATION + bare 'remote' mention", result["empty_desc_any"], args.cap
        )

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = (
        _OUT / args.ats / f"report-{args.mode}-{args.boards}b-seed{args.seed}-{ts}.json"
    )
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, indent=1))
    print(f"\nreport -> {report.relative_to(_ROOT)}", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
