#!/usr/bin/env python3
"""Fully-paginated Wayback harvester for one dense ATS domain (e.g. Zoho).

The flat-limit feeder can't cover domains Wayback crawled deep-but-narrow (thousands of pages
per tenant). This walks the ENTIRE CDX result set using resume-key pagination and appends
newly-found unique tenants to data/wayback-ats/{ats}.csv after every page.

Resumable: the cursor is saved to data/wayback-ats/.{ats}_resume after each page, so you can
stop (Ctrl+C) any time and re-run to continue. Dedups against tenants already in the CSV.

Usage:  python scripts/discover/wayback_paginate.py [ats] [domain] [style] [max_pages]
        python scripts/discover/wayback_paginate.py zoho zohorecruit.com sub
        python scripts/discover/wayback_paginate.py zoho zohorecruit.com sub 5   # just 5 pages (test)
"""
import csv
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
WB = ROOT / "data" / "wayback-ats"
PAGE = 15000          # urls per CDX page
SLEEP = 1.0           # politeness between pages (seconds)
TIMEOUT = 120
UA = "HeadStart-wayback/0.1 (ATS tenant discovery)"
CTX = ssl._create_unverified_context()
socket.setdefaulttimeout(TIMEOUT)

INFRA = {"www", "app", "apps", "blog", "support", "help", "api", "status", "smtp", "mail",
         "email", "cdn", "assets", "static", "go", "info", "docs", "careers", "jobs", "admin",
         "portal", "test", "staging", "dev", "demo", "about", "home", "login", "secure", "my"}


def valid(label):
    return bool(re.match(r"^[a-z0-9][a-z0-9-]{1,62}$", label)) and label not in INFRA


def extract(url, domain, style):
    m = re.match(r"^https?://([^/]+)(/[^?#\s]*)?", url)
    if not m:
        return None
    host = m.group(1).lower()
    path = m.group(2) or ""
    if style == "sub":
        suffix = "." + domain
        if host.endswith(suffix):
            label = host[: -len(suffix)]
            if "." not in label and valid(label):
                return label
    elif style == "path":
        if host == domain and path:
            seg = path.lstrip("/").split("/")[0].split("?")[0].lower()
            if valid(seg) and seg != "embed":
                return seg
    elif style == "workday":
        if host.endswith("." + domain):
            label = host.split(".")[0]
            if valid(label):
                return label
    return None


def fetch_page(domain, resume):
    url = (f"https://web.archive.org/cdx/search/cdx?url={urllib.parse.quote(domain)}"
           f"&matchType=domain&fl=original&collapse=urlkey&limit={PAGE}&showResumeKey=true")
    if resume:
        url += "&resumeKey=" + urllib.parse.quote(resume, safe="")
    for attempt in range(1, 5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
                text = r.read().decode("utf-8", "replace")
            lines = text.split("\n")
            while lines and lines[-1] == "":
                lines.pop()
            nxt = None
            if len(lines) >= 2 and lines[-2] == "":     # blank line then resume key
                nxt = lines[-1].strip() or None
                lines = lines[:-2]
            return [ln for ln in lines if ln], nxt
        except Exception as e:
            print(f"  fetch attempt {attempt} failed: {e}", flush=True)
            time.sleep(5 * attempt)
    return None, resume   # failed; caller stops, state preserved


def main():
    ats = sys.argv[1] if len(sys.argv) > 1 else "zoho"
    domain = sys.argv[2] if len(sys.argv) > 2 else "zohorecruit.com"
    style = sys.argv[3] if len(sys.argv) > 3 else "sub"
    max_pages = int(sys.argv[4]) if len(sys.argv) > 4 else 0   # 0 = until end

    WB.mkdir(parents=True, exist_ok=True)
    out = WB / f"{ats}.csv"
    state = WB / f".{ats}_resume"

    seen = set()
    if out.exists():
        with out.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                seen.add(row["tenant"])
    else:
        out.write_text("ats,tenant,url\n", encoding="utf-8")
    resume = state.read_text(encoding="utf-8").strip() if state.exists() else ""
    print(f"start: {ats} ({domain}, {style}) | already have {len(seen)} tenants"
          + (" | resuming" if resume else ""), flush=True)

    pages = total = 0
    f = out.open("a", newline="", encoding="utf-8")
    w = csv.writer(f)
    try:
        while True:
            urls, nxt = fetch_page(domain, resume)
            if urls is None:
                print("fetch failed repeatedly — cursor saved, re-run to resume", flush=True)
                break
            pages += 1
            total += len(urls)
            new = 0
            for u in urls:
                t = extract(u, domain, style)
                if t and t not in seen:
                    seen.add(t)
                    w.writerow([ats, t, f"https://{t}.{domain}" if style != "path"
                                else f"https://{domain}/{t}"])
                    new += 1
            f.flush()
            print(f"page {pages}: scanned {len(urls)} urls, +{new} new "
                  f"(tenants={len(seen)}, urls_total={total})", flush=True)
            if nxt and (max_pages == 0 or pages < max_pages):
                resume = nxt
                state.write_text(resume, encoding="utf-8")
                time.sleep(SLEEP)
            else:
                if not nxt:
                    print("reached end of results", flush=True)
                    state.unlink(missing_ok=True)
                else:
                    print(f"stopped at max_pages={max_pages} (cursor saved)", flush=True)
                break
    finally:
        f.close()
    print(f"DONE: {len(seen)} unique {ats} tenants in data/wayback-ats/{ats}.csv", flush=True)


if __name__ == "__main__":
    main()
