#!/usr/bin/env python3
"""Fold the fingerprinter's resolved Boards into the pipeline's candidate pool.

This is the step that turns "we know which ATS this company is on" into rows the liveness
checker can settle. Reads ``data/resolve/coverage.csv`` (name,domain,ats,slug,source,flag —
produced by scripts/resolve/merge_results.py from the static and verify passes) and folds each
row into ``data/ats-tenants-merged/{ats}.csv``, the pool's schema ``ats,tenant,url,source``:

Reading coverage.csv rather than fingerprint_results.csv directly is deliberate: coverage is
where merge_results.py applies its hand-verified FALSE_POSITIVES list and raises the
``review:enterprise-on-smb-ats`` flag. Consuming the raw results skipped both.

- a tenant already in the pool gets ``fingerprint`` appended to its source (``wayback`` ->
  ``wayback+fingerprint``) and keeps its existing URL,
- a tenant new to the pool is added with source ``fingerprint``,
- an ATS with no scraper in the registry is skipped and counted, never written blind.

Why not reuse merge_harvest_into_tenants.py: that one lower-cases every slug. SmartRecruiters
board ids carry capitals (the liveness ledger holds 8,736 mixed-case tenants) and Workday site
names are case-sensitive, so routing these rows through it would corrupt both. Tenant matching
here is still case-insensitive, so a new capitalised Slug never duplicates a lower-case row
already in the pool.

Tenant/URL shapes were read off real rows in each pool file rather than assumed. Two caveats
worth knowing: eightfold's pool file holds BOTH shapes (``10xgenomics`` and
``10xgenomics.eightfold.ai``), so we write the bare label and accept that it will not dedupe
against the host-shaped rows; and workday's pool tenants follow no derivable convention at all
(22,206 of 24,252 match neither site nor host label), so our rows dedupe only against each
other, not against pre-existing workday rows.

Run:  python scripts/merge/merge_fingerprint_into_tenants.py [--dry-run]
"""

import argparse
import csv
import re
from pathlib import Path

WORKDAY_URL = re.compile(
    r"^https://(?P<co>[^.]+)\.(?P<pod>wd\d+)\.myworkdayjobs\.com/(?P<site>[^/?#]+)",
    re.IGNORECASE,
)

ROOT = Path(__file__).resolve().parent.parent.parent
SRC = ROOT / "data" / "resolve" / "coverage.csv"
MERGED = ROOT / "data" / "ats-tenants-merged"

# {tenant} -> board URL, matching the shape already present in each pool file.
URL_SHAPES = {
    "greenhouse": "https://job-boards.greenhouse.io/{t}",
    "lever": "https://jobs.lever.co/{t}",
    "ashby": "https://jobs.ashbyhq.com/{t}",
    "smartrecruiters": "careers.smartrecruiters.com/{t}",
    "workable": "https://apply.workable.com/{t}",
    "recruitee": "https://{t}.recruitee.com",
    "zoho": "https://{t}.zohorecruit.com",
    "teamtailor": "https://{t}.teamtailor.com",
    # scheme-less on purpose: these three pool files store the bare host (10times.freshteam.com,
    # 1force.hire.trakstar.com, ats.rippling.com/10-west-reg), so writing https:// would make our
    # rows the odd ones out in a file the liveness checker reads back.
    "freshteam": "{t}.freshteam.com",
    "trakstar": "{t}.hire.trakstar.com",
    "rippling": "ats.rippling.com/{t}",
    "darwinbox": "https://{t}.darwinbox.in",
    "keka": "https://{t}.keka.com",
    "ripplehire": "https://{t}.ripplehire.com",
    "sensehq": "https://{t}.sensehq.com",
}
# ATSes whose fingerprint Slug is a full host/URL rather than a bare label. The pool stores the
# label as the tenant and the host in the url, so split the Slug rather than store it whole.
HOSTY = {"personio": "{t}", "eightfold": "https://{t}/"}


