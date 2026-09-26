#!/usr/bin/env python3
"""Zoho Recruit vanity career hosts from the live TLS certificates of known ones.

Zoho serves every customer's vanity career host (`careers.acme.com`, CNAMEd to
`recruit.cs.zohohost.{dc}`) behind Let's Encrypt certificates that bundle ~80-100 customers'
hosts as SANs. One handshake with SNI set to a known vanity host returns its current bundle,
so walking handshakes from a seed list names the whole current cohort — with no rate limit,
unlike the CT history `mine_zoho_vanity_ct.py` reads (which also names members that
have since left). Measured 2026-09-26: 3,510 known hosts -> 305 more, then a fixed point after
one further round (0 new).

Every host is a candidate only: run `mine_zoho_vanity_resolve.py` on the output and land the
canonical `{label}.zohorecruit.{dc}` host it names, never the vanity host.

Output: new hosts (not in SEEDS), one per line, streamed. 16 concurrent handshakes.

Run:  python -u scripts/discover/mine_zoho_vanity_tls_sans.py SEEDS_FILE > NEW_HOSTS.txt
"""

import socket
import ssl
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

WORKERS = 16
#: A cert with fewer SANs is a customer's own, not a Zoho bundle.
MIN_BUNDLE_SANS = 10
CONTEXT = ssl.create_default_context()


def bundle_of(host: str) -> list[str]:
    try:
        with (
            socket.create_connection((host, 443), timeout=10) as sock,
            CONTEXT.wrap_socket(sock, server_hostname=host) as tls,
        ):
            cert = tls.getpeercert() or {}
    except (OSError, ssl.SSLError):
        return []
    return [v.lower() for k, v in cert.get("subjectAltName", ()) if k == "DNS"]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    with open(argv[1]) as f:
        seen = set(f.read().split())
    frontier = sorted(seen)
    while frontier:
        found: set[str] = set()
        with ThreadPoolExecutor(WORKERS) as pool:
            for fut in as_completed([pool.submit(bundle_of, h) for h in frontier]):
                names = fut.result()
                if len(names) < MIN_BUNDLE_SANS:
                    continue
                for name in names:
                    if name not in seen and not name.startswith("*."):
                        seen.add(name)
                        found.add(name)
                        print(name, flush=True)
        frontier = sorted(found)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
