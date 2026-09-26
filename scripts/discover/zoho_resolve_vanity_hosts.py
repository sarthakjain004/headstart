#!/usr/bin/env python3
"""Resolve Zoho Recruit vanity career hosts to the canonical `{label}.zohorecruit.{dc}` Board.

A customer's vanity host (`careers.acme.com`, CNAMEd to `recruit.cs.zohohost.{dc}`) serves the
*same Board* as its `{label}.zohorecruit.{dc}` host: both pages carry the same `org_info.id`
(measured 2026-09-26 on alconcysec, empowersstaffing, myoperator, 2coms, yellow). Landing the
vanity host beside the canonical one would serve every posting twice, so this names the
canonical host, which is what gets landed.

Per host, three reads, cheapest first:
  1. `GET /jobs/Careers` (the scraper's own page): is it a live Zoho Board at all (`#jobs`,
     no `cl-error-block`), its job count, its `org_info.id`, and its data centre, read from the
     page's `recruit_home` (`www.zoho.in/recruit` -> `zohorecruit.in`; `zohocloud.ca` -> `.ca`).
  2. The public listing `GET /recruit/v2/public/Job_Openings?pagename=Careers`: each record's
     `$url` names the host Zoho treats as primary, a `*.zohorecruit.*` one on about a third.
  3. Otherwise, label guesses from the vanity host's registrable label (`careers.myoperator.com`
     -> `myoperator`, `myoperatorcareers`, ...) on that one data centre, accepted only when the
     guess's `org_info.id` equals the vanity page's. A guess is never accepted on name alone:
     `myoperator-careers.zohorecruit.com` is a different org from `careers.myoperator.com`.

A host none of these resolves is reported `vanity-only`: it is a real Board, but it is not
landed, because nothing here can tell whether the ledger already holds it under another label.

Output: TSV `vanity  verdict  jobs  dc  org_id  canonical  method`, streamed as hosts finish.
Politeness: 6 workers; each host costs 1-6 requests.

Run:  python -u scripts/discover/zoho_resolve_vanity_hosts.py HOSTS_FILE > OUT.tsv
"""

import html
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from curl_cffi import requests

JOBS_INPUT = re.compile(r'value="([^"]*)"\s+id="jobs"')
ORG_ID = re.compile(r'"org_info":\{[^}]*?"id":"(\d+)"')
RECRUIT_HOME = re.compile(
    r'"recruit_home":"https://www\.(zoho(?:cloud)?\.[a-z.]+)/recruit'
)
ZOHORECRUIT_HOST = re.compile(r"^[a-z0-9-]+\.zohorecruit\.[a-z.]+$")
#: Second-level labels that belong to a public suffix, so the registrable label is one further left.
SUFFIX_SECOND_LEVEL = {"com", "co", "org", "net", "gov", "edu", "ac", "or", "ne", "go"}
WORKERS = 6


def get(url: str) -> str | None:
    try:
        r = requests.get(url, timeout=20, impersonate="chrome", allow_redirects=True)
    except Exception:  # noqa: BLE001 — a host that will not answer is simply unresolved
        return None
    return r.text if r.status_code == 200 else None


def read_board(host: str) -> tuple[int, str, str] | None:
    """(jobs, org_id, data-centre domain) of a live Zoho Board at `host`, else None."""
    page = get(f"https://{host}/jobs/Careers")
    if not page or "cl-error-block" in page:
        return None
    jobs = JOBS_INPUT.search(page)
    if not jobs:
        return None
    text = html.unescape(page)
    org = ORG_ID.search(text)
    home = RECRUIT_HOME.search(text)
    tld = home.group(1).split(".", 1)[1] if home else ""
    try:
        count = len(json.loads(html.unescape(jobs.group(1))))
    except ValueError:
        count = -1
    return count, org.group(1) if org else "", f"zohorecruit.{tld}" if tld else ""


def api_primary_host(host: str) -> str:
    body = get(
        f"https://{host}/recruit/v2/public/Job_Openings?pagename=Careers&source=CareerSite"
    )
    try:
        records = json.loads(body or "{}").get("data") or []
    except (ValueError, AttributeError):
        return ""
    for rec in records:
        url = rec.get("$url") or ""
        found = url.split("/")[2].lower() if url.count("/") >= 2 else ""
        if ZOHORECRUIT_HOST.match(found):
            return found
    return ""


def label_guesses(host: str) -> list[str]:
    labels = host.lower().split(".")
    core = labels[:-1]
    if len(core) >= 2 and core[-1] in SUFFIX_SECOND_LEVEL:
        core = core[:-1]
    reg = core[-1] if core else ""
    if not reg:
        return []
    guesses = [
        reg,
        reg.replace("-", ""),
        f"{reg}careers",
        f"{reg}-careers",
        f"careers{reg}",
    ]
    return list(dict.fromkeys(g for g in guesses if g))


def resolve(host: str) -> list:
    board = read_board(host)
    if board is None:
        return [host, "not-a-board", "", "", "", "", ""]
    jobs, org, dc = board
    if ZOHORECRUIT_HOST.match(host):
        return [host, "canonical", jobs, dc, org, host, "self"]
    primary = api_primary_host(host)
    if primary:
        return [host, "resolved", jobs, dc, org, primary, "api"]
    if org and dc:
        for label in label_guesses(host):
            guess = f"{label}.{dc}"
            other = read_board(guess)
            if other and other[1] == org:
                return [host, "resolved", jobs, dc, org, guess, "guess"]
    return [host, "vanity-only", jobs, dc, org, "", ""]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    with open(argv[1]) as f:
        hosts = list(dict.fromkeys(h.strip().lower() for h in f if h.strip()))
    with ThreadPoolExecutor(WORKERS) as pool:
        for fut in as_completed([pool.submit(resolve, h) for h in hosts]):
            print(*fut.result(), sep="\t", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
