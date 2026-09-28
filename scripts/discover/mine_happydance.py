#!/usr/bin/env python3
"""Happydance career fronts: candidate hosts kept where their CNAME reaches the vendor.

A Happydance front sits on its customer's own host (``careers.cognizant.com``), so there is no
vendor namespace for Wayback or Common Crawl to sweep. What every front measured shares is its
DNS: 33 of 35 CNAME to ``careers.happydance.website`` and the other 2 to a
``phcreative-*.azurefd.net`` Azure Front Door (2026-09-28, ADR-0264). So this is a DNS sieve:

1. candidates — the hosts named on the command line or in a file (the 2026-09-28 pool came from
   urlscan.io scans that loaded ``happydance.love`` and a CNAME sweep of 24,651 Indeed apply
   hosts), plus hosts derived from crt.sh's certificates for ``%.happydance.website``, whose
   names carry a customer label (``cognizant-careers-lb``, ``tombola-careers-cdn``) turned into
   the usual careers-host spellings;
2. each is resolved on a public resolver (the OS one answers false negatives under load, as
   ``jibeapply.com``'s did) and kept when its CNAME chain reaches the vendor.

What it does not cover: a front whose customer label crt.sh does not name and that nobody
submitted to urlscan or syndicated to Indeed. The vendor claims 250+ fronts; this found 40.

Run:  python scripts/discover/mine_happydance.py [HOSTS_FILE ...]   (appends to the pool)
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import dns.resolver

ROOT = Path(__file__).resolve().parents[2]
POOL = ROOT / "data" / "ats-tenants-merged" / "happydance.csv"
VENDOR = re.compile(
    r"(?:^|\.)happydance\.website$|^phcreative-[^.]+\.[^.]+\.azurefd\.net$"
)
#: A crt.sh name's customer label: ``{customer}-careers-{role}`` and its spellings.
CUSTOMER = re.compile(
    r"^([a-z0-9]+?)(?:-careers)?(?:-v2)?-(?:cdn|lb|webdeploy|production)\."
)
#: Names on the certificate that are the vendor's own, not a customer's.
VENDOR_LABELS = {
    "assets",
    "brandassets",
    "happydance",
    "new",
    "phcreative",
    "uat",
    "us",
}

_resolver = dns.resolver.Resolver(configure=False)
_resolver.nameservers = ["1.1.1.1", "8.8.8.8"]


def crtsh_customers() -> set[str] | None:
    """The customer labels on crt.sh's certificates, or None when crt.sh did not answer (it
    502s for minutes at a time) — reported as not measured, never as no customers. A saved
    capture of the same query can stand in: ``HAPPYDANCE_CRTSH_JSON=path``."""
    saved = os.environ.get("HAPPYDANCE_CRTSH_JSON")
    try:
        if saved:
            entries = json.loads(Path(saved).read_text())
        else:
            url = "https://crt.sh/?q=%25.happydance.website&output=json"
            with urllib.request.urlopen(url, timeout=120) as response:
                entries = json.load(response)
    except (OSError, ValueError) as exc:
        print(f"crt.sh not measured: {exc}", flush=True)
        return None
    names = {n.strip().lower() for e in entries for n in e["name_value"].split("\n")}
    return {
        m.group(1)
        for name in names
        if (m := CUSTOMER.match(name)) and m.group(1) not in VENDOR_LABELS
    }


def derived_hosts(customer: str) -> list[str]:
    return [
        f"careers.{customer}.com",
        f"jobs.{customer}.com",
        f"www.{customer}careers.com",
        f"careers.{customer}.co.uk",
        f"careers.{customer}.group",
        f"www.{customer}.careers",
        f"www.{customer}.jobs",
    ]


def cname_chain(host: str) -> list[str]:
    chain, name = [], host
    for _ in range(6):
        try:
            answer = _resolver.resolve(name, "CNAME")
        except Exception:  # noqa: BLE001 - no CNAME, NXDOMAIN or timeout all end the chain
            break
        name = str(answer[0].target).rstrip(".").lower()
        chain.append(name)
    return chain


def main(files: list[str]) -> None:
    candidates = {
        line.strip().lower()
        for path in files
        for line in Path(path).read_text().split()
        if line.strip()
    }
    customers = crtsh_customers() or set()
    print(f"crt.sh names {len(customers)} customers: {sorted(customers)}", flush=True)
    derived = {host for c in customers for host in derived_hosts(c)}
    candidates |= derived
    held = set()
    if POOL.exists():
        held = {row["tenant"] for row in csv.DictReader(POOL.open())}
    found: list[tuple[str, str]] = []
    with ThreadPoolExecutor(16) as pool:
        futures = {pool.submit(cname_chain, host): host for host in sorted(candidates)}
        for future in as_completed(futures):
            host, chain = futures[future], future.result()
            if any(VENDOR.search(name) for name in chain):
                source = "crtsh-derived" if host in derived else "candidates"
                print(f"{host}\t{' > '.join(chain)}\t{source}", flush=True)
                if host not in held:
                    found.append((host, source))
    new = not POOL.exists()
    with POOL.open("a", newline="") as out:
        writer = csv.writer(out)
        if new:
            writer.writerow(["ats", "tenant", "url", "source"])
        for host, source in sorted(found):
            writer.writerow(["happydance", host, f"https://{host}/", source])
    print(f"{len(found)} new fronts appended to {POOL}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