def rows_from(ats: str, slug: str) -> tuple[str, str] | None:
    """(tenant, url) for one fingerprint hit, or None if this ATS isn't poolable."""
    if ats == "workday":
        # slug_from() returns the url itself, so the URL is the load-bearing field and the tenant
        # column only has to be a stable unique key. It must NOT be the last URL segment: that is
        # a site name, and site names collide hard across companies (robots x1966, external x931,
        # careers x727 in the existing pool) — keying on it silently merged distinct boards and
        # dropped 10 of 99. company+pod+site is unique per board and still reads like a label.
        # The pod belongs in the key: Rappi serves the same site name from wd12 AND wd3, which
        # are two distinct board URLs. Lower-casing still folds the case variants a page emits
        # for one board (luminegrp motive/Motive, transunion transunion/TransUnion).
        url = slug.rstrip("/")
        m = WORKDAY_URL.match(url)
        return (f"{m['co']}-{m['pod']}-{m['site']}".lower(), url) if m else None
    if ats in HOSTY:
        host = slug.strip("/")
        return (host.split(".")[0], HOSTY[ats].format(t=host))
    shape = URL_SHAPES.get(ats)
    return (slug, shape.format(t=slug)) if shape else None


def load_pool(path: Path) -> dict[str, list[str]]:
    """lower(tenant) -> [tenant, url, source] for a pool file."""
    pool: dict[str, list[str]] = {}
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                t = (r.get("tenant") or "").strip()
                if t:
                    pool[t.lower()] = [
                        t,
                        (r.get("url") or "").strip(),
                        (r.get("source") or "").strip(),
                    ]
    return pool


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Fold fingerprint hits into the tenant pool."
    )
    ap.add_argument("--src", type=Path, default=SRC)
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = ap.parse_args()

    if not args.src.exists():
        raise SystemExit(f"no fingerprint results at {args.src}")

    found: dict[str, dict[str, str]] = {}
    unsupported: dict[str, int] = {}
    flagged: list[str] = []
    hits = 0
    for r in csv.DictReader(args.src.open(encoding="utf-8")):
        ats, slug = (r.get("ats") or "").strip(), (r.get("slug") or "").strip()
        if not ats or not slug:
            continue
        # merge_results.py flags an enterprise name found on an SMB-only ATS as a likely
        # namesake/squat. Those must not enter the pool: liveness can prove a board is alive
        # but not that it belongs to this company, so a squat would be recorded as coverage.
        if (r.get("flag") or "").strip():
            flagged.append(f"{r.get('domain', '?')} -> {ats}:{slug} ({r['flag']})")
            continue
        hits += 1
        built = rows_from(ats, slug)
        if built is None:
            unsupported[ats] = unsupported.get(ats, 0) + 1
            continue
        tenant, url = built
        found.setdefault(ats, {}).setdefault(tenant, url)

    print(f"{hits} hits across {len(found)} poolable ATSes", flush=True)
    if flagged:
        # never a silent drop — print every one so it can be eyeballed and re-added by hand
        print(f"held back {len(flagged)} flagged for review:", flush=True)
        for f in flagged:
            print(f"  {f}", flush=True)
    if unsupported:
        skipped = ", ".join(f"{a} {n}" for a, n in sorted(unsupported.items()))
        print(f"skipped (no pool shape / no scraper): {skipped}", flush=True)

    MERGED.mkdir(parents=True, exist_ok=True)
    print(f"\n{'ATS':<18}{'existing':>9}{'+new':>7}{'total':>8}", flush=True)
    added_total = 0
    for ats in sorted(found):
        path = MERGED / f"{ats}.csv"
        pool = load_pool(path)
        before = len(pool)
        added = 0
        for tenant, url in found[ats].items():
            key = tenant.lower()
            if key in pool:
                src = pool[key][2]
                if "fingerprint" not in src:
                    pool[key][2] = f"{src}+fingerprint" if src else "fingerprint"
                if not pool[key][1]:
                    pool[key][1] = url
            else:
                pool[key] = [tenant, url, "fingerprint"]
                added += 1
        added_total += added
        print(f"{ats:<18}{before:>9}{added:>7}{len(pool):>8}", flush=True)
        if args.dry_run:
            continue
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["ats", "tenant", "url", "source"])
            for tenant, url, source in sorted(
                pool.values(), key=lambda v: v[0].lower()
            ):
                w.writerow([ats, tenant, url, source])

    verb = "would add" if args.dry_run else "added"
    print(
        f"\n{verb} {added_total} new tenants -> {MERGED.relative_to(ROOT)}", flush=True
    )
    print(
        "next: python scripts/validate/check_liveness.py --dir data/ats-tenants-merged <ats>",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
