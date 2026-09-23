"""Cross-tenant rate-limit ramp that doubles as the full-pool census.

Shuffles the pool (seed 7), then runs successive 250-tenant slices at rising concurrency, each
tenant fetched once (`/json?verbose=true`, redirects not followed). Every record carries its
stage, so req/s, latency and every status other than 200/404 are per concurrency level. The
remainder of the pool then runs at REST_CONC.

Usage: python ramp.py POOL_CSV OUT_JSONL STAGES REST_CONC     (STAGES like 4,8,16,32,64)
"""
import csv, json, random, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from curl_cffi import requests

pool, out, stages, rest = sys.argv[1], sys.argv[2], [int(x) for x in sys.argv[3].split(",")], int(sys.argv[4])
slugs = sorted({r["tenant"] for r in csv.DictReader(open(pool))})
random.Random(7).shuffle(slugs)


def one(slug):
    t = time.time()
    try:
        r = requests.get(f"https://{slug}.breezy.hr/json?verbose=true", allow_redirects=False,
                         headers={"User-Agent": "headstart/0.1"}, timeout=30)
    except Exception as e:  # noqa: BLE001
        return {"slug": slug, "status": None, "err": repr(e)[:200], "elapsed": round(time.time() - t, 3)}
    rec = {"slug": slug, "status": r.status_code, "location": r.headers.get("location"),
           "elapsed": round(time.time() - t, 3), "bytes": len(r.content),
           "ctype": r.headers.get("content-type"), "retry_after": r.headers.get("retry-after")}
    if r.status_code == 200:
        try:
            rows = r.json()
            rec["n"] = len(rows) if isinstance(rows, list) else None
            rec["rows"] = rows
        except ValueError:
            rec["body"] = r.text[:300]
    else:
        rec["body"] = r.text[:300]
    return rec


plan, i = [], 0
for c in stages:
    plan.append((c, slugs[i:i + 250])); i += 250
plan.append((rest, slugs[i:]))
with open(out, "w") as f:
    for conc, chunk in plan:
        t0 = time.time(); bad = 0; lat = []
        with ThreadPoolExecutor(conc) as ex:
            for fu in as_completed([ex.submit(one, s) for s in chunk]):
                rec = fu.result(); rec["stage_conc"] = conc
                f.write(json.dumps(rec) + "\n"); f.flush()
                lat.append(rec["elapsed"])
                if rec["status"] not in (200, 404):
                    bad += 1
                    print("  NON-200/404", rec["slug"], rec["status"], rec.get("location"), rec.get("err"), flush=True)
        dt = time.time() - t0; lat.sort()
        print(f"conc {conc}: {len(chunk)} req in {dt:.1f}s = {len(chunk)/dt:.1f} req/s, "
              f"p50 {lat[len(lat)//2]:.2f}s p95 {lat[int(len(lat)*.95)]:.2f}s, other-status {bad}", flush=True)
