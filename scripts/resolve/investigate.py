#!/usr/bin/env python3
"""Investigate the ATS of un-investigated companies (from data/resolve/unfound_companies.csv).

For each company runs two reliable checks:
  1. clean-API slug-probe — greenhouse/lever/ashby/smartrecruiters/workable/recruitee with slug
     variants (domain label + normalized name); a jobs>0 response confirms the board.
  2. careers-page scan — fetch homepage + /careers via curl_cffi (bypasses TLS-fingerprint bot
     walls) and grep for ANY known ATS host, including the newer providers the fingerprinter
     doesn't slug-probe (Trakstar / SenseHQ / Skillate / Workday / Oracle HCM / the India tier).

Writes data/resolve/investigated.csv (name,domain,found,method) incrementally.
Run:  python scripts/resolve/investigate.py [N] [offset]
"""

import csv
import re
import sys
from concurrent.futures import as_completed, ThreadPoolExecutor
from pathlib import Path

from curl_cffi import requests as cr

ROOT = Path(__file__).resolve().parent.parent.parent
UNFOUND = ROOT / "data" / "resolve" / "unfound_companies.csv"
OUT = ROOT / "data" / "resolve" / "investigated.csv"
IMP = "chrome"

CLEAN = {
    "greenhouse": (
        "https://boards-api.greenhouse.io/v1/boards/{s}/jobs",
        lambda d: len(d.get("jobs", [])),
    ),
    "lever": (
        "https://api.lever.co/v0/postings/{s}?mode=json",
        lambda d: len(d) if isinstance(d, list) else 0,
    ),
    "ashby": (
        "https://api.ashbyhq.com/posting-api/job-board/{s}",
        lambda d: len(d.get("jobs", [])),
    ),
    "smartrecruiters": (
        "https://api.smartrecruiters.com/v1/companies/{s}/postings",
        lambda d: d.get("totalFound", 0) if isinstance(d, dict) else 0,
    ),
    "workable": (
        "https://apply.workable.com/api/v1/widget/accounts/{s}?details=true",
        lambda d: len(d.get("jobs", [])) if isinstance(d, dict) else 0,
    ),
    "recruitee": (
        "https://{s}.recruitee.com/api/offers/",
        lambda d: len(d.get("offers", [])) if isinstance(d, dict) else 0,
    ),
}

# ATS host fingerprints for the careers-page scan (capture group = tenant/slug where useful)
HOST = re.compile(
    r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com|(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9-]+)"
    r"|jobs\.lever\.co/([a-z0-9-]+)|jobs\.ashbyhq\.com/([a-z0-9-]+)|apply\.workable\.com/([a-z0-9-]+)"
    r"|([a-z0-9-]+)\.recruitee\.com|([a-z0-9-]+)\.darwinbox\.(?:in|com)|([a-z0-9-]+)\.keka\.com"
    r"|([a-z0-9-]+)\.zohorecruit\.(?:com|in)|([a-z0-9-]+)\.sensehq\.com|([a-z0-9-]+)\.hire\.trakstar\.com"
    r"|([a-z0-9-]+)\.skillate\.com"
    r"|([a-z0-9-]+)\.turbohire\.co|([a-z0-9-]+)\.fa\.ocs\.oraclecloud\.com"
    r"|smartrecruiters\.com/([a-z0-9-]+)",
    re.I,
)
JUNK = {"www", "careers", "jobs", "for", "en", "job", "embed", "apply", "go", "app"}


def get(url, as_json=False):
    try:
        r = cr.get(url, impersonate=IMP, timeout=12, allow_redirects=True, verify=False)
        if r.status_code >= 400:
            return None
        return r.json() if as_json else r.text
    except Exception:
        return None


def candidate_slugs(name, domain):
    cands = set()
    label = domain.split("//")[-1].split("/")[0].split(".")[0].lower()
    if label and label != "www":
        cands.add(label)
    norm = re.sub(r"[^a-z0-9]", "", name.lower())
    if norm:
        cands.add(norm)
    return {c for c in cands if len(c) >= 3}


def probe_clean(name, domain):
    hits = set()
    for s in candidate_slugs(name, domain):
        for ats, (tmpl, count) in CLEAN.items():
            d = get(tmpl.format(s=s), as_json=True)
            if d is not None:
                try:
                    if count(d) > 0:
                        hits.add(f"{ats}:{s}")
                except Exception:
                    pass
    return hits


def scan_careers(domain):
    blob = ""
    for u in (
        f"https://{domain}/",
        f"https://{domain}/careers",
        f"https://{domain}/company/careers",
    ):
        h = get(u)
        if h:
            blob += "\n" + h
    hosts = set()
    for m in HOST.finditer(blob):
        tok = next((g for g in m.groups() if g), "")
        if tok and tok.lower() not in JUNK and len(tok) >= 3:
            hosts.add(m.group(0).split("/")[0].lower())
    return hosts


def investigate(row):
    name, domain = row["name"], row["domain"]
    found = probe_clean(name, domain)
    method = "clean-api" if found else ""
    careers = scan_careers(domain)
    if careers:
        found |= careers
        method = (method + "+careers-scan").strip("+") if method else "careers-scan"
    return name, domain, sorted(found), method or "none"


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    offset = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    rows = [
        r
        for r in csv.DictReader(UNFOUND.open(encoding="utf-8"))
        if r["status"] == "un-investigated"
    ][offset : offset + n]
    print(f"investigating {len(rows)} un-investigated companies", flush=True)
    cf = OUT.open("w", newline="", encoding="utf-8")
    cw = csv.writer(cf)
    cw.writerow(["name", "domain", "found", "method"])
    hit = done = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        for fut in as_completed([ex.submit(investigate, r) for r in rows]):
            name, domain, found, method = fut.result()
            done += 1
            cw.writerow([name, domain, ";".join(found), method])
            cf.flush()
            if found:
                hit += 1
            print(
                f"  [{done}/{len(rows)}] {name} ({domain}): "
                + (", ".join(found) if found else "-"),
                flush=True,
            )
    cf.close()
    print(f"\n{hit}/{len(rows)} got an ATS -> {OUT.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    main()
