#!/usr/bin/env python3
"""Verify the no-ATS bucket from fingerprint_results.csv.

For every company the fingerprinter returned no ATS for, run two stronger checks the main pass
is weak on, to separate genuine "in-house / no public ATS" from a missed board:

  1. Re-probe the clean-JSON ATS APIs (greenhouse/lever/ashby/smartrecruiters) with candidate
     slugs. Heals transient network misses (a timed-out probe in the big run) and catches boards
     hidden behind deep subpages or custom domains that careers-page scanning can't see.

  2. India-tier subdomain probe with the TITLE test. These providers (darwinbox, keka, zoho,
     freshteam, greythr, peoplestrong, jobsoid, ripplehire, turbohire, qandle) answer HTTP 200
     for ANY subdomain (wildcard), so status code proves nothing. A real tenant renders the
     company name in the page <title>; a nonexistent one renders the generic provider name. We
     compare each candidate subdomain's title against a cached fake-tenant baseline per provider
     and accept only when the page differs from the generic AND echoes the company.

Writes data/resolve/verify_results.csv: name,domain,found (ats:slug;... or ""),method.
Run:  python scripts/resolve/verify_misses.py
"""

import csv
import json
import re
import socket
import ssl
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fingerprint import candidate_slugs  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS = ROOT / "data" / "resolve" / "fingerprint_results.csv"
OUT = ROOT / "data" / "resolve" / "verify_results.csv"
TIMEOUT = 10
FETCH_DEADLINE = 12  # hard wall-clock cap per fetch (defeats slow-trickle hangs)
COMPANY_BUDGET = 45  # hard cap per company so the worker pool never stalls
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
CTX = ssl._create_unverified_context()
socket.setdefaulttimeout(TIMEOUT)

CLEAN_PROBES = {
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
}

# India-tier subdomain ATSes: tenant = {slug}.{host}. Wildcard — must use the title test.
SUBDOMAIN_HOSTS = {
    "darwinbox": ["darwinbox.in", "darwinbox.com"],
    "keka": ["keka.com"],
    "zoho": ["zohorecruit.com", "zohorecruit.in"],
    "ripplehire": ["ripplehire.com"],
    "turbohire": ["turbohire.co"],
    "qandle": ["qandle.com"],
}


def get_text(url, cap=200000):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
            # chunked read with a wall-clock deadline — a slow-trickle server can otherwise keep
            # one read() alive forever and deadlock the pool (see fingerprint.py).
            start = time.monotonic()
            buf = bytearray()
            while len(buf) < cap:
                if time.monotonic() - start > FETCH_DEADLINE:
                    break
                chunk = r.read(65536)
                if not chunk:
                    break
                buf += chunk
            return bytes(buf).decode("utf-8", "replace")
    except Exception:
        return None


def get_json(url):
    t = get_text(url, cap=300000)
    if t is None:
        return None
    try:
        return json.loads(t)
    except Exception:
        return None


def title_of(html):
    m = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.I | re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip().lower() if m else ""


# cache one fake-tenant baseline (title) per host so we know the "generic provider" page
_BASELINE = {}


def baseline_title(host):
    if host not in _BASELINE:
        _BASELINE[host] = title_of(get_text(f"https://zzqxnonexist99.{host}/"))
    return _BASELINE[host]


def probe_clean(name, domain):
    hits = set()
    for s in candidate_slugs(name, domain):
        for ats, (tmpl, count) in CLEAN_PROBES.items():
            d = get_json(tmpl.format(s=s))
            if d is not None:
                try:
                    if count(d) > 0:
                        hits.add((ats, s))
                except Exception:
                    pass
    return hits


def probe_subdomain(name, domain, deadline=None):
    hits = set()
    norm = re.sub(r"[^a-z0-9]", "", name.lower())
    for s in candidate_slugs(name, domain):
        for ats, hosts in SUBDOMAIN_HOSTS.items():
            for host in hosts:
                if deadline is not None and time.monotonic() > deadline:
                    return hits
                html = get_text(f"https://{s}.{host}/")
                if not html:
                    continue
                t = title_of(html)
                base = baseline_title(host)
                # real tenant: title differs from the generic baseline AND names the company —
                # require the slug or the full normalized name in the title, not just any token
                # (an "any token" match flags coincidental words; this is the FP guard).
                if (
                    t
                    and t != base
                    and (s in t or (norm and norm in re.sub(r"[^a-z0-9]", "", t)))
                ):
                    hits.add((ats, s))
    return hits


# a company that IS an ATS provider matches its own infra subdomains (darwinbox -> darwinbox);
# that's a self-reference, not a tenant board. Mirror fingerprint.py's PROVIDER_DOMAINS guard.
PROVIDER_DOMAINS = {
    "darwinbox": {"darwinbox.in", "darwinbox.com"},
    "keka": {"keka.com"},
    "zoho": {"zohorecruit.com", "zohorecruit.eu", "zohorecruit.in", "zohorecruit.ca"},
    "qandle": {"qandle.com"},
    "ripplehire": {"ripplehire.com"},
    "turbohire": {"turbohire.co"},
}


def reg_domain(domain):
    parts = domain.lower().split("//")[-1].split("/")[0].split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else domain.lower()


def verify(row):
    name, domain = row["name"], row["domain"]
    deadline = time.monotonic() + COMPANY_BUDGET
    hits = probe_clean(name, domain)
    method = "clean-probe" if hits else ""
    if not hits:
        hits = probe_subdomain(name, domain, deadline)
        method = "subdomain-title" if hits else "in-house?"
    rd = reg_domain(domain)
    hits = {(a, t) for (a, t) in hits if rd not in PROVIDER_DOMAINS.get(a, set())}
    return name, domain, hits, method


def main():
    if not RESULTS.exists():
        print(f"missing {RESULTS}; run fingerprint.py first")
        return
    rows = list(csv.DictReader(RESULTS.open(encoding="utf-8")))
    misses = [r for r in rows if not r["hits"].strip()]
    print(f"{len(rows)} total, {len(misses)} misses to verify", flush=True)

    cf = OUT.open("w", newline="", encoding="utf-8")
    cw = csv.writer(cf)
    cw.writerow(["name", "domain", "found", "method"])
    recovered = done = 0
    with ThreadPoolExecutor(max_workers=12) as ex:
        for fut in as_completed([ex.submit(verify, r) for r in misses]):
            name, domain, hits, method = fut.result()
            done += 1
            label = ";".join(f"{a}:{t}" for a, t in sorted(hits))
            cw.writerow([name, domain, label, method])
            cf.flush()
            if hits:
                recovered += 1
                print(
                    f"  [{done}/{len(misses)}] RECOVERED {name} ({domain}): "
                    f"{label}  [{method}]",
                    flush=True,
                )
    cf.close()
    print(
        f"\n{recovered}/{len(misses)} misses recovered to an ATS -> "
        f"{OUT.relative_to(ROOT)}",
        flush=True,
    )


if __name__ == "__main__":
    main()
