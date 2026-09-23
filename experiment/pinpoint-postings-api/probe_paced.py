"""Paced load: R request starts per second across distinct live tenants' /postings.json for D
seconds (threads, so slow responses overlap). Prints ok/refused per 10 s window, so the onset of
the connection-refusal wall is visible. Usage: probe_paced.py RATE SECONDS [path]"""
import sys, time, csv, threading, collections, itertools
import requests
rate, dur = float(sys.argv[1]), float(sys.argv[2])
path = sys.argv[3] if len(sys.argv) > 3 else "/postings.json"
slugs = [r["tenant"] for r in csv.DictReader(open("../../data/validate/liveness/pinpoint.csv")) if r["status"] == "live"]
win = collections.defaultdict(collections.Counter); lock = threading.Lock(); t0 = time.time()
def one(s, k):
    try:
        st = requests.get(f"https://{s}.pinpointhq.com{path}", headers={"User-Agent": "headstart/0.1"}, timeout=30, allow_redirects=False).status_code
        res = "ok" if st == 200 else f"http{st}"
    except requests.ConnectionError:
        res = "refused"
    except Exception as e:
        res = type(e).__name__
    with lock: win[k][res] += 1
threads = []
for i, s in enumerate(itertools.cycle(slugs)):
    now = time.time() - t0
    if now > dur: break
    th = threading.Thread(target=one, args=(s, int(now // 10))); th.start(); threads.append(th)
    time.sleep(max(0, (i + 1) / rate - (time.time() - t0)))
for th in threads: th.join()
for k in sorted(win): print(f"rate={rate} t={k*10:>4}s", dict(win[k]), flush=True)
