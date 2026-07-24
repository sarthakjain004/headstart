"""Mine one Common Crawl index for boards of the ATSes HeadStart supports — straight from CC's
CDX API.

This is the global rewrite of the old India-only miner. It covers every ATS in the scraper
registry, each with its real URL shapes *including regional data centres* (Lever/Greenhouse EU,
Zoho's .eu/.in/.ca hosts), and extracts the tenant the right way per ATS: a subdomain label
(``{tenant}.darwinbox.in``), a path slug (``boards.greenhouse.io/{slug}``), a full careers host
(Zoho/Personio/Oracle), or a full board URL (Workday) — matching what each scraper's ``slug_from``
expects downstream.

Scope: **one crawl** (default: the newest = June 2026, ``CC-MAIN-2026-25``). Pass a crawl id to
pick another (``python -u scripts/discover/cc_miner.py CC-MAIN-2026-21``).

Robust to CC's aggressive rate-limiting, same discipline as before:
  * ``curl --fail``-style classification: a 200 with empty body / a 404 is a real no-match (ok);
    a 403/429/5xx/timeout is a block (not data), retried, and on persistent block the run exits
    cleanly (exit 0) so a wrapper can wait it out and relaunch.
  * Resumable + incremental: each ``(crawl, target, page)`` is checkpointed only when it fully
    succeeds, and the CSV is rewritten after every completed page — a throttled page is retried
    next run, never silently recorded as empty.
  * IP rotation via Cloudflare WARP: if CC hard-blocks the egress IP itself (sustained 403/429 or
    connection-refused across retries — not a transient throttle a wait fixes), rotate to a fresh
    egress IP with WARP and relaunch. The checkpoint resumes mid-sweep with zero re-fetch of
    completed pages, so no data is re-pulled:
        warp-cli connect                 # (or: warp-cli disconnect && warp-cli connect) -> new IP
        curl -s https://www.cloudflare.com/cdn-cgi/trace | grep -E '^ip=|^warp='   # confirm rotate
    then re-run (optionally per crawl: ``CC_ONLY_ATS=... python cc_miner.py <crawl-id>``). Verified
    2026-07-23, when a multi-crawl eightfold sweep tripped CC's per-IP block.

Output: ``data/discover/cc_ats_tenants.csv`` with ``ats,tenant,url`` (the feeder contract in
CONTEXT.md; ``url`` is a representative capture the resolve/scrape steps can read the slug back from).

Usage: python -u scripts/discover/cc_miner.py [crawl-id]   (run from repo root)
       CC_ONLY_ATS=eightfold python -u scripts/discover/cc_miner.py   (restrict to one ATS)
       CC_PACE=2.5 python -u scripts/discover/cc_miner.py   (slower request pacing to dodge blocks)
"""

import collections
import csv
import json
import os
import re
import subprocess
import sys
import time

CRAWL_ARG = sys.argv[1] if len(sys.argv) > 1 else None
CSV = "data/discover/cc_ats_tenants.csv"
DONE = "data/discover/cc_miner_checkpoint.txt"
CC_COLLINFO = "https://index.commoncrawl.org/collinfo.json"
# Safety backstop on pages per target; the clean throttle-exit + resume is the real bound, so this
# is only here to stop a pathological crawl. Override with CC_MAX_PAGES.
MAX_PAGES = int(os.environ.get("CC_MAX_PAGES") or 400)
# Seconds to pace between successful CDX requests. Raise it (CC_PACE=2.5) to stay under CC's
# per-IP rate limit on a long multi-crawl sweep; lower it for a quick single-crawl run.
PACE = float(os.environ.get("CC_PACE") or 1.0)

