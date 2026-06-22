#!/usr/bin/env python3
"""Consolidate the harvested external ATS company lists into one deduplicated
list per ATS provider.

Reads every saved file under ``data/ats-company-lists/sources/**`` and unions,
per ATS provider, the company slugs found via two complementary strategies:

  1. URL scan      - regex ATS board URLs out of any text and parse (ats, slug).
                     Handles the multi-ATS files (company->ATS maps, dossiers).
  2. Filename-keyed - for files whose name encodes one provider (``lever.csv``,
                     ``slugs_lever.csv``, ``lever_slugs.txt``,
                     ``lever_companies.json`` ...) take the slug column / bare
                     tokens directly. Handles the big single-provider lists.

Writes ``by-provider/<ats>.csv`` (slug,url,n_sources,sources) plus
``by-provider/_summary.csv`` and a combined ``by-provider/_all.csv``.

Stdlib only. Idempotent - overwrites by-provider/ each run, so it can be re-run
as more sources are added.
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "data" / "ats-company-lists" / "sources"
OUT = ROOT / "data" / "ats-company-lists" / "by-provider"

# --- ATS name normalization -------------------------------------------------
ALIASES = {
    "recruiterbox": "trakstar", "trakstarhire": "trakstar",
    "joincom": "join",
    "breezyhr": "breezy",
    "sap": "successfactors", "sapsf": "successfactors",
    "oraclecloud": "oracle", "oraclehcm": "oracle", "oraclerecruitingcloud": "oracle",
    "zoho": "zohorecruit",
    "sensehq": "sense",
}


def norm_ats(a: str) -> str:
    a = a.strip().lower().replace("-", "").replace("_", "").replace(" ", "")
    return ALIASES.get(a, a)


# --- URL host patterns: group(1) = slug (host subdomain or path token) -------
HOST_PATTERNS = [
    ("greenhouse", re.compile(r"(?:boards|job-boards|boards-api)\.greenhouse\.io/(?:embed/job_board\?for=|v1/boards/)?([A-Za-z0-9_.-]+)", re.I)),
    ("lever", re.compile(r"(?:jobs|api)\.(?:eu\.)?lever\.co/(?:v0/postings/)?([A-Za-z0-9_.-]+)", re.I)),
    ("ashby", re.compile(r"(?:jobs|api)\.ashbyhq\.com/([A-Za-z0-9_.-]+)", re.I)),
    ("smartrecruiters", re.compile(r"(?:jobs|api|careers)\.smartrecruiters\.com/(?:v1/companies/)?([A-Za-z0-9_.-]+)", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/([A-Za-z0-9_.-]+)", re.I)),
    ("workable", re.compile(r"\b([A-Za-z0-9_-]+)\.workable\.com", re.I)),
    ("recruitee", re.compile(r"\b([A-Za-z0-9_-]+)\.recruitee\.com", re.I)),
    ("bamboohr", re.compile(r"\b([A-Za-z0-9_-]+)\.bamboohr\.com", re.I)),
    ("teamtailor", re.compile(r"\b([A-Za-z0-9_-]+)\.teamtailor\.com", re.I)),
    ("personio", re.compile(r"\b([A-Za-z0-9_-]+)\.jobs\.personio\.(?:de|com)", re.I)),
    ("icims", re.compile(r"(?:careers-)?([A-Za-z0-9_-]+)\.icims\.com", re.I)),
    ("jobvite", re.compile(r"jobs\.jobvite\.com/([A-Za-z0-9_.-]+)", re.I)),
    ("jazzhr", re.compile(r"\b([A-Za-z0-9_-]+)\.applytojob\.com", re.I)),
    ("taleo", re.compile(r"\b([A-Za-z0-9_-]+)\.taleo\.net", re.I)),
    ("successfactors", re.compile(r"\b([A-Za-z0-9_-]+)\.(?:sapsf|successfactors)\.(?:com|eu)", re.I)),
    ("oracle", re.compile(r"\b([A-Za-z0-9_-]+)\.fa\.(?:[a-z0-9-]+\.)*oraclecloud\.com", re.I)),
    ("zohorecruit", re.compile(r"\b([A-Za-z0-9_-]+)\.zohorecruit\.(?:com|in|eu|com\.au)", re.I)),
    ("darwinbox", re.compile(r"\b([A-Za-z0-9_-]+)\.darwinbox\.(?:in|com)", re.I)),
    ("keka", re.compile(r"\b([A-Za-z0-9_-]+)\.keka\.com", re.I)),
    ("trakstar", re.compile(r"\b([A-Za-z0-9_-]+)\.hire\.trakstar\.com", re.I)),
    ("ripplehire", re.compile(r"\b([A-Za-z0-9_-]+)\.ripplehire\.com", re.I)),
    ("sense", re.compile(r"\b([A-Za-z0-9_-]+)\.sensehq\.com", re.I)),
    ("freshteam", re.compile(r"\b([A-Za-z0-9_-]+)\.freshteam\.com", re.I)),
    ("comeet", re.compile(r"\b([A-Za-z0-9_-]+)\.comeet\.co\b", re.I)),
    ("breezy", re.compile(r"\b([A-Za-z0-9_-]+)\.breezy\.hr", re.I)),
    ("pinpoint", re.compile(r"\b([A-Za-z0-9_-]+)\.pinpointhq\.com", re.I)),
    ("join", re.compile(r"join\.com/(?:companies/)?([A-Za-z0-9_.-]+)", re.I)),
    ("rippling", re.compile(r"ats\.rippling\.com/([A-Za-z0-9_.-]+)", re.I)),
]

# Workday boards need instance+site, so they are handled separately (not via the
# slug-token machinery): from full myworkdayjobs URLs and from Feashliaa-style
# ``company|wdN|site`` pipe tuples.
WORKDAY_URL = re.compile(r"([A-Za-z0-9-]+\.wd\d+\.myworkdayjobs\.com(?:/[A-Za-z0-9_-]+)?)", re.I)
WORKDAY_PIPE = re.compile(r"\b([a-z0-9][a-z0-9-]{0,60})\|(wd\d+)\|([A-Za-z0-9_-]+)", re.I)

# canonical URL templates for providers whose board is a simple function of slug
CANON = {
    "greenhouse": "https://boards.greenhouse.io/{s}",
    "lever": "https://jobs.lever.co/{s}",
    "ashby": "https://jobs.ashbyhq.com/{s}",
    "smartrecruiters": "https://jobs.smartrecruiters.com/{s}",
    "workable": "https://apply.workable.com/{s}",
    "recruitee": "https://{s}.recruitee.com",
    "bamboohr": "https://{s}.bamboohr.com",
    "teamtailor": "https://{s}.teamtailor.com",
    "freshteam": "https://{s}.freshteam.com",
    "zohorecruit": "https://{s}.zohorecruit.com",
    "trakstar": "https://{s}.hire.trakstar.com",
    "breezy": "https://{s}.breezy.hr",
    "keka": "https://{s}.keka.com",
    "comeet": "https://www.comeet.com/jobs/{s}",
    "join": "https://join.com/companies/{s}",
    "darwinbox": "https://{s}.darwinbox.in",
    "ripplehire": "https://{s}.ripplehire.com",
    "sense": "https://{s}.sensehq.com",
    "pinpoint": "https://{s}.pinpointhq.com",
}

STOP = {
    "slug", "slugs", "name", "names", "company", "companies", "url", "urls", "token",
    "tokens", "id", "ids", "ats", "careers", "career", "jobs", "job", "boards", "board",
    "www", "api", "http", "https", "com", "co", "io", "net", "org", "embed", "for",
    "v0", "v1", "postings", "example", "examples", "sample", "test", "null", "none",
    "tbd", "todo", "search", "external_careers", "index", "main", "data", "list",
}

KNOWN = {
    "greenhouse", "lever", "ashby", "workday", "smartrecruiters", "workable", "recruitee",
    "bamboohr", "teamtailor", "personio", "jazzhr", "jobvite", "icims", "taleo",
    "successfactors", "oracle", "zohorecruit", "darwinbox", "keka", "trakstar",
    "ripplehire", "sense", "freshteam", "comeet", "breezy", "pinpoint", "paylocity",
    "phenom", "eightfold", "avature", "cornerstone", "gem", "join", "rippling",
    "manatal", "oorwin", "softgarden", "polymer", "ceipal", "peoplestrong", "skillate",
    "turbohire", "recruiterbox", "zoho", "breezyhr", "sensehq",
}
KNOWN_NORM = {norm_ats(k) for k in KNOWN}

# filename-keyed files we must NOT treat as slug lists (taxonomies / code / meta)
SKIP_NAMES = {
    "atsplatform.ts", "ats_detector.rs", "all_ats_providers.txt",
    "supported_ats_providers.txt",
}

SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def valid_slug(s: str) -> bool:
    s = s.strip().strip("/").strip()
    if not s or len(s) > 80:
        return False
    if s.lower() in STOP:
        return False
    if not SLUG_RE.match(s):
        return False
    if s.isdigit() and len(s) > 4:  # drop bare numeric junk ids
        return False
    return True


def provider_from_name(stem: str):
    stem = stem.lower()
    for pat in (r"^slugs[_-](.+)$", r"^(.+?)[_-]slugs$", r"^(.+?)[_-]companies$",
                r"^(.+?)[_-]companies[_-].+$", r"^(.+?)[_-]sources$",
                r"^(.+?)[_-]customers$", r"^([a-z0-9]+)$"):
        m = re.match(pat, stem)
        if m:
            cand = norm_ats(m.group(1))
            if cand in KNOWN_NORM:
                return cand
    return None


def url_scan(text: str):
    """Yield (ats, slug, url) for every ATS board URL found in text."""
    for ats, rx in HOST_PATTERNS:
        for m in rx.finditer(text):
            slug = m.group(1)
            if not valid_slug(slug):
                continue
            yield ats, slug, m.group(0)
    # Workday: keep the full board (host + optional site path) as the slug, since
    # the instance (wdN) and site are needed to actually scrape it.
    for m in WORKDAY_URL.finditer(text):
        board = m.group(1)
        yield "workday", board, "https://" + board
    for m in WORKDAY_PIPE.finditer(text):
        co, inst, site = m.groups()
        board = f"{co}.{inst}.myworkdayjobs.com/{site}"
        yield "workday", board, "https://" + board


def _json_slugs(data):
    out = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                for k in ("slug", "token", "handle", "company_slug", "id", "guid", "name", "company"):
                    v = item.get(k)
                    if isinstance(v, str):
                        out.append(v)
                        break
    elif isinstance(data, dict):
        for v in data.values():
            if isinstance(v, str):
                out.append(v)
            elif isinstance(v, (list, dict)):
                out.extend(_json_slugs(v))
    return out


def filename_keyed_slugs(ats: str, path: Path):
    """Best-effort bare-slug extraction for a single-provider file."""
    suf = path.suffix.lower()
    raw = path.read_text(encoding="utf-8", errors="ignore")
    if suf == ".json":
        try:
            return _json_slugs(json.loads(raw))
        except Exception:
            return []
    if suf == ".csv":
        rows = list(csv.reader(io.StringIO(raw)))
        if not rows:
            return []
        header = [c.strip().lower() for c in rows[0]]
        if "slug" in header:
            i = header.index("slug")
            return [r[i] for r in rows[1:] if len(r) > i]
        if len(header) == 1:
            return [r[0] for r in rows if r]          # single column, no header assumed
        return []                                      # multi-col w/o slug -> url_scan covers
    if suf == ".txt":
        return [re.split(r"[,\t ]", ln.strip())[0] for ln in raw.splitlines() if ln.strip()]
    return []


def main() -> int:
    if not SRC.is_dir():
        print(f"no sources dir at {SRC}", file=sys.stderr)
        return 1

    # ats -> slug_lower -> {slug, url, sources:set}
    data: dict[str, dict[str, dict]] = defaultdict(dict)

    def add(ats, slug, url, source):
        ats = norm_ats(ats)
        key = slug.strip().strip("/").lower()
        rec = data[ats].get(key)
        if rec is None:
            data[ats][key] = {"slug": slug.strip().strip("/"), "url": url, "sources": {source}}
        else:
            rec["sources"].add(source)
            if not rec["url"] and url:
                rec["url"] = url

    files = [p for p in SRC.rglob("*")
             if p.is_file() and p.suffix.lower() != ".md"
             and not p.name.startswith("_")
             and p.name.lower() not in SKIP_NAMES]

    for path in files:
        rel = path.relative_to(SRC).parts
        source = rel[1] if len(rel) > 1 else rel[0]   # the <source-slug> folder
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        # strategy 1: URL scan (every file)
        for ats, slug, url in url_scan(text):
            add(ats, slug, url, source)

        # strategy 2: filename-keyed bare slugs (single-provider files)
        ats = provider_from_name(path.stem)
        if ats and path.suffix.lower() in (".csv", ".txt", ".json"):
            for slug in filename_keyed_slugs(ats, path):
                if valid_slug(slug):
                    add(ats, slug, CANON.get(ats, "").format(s=slug.strip().strip("/")) if ats in CANON else "", source)

    # write outputs
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.csv"):
        old.unlink()

    summary = []
    all_rows = []
    for ats in sorted(data):
        recs = sorted(data[ats].values(), key=lambda r: r["slug"].lower())
        with (OUT / f"{ats}.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["slug", "url", "n_sources", "sources"])
            for r in recs:
                srcs = ";".join(sorted(r["sources"]))
                w.writerow([r["slug"], r["url"], len(r["sources"]), srcs])
                all_rows.append([ats, r["slug"], r["url"], len(r["sources"])])
        summary.append((ats, len(recs), len({s for r in recs for s in r["sources"]})))

    summary.sort(key=lambda t: t[1], reverse=True)
    with (OUT / "_summary.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ats", "unique_slugs", "source_files"])
        w.writerows(summary)
    with (OUT / "_all.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ats", "slug", "url", "n_sources"])
        w.writerows(all_rows)

    total = sum(n for _, n, _ in summary)
    print(f"providers: {len(summary)}   unique (ats,slug) pairs: {total}\n")
    print(f"{'ats':<18}{'unique_slugs':>13}{'src_files':>11}")
    print("-" * 42)
    for ats, n, s in summary:
        print(f"{ats:<18}{n:>13}{s:>11}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
