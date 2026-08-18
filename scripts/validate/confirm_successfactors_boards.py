#!/usr/bin/env python3
"""Tell a real SuccessFactors RMK Board from a careers host that merely looks like one.

Why this exists: ``check_liveness.p_successfactors`` calls a host Live when ``/sitemap.xml`` is a
``<urlset>`` containing the substring ``/job/``, and reports that substring's count as the job
count. Plenty of non-SuccessFactors career sites satisfy that. Measured on the existing ledger
(2026-07-27): ``careers.nba.com`` is a **Phenom** site whose sitemap lists
``/job/JR000743/Content-Production-Lead-Local-Media`` — 47 ``/job/`` hits, recorded as 46
SuccessFactors jobs, and not a SuccessFactors board at all.

The separator is the URL *shape*, not the substring. Every RMK listing surface emits
``/job/{title-slug}/{numeric-id}/`` — the same ``_JOB_PATH`` the scraper parses with, and the
only thing it can actually turn into a Job. A host is confirmed only when a listing surface
yields at least one URL of that shape, so a confirmed count is the number of jobs the scraper
would really read.

The three surfaces are tried cheapest-first, mirroring ``scrapers/successfactors.py``:

1. ``/sitemap.xml`` as a **urlset** of ``/job/{slug}/{id}/`` — one GET enumerates the board.
2. ``/search/?startrow=0`` — the server-rendered pages, for tenants whose sitemap is the
   Google-jobs RSS feed (SAP, Alstom) or whose sitemap lists no jobs.
3. the sitemap **RSS** feed's ``<item>`` count — last resort, and only recorded when the feed
   is RMK's (``base.google.com/ns/1.0``), for tenants whose ``/search/`` is CSB-rendered.

CSB-only tenants (Ericsson-class) correctly fail all three: they have no RMK surface, so they are
reported ``csb`` rather than mis-recorded as a board.

Reads a file of hosts (one per line) or a CSV with a ``tenant`` column, streams one verdict per
line, and writes confirmed Boards to ``--out`` (``ats,tenant,url,jobs,surface``) as they land.

Run:  python -u scripts/validate/confirm_successfactors_boards.py HOSTS --out CONFIRMED.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
from headstart import http  # needs src on sys.path first

UA = "HeadStart-discovery/0.1 (ATS board confirmation)"
# The scraper's own job-URL shape: /job/{slug}/{numeric id}/ (scrapers/successfactors.py _JOB_PATH)
JOB_PATH = re.compile(r"(/job/[^\s\"'<>?#]+/(\d+)/)")
PROBE_CAP = 512 * 1024  # capped stream: RMK RSS feeds trickle at ~30 KB/s
TIMEOUT = 25
_ATTEMPTS = 3
_DNS = 6  # curl CURLE_COULDNT_RESOLVE_HOST — the host does not exist, never retried


def _stream(url: str, cap: int = PROBE_CAP) -> tuple[int, str]:
    """GET `url`, reading at most `cap` bytes. Returns (status, text); status 0 on an
    unsettled transport failure and -1 when the host does not resolve. Streamed because an RSS
    sitemap never ends in useful time.

    Retried, because a blip must never be recorded as "not a board": a smoke run at 10 workers
    turned 3 known-good boards into ``sitemap-unreachable`` purely transiently (2026-07-27).
    A DNS failure is definitive and never retried.
    """
    for attempt in range(_ATTEMPTS):
        try:
            r = http.session().request(
                "GET",
                url,
                headers={"User-Agent": UA},
                timeout=TIMEOUT,
                verify=False,
                stream=True,
            )
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "code", None) == _DNS:
                return -1, ""
            if attempt == _ATTEMPTS - 1:
                return 0, ""
            time.sleep(1.5 * (attempt + 1))
            continue
        chunks, size = [], 0
        try:
            for chunk in r.iter_content():
                chunks.append(chunk)
                size += len(chunk)
                if size >= cap:
                    break
        except Exception:  # noqa: BLE001, S110
            pass  # torn mid-stream: keep what arrived, it may already prove the shape
        finally:
            r.close()
        return r.status_code, b"".join(chunks).decode("utf-8", "replace")
    return 0, ""  # pragma: no cover - the loop above always returns


def confirm(host: str) -> tuple[str, str, int, str]:
    """(host, verdict, jobs, surface). verdict is 'rmk' | 'csb' | 'none' | 'error'."""
    status, text = _stream(f"https://{host}/sitemap.xml")
    if status == -1:
        return host, "none", 0, "nxdomain"
    if status == 0:
        return host, "error", 0, "sitemap-unreachable"
    if status == 200:
        n = len({m.group(2) for m in JOB_PATH.finditer(text)})
        if n:
            return host, "rmk", n, "sitemap"
    is_rss = "base.google.com/ns/1.0" in text or "<rss" in text

    # 2. the server-rendered search pages
    s2, t2 = _stream(f"https://{host}/search/?startrow=0")
    if s2 == 200:
        n = len({m.group(2) for m in JOB_PATH.finditer(t2)})
        if n:
            return host, "rmk", n, "search"

    # 3. the RMK RSS feed itself — only counts when the feed is RMK's
    if is_rss:
        n = text.count("<item>")
        if n:
            return host, "rmk", n, "rss"
        return host, "csb", 0, "rss-empty"
    if status == 200 and ("<urlset" in text or "<sitemapindex" in text):
        return host, "none", 0, "sitemap-not-rmk"
    return host, "none", 0, f"http-{status}"


def load_hosts(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines and "," in lines[0] and "tenant" in lines[0].lower():
        rows = csv.DictReader(lines)
        return list(
            dict.fromkeys(r["tenant"].strip().lower() for r in rows if r.get("tenant"))
        )
    return list(dict.fromkeys(ln.lower() for ln in lines if "." in ln))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("hosts")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=32)
    ap.add_argument(
        "--all-out", help="optional CSV of every verdict, not just confirmed"
    )
    args = ap.parse_args()

    hosts = load_hosts(Path(args.hosts))
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"confirming {len(hosts)} hosts, {args.workers} workers", flush=True)

    counts: dict[str, int] = {}
    all_f = None
    all_w = None
    if args.all_out:
        all_f = Path(args.all_out).open("w", newline="", encoding="utf-8")  # noqa: SIM115
        all_w = csv.writer(all_f)
        all_w.writerow(["host", "verdict", "jobs", "surface"])

    done = 0
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ats", "tenant", "url", "jobs", "surface"])
        f.flush()
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = [ex.submit(confirm, h) for h in hosts]
            for fut in as_completed(
                futs
            ):  # as_completed: one slow host can't stall the rest
                host, verdict, jobs, surface = fut.result()
                counts[verdict] = counts.get(verdict, 0) + 1
                done += 1
                if all_w:
                    all_w.writerow([host, verdict, jobs, surface])
                    all_f.flush()
                if verdict == "rmk":
                    w.writerow(["successfactors", host, host, jobs, surface])
                    f.flush()  # stream: a killed run keeps every board already confirmed
                    print(f"  RMK  {host:<45} {jobs:>6} jobs  ({surface})", flush=True)
                if done % 250 == 0:
                    print(
                        f"  ... {done}/{len(hosts)} {dict(sorted(counts.items()))}",
                        flush=True,
                    )
    if all_f:
        all_f.close()
    print(f"DONE {dict(sorted(counts.items()))} -> {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
