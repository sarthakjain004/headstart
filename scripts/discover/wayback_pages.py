#!/usr/bin/env python3
"""Page-based Wayback harvester for one dense ATS domain (e.g. Zoho).

The CDX index is split into N pages (see ?showNumPages=true). Unlike the flat limit (stuck on
early tenants) or filters/host-collapse (which time out), every page is directly addressable
with &page=K, so we fetch ALL pages CONCURRENTLY, extract hosts, and dedup to the full tenant
set in one bounded pass.

Writes new tenants to data/wayback-ats/{ats}.csv as pages complete. Resumable: completed page
numbers are recorded in data/wayback-ats/.{ats}_pages_done, so re-running skips finished pages.

Usage:  python scripts/discover/wayback_pages.py [ats] [domain] [style] [workers]
        python scripts/discover/wayback_pages.py zoho zohorecruit.com sub
"""

import csv
import re
import socket
import ssl
import sys
import threading
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
WB = ROOT / "data" / "wayback-ats"
TIMEOUT = 120
UA = "HeadStart-wayback/0.1 (ATS tenant discovery)"
CTX = ssl._create_unverified_context()
socket.setdefaulttimeout(TIMEOUT)

INFRA = {
    "www",
    "app",
    "apps",
    "blog",
    "support",
    "help",
    "api",
    "status",
    "smtp",
    "mail",
    "email",
    "cdn",
    "assets",
    "static",
    "go",
    "info",
    "docs",
    "careers",
    "jobs",
    "admin",
    "portal",
    "test",
    "staging",
    "dev",
    "demo",
    "about",
    "home",
    "login",
    "secure",
    "my",
}


def valid(label):
    return bool(re.match(r"^[a-z0-9][a-z0-9-]{1,62}$", label)) and label not in INFRA


def extract(url, domain, style):
    m = re.match(r"^https?://([^/]+)(/[^?#\s]*)?", url)
    if not m:
        return None
    host, path = m.group(1).lower(), (m.group(2) or "")
    if style == "sub" and host.endswith("." + domain):
        label = host[: -len("." + domain)]
        if "." not in label and valid(label):
            return label
    if style == "path" and host == domain and path:
        seg = path.lstrip("/").split("/")[0].split("?")[0].lower()
        if valid(seg) and seg != "embed":
            return seg
    if style == "workday" and host.endswith("." + domain):
        label = host.split(".")[0]
        if valid(label):
            return label
    return None


def get(url):
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            pass
    return None


def main():
    ats = sys.argv[1] if len(sys.argv) > 1 else "zoho"
    domain = sys.argv[2] if len(sys.argv) > 2 else "zohorecruit.com"
    style = sys.argv[3] if len(sys.argv) > 3 else "sub"
    workers = int(sys.argv[4]) if len(sys.argv) > 4 else 10
    cdx = f"https://web.archive.org/cdx/search/cdx?url={urllib.parse.quote(domain)}&matchType=domain"
    base = (
        cdx + "&fl=original&collapse=urlkey"
    )  # page fetches; showNumPages needs the clean url

    WB.mkdir(parents=True, exist_ok=True)
    out = WB / f"{ats}.csv"
    state = WB / f".{ats}_pages_done"

    npages_txt = get(cdx + "&showNumPages=true")
    if not npages_txt or not npages_txt.strip().isdigit():
        print(f"could not get page count: {npages_txt!r}")
        return
    npages = int(npages_txt.strip())

    seen = set()
    if out.exists():
        with out.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                seen.add(row["tenant"])
    else:
        out.write_text("ats,tenant,url\n", encoding="utf-8")
    done = set()
    if state.exists():
        done = {int(x) for x in state.read_text().split() if x.strip().isdigit()}
    todo = [p for p in range(npages) if p not in done]
    print(
        f"{ats}: {npages} pages, {len(done)} done, {len(todo)} to fetch, "
        f"{len(seen)} tenants so far",
        flush=True,
    )

    lock = threading.Lock()
    f = out.open("a", newline="", encoding="utf-8")
    w = csv.writer(f)
    sf = state.open("a", encoding="utf-8")
    counter = {"n": 0}

    def do(page):
        text = get(f"{base}&page={page}")
        if text is None:
            return None
        urls = [ln for ln in text.split("\n") if ln]
        with lock:
            new = 0
            for u in urls:
                t = extract(u, domain, style)
                if t and t not in seen:
                    seen.add(t)
                    url = (
                        f"https://{t}.{domain}"
                        if style != "path"
                        else f"https://{domain}/{t}"
                    )
                    w.writerow([ats, t, url])
                    new += 1
            f.flush()
            sf.write(f"{page}\n")
            sf.flush()
            counter["n"] += 1
            if counter["n"] % 25 == 0:
                print(
                    f"  {counter['n']}/{len(todo)} pages, {len(seen)} tenants",
                    flush=True,
                )
            return new

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for fut in as_completed([ex.submit(do, p) for p in todo]):
            fut.result()
    f.close()
    sf.close()
    print(
        f"DONE: {len(seen)} unique {ats} tenants in data/wayback-ats/{ats}.csv",
        flush=True,
    )


if __name__ == "__main__":
    main()
