#!/usr/bin/env python3
"""Resolve a company domain to its Workday board by following its careers page.

Guessing a Workday tenant from a company name is expensive and imprecise: the pod is not
derivable, so a wrong guess costs one request per data centre (~18) to disprove. A company's own
careers page settles it in one or two: enterprises put the board behind ``careers.{domain}`` /
``{domain}/careers``, which either **redirects** to ``{tenant}.{wdN}.myworkdayjobs.com/{site}`` or
**links** to it from the page body. Either way the tenant, pod and site all arrive together and
correct — no guessing.

Input is ``name,domain`` CSV (or one bare domain per line). Output is ``ats,tenant,url`` rows,
matching the miner contract so it merges with data/wayback-ats/workday.csv. Boards found here are
still candidates — run the liveness checker to confirm against the cxs endpoint.

Run:  python -u scripts/resolve/workday_careers_link.py --input FILE [--out CSV] [--workers N]
"""

import argparse
import csv
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
UA = "Mozilla/5.0 (compatible; HeadStart-discovery/0.1; careers-board discovery)"
TIMEOUT = 20
BOARD_RE = re.compile(
    r"([a-z0-9][a-z0-9-]*)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_.\-]+)",
    re.IGNORECASE,
)
# Path segments Workday serves that are not careers sites.
NOT_A_SITE = {
    "wday",
    "cxs",
    "job",
    "jobs",
    "robots",
    "sitemap",
    "assets",
    "api",
    "refreshfacet",
    "talentcommunity",
    "images",
    "static",
    "favicon",
    "details",
}
# Tried in order; the first that yields a board wins. Most enterprises use one of the first two.
PATTERNS = ("https://careers.{d}/", "https://{d}/careers", "https://jobs.{d}/")


def fetch(url):
    """Page text (following redirects), or '' on any failure. Final URL is prepended so a
    redirect *to* a board is caught even when the body is JS-rendered."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read(600_000).decode("utf-8", "replace")
            return f"{r.url}\n{body}"
    except urllib.error.HTTPError as e:
        try:
            return f"{e.url}\n" + e.read(200_000).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return ""
    except Exception:  # noqa: BLE001
        return ""


def boards_in(text):
    """Distinct (tenant, pod, site) triples named in a page."""
    out = []
    for m in BOARD_RE.finditer(text):
        tenant, pod, site = m.group(1).lower(), m.group(2), m.group(3).strip(".")
        if site.lower() in NOT_A_SITE or "." in site or len(site) < 2:
            continue
        triple = (tenant, pod, site)
        if triple not in out:
            out.append(triple)
    return out


def known_pairs():
    pairs = set()
    for p in [
        ROOT / "data/validate/liveness/workday.csv",
        ROOT / "data/ats-tenants-merged/workday.csv",
        ROOT / "data/wayback-ats/workday.csv",
        ROOT / "experiment/ats-gap-workday/artifacts/workday_sites.csv",
    ]:
        if not p.exists():
            continue
        for r in csv.DictReader(p.open(encoding="utf-8", errors="replace")):
            t = (r.get("tenant") or "").strip().lower()
            if "/" in t and not t.startswith("http"):
                pairs.add(t)
            m = BOARD_RE.search(r.get("url") or "")
            if m:
                pairs.add(f"{m.group(1).lower()}/{m.group(3).lower()}")
    return pairs


def read_input(path):
    """[(name, domain)] from a name,domain CSV or a bare-domain list."""
    rows, text = [], Path(path).read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        domain = next((p for p in parts if "." in p and " " not in p), None)
        if not domain:
            continue
        domain = re.sub(r"^https?://", "", domain).strip("/").lower()
        if domain in ("domain", "website"):
            continue
        rows.append((parts[0] if len(parts) > 1 else domain, domain))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", default="data/wayback-ats/workday.csv")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    companies = read_input(args.input)
    known = known_pairs()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        for r in csv.DictReader(out.open(encoding="utf-8")):
            known.add((r.get("tenant") or "").strip().lower())
    state = out.with_name(f".{out.stem}_careers_done")
    done = set(state.read_text().split()) if state.exists() else set()
    todo = [(n, d) for n, d in companies if d not in done]
    print(
        f"careers-link: {len(todo)} domains ({len(done)} done, {len(known)} boards known)",
        flush=True,
    )

    lock = threading.Lock()
    new_file = not out.exists()
    fh = out.open("a", newline="", encoding="utf-8")
    writer = csv.writer(fh)
    if new_file:
        writer.writerow(["ats", "tenant", "url"])
    sf = state.open("a", encoding="utf-8")
    stats = {"done": 0, "hit": 0, "new": 0}

    def work(item):
        name, domain = item
        found = []
        for pattern in PATTERNS:
            found = boards_in(fetch(pattern.format(d=domain)))
            if found:
                break
            time.sleep(0.3)
        with lock:
            stats["done"] += 1
            if found:
                stats["hit"] += 1
            for tenant, pod, site in found:
                key = f"{tenant}/{site.lower()}"
                if key in known:
                    continue
                known.add(key)
                stats["new"] += 1
                writer.writerow(
                    ["workday", key, f"https://{tenant}.{pod}.myworkdayjobs.com/{site}"]
                )
                print(f"  NEW {key}  (pod {pod})  <- {name} [{domain}]", flush=True)
            fh.flush()
            sf.write(f"{domain}\n")
            sf.flush()
            if stats["done"] % 50 == 0:
                print(
                    f"  [{stats['done']}/{len(todo)}] workday-hits={stats['hit']} "
                    f"new={stats['new']}",
                    flush=True,
                )

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for fut in as_completed([ex.submit(work, c) for c in todo]):
            fut.result()
    fh.close()
    sf.close()
    print(
        f"DONE: {stats['new']} new boards, {stats['hit']}/{len(todo)} domains on Workday -> {out}",
        flush=True,
    )


if __name__ == "__main__":
    main()
