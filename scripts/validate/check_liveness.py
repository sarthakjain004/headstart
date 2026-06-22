#!/usr/bin/env python3
"""Liveness checker for ATS tenant lists — loss-free three-state, multi-pass.

Reads a dir of {ats}.csv (ats,tenant,url,...) and classifies each board into one of three
states, so a transient blip never gets mistaken for a dead board:

  LIVE     -> 200 with a parseable job list      -> active/{ats}.csv  (ats,tenant,url,jobs)
  DEAD     -> definitive: 404/410, or DNS doesn't resolve (curl code 6)  -> active/.{ats}_dead
  UNKNOWN  -> couldn't tell: timeout, reset, 5xx, 429, parse-fail        -> re-probed next pass

Only DEAD is a final negative. Everything UNKNOWN is collected and re-probed in additional
passes, each one more patient (longer timeout, lower concurrency to shed self-induced
congestion), until nothing is left unknown — or, after the last pass, the few that still won't
resolve are written to active/unresolved.csv and reported, never silently dropped. So a live
board can't be lost to a transient failure: the verdict is always LIVE, DEAD, or explicitly
unresolved.

Resume: a tenant is "settled" once it's in active/{ats}.csv (live) or active/.{ats}_dead. A
re-run re-probes only the unsettled remainder.

Run:   python scripts/validate/check_liveness.py --dir data/ats-tenants-merged
       python scripts/validate/check_liveness.py --dir data/ats-tenants-merged zoho workday
       python scripts/validate/check_liveness.py --dir data/ats-tenants-merged --limit 20  # smoke
"""
import csv
import json
import re
import sys
import threading
import time
import urllib.parse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from itertools import zip_longest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
from headstart import http  # noqa: E402 - needs src on sys.path first

UA = "HeadStart-liveness/0.1 (careers-board liveness check)"
TIMEOUT = 12              # reassigned per pass by the runner
_RETRY = {429, 500, 502, 503, 504}
_DNS_ERR = 6             # curl CURLE_COULDNT_RESOLVE_HOST -> host doesn't exist -> DEAD
# each pass: (timeout_seconds, workers). Later passes are slower but more patient so a board
# that was merely congested gets a fair chance before we give up on it.
PASSES = [(12, 64), (25, 24), (45, 8), (70, 4)]

LIVE, DEAD, UNKNOWN = "live", "dead", "unknown"

_ZOHO_JOBS = re.compile(r'value="([^"]*)"\s+id="jobs"')
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_TOKEN = re.compile(r"token=([A-Za-z0-9_-]+)")
_WD_URL = re.compile(r"^https://([^.]+)\.(wd\d+)\.myworkdayjobs\.com/([^/?#]+)")


def _is_dns(exc):
    return getattr(exc, "code", None) == _DNS_ERR


def _get(url, headers=None):
    """GET, retrying transient failures. Returns (status, body) where status is the HTTP code,
    "dns" if the host can't resolve (definitive), or None for an exhausted transient failure.
    Reads the full body (Zoho parks its jobs <input> at the end of a ~1.7MB page)."""
    h = {"User-Agent": UA, **(headers or {})}
    for attempt in range(3):
        try:
            r = http.get(url, headers=h, timeout=TIMEOUT, verify=False)
        except http.RequestsError as e:
            if _is_dns(e):
                return "dns", b""
            if attempt == 2:
                return None, b""
            time.sleep(1.0 * (attempt + 1))
            continue
        if r.status_code in _RETRY and attempt < 2:
            time.sleep(1.0 * (attempt + 1))
            continue
        return r.status_code, r.content
    return None, b""


def _post(url, json_body, headers):
    """POST JSON. Returns (status, parsed_json|None); status is the code, "dns", or None."""
    for attempt in range(3):
        try:
            r = http.post(url, json=json_body, headers=headers, timeout=TIMEOUT, verify=False)
        except http.RequestsError as e:
            if _is_dns(e):
                return "dns", None
            if attempt == 2:
                return None, None
            time.sleep(1.0 * (attempt + 1))
            continue
        if r.status_code in _RETRY and attempt < 2:
            time.sleep(1.0 * (attempt + 1))
            continue
        if r.status_code == 200:
            try:
                return 200, r.json()
            except Exception:
                return None, None  # 200 but unparseable -> treat as transient
        return r.status_code, None
    return None, None


def _verdict(status, jobs):
    """Map an HTTP status + parsed count into (verdict, jobs)."""
    if status == "dns" or status in (404, 410):
        return DEAD, None
    if status == 200:
        return (LIVE, jobs) if jobs is not None else (UNKNOWN, None)
    return UNKNOWN, None  # timeout / 5xx / 403 / 429-exhausted / other -> retry


