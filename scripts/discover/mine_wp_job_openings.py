#!/usr/bin/env python3
"""WP Job Openings sites from Common Crawl's columnar URL index and urlscan.io, pooled under their
own host.

WP Job Openings (now branded HireZoot) is a WordPress plugin, so a Board sits on its company's own
host and there is no vendor namespace for the CDX API's ``matchType=domain`` to sweep. What every
site shares is the plugin's own vocabulary in its URLs (:data:`FINGERPRINT`): the post type
``awsm_job_openings`` — its REST route ``/wp/v2/awsm_job_openings/{id}``, which every job page
links in its ``<head>``; its sitemap ``awsm_job_openings-sitemap.xml``; the
``?post_type=awsm_job_openings&p={id}`` link a job's guid carries — and the plugin directory
``/wp-content/plugins/wp-job-openings/``. The columnar index (``cc-index/table``, one Parquet file
set per crawl) can be filtered on ``url_path`` and ``url_query`` across every host, which the CDX
API cannot; DuckDB reads just those two columns over HTTPS, about 63 MB of each 370 MB part file.

``mine`` reads both the ``warc`` subset (pages fetched) and ``crawldiagnostics`` (redirects and
errors: a guid link 301s to the job's pretty URL) of each crawl. Each (crawl, part file) is
checkpointed once it has been read in full, so a killed run resumes; a part file that failed is
retried next run, never read as empty. The query is broader than the fingerprint (it also matches
wordpress.org's own page for the plugin); ``pool`` applies the fingerprint.

``urlscan`` walks urlscan.io's scans whose page loaded a file of the plugin's (:func:`urlscan`), a
source that sees sites Common Crawl never fetched a plugin URL of.

``pool`` turns the captured and scanned hosts, and any ``--hosts`` file of hosts found another way
(the careers-page fingerprinter's), into rows of ``data/ats-tenants-merged/wp_job_openings.csv``
under the host each site's own REST API names — the host its postings link to, else its REST
index's ``home``, else the host a redirect lands on. ``acude.uy`` redirects to ``www.acude.uy`` and
``www.adridgemedia.com`` lists ``adridgemedia.com``'s postings, and the pool must hold each Board
once. A host whose route answers nothing readable is kept as it is, for
``check_liveness.p_wp_job_openings`` to settle.

What it does not cover: a site neither source ever saw load or link a plugin URL — one whose job
URLs all sit under a custom permalink base (``/career/{slug}/``) and that no one submitted to
urlscan.

``mine`` needs ``duckdb`` (not a project dependency; ``duck_probe.py`` is the precedent).

Run:  python -u scripts/discover/mine_wp_job_openings.py mine [CRAWL ...]  (default: newest 6)
      python -u scripts/discover/mine_wp_job_openings.py urlscan [--pages N]
      python -u scripts/discover/mine_wp_job_openings.py pool [--hosts FILE --source TAG]
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import re
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from headstart.network import http
from headstart.scrapers.wp_job_openings import IMPERSONATE

CAPTURES = ROOT / "data" / "discover" / "wp_job_openings_cc_urls.csv"
DONE = ROOT / "data" / "discover" / "wp_job_openings_cc_checkpoint.txt"
URLSCAN_HOSTS = ROOT / "data" / "discover" / "wp_job_openings_urlscan_hosts.csv"
URLSCAN_CURSOR = ROOT / "data" / "discover" / "wp_job_openings_urlscan_cursor.txt"
POOL = ROOT / "data" / "ats-tenants-merged" / "wp_job_openings.csv"
DATA = "https://data.commoncrawl.org"
SUBSETS = ("warc", "crawldiagnostics")
WORKERS = int(os.environ.get("WORKERS") or 6)
QUERY = """SELECT url_host_name, url FROM read_parquet('{src}')
WHERE contains(url_path, 'awsm_job_openings') OR contains(url_path, 'wp-job-openings')
   OR contains(url_query, 'awsm_job_openings')"""
#: A URL only a site running the plugin serves.
FINGERPRINT = re.compile(
    r"[?&](?:amp;|#038;)?(?:post_type=)?awsm_job_openings[=&]"
    r"|/wp/v2/awsm_job_openings\b"
    r"|awsm_job_openings-sitemap"
    r"|wp-sitemap-posts-awsm_job_openings"
    r"|/wp-content/plugins/wp-job-openings/",
    re.IGNORECASE,
)
_ROUTE = "?rest_route=/wp/v2/awsm_job_openings&per_page=1&_fields=link"
#: A copy of a site rather than the site: a hosting platform's staging or temporary domain, or a
#: staging label. On the first full ledger (2026-09-28) 50 live hosts matched, copying real sites'
#: postings (`stg-principalresourcingcouk-staging.kinsta.cloud` 50, `beta.billionreaders.org` 23).
NONPRODUCTION = re.compile(
    r"^(?:staging|stage|stg|dev|test|beta|demo|template|sandbox|uat|preprod)\d*[.-]"
    r"|\.(?:kinsta\.cloud|wpenginepowered\.com|wpengine\.com|hostingersite\.com"
    r"|myftpupload\.com|azurewebsites\.net|tempurl\.host|pantheonsite\.io|flywheelsites\.com"
    r"|flywheelstaging\.com|cloudwaysapps\.com|instawp\.xyz|wpcomstaging\.com|sg-host\.com"
    r"|stackstaging\.com)$"
)

_write = threading.Lock()


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "headstart/0.1"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def newest_crawls(n: int) -> list[str]:
    info = json.loads(_get("https://index.commoncrawl.org/collinfo.json"))
    return [crawl["id"] for crawl in info[:n]]


def part_files(crawl: str) -> list[str]:
    paths = gzip.decompress(
        _get(f"{DATA}/crawl-data/{crawl}/cc-index-table.paths.gz")
    ).decode()
    return [
        path
        for path in paths.splitlines()
        if path.endswith(".parquet")
        and any(f"/subset={subset}/" in path for subset in SUBSETS)
    ]


def scan(path: str) -> list[tuple[str, str]]:
    import duckdb

    con = duckdb.connect()
    con.execute("LOAD httpfs; SET http_retries=6; SET http_retry_wait_ms=2000;")
    try:
        return con.execute(QUERY.format(src=f"{DATA}/{path}")).fetchall()
    finally:
        con.close()


def mine(crawls: list[str]) -> None:
    import duckdb

    duckdb.connect().execute("INSTALL httpfs;")
    done = set(DONE.read_text().split()) if DONE.exists() else set()
    CAPTURES.parent.mkdir(parents=True, exist_ok=True)
    fresh = not CAPTURES.exists()
    with CAPTURES.open("a", newline="") as out, DONE.open("a") as checkpoint:
        writer = csv.writer(out)
        if fresh:
            writer.writerow(["host", "url", "crawl"])
        for crawl in crawls or newest_crawls(6):
            todo = [p for p in part_files(crawl) if p not in done]
            print(f"{crawl}: {len(todo)} part files to read", flush=True)
            started, hits, failed = time.time(), 0, 0
            with ThreadPoolExecutor(WORKERS) as pool:
                futures = {pool.submit(scan, path): path for path in todo}
                for n, future in enumerate(as_completed(futures), 1):
                    path = futures[future]
                    try:
                        rows = future.result()
                    except Exception as exc:  # noqa: BLE001 — retried next run
                        failed += 1
                        print(f"  failed {path.rsplit('/', 1)[1]}: {exc}", flush=True)
                        continue
                    with _write:
                        writer.writerows((host, url, crawl) for host, url in rows)
                        out.flush()
                        checkpoint.write(path + "\n")
                        checkpoint.flush()
                    hits += len(rows)
                    if rows or n % 25 == 0:
                        print(
                            f"  {crawl} {n}/{len(todo)} +{len(rows)} urls "
                            f"({hits} so far, {time.time() - started:.0f}s)",
                            flush=True,
                        )
            print(
                f"{crawl}: {hits} urls, {failed} part files failed (retried next run)",
                flush=True,
            )


def urlscan(pages: int) -> None:
    """Hosts of the pages urlscan.io scanned that loaded a file of the plugin's.

    ``filename:wp-job-openings`` matches a scan whose page requested anything under the plugin's
    directory, and each result names that page's host. The free search API answers 100 results a
    query and pages on with ``search_after``; anonymous callers get 100 queries an hour, so a 429
    ends the walk, reported, and a re-run resumes from the last page it wrote.
    """
    out, cursor = URLSCAN_HOSTS, URLSCAN_CURSOR
    after = cursor.read_text().strip() if cursor.exists() else ""
    fresh = not out.exists()
    with out.open("a", newline="") as fh:
        writer = csv.writer(fh)
        if fresh:
            writer.writerow(["host", "scanned"])
        for n in range(pages):
            query = "q=filename:wp-job-openings&size=100" + (
                f"&search_after={after}" if after else ""
            )
            try:
                data = json.loads(_get(f"https://urlscan.io/api/v1/search/?{query}"))
            except Exception as exc:  # noqa: BLE001 — a refusal ends the walk, reported
                print(f"  page {n}: {exc} — stopped; re-run resumes here", flush=True)
                return
            results = data.get("results") or []
            for result in results:
                page = result.get("page") or {}
                if page.get("domain"):
                    writer.writerow(
                        [page["domain"].lower(), result["task"].get("time")]
                    )
            fh.flush()
            if not results or not data.get("has_more"):
                print(f"  page {n}: {len(results)} results, no more", flush=True)
                return
            after = ",".join(str(value) for value in results[-1]["sort"])
            cursor.write_text(after)
            print(f"  page {n}: {len(results)} results", flush=True)
            time.sleep(2.5)


def captured_hosts() -> set[str]:
    """Every host Common Crawl captured a :data:`FINGERPRINT` URL on."""
    if not CAPTURES.exists():
        return set()
    with CAPTURES.open(newline="") as f:
        return {
            row["host"].strip().lower()
            for row in csv.DictReader(f)
            if row["host"] and FINGERPRINT.search(row["url"])
        }


def _rest(url: str) -> tuple[str, object] | None:
    """The host a REST request landed on and its parsed JSON, or None when it answers none."""
    try:
        response = http.fetch(
            "GET",
            url,
            headers={"User-Agent": "headstart/0.1", "Accept": "application/json"},
            timeout=30,
            attempts=2,
            impersonate=IMPERSONATE,
        )
        return (urlsplit(response.url or url).hostname or "").lower(), json.loads(
            response.content
        )
    except (http.RequestsError, ValueError):
        return None


def site_host(host: str) -> str:
    """The host this site's own REST API names: its postings' link host, else the ``home`` its
    REST index states (a site with nothing published links no posting, and ``dstc.sa`` and
    ``www.dstc.sa`` both answer), else the host a redirect lands on, else ``host`` itself when
    the route answers nothing readable."""
    answer = _rest(f"https://{host}/{_ROUTE}")
    if answer is None or not isinstance(answer[1], list):
        return host
    landed, rows = answer
    link = rows[0].get("link") if rows and isinstance(rows[0], dict) else None
    if isinstance(link, str) and urlsplit(link).hostname:
        return urlsplit(link).hostname.lower()
    index = _rest(f"https://{landed or host}/?rest_route=/")
    home = index[1].get("home") if index and isinstance(index[1], dict) else None
    named = urlsplit(home).hostname if isinstance(home, str) else None
    return (named or landed or host).lower()


def scanned_hosts() -> set[str]:
    """Every host urlscan.io scanned a page on that loaded a file of the plugin's."""
    if not URLSCAN_HOSTS.exists():
        return set()
    with URLSCAN_HOSTS.open(newline="") as f:
        return {row["host"].strip().lower() for row in csv.DictReader(f) if row["host"]}


