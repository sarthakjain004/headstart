#!/usr/bin/env python3
"""Turn Wellfound's company -> ATS *provider* hints into actual tenant slugs, by probing.

wellfound_ledger_gap.py leaves companies we know are on a supported ATS but whose tenant slug no
name match found. Wellfound never exposes that slug, so the only way to get it is to guess and ask
the ATS. This derives a handful of slug spellings per company and probes each with check_liveness's
own PROBES — the same code that writes the ledger, so a hit here means a hit there.

Guessing is cheap and the probe is authoritative, so the bias is to try several spellings and let
the ATS adjudicate. Only confirmed-live tenants are written out; misses are dropped rather than
recorded, because every row here is destined for the committed liveness ledger and speculative
slugs would leave permanent junk `dead` rows in it.

Output: data/discover/wellfound_resolved_tenants.csv (ats,tenant,url,company,jobs_on_ats,source).

Usage:  python scripts/discover/wellfound_slug_probe.py
"""

import csv
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "validate"))
sys.path.insert(0, str(ROOT / "src"))
from check_liveness import LIVE, PROBES

from headstart import liveness

GAP = ROOT / "data" / "resolve" / "wellfound_ledger_gap.csv"
OUT = ROOT / "data" / "discover" / "wellfound_resolved_tenants.csv"

# Board URL per ATS, matching the shape already in the pool + ledger.
URL = {
    "ashby": "https://jobs.ashbyhq.com/{t}",
    "greenhouse": "https://job-boards.greenhouse.io/{t}",
    "workable": "https://apply.workable.com/{t}",
    "lever": "https://jobs.lever.co/{t}",
}
# Legal/marketing suffixes that show up in a display name but rarely in a slug.
_SUFFIX = re.compile(r"(inc|llc|ltd|limited|corp|co|gmbh|technologies|labs|ai|io|xyz)$")
COLS = ["ats", "tenant", "url", "company", "jobs_on_ats", "source"]


def variants(company: str, wf_slug: str) -> list[str]:
    """Plausible tenant spellings for one company, most-likely first, deduped.

    Includes domain-shaped slugs. Ashby in particular lets a tenant be its bare hostname
    (Northflank's board is literally `northflank.com`), and a lot of these companies are named
    for their domain in the first place — RWA.xyz, Vibe.co, Constructor.io — where Wellfound
    renders the dot as `_` or `-` in its own slug. Stripping punctuation loses both cases.
    """
    wf = re.sub(r"-\d+$", "", wf_slug or "")  # strip Wellfound's -N disambiguator
    words = re.sub(r"[^a-z0-9]+", " ", (company or "").lower()).split()
    joined = "".join(words)
    hyphen = "-".join(words)
    # The company's own dotted form: "RWA.xyz" -> rwa.xyz, "Constructor.io" -> constructor.io.
    dotted = re.sub(r"[^a-z0-9.]+", "", (company or "").lower()).strip(".")
    stem = _SUFFIX.sub("", joined)
    out = [
        joined,
        hyphen,
        wf,
        wf.replace("-", ""),
        wf.replace("_", "-"),
        stem,
        words[0] if words else "",
        dotted if "." in dotted else "",
        wf.replace("_", "."),  # rwa_xyz -> rwa.xyz
        f"{joined}.com",
        f"{stem}.com",
        f"{stem}.io",
        f"{stem}.ai",
    ]
    seen, keep = set(), []
    for v in out:
        if v and len(v) >= 2 and v not in seen:
            seen.add(v)
            keep.append(v)
    return keep


def main() -> int:
    rows = [r for r in csv.DictReader(GAP.open(encoding="utf-8")) if r["ats"] in URL]

    # Never re-add something already tracked: load the ledger per ATS up front.
    known = {a: set(liveness.load(liveness.dir_for(ROOT) / f"{a}.csv")) for a in URL}

    jobs = []  # (ats, company, tenant) to probe
    for r in rows:
        for v in variants(r["company"], r["wellfound_slug"]):
            if v not in known[r["ats"]]:
                jobs.append((r["ats"], r["company"], v))

    print(
        f"{len(rows)} gap companies -> {len(jobs)} candidate slugs to probe\n",
        flush=True,
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    # Append, never clobber: a re-run with wider variants only *adds* spellings, and companies
    # settled by an earlier run are already in the ledger (so excluded from probing above) —
    # rewriting the file from scratch would silently drop them.
    prior = list(csv.DictReader(OUT.open(encoding="utf-8"))) if OUT.exists() else []
    f = OUT.open("a" if prior else "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(f, fieldnames=COLS, lineterminator="\n")
    if not prior:
        writer.writeheader()

    def probe(job):
        ats, company, tenant = job
        url = URL[ats].format(t=tenant)
        try:
            verdict, n = PROBES[ats](tenant, url)
        except Exception:  # noqa: BLE001
            verdict, n = "unknown", None
        return ats, company, tenant, url, verdict, n

    hits, done = 0, 0
    resolved = {(r["ats"], r["company"]) for r in prior}
    # as_completed, not map: one slow board must not hold up the rest (repo convention).
    with ThreadPoolExecutor(max_workers=16) as ex:
        futures = [ex.submit(probe, j) for j in jobs]
        for fut in as_completed(futures):
            ats, company, tenant, url, verdict, n = fut.result()
            done += 1
            if verdict != LIVE:
                continue
            # First live spelling wins; a company needs exactly one board.
            if (ats, company) in resolved:
                continue
            resolved.add((ats, company))
            writer.writerow(
                {
                    "ats": ats,
                    "tenant": tenant,
                    "url": url,
                    "company": company,
                    "jobs_on_ats": n if n is not None else "",
                    "source": "wellfound",
                }
            )
            f.flush()
            hits += 1
            print(
                f"  LIVE  {ats:11s} {company[:26]:26s} -> {tenant}  ({n} jobs)",
                flush=True,
            )

    f.close()
    print(
        f"\nprobed {done} slugs: {hits} companies resolved to a live board"
        f" -> {OUT.relative_to(ROOT)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