# Per ATS: the CDX hosts/domains to query (matchType=domain), the regexes that capture the tenant
# from a matched URL (group 1, except workday which uses host+site groups), and how to read that
# capture ("label" = subdomain label, "slug" = path slug, "host" = full careers host,
# "workday" = rebuilt board URL, "oracle" = careers host). Regional data centres are folded into
# the target list and the regex alternations.
ATS_PATTERNS = {
    "greenhouse": {
        "targets": [
            "boards.greenhouse.io",
            "job-boards.greenhouse.io",
            "boards-api.greenhouse.io",
            "job-boards.eu.greenhouse.io",
            "boards.eu.greenhouse.io",
            "boards-api-eu.greenhouse.io",
        ],
        "kind": "slug",
        "patterns": [
            r"boards-api(?:-eu)?\.greenhouse\.io/v1/boards/([a-z0-9_-]+)",
            r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/embed/job_board(?:/js)?\?(?:[^\"'\s]*&)?for=([a-z0-9_-]+)",
            r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/([a-z0-9][a-z0-9_-]+)",
        ],
    },
    "lever": {
        "targets": [
            "jobs.lever.co",
            "api.lever.co",
            "jobs.eu.lever.co",
            "api.eu.lever.co",
        ],
        "kind": "slug",
        "patterns": [
            r"api(?:\.eu)?\.lever\.co/v0/postings/([a-z0-9][a-z0-9-]+)",
            r"jobs(?:\.eu)?\.lever\.co/([a-z0-9][a-z0-9-]+)",
        ],
    },
    "ashby": {
        "targets": ["jobs.ashbyhq.com", "api.ashbyhq.com"],
        "kind": "slug",
        "patterns": [
            r"api\.ashbyhq\.com/posting-api/job-board/([a-z0-9][a-z0-9._-]+)",
            r"jobs\.ashbyhq\.com/([a-z0-9][a-z0-9._-]+)",
        ],
    },
    "smartrecruiters": {
        "targets": [
            "jobs.smartrecruiters.com",
            "careers.smartrecruiters.com",
            "api.smartrecruiters.com",
        ],
        "kind": "slug",
        "patterns": [
            r"api\.smartrecruiters\.com/v1/companies/([a-zA-Z0-9_-]+)/postings",
            r"(?:jobs|careers)\.smartrecruiters\.com/([a-zA-Z0-9_-]+)",
        ],
    },
    "workable": {
        "targets": ["apply.workable.com"],
        "kind": "slug",
        "patterns": [
            r"apply\.workable\.com/(?:api/v1/widget/accounts/)?([a-z0-9][a-z0-9-]+)",
        ],
    },
    "rippling": {
        "targets": ["ats.rippling.com", "api.rippling.com"],
        "kind": "slug",
        "patterns": [
            r"ats\.rippling\.com/([a-z0-9][a-z0-9-]+)",
            r"api\.rippling\.com/platform/api/ats/v1/board/([a-z0-9][a-z0-9-]+)",
        ],
    },
    "join": {
        "targets": ["join.com"],
        "kind": "slug",
        "patterns": [r"join\.com/companies/([a-z0-9][a-z0-9-]+)"],
    },
    "darwinbox": {
        "targets": ["darwinbox.in", "darwinbox.com"],
        "kind": "label",
        "patterns": [r"([a-z0-9][a-z0-9-]*)\.darwinbox\.(?:in|com)"],
    },
    "eightfold": {
        # Every Eightfold-hosted tenant is {slug}.eightfold.ai (the board host the scraper reads).
        # Custom-domain tenants (careers.qualcomm.com, jobs.nvidia.com) can't be found by host-mining
        # eightfold.ai — they need a content fingerprint, out of scope for a CDX host-miner.
        # Anchor the label to a host boundary (`//` or an encoded `%2f`): Eightfold share/redirect
        # URLs embed a second, percent-encoded eightfold host in the query (`...%2f%2fbcg.eightfold.ai`),
        # and a bare pattern would capture `2fbcg` instead of `bcg`.
        "targets": ["eightfold.ai"],
        "kind": "label",
        "patterns": [r"(?://|%2f)([a-z0-9][a-z0-9-]*)\.eightfold\.ai"],
    },
    "keka": {
        "targets": ["keka.com"],
        "kind": "label",
        "patterns": [r"([a-z0-9][a-z0-9-]*)\.keka\.com"],
    },
    "recruitee": {
        "targets": ["recruitee.com"],
        "kind": "label",
        "patterns": [r"([a-z0-9][a-z0-9-]*)\.recruitee\.com"],
    },
    "ripplehire": {
        "targets": ["ripplehire.com"],
        "kind": "label",
        "patterns": [r"([a-z0-9][a-z0-9-]*)\.ripplehire\.com"],
    },
    "sensehq": {
        "targets": ["sensehq.com"],
        "kind": "label",
        "patterns": [r"([a-z0-9][a-z0-9-]*)\.sensehq\.com"],
    },
    "teamtailor": {
        "targets": ["teamtailor.com"],
        "kind": "label",
        "patterns": [r"([a-z0-9][a-z0-9-]*)\.teamtailor\.com"],
    },
    "trakstar": {
        "targets": ["hire.trakstar.com"],
        "kind": "label",
        "patterns": [r"([a-z0-9][a-z0-9-]*)\.hire\.trakstar\.com"],
    },
    "zoho": {
        "targets": [
            "zohorecruit.com",
            "zohorecruit.eu",
            "zohorecruit.in",
            "zohorecruit.ca",
        ],
        "kind": "host",
        "patterns": [r"([a-z0-9][a-z0-9-]*\.zohorecruit\.(?:com|eu|in|ca))"],
    },
    "personio": {
        "targets": ["jobs.personio.com", "jobs.personio.de"],
        "kind": "host",
        "patterns": [r"([a-z0-9][a-z0-9-]*\.jobs\.personio\.(?:com|de))"],
    },
    "workday": {
        "targets": ["myworkdayjobs.com"],
        "kind": "workday",
        # groups: (host, site); a leading locale (en-US) is skipped. Rebuilt to the canonical
        # board URL https://{host}/{site} that WorkdayScraper.slug_from expects.
        "patterns": [
            r"https?://([a-z0-9-]+\.wd\d+\.myworkdayjobs\.com)/(?:[a-z]{2}-[A-Z]{2}/)?([a-zA-Z0-9_-]+)",
        ],
    },
    "oracle": {
        "targets": ["oraclecloud.com"],
        "kind": "oracle",
        "patterns": [
            r"([a-z0-9-]+\.fa\.[a-z0-9-]+\.oraclecloud\.com)/hcm(?:UI|RestApi)",
        ],
    },
}

# Tokens that are never a real tenant/slug: provider infra + marketing subdomains + the path
# words that sit where a slug would (embed/job_board/api/...). Applied to "label" and "slug" kinds.
BLOCK = {
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
    "job",
    "dash",
    "embed",
    "content",
    "cloud",
    "ess",
    "qa",
    "demo",
    "test",
    "staging",
    "dev",
    "sandbox",
    "uat",
    "partners",
    "partner",
    "community",
    "developer",
    "developers",
    "events",
    "customers",
    "m",
    "board",
    "boards",
    "job_board",
    "v0",
    "v1",
    "v2",
    "postings",
    "posting",
    "companies",
    "company",
    "platform",
    "widget",
    "public",
    "js",
    "en",
    "en-us",
    "en-gb",
    "auth",
    "signup",
    "resources",
    "pricing",
    "about",
    "contact",
    "product",
    "products",
    "news",
    "wday",
    "cxs",
}
INFRA = BLOCK  # back-compat alias: probe_ats.py filters subdomain labels against cc_miner.INFRA


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
    """Return (body, ok). ok=False => real block/throttle (429/503/5xx/timeout). CC's CDX API
    returns 404 for a domain absent from a crawl — a legitimate empty result, so 404 => ("", True)."""
    for _ in range(attempts):
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
                time.sleep(PACE)  # gentle pacing
                return body, True
            if code == "404":
                time.sleep(PACE)
                return "", True  # legitimate no-match for this target in this crawl
            # 403/429/5xx => real block; fall through to retry
        time.sleep(3)
    return "", False


def resolve_crawl(requested):
    """Resolve (crawl_id, cdx_api). Default = the newest crawl (June 2026 right now)."""
    body, ok = curl(CC_COLLINFO)
    if not ok or not body.strip():
        print("[miner] collinfo unreachable -> exiting (wrapper retries)", flush=True)
        sys.exit(3)
    crawls = json.loads(body)
    if requested:
        for c in crawls:
            if c["id"] == requested:
                return c["id"], c["cdx-api"]
        sys.exit(f"[miner] crawl {requested!r} not in collinfo")
    return crawls[0]["id"], crawls[0]["cdx-api"]  # newest first


def num_pages(cdx, target):
    """CDX page count for this target in the crawl. None => throttled; 0 => absent (404)."""
    body, ok = curl(
        f"{cdx}?url={target}&matchType=domain&showNumPages=true&output=json"
    )
    if not ok:
        return None
    body = body.strip()
    if not body:
        return 0
    try:
        return int(json.loads(body).get("pages", 1))
    except Exception:
        return 1


def tenant_from(kind, match):
    """Normalize one regex match to a (tenant, url_hint) or None to drop it."""
    if kind == "workday":
        host, site = match.group(1), match.group(2)
        if site.lower() in BLOCK or len(site) < 2:
            return None
        board = f"https://{host}/{site}"
        return board, board  # canonical board URL (what slug_from reads)
    tok = match.group(1)
    if kind in ("host", "oracle"):
        return tok.lower(), None  # full careers host; url filled from the raw capture
    tok = tok.lower()  # label / slug
    if tok in BLOCK or len(tok) < 2 or tok.isdigit():
        return None
    return tok, None


def query_target(cdx, ats, spec, target, done, tenants, crawl):
    """Query one target across all its pages, extracting tenants. Returns False on throttle."""
    pats = [re.compile(p, re.I) for p in spec["patterns"]]
    pages = num_pages(cdx, target)
    if pages is None:
        return False
    if pages == 0:
        return True
    capped = min(pages, MAX_PAGES)
    if pages > MAX_PAGES:
        print(f"    ! {target}: {pages} pages, capping at {MAX_PAGES}", flush=True)
    for page in range(capped):
        key = f"{crawl}|{target}|{page}"
        if key in done:
            continue
        body, ok = curl(
            f"{cdx}?url={target}&matchType=domain&output=json&fl=url&collapse=urlkey&page={page}"
        )
        if not ok:
            return False
        for line in body.splitlines():
            try:
                u = json.loads(line)["url"]
            except Exception:
                continue
            for pat in pats:
                for m in pat.finditer(u):
                    result = tenant_from(spec["kind"], m)
                    if result:
                        tenant, url_hint = result
                        tenants[ats].setdefault(tenant, url_hint or u)
        done.add(key)
        write_csv(tenants)
        with open(DONE, "w", encoding="utf-8") as f:
            f.write("\n".join(sorted(done)))
    return True


def write_csv(tenants):
    rows = sorted(
        (ats, tenant, url)
        for ats, hits in tenants.items()
        for tenant, url in hits.items()
    )
    with open(CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ats", "tenant", "url"])
        w.writerows(rows)
    return len(rows)


def load_existing():
    tenants = collections.defaultdict(dict)
    if os.path.exists(CSV):
        with open(CSV, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                tenants[row["ats"]][row["tenant"]] = row.get("url", "")
    done = set()
    if os.path.exists(DONE):
        with open(DONE, encoding="utf-8") as f:
            done = {ln.strip() for ln in f if ln.strip()}
    return tenants, done


def main():
    os.makedirs("data/discover", exist_ok=True)
    only = os.environ.get("CC_ONLY_ATS")
    specs = {a: s for a, s in ATS_PATTERNS.items() if not only or a == only}
    if only and not specs:
        sys.exit(f"[miner] CC_ONLY_ATS={only!r} not in ATS_PATTERNS")
    crawl, cdx = resolve_crawl(CRAWL_ARG)
    tenants, done = load_existing()
    print(
        f"mining {crawl} ({cdx.split('/')[-1]}) | {len(specs)} ATS(es)"
        + (f" [CC_ONLY_ATS={only}]" if only else "")
        + f" | {sum(len(v) for v in tenants.values())} tenants known | {len(done)} pages done",
        flush=True,
    )

    for ats, spec in specs.items():
        for target in spec["targets"]:
            ok = query_target(cdx, ats, spec, target, done, tenants, crawl)
            if not ok:
                print(
                    f"[{ats}] {target} INCOMPLETE (throttled) -> exiting to wait",
                    flush=True,
                )
                return  # clean exit; wrapper waits out the block then relaunches
        print(f"[{ats}] done | {len(tenants[ats])} tenants", flush=True)

    total = write_csv(tenants)
    print(f"DONE. {total} (ats,tenant) rows across {crawl} -> {CSV}", flush=True)
    for ats in specs:
        if tenants[ats]:
            print(f"  {ats}: {len(tenants[ats])}", flush=True)


if __name__ == "__main__":
    main()
