#!/usr/bin/env python3
"""Job-seeker-feed miner — harvest Greenhouse Slugs from MyGreenhouse's own browse feed.

The other two Greenhouse miners read the *archived web*: mine_greenhouse.py page-mines the
board hosts, mine_greenhouse_embeds.py mines the `?for=` embed fingerprint out of Wayback. Both
inherit that surface's blind spot — a Company only appears if somebody, somewhere, linked or
archived its Board. A single-site veterinary clinic with no careers page and no inbound links is
invisible to both, however live its Board is.

`my.greenhouse.io/jobs/search` is Greenhouse's own job-seeker product, and its unfiltered
"Browse jobs" feed is a publish-time-ordered stream of every job on the network. Every card
carries the Slug, so walking the feed enumerates Boards by *what is publishing right now* rather
than by what the web happens to remember. That is a different population, which is why it finds
anything at all against a ledger already mined from Wayback, Common Crawl and Hacker News.

Measured 2026-08-25 (against a 19,493-row ledger, 9,152 live):

  * 972 jobs read -> 466 distinct Slugs -> 33 not already known -> **33 confirmed live** (100%)
    via `boards-api.greenhouse.io/v1/boards/{slug}/jobs`, carrying 2,538 open jobs between them.
  * The 100% is structural, not luck: a Slug reaches this feed only because that Board has a job
    published right now, so there is nothing for the probe to disagree with. Wayback embeds ran
    ~25% and Hacker News ~42% on the same ledger.
  * Yield held flat at ~30 new Slugs per 1,000 jobs across 2,184 jobs / ~21 hours of publishing,
    with no sign of decay - roughly 3% of everything Greenhouse publishes is on a Board this
    project does not know about.

Read the Slug from `viewJobPath`, never from `publicUrl`. `publicUrl` is whatever host the
Company serves its Board from, and 38% of jobs (373/972 measured) are not on
job-boards.greenhouse.io at all -- custom domains (careers.nebius.com, bitwarden.com/careers/)
or the EU pod. `viewJobPath` is always `/jobs/{slug}/{id}` and was present on 972 of 972 jobs, so
it closes the custom-domain gap for free and hands over the non-derivable parent-entity class
too (anymindgroup.com -> `adasiaholdings`).

New Slugs are folded into data/wayback-ats/greenhouse.csv (candidate-grade, same as the sibling
miners). Validate with:

    python scripts/validate/check_liveness.py --dir data/wayback-ats greenhouse

Needs an authenticated job-seeker session -- the feed 302s to the sign-in page otherwise, and a
guest session is not enough. Put the `_session_id` cookie in MYGREENHOUSE_SESSION; it is issued
with a 14-day expiry, so expect to refresh it about fortnightly. The Inertia asset version is
read back from the live sign-in page on every run rather than pinned, because it changes on every
front-end deploy and a stale one earns a 409.

The feed rate-limits with an opaque 403 -- no Retry-After, no X-RateLimit-* -- but it is
transient, not a ban (a plain request ~4 minutes later returned 200). Measured 2026-08-25 by
walking a ladder paced on *effective* rate: 0.94 and 0.91 req/s both 403'd (at request 82 and 71
respectively), while 0.70 req/s survived 170 straight requests and 0.48 survived 150. So the
ceiling sits between 0.70 and 0.91; --rate defaults below it, at 0.6, because the longest clean
run at 0.70 was only 170 requests and a backfill is much longer. Backoff resumes from the last
cursor rather than restarting the walk, and the cursor is checkpointed to disk, so an interrupted
run picks up where it stopped.

Run:  MYGREENHOUSE_SESSION=... python scripts/discover/mine_greenhouse_jobseeker.py --pages 500
      MYGREENHOUSE_SESSION=... python scripts/discover/mine_greenhouse_jobseeker.py --restart
"""

import argparse
import csv
import gzip
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
WB = ROOT / "data" / "wayback-ats"
LEDGER = ROOT / "data" / "validate" / "liveness" / "greenhouse.csv"
STATE = WB / ".jobseeker_cursor"

BASE = "https://my.greenhouse.io"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
BACKOFF = 90

# `/jobs/{slug}/{id}`. The Slug charset is wider than the ledger's current contents -- one live
# Board measured as `1pyra)mid_health&care` (372 jobs) -- so this deliberately does not impose
# the `[a-z0-9_-]` shape the sibling miners use, and the probe URL is percent-encoded instead.
JOB_PATH = re.compile(r"^/jobs/(.+)/\d+$")


def inertia_version():
    """Read the current asset version off the sign-in page (no session needed)."""
    req = urllib.request.Request(f"{BASE}/users/sign_in")
    req.add_header("User-Agent", UA)
    req.add_header("Accept", "text/html,application/xhtml+xml")
    with urllib.request.urlopen(req, timeout=60) as f:
        body = f.read().decode("utf-8", "replace")
    m = re.search(r'data-page="(.*?)"\s*>', body, re.DOTALL)
    if not m:
        sys.exit("could not read the Inertia version off the sign-in page")
    return json.loads(html.unescape(m.group(1)))["version"]


