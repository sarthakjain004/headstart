"""Reproduce the liveness pass's false DEADs: fire the plain `/json` at known-live tenants at a
high concurrency and record every non-200 with its headers and body size.

Usage: python burst_404.py LEDGER_CSV OUT_JSONL CONC N"""
import csv, json, random, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from curl_cffi import requests

ledger, out, conc, n = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
live = [r["tenant"] for r in csv.DictReader(open(ledger)) if r["status"] == "live"]
random.Random(3).shuffle(live)
live = live[:n]


def one(t):
    t0 = time.time()
    try:
        r = requests.get(f"https://{t}.breezy.hr/json", allow_redirects=False,
                         headers={"User-Agent": "headstart/0.1"}, timeout=25)
    except Exception as e:  # noqa: BLE001
        return {"tenant": t, "status": None, "err": repr(e)[:120]}
    rec = {"tenant": t, "status": r.status_code, "bytes": len(r.content), "elapsed": round(time.time() - t0, 2)}
    if r.status_code != 200:
        rec["headers"] = {k: v for k, v in r.headers.items() if k.lower() in (
            "server", "x-cache", "via", "content-type", "x-amz-cf-pop", "x-envoy-upstream-service-time", "retry-after")}
        rec["title"] = r.text[:3000].split("<title>")[1].split("</title>")[0] if "<title>" in r.text[:3000] else r.text[:120]
    return rec


t0 = time.time(); c = {}
with open(out, "w") as f, ThreadPoolExecutor(conc) as ex:
    for fu in as_completed([ex.submit(one, t) for t in live]):
        rec = fu.result(); f.write(json.dumps(rec) + "\n"); f.flush()
        k = rec["status"] or rec.get("err", "")[:40]; c[k] = c.get(k, 0) + 1
        if rec["status"] != 200:
            print(rec, flush=True)
print(f"conc {conc}: {len(live)} in {time.time()-t0:.1f}s {c}", flush=True)
