"""Measure the public Taleo Enterprise detail-page concurrency knee.

Writes each completed request immediately to `artifacts/detail_ladder.jsonl`.
The workload is 32 distinct D.R. Horton public requisitions, two passes per width.
"""

from __future__ import annotations

import json
import time
from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from curl_cffi import requests

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "artifacts" / "detail_ladder_v2.jsonl"
BASE = "https://drhorton.taleo.net/careersection/2/jobsearch.ftl?lang=en"
API = "https://drhorton.taleo.net/careersection/rest/jobboard/searchjobs?lang=en&portal=101430233"
WIDTHS = (1, 4, 8, 16, 32)


def payload(page: int) -> dict:
    return {
        "advancedSearchFiltersSelectionParam": {
            "searchFilterSelections": [
                {"id": key, "selectedValues": []}
                for key in (
                    "ORGANIZATION", "LOCATION", "JOB_FIELD", "URGENT_JOB",
                    "EMPLOYEE_STATUS", "STUDY_LEVEL", "WILL_TRAVEL", "JOB_SHIFT", "JOB_NUMBER",
                )
            ]
        },
        "fieldData": {"fields": {"JOB_TITLE": "", "KEYWORD": "", "LOCATION": ""}, "valid": True},
        "filterSelectionParam": {
            "searchFilterSelections": [
                {"id": key, "selectedValues": []}
                for key in ("POSTING_DATE", "LOCATION", "JOB_FIELD", "JOB_TYPE", "JOB_SCHEDULE", "JOB_LEVEL")
            ]
        },
        "multilineEnabled": False,
        "pageNo": page,
        "sortingSelection": {"ascendingSortingOrder": "false", "sortBySelectionParam": "3"},
    }


def detail(url: str) -> dict:
    started = time.monotonic()
    try:
        response = requests.get(url, headers={"User-Agent": "headstart/0.1"}, impersonate="chrome", timeout=30)
        return {"status": response.status_code, "bytes": len(response.content), "seconds": time.monotonic() - started}
    except Exception as exc:  # noqa: BLE001 -- outcome data, never hide it
        return {"status": type(exc).__name__, "bytes": 0, "seconds": time.monotonic() - started}


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument("--width", type=int, choices=WIDTHS)
    args = parser.parse_args()
    headers = {"User-Agent": "headstart/0.1", "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest", "tz": "GMT+00:00", "Referer": BASE}
    session = requests.Session(impersonate="chrome")
    session.get(BASE, headers=headers, timeout=30)
    rows = session.post(API, json=payload(1), headers=headers, timeout=30).json()["requisitionList"]
    rows += session.post(API, json=payload(2), headers=headers, timeout=30).json()["requisitionList"]
    urls = [f"https://drhorton.taleo.net/careersection/2/jobdetail.ftl?lang=en&job={row['jobId']}" for row in rows[:16]]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as out:
        for run in range(2):
            started = time.monotonic()
            workload = urls if run == 0 else list(reversed(urls))
            with ThreadPoolExecutor(max_workers=args.width) as pool:
                futures = {pool.submit(detail, url): url for url in workload}
                for future in as_completed(futures):
                    row = {"width": args.width, "run": run, "url": futures[future], **future.result()}
                    out.write(json.dumps(row) + "\n")
                    out.flush()
                    print(json.dumps(row), flush=True)
            print(json.dumps({"width": args.width, "run": run, "elapsed": time.monotonic() - started, "complete": True}), flush=True)


if __name__ == "__main__":
    main()