def fetch(session, version, cursor):
    """One page of the browse feed. Returns (status, jobPosts, more)."""
    qs = f"?{urllib.parse.urlencode({'search_after': cursor})}" if cursor else ""
    req = urllib.request.Request(f"{BASE}/jobs/search{qs}")
    for k, v in (
        ("User-Agent", UA),
        ("Accept", "text/html, application/xhtml+xml"),
        ("Accept-Encoding", "gzip"),
        ("X-Inertia", "true"),
        ("X-Inertia-Version", version),
        ("X-Job-Search", "true"),
        ("X-Requested-With", "XMLHttpRequest"),
        ("X-Inertia-Partial-Component", "jobs"),
        ("X-Inertia-Partial-Data", "jobPosts,moreResultsAvailable"),
        ("Cookie", f"_session_id={session}"),
    ):
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=60) as f:
            raw = f.read()
            if f.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            props = json.loads(raw).get("props", {})
            return (
                f.status,
                props.get("jobPosts", []),
                props.get("moreResultsAvailable"),
            )
    except urllib.error.HTTPError as e:
        if e.code in (301, 302):
            sys.exit(
                "redirected to sign-in: MYGREENHOUSE_SESSION is missing or expired"
            )
        return e.code, [], None


def known_slugs():
    with LEDGER.open() as f:
        return {r["tenant"].lower() for r in csv.DictReader(f)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=500, help="max pages (12 jobs each)")
    ap.add_argument("--rate", type=float, default=0.6, help="effective requests/second")
    ap.add_argument("--restart", action="store_true", help="ignore the saved cursor")
    args = ap.parse_args()

    session = os.environ.get("MYGREENHOUSE_SESSION")
    if not session:
        sys.exit(
            "set MYGREENHOUSE_SESSION to the _session_id cookie of a signed-in job seeker"
        )

    version = inertia_version()
    known = known_slugs()
    print(f"inertia version {version} | ledger holds {len(known):,} Slugs", flush=True)

    cursor = None
    if not args.restart and STATE.exists():
        cursor = STATE.read_text().strip() or None
        if cursor:
            print(f"resuming from cursor {cursor}", flush=True)

    # data/wayback-ats/ is gitignored, so on a fresh clone the candidate file does not exist yet.
    out = WB / "greenhouse.csv"
    seen = set()
    if out.exists():
        with out.open() as f:
            for r in csv.DictReader(f):
                seen.add(r["tenant"].lower())
    else:
        WB.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="") as f:
            csv.writer(f).writerow(["ats", "tenant", "url"])

    fh = out.open("a", newline="")
    w = csv.writer(fh)

    period = 1.0 / args.rate
    started = time.time()
    jobs = found = stalls = 0
    for page in range(1, args.pages + 1):
        due = started + (page - 1) * period
        if time.time() < due:
            time.sleep(due - time.time())

        status, posts, more = fetch(session, version, cursor)
        if status == 403:
            stalls += 1
            print(
                f"  403 on page {page} - backing off {BACKOFF}s, resuming from {cursor}",
                flush=True,
            )
            time.sleep(BACKOFF)
            started = time.time() - (page - 1) * period  # re-anchor the pacing clock
            continue
        if status != 200:
            print(f"stopping: page {page} returned {status}", flush=True)
            break
        if not posts:
            print(f"stopping: page {page} was empty (more={more})", flush=True)
            break

        jobs += len(posts)
        for job in posts:
            m = JOB_PATH.match(job.get("viewJobPath") or "")
            if not m:
                continue
            slug = m.group(1).lower()
            if slug in known or slug in seen:
                continue
            seen.add(slug)
            found += 1
            w.writerow(
                [
                    "greenhouse",
                    slug,
                    f"https://job-boards.greenhouse.io/{urllib.parse.quote(slug, safe='')}",
                ]
            )
            fh.flush()
            print(f"  NEW {slug}  (from {job['companyName'][:40]})", flush=True)

        cursor = posts[-1]["firstPublished"]
        STATE.write_text(cursor)
        if page % 25 == 0:
            print(
                f"page {page} | {jobs:,} jobs | {found} new | cursor {cursor}",
                flush=True,
            )
        if not more:
            print(f"stopping: feed reported no more results at page {page}", flush=True)
            break

    fh.close()
    print(
        f"done: {jobs:,} jobs read, {found} new Slugs, {stalls} rate-limit stalls, "
        f"oldest {cursor}\nvalidate: python scripts/validate/check_liveness.py "
        f"--dir data/wayback-ats greenhouse",
        flush=True,
    )


if __name__ == "__main__":
    main()