def pool(extra: Path | None, source: str) -> None:
    """Append every candidate not yet pooled, under its site's host, tagged by every source that
    found it (`cc`, `urlscan`, and ``source`` for the ``--hosts`` file), '+'-joined."""
    held = set()
    if POOL.exists():
        with POOL.open(newline="") as f:
            held = {row["tenant"].lower() for row in csv.DictReader(f)}
    found: dict[str, list[str]] = {}
    for tag, hosts in (("cc", captured_hosts()), ("urlscan", scanned_hosts())):
        for host in hosts:
            found.setdefault(host, []).append(tag)
    if extra:
        for line in extra.read_text().split():
            found.setdefault(line.strip().lower(), []).append(source)
    todo = {host: "+".join(tags) for host, tags in found.items() if host not in held}
    print(f"{len(found)} candidate hosts, {len(todo)} not yet pooled", flush=True)
    fresh = not POOL.exists()
    POOL.parent.mkdir(parents=True, exist_ok=True)
    with POOL.open("a", newline="") as out, ThreadPoolExecutor(16) as workers:
        writer = csv.writer(out)
        if fresh:
            writer.writerow(["ats", "tenant", "url", "source"])
        futures = {workers.submit(site_host, host): host for host in todo}
        for future in as_completed(futures):
            host = futures[future]
            site = future.result()
            with _write:
                if site in held or NONPRODUCTION.search(site):
                    continue
                held.add(site)
                writer.writerow(
                    ["wp_job_openings", site, f"https://{site}/", todo[host]]
                )
                out.flush()
            print(f"  {host} -> {site}" if site != host else f"  {host}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    mine_parser = sub.add_parser("mine")
    mine_parser.add_argument("crawls", nargs="*")
    urlscan_parser = sub.add_parser("urlscan")
    urlscan_parser.add_argument("--pages", type=int, default=90)
    pool_parser = sub.add_parser("pool")
    pool_parser.add_argument("--hosts", type=Path)
    pool_parser.add_argument("--source", default="fingerprint")
    args = parser.parse_args()
    if args.command == "mine":
        mine(args.crawls)
    elif args.command == "urlscan":
        urlscan(args.pages)
    else:
        pool(args.hosts, args.source)


if __name__ == "__main__":
    main()
