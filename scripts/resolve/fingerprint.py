#!/usr/bin/env python3
"""Embed / custom-domain ATS fingerprinter (Phase 2 of the JobPosting/JSON-LD plan).

For each company it fetches the homepage + careers page(s) and scans the RAW HTML for an
embedded ATS fingerprint (an embed script, a board link, or an API call) -> (ats, token).
This catches boards that URL-crawling misses because the company embeds the board on its own
site or uses a custom domain (the Greenhouse address never appears as a crawlable URL).

Usage:  python scripts/resolve/fingerprint.py [n]   # first n companies from config/seed_india.csv
"""

import asyncio
import csv
import json
import re
import sys
from pathlib import Path

from curl_cffi.requests import AsyncSession

ROOT = Path(__file__).resolve().parent.parent.parent
SEED = ROOT / "config" / "seed_india.csv"
FETCH_DEADLINE = 10  # hard wall-clock cap per request
COMPANY_BUDGET = (
    60  # hard cap per company (asyncio.wait_for), so nothing stalls the batch
)
# Keep concurrency modest: each in-flight company also fetches its own (often heavy SPA) domain,
# so too many at once saturates the link and slows every request until companies blow the budget
# and get cancelled (empty). HTTP/2 multiplexes the shared ATS-API hosts; the unique company
# domains are the bottleneck, so ~10 is the sweet spot (24 regressed 86 -> 60 hits).
CONCURRENCY = 10
# curl_cffi impersonates a real browser's TLS/JA3 + HTTP/2 fingerprint, so Cloudflare/Akamai bot
# walls that 403 plain urllib (meesho.com, lenskart.com — block persists even with browser
# headers, since it's TLS-fingerprint based) are passed as a normal browser would. One shared
# AsyncSession means concurrent requests to the same host (all greenhouse probes, all lever
# probes, ...) ride one HTTP/2 connection as parallel streams instead of one TCP+TLS per call.
IMPERSONATE = "chrome"

# token values that are never a real ATS slug (incl. provider-infra / marketing subdomains)
BLOCK = {
    "embed",
    "job_board",
    "js",
    "jobs",
    "job",
    "board",
    "boards",
    "api",
    "v0",
    "v1",
    "postings",
    "posting-api",
    "www",
    "careers",
    "career",
    "en",
    "content",
    "static",
    "assets",
    "for",
    "apply",
    "widget",
    "client",
    "public",
    "search",
    "css",
    "images",
    "app",
    "help",
    "blog",
    "support",
    "docs",
    "status",
    "mail",
    "portal",
    "secure",
    "login",
    "auth",
    "home",
    "info",
    "go",
    # provider marketing/infra subdomains seen self-matching (keka.com, darwinbox.com, ...)
    "signup",
    "academy",
    "dbx",
    "explore",
    "newsroom",
    "hr",
    "c",
    "partners",
    "developers",
    "community",
    "events",
    "demo",
    "resources",
    "pricing",
    "about",
    "contact",
    "product",
    "products",
    "news",
    "get",
    "try",
    "marketing",
    "sales",
}

# Each subdomain-tier ATS's own base domains — used to drop self-referential matches (scanning
# keka.com's site finds hr.keka.com etc., which are infra, not a tenant board).
PROVIDER_DOMAINS = {
    "greenhouse": {"greenhouse.io"},
    "lever": {"lever.co"},
    "ashby": {"ashbyhq.com"},
    "zoho": {"zohorecruit.com", "zohorecruit.eu", "zohorecruit.in", "zohorecruit.ca"},
    "recruitee": {"recruitee.com"},
    "workable": {"workable.com"},
    "darwinbox": {"darwinbox.in", "darwinbox.com"},
    "keka": {"keka.com"},
    "qandle": {"qandle.com"},
    "ripplehire": {"ripplehire.com"},
    "turbohire": {"turbohire.co"},
}


def reg_domain(domain):
    """Crude registered domain: the last two labels of the host."""
    parts = domain.lower().split("//")[-1].split("/")[0].split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else domain.lower()


