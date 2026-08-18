#!/usr/bin/env python3
"""Tell a real SmartRecruiters Board from a slug that was never one — and read its job count.

Why this exists: the posting API cannot answer the question on its own. It returns
``200 {"totalFound": 0, "content": []}`` for a slug that has never existed, exactly as it does
for a real Board with no openings right now::

    api.smartrecruiters.com/v1/companies/definitelynotarealboard12345/postings -> 200, totalFound 0
    api.smartrecruiters.com/v1/companies/Zomato1/postings                      -> 200, totalFound 3

So any check that only reads that endpoint calls every candidate Live (that is what
``check_liveness.p_smartrecruiters`` does, and why the ledger reads 99.9% live).

Two signals together settle it, cheapest-first:

1. **The posting API's count.** ``totalFound > 0`` is proof on its own — a slug that was never a
   Board cannot serve open postings. It also hands back the canonical spelling of the slug in
   ``content[0].company.identifier`` (the API matches case-insensitively, but the Board's own
   casing is ``NestlePurinaPetCare``, not ``nestlepurinapetcare``).
2. **The Board host, redirect not followed** — the tie-break when the count is 0::

       careers.smartrecruiters.com/{slug} -> 200                          real Board, no openings
       careers.smartrecruiters.com/{slug} -> 302 jobs.smartrecruiters.com not a Board

Order matters, because of a second trap: the Board host *also* 302s for real Boards whose customer
runs the career site on its own domain. ``Accor`` 302s yet serves 512 postings. Asking the API
first means those are confirmed by their postings and never reach the tie-break. What the tie-break
genuinely cannot separate is a custom-domain Board with *zero* current openings from a slug that
never existed — identical from outside, and neither is worth anything to the index — so both are
reported as "not a Board".

Reads a candidate CSV with a ``tenant`` column, streams one verdict per line, and writes the
confirmed Boards to ``--out`` (``ats,tenant,url,jobs``) as they land.

Run:  python -u scripts/validate/confirm_smartrecruiters_boards.py CANDIDATES.csv --out CONFIRMED.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

UA = "headstart/0.1 (job-board reader)"
CTX = ssl._create_unverified_context()
TIMEOUT = 25
BOARD_HOST = "https://careers.smartrecruiters.com"
API = "https://api.smartrecruiters.com/v1/companies"

# ADR-0026: both hosts are SmartRecruiters', so cap in-flight requests and space request starts.
_PACE = 0.08  # seconds between request starts, process-wide (~12 req/s)
_pace_lock = threading.Lock()
_next_start = 0.0
_ban_until = 0.0


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Keep the 3xx — the redirect *is* the answer here."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _wait_turn() -> None:
    global _next_start
    with _pace_lock:
        start = max(time.monotonic(), _next_start, _ban_until)
        _next_start = start + _PACE
    delay = start - time.monotonic()
    if delay > 0:
        time.sleep(delay)


def _back_off(seconds: float) -> None:
    global _ban_until
    with _pace_lock:
        _ban_until = max(_ban_until, time.monotonic() + seconds)


def _fetch(
    url: str, want_body: bool, attempts: int = 4, timeout: int = TIMEOUT
) -> tuple[int | None, str]:
    """(status, body) — status None means we never got a clean answer."""
    for attempt in range(attempts):
        _wait_turn()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with _OPENER.open(req, timeout=timeout) as r:
                return r.status, (
                    r.read().decode("utf-8", "replace") if want_body else ""
                )
        except urllib.error.HTTPError as e:
            if e.code == 429:
                # A rate limit is about the whole process, so it gates every worker.
                _back_off(5 * (attempt + 1))
                continue
            if e.code in (500, 502, 503, 504):
                # A 5xx is about *this* request. Backing the global gate off for it would stall
                # every other worker on one flaky Board — measured at ~9% of candidates, which
                # dragged throughput from ~7/s to ~1/s. Sleep locally instead.
                time.sleep(1 + attempt)
                continue
            return e.code, ""
        except Exception:  # noqa: BLE001
            time.sleep(1 + attempt)
    return None, ""


def is_board(slug: str) -> bool | None:
    """True if the Board host serves this slug; None if we could not tell.

    Kept on a short leash: this only ever runs for candidates with **zero** postings, and some
    Board-host requests simply hang (``Evo4`` sits past 30 s). Letting those burn the default
    4 x 25 s would stall a worker for 100 s to settle a Board that has nothing to offer anyway.
    """
    status, _ = _fetch(
        f"{BOARD_HOST}/{urllib.parse.quote(slug)}",
        want_body=False,
        attempts=2,
        timeout=12,
    )
    if status == 200:
        return True
    if status in (301, 302, 303, 307, 308, 404, 400):
        return False
    return None


def board_jobs(slug: str) -> tuple[int | None, str]:
    """(job count, canonical slug) from the posting API; canonical falls back to the input."""
    status, body = _fetch(
        f"{API}/{urllib.parse.quote(slug)}/postings?limit=1", want_body=True
    )
    if status != 200:
        return None, slug
    try:
        data = json.loads(body)
    except ValueError:
        return None, slug
    count = data.get("totalFound")
    content = data.get("content") or []
    canonical = (
        (content[0].get("company") or {}).get("identifier") if content else None
    ) or slug
    return (count if isinstance(count, int) else None), canonical


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "candidates", help="CSV with a 'tenant' column (or one slug per line)"
    )
    ap.add_argument("--out", required=True, help="where confirmed Boards are written")
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()

    src = Path(args.candidates)
    text = src.read_text(encoding="utf-8")
    if "tenant" in text.split("\n", 1)[0]:
        slugs = [
            r["tenant"] for r in csv.DictReader(text.splitlines()) if r.get("tenant")
        ]
    else:
        slugs = [
            ln.strip()
            for ln in text.splitlines()
            if ln.strip() and not ln.startswith("#")
        ]
    # dedupe case-insensitively: the API matches case-insensitively, so two spellings are one Board
    uniq: dict[str, str] = {}
    for s in slugs:
        uniq.setdefault(s.lower(), s)
    slugs = list(uniq.values())
    print(f"{len(slugs)} candidate slugs to confirm", flush=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fh = out.open("w", newline="", encoding="utf-8")
    w = csv.writer(fh)
    w.writerow(["ats", "tenant", "url", "jobs"])
    lock = threading.Lock()
    tally = {"board": 0, "not": 0, "unknown": 0, "jobs": 0, "hiring": 0, "n": 0}

    def check(slug: str):
        count, canonical = board_jobs(slug)
        if count is None:
            return slug, None, None, slug  # API never answered cleanly
        if count > 0:
            return slug, True, count, canonical  # open postings prove the Board
        return slug, is_board(slug), 0, canonical  # 0 postings — the Board host decides

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(check, s) for s in slugs]
        for fut in as_completed(futures):
            slug, verdict, count, canonical = fut.result()
            with lock:
                tally["n"] += 1
                if verdict is True:
                    tally["board"] += 1
                    tally["jobs"] += count or 0
                    tally["hiring"] += 1 if (count or 0) > 0 else 0
                    w.writerow(
                        [
                            "smartrecruiters",
                            canonical,
                            f"careers.smartrecruiters.com/{canonical}",
                            count if count is not None else "",
                        ]
                    )
                    fh.flush()
                    print(
                        f"  [{tally['n']}/{len(slugs)}] BOARD  {canonical:<40} jobs={count}",
                        flush=True,
                    )
                elif verdict is False:
                    tally["not"] += 1
                    if tally["n"] % 200 == 0:
                        print(
                            f"  [{tally['n']}/{len(slugs)}] ... {tally['board']} boards so far",
                            flush=True,
                        )
                else:
                    tally["unknown"] += 1
                    print(f"  [{tally['n']}/{len(slugs)}] ?????  {slug}", flush=True)
    fh.close()
    print(
        f"\nconfirmed {tally['board']} Boards ({tally['hiring']} hiring, "
        f"{tally['jobs']} jobs) / {tally['not']} not Boards / {tally['unknown']} undetermined"
        f" -> {out}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
