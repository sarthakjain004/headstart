#!/usr/bin/env python3
"""Draw the tech filter's blind hold-out, and estimate recall/precision from its labels.

The hold-out (``tests/fixtures/tech_filter_holdout.tsv``) is a stratified random sample of
English Indeed titles: 500 rows the filter drops, 300 it keeps, drawn after the code was final
and labelled blind (docs/tech-filter/2026-09-23_spellings-and-trades.md). Nothing is tuned on it.

Two subcommands:

  draw      replay the sample from the Indeed harvest with a given filter module, and print the
            titles with the number of drawn rows each stands for (the row weights)
  estimate  weight the fixture's labels back to the population and report recall/precision for
            the CURRENT filter (``headstart.jobs.tech_filter``), by row, with the ambiguous labels
            both left out and counted as tech

The design strata are the verdicts of the filter the sample was drawn with (``--draw-filter``,
the module at commit 6a1b3630), not the current one, so the same labels estimate any later
version of the filter without bias.

Run (from the repo root):
  python scripts/eval/tech_filter_holdout.py draw --draw-filter <path/to/tech_filter.py>
  PYTHONPATH=src python scripts/eval/tech_filter_holdout.py estimate
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import random
import re
import sys
from pathlib import Path

HARVEST = Path("experiment/indeed-harvest/artifacts/indeed_jobs_v3.jsonl")
FIXTURE = Path("tests/fixtures/tech_filter_holdout.tsv")
META = Path("tests/fixtures/tech_filter_holdout.meta.json")
EVAL_SET = Path("tests/fixtures/tech_filter_eval.tsv")
SEED = 20260924
SIZES = {"dropped": 500, "kept": 300}

# English-market countries of the harvest's query plan, and a title that reads as English.
ENGLISH_COUNTRIES = {"US", "IN", "GB", "CA", "SG", "PH", "AU", "AE", "IE", "ZA", "MY"}
_NON_ENGLISH = re.compile(
    r"[^\x00-\x7f–—’‘“”•·®™é]|\b(ingénieur|développeur|responsable|chargé|technicien"
    r"|gestionnaire|analyste|conseiller|chef de|directeur|spécialiste|de la|des|du)\b",
    re.IGNORECASE,
)


def _load_filter(path: str):
    spec = importlib.util.spec_from_file_location("draw_filter", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["draw_filter"] = module
    spec.loader.exec_module(module)
    return module


def _population(draw_filter, harvest: Path) -> dict[str, list[str]]:
    """English Indeed titles, one per posting, split by the draw filter's verdict."""
    with EVAL_SET.open(encoding="utf-8", newline="") as fh:
        labelled = {
            r["title"].strip().lower() for r in csv.DictReader(fh, delimiter="\t")
        }
    seen, pools = set(), {"kept": [], "dropped": []}
    with harvest.open(encoding="utf-8") as fh:
        for line in fh:
            job = json.loads(line)
            if job["key"] in seen:
                continue
            seen.add(job["key"])
            title = (job.get("title") or "").strip()
            country = (job.get("_query") or {}).get("country")
            if country not in ENGLISH_COUNTRIES or _NON_ENGLISH.search(title):
                continue
            if not title or title.lower() in labelled:
                continue
            pools["kept" if draw_filter.is_tech(title) else "dropped"].append(title)
    return pools


def draw(args) -> None:
    pools = _population(_load_filter(args.draw_filter), Path(args.harvest))
    print(f"population: kept {len(pools['kept']):,}  dropped {len(pools['dropped']):,}")
    rng = random.Random(SEED)
    # Order matters for reproduction: dropped first, then kept, from one generator.
    for stratum in ("dropped", "kept"):
        pool = pools[stratum]
        weights: dict[str, int] = {}
        for title in rng.sample(pool, len(pool)):
            # Every row drawn before the n-th distinct title counts, so a title seen twice in
            # the draw stands for two rows — the row-level sample, not a title-level one.
            if len(weights) == SIZES[stratum] and title.lower() not in weights:
                break
            weights[title.lower()] = weights.get(title.lower(), 0) + 1
        for title, rows in weights.items():
            print(f"{stratum}\t{rows}\t{title}")


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    p = k / n
    c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return c - h, c + h


def estimate(_args) -> None:
    from headstart.jobs.tech_filter import is_tech

    meta = json.loads(META.read_text())
    with FIXTURE.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))

    def weighted(ambiguous_is_tech: bool) -> tuple[float, float]:
        # Per design stratum: row-weighted share of (tech & kept-now) and of tech.
        tp = tech = kept = 0.0
        for stratum in ("kept", "dropped"):
            sample = [r for r in rows if r["stratum"] == stratum]
            n = sum(int(r["rows"]) for r in sample)
            scale = meta["population"][stratum] / n
            for r in sample:
                w = int(r["rows"]) * scale
                is_t = r["label"] == "tech" or (
                    ambiguous_is_tech and r["label"] == "ambiguous"
                )
                now = is_tech(r["title"])
                if r["label"] == "ambiguous" and not ambiguous_is_tech:
                    continue
                tech += w * is_t
                tp += w * (is_t and now)
                kept += w * now
        return tp / tech, tp / kept

    r, p = weighted(False)
    r_amb, p_amb = weighted(True)
    missed = sum(1 for x in rows if x["label"] == "tech" and not is_tech(x["title"]))
    fps = sum(1 for x in rows if x["label"] == "not_tech" and is_tech(x["title"]))
    print(f"recall    {r:.1%}   (ambiguous counted as tech: {r_amb:.1%})")
    print(f"precision {p:.1%}   (ambiguous counted as tech: {p_amb:.1%})")
    tech_rows = [x for x in rows if x["label"] == "tech"]
    lo, hi = _wilson(len(tech_rows) - missed, len(tech_rows))
    print(
        f"unweighted: {len(tech_rows) - missed}/{len(tech_rows)} tech titles kept "
        f"({lo:.1%}-{hi:.1%}); {fps} non-tech titles kept"
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("draw")
    d.add_argument(
        "--draw-filter", required=True, help="tech_filter.py the sample was drawn with"
    )
    d.add_argument("--harvest", default=str(HARVEST), help="the Indeed harvest JSONL")
    d.set_defaults(fn=draw)
    sub.add_parser("estimate").set_defaults(fn=estimate)
    args = ap.parse_args()
    args.fn(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
