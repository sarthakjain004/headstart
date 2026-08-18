#!/usr/bin/env python3
"""Embed / custom-domain ATS fingerprinter (Phase 2 of the JobPosting/JSON-LD plan).

For each company it fetches the homepage + careers page(s) and scans the RAW HTML for an
embedded ATS fingerprint (an embed script, a board link, or an API call) -> (ats, token).
This catches boards that URL-crawling misses because the company embeds the board on its own
site or uses a custom domain (the Greenhouse address never appears as a crawlable URL).

Resumable: the output is keyed by domain and re-running skips companies already recorded, so
a run killed part-way costs only its in-flight companies. Pass --restart to start clean.

Usage:  python scripts/resolve/fingerprint.py [n] [--seed CSV] [--out CSV]
        python scripts/resolve/fingerprint.py --seed config/seed_global.csv     # all rows
        python scripts/resolve/fingerprint.py 500 --seed config/seed_global.csv # first 500
"""

import argparse
import asyncio
import csv
import json
import re
from pathlib import Path

from curl_cffi.requests import AsyncSession

ROOT = Path(__file__).resolve().parent.parent.parent
SEED = ROOT / "config" / "seed_india.csv"
DEFAULT_OUT = ROOT / "data" / "resolve" / "fingerprint_results.csv"
FIELDS = ["name", "domain", "hits", "status"]
FETCH_DEADLINE = 10  # hard wall-clock cap per request
COMPANY_BUDGET = (
    60  # hard cap per company (asyncio.wait_for), so nothing stalls the batch
)
# HTTP/2 multiplexes the shared ATS-API hosts, so the unique company domains are the bottleneck.
# This was 10, on an earlier finding that 24 regressed hits 86 -> 60. That finding did not
# survive the backtracking fix: it was the blocked event loop, not link saturation. With a
# catastrophically-backtracking detect() running inline, more concurrent heavy pages meant more
# loop-blocking, so companies blew COMPANY_BUDGET and returned empty — which read as "too much
# concurrency". Re-measured mid-run on the same seed after the fix: 24 gives 108 companies/min
# vs 82 at 10 (+32%), with timeouts flat at zero and hit rate not degraded. Note the hit-rate
# comparison is across different seed rows, so treat it as "no regression", not as a gain.
CONCURRENCY = 24
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
    "smartrecruiters": {"smartrecruiters.com"},
    "teamtailor": {"teamtailor.com"},
    "freshteam": {"freshteam.com", "freshworks.com"},
    "trakstar": {"trakstar.com"},
    "sensehq": {"sensehq.com"},
    "rippling": {"rippling.com"},
    "personio": {"personio.de", "personio.com"},
    "eightfold": {"eightfold.ai"},
}

# Legal-entity suffixes a registry carries but a board slug never does ("Infosys Limited" ->
# infosys, "SAP SE" -> sap). Stripped to make an EXTRA candidate slug, never to replace the
# full one — so a company whose slug really does end in a suffix word is still probed.
LEGAL_SUFFIXES = {
    "inc",
    "llc",
    "ltd",
    "limited",
    "pvt",
    "private",
    "plc",
    "corp",
    "corporation",
    "gmbh",
    "ag",
    "se",
    "sa",
    "nv",
    "bv",
    "ab",
    "oy",
    "as",
    "srl",
    "spa",
    "kk",
    "pte",
    "holdings",
    "company",
}


def reg_domain(domain):
    """Crude registered domain: the last two labels of the host."""
    parts = domain.lower().split("//")[-1].split("/")[0].split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else domain.lower()


# Every subdomain-style pattern MUST start with this. Without it a pattern like
# `([a-z0-9][a-z0-9-]*)\.recruitee\.com` starts a match attempt at every offset inside a long
# run of hostname-legal characters — minified JS and base64 blobs are full of them — consuming
# the whole run before failing on the literal suffix, then retrying one character later. That is
# O(n^2) on a 900KB page: measured >120s for a single 520KB blob, which froze the whole run
# (detect() is synchronous, so it blocks the event loop and COMPANY_BUDGET's wait_for can never
# fire). The lookbehind makes the engine reject a mid-run offset in one step.
HOST = r"(?<![a-z0-9.-])"

