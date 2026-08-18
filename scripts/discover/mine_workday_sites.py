#!/usr/bin/env python3
"""Enumerate a Workday tenant's careers *sites* (and resolve its data centre) from robots.txt.

A Workday board is ``{tenant}.{wdN}.myworkdayjobs.com/{site}`` — the cxs API needs the pod *and*
the site, and one tenant routinely runs several sites (External_Career_Site, campus, per-brand,
per-region), each a separate board with separate jobs. Tenant lists alone therefore under-count
boards badly.

``https://{tenant}.{wdN}.myworkdayjobs.com/robots.txt`` solves both in one GET: it is served only
by the pod actually hosting the tenant (any other pod answers 422), and it lists every public
careers site on it::

    Sitemap: https://wmeimg.wd1.myworkdayjobs.com/WMEGRP/siteMap.xml
    Sitemap: https://wmeimg.wd1.myworkdayjobs.com/160over90US/siteMap.xml
    Allow: /WMEGRP/
    Allow: /160over90US/

So one request per (tenant, pod) yields the pod verdict *and* the case-correct site list. Pods are
tried in descending order of prevalence in our pool, stopping at the first that answers — which
also recovers tenants whose recorded pod has gone stale (Workday migrates tenants between pods).

Reads its tenant list from the liveness ledger and the merged pool by default; ``--tenants FILE``
takes one bare tenant per line instead (for candidates that aren't in the pool yet). Output is
``ats,tenant,url`` — ``tenant`` lowercased ``{tenant}/{site}`` to match the ledger key, ``url``
case-preserved, so it merges cleanly with data/wayback-ats/workday.csv.

Run:  python -u scripts/discover/mine_workday_sites.py [--tenants FILE] [--out CSV] [--workers N]
"""

import argparse
import csv
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
import urllib.error
import urllib.request

from headstart.scrapers.workday import INSTANCES  # needs src on sys.path

LEDGER = ROOT / "data" / "validate" / "liveness" / "workday.csv"
POOL = ROOT / "data" / "ats-tenants-merged" / "workday.csv"
UA = "HeadStart-discovery/0.1 (workday site enumeration)"
TIMEOUT = 20
BOARD_RE = re.compile(
    r"^https?://([a-z0-9][a-z0-9-]*)\.(wd\d+)\.myworkdayjobs\.com/([^/?#]+)",
    re.IGNORECASE,
)
# robots.txt lists each public site twice; either line is enough, both are parsed for safety.
ALLOW_RE = re.compile(r"^Allow:\s*/([^/\s]+)/\s*$", re.IGNORECASE | re.MULTILINE)
SITEMAP_RE = re.compile(
    r"^Sitemap:\s*https?://[^/]+/([^/\s]+)/siteMap\.xml\s*$",
    re.IGNORECASE | re.MULTILINE,
)
# Path segments Workday serves that are not careers sites.
NOT_A_SITE = {"refreshFacet", "private", "talentcommunity", "wday", "cxs", "assets"}


class Backoff:
    """Shared per-host throttle: a 429/5xx pauses every worker briefly, then decays.

    Workday is one rate-sensitive CDN, so backing off a single request is useless — the whole
    sweep has to ease off (ADR-0026, per-IP politeness).
    """

    def __init__(self):
        self.until = 0.0
        self.lock = threading.Lock()

    def wait(self):
        while True:
            with self.lock:
                delay = self.until - time.time()
            if delay <= 0:
                return
            time.sleep(min(delay, 5))

    def trip(self, seconds=20):
        with self.lock:
            self.until = max(self.until, time.time() + seconds)


BACKOFF = Backoff()


def fetch_robots(tenant, pod):
    """(status, body) for one tenant/pod robots.txt. status None = transport failure."""
    url = f"https://{tenant}.{pod}.myworkdayjobs.com/robots.txt"
    for attempt in range(3):
        BACKOFF.wait()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                BACKOFF.trip(20 * (attempt + 1))
                continue
            return e.code, ""  # 422 (wrong pod) / 404 are real verdicts, not failures
        except Exception:  # noqa: BLE001
            time.sleep(2 * (attempt + 1))
    return None, ""


