#!/usr/bin/env python3
"""Liveness checker for ATS tenant lists — loss-free three-state, multi-pass, ledger-backed.

Reads the candidate pool (a dir of {ats}.csv) and classifies each board into one of three states,
so a transient blip never gets mistaken for a dead board:

  LIVE     -> 200 with a parseable job list
  DEAD     -> definitive: 404/410, or DNS doesn't resolve (curl code 6)
  UNKNOWN  -> couldn't tell: timeout, reset, 5xx, 429, parse-fail  -> re-probed next pass

Verdicts land in the liveness ledger (ADR-0012), one CSV per ATS at
data/validate/liveness/{ats}.csv (ats,tenant,url,status,jobs,checked_at) — the single source of
truth. The Active list is just its status==live rows (config.load_active_companies); dead is
status==dead; still-unknown is status==unknown.

Incremental + fresh: a board is re-probed only when it's new, unknown, or past its per-status TTL
(live 7d / dead 90d, env-tunable via HEADSTART_LIVE_TTL_DAYS / HEADSTART_DEAD_TTL_DAYS or the
flags below) — so adding companies to the pool never re-checks the whole thing, yet settled boards
still refresh on a cadence (catching a live-but-empty board that opens roles, or a live board that
has since died). UNKNOWN is re-probed across increasingly patient passes; whatever is still unknown
after the last pass is recorded as status=unknown (re-probed next run), never silently dropped. The
ledger is rewritten after each pass, so a crash keeps the verdicts already settled.

Run:   python scripts/validate/check_liveness.py                       # all ATSes, respect TTLs
       python scripts/validate/check_liveness.py zoho workday          # only these ATSes
       python scripts/validate/check_liveness.py --force               # re-probe everything
       python scripts/validate/check_liveness.py --live-ttl 3 --limit 20  # smoke
"""

import csv
import json
import os
import re
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from itertools import zip_longest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
from headstart import http, liveness  # noqa: E402 - needs src on sys.path first

