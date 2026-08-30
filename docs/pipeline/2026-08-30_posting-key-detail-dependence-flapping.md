# `_posting_key`'s detail-dependence is 58% of all index flapping

**Date:** 2026-08-30 · **Window:** the 12 pipeline runs `33283745755`→`33303633939` (2026-08-30,
01:39→09:16Z) · **Predicted by:** [ADR-0088](../adr/0088-a-lost-detail-is-not-a-truncation.md),
which named this defect on `roche` and deferred the fix to `_posting_key` · **Distinct from**
`2026-08-24_pwc-flapping-root-cause.md`, which is a *listing*-page race on a different pwc board

## Summary

`flap_audit --runs 12` returned **RED**: 25% already-known adds against a 10% bar, 216 flapped
rows. A single board, `workday:roche/roche-ext`, is **58%** of that, and the top-12 board list
sums to exactly 216 — it is the whole population, not a sample.

The cause is not ATS instability and not the eviction logic. It is that
**`_posting_key` returns a different id for the same posting depending on whether an optional
network fetch succeeded.**

```
detail OK   -> 202607-119609
detail FAIL -> ERP-Solution-Consultant---EHS_202607-119609
```

A posting whose detail fetch fails is not *missing* from the scrape — it is **renamed**. The old
id is absent, goes Unconfirmed (ADR-0083), and is evicted on the second consecutive absence. When
the detail pass recovers, the original id reappears and is re-added. That is the flap.

## The evidence

`_posting_key` prefers `_detail["jobReqId"]`, which exists only after the per-job detail pass.
Its fallback tiers are the listing's `bulletFields` req-id (gated on `_looks_like_req_id`) and
then the `externalPath` tail. **Two independent doors lead to the tiers disagreeing:**

| board | `bulletFields` | why the tiers disagree |
|---|---|---|
| `roche/roche-ext` | `['202607-119609']` | `_looks_like_req_id` **rejects** `NNNNNN-NNNNNN` — neither regex alternative admits digits-hyphen-digits |
| `pwc/crm_experienced_careers_site` | req id present | rejects `726071WD` (digits-then-letters) |
| `saabgroup/Saab_careers` | **`None`** | no bulletFields at all — nothing for the regex to accept |
| `autodesk/Ext` | — | same shape as saab (`26WD100347` also rejected) |

So the regex gap is *one* door, not the root cause. Saab has no bulletFields to test and flaps
anyway. The root cause is the detail-dependence itself.

### Confirmed end-to-end in production

The two id shapes **alternate** in the merge logs — they are mutually exclusive spellings of the
same postings trading places:

| run | roche adds | roche evicts |
|---|---|---|
| `33283745755` | — | 47 externalPath-tail |
| `33286160766` | **125 externalPath-tail** | — |
| `33288099045` | — | **77 bare req-id** |
| `33289938377` | **75 bare req-id** | 16 externalPath-tail |
| `33291624533` | 2 bare req-id | 72 externalPath-tail |
| `33293724271` | 1 externalPath-tail | 37 externalPath-tail |

And the run that evicted the 77 bare req-ids says why, in its own shard log:

```
spare egress: workday:roche/roche-ext walled the current IP — rotating
workday:roche/roche-ext: 827 of 1210 detail(s) failed mid-crawl (HTTP 400 x827)
```

68% of details lost → 68% of the board renamed → eviction on the second such run.

All 216 flapped rows took the `evict` (sync) path; **zero** took either prune path.

## Blast radius

- **5 boards** emitted both id shapes in the 12-run window: roche, pwc/crm, jj/JJ,
  tapestry, usbank.
- **217 Workday boards** logged detail losses in run `33288099045` alone. Most lose too few
  details to cross into eviction; the defect is latent on all of them.
- A board only flaps when losses are both **large** and **sustained across two consecutive
  scrapes of that board** — which is why the churn is concentrated rather than universal.

## Migration cost of fixing it in `_posting_key`

