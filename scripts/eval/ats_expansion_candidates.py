#!/usr/bin/env python3
"""Rank ATS-expansion candidates by tech% and India% yield, to decide what to build next.

`kalil0321/ats-scrapers` (github.com/kalil0321/ats-scrapers) is an open-source library that
scrapes ~60 ATS platforms and publishes a daily hosted snapshot (no auth) at
`https://storage.stapply.ai/jobhive/v1/manifest.json`. Its scraper roster includes several
platforms HeadStart has no scraper for. Rather than sampling those platforms live ourselves,
this reads their already-scraped, full per-ATS job data and applies HeadStart's own gates —
`headstart.tech_filter.is_tech` and `headstart.geo.classify` — to see what fraction would
actually be tech roles and how much of that is India, two numbers for weighing the next scraper
to build (CLAUDE.md's build list).

This is a full-population read (today's whole published slice per ATS), not a sample: no
probe-size caveat applies to the tech%/India% numbers themselves. What IS a caveat: this is a
third party's company list and scrape, not ours — their tenant coverage, scrape recency, and
"jobs" definition may not match what HeadStart's own discovery/scraper would find, so treat
these as a strong prioritization signal, not a promise of what a HeadStart scraper would yield.

Usage:  python -u scripts/eval/ats_expansion_candidates.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from headstart.geo import classify as geo_classify
from headstart.tech_filter import is_tech

MANIFEST_URL = "https://storage.stapply.ai/jobhive/v1/manifest.json"
UA = "headstart-eval/0.1"

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "experiment" / "ats-scraper-candidates"
PARQUET_DIR = OUT_DIR / "artifacts" / "parquet"

# ats-scrapers source key -> HeadStart's own scraper module, for platforms already covered.
# (taleo -> taleo_be: verified live 2026-09-16, ats-scrapers' taleo.py targets
# `{tenant}.tbe.taleo.net/.../careers/v2/searchResults`, the Business Edition URL shape.)
ALREADY_COVERED = {
    "amazon": "amazon",
    "apple": "apple",
    "ashby": "ashby",
    "bytedance": "bytedance",
    "darwinbox": "darwinbox",
    "eightfold": "eightfold",
    "google": "google",
    "greenhouse": "greenhouse",
    "icims": "icims",
    "jazzhr": "jazzhr",
    "jobvite": "jobvite",
    "join_com": "join (disabled)",
    "keka": "keka",
    "lever": "lever",
    "meta": "meta",
    "oracle": "oracle",
    "personio": "personio",
    "phenom": "phenom",
    "recruitee": "recruitee",
    "rippling": "rippling",
    "smartrecruiters": "smartrecruiters",
    "successfactors": "successfactors",
    "taleo": "taleo_be",
    "teamtailor": "teamtailor",
    "tesla": "tesla",
    "tiktok": "tiktok",
    "uber": "uber",
    "workable": "workable",
    "workday": "workday",
    # Verified live 2026-09-16: recruiterbox.com 301s every tenant to {slug}.hire.trakstar.com
    # (Recruiterbox rebranded to Trakstar Hire), and ats-scrapers' own recruiterbox rows carry
    # *.hire.trakstar.com URLs already. Not a distinct platform — a discovery-gap in trakstar.py's
    # tenant list, not a new scraper to build.
    "recruiterbox": "trakstar (legacy Recruiterbox brand — same platform, discovery gap not a build gap)",
}

# Multi-employer job boards / government portals in the manifest: not per-company ATS
# platforms, so "would we build a scraper for this ATS" doesn't apply the same way.
NOT_A_COMPANY_ATS = {
    "arbetsformedlingen",
    "builtin",
    "bundesagentur",
    "eures",
    "getonbrd",
    "jobbankca",
    "jobsch",
    "manfred",
    "programathor",
    "wanted",
    "welcometothejungle",
    "weworkremotely",
    "ycombinator",
}

# ats-companies/{ats}.csv row counts (incl. header), fetched 2026-09-16 from
# raw.githubusercontent.com/kalil0321/ats-scrapers/main/ats-companies/ — the known-tenant pool
# size, for a rough coverage ratio against the jobs dataset's unique-company count.
KNOWN_TENANT_POOL = {
    "avature": 126,
    "bamboohr": 5632,
    "beisen": 221,
    "beisen_legacy": 8,
    "breezy": 1384,
    "cornerstone": 564,
    "dayforce": 348,
    "gem": 496,
    "gupy": 589,
    "herp": 969,
    "hrmos": 960,
    "moka": 198,
    "pageup": 21,
    "paycom": 5135,
    "paylocity": 48,
    "pinpoint": 406,
    "recruiterbox": 314,
    "softgarden": 392,
    "ukg": 107,
    "mercor": 1,
}


def fetch_manifest() -> dict:
    p = subprocess.run(
        ["curl", "-sS", "--max-time", "30", "-A", UA, MANIFEST_URL],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(p.stdout)


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["curl", "-sS", "--max-time", "180", "-A", UA, "-o", str(dest), url], check=True
    )


def analyze(ats: str, path: Path) -> dict:
    wanted = ["title", "company", "department", "location"]
    available = set(pq.ParquetFile(path).schema_arrow.names)
    df = pd.read_parquet(path, columns=[c for c in wanted if c in available])
    total = len(df)
    if total == 0:
        return {"ats": ats, "total": 0}

    def _str_or_none(v: object) -> str | None:
        return v if isinstance(v, str) else None

    # Row-wise `df.apply(axis=1)` builds a per-row Series across columns and silently turns a
    # missing string cell into float NaN in the process (pandas 3's "str" dtype interacting
    # with mixed-dtype row construction) — `tech_filter.classify` then dies on `NaN.strip()`.
    # Plain Python lists sidestep that row-construction step entirely.
    titles = [_str_or_none(v) for v in df["title"].tolist()]
    depts = (
        [_str_or_none(v) for v in df["department"].tolist()]
        if "department" in df
        else [None] * total
    )
    locs = (
        [_str_or_none(v) for v in df["location"].tolist()]
        if "location" in df
        else [None] * total
    )

    tech_mask = pd.Series(
        [is_tech(t, d) for t, d in zip(titles, depts)], index=df.index
    )
    india_mask = pd.Series([geo_classify(loc) == "IN" for loc in locs], index=df.index)

    tech_titles = df.loc[tech_mask, "title"].dropna().head(5).tolist()
    india_titles = df.loc[india_mask, "title"].dropna().head(5).tolist()
    unique_companies = df["company"].nunique() if "company" in df else None

    return {
        "ats": ats,
        "total": total,
        "unique_companies": unique_companies,
        "known_tenant_pool": KNOWN_TENANT_POOL.get(ats),
        "tech": int(tech_mask.sum()),
        "tech_pct": 100 * tech_mask.mean(),
        "india": int(india_mask.sum()),
        "india_pct": 100 * india_mask.mean(),
        "tech_and_india": int((tech_mask & india_mask).sum()),
        "sample_tech_titles": tech_titles,
        "sample_india_titles": india_titles,
    }


def main() -> None:
    manifest = fetch_manifest()
    by_ats = manifest["by_ats"]
    print(
        f"manifest generated_at={manifest['generated_at']} "
        f"ats_count={manifest['stats']['ats_count']} "
        f"total_jobs={manifest['stats']['total_jobs']}",
        flush=True,
    )

    candidates = sorted(
        k for k in by_ats if k not in ALREADY_COVERED and k not in NOT_A_COMPANY_ATS
    )
    print(
        f"\n{len(candidates)} candidate ATSes not yet covered: {candidates}\n",
        flush=True,
    )

    # Rewritten after every ATS, not once at the end: each one costs a download and a full-file
    # scan, so a crash on the last of them would otherwise discard every earlier result.
    out_json = OUT_DIR / "artifacts" / "ats_expansion_candidates.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)

    results = []
    for ats in candidates:
        entry = by_ats[ats]
        url = entry.get("parquet") or entry["csv"]
        dest = PARQUET_DIR / f"{ats}.parquet"
        if not dest.exists():
            print(
                f"[{ats}] downloading {entry['rows']} rows ({entry.get('parquet_size_bytes', entry['size_bytes']) / 1e6:.1f}MB)...",
                flush=True,
            )
            download(url, dest)
        stats = analyze(ats, dest)
        results.append(stats)
        print(
            f"[{ats}] total={stats['total']} tech={stats.get('tech')} "
            f"({stats.get('tech_pct', 0):.1f}%) india={stats.get('india')} "
            f"({stats.get('india_pct', 0):.1f}%) tech&india={stats.get('tech_and_india')}",
            flush=True,
        )
        out_json.write_text(json.dumps(results, indent=2, ensure_ascii=False))

    print(f"\nwrote {out_json}", flush=True)

    results.sort(key=lambda r: r.get("tech", 0), reverse=True)
    print(
        f"\n{'ATS':<15}{'jobs':>8}{'tech':>8}{'tech%':>8}{'india':>8}{'india%':>8}{'tech&IN':>9}{'companies':>11}{'pool':>7}"
    )
    for r in results:
        print(
            f"{r['ats']:<15}{r['total']:>8}{r.get('tech', 0):>8}{r.get('tech_pct', 0):>7.1f}%"
            f"{r.get('india', 0):>8}{r.get('india_pct', 0):>7.1f}%{r.get('tech_and_india', 0):>9}"
            f"{r.get('unique_companies') or 0:>11}{r.get('known_tenant_pool') or 0:>7}"
        )


if __name__ == "__main__":
    main()