PATTERNS = {
    "greenhouse": [
        r'(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/embed/job_board[^"\'\s]*?[?&]for=([a-zA-Z0-9_-]+)',
        r"boards-api(?:-eu)?\.greenhouse\.io/v1/boards/([a-zA-Z0-9_-]+)",
        r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/([a-zA-Z0-9_-]+)",
    ],
    "lever": [
        r"api\.lever\.co/v0/postings/([a-zA-Z0-9_-]+)",
        r"jobs\.lever\.co/([a-zA-Z0-9_-]+)",
    ],
    "ashby": [
        r"api\.ashbyhq\.com/posting-api/job-board/([a-zA-Z0-9_-]+)",
        r"jobs\.ashbyhq\.com/([a-zA-Z0-9_-]+)",
    ],
    "zoho": [r"([a-z0-9][a-z0-9-]*)\.zohorecruit\.(?:com|eu|in|ca)"],
    "recruitee": [r"([a-z0-9][a-z0-9-]*)\.recruitee\.com"],
    "workable": [r"apply\.workable\.com/([a-zA-Z0-9_-]+)"],
    # India subdomain tier — same providers the CC/Wayback miners cover; tenant = subdomain
    "darwinbox": [r"([a-z0-9][a-z0-9-]*)\.darwinbox\.(?:in|com)"],
    "keka": [r"([a-z0-9][a-z0-9-]*)\.keka\.com"],
    "qandle": [r"([a-z0-9][a-z0-9-]*)\.qandle\.com"],
    "ripplehire": [r"([a-z0-9][a-z0-9-]*)\.ripplehire\.com"],
    "turbohire": [r"([a-z0-9][a-z0-9-]*)\.turbohire\.co"],
}
WORKDAY = re.compile(
    r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[a-z]{2}/)?([a-zA-Z0-9_-]+)"
)


def detect(html):
    hits = set()
    for ats, pats in PATTERNS.items():
        for p in pats:
            for m in re.finditer(p, html, re.I):
                tok = m.group(1)
                # require len 3-60: a 1-2 char token is almost always garbage from a minified
                # JS path (e.g. a stray `apply.workable.com/j` -> "j"), not a real board slug.
                if tok and tok.lower() not in BLOCK and 3 <= len(tok) <= 60:
                    hits.add((ats, tok.lower()))
    for m in WORKDAY.finditer(html):
        if m.group(3).lower() not in BLOCK:
            hits.add(("workday", f"{m.group(1).lower()}/{m.group(3)}"))
    return hits


async def fetch(session, url, cap=900000, retries=0):
    # retries>0: re-attempt on transient failures (timeout, reset, 5xx/429) — a transient blip
    # on an ATS API otherwise reads as "no board" and silently drops a real hit. A 4xx (e.g.
    # greenhouse 404 = no such board) is definitive and never retried. `timeout` bounds the whole
    # request so a slow site can't stall the batch.
    for attempt in range(retries + 1):
        try:
            r = await session.get(
                url,
                impersonate=IMPERSONATE,
                timeout=FETCH_DEADLINE,
                verify=False,
                allow_redirects=True,
            )
            if r.status_code >= 400:
                if r.status_code == 429 or r.status_code >= 500:
                    continue  # transient — retry
                return ""  # definitive (404/403/...) — don't retry
            ct = r.headers.get("content-type", "")
            if not any(x in ct for x in ("html", "text", "javascript", "json", "xml")):
                return ""
            return r.content[:cap].decode("utf-8", "replace")
        except Exception:
            continue  # transient (timeout, conn) — retry
    return ""


async def careers_html(session, domain):
    """Scan the homepage + /careers for an embedded ATS host (the India-tier darwinbox/keka
    boards appear as `*.darwinbox.in` links in the careers HTML — often only on /careers, which
    may redirect to a custom careers.* host). Both fetches fire concurrently (multiplexed); each
    is bounded by FETCH_DEADLINE. Skipping /careers was silently dropping boards whose link lives
    only there (e.g. BigBasket -> careers.bigbasket.com)."""
    home, careers = await asyncio.gather(
        fetch(session, f"https://{domain}/"),
        fetch(session, f"https://{domain}/careers"),
    )
    return home + "\n" + careers