A first pass sampled 14 random Workday boards (12 usable, 942 postings): 11 stable, 1 migrating
(`gellerco`, no bulletFields). **That sample was too small to publish a figure from, and an early
draft of ADR-0097 published one anyway** — see the sweep below, which replaced it.

**The sweep** (`scripts/eval/workday_id_migration.py --boards 140`; 102 boards returned usable
listing+detail data). Run twice, once per design:

| identity rule | boards migrating | postings migrating | projected served rows |
|---|---|---|---|
| shape tier alone | 6 / 102 (5.9%) | 2,212 / 27,287 (8.1%) | ~6,000 |
| **+ URL-vouched tier** | **1 / 102 (1.0%)** | **452 / 27,510 (1.6%)** | **~1,200** |

A reviewer's re-run of the first row at the same seed got 8 / 102 and 1,740 postings — a Board
that fails its detail fetch drops out of the sample, so the counterfactual moves between runs.
The ~5× reduction survives both; the exact first-row figures should be read as a range.

Boards are projected over the **7,620 Scrapable Workday Boards** `load_active_companies()`
reports, not the ledger's 10,538 raw rows; served rows apply Workday's ~6.9% tech keep rate. One
migrating board in 102 is a small numerator — treat the projection as an order of magnitude.

The single board left is `saabgroup/Saab_careers`, which carries no `bulletFields` at all.

Under the shape-only design the six were:

| board | rows | why it migrated |
|---|---:|---|
| `usbank/US_Bank_Careers` | 1,479 | `2026-0026665` — YYYY-serial, below the six-digit floor |
| `mercyhealth/mercyhealthcareers` | 579 | `2026-02608` — same |
| `montagehealth/montage_health` | 84 | `2026-968` — same |
| `aafp/aafp_careers` | 6 | `37-26` — same |
| `wisconsin/UW_Milwaukee` | 30 | bulletFields is `Application Deadline: 09/13/2026`; the real req id is nowhere in the listing |
| `hoedlmayr/External` | 34 | **no bulletFields** — irreducible |

Four of six are the year-serial shape the ZIP+4 guard excludes: `^\d{6,}[-_]\d{3,}$` keeps out a
bare `12345-6789`, and a four-digit year with it. **All four are recovered by the URL-vouched
tier**, which needs no shape at all — it asks whether the posting's own `externalPath` ends with
the field. Only `hoedlmayr` (no bulletFields) and `wisconsin` (a date label, real req id absent
from the listing) survive as irreducible, and of those only one still migrated in the re-run.

## The reproduction loop

Deterministic, no network, milliseconds — asserts the invariant directly:

```python
item = {'bulletFields': ['202607-119609'],
        'externalPath': '/job/Hyderabad/ERP-Solution-Consultant---EHS_202607-119609'}
with_detail = {**item, '_detail': {'jobReqId': '202607-119609'}}
without     = {**item, '_detail': {}}
assert _posting_key(with_detail) == _posting_key(without)   # currently FAILS
```

A listing-only stability probe (`_exhaust` with no detail pass) fetches roche in **4s** and
confirms the raw listing itself is stable — 1,208 postings, identical across three consecutive
fetches. The instability is entirely ours.

## What this does not explain

`saabgroup/Saab_careers` (14% of flapped rows) emits *only* externalPath-tail ids in the window,
so its detail pass fails on every run rather than intermittently — the tier never flips back.
Why those rows still evict and return is **not established here**. The `-N` externalPath suffix
was tested as a candidate and ruled out: zero suffix-pairs among its 36 ids.

---

# Upstream: why the detail fetches fail at all

The rename above is the *proximate* cause. The thing that triggers it is a second, independent
defect one layer up.

## Workday throttles CI as HTTP 400, and we handle 400 nowhere

| | `_TRANSIENT` (retried) | `workday.egress_fallback_on` (rotates egress) |
|---|---|---|
| 403, 405, 429, 5xx | ✅ | 429 only |
| **400** | **❌** | **❌** |

