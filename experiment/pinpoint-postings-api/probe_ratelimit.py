"""Concurrency ramp. Mode `one`: N requests to one tenant's /postings.json per level.
Mode `many`: one request per distinct tenant per level (live pool tenants). Mode `pages`:
posting pages of one big board. Prints req/s, p50/p95 latency and non-200 counts per level."""
import sys, time, json, glob, statistics, collections
from concurrent.futures import ThreadPoolExecutor
import requests
H = {"User-Agent": "headstart/0.1"}
mode = sys.argv[1]
S = requests.Session()
S.mount("https://", requests.adapters.HTTPAdapter(pool_connections=200, pool_maxsize=200))
def get(u):
    t = time.time()
    try:
        r = S.get(u, headers=H, timeout=60); return r.status_code, time.time() - t
    except Exception as e:
        return type(e).__name__, time.time() - t
live = sorted(f.split("/")[-1][:-5] for f in glob.glob("artifacts/listings/*.json"))
pages = [p["url"] for p in json.load(open("artifacts/listings/joinparachute.json"))["data"]]
off = 0
for c in (1, 4, 16, 32, 64, 128):
    if mode == "one":
        urls = ["https://zincwork.pinpointhq.com/postings.json"] * max(16, 2 * c)
    elif mode == "pages":
        urls = pages[off:off + max(16, 2 * c)]; off += len(urls)
    else:
        urls = [f"https://{s}.pinpointhq.com/postings.json" for s in live[off:off + max(16, 2 * c)]]; off += len(urls)
    t = time.time()
    with ThreadPoolExecutor(c) as ex:
        res = list(ex.map(get, urls))
    el = time.time() - t
    lat = sorted(x[1] for x in res)
    print(mode, f"conc={c}", f"n={len(res)}", f"{len(res)/el:.1f} req/s", f"p50={statistics.median(lat):.2f}s",
          f"p95={lat[int(.95*len(lat))-1]:.2f}s", dict(collections.Counter(x[0] for x in res)), flush=True)
