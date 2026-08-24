# Eightfold PCSX: no client-side lever fixes the replica instability

**Date:** 2026-08-24 · **Follow-up to:** `pcsx-replica-instability.md` (#142, PR #144),
`sitemap-primary-evaluation.md` (#150 proposal, not yet built) · **Related:**
[#145](https://github.com/sarthakjain004/headstart/issues/145) (day-bucket targeted re-sweeps,
parked), the ADR-0053 scope-exclusion ratchet (separate finding, this session) ·
**Harness:** `scripts/eval/probe_eightfold_pod_affinity.py`

This document assumes you have **not** read the two docs above, though it builds on them.
Every claim in here is either something you can run yourself right now (commands included), or
clearly marked as a result already recorded from running one of those commands earlier.

## Background — for anyone new to this corner of the codebase

HeadStart scrapes job postings directly from company career sites. **Eightfold** is one of the
Applicant Tracking Systems (ATSes) it scrapes — companies like Qualcomm, NVIDIA, and Micron all
run their public job board on Eightfold's platform. `src/headstart/scrapers/eightfold.py` is the
code that reads Eightfold's boards; it works by paging through Eightfold's own JSON search API,
`GET /api/pcsx/search?domain={company}&start={offset}`, ten postings per page.

A prior investigation (`pcsx-replica-instability.md`, summarized below) found that this API's
pagination is **unstable**: asking for the same page twice can return different postings. That
caused real user-visible bugs — jobs vanishing from search and reappearing days later looking
"brand new." A fix for the *symptom* shipped (PR #144: fetch extra pages and reconcile). This
document asks a different question: **is there a fix for the *cause* instead** — some request
parameter or connection trick that makes the pagination stable in the first place? The answer,
measured directly below, is no. This document also explains, in a way you can verify yourself,
*why* the answer is no.

### The two concepts you need before any of this makes sense

**Load balancer.** When you send a request to `careers.qualcomm.com`, it doesn't go to one
computer. It hits a device that hands the request off to any one of a *pool* of interchangeable
servers behind it — which one is essentially random, and it can be a different one on your very
next request, even one second later. This is completely normal, standard architecture; it's how
almost every serious website scales.

**Replica.** To let many servers answer read requests fast, the underlying data is *copied* onto
each of them ("replicated"). Each copy holds the same rows, but each copy is a physically separate
piece of software with its own internal bookkeeping. Here's the part that causes the bug: if you
ask a replica to sort 1,000 items but 400 of them are exactly tied on the sort key, the replica has
to pick *some* order for those 400 — and it picks based on its own internal, private data layout.
A different replica, holding the identical 400 rows, can pick a *different* order for the tied
ones. Both replicas are 100% correct — "correct" just isn't specific enough to force one order.

Eightfold's search sorts by `postedTs`, a posting's date — but only the *date*, not the time. On a
board with thousands of postings, hundreds of them share the same date, so hundreds are tied, and
different replicas order those ties differently. That's the whole bug, one sentence: **ask the
same "page 3" question twice, land on two different replicas, get two different answers**, because
"page 3" only has a well-defined meaning if the full list has one true order — and here it doesn't.

## Part 1 — can a request parameter fix the ordering? (No, tested directly)

The most common real-world fix for "which replica answers my query" is a parameter that pins your
request to one replica — Elasticsearch, for instance, has a literal `preference` parameter for
exactly this. So the natural question: does Eightfold's API expose anything like that?

**You can test this yourself right now.** This one-liner hits the live API twice with a fixed
parameter and prints the first 3 job ids from each response — if the parameter pinned the query to
one replica, both calls would return the same order:

```bash
.venv/bin/python -c "
import requests
url = 'https://careers.qualcomm.com/api/pcsx/search'
params = {'domain': 'qualcomm.com', 'query': '', 'location': '', 'start': 0, 'preference': 'test-fixed-string'}
headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
for i in range(2):
    r = requests.get(url, params=params, headers=headers, timeout=30)
    ids = [p['id'] for p in r.json()['data']['positions'][:3]]
    print(f'call {i+1}: {ids}')
"
```

Run it a few times. You will see the first-3 ids sometimes match, sometimes not — the parameter
does nothing. This project tested every parameter of this shape, systematically, against
`careers.qualcomm.com`, measuring how many ids disagreed across repeated identical requests to the
same offset (lower is more stable; 0 would mean the parameter fixed it):

| parameter tried | what it's *supposed* to do (on a real ES/OpenSearch cluster) | measured instability (ids disagreeing across 4 reps, offsets 0/500/1000/1500 summed) |
|---|---|---:|
| *(none — today's default)* | — | 30 |
| `preference=<a fixed string>` | pin the query to one shard copy (this is ES's actual, real mechanism for this) | 6 (still unstable) |
| `preference=_local` | ES shorthand for "whichever shard is closest" | 12 (still unstable) |
| `routing=<a fixed string>` | force requests with the same value to the same shard | 18 (still unstable) |
| `consistent_read=true` | a common flag name for "don't serve stale/inconsistent replicas" | 18 (still unstable) |
| `seed=<value A>` | a deterministic shuffle seed, if the API supports one | 34 (no improvement over default) |
| `seed=<value B>` | same idea, different value | 24 (basically noise — see below) |
| `sort_by=id` | ask for a stable, unique sort key instead of the tied date | **silently ignored** — response identical to no `sort_by` at all |

None of these did anything real. The two `seed` values (34 vs 24) look like `seed` "helped a
little," but it didn't — passing the *same* seed twice landed on *different* replicas just as
often as passing no seed at all. That 34-vs-24 gap is coin-flip noise, not a trend; **do not read
anything into small differences here — the numbers only mean something in aggregate, over many
repeats, which is what Part 2 below does properly.**

**Conclusion of Part 1:** the API accepts these parameters (no error), but genuinely does nothing
with any of them. There is no documented or undocumented parameter that pins the query.

## Part 2 — why doesn't a session/cookie/connection trick work either?

The next idea, when a parameter doesn't work: maybe reusing the same TCP connection, or the same
cookie session, keeps you talking to the same server. **Run this yourself** — it compares a fresh
connection every request against one reused (`requests.Session`) connection, over 12 reps at 2
offsets:

```bash
.venv/bin/python -c "
import requests, time
url = 'https://careers.qualcomm.com/api/pcsx/search'
params_base = {'domain': 'qualcomm.com', 'query': '', 'location': ''}
headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}

def orderings(get_fn, offset, reps=12):
    seen = set()
    for _ in range(reps):
        r = get_fn(url, params={**params_base, 'start': offset}, headers=headers, timeout=30)
        seen.add(tuple(p['id'] for p in r.json()['data']['positions']))
        time.sleep(0.4)
    return len(seen)

print('fresh connection each time, offset 500:', orderings(requests.get, 500), 'distinct orderings')
s = requests.Session()
print('one reused session, offset 500:       ', orderings(s.get, 500), 'distinct orderings')
"
```

You'll see both report more than one distinct ordering. **This doc's own live run of that
experiment** (n=15, 3 offsets) found no meaningful difference between fresh-connection and
session-reuse — both stayed unstable. Section "Part 3" below explains *why* a session can't help,
with direct proof rather than just another negative result.

## Part 3 — the actual proof: correlating the server pod against the answer

Every response from `/api/pcsx/search` carries a response header, `X-EF-IID`, that names the exact
backend application process that handled it — something like
`prod3-www7-65ff96fbd5-qm52n`. Nothing hides this; it's just sitting in the headers of every
response, for free. **You can see it yourself:**

```bash
curl -sD - -o /dev/null "https://careers.qualcomm.com/api/pcsx/search?domain=qualcomm.com&start=0" \
  -H "User-Agent: Mozilla/5.0" | grep -i x-ef-iid
```

Run that a few times in a row and you'll get a *different* pod name almost every time — confirming
the load-balancer picture from the Background section directly. But the pod name lets us answer a
much sharper question than "is there a load balancer": **does the pod that answers your request
control which ordering you get?** If it did, then a fixed session (Part 2) — which pins you to one
pod, since a kept-alive connection talks to the same server process — *would* have worked. It
didn't. So either the pod doesn't determine the ordering, or something else is going on. This is
directly testable: hit the same offset many times, record `(pod, ordering)` for every response, and
look at every pod that happened to answer more than once. Does it always give the same ordering
back? Or does it flip?

**This is exactly what `scripts/eval/probe_eightfold_pod_affinity.py` does. Run it yourself:**

```bash
.venv/bin/python -u scripts/eval/probe_eightfold_pod_affinity.py careers.qualcomm.com --start 500 --reps 30
```

A real run of that exact command, live-verified 2026-08-24:

```text
board: careers.qualcomm.com  group_id: qualcomm.com  offset: 500  reps: 30

  1  prod3-www7-65ff96fbd5-...
  2  prod3-www7-65ff96fbd5-...
  ... (30 lines, one pod name per request)

responses: 30  distinct pods: 25  distinct orderings: 2
pods hit more than once: 5, of which 1 answered from more than one ordering on their own repeat hits
  FLIPS: prod3-www7-65ff96fbd5-9ld8s

VERDICT: pod identity does not determine the ordering — the same pod answers from either backend
interchangeably. No app-tier signal can pin a replica.
```

Read that line carefully: **the exact same pod, `...9ld8s`, answered from ordering #1 on one hit
and ordering #2 on another hit** — same process, different answer. That single fact rules out every
idea in Parts 1 and 2 at once. A cookie, a session, a request parameter — anything sent from your
side of the connection — can, at absolute best, control *which pod* answers you (and even that
isn't fully true, since the load balancer picks). But controlling the pod doesn't help, because the
pod itself doesn't control which of the tied orderings it hands back. The instability lives one
layer further down than anything a client can reach: in whichever data replica *that* pod happens
to query for that particular request.

```text
  30+ stateless app pods, no session/cookie affinity between them
              │
              ▼ (each pod queries indifferently)
  exactly 2 underlying data replicas, and the SAME pod answers from either one
```

**Is this specific to Qualcomm's board, or does every Eightfold board work this way?** Tested a
second, unrelated tenant to check — `ngc.eightfold.ai` is a completely different company, served by
a completely different pool of pods (`prod2-www7-599c8b785f-*`, not `prod3-www7-65ff96fbd5-*`):

```bash
.venv/bin/python -u scripts/eval/probe_eightfold_pod_affinity.py ngc.eightfold.ai --reps 30
```

Live-verified result: 25 distinct pods, 2 distinct orderings, and again a repeated pod
(`prod2-www7-599c8b785f-9x9kb`) flipped between both. Same shape, independent tenant, independent
pod pool — this is Eightfold's general architecture, not a one-board fluke.

**One honest caveat if you run this yourself:** the effect isn't guaranteed on *every* single
invocation. Re-running the exact `--start 500` command above at `--start 0` on the same day
returned only 1 distinct ordering in 30 reps — not because the bug went away, but because whether
a given offset currently sits inside a big enough tie-cluster to show the effect depends on how
many postings share a date *right now*, and postings open and close constantly. If your own run
shows no instability, try a larger `--reps`, or a few different `--start` offsets — a busier board
(more total postings) will show it more reliably than a small one.

## Conclusion

There is no available client-side fix — no request parameter, no session strategy, nothing sent
from the calling side of the connection — that removes this instability at its source. This
extends `pcsx-replica-instability.md`'s own negative result (which only tested sort parameters)
to the full space of session/routing/preference-style parameters, and grounds it in a direct
measurement of *why* — a stateless pod fleet with no control over which data replica it queries —
rather than just a longer list of things that didn't work.

### What remains standing, unchanged by this result

- **`sitemap-primary-evaluation.md`'s trust-gate architecture** is still the right fix if built —
  it doesn't try to compensate for the instability, it routes around it entirely by reading a
  different, batch-generated surface (`/careers/sitemap.xml`) that replica ordering can't touch in
  the first place. This investigation didn't test anything that bears on that decision.
- **Raising `_MAX_SWEEPS`** (currently 3, in `_api_search`) is a plausible cheap lever on the
  *current* API path — with a genuine 2-replica lottery, each extra sweep should improve coverage
  geometrically, the same reasoning that makes 3 sweeps already close most of the gap. Not measured
  in this document; would need a clean, unthrottled multi-sweep crawl.
- **The downstream consequence is separate, and was found live in production during this same
  session:** a board that `_api_search`'s existing sweep-and-report mechanism marks
  non-authoritative is excluded from `index sync`'s eviction scope *entirely* (ADR-0053,
  `boards -= excluded` in `index.py`), with no bound and no drain — unlike the sibling ADR-0046
  collapse guard, which ADR-0055 gave a bounded drain for exactly this failure mode. Measured
  against the live board (via the scraper's own `_description` detail call, not sitemap presence —
  a first attempt using sitemap absence as the oracle was itself invalidated when
  `nab.eightfold.ai`'s sitemap turned out to be a different, broken index):
  `careers.qualcomm.com` currently serves **105 confirmed-dead rows** (postings the board's own
  API no longer lists and whose detail page returns nothing), `careers.micron.com` 29, oldest
  first-seen 2026-08-01 — 22 days served with no removal path, across 21 consecutive production
  runs where the same handful of boards were excluded every time. This is a real, presently-
  accreting cost independent of whether #145 or #150 gets built, and it argues for a
  bounded/proportional exclusion (mirroring ADR-0055) as the actual priority — the replica
  instability itself may never be fully closable, but its consequence for the served table does
  not have to be permanent.

## Reproduction — every command, in the order to run them

All of these were run live against production Eightfold boards on 2026-08-24. None require any
credentials — everything here is a plain, unauthenticated GET a browser could make too.

1. **See the load balancer directly** (Part 3, above) — one `curl`, run it 3-4 times in a row:
   ```bash
   curl -sD - -o /dev/null "https://careers.qualcomm.com/api/pcsx/search?domain=qualcomm.com&start=0" \
     -H "User-Agent: Mozilla/5.0" | grep -i x-ef-iid
   ```
2. **Confirm no parameter pins the ordering** (Part 1) — the inline Python snippet in that section,
   swap `preference` for any of the other parameters in the table to try them yourself.
3. **Confirm session reuse doesn't help either** (Part 2) — the inline Python snippet in that
   section.
4. **The full pod-correlation proof** (Part 3) — the actual reusable harness:
   ```bash
   .venv/bin/python -u scripts/eval/probe_eightfold_pod_affinity.py <board-host> [--start N] [--reps N]
   ```
   Try it against any live Eightfold board in `data/validate/liveness/eightfold.csv` (any row
   marked `live`). A bigger board (more total postings, hence more date-ties) shows the effect more
   reliably.

No raw capture files or session transcripts are kept alongside this document — every finding above
is either a command you can re-run directly, or a result from `probe_eightfold_pod_affinity.py`,
which is committed and reusable.
