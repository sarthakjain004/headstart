#!/usr/bin/env python3
"""SuccessFactors discovery — Wayback-mine the SF customer universe, then resolve to RMK boards.

SuccessFactors is the one supported ATS with no enumerable namespace: the **RMK** board we scrape
(scrapers/successfactors.py) lives on the customer's own vanity domain (jobs.sap.com,
careers.wipro.com) — nothing to enumerate. Common Crawl is a dead end (it only crawls the bare
provider hosts, career{N}.sapsf.com/robots.txt — not the session-based career URLs). So this miner
works the one signal that *is* in the archive:

  Stage 1 (Wayback CDX): mine successfactors.eu + sapsf.com for the career URLs real users hit
    (career{N}.{...}/career?company={ID}) -> the SF **customer-id universe** {(career_host, id)}.
    successfactors.com yields nothing (only bare hosts are archived there).

  Stage 2 (resolve -> RMK): a company id is a CSB tenant key, not a vanity host, so derive candidate
    vanity hosts from each name-like id ({prefix}.{label}.{tld}) and fingerprint each candidate's
    /sitemap.xml for the RMK shape (an RSS Google-jobs feed, or a urlset of /job/{id}/ URLs — the
    same check the liveness probe uses). Confirmed hosts are real RMK boards the scraper can read.

Opaque numeric ids (C0000031808P) can't be resolved by derivation and are reported but skipped.
CSB-only tenants (their vanity host has no RMK sitemap) simply don't confirm — correct, since the
CSB DWR surface is out of scope (experiment/successfactors-csb/LOG.md).

Outputs (feeder contract, streamed as work completes):
  data/discover/sf_csb_companies.csv        ats,company,career_host,captures   (stage 1 universe)
  data/discover/sf_rmk_candidates.csv       ats,tenant,url,company,source      (stage 2 confirmed)

Run:  python -u scripts/discover/mine_successfactors.py [--limit N]
"""

from __future__ import annotations

import csv
import re
import ssl
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "data" / "discover"
UA = "HeadStart-wayback/0.1 (successfactors discovery)"
CTX = ssl._create_unverified_context()

SF_DOMAINS = ("successfactors.eu", "successfactors.com", "sapsf.com")
# career{N}.{sf-domain}/career?...(career_)company={ID}
_CAREER = re.compile(
    r"https?://(career[a-z0-9]*\.(?:successfactors\.(?:eu|com)|sapsf\.com))/career\?"
    r".*?(?:career_)?company=([A-Za-z0-9_.-]+)",
    re.I,
)
# an id that starts with a digit or the C{digits} customer-number form is opaque -> not derivable
_OPAQUE = re.compile(r"^C?\d")
# trailing environment/instance suffixes on a name-like id: BurberryProd, atlascopcoP, algomasteelT
_SUFFIX = re.compile(
    r"(prod|prd|production|test|tst|stage|stg|dev|corp|global|ext|eu|us|dp|p|t)+$", re.I
)

# candidate vanity host shapes, cheapest-first (matches the confirmed 26-board pool spread)
_PREFIXES = ("jobs", "careers", "career", "jobsearch", "jobdetails")
_TLDS = (".com", ".net", ".co", ".io")

# RMK sitemap fingerprint (same test as scripts/validate/check_liveness.py p_successfactors)
_RMK_RSS = "base.google.com/ns/1.0"


def wb_get(url: str) -> str | None:
    for _ in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=120, context=CTX) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            continue
    return None


def mine_universe(limit: int) -> dict[tuple[str, str], int]:
    """Stage 1: Wayback -> {(career_host, company_id): captures}."""
    pairs: dict[tuple[str, str], int] = {}
    for dom in SF_DOMAINS:
        cap = f"&limit={limit}" if limit else ""
        text = wb_get(
            f"https://web.archive.org/cdx/search/cdx?url={urllib.parse.quote(dom)}"
            f"&matchType=domain&fl=original&collapse=urlkey{cap}"
        )
        if not text:
            print(f"  [stage1] {dom}: no data (throttled?)", flush=True)
            continue
        hits = 0
        for line in text.splitlines():
            m = _CAREER.match(line)
            if m:
                key = (m.group(1).lower(), m.group(2))
                pairs[key] = pairs.get(key, 0) + 1
                hits += 1
        print(f"  [stage1] {dom}: {hits} career-url hits", flush=True)
    return pairs