PATTERNS = {
    "greenhouse": [
        # bounded {0,200}: an unbounded lazy [^"'\s]*? has the same quadratic blow-up when the
        # literal prefix matches but no `for=` follows before the next quote.
        r'(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/embed/job_board[^"\'\s]{0,200}?[?&]for=([a-zA-Z0-9_-]+)',
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
    "zoho": [HOST + r"([a-z0-9][a-z0-9-]*)\.zohorecruit\.(?:com|eu|in|ca)"],
    "recruitee": [HOST + r"([a-z0-9][a-z0-9-]*)\.recruitee\.com"],
    "workable": [r"apply\.workable\.com/([a-zA-Z0-9_-]+)"],
    # Supported scrapers that had no HTML signature — an embedded board on any of these was
    # invisible to the page scan and only ever found if the slug probe happened to guess it.
    # Shapes derived from each scraper's own url() construction in src/headstart/scrapers/.
    "smartrecruiters": [
        r"api\.smartrecruiters\.com/v1/companies/([a-zA-Z0-9_-]+)",
        r"(?:careers|jobs)\.smartrecruiters\.com/([a-zA-Z0-9_-]+)",
    ],
    "teamtailor": [HOST + r"([a-z0-9][a-z0-9-]*)\.teamtailor\.com"],
    "freshteam": [HOST + r"([a-z0-9][a-z0-9-]*)\.freshteam\.com"],
    "trakstar": [HOST + r"([a-z0-9][a-z0-9-]*)\.hire\.trakstar\.com"],
    "sensehq": [HOST + r"([a-z0-9][a-z0-9-]*)\.sensehq\.com"],
    "rippling": [r"ats\.rippling\.com/([a-zA-Z0-9_-]+)"],
    # personio and eightfold key on the FULL careers host, not a bare label (personio's
    # slug_from returns "{tenant}.jobs.personio.de"; eightfold's url() is "https://{slug}/careers"),
    # so these capture the whole host.
    "personio": [HOST + r"([a-z0-9][a-z0-9-]*\.jobs\.personio\.(?:de|com))"],
    "eightfold": [HOST + r"([a-z0-9][a-z0-9-]*\.eightfold\.ai)"],
    # India subdomain tier — same providers the CC/Wayback miners cover; tenant = subdomain
    "darwinbox": [HOST + r"([a-z0-9][a-z0-9-]*)\.darwinbox\.(?:in|com)"],
    "keka": [HOST + r"([a-z0-9][a-z0-9-]*)\.keka\.com"],
    "qandle": [HOST + r"([a-z0-9][a-z0-9-]*)\.qandle\.com"],
    "ripplehire": [HOST + r"([a-z0-9][a-z0-9-]*)\.ripplehire\.com"],
    "turbohire": [HOST + r"([a-z0-9][a-z0-9-]*)\.turbohire\.co"],
}
# re.I matters: real Workday URLs carry an uppercase region ("/en-US/AcmeCareers"). Without it
# the locale group (lowercase-only) failed to match, the optional group matched empty, and the
# site capture swallowed "en-US" — so every localed board resolved to .../en-US instead of its
# real site. LOCALE re-checks the capture for the same reason, for URL shapes that put the
# locale somewhere this pattern doesn't model.
WORKDAY = re.compile(
    HOST
    + r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)
# Both locale shapes Workday serves: "en-US" and the bare "es". Matching only the hyphenated
# form let /es/ through, and the site capture swallowed the language code — e.g. Rappi's boards
# were recorded as .../es instead of their real site.
# The bare form is an explicit language list, NOT any two letters: real site names are two
# letters too (Knight Frank's board is .../KF), and rejecting those would drop live boards.
LOCALE = re.compile(
    r"^(?:[a-z]{2}-[a-z]{2}|en|es|fr|de|pt|it|ja|zh|ko|nl|ru|ar|pl|tr|sv|da|fi|cs|hu|th|vi|id)$",
    re.IGNORECASE,
)

# ATSes whose board id keeps its capitals. The SmartRecruiters postings API itself is
# case-INSENSITIVE (Zomato1 and zomato1 both 200), so this is not about the fetch — it is about
# matching the ledger, where 8,736 of 12,644 smartrecruiters tenants carry capitals. Lower-casing
# here would mint a second, non-matching row for a board we already hold. Every other supported
# ATS keys on a lower-case slug.
CASE_SENSITIVE = {"smartrecruiters"}

# Careers paths worth fetching per company. Tested wider: over two 40-company miss diagnoses
# (80 sites x 10 paths), every ATS signal reachable from /jobs, /careers/, /company/careers,
# /about/careers or /en/careers ALSO appeared on / or /careers. Adding /jobs cost a third more
# page fetches and changed the hit count by zero on both cohorts, so it stays out.
CAREERS_PATHS = ("/", "/careers")


def detect(html):
    hits = set()
    for ats, pats in PATTERNS.items():
        for p in pats:
            for m in re.finditer(p, html, re.IGNORECASE):
                raw = m.group(1) or ""
                tok = raw if ats in CASE_SENSITIVE else raw.lower()
                # require len 3-60: a 1-2 char token is almost always garbage from a minified
                # JS path (e.g. a stray `apply.workable.com/j` -> "j"), not a real board slug.
                # The split() check also screens host-shaped tokens (personio/eightfold), where
                # the blocked word is the leading label: "www.eightfold.ai" is not a tenant.
                lo = tok.lower()
                if tok and 3 <= len(tok) <= 60:  # noqa: SIM102
                    if lo not in BLOCK and lo.split(".")[0] not in BLOCK:
                        hits.add((ats, tok))
    for m in WORKDAY.finditer(html):
        # The Workday scraper's slug IS the full board URL — src/headstart/scrapers/workday.py
        # slug_from() returns url.rstrip("/") and validates against
        # ^https://{co}.wdN.myworkdayjobs.com/{site}. Emitting "{co}/{site}" dropped the wdN
        # data-centre and the scheme, so every Workday hit was unusable downstream.
        co, pod, site = m.group(1).lower(), m.group(2).lower(), m.group(3)
        if site.lower() not in BLOCK and not LOCALE.match(site):
            hits.add(("workday", f"https://{co}.{pod}.myworkdayjobs.com/{site}"))
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
                    # transient — back off before retrying. An immediate re-hit of a 429 just
                    # spends the next token bucket too and reads as another failure.
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                return ""  # definitive (404/403/...) — don't retry
            ct = r.headers.get("content-type", "")
            if not any(x in ct for x in ("html", "text", "javascript", "json", "xml")):
                return ""
            return r.content[:cap].decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            await asyncio.sleep(0.5 * (attempt + 1))
            continue  # transient (timeout, conn) — retry
    return ""


async def careers_html(session, domain):
    """Scan the homepage + /careers for an embedded ATS host (the India-tier darwinbox/keka
    boards appear as `*.darwinbox.in` links in the careers HTML — often only on /careers, which
    may redirect to a custom careers.* host). Both fetches fire concurrently (multiplexed); each
    is bounded by FETCH_DEADLINE. Skipping /careers was silently dropping boards whose link lives
    only there (e.g. BigBasket -> careers.bigbasket.com)."""
    pages = await asyncio.gather(
        *[fetch(session, f"https://{domain}{p}", retries=1) for p in CAREERS_PATHS]
    )
    return "\n".join(pages)


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
    words = re.findall(r"[a-z0-9]+", name.lower())
    if words:
        cands.add("".join(words))  # "Pine Labs" -> pinelabs; "slice" -> slice
    # Registry-sourced seeds (Wikidata, company registers) carry a legal suffix the board slug
    # never has. Add the trimmed variant alongside the full one, never instead of it.
    trimmed = list(words)
    while len(trimmed) > 1 and trimmed[-1] in LEGAL_SUFFIXES:
        trimmed.pop()
    if trimmed and trimmed != words:
        cands.add("".join(trimmed))  # "Infosys Limited" -> infosys
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
        except Exception:  # noqa: BLE001, S110
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
    # detect() off the loop: it is pure CPU over up to 1.8MB, and run inline a slow page stalls
    # every other in-flight company AND stops COMPANY_BUDGET's wait_for from firing, since a
    # timeout needs the loop to run. `re` doesn't release the GIL so this doesn't parallelise the
    # work — it just keeps the loop responsive, which is what makes the per-company cap real.
    hits = await asyncio.to_thread(detect, ch) | ps
    # drop self-references: a company that IS an ATS provider (keka.com, darwinbox.com) matches
    # its own infra subdomains; that's not a tenant board.
    rd = reg_domain(domain)
    hits = {(a, t) for (a, t) in hits if rd not in PROVIDER_DOMAINS.get(a, set())}
    # A company whose pages never loaded is not the same as one scanned and found clean — the
    # first is worth re-probing (dead/parked/blocked host), the second is settled. Recording
    # them identically as "" was hiding that, which matters most on derived-domain seed rows.
    return name, domain, hits, "ok" if ch.strip() else "unreachable"


def load_seed(path: Path, n: int | None = None) -> list[dict]:
    """Seed rows from a name,domain CSV — the first n, or all of them."""
    if not path.exists():
        raise SystemExit(f"seed not found: {path}")
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    lacks = {"name", "domain"} - set(rows[0]) if rows else set()
    if lacks:
        raise SystemExit(f"{path} lacks column(s): {', '.join(sorted(lacks))}")
    return rows[:n] if n else rows


def open_results(path: Path, restart: bool):
    """(writer, handle, domains already recorded) for a resumable results CSV.

    Refuses to append onto an older layout rather than writing rows the reader will
    mis-key. Shared with fingerprint_headless.py so both passes write one schema.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = restart or not path.exists()
    already: set[str] = set()
    if not fresh:
        with path.open(encoding="utf-8") as fh:
            prev = csv.DictReader(fh)
            if prev.fieldnames != FIELDS:
                raise SystemExit(
                    f"{path} has an older layout {prev.fieldnames}; re-run with "
                    f"--restart to rewrite it as {FIELDS}"
                )
            already = {r["domain"] for r in prev if r.get("domain")}
    handle = path.open("w" if fresh else "a", newline="", encoding="utf-8")
    writer = csv.writer(handle)
    if fresh:
        writer.writerow(FIELDS)  # hits = "ats:slug;ats:slug" or "" if none
    return writer, handle, already


async def main():
    ap = argparse.ArgumentParser(description="Embed / custom-domain ATS fingerprinter.")
    ap.add_argument(
        "n", nargs="?", type=int, help="only the first n seed rows (default: all)"
    )
    ap.add_argument(
        "--seed", type=Path, default=SEED, help="seed CSV with name,domain columns"
    )
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="results CSV")
    ap.add_argument(
        "--restart", action="store_true", help="truncate the output instead of resuming"
    )
    args = ap.parse_args()

    rows = load_seed(args.seed, args.n)
    cw, cf, already = open_results(args.out, args.restart)
    pending = [r for r in rows if r["domain"] not in already]
    print(
        f"{len(rows)} seed rows | {len(already)} already done | {len(pending)} to scan",
        flush=True,
    )
    if not pending:
        cf.close()
        return

    sem = asyncio.Semaphore(CONCURRENCY)
    hit = done = 0
    states = {}

    async def worker(row):
        async with sem:
            try:
                return await asyncio.wait_for(run(session, row), COMPANY_BUDGET)
            except TimeoutError:
                return row["name"], row["domain"], set(), "timeout"
            except Exception:  # noqa: BLE001
                return row["name"], row["domain"], set(), "error"

    async with AsyncSession() as session:
        # flush per company as it finishes: one slow site can't stall the batch, results file
        # is usable mid-run and a kill costs only the in-flight companies.
        for fut in asyncio.as_completed([worker(r) for r in pending]):
            name, domain, hits, status = await fut
            done += 1
            states[status] = states.get(status, 0) + 1
            label = ";".join(f"{a}:{t}" for a, t in sorted(hits))
            cw.writerow([name, domain, label, status])
            cf.flush()
            shown = ", ".join(f"{a}:{t}" for a, t in sorted(hits)) if hits else "-"
            if hits:
                hit += 1
            print(f"  [{done}/{len(pending)}] {name} ({domain}): {shown}", flush=True)
    cf.close()
    breakdown = ", ".join(f"{k} {v}" for k, v in sorted(states.items()))
    print(
        f"\n{hit}/{len(pending)} companies fingerprinted to an ATS ({breakdown})"
        f" -> {args.out}",
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