# Slug-probe (discovery method 4): hit the clean-JSON ATS APIs with candidate slugs derived
# from the company name/domain. Catches boards careers-page scanning misses because the board
# link is buried on a deep subpage (PhonePe -> /careers/job-openings/) or on a custom domain
# (slice -> slice.careers). Require jobs>0 so empty/wrong slugs don't register; real slug
# collisions (a namesake board) still need an eyeball, as always.
ATS_PROBES = {
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


def candidate_slugs(name, domain):
    cands = set()
    label = domain.split("//")[-1].split("/")[0].split(".")[0].lower()
    if label and label != "www":
        cands.add(label)  # phonepe.com -> phonepe
    norm = re.sub(r"[^a-z0-9]", "", name.lower())
    if norm:
        cands.add(norm)  # "Pine Labs" -> pinelabs; "slice" -> slice
    # min length 3: a 2-char slug (e.g. "fi" from fi.money) is too generic and collides with
    # unrelated namesakes (lever/fi is a US firm, not the Indian Fi Money) — false positives.
    return {c for c in cands if len(c) >= 3 and c not in BLOCK}


async def probe_slugs(session, name, domain):
    # Fire every (slug, ATS) probe for this company concurrently; same-host probes across all
    # in-flight companies multiplex over one HTTP/2 connection. cap 2MB: a board's JSON scales
    # with its posting count (Meesho's lever feed is ~800KB for 46 roles); a smaller cap
    # truncates large boards mid-JSON -> parse fails -> board silently dropped.
    tasks = []
    for s in candidate_slugs(name, domain):
        for ats, (tmpl, count) in ATS_PROBES.items():
            tasks.append(
                (
                    ats,
                    s,
                    count,
                    fetch(session, tmpl.format(s=s), cap=2000000, retries=1),
                )
            )
    raws = await asyncio.gather(*[t[3] for t in tasks])
    hits = set()
    for (ats, s, count, _), raw in zip(tasks, raws):
        if not raw:
            continue
        try:
            d = json.loads(raw)
            if count(d) > 0:
                hits.add((ats, s))
        except Exception:
            pass
    return hits


async def run(session, row):
    name, domain = row["name"], row["domain"]
    # careers scan + all slug-probes run concurrently (multiplexed). careers_html catches
    # on-page India-tier embeds (BigBasket's darwinbox link); slug-probe catches clean-JSON
    # boards by candidate slug (PhonePe -> greenhouse). The subdomain title-probe is NOT here —
    # it's ~20 fetches/company; it lives in the separate verify_misses.py pass over the misses.
    ch, ps = await asyncio.gather(
        careers_html(session, domain), probe_slugs(session, name, domain)
    )
    hits = detect(ch) | ps
    # drop self-references: a company that IS an ATS provider (keka.com, darwinbox.com) matches
    # its own infra subdomains; that's not a tenant board.
    rd = reg_domain(domain)
    hits = {(a, t) for (a, t) in hits if rd not in PROVIDER_DOMAINS.get(a, set())}
    return name, domain, hits


async def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    rows = list(csv.DictReader(SEED.open(encoding="utf-8")))[:n]
    out = ROOT / "data" / "resolve" / "fingerprint_results.csv"
    cf = out.open("w", newline="", encoding="utf-8")
    cw = csv.writer(cf)
    cw.writerow(["name", "domain", "hits"])  # hits = "ats:slug;ats:slug" or "" if none
    sem = asyncio.Semaphore(CONCURRENCY)
    hit = done = 0

    async def worker(row):
        async with sem:
            try:
                return await asyncio.wait_for(run(session, row), COMPANY_BUDGET)
            except Exception:
                return row["name"], row["domain"], set()

    async with AsyncSession() as session:
        # flush per company as it finishes: one slow site can't stall the batch, results file
        # is usable mid-run.
        for fut in asyncio.as_completed([worker(r) for r in rows]):
            name, domain, hits = await fut
            done += 1
            label = ";".join(f"{a}:{t}" for a, t in sorted(hits))
            cw.writerow([name, domain, label])
            cf.flush()
            shown = ", ".join(f"{a}:{t}" for a, t in sorted(hits)) if hits else "-"
            if hits:
                hit += 1
            print(f"  [{done}/{len(rows)}] {name} ({domain}): {shown}", flush=True)
    cf.close()
    print(
        f"\n{hit}/{len(rows)} companies fingerprinted to an ATS "
        f"-> {out.relative_to(ROOT)}",
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
