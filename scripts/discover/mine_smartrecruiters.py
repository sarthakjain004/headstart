#!/usr/bin/env python3
"""SmartRecruiters miner — harvest every Board slug SmartRecruiters ever served, from Wayback.

SmartRecruiters is path-based across four hosts, and each one leaks slugs differently:

    jobs.smartrecruiters.com/{slug}[/{jobId}-{title}]   the live board host  (298 CDX pages)
    careers.smartrecruiters.com/{slug}                  older board host, now 302s to jobs (17)
    www.smartrecruiters.com/{slug}/{jobId}-{title}      the original board path (100)
    api.smartrecruiters.com/v1/companies/{slug}/...     the posting API itself, crawled (5)

Why not ``wayback_pages.py --style path``: that helper lowercases every segment, and while the
posting API happens to be case-insensitive, the *canonical* slug is mixed-case (``Zomato1``,
``NestlePurinaPetCare``) and is what the ledger and job URLs should carry. This keeps case, and
the verify step re-reads the canonical spelling from the API's ``company.identifier``.

www is also SmartRecruiters' own marketing site, so its first path segment is mostly pages, not
Boards — RESERVED filters the obvious ones and verification settles the rest. Over-generating
candidates is cheap: the posting API is one unauthenticated GET per slug.

Writes candidates to data/wayback-ats/smartrecruiters.csv. Resumable — completed CDX pages are
recorded in data/wayback-ats/.smartrecruiters_pages_done, so a re-run skips them.

Run:  python -u scripts/discover/mine_smartrecruiters.py [workers]
"""

import csv
import re
import ssl
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
WB = ROOT / "data" / "wayback-ats"
OUT = WB / "smartrecruiters.csv"
STATE = WB / ".smartrecruiters_pages_done"
UA = "HeadStart-wayback/0.1 (ATS board discovery)"
TIMEOUT = 180
CTX = ssl._create_unverified_context()

HOSTS = [
    "jobs.smartrecruiters.com",
    "careers.smartrecruiters.com",
    "www.smartrecruiters.com",
    "smartrecruiters.com",  # the bare apex serves the same /{slug} board path as www
]
API_HOST = "api.smartrecruiters.com"

# First path segments that are SmartRecruiters' own site, not a Board.
RESERVED = {
    "a",
    "about",
    "about-us",
    "account",
    "api",
    "app",
    "assets",
    "auth",
    "blog",
    "brand",
    "careers",
    "cdn",
    "company",
    "contact",
    "content",
    "css",
    "customers",
    "dam",
    "de",
    "demo",
    "docs",
    "download",
    "downloads",
    "embed",
    "en",
    "enterprise",
    "es",
    "events",
    "favicon.ico",
    "feed",
    "fr",
    "hiring",
    "hiring-success",
    "home",
    "images",
    "img",
    "index.html",
    "integrations",
    "job",
    "job-listing",
    "jobs",
    "legal",
    "login",
    "logout",
    "marketplace",
    "media",
    "news",
    "newsroom",
    "nl",
    "oauth",
    "partners",
    "platform",
    "press",
    "pricing",
    "privacy",
    "product",
    "products",
    "recruiting",
    "resources",
    "robots.txt",
    "search",
    "security",
    "signup",
    "sitemap.xml",
    "solutions",
    "static",
    "support",
    "terms",
    "tour",
    "trust",
    "uk",
    "user",
    "users",
    "video",
    "webinar",
    "webinars",
    "web-sso",
    "wp-admin",
    "wp-content",
    "wp-includes",
    "wp-json",
    "xmlrpc.php",
}
_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,62}$")


def slug_from(url: str) -> str | None:
    """The Board slug in one archived URL, case preserved (None if the URL isn't a Board)."""
    m = re.match(r"^https?://([^/]+)(/[^?#\s]*)?", url)
    if not m:
        return None
    host, path = m.group(1).lower(), (m.group(2) or "")
    # Archived pre-HTTPS captures carry the port ("www.smartrecruiters.com:80"), which would
    # otherwise fail every host match below and silently drop the oldest Boards.
    host = host.split(":", 1)[0]
    segs = [s for s in path.split("/") if s]
    if not segs:
        return None
    if host.endswith(API_HOST):
        # /v1/companies/{slug}/postings — the slug is the segment after "companies"
        if len(segs) >= 3 and segs[1] == "companies":
            seg = urllib.parse.unquote(segs[2])
        else:
            return None
    elif any(host.endswith(h) for h in HOSTS):
        seg = urllib.parse.unquote(segs[0])
    else:
        return None
    if seg.lower() in RESERVED or not _SLUG.match(seg) or seg.lower().endswith(".php"):
        return None
    return seg


