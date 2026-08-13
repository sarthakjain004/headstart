#!/usr/bin/env python3
"""Page-based Wayback harvester for one ATS — the method for dense domains.

The CDX index is split into N pages (see ?showNumPages=true). Unlike the flat limit (stuck on
early slugs) or filters/host-collapse (which time out), every page is directly addressable
with &page=K, so we fetch ALL pages CONCURRENTLY, extract hosts, and dedup to the full slug
set in one bounded pass.

Sweeps every board host the ATS serves from (`wayback_feeder.ATS_HOSTS`), not just one: Zoho
alone spreads 8,197 known slugs over 8 TLDs. Writes new slugs to data/wayback-ats/{ats}.csv
as pages complete. Resumable: completed page numbers are recorded per host in
data/wayback-ats/.{ats}_{host}_pages_done, so re-running skips finished pages.

Usage:  python scripts/discover/wayback_pages.py zoho
        python scripts/discover/wayback_pages.py zoho --workers 20
        python scripts/discover/wayback_pages.py zoho --domain zohorecruit.in   # one host only
        python scripts/discover/wayback_pages.py turbohire --domain turbohire.co --style sub
"""

import socket
import ssl
import threading
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from wayback_feeder import WB, adopt_legacy_state, cli, extract, hosts_for, slug_sink

TIMEOUT = 120
UA = "HeadStart-wayback/0.1 (ATS tenant discovery)"
CTX = ssl._create_unverified_context()
socket.setdefaulttimeout(TIMEOUT)


def get(url):
    for _ in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            pass
    return None


def sweep(ats, domain, style, workers, sink):
    """Harvest every CDX page for one host, appending new slugs as pages land."""
    cdx = f"https://web.archive.org/cdx/search/cdx?url={urllib.parse.quote(domain)}&matchType=domain"
    base = cdx + "&fl=original&collapse=urlkey"  # showNumPages needs the clean url

    npages_txt = get(cdx + "&showNumPages=true")
    if not npages_txt or not npages_txt.strip().isdigit():
        print(f"{ats}/{domain}: could not get page count: {npages_txt!r}", flush=True)
        return
    npages = int(npages_txt.strip())

    state = WB / f".{ats}_{domain}_pages_done"
    done = set()
    if state.exists():
        done = {int(x) for x in state.read_text().split() if x.strip().isdigit()}
    todo = [p for p in range(npages) if p not in done]
    print(
        f"{ats}/{domain}: {npages} pages, {len(done)} done, {len(todo)} to fetch",
        flush=True,
    )

    lock = threading.Lock()
    counter = {"n": 0, "new": 0}
    with state.open("a", encoding="utf-8") as sf:

        def do(page):
            text = get(f"{base}&page={page}")
            if text is None:
                return
            urls = [ln for ln in text.split("\n") if ln]
            with lock:
                for u in urls:
                    found = extract(u, domain, style)
                    if found and sink.add(found, style):
                        counter["new"] += 1
                sink.flush()
                sf.write(f"{page}\n")
                sf.flush()
                counter["n"] += 1
                if counter["n"] % 25 == 0:
                    print(
                        f"  {ats}/{domain}: {counter['n']}/{len(todo)} pages, "
                        f"+{counter['new']} new",
                        flush=True,
                    )

        with ThreadPoolExecutor(max_workers=workers) as ex:
            for fut in as_completed([ex.submit(do, p) for p in todo]):
                fut.result()
    print(f"{ats}/{domain}: done, +{counter['new']} new", flush=True)


def main():
    ap = cli(__doc__)
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()
    # Resolve first: both calls below touch the filesystem, and a bad argument should
    # not leave a stray CSV or a renamed cursor behind before it is rejected.
    targets = hosts_for(args.ats, args.domain, args.style)
    adopt_legacy_state(args.ats, "pages_done")
    with slug_sink(args.ats) as sink:
        for domain, style in targets:
            sweep(args.ats, domain, style, args.workers, sink)


if __name__ == "__main__":
    main()