A Workday 400 is therefore **neither retried nor does it rotate the egress IP**. It settles
instantly and silently as a lost detail, first attempt, no recovery path. That is why loss rates
reach 68–97% on a single board — measured in run `33288099045`: `roche` 827/1210 (68%), `analogdevices` 655/920 (71%), `walmart` 729/934 (78%), `dxctechnology` 837/860 (97%).

## The 400s are transient throttling, not malformed requests

Four measurements, each of which could have falsified it:

1. **The same postings recover next run.** Of the 77 roche postings whose detail 400'd in run
   `33288099045`, **75 (97%)** fetched successfully in run `33289938377`. The 400 is not a
   property of the request.
2. **The identical load from a residential IP returns 429, not 400.** A full local pass over all
   1,208 roche details at production concurrency (25 streams): `{200: 1124, 429: 84}` — 93%
   success, and the failures arrive as the *retryable* status.
3. **It is not concurrency.** 400 details at **80** streams — 3× production — returned `{200: 400}`.
   Not reproducible from this IP at any rate tried.
4. **It is not board size.** 400-share by board size is 29.6% (0–100 details), 9.9%, 20.1%, 11.0%,
   8.4% (1500+). Small boards are hit *hardest*, so it is a shard-wide episode catching whatever
   is in flight, not a per-board rate ceiling.

The remaining explanation consistent with all four is **IP reputation**: Cloudflare (all of
`myworkdayjobs.com` is Cloudflare-fronted) serves 400 to the Azure datacenter ranges GitHub
Actions runs on, and to the WARP egress we rotate to, while returning 200/429 to a residential IP.

## Hypotheses tested and killed

| hypothesis | test | verdict |
|---|---|---|
| The ATS listing oscillates | 3 consecutive live fetches | **dead** — 1,208 postings, identical |
| Concurrency causes the 400s | 80 streams locally | **dead** — 400/400 succeeded |
| Cloudflare-fronting discriminates | `server` header, 12 boards each side | **dead** — 12/12 Cloudflare both |
| Board size drives it | 400-share by size bucket | **dead** — inverse if anything |
| `externalPath` `-N` suffix churn (saab) | suffix-pair search across 36 ids | **dead** — 0 pairs |
| `_resolve_instance` picks a wrong pod | read: all probes fail when walled → keeps hint | **dead** |

## What is still unproven

Whether an **immediate in-run retry** recovers a CI 400. The 97% recovery evidence is
*next-run* — hours later — and the 400 could not be reproduced from this machine, so retry
efficacy cannot be measured locally. If the throttle episode outlasts the retry ladder
(3 attempts, 30 s cap), only **egress rotation** would help, not retries. Settling that needs a
CI run, not more local work.

---

# Outcome

Fixed 2026-08-30 by **[ADR-0097](../adr/0097-a-postings-id-comes-from-the-listing-never-the-detail.md)**:
`_posting_key` no longer reads `item["_detail"]`, and `_REQ_ID_SHAPE` gained the three
digit-leading shapes so the listing tier *agrees* with the value the detail used to supply.

Verified live after the change, on the four motivating boards:

| board | rows | id after the fix |
|---|---:|---|
| roche/roche-ext | 1,208 | **unchanged** |
| pwc/crm_experienced_careers_site | 1,716 | **unchanged** |
| autodesk/Ext | 420 | **unchanged** |
| saabgroup/Saab_careers | 452 | renamed once — no `bulletFields` to vouch with |
| usbank, mercyhealth, cree | 2,198 | **unchanged** — recovered by the URL-vouched tier |

The stability invariant (`key(with detail) == key(without detail)`) held on every posting probed,
and every board tried kept every id distinct (roche 1,209/1,209, tutorperini 235/235, nkg 48/48).
**Corpus-wide the churn is ~1,200 served rows across ~75 boards** — see the sweep table above.

**The upstream 400 handling is deliberately still open** — see the section above. This fix makes
identity correct whether or not that ever lands.
