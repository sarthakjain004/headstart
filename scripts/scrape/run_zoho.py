#!/usr/bin/env python3
"""Run the Zoho scraper over the merged Zoho tenants — real jobs, not soft-200 liveness.

Reads data/ats-tenants-merged/zoho.csv, fetches each tenant's careers page concurrently,
parses real Job rows, and writes them to data/jobs/zoho.csv. Prints a summary: how many
tenants actually have open roles (true liveness) vs live-but-empty vs dead/error.

Usage:  python scripts/scrape/run_zoho.py [workers] [limit]
        python scripts/scrape/run_zoho.py 10 50   # 10 workers, first 50 tenants (smoke test)
"""
import csv
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
from headstart.scrapers.zoho import ZohoScraper  # noqa: E402

SRC = ROOT / "data" / "ats-tenants-merged" / "zoho.csv"
OUT = ROOT / "data" / "jobs" / "zoho.csv"


def host_of(url, tenant):
    return urlparse(url).netloc or f"{tenant}.zohorecruit.com"


def scrape(row):
    host = host_of(row["url"], row["tenant"])
    try:
        return host, ZohoScraper(host, row["tenant"]).fetch(), None
    except Exception as exc:
        return host, [], type(exc).__name__


def main():
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    rows = list(csv.DictReader(SRC.open(encoding="utf-8")))
    if limit:
        rows = rows[:limit]
    OUT.parent.mkdir(parents=True, exist_ok=True)

    probed = hiring = empty = errors = total_jobs = 0
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["company", "title", "location", "remote", "department", "url", "posted_at", "host"])
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for fut in as_completed([ex.submit(scrape, r) for r in rows]):
                host, jobs, err = fut.result()
                probed += 1
                if err:
                    errors += 1
                elif jobs:
                    hiring += 1
                    total_jobs += len(jobs)
                    for j in jobs:
                        w.writerow([j.company, j.title, j.location, j.remote,
                                    j.department, j.url, j.posted_at, host])
                else:
                    empty += 1
                if probed % 250 == 0:
                    f.flush()
                    print(f"  {probed}/{len(rows)} | hiring={hiring} jobs={total_jobs} "
                          f"empty={empty} err={errors}", flush=True)
    print(f"DONE: {probed} tenants | hiring={hiring} | live-empty={empty} | "
          f"dead/error={errors} | total_jobs={total_jobs}", flush=True)
    print("jobs -> data/jobs/zoho.csv", flush=True)


if __name__ == "__main__":
    main()
