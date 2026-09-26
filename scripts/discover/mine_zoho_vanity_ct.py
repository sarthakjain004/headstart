#!/usr/bin/env python3
"""Custom-domain Zoho Recruit career sites, snowballed through certificate transparency.

A Zoho Recruit customer can serve its career site on its own host (`careers.acme.com`, CNAMEd
to `recruit.cs.zohohost.{tld}`). Zoho then issues that host a Let's Encrypt certificate — and
bundles it with ~80-100 *other customers'* career hosts as SANs on one certificate (measured
2026-09-26: `career.alconcysec.com` shared a cert with 82 others, every one a career/jobs host).
Zoho re-issues the bundle as customers join and leave, so the CT history of any one member names
many more. Walking that graph from a few seeds enumerates the custom-domain cohort, which no
`*.zohorecruit.*` search can see: the host carries no Zoho name at all.

Every host is only a candidate. A bundle can hold a host whose career site has since gone, and a
custom domain is the *same Board* as its `{label}.zohorecruit.{tld}` host when both exist (same
`org_info.id`), so every host goes through `mine_zoho_vanity_resolve.py`, and only the
canonical host it names is landed.

Source: the Cert Spotter API (`/v1/issuances?domain=`), about 100 unauthenticated queries an
hour per IP; a 429 is slept out using its `retry-after`. (crt.sh answered 502 all day 2026-09-26.) Resumable: queried hosts are recorded in
`{OUT}.queried` and never asked again.

Run:  python -u scripts/discover/mine_zoho_vanity_ct.py SEEDS_FILE OUT_FILE [MAX_QUERIES]
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.certspotter.com/v1/issuances"
UA = "HeadStart-discovery/0.1 (ATS tenant discovery; polite)"
PACE = 1.0
#: A cert with fewer SANs than this is a customer's own, not a Zoho bundle; its names say nothing
#: about other Zoho customers.
MIN_BUNDLE_SANS = 10


def issuances(host: str) -> list[dict] | None:
    url = f"{API}?" + urllib.parse.urlencode({"domain": host, "expand": "dns_names"})
    for _ in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code != 429:
                return None
            wait = int(e.headers.get("retry-after") or 600) + 5
            print(f"  429: sleeping {wait}s", flush=True)
            time.sleep(wait)
        except Exception:  # noqa: BLE001 — timeouts and resets: back off and retry
            time.sleep(30)
    return None


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    seeds, out = Path(argv[1]), Path(argv[2])
    max_queries = int(argv[3]) if len(argv) > 3 else 10_000
    queried_file = out.with_suffix(".queried")
    known = set(out.read_text().split()) if out.exists() else set()
    queried = set(queried_file.read_text().split()) if queried_file.exists() else set()
    frontier = [h for h in seeds.read_text().split() if h not in queried]
    frontier += sorted(known - queried - set(frontier))
    certs_seen: set[str] = set()
    n = 0
    with out.open("a") as f, queried_file.open("a") as qf:
        while frontier and n < max_queries:
            host = frontier.pop(0)
            if host in queried:
                continue
            data = issuances(host)
            time.sleep(PACE)
            n += 1
            queried.add(host)
            qf.write(host + "\n")
            qf.flush()
            if data is None:
                continue
            new = 0
            for cert in data:
                names = [d.lower() for d in cert.get("dns_names", [])]
                if len(names) < MIN_BUNDLE_SANS or cert.get("id") in certs_seen:
                    continue
                certs_seen.add(cert.get("id"))
                for name in names:
                    if name.startswith("*.") or name in known:
                        continue
                    known.add(name)
                    f.write(name + "\n")
                    new += 1
                    frontier.append(name)
            f.flush()
            print(
                f"[{n}] {host}: {len(data)} certs, +{new} (known {len(known)}, "
                f"frontier {len(frontier)})",
                flush=True,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
