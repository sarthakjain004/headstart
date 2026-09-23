"""One-tenant ramp: 64 requests per concurrency level against one Board's `/json?verbose=true`
(and, separately, its `/p/` detail pages, the surface upstream reported 403s on).
Usage: python single_tenant_ramp.py SLUG"""
import sys,time,collections
from concurrent.futures import ThreadPoolExecutor
from curl_cffi import requests
s=sys.argv[1]; H={'User-Agent':'headstart/0.1'}
urls=[x['url'] for x in requests.get(f'https://{s}.breezy.hr/json',headers=H).json()]
def get(u):
    t=time.time(); r=requests.get(u,headers=H,timeout=60,allow_redirects=False); return r.status_code,time.time()-t,len(r.content)
for label,pick in (('listing',lambda i:f'https://{s}.breezy.hr/json?verbose=true'),('detail',lambda i:urls[i%len(urls)])):
    for c in (1,4,16,32,64,128):
        n=64 if c<128 else 256
        t0=time.time()
        with ThreadPoolExecutor(c) as ex: res=list(ex.map(get,[pick(i) for i in range(n)]))
        dt=time.time()-t0; lat=sorted(r[1] for r in res)
        print(f'{label} conc {c}: {n} req {n/dt:.1f} req/s p50 {lat[n//2]:.2f}s p95 {lat[int(n*.95)]:.2f}s statuses {dict(collections.Counter(r[0] for r in res))} bytes {res[0][2]}',flush=True)