def candidate_hosts(company_id: str) -> list[str]:
    """Name-like company id -> candidate vanity hosts. Opaque ids yield nothing."""
    if _OPAQUE.match(company_id):
        return []
    label = _SUFFIX.sub("", company_id.lower()) or company_id.lower()
    if len(label) < 3:
        return []
    return [f"{p}.{label}{tld}" for tld in _TLDS for p in _PREFIXES]


def is_rmk(host: str) -> str | None:
    """GET https://{host}/sitemap.xml (capped) and return the host if it is an RMK board."""
    try:
        req = urllib.request.Request(
            f"https://{host}/sitemap.xml", headers={"User-Agent": UA}
        )
        with urllib.request.urlopen(req, timeout=20, context=CTX) as r:
            if r.status != 200:
                return None
            body = r.read(200_000).decode("utf-8", "replace")
    except Exception:
        return None
    if _RMK_RSS in body or "<rss" in body:
        return host
    if "<urlset" in body and "/job/" in body:
        return host
    return None


def resolve_rmk(pairs: dict[tuple[str, str], int]) -> list[tuple[str, str]]:
    """Stage 2: derive + fingerprint. Returns confirmed [(vanity_host, company_id)], first host
    per company wins. Streams confirmations as they land."""
    by_company: dict[str, list[str]] = {}
    for _career_host, cid in pairs:
        by_company.setdefault(cid, []).extend(candidate_hosts(cid))
    # one probe per (company, candidate); stop at the first confirmed host for a company
    confirmed: dict[str, str] = {}
    tasks = [
        (cid, host)
        for cid, hosts in by_company.items()
        for host in dict.fromkeys(hosts)
    ]
    print(
        f"  [stage2] {len(by_company)} name-like companies -> {len(tasks)} host probes",
        flush=True,
    )
    with ThreadPoolExecutor(max_workers=24) as ex:
        futs = {ex.submit(is_rmk, host): (cid, host) for cid, host in tasks}
        for fut in as_completed(futs):
            cid, host = futs[fut]
            if cid in confirmed:
                continue
            if fut.result():
                confirmed[cid] = host
                print(f"    ✓ {cid:<20} -> {host}", flush=True)
    return sorted(confirmed.items(), key=lambda kv: kv[1])


def main() -> None:
    limit = 0
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    OUT.mkdir(parents=True, exist_ok=True)

    print("stage 1: mining the SF customer universe from Wayback", flush=True)
    pairs = mine_universe(limit)
    universe = OUT / "sf_csb_companies.csv"
    with universe.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ats", "company", "career_host", "captures"])
        for (host, cid), n in sorted(pairs.items(), key=lambda kv: -kv[1]):
            w.writerow(["successfactors", cid, host, n])
    namey = sum(1 for (_h, c) in pairs if not _OPAQUE.match(c))
    print(
        f"  -> {len(pairs)} (host,company) pairs ({namey} name-like) -> {universe}",
        flush=True,
    )

    print("\nstage 2: resolving name-like ids to RMK vanity hosts", flush=True)
    confirmed = resolve_rmk(pairs)
    cand = OUT / "sf_rmk_candidates.csv"
    with cand.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ats", "tenant", "url", "company", "source"])
        for cid, host in confirmed:
            w.writerow(["successfactors", host, host, cid, "wayback-csb-resolve"])
    print(
        f"\nDONE. {len(confirmed)} RMK boards confirmed from the mined universe -> {cand}",
        flush=True,
    )


if __name__ == "__main__":
    main()
