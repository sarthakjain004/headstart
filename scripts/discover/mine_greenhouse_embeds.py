#!/usr/bin/env python3
"""Embedded-Board miner — harvest Greenhouse Slugs from the `?for=` embed fingerprint.

mine_greenhouse.py page-mines the board hosts and takes the first path segment, which only
ever sees a Company that serves its Board at `{host}/{slug}`. A Board that is *embedded* —
iframed or script-injected into the Company's own careers page via
`greenhouse.io/embed/job_board?for={slug}` — has no such URL, so that miner skips it outright
(its `extract()` drops the `embed` segment). Those Boards are reachable only through the
`for=` query parameter, and Wayback archives the embed subresource whenever it archives the
careers page holding it. This mines exactly that parameter.

The same fingerprint also covers the custom-domain case: a Company serving Greenhouse from its
own host still loads the embed from `boards.greenhouse.io`, and its apply links carry
`gh_src=`/`gh_jid=` back to a `job-boards.greenhouse.io/{slug}` URL.

Measured 2026-07-27 (first run, against an 11,782-Slug ledger): 71,271 archived embed URLs ->
7,195 distinct Slugs -> 3,542 not already known -> **875 confirmed live** via
`boards-api.greenhouse.io/v1/boards/{slug}` (~25%). That is the single highest-yield Greenhouse
angle measured; the public crawled Slug lists and the grnh.se shortener both returned 0 new.

New Slugs are folded into data/wayback-ats/greenhouse.csv (candidate-grade — historical, so it
includes long-dead Boards). Validate with:

    python scripts/validate/check_liveness.py --dir data/wayback-ats greenhouse

Wayback rate-limits by IP and will start returning empty bodies after a sustained run; the page
state file makes this resumable, so just re-run it later to pick up the pages that failed.

Run:  python scripts/discover/mine_greenhouse_embeds.py
"""

import csv
import re
import socket
import ssl
import sys
import threading
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
WB = ROOT / "data" / "wayback-ats"
TIMEOUT = 180
UA = "HeadStart-wayback/0.1 (ATS board discovery)"
CTX = ssl._create_unverified_context()
socket.setdefaulttimeout(TIMEOUT)
WORKERS = 4  # polite: Wayback CDX blocks an IP that bursts

# Every host that serves an embed. The US legacy host carries almost all of it; the EU pair is
# small but is where the regional-only Companies sit.
HOSTS = [
    ("gh_emb_b", "boards.greenhouse.io/embed"),
    ("gh_emb_jb", "job-boards.greenhouse.io/embed"),
    ("gh_emb_eu_b", "boards.eu.greenhouse.io/embed"),
    ("gh_emb_eu_jb", "job-boards.eu.greenhouse.io/embed"),
]
# A Slug is a lowercase label; a bare number is a job id (the `token=` param), never a Slug.
VALID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}$")


def get(url, tries=4):
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001, S110
            pass
    return None


def slugs_in(url):
    """The `for=` values in one archived URL (the embed's Slug parameter)."""
    out = []
    for k, v in urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query):
        if k.lower() != "for":
            continue
        s = v.strip().lower()
        if VALID.match(s) and not s.isdigit():
            out.append(s)
    return out


def mine(label, prefix, seen, write):
    """Page-mine one host prefix, calling write(slug) for each newly-seen Slug."""
    q = urllib.parse.quote(prefix, safe="")
    cdx = f"https://web.archive.org/cdx/search/cdx?url={q}&matchType=prefix"
    npages = get(cdx + "&showNumPages=true")
    if not npages or not npages.strip().isdigit():
        print(f"{label}: no page count (rate-limited?) — re-run later", flush=True)
        return
    npages = int(npages.strip())
    state = WB / f".{label}_pages_done"
    done = (
        {int(x) for x in state.read_text().split() if x.strip().isdigit()}
        if state.exists()
        else set()
    )
    todo = [p for p in range(npages) if p not in done]
    print(f"{label}: {npages} pages, {len(todo)} to fetch", flush=True)
    lock = threading.Lock()
    sf = state.open("a", encoding="utf-8")

    def do(page):
        text = get(f"{cdx}&fl=original&collapse=urlkey&page={page}")
        if text is None:
            return  # leave the page unmarked so a re-run retries it
        with lock:
            new = 0
            for line in text.split("\n"):
                for s in slugs_in(line.strip()):
                    if s not in seen:
                        seen.add(s)
                        write(s)
                        new += 1
            sf.write(f"{page}\n")
            sf.flush()
            print(
                f"  {label} page {page}: +{new} slugs ({len(seen)} total)", flush=True
            )

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for fut in as_completed([ex.submit(do, p) for p in todo]):
            fut.result()
    sf.close()


def main():
    WB.mkdir(parents=True, exist_ok=True)
    out = WB / "greenhouse.csv"
    seen = set()
    if out.exists():
        with out.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                seen.add(r["tenant"].lower())
    else:
        out.write_text("ats,tenant,url\n", encoding="utf-8")
    before = len(seen)
    fh = out.open("a", newline="", encoding="utf-8")
    w = csv.writer(fh)

    def write(slug):
        w.writerow(["greenhouse", slug, f"https://job-boards.greenhouse.io/{slug}"])
        fh.flush()  # stream: a crash keeps everything already found

    for label, prefix in HOSTS:
        print(f"=== mining {prefix} ===", flush=True)
        mine(label, prefix, seen, write)
    fh.close()
    print(
        f"greenhouse.csv: {before} -> {len(seen)} (+{len(seen) - before})", flush=True
    )
    print(
        "validate: python scripts/validate/check_liveness.py --dir data/wayback-ats greenhouse",
        flush=True,
    )


if __name__ == "__main__":
    sys.exit(main())
