"""Mine Common Crawl for India ATS/HRMS tenant subdomains, straight from the source.

  * urllib can't reach CC here; curl (via Cloudflare WARP) can -> all HTTP via curl.
  * Robust to CC's aggressive rate-limiting:
      - index list cached to disk (data/discover/cc_index_cache.txt) so relaunches never depend on a
        live collinfo fetch (that empty-fetch -> json crash was the real killer);
      - curl --fail: a 200 with empty body is a real no-match (ok), a block/429/timeout is
        a failure (not data);
      - an index is marked done ONLY when every query succeeded, so a throttled index is
        retried later, never silently recorded as empty;
      - on throttle the run bails fast and exits cleanly so the wrapper can wait it out.
  * Resumable + sequential + paced. Run with `python -u`.

Usage: python -u scripts/discover/cc_miner.py [count|all]   (run from repo root; default: all)
"""

import collections
import csv
import json
import os
import re
import subprocess
import sys
import time

COUNT = sys.argv[1] if len(sys.argv) > 1 else "all"
CSV = "data/discover/india_ats_tenants.csv"
DONE = "data/discover/cc_miner_checkpoint.txt"
INDEX_CACHE = "data/discover/cc_index_cache.txt"
CC_PING = "https://index.commoncrawl.org/collinfo.json"

DOMAINS = {
    "zoho": ["zohorecruit.in", "zohorecruit.com", "zohorecruit.eu"],
    "freshteam": ["freshteam.com"],
    "darwinbox": ["darwinbox.in", "darwinbox.com"],
    "keka": ["keka.com"],
    "greythr": ["greythr.com"],
    "peoplestrong": ["peoplestrong.com"],
    "jobsoid": ["jobsoid.com"],
    "ripplehire": ["ripplehire.com"],
    "turbohire": ["turbohire.co"],
    "qandle": ["qandle.com"],
    "beehive": ["beehivehcm.com"],
    "workable": ["workable.com"],
    "recruitee": ["recruitee.com"],
}
INFRA = {
    "www",
    "app",
    "apps",
    "api",
    "help",
    "support",
    "static",
    "cdn",
    "blog",
    "login",
    "accounts",
    "account",
    "status",
    "docs",
    "mail",
    "go",
    "info",
    "assets",
    "img",
    "images",
    "media",
    "hrms",
    "ats",
    "secure",
    "portal",
    "careers",
    "career",
    "jobs",
    "dash",
    "embed",
    "content",
    "commune",
    "cloud",
    "ess",
    "selfservice",
    "mservices",
    "qa",
    "demo",
    "test",
    "staging",
    "dev",
    "sandbox",
    "uat",
    "affiliate",
    "partners",
    "partner",
    "community",
    "developer",
    "developers",
    "chatbot",
    "featurerequest",
    "certification",
    "attend",
    "events",
    "customers",
    "ww1",
    "m",
}


def _run(cmd, timeout=90):
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except Exception:
        return None


def curl(url, attempts=6):
    """Return (body, ok). ok=False => real block/throttle (429/503/5xx/timeout). CC's CDX
    API returns 404 {"message":"No Captures found"} for a domain absent from a crawl — that
    is a legitimate empty result, so 404 returns ("", True), not a throttle."""
    for a in range(attempts):
        p = _run(
            [
                "curl",
                "-s",
                "-w",
                "\n%{http_code}",
                "--connect-timeout",
                "10",
                "-m",
                "60",
                url,
            ]
        )
        if p is not None and p.returncode == 0:
            out = p.stdout
            nl = out.rfind("\n")
            code = out[nl + 1 :].strip() if nl >= 0 else out.strip()
            body = out[:nl] if nl >= 0 else ""
            if code == "200":
                time.sleep(0.3)  # gentle pacing
                return body, True
            if code == "404":
                time.sleep(0.3)
                return "", True  # legitimate no-match for this domain in this crawl
            # 403/429/5xx => real block; fall through to retry
        time.sleep(3)
    return "", False


def index_work(cdx):
    """Returns (tenants_by_ats, ok). Aborts at the first failed query so a throttled
    index is detected in seconds rather than grinding through every domain."""
    local = collections.defaultdict(set)
    for ats, ds in DOMAINS.items():
        for d in ds:
            body, ok = curl(
                f"{cdx}?url={d}&matchType=domain&output=json&fl=url&limit=10000"
            )
            if not ok:
                return local, False
            for line in body.splitlines():
                try:
                    u = json.loads(line)["url"]
                except Exception:
                    continue
                m = re.match(r"https?://([^/]+)", u)
                if m and m.group(1).lower().endswith("." + d):
                    label = m.group(1).lower()[: -(len(d) + 1)].split(".")[0]
                    if label and label not in INFRA:
                        local[ats].add(m.group(1).lower())
    return local, True


def write_csv(tenants):
    rows = sorted((ats, h) for ats, hosts in tenants.items() for h in hosts)
    with open(CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ats", "host"])
        w.writerows(rows)
    return len(rows)


def load_existing():
    tenants = collections.defaultdict(set)
    if os.path.exists(CSV):
        with open(CSV, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                tenants[row["ats"]].add(row["host"])
    done = set()
    if os.path.exists(DONE):
        with open(DONE, encoding="utf-8") as f:
            done = {ln.strip() for ln in f if ln.strip()}
    return tenants, done


def load_indexes():
    if os.path.exists(INDEX_CACHE):
        cached = [
            ln.strip() for ln in open(INDEX_CACHE, encoding="utf-8") if ln.strip()
        ]
        if cached:
            return cached
    body, ok = curl(CC_PING)
    if not ok or not body.strip():
        print("[miner] collinfo unreachable -> exiting (wrapper retries)", flush=True)
        sys.exit(3)
    indexes = [e["cdx-api"] for e in json.loads(body)]
    with open(INDEX_CACHE, "w", encoding="utf-8") as f:
        f.write("\n".join(indexes))
    return indexes


def main():
    os.makedirs("data", exist_ok=True)
    indexes = load_indexes()
    if COUNT != "all":
        indexes = indexes[: int(COUNT)]
    tenants, done = load_existing()
    print(
        f"mining {len(indexes)} indexes | {len(done)} done | "
        f"{sum(len(v) for v in tenants.values())} tenants known",
        flush=True,
    )

    for i, cdx in enumerate(indexes, 1):
        key = cdx.split("/")[-1]
        if key in done:
            continue
        local, ok = index_work(cdx)
        if not ok:
            print(
                f"[{i}/{len(indexes)}] {key.replace('-index', '')} INCOMPLETE (throttled) "
                f"-> exiting to wait",
                flush=True,
            )
            return  # clean exit; wrapper waits out the block then relaunches
        for ats, hosts in local.items():
            tenants[ats] |= hosts
        done.add(key)
        write_csv(tenants)
        with open(DONE, "w", encoding="utf-8") as f:
            f.write("\n".join(sorted(done)))
        counts = " ".join(f"{a}={len(tenants[a])}" for a in DOMAINS)
        print(
            f"[{i}/{len(indexes)}] {key.replace('-index', '')} | {counts}", flush=True
        )

    write_csv(tenants)
    print(
        f"DONE. distinct tenant hosts={sum(len(v) for v in tenants.values())} -> {CSV}",
        flush=True,
    )
    for ats in DOMAINS:
        if tenants[ats]:
            print(f"  {ats}: {len(tenants[ats])}", flush=True)


if __name__ == "__main__":
    main()
