#!/usr/bin/env python3
"""Zwayam board verifier — ask public.zwayam.com whether a hostname is a registered tenant.

Zwayam (Info Edge / Naukri Talent Cloud) hosts every customer career site on the *customer's own*
domain, so there is no `*.zwayam.com` namespace to enumerate and no slug to derive. The only ground
truth is the search endpoint itself: it keys off the `domain` form field (the `companyId` field is
accepted but IGNORED — verified 2026-08-27), so posting a candidate hostname and reading back
`data.totalCount` is a definitive registered/not-registered answer.

Akamai IP-blocks direct bursts with a persistent 403, so every request goes through Cloudflare WARP
(socks5h://127.0.0.1:40000) with a browser User-Agent — without the UA the body comes back empty.

Registered   -> HTTP 200, data.totalCount + data.data[]
Unregistered -> HTTP 200, "data": null

Reads candidate hostnames (one per line, `#` comments ok) from a file or stdin; appends
`host,jobs` for every registered board to OUT and streams progress. Re-running resumes: hosts
already probed (in OUT or in the companion `.checked` file) are skipped.

Run:  python -u scripts/discover/zwayam_probe.py CANDIDATES.txt OUT.csv [--workers 4]
"""

import argparse
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PROXY = "socks5h://127.0.0.1:40000"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
FILTER_CRI = json.dumps(
    {
        "paginationStartNo": 0,
        "selectedCall": "sort",
        "sortCriteria": {"name": "modifiedDate", "isAscending": False},
        "anyOfTheseWords": "",
    }
)
PACE = 0.25
_lock = threading.Lock()


def probe(host: str, attempts: int = 3):
    """(jobs, status) for one candidate. jobs is None when not a registered tenant.

    status: "ok" (answered, registered or not), "blocked" (403 that outlived the backoff),
    "error" (transport failure / unparseable body)."""
    for i in range(attempts):
        p = subprocess.run(  # noqa: PLW1510
            [
                "curl",
                "-s",
                "-x",
                PROXY,
                "-w",
                "\n%{http_code}",
                "--connect-timeout",
                "15",
                "-m",
                "60",
                "-A",
                UA,
                "-H",
                f"Origin: https://{host}",
                "-H",
                f"Referer: https://{host}/",
                "-F",
                f"filterCri={FILTER_CRI}",
                "-F",
                f"domain={host}",
                "-F",
                "companyId=MQ==",
                "https://public.zwayam.com/jobs/search",
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
        out = p.stdout
        nl = out.rfind("\n")
        code = out[nl + 1 :].strip() if nl >= 0 else ""
        body = out[:nl] if nl >= 0 else ""
        if code == "403":
            time.sleep(60)
            continue
        if code != "200" or not body.strip():
            time.sleep(3 * (i + 1))
            continue
        try:
            data = json.loads(body).get("data")
        except Exception:  # noqa: BLE001
            return None, "error"
        if not data:
            return None, "ok"
        return int(data.get("totalCount") or 0), "ok"
    return None, "blocked" if code == "403" else "error"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("candidates")
    ap.add_argument("out")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()

    raw = (
        sys.stdin.read()
        if a.candidates == "-"
        else Path(a.candidates).read_text(errors="replace")
    )
    cands = []
    for ln in raw.splitlines():
        h = ln.split("#")[0].strip().lower().strip("/")
        if h.startswith("http"):
            h = h.split("://", 1)[1].split("/")[0]
        if h and "." in h and h not in cands:
            cands.append(h)

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    checked_p = out.with_suffix(out.suffix + ".checked")
    done = set()
    if checked_p.exists():
        done |= {ln.strip() for ln in checked_p.read_text().splitlines() if ln.strip()}
    todo = [h for h in cands if h not in done]
    print(
        f"zwayam-probe: {len(todo)} to probe ({len(cands) - len(todo)} already checked)",
        flush=True,
    )

    hits = 0
    with (
        out.open("a") as fo,
        checked_p.open("a") as fc,
        ThreadPoolExecutor(a.workers) as ex,
    ):
        futs = {}
        for h in todo:
            futs[ex.submit(probe, h)] = h
            time.sleep(PACE)
        for n, fu in enumerate(as_completed(futs), start=1):
            h = futs[fu]
            try:
                jobs, status = fu.result()
            except Exception as e:  # noqa: BLE001
                print(f"  [{n}/{len(todo)}] {h}: error {e}", flush=True)
                continue
            with _lock:
                if status == "ok":
                    fc.write(h + "\n")
                    fc.flush()
                if jobs is not None:
                    hits += 1
                    fo.write(f"{h},{jobs}\n")
                    fo.flush()
                    print(f"  [{n}/{len(todo)}] HIT {h}: {jobs} jobs", flush=True)
                elif status != "ok":
                    print(f"  [{n}/{len(todo)}] {h}: {status}", flush=True)
                elif n % 25 == 0:
                    print(f"  [{n}/{len(todo)}] ... {hits} hits so far", flush=True)
    print(f"DONE {hits} registered boards -> {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
