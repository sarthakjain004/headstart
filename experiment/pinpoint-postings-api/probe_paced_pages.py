"""Paced posting-page load (Accept: text/html) across many Boards' postings, per 10 s window.
Usage: probe_paced_pages.py RATE SECONDS"""
import sys, time, glob, json, threading, collections
import requests
rate, dur = float(sys.argv[1]), float(sys.argv[2])
urls = []
for f in sorted(glob.glob("artifacts/listings/*.json")):
    d = json.load(open(f))["data"]
    urls += [f"https://{f.split('/')[-1][:-5]}.pinpointhq.com{p['path']}" for p in d[:5]]
win = collections.defaultdict(collections.Counter); lock = threading.Lock(); t0 = time.time()
def one(u, k):
    try:
        st = requests.get(u, headers={"User-Agent": "headstart/0.1", "Accept": "text/html"}, timeout=30).status_code
        res = "ok" if st == 200 else f"http{st}"
    except requests.ConnectionError:
        res = "refused"
    except Exception as e:
        res = type(e).__name__
    with lock: win[k][res] += 1
ths = []
for i, u in enumerate(urls):
    now = time.time() - t0
    if now > dur: break
    th = threading.Thread(target=one, args=(u, int(now // 10))); th.start(); ths.append(th)
    time.sleep(max(0, (i + 1) / rate - (time.time() - t0)))
for th in ths: th.join()
for k in sorted(win): print(f"pages rate={rate} t={k*10:>4}s", dict(win[k]), flush=True)
