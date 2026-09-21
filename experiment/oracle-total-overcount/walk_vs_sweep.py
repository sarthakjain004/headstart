"""Differential loop: does Oracle's paginated walk lose rows the API would still serve?

For each Board: (a) the scraper's own listing walk, (b) an exhaustive sweep of every 200-row offset
window up to the stated total — past any empty page the walk stops on — deduped by requisition Id.
If walk == sweep everywhere, `TotalJobsCount` over-counts and a shortfall against it is not
evidence of an unread remainder. See LOG.md, and ADR-0169 for the decision it drove.

    python experiment/oracle-total-overcount/walk_vs_sweep.py etud.fa.us8.oraclecloud.com ...

A Board at or above the API's 10,000-offset ceiling is reported as `ceiling` and NOT counted as
verified: the sweep cannot look past the same wall the walk stops at, so `lost=0` there is true by
construction rather than measured.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import sys
import urllib.request
from pathlib import Path

# Resolve the package from the repo this file lives in, so the invocation in LOG.md works from
# anywhere. A hardcoded relative path here made the committed artifact unreproducible.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from headstart.scrapers import registry

UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    "Accept": "application/json",
}
PAGE = 200
CEILING = 10_000


def page(host: str, off: int) -> tuple[list[str], int | None]:
    url = (
        f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
        f"?onlyData=true&expand=requisitionList&finder=findReqs;limit={PAGE},offset={off}"
    )
    body = urllib.request.urlopen(
        urllib.request.Request(url, headers=UA), timeout=90
    ).read()
    item = (json.loads(body).get("items") or [{}])[0]
    rows = item.get("requisitionList") or []
    return [str(r.get("Id")) for r in rows], item.get("TotalJobsCount")


def sweep(host: str, stated: int) -> set[str]:
    """Every offset window up to `stated`, as_completed so one slow page can't hold up the rest."""
    offsets = list(range(0, min(stated, CEILING) + PAGE, PAGE))
    ids: set[str] = set()
    with cf.ThreadPoolExecutor(8) as ex:
        futures = {ex.submit(page, host, off): off for off in offsets}
        for fut in cf.as_completed(futures):
            got, _ = fut.result()
            ids.update(got)
    return ids


def one(host: str) -> dict:
    scraper = registry.get_scraper("oracle", host, host)
    # `_listing()` is the walk under test. `fetch_raw` would also run the detail pass, which costs
    # a request per posting and measures nothing this asks about.
    walk = {str(r.get("Id")) for r in scraper._listing()}
    _, stated = page(host, 0)
    stated = int(stated or 0)
    swept = sweep(host, stated) if stated else set()
    return {
        "host": host,
        "stated": stated,
        "walk": len(walk),
        "sweep": len(swept),
        "lost": len(swept - walk),
        "extra": len(walk - swept),
        # The sweep stops at the same wall the walk does, so this row proves nothing either way.
        "ceiling": stated > CEILING,
        "verdict": (scraper.truncated or "")[:46],
    }


def main(hosts: list[str]) -> None:
    print(
        f"{'host':46} {'stated':>7} {'walk':>6} {'sweep':>6} {'lost':>5} {'extra':>5} "
        f"{'ceiling':>7}  verdict",
        flush=True,
    )
    for host in hosts:
        try:
            r = one(host)
        except Exception as exc:  # noqa: BLE001 - one dead pod must not end the sweep
            print(f"{host[:46]:46} ERROR {type(exc).__name__}: {exc}"[:150], flush=True)
            continue
        print(
            f"{r['host'][:46]:46} {r['stated']:7} {r['walk']:6} {r['sweep']:6} "
            f"{r['lost']:5} {r['extra']:5} {r['ceiling']!s:>7}  {r['verdict']}",
            flush=True,
        )


if __name__ == "__main__":
    main(sys.argv[1:])
