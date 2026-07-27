#!/usr/bin/env python3
"""Wayback miner for Workday boards.

Workday boards are {tenant}.{wdN}.myworkdayjobs.com/{site} — the data center (wdN) AND the
careers-site name both matter (the cxs API needs both), so we extract the full (host, site)
careers URL, not just a tenant label. Page-mines myworkdayjobs.com from Wayback (concurrent,
resumable) and writes boards to data/wayback-ats/workday.csv as
  tenant = "{tenant}/{site}"   (matches the jobhive slug format, so the merge unions cleanly)
  url    = "https://{host}/{site}"

Run:  python scripts/discover/mine_workday.py [workers]
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
OUT = WB / "workday.csv"
STATE = WB / ".workday_pages_done"
TIMEOUT = 120
UA = "HeadStart-wayback/0.1 (workday discovery)"
CTX = ssl._create_unverified_context()
socket.setdefaulttimeout(TIMEOUT)

HOST_RE = re.compile(r"^([a-z0-9][a-z0-9-]*)\.(wd\d+)\.myworkdayjobs\.com$")
LOCALE_RE = re.compile(r"^[a-z]{2}[-_][a-z]{2}$", re.I)
SKIP_SITE = {"job", "wday", "cxs", "api", "assets"}


def get(url):
    for _ in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            pass
    return None


def extract(url):
    """Return ('tenant/site', 'https://host/site') or None."""
    m = re.match(r"^https?://([^/]+)(/[^?#\s]*)?", url)
    if not m:
        return None
    host = m.group(1).lower()
    hm = HOST_RE.match(host)
    if not hm:
        return None
    tenant = hm.group(1)
    if tenant.startswith("2f"):  # %2f URL-encoding artifact (e.g. "2fasmglobal")
        return None
    segs = [s for s in (m.group(2) or "").split("/") if s]
    if segs and segs[0].lower() == "wday":
        # careers API is /wday/cxs/{tenant}/{site}/jobs|job — require the trailing jobs/job so
        # we don't grab i18n cxs endpoints like /wday/cxs/{tenant}/videoLabels (no /jobs).
        site = (
            segs[3]
            if len(segs) >= 5
            and segs[1].lower() == "cxs"
            and segs[4].lower() in ("jobs", "job")
            else None
        )
    else:
        if segs and LOCALE_RE.match(segs[0]):  # drop a leading /{locale}
            segs = segs[1:]
        # require a /{site}/job/... path: the "job" segment confirms a real careers site,
        # not a static/asset path (videoLabels, cdn-cgi, ...) which share the same position.
        site = segs[0] if len(segs) >= 2 and segs[1].lower() == "job" else None
    if (
        not site or "." in site or site.lower() in SKIP_SITE
    ):  # "." rejects favicon.ico, robots.txt, etc.
        return None
    # The slug is lowercased (the url keeps the site's true casing): merge_tenants lowercases the
    # tenant column, and the liveness ledger keys on it verbatim. Emitting "acme/External_Careers"
    # beside an existing "acme/external_careers" makes the checker treat one board as two and
    # re-probe it into a duplicate row — the cxs API accepts either casing, so both read live.
    return f"{tenant}/{site.lower()}", f"https://{host}/{site}"


def main():
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    cdx = (
        "https://web.archive.org/cdx/search/cdx?url=myworkdayjobs.com&matchType=domain"
    )
    base = cdx + "&fl=original&collapse=urlkey"

    npages_txt = get(cdx + "&showNumPages=true")
    if not npages_txt or not npages_txt.strip().isdigit():
        print(f"could not get page count: {npages_txt!r}")
        return
    npages = int(npages_txt.strip())

    boards = {}  # slug -> url
    if OUT.exists():
        with OUT.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                boards[r["tenant"]] = r["url"]
    done = set()
    if STATE.exists():
        done = {int(x) for x in STATE.read_text().split() if x.strip().isdigit()}
    todo = [p for p in range(npages) if p not in done]
    print(
        f"workday: {npages} pages, {len(done)} done, {len(todo)} to fetch, "
        f"{len(boards)} boards",
        flush=True,
    )

    lock = threading.Lock()
    new_file = not OUT.exists()
    f = OUT.open("a", newline="", encoding="utf-8")
    w = csv.writer(f)
    if new_file:
        w.writerow(["ats", "tenant", "url"])
    sf = STATE.open("a", encoding="utf-8")
    counter = {"n": 0}

    def do(page):
        text = get(f"{base}&page={page}")
        if text is None:
            return None
        found = [r for r in (extract(u) for u in text.split("\n") if u) if r]
        with lock:
            for slug, url in found:
                if slug not in boards:
                    boards[slug] = url
                    w.writerow(["workday", slug, url])
            f.flush()
            sf.write(f"{page}\n")
            sf.flush()
            counter["n"] += 1
            if counter["n"] % 25 == 0:
                print(
                    f"  {counter['n']}/{len(todo)} pages, {len(boards)} boards",
                    flush=True,
                )

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for fut in as_completed([ex.submit(do, p) for p in todo]):
            fut.result()
    f.close()
    sf.close()
    print(
        f"DONE: {len(boards)} unique workday boards in data/wayback-ats/workday.csv",
        flush=True,
    )


if __name__ == "__main__":
    main()