def _len_of(body, *keys):
    """JSON list length: top-level list, or the first of `keys`. None if body won't parse."""
    try:
        data = json.loads(body)
    except Exception:
        return None
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for k in keys:
            if isinstance(data.get(k), list):
                return len(data[k])
        return 0
    return None


def _classify(url, count):
    """GET `url`, run `count(body) -> int|None`, return (verdict, jobs)."""
    status, body = _get(url)
    return _verdict(status, count(body) if status == 200 else None)


# --- per-ATS probes: return (verdict, jobs) ---

def p_greenhouse(t, u):
    return _classify(f"https://boards-api.greenhouse.io/v1/boards/{t}/jobs",
                     lambda b: _len_of(b, "jobs"))


def p_lever(t, u):
    host = "api.eu" if "jobs.eu.lever.co" in u else "api"
    return _classify(f"https://{host}.lever.co/v0/postings/{t}?mode=json", _len_of)


def p_ashby(t, u):
    return _classify(f"https://api.ashbyhq.com/posting-api/job-board/{t}",
                     lambda b: _len_of(b, "jobs"))


def p_recruitee(t, u):
    return _classify(f"https://{t}.recruitee.com/api/offers/", lambda b: _len_of(b, "offers"))


def p_workable(t, u):
    return _classify(f"https://apply.workable.com/api/v1/widget/accounts/{t}",
                     lambda b: _len_of(b, "jobs"))


def _zoho_count(body):
    import html as _html
    m = _ZOHO_JOBS.search(body.decode("utf-8", "replace"))
    if not m:
        return None  # 200 but no jobs <input> -> odd/transient, re-probe
    try:
        return len(json.loads(_html.unescape(m.group(1))))
    except Exception:
        return None


def p_zoho(t, u):
    return _classify(f"{u.rstrip('/')}/jobs/Careers", _zoho_count)


def p_keka(t, u):
    base = f"https://{t}.keka.com/careers/api"
    status, body = _get(f"{base}/organization/default/careerportalinfo")
    if status == "dns" or status in (404, 410):
        return DEAD, None
    if status != 200:
        return UNKNOWN, None
    m = _UUID.search(body.decode("utf-8", "replace"))
    if not m:
        return UNKNOWN, None
    return _classify(f"{base}/embedjobs/default/active/{m.group(0)}", _len_of)


