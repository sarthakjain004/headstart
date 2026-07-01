"""Query the Common Crawl columnar (Parquet) cc-index for one crawl via DuckDB over
plain HTTPS (no S3 auth/listing). Reads the per-crawl paths manifest, then filters the
warc parquet files by url_host_registered_domain. Usage: python -u duck_probe.py [crawl]
"""

import gzip
import subprocess
import sys
import time

import duckdb

crawl = sys.argv[1] if len(sys.argv) > 1 else "CC-MAIN-2024-51"
raw = subprocess.run(
    [
        "curl",
        "-s",
        "-m",
        "60",
        f"https://data.commoncrawl.org/crawl-data/{crawl}/cc-index-table.paths.gz",
    ],
    capture_output=True,
).stdout
paths = gzip.decompress(raw).decode().splitlines()
urls = [
    "https://data.commoncrawl.org/" + p
    for p in paths
    if "/subset=warc/" in p and p.endswith(".parquet")
]
print(f"{crawl}: {len(urls)} warc parquet files", flush=True)

DOMAINS = [
    "zohorecruit.in",
    "zohorecruit.com",
    "zohorecruit.eu",
    "freshteam.com",
    "darwinbox.in",
    "darwinbox.com",
    "keka.com",
    "greythr.com",
    "peoplestrong.com",
    "jobsoid.com",
    "ripplehire.com",
    "turbohire.co",
    "qandle.com",
    "beehivehcm.com",
    "workable.com",
    "recruitee.com",
]
dlist = ",".join(f"'{d}'" for d in DOMAINS)

con = duckdb.connect()
con.execute("INSTALL httpfs; LOAD httpfs;")
con.execute(
    "SET threads=1;"
)  # fully sequential -> one request at a time, gentlest on CC
con.execute("SET http_retries=8;")
con.execute("SET http_retry_wait_ms=3000;")
con.execute("SET http_keep_alive=true;")
t = time.time()
q = f"""SELECT url_host_registered_domain AS ats, count(DISTINCT url_host_name) AS hosts
        FROM read_parquet({urls!r})
        WHERE url_host_registered_domain IN ({dlist})
        GROUP BY 1 ORDER BY 2 DESC"""
rows = con.execute(q).fetchall()
for ats, n in rows:
    print(f"  {ats:20} {n}", flush=True)
print(
    f"total tenant hosts: {sum(n for _, n in rows)} | elapsed: {round(time.time() - t, 1)}s",
    flush=True,
)
