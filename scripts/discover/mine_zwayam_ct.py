#!/usr/bin/env python3
"""Certificate-Transparency miner for Zwayam tenants — the shared SAN cert IS the tenant list.

Zwayam boards live on the customer's own domain, so there is no provider namespace to enumerate
and the archive corpora only see whichever boards a crawler happened to visit. But Zwayam fronts
every custom-domain board with **one shared GlobalSign OV certificate**, and every issuance of it
carries `zwayam.com` alongside the full SAN list of live tenants (`careers.microland.com`,
`jobs.happiestminds.com`, `career.crisil.com`, …). CT logs publish every issuance, so one query
keyed on `zwayam.com` returns the tenant roster — and, because reissues happen every few weeks,
the union across issuances recovers tenants that have since been rotated off the cert.

That makes CT strictly better than the crawl corpora for this ATS: complete rather than
crawl-biased, and it names hosts no crawler ever indexed.

Sources are queried in order and unioned: SSLMate CertSpotter (no key needed for a modest rate)
then crt.sh (often 502s; treated as best-effort). Output is one candidate hostname per line,
appended and deduped, for `scripts/discover/zwayam_probe.py` to turn into ground truth.

Run: python -u scripts/discover/mine_zwayam_ct.py OUT_FILE [SEED_DOMAIN ...]
"""

import json
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CTX = ssl._create_unverified_context()
UA = "HeadStart-discovery/0.1 (ATS tenant discovery; polite)"
#: Any domain that appears on the shared cert works as a seed; `zwayam.com` is on every issuance.
SEEDS = ["zwayam.com"]
#: Names on the shared cert that are Zwayam's own infrastructure, not a tenant board.
PROVIDER = {"zwayam.com", "www.zwayam.com", "openings.co", "www.openings.co"}


def certspotter(domain: str) -> set[str]:
    """Every DNS name on every CT-logged cert covering `domain`, paging until exhausted."""
    names: set[str] = set()
    after = None
    for _ in range(40):
        url = (
            f"https://api.certspotter.com/v1/issuances?domain={domain}"
            "&include_subdomains=true&expand=dns_names"
        )
        if after:
            url += f"&after={after}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90, context=CTX) as r:
                rows = json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(30)
                continue
            print(f"  certspotter {domain}: HTTP {e.code}", flush=True)
            break
        except Exception as e:  # noqa: BLE001
            print(f"  certspotter {domain}: {e}", flush=True)
            break
        if not rows:
            break
        for row in rows:
            for n in row.get("dns_names", []):
                n = n.strip().lower().lstrip("*.")
                if n and "." in n:
                    names.add(n)
        after = rows[-1].get("id")
        if not after:
            break
        time.sleep(1)
    return names


def crtsh(domain: str) -> set[str]:
    """Best-effort second CT source; crt.sh 502s under load, so a failure is not an error."""
    names: set[str] = set()
    try:
        req = urllib.request.Request(
            f"https://crt.sh/?q=%25.{domain}&output=json", headers={"User-Agent": UA}
        )
        with urllib.request.urlopen(req, timeout=90, context=CTX) as r:
            rows = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        print(f"  crt.sh {domain}: {e}", flush=True)
        return names
    for row in rows:
        for n in str(row.get("name_value", "")).split("\n"):
            n = n.strip().lower().lstrip("*.")
            if n and "." in n:
                names.add(n)
    return names


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    out = Path(argv[1])
    seeds = argv[2:] or SEEDS
    out.parent.mkdir(parents=True, exist_ok=True)
    seen = {ln.strip() for ln in out.read_text().splitlines() if ln.strip()} if out.exists() else set()
    print(f"ct-zwayam: {len(seeds)} seeds, {len(seen)} known", flush=True)

    with out.open("a") as f:
        for d in seeds:
            for src, fn in (("certspotter", certspotter), ("crt.sh", crtsh)):
                found = fn(d)
                new = 0
                for h in sorted(found - seen - PROVIDER):
                    seen.add(h)
                    f.write(h + "\n")
                    new += 1
                f.flush()  # stream: a killed run keeps everything already found
                print(f"  {src} {d}: {len(found)} names, +{new} (total {len(seen)})", flush=True)
    print(f"DONE {len(seen)} candidate hosts -> {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
