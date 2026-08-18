#!/usr/bin/env python3
"""Strip path and query from the `url` column of the liveness ledgers.

`cc_miner.tenant_from` used to return no url hint for host-shaped ATSes, so the caller stored the
raw Common Crawl capture instead — usually a job deep link, sometimes with tracking parameters.
Those rows went into `data/validate/liveness/{ats}.csv` verbatim.

It bit personio hardest. `PersonioScraper.url()` appends `/xml` to the slug, so a stored
`falkemedia.jobs.personio.de/job/186062?language=de` became
`https://falkemedia.jobs.personio.de/job/186062?language=de/xml` — the suffix inside the query
string. Personio answered with the ordinary HTML job page at HTTP 200, `ET.fromstring` died on it
(678 ParseErrors across 19 pipeline runs), and the liveness prober, which shared the same
non-normalising split, recorded all 312 such boards `live` with `jobs=0`.

The scrapers and the prober are fixed; this repairs the data they read. The `tenant` column is
already correct (633 of 634 personio rows), so this only rewrites `url` — no row is added,
dropped, or re-keyed.

Rows whose status came from a probe of the *wrong* URL are also reset to `unknown` with `--reprobe`,
so the next `check_liveness` run re-verifies them rather than trusting a verdict about a job page.

    python scripts/validate/normalise_ledger_urls.py --dry-run
    python scripts/validate/normalise_ledger_urls.py --apply --reprobe
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from headstart.models import host_of  # noqa: E402 - needs src on sys.path first

REPO_ROOT = Path(__file__).resolve().parents[2]
LEDGER_DIR = REPO_ROOT / "data" / "validate" / "liveness"

# ONLY the ATSes whose scraper reads `url` and expects a bare HOST. Everything else either
# ignores `url` entirely (it derives the slug from `tenant`) or — Workday — needs the path,
# because `https://{co}.wd1.myworkdayjobs.com/{site}` carries the site. Verified by inspecting
# which scrapers override `slug_from`: personio, workday and zoho are the only three, and of
# those only personio and zoho want a host.
#
# This allowlist is the whole safety of the script. Run unscoped it would rewrite 126,665 rows —
# stripping `boards.greenhouse.io/stripe` to `boards.greenhouse.io` and destroying the ledger.
HOST_SHAPED = ("personio", "zoho")


def is_polluted(url: str) -> bool:
    """Whether this `url` carries anything past the host. A trailing slash does not count —
    it is harmless and rewriting it would churn thousands of rows for nothing."""
    rest = (url or "").split("://", 1)[-1]
    host, sep, tail = rest.partition("/")
    return bool("?" in rest or (sep and tail.strip("/")))


def normalise(path: Path, *, apply: bool, reprobe: bool) -> tuple[int, int]:
    rows = list(csv.DictReader(path.open(encoding="utf-8", newline="")))
    if not rows:
        return 0, 0
    fields = list(rows[0].keys())
    changed = reset = 0
    for row in rows:
        url = row.get("url") or ""
        if not is_polluted(url):
            continue
        host = host_of(url)
        if not host:
            continue  # nothing recoverable; leave the row exactly as it is
        row["url"] = f"https://{host}" if "://" in url else host
        changed += 1
        if reprobe and row.get("status") != "unknown":
            # The old verdict describes a job page, not a board — including the `jobs=0` that
            # made these look alive-but-empty. Clear it so the fixed prober decides afresh.
            row["status"] = "unknown"
            row["jobs"] = ""
            reset += 1
    if apply and changed:
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    return changed, reset


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write; default is a dry run")
    ap.add_argument(
        "--reprobe",
        action="store_true",
        help="also reset a rewritten row to status=unknown so check_liveness re-verifies it",
    )
    args = ap.parse_args()

    total = total_reset = 0
    for path in sorted(LEDGER_DIR.glob("*.csv")):
        if path.stem not in HOST_SHAPED:
            continue
        changed, reset = normalise(path, apply=args.apply, reprobe=args.reprobe)
        if changed:
            total += changed
            total_reset += reset
            tail = f", {reset} reset to unknown" if reset else ""
            print(f"{path.name}: {changed} url(s) normalised{tail}", flush=True)
    verb = "rewrote" if args.apply else "would rewrite"
    print(f"\n{verb} {total} url(s); {total_reset} reset for re-probe", flush=True)
    if not args.apply:
        print("dry run — pass --apply to write", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