def p_workday(t, u):
    m = _WD_URL.match(u.rstrip("/"))
    if not m:
        return DEAD, None  # not a Workday URL -> can't be a board
    co, inst, site = m.groups()
    api = f"https://{co}.{inst}.myworkdayjobs.com/wday/cxs/{co}/{site}/jobs"
    status, data = _post(api, {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
                         {"User-Agent": UA, "Content-Type": "application/json",
                          "Accept": "application/json"})
    return _verdict(status, int(data.get("total", 0)) if status == 200 and data else None)


def p_ripplehire(t, u):
    headers = {"User-Agent": UA}
    try:
        r = http.get(f"https://{t}.ripplehire.com/candidate/careers",
                     headers=headers, timeout=TIMEOUT, verify=False)
    except http.RequestsError as e:
        return (DEAD, None) if _is_dns(e) else (UNKNOWN, None)
    m = _TOKEN.search(r.url)
    if not m:
        return UNKNOWN, None
    params = json.dumps({"page": 0, "search": "*:*", "token": m.group(1),
                         "source": "CAREERSITE", "pagesize": 1})
    data = urllib.parse.urlencode({"careerSiteUrlParams": params, "lang": "en"})
    try:
        r2 = http.post(f"https://{t}.ripplehire.com/candidate/candidatejobsearch", data=data,
                       headers={"User-Agent": UA, "Accept": "application/json",
                                "X-Requested-With": "XMLHttpRequest",
                                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
                       timeout=TIMEOUT, verify=False)
    except http.RequestsError:
        return UNKNOWN, None
    if r2.status_code != 200:
        return UNKNOWN, None
    try:
        return LIVE, int(r2.json().get("totalJobCount", 0))
    except Exception:
        return UNKNOWN, None


def p_darwinbox(t, u):
    host_tld = "com" if ".darwinbox.com" in u else "in"
    dns_fails = 0
    for tld in (host_tld, *[x for x in ("in", "com") if x != host_tld]):
        api = f"https://{t}.darwinbox.{tld}/ms/candidateapi/job/alljobs?companyId=main"
        try:
            r = http.post(api, json={"companyId": "main", "page": 1,
                                     "sort_option": "new", "limit": 100},
                          headers={"Accept": "application/json", "User-Agent": UA},
                          timeout=TIMEOUT, verify=False)
        except http.RequestsError as e:
            if _is_dns(e):
                dns_fails += 1
            continue
        if r.status_code == 200:
            try:
                return LIVE, len(r.json().get("data") or [])
            except Exception:
                return UNKNOWN, None
        # 404/other on this tld -> try the other tld
    return (DEAD, None) if dns_fails == 2 else (UNKNOWN, None)


PROBES = {
    "greenhouse": p_greenhouse, "lever": p_lever, "ashby": p_ashby, "recruitee": p_recruitee,
    "workable": p_workable, "zoho": p_zoho, "workday": p_workday, "keka": p_keka,
    "ripplehire": p_ripplehire, "darwinbox": p_darwinbox,
}


def main():
    args = sys.argv[1:]
    indir = ROOT / "data" / "ats-tenants-merged"
    limit = 0
    rest = []
    i = 0
    while i < len(args):
        if args[i] == "--dir":
            indir = Path(args[i + 1]); i += 2
        elif args[i] == "--limit":
            limit = int(args[i + 1]); i += 2
        else:
            rest.append(args[i]); i += 1
    filt = set(rest)
    outdir = indir / "active"
    outdir.mkdir(parents=True, exist_ok=True)

    # Load each ATS, skip already-settled tenants (live in active.csv OR confirmed dead), and
    # open append handles. Build one interleaved work list across all ATSes.
    buckets = {}                    # ats -> [(ats, tenant, url), ...]
    files = {}                      # ats -> (active_writer, active_fh, dead_fh)
    for csvf in sorted(indir.glob("*.csv")):
        ats = csvf.stem
        if ats not in PROBES or (filt and ats not in filt):
            continue
        rows = list(csv.DictReader(csvf.open(encoding="utf-8")))
        active_csv = outdir / f"{ats}.csv"
        dead_file = outdir / f".{ats}_dead"
        live_set = ({r["tenant"] for r in csv.DictReader(active_csv.open(encoding="utf-8"))}
                    if active_csv.exists() else set())
        dead_set = (set(dead_file.read_text(encoding="utf-8").split("\n")) - {""}
                    if dead_file.exists() else set())
        settled = live_set | dead_set
        todo = [r for r in rows if r["tenant"] not in settled]
        if limit:
            todo = todo[:limit]
        if not todo:
            continue
        fresh = not active_csv.exists()
        afh = active_csv.open("a", newline="", encoding="utf-8")
        aw = csv.writer(afh)
        if fresh:
            aw.writerow(["ats", "tenant", "url", "jobs"]); afh.flush()
        files[ats] = (aw, afh, dead_file.open("a", encoding="utf-8"))
        buckets[ats] = [(ats, r["tenant"], r["url"]) for r in todo]

    items = [x for row in zip_longest(*buckets.values()) for x in row if x is not None]
    live_n = defaultdict(int)
    dead_n = defaultdict(int)
    lock = threading.Lock()

    def run_pass(work, timeout, workers):
        global TIMEOUT
        TIMEOUT = timeout
        unknowns = []
        ulock = threading.Lock()

        def do(item):
            ats, tenant, url = item
            try:
                verdict, jobs = PROBES[ats](tenant, url)
            except Exception:
                verdict, jobs = UNKNOWN, None
            if verdict == UNKNOWN:
                with ulock:
                    unknowns.append(item)
                return
            with lock:
                aw, afh, dfh = files[ats]
                if verdict == LIVE:
                    aw.writerow([ats, tenant, url, jobs]); afh.flush(); live_n[ats] += 1
                else:
                    dfh.write(tenant + "\n"); dfh.flush(); dead_n[ats] += 1

        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(do, work))
        return unknowns

    for n, (timeout, workers) in enumerate(PASSES, 1):
        if not items:
            break
        print(f"pass {n}: {len(items)} to probe (timeout={timeout}s, workers={workers})", flush=True)
        items = run_pass(items, timeout, workers)
        live = sum(live_n.values()); dead = sum(dead_n.values())
        print(f"  -> live {live}, dead {dead}, still unknown {len(items)}", flush=True)

    if items:  # never silently dropped — surfaced for a human to decide
        with (outdir / "unresolved.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["ats", "tenant", "url"])
            w.writerows(items)
        print(f"UNRESOLVED after {len(PASSES)} passes: {len(items)} -> active/unresolved.csv", flush=True)

    for _, afh, dfh in files.values():
        afh.close(); dfh.close()
    for ats in sorted(files):
        print(f"{ats}: live {live_n[ats]}, dead {dead_n[ats]}", flush=True)


if __name__ == "__main__":
    main()
