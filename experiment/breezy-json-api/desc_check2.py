"""Same sample as desc_check.py; compares html_to_text of listing description vs the detail's
JSON-LD description and the page's <div class="description">, and inspects pages with no JSON-LD."""
import json,collections,random,re,sys
sys.path.insert(0,'/Users/sarthakjain/Projects/HeadStart/.claude/worktrees/add-breezy-scraper/src')
from headstart.models import html_to_text
from concurrent.futures import ThreadPoolExecutor
from curl_cffi import requests
R=[json.loads(l) for l in open('2026-09-23_pool_census_ramp.jsonl')]
boards=[r for r in R if r['status']==200 and r['rows']]
random.seed(11); bs=random.sample(boards,120)
smp=[]
for b in bs:
    for x in random.sample(b['rows'],min(2,len(b['rows']))): smp.append((b['slug'],x))
def one(t):
    s,x=t
    r=requests.get(x['url'],headers={'User-Agent':'headstart/0.1'},timeout=30,allow_redirects=False)
    rec={'slug':s,'id':x['id'],'status':r.status_code,'loc':r.headers.get('location')}
    if r.status_code!=200: return rec
    h=r.text
    a=html_to_text(x.get('description') or '')
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>',h,re.S):
        try: d=json.loads(m.group(1))
        except Exception: continue
        if d.get('@type')=='JobPosting':
            b=html_to_text(d.get('description') or '')
            rec.update(ld=True, text_eq=a==b, la=len(a), lb=len(b), datePosted=d.get('datePosted'), pub=x.get('published_date'))
            if a!=b:
                import difflib
                sm=difflib.SequenceMatcher(None,a,b); rec['ratio']=round(sm.ratio(),4)
                rec['ops']=[(o,a[i1:i2][:60],b[j1:j2][:60]) for o,i1,i2,j1,j2 in sm.get_opcodes() if o!='equal'][:3]
            return rec
    rec['ld']=False; rec['title']=re.findall(r'<title>(.*?)</title>',h)[:1]; rec['has_desc_div']='class="description"' in h
    return rec
with ThreadPoolExecutor(8) as ex: res=list(ex.map(one,smp))
json.dump(res,open('desc_check2.json','w'))
print(len(res), collections.Counter((r['status'],r.get('ld'),r.get('text_eq')) for r in res))
for r in res:
    if r.get('ld') and not r['text_eq']: print(r['slug'],r['la'],r['lb'],r.get('ratio'),r.get('ops'))
for r in res:
    if r.get('ld') is False or r['status']!=200: print('NOLD',r)