# Wayback's CDX server rate-limits hard: a burst of 6 concurrent pages earns a multi-minute 429
# ban on the egress IP (observed 2026-07-27). ADR-0026 politeness — space request *starts* across
# all workers, and back off exponentially when it does push back.
_PACE = 2.0  # min seconds between request starts, process-wide
_pace_lock = threading.Lock()
_next_start = 0.0


def _wait_turn() -> None:
    global _next_start
    with _pace_lock:
        start = max(time.monotonic(), _next_start)
        _next_start = start + _PACE
    delay = start - time.monotonic()
    if delay > 0:
        time.sleep(delay)


def get(url: str) -> str | None:
    backoff = 30.0
    for _ in range(6):
        _wait_turn()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print(f"  [429] backing off {backoff:.0f}s", flush=True)
                time.sleep(backoff)
                backoff = min(backoff * 2, 480)
            else:
                time.sleep(5)
        except Exception:
            time.sleep(5)
    return None


def num_pages(host: str) -> int:
    txt = get(
        f"https://web.archive.org/cdx/search/cdx?url={host}&matchType=prefix&showNumPages=true"
    )
    return int(txt.strip()) if txt and txt.strip().isdigit() else 0


def main():
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    WB.mkdir(parents=True, exist_ok=True)

    # seen: lowercased slug -> the casing we recorded (first mixed-case spelling wins, since
    # the canonical slug is mixed-case and an all-lower archived URL is often a normalisation).
    seen: dict[str, str] = {}
    if OUT.exists():
        with OUT.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                seen[row["tenant"].lower()] = row["tenant"]
    else:
        OUT.write_text("ats,tenant,url\n", encoding="utf-8")
    done = set()
    if STATE.exists():
        done = {ln.strip() for ln in STATE.read_text().split() if ln.strip()}

    todo = []
    for host in HOSTS + [API_HOST]:
        n = num_pages(host)
        print(f"{host}: {n} CDX pages", flush=True)
        todo += [(host, p) for p in range(n) if f"{host}:{p}" not in done]
    print(
        f"smartrecruiters: {len(todo)} pages to fetch, {len(done)} already done, "
        f"{len(seen)} slugs so far",
        flush=True,
    )

    lock = threading.Lock()
    fh = OUT.open("a", newline="", encoding="utf-8")
    w = csv.writer(fh)
    sf = STATE.open("a", encoding="utf-8")
    n_done = 0

    def fetch(item):
        host, page = item
        text = get(
            f"https://web.archive.org/cdx/search/cdx?url={host}&matchType=prefix"
            f"&fl=original&collapse=urlkey&page={page}"
        )
        if text is None:
            return item, None
        return item, [ln for ln in text.split("\n") if ln]

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(fetch, item) for item in todo]
        for fut in as_completed(futures):
            (host, page), urls = fut.result()
            if urls is None:
                print(f"  !! {host} page {page}: failed after retries", flush=True)
                continue
            with lock:
                new, rejected = 0, None
                for u in urls:
                    s = slug_from(u)
                    if not s:
                        rejected = rejected or u
                        continue
                    key = s.lower()
                    if key in seen:
                        # prefer a mixed-case spelling over an all-lowercase one
                        if seen[key].islower() and not s.islower():
                            seen[key] = s
                        continue
                    seen[key] = s
                    w.writerow(
                        ["smartrecruiters", s, f"careers.smartrecruiters.com/{s}"]
                    )
                    new += 1
                fh.flush()
                sf.write(f"{host}:{page}\n")
                sf.flush()
                n_done += 1
                note = f"  e.g. skipped {rejected}" if new == 0 and rejected else ""
                print(
                    f"  [{n_done}/{len(todo)}] {host} p{page}: "
                    f"{len(urls)} urls, +{new} new -> {len(seen)} slugs{note}",
                    flush=True,
                )
    fh.close()
    sf.close()
    print(f"DONE: {len(seen)} unique slugs in {OUT}", flush=True)


if __name__ == "__main__":
    main()
