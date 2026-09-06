#!/usr/bin/env python3
"""Per-ATS health of `Job.location`, measured by scraping live Boards.

The sibling `location_filter_audit.py` answers the same question about the **served table** — what
is already indexed. This one answers it about the **scraper**, which is where a location defect is
actually fixed, and it answers it before a change reaches the index.

The point is the before/after workflow, because every location fix so far has needed one and each
was hand-rolled from scratch:

    .venv/bin/python -u scripts/eval/location_field_health.py --ats recruitee --boards 300
    # ... edit the scraper's parse() ...
    .venv/bin/python -u scripts/eval/location_field_health.py --ats recruitee --boards 300

`fetch_raw()` output is **cached per Board**, so the second run re-parses the identical bytes. That
matters more than the time it saves: without it the two runs sample a moving Board and any
difference could be the world changing rather than the mapping. `--refetch` forces new bytes.

Rows are classified into the shapes these audits keep finding, rather than one "bad" bucket —
a `missing` and a `country-only` have completely different fixes:

  missing      no location at all
  placeless    a whole-string marker naming no place ("Remote", "Poste a distance") — shares its
               vocabulary with `location_filter_audit.is_placeless`, imported rather than copied
  country-only a bare 2-3 letter tag ("SA", "IND") — a real place, but `geo.where()` is an
               set of place *names*, so it matches none of them
  dirty        embedded control characters, doubled/empty comma segments, a leaked URL scheme, or
               untrimmed edges — every one of these was a real defect in some scraper
  place        anything else

Run:  .venv/bin/python -u scripts/eval/location_field_health.py --ats zoho --boards 500
      .venv/bin/python -u scripts/eval/location_field_health.py --ats all --boards 120
Exit: 0 normally, 1 if every sampled Board errored — a dead sample must be loud, never "0 findings".
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import random
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))

from headstart.config import load_active_companies
from headstart.scrapers.registry import SCRAPERS, get_scraper

_LEDGER = _ROOT / "data" / "validate" / "liveness"
_OUT = _ROOT / "experiment" / "location-field-health"
_SCRAPED_AT = "2026-01-01T00:00:00Z"


def _placeless() -> object:
    """`location_filter_audit.is_placeless`, imported rather than reimplemented.

    Its marker list is the one place that vocabulary lives, and a second copy here would be one
    more thing to keep in step. Sibling scripts are not a package, hence the path import.

    Self-checked on import against a marker it must catch. That is not ceremony: this function
    silently decides how many rows get called `placeless`, so a predicate that has drifted would
    under-report the exact defect this script exists to find, and would do it quietly. It caught
    a real one — the localized markers were measured and fixed on a branch that never reached
    main, so every reader of `is_placeless` was scoring `Poste a distance` as a valid place.
    """
    path = Path(__file__).with_name("location_filter_audit.py")
    spec = importlib.util.spec_from_file_location("location_filter_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not module.is_placeless("poste a distance"):
        sys.exit(
            "location_filter_audit.is_placeless does not recognise localized remote markers — "
            "counts here would under-report. Land the _PLACELESS fix before trusting this run."
        )
    return module.is_placeless


_COUNTRY_TAG = re.compile(r"^[A-Za-z]{2,3}$")
_CONTROL = re.compile(r"[\r\n\t\x00-\x1f]")
_EMPTY_SEGMENT = re.compile(r"(^\s*,)|(,\s*,)|(,\s*$)")


def classify(location: str | None, is_placeless) -> str:
    if location is None or not location.strip():
        return "missing"
    raw = location
    if _CONTROL.search(raw) or _EMPTY_SEGMENT.search(raw) or "://" in raw:
        return "dirty"
    if raw != raw.strip():
        return "dirty"
    if is_placeless(raw.strip().lower()):
        return "placeless"
    if _COUNTRY_TAG.match(raw.strip()):
        return "country-only"
    return "place"


def sample(ats: str, n: int, seed: int) -> list:
    """A prefix-stable sample: a full seeded permutation, then the first n.

    So raising --boards keeps every Board the smaller run used, and two runs at different sizes
    stay comparable instead of being two unrelated samples.
    """
    pool = [c for c in load_active_companies(_LEDGER, min_jobs=1) if c.ats == ats]
    pool.sort(key=lambda c: c.slug)
    return random.Random(seed).sample(pool, len(pool))[:n]


def scrape(company, cache_dir: Path, refetch: bool):
    """`(slug, jobs, error)`. Raw bytes are cached so a re-run re-parses the same input."""
    blob = cache_dir / f"{company.slug.replace('/', '_')}.json"
    scraper = get_scraper(company.ats, company.slug, company.name)
    raw = None
    if blob.exists() and not refetch:
        try:
            raw = json.loads(blob.read_text())
        except (OSError, ValueError):
            raw = None
    if raw is None:
        try:
            raw = scraper.fetch_raw()
        except Exception as exc:  # noqa: BLE001 — every failure is reported, never swallowed
            return company.slug, [], type(exc).__name__
        try:
            blob.write_text(json.dumps(raw))
        except (OSError, TypeError, ValueError):
            pass  # not every raw is JSON; the parse below still runs on this pass
    try:
        return company.slug, scraper.parse(raw, _SCRAPED_AT), None
    except Exception as exc:  # noqa: BLE001
        return company.slug, [], f"parse:{type(exc).__name__}"


def audit(ats: str, boards: int, seed: int, refetch: bool, workers: int) -> dict:
    is_placeless = _placeless()
    companies = sample(ats, boards, seed)
    cache_dir = _OUT / "cache" / ats
    cache_dir.mkdir(parents=True, exist_ok=True)
    print(f"{ats}: {len(companies)} Boards sampled (seed {seed})", flush=True)

    shapes: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    examples: dict[str, list] = {}
    answered = jobs = 0
    for index, (slug, parsed, error) in enumerate(
        ThreadPoolExecutor(workers).map(
            lambda c: scrape(c, cache_dir, refetch), companies
        ),
        1,
    ):
        if error:
            errors[error] += 1
        elif parsed:
            answered += 1
        for job in parsed:
            jobs += 1
            shape = classify(job.location, is_placeless)
            shapes[shape] += 1
            if shape != "place" and len(examples.setdefault(shape, [])) < 8:
                examples[shape].append({"board": slug, "location": job.location})
        if index % 50 == 0:
            print(
                f"  {index}/{len(companies)} Boards, {jobs:,} Jobs, "
                f"{sum(v for k, v in shapes.items() if k != 'place'):,} not a clean place",
                flush=True,
            )

    print(f"\n{ats}: {answered} Boards answered, {jobs:,} Jobs", flush=True)
    if not answered:
        # Loud, because an all-error run and a clean run otherwise print the same zeros.
        print(f"  EVERY Board failed — {dict(errors)}", flush=True)
    for shape, count in shapes.most_common():
        print(
            f"  {shape:13} {count:7,}  ({100 * count / max(jobs, 1):5.1f}%)", flush=True
        )
    if errors:
        print(f"  errors: {dict(errors)}", flush=True)
    for shape, rows in examples.items():
        print(f"\n  {shape} examples:", flush=True)
        for row in rows[:5]:
            print(f"    {row['board'][:34]:34} {row['location']!r}", flush=True)

    report = {
        "ats": ats,
        "boards_sampled": len(companies),
        "boards_answered": answered,
        "jobs": jobs,
        "shapes": dict(shapes),
        "errors": dict(errors),
        "examples": examples,
    }
    _OUT.mkdir(parents=True, exist_ok=True)
    (_OUT / f"{ats}.json").write_text(json.dumps(report, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ats", required=True, help="one ATS, or 'all'")
    parser.add_argument("--boards", type=int, default=300)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument(
        "--refetch", action="store_true", help="ignore the cache and fetch fresh bytes"
    )
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()

    names = sorted(SCRAPERS) if args.ats == "all" else [args.ats]
    reports = [
        audit(name, args.boards, args.seed, args.refetch, args.workers)
        for name in names
    ]
    return 0 if any(r["boards_answered"] for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