def sites_in(body):
    """Case-correct site names listed in a robots.txt body."""
    found = []
    for name in SITEMAP_RE.findall(body) + ALLOW_RE.findall(body):
        if name in NOT_A_SITE or "." in name or name in found:
            continue
        found.append(name)
    return found


def resolve(tenant, pods):
    """(pod, [sites]) for a tenant — first pod that answers 200 *and names a site*.

    A 200 alone is not proof: ``wd117`` is a catch-all that serves a generic Cloudflare
    robots.txt (``Allow: /``, no Sitemap) for *any* hostname, so it 200s for tenants that
    don't exist. Requiring a named site is what separates a real board host from that.
    """
    for pod in pods:
        status, body = fetch_robots(tenant, pod)
        if status == 200:
            sites = sites_in(body)
            if sites:
                return pod, sites
    return None, []


def known_boards():
    """(pairs, tenant->pods) from the ledger + merged pool. pairs are lowercase tenant/site."""
    pairs, pods = set(), {}
    for path in (LEDGER, POOL):
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                m = BOARD_RE.match((row.get("url") or "").strip())
                if not m:
                    continue
                tenant, pod, site = m.group(1).lower(), m.group(2), m.group(3)
                pairs.add(f"{tenant}/{site.lower()}")
                pods.setdefault(tenant, [])
                if pod not in pods[tenant]:
                    pods[tenant].append(pod)
    return pairs, pods


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenants", help="file of bare tenant names, one per line")
    ap.add_argument("--out", default="data/wayback-ats/workday.csv")
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()

    known, ledger_pods = known_boards()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    state = out.with_name(f".{out.stem}_sites_done")

    if args.tenants:
        tenants = [
            t.strip().lower()
            for t in Path(args.tenants).read_text().splitlines()
            if t.strip() and not t.startswith("#")
        ]
    else:
        tenants = sorted(ledger_pods)

    # Carry forward whatever the output already holds, so a re-run never duplicates a row.
    if out.exists():
        with out.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                known.add((row.get("tenant") or "").strip().lower())
    done = set(state.read_text().split()) if state.exists() else set()
    todo = [t for t in dict.fromkeys(tenants) if t not in done]
    print(
        f"workday sites: {len(todo)} tenants to enumerate "
        f"({len(done)} done, {len(known)} boards known)",
        flush=True,
    )

    lock = threading.Lock()
    new_file = not out.exists()
    fh = out.open("a", newline="", encoding="utf-8")
    writer = csv.writer(fh)
    if new_file:
        writer.writerow(["ats", "tenant", "url"])
    sf = state.open("a", encoding="utf-8")
    stats = {"done": 0, "new": 0, "resolved": 0, "migrated": 0}

    def work(tenant):
        # Prevalence order, ledger pods first — the recorded pod is right ~99% of the time, and
        # the sweep behind it recovers the tenants that have since migrated.
        hinted = ledger_pods.get(tenant, [])
        pods = hinted + [p for p in INSTANCES if p not in hinted]
        pod, sites = resolve(tenant, pods)
        with lock:
            stats["done"] += 1
            if pod:
                stats["resolved"] += 1
                if hinted and pod != hinted[0]:
                    stats["migrated"] += 1
                    print(f"  migrated: {tenant} {hinted[0]} -> {pod}", flush=True)
            for site in sites:
                key = f"{tenant}/{site.lower()}"
                if key in known:
                    continue
                known.add(key)
                stats["new"] += 1
                writer.writerow(
                    [
                        "workday",
                        key,
                        f"https://{tenant}.{pod}.myworkdayjobs.com/{site}",
                    ]
                )
                print(f"  NEW {key}  (pod {pod})", flush=True)
            fh.flush()
            sf.write(f"{tenant}\n")
            sf.flush()
            if stats["done"] % 100 == 0:
                print(
                    f"  [{stats['done']}/{len(todo)}] resolved={stats['resolved']} "
                    f"new={stats['new']} migrated={stats['migrated']}",
                    flush=True,
                )

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for fut in as_completed([ex.submit(work, t) for t in todo]):
            fut.result()
    fh.close()
    sf.close()
    print(
        f"DONE: {stats['new']} new boards from {stats['resolved']}/{len(todo)} "
        f"resolved tenants ({stats['migrated']} migrated pods) -> {out}",
        flush=True,
    )


if __name__ == "__main__":
    main()