UA = "HeadStart-liveness/0.1 (careers-board liveness check)"
TIMEOUT = 12  # reassigned per pass by the runner
_DNS_ERR = 6  # curl CURLE_COULDNT_RESOLVE_HOST -> host doesn't exist -> DEAD
# One attempt per probe: the multi-pass retry below IS the retry mechanism, so http.fetch's own
# 3-attempt backoff would just tie a worker up in sleep() instead of probing the next board. A
# transient failure becomes UNKNOWN here and gets a more patient re-probe on the next pass.
_ATTEMPTS = 1
# each pass: (timeout_seconds, workers). Later passes are slower but more patient so a board
# that was merely congested gets a fair chance before we give up on it. Pass-1 workers scale
# with the machine: the work is I/O-bound and curl_cffi releases the GIL on each request, so we
# run far more threads than cores; interleaving across ATSes keeps per-host concurrency gentle.
_CORES = os.cpu_count() or 8
# Pass-1 concurrency. The work is network-bound and curl_cffi drops the GIL on every request,
# so hundreds of threads run truly concurrently across all cores; processes wouldn't help (the
# only GIL-bound bit is the small parse) and can't share the active/ write handles. Default to
# cores*24; override with LIVENESS_WORKERS=N to push it (the ceiling becomes host rate-limiting,
# not the machine — over-cranking just turns into UNKNOWNs that the patient retry passes mop up).
_W = int(os.environ.get("LIVENESS_WORKERS") or _CORES * 24)
PASSES = [(12, _W), (25, max(_W // 4, 16)), (45, max(_W // 12, 8)), (70, 4)]


LIVE, DEAD, UNKNOWN = "live", "dead", "unknown"

_ZOHO_JOBS = re.compile(r'value="([^"]*)"\s+id="jobs"')
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_TOKEN = re.compile(r"token=([A-Za-z0-9_-]+)")
_WD_URL = re.compile(r"^https://([^.]+)\.(wd\d+)\.myworkdayjobs\.com/([^/?#]+)")


def _is_dns(exc):
    return getattr(exc, "code", None) == _DNS_ERR


def _get(url, headers=None):
    """GET via the reliable-fetch seam. Returns (status, body): the HTTP code, "dns" if the host
    can't resolve (definitive), or None for an exhausted transient failure. Reads the full body
    (Zoho parks its jobs <input> at the end of a ~1.7MB page)."""
    h = {"User-Agent": UA, **(headers or {})}
    try:
        r = http.fetch(
            "GET", url, headers=h, timeout=TIMEOUT, verify=False, attempts=_ATTEMPTS
        )
    except http.RequestsError as e:
        return ("dns", b"") if _is_dns(e) else (None, b"")
    return r.status_code, r.content


def _post(url, json_body, headers):
    """POST JSON via the reliable-fetch seam. Returns (status, parsed_json|None); status is the
    code, "dns", or None."""
    try:
        r = http.fetch(
            "POST",
            url,
            json=json_body,
            headers=headers,
            timeout=TIMEOUT,
            verify=False,
            attempts=_ATTEMPTS,
        )
    except http.RequestsError as e:
        return ("dns", None) if _is_dns(e) else (None, None)
    if r.status_code == 200:
        try:
            return 200, r.json()
        except Exception:
            return None, None  # 200 but unparseable -> treat as transient
    return r.status_code, None


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
    return _classify(
        f"https://boards-api.greenhouse.io/v1/boards/{t}/jobs",
        lambda b: _len_of(b, "jobs"),
    )


def p_lever(t, u):
    host = "api.eu" if "jobs.eu.lever.co" in u else "api"
    return _classify(f"https://{host}.lever.co/v0/postings/{t}?mode=json", _len_of)


def p_ashby(t, u):
    return _classify(
        f"https://api.ashbyhq.com/posting-api/job-board/{t}",
        lambda b: _len_of(b, "jobs"),
    )


def p_recruitee(t, u):
    return _classify(
        f"https://{t}.recruitee.com/api/offers/", lambda b: _len_of(b, "offers")
    )


def p_workable(t, u):
    return _classify(
        f"https://apply.workable.com/api/v1/widget/accounts/{t}",
        lambda b: _len_of(b, "jobs"),
    )


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
    status, data = _post(
        api,
        {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
        {
            "User-Agent": UA,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    return _verdict(
        status, int(data.get("total", 0)) if status == 200 and data else None
    )


def p_ripplehire(t, u):
    headers = {"User-Agent": UA}
    try:
        r = http.fetch(
            "GET",
            f"https://{t}.ripplehire.com/candidate/careers",
            headers=headers,
            timeout=TIMEOUT,
            verify=False,
            attempts=_ATTEMPTS,
        )
    except http.RequestsError as e:
        return (DEAD, None) if _is_dns(e) else (UNKNOWN, None)
    m = _TOKEN.search(r.url)
    if not m:
        return UNKNOWN, None
    params = json.dumps(
        {
            "page": 0,
            "search": "*:*",
            "token": m.group(1),
            "source": "CAREERSITE",
            "pagesize": 1,
        }
    )
    data = urllib.parse.urlencode({"careerSiteUrlParams": params, "lang": "en"})
    try:
        r2 = http.fetch(
            "POST",
            f"https://{t}.ripplehire.com/candidate/candidatejobsearch",
            data=data,
            headers={
                "User-Agent": UA,
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            },
            timeout=TIMEOUT,
            verify=False,
            attempts=_ATTEMPTS,
        )
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
            r = http.fetch(
                "POST",
                api,
                json={
                    "companyId": "main",
                    "page": 1,
                    "sort_option": "new",
                    "limit": 100,
                },
                headers={"Accept": "application/json", "User-Agent": UA},
                timeout=TIMEOUT,
                verify=False,
                attempts=_ATTEMPTS,
            )
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


def p_smartrecruiters(t, u):
    def count(b):
        try:
            d = json.loads(b)
        except Exception:
            return None
        tf = d.get("totalFound")
        return tf if isinstance(tf, int) else len(d.get("content") or [])

    return _classify(
        f"https://api.smartrecruiters.com/v1/companies/{t}/postings?limit=10", count
    )


def p_teamtailor(t, u):
    return _classify(
        f"https://{t}.teamtailor.com/jobs.json", lambda b: _len_of(b, "items")
    )


def p_rippling(t, u):
    return _classify(
        f"https://api.rippling.com/platform/api/ats/v1/board/{t}/jobs",
        lambda b: _len_of(b, "items", "jobs"),
    )


def p_trakstar(t, u):
    status, body = _get(f"https://{t}.hire.trakstar.com/")
    if status == "dns" or status in (404, 410):
        return DEAD, None
    if status != 200:
        return UNKNOWN, None
    n = len(body.decode("utf-8", "replace").split("js-careers-page-job-list-item")) - 1
    return LIVE, max(n, 0)


def p_personio(t, u):
    host = (u or "").split("://", 1)[-1].rstrip("/") or f"{t}.jobs.personio.de"
    status, body = _get(f"https://{host}/xml")
    if status == "dns" or status in (404, 410):
        return DEAD, None
    if status != 200:
        return UNKNOWN, None
    return LIVE, body.decode("utf-8", "replace").count("<position>")


def p_join(t, u):
    status, body = _get(f"https://join.com/companies/{t}")
    if status == "dns" or status in (404, 410):
        return DEAD, None
    if status != 200:
        return UNKNOWN, None
    m = re.search(rb'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', body, re.S)
    if not m:
        return UNKNOWN, None
    try:
        state = (json.loads(m.group(1)).get("props") or {}).get("pageProps") or {}
        cid = ((state.get("initialState") or {}).get("company") or {}).get("id")
    except Exception:
        return UNKNOWN, None
    if not cid:
        return UNKNOWN, None
    return _classify(
        f"https://join.com/api/public/companies/{cid}/jobs?locale=en&page=1&pageSize=1",
        lambda b: (lambda d: (d.get("pagination") or {}).get("rowCount"))(
            json.loads(b)
        ),
    )


PROBES = {
    "greenhouse": p_greenhouse,
    "lever": p_lever,
    "ashby": p_ashby,
    "recruitee": p_recruitee,
    "workable": p_workable,
    "zoho": p_zoho,
    "workday": p_workday,
    "keka": p_keka,
    "ripplehire": p_ripplehire,
    "darwinbox": p_darwinbox,
    "smartrecruiters": p_smartrecruiters,
    "teamtailor": p_teamtailor,
    "rippling": p_rippling,
    "trakstar": p_trakstar,
    "personio": p_personio,
    "join": p_join,
}


def _parse_args(args):
    indir = ROOT / "data" / "ats-tenants-merged"
    ledger_dir = liveness.dir_for(ROOT)
    limit = 0
    live_ttl, dead_ttl = liveness.LIVE_TTL_DAYS, liveness.DEAD_TTL_DAYS
    force = False
    rest = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--dir":
            indir = Path(args[i + 1])
            i += 2
        elif a == "--ledger-dir":
            ledger_dir = Path(args[i + 1])
            i += 2
        elif a == "--limit":
            limit = int(args[i + 1])
            i += 2
        elif a == "--live-ttl":
            live_ttl = int(args[i + 1])
            i += 2
        elif a == "--dead-ttl":
            dead_ttl = int(args[i + 1])
            i += 2
        elif a == "--force":
            force = True
            i += 1
        else:
            rest.append(a)
            i += 1
    return indir, ledger_dir, limit, live_ttl, dead_ttl, force, set(rest)


def main():
    indir, ledger_dir, limit, live_ttl, dead_ttl, force, filt = _parse_args(
        sys.argv[1:]
    )
    ledger_dir.mkdir(parents=True, exist_ok=True)
    today = date.today()
    today_iso = today.isoformat()

    # Per ATS: carry the whole ledger forward (verdicts[ats]: tenant -> Verdict), and re-probe only
    # the pool rows that are new, unknown, or past their TTL (ADR-0012). The rest stay untouched.
    verdicts: dict[str, dict[str, liveness.Verdict]] = {}
    buckets = {}  # ats -> [(ats, tenant, url), ...] to probe this run
    for csvf in sorted(indir.glob("*.csv")):
        ats = csvf.stem
        if ats not in PROBES or (filt and ats not in filt):
            continue
        ledger = liveness.load(ledger_dir / f"{ats}.csv")
        verdicts[ats] = ledger
        rows = list(csv.DictReader(csvf.open(encoding="utf-8")))
        todo = [
            r
            for r in rows
            if force
            or liveness.needs_probe(
                ledger.get(r["tenant"]), today, live_ttl=live_ttl, dead_ttl=dead_ttl
            )
        ]
        if limit:
            todo = todo[:limit]
        if todo:
            buckets[ats] = [(ats, r["tenant"], r["url"]) for r in todo]

    items = [x for row in zip_longest(*buckets.values()) for x in row if x is not None]
    print(
        f"to probe: {len(items)} of {sum(len(v) for v in verdicts.values())} known "
        f"(live_ttl={live_ttl}d, dead_ttl={dead_ttl}d{', force' if force else ''})",
        flush=True,
    )
    lock = threading.Lock()

    def flush_ledger():
        for ats, rows in verdicts.items():
            liveness.write(ledger_dir / f"{ats}.csv", rows.values())

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
            with lock:  # settle it in the ledger (url refreshed from the pool)
                verdicts[ats][tenant] = liveness.Verdict(
                    ats, tenant, url, verdict, jobs, today_iso
                )

        start = time.monotonic()
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(do, work))
        elapsed = time.monotonic() - start
        print(
            f"  pass done in {elapsed:.1f}s ({len(work) / elapsed:.0f} boards/s)",
            flush=True,
        )
        return unknowns

    for n, (timeout, workers) in enumerate(PASSES, 1):
        if not items:
            break
        print(
            f"pass {n}: {len(items)} to probe (timeout={timeout}s, workers={workers})",
            flush=True,
        )
        items = run_pass(items, timeout, workers)
        flush_ledger()  # checkpoint per pass so a crash keeps settled verdicts
        settled = sum(
            1
            for rows in verdicts.values()
            for v in rows.values()
            if v.checked_at == today_iso
        )
        print(
            f"  -> {settled} settled this run, still unknown {len(items)}", flush=True
        )

    # Whatever is still unknown after the last pass is recorded as such (re-probed next run),
    # never silently dropped.
    for ats, tenant, url in items:
        verdicts[ats][tenant] = liveness.Verdict(
            ats, tenant, url, UNKNOWN, None, today_iso
        )
    flush_ledger()

    for ats in sorted(verdicts):
        rows = verdicts[ats].values()
        live = sum(1 for v in rows if v.status == LIVE)
        dead = sum(1 for v in rows if v.status == DEAD)
        unknown = sum(1 for v in rows if v.status == UNKNOWN)
        print(f"{ats}: live {live}, dead {dead}, unknown {unknown}", flush=True)


if __name__ == "__main__":
    main()
