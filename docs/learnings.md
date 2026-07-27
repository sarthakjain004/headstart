# Learnings

Running log of non-obvious findings worth keeping. Newest first.

## A cross-encoder reranker needs the Job *text*, which the index deliberately doesn't keep (2026-07-26)

Designing resume→Job matching, the plan was "retrieve top N by cosine, then rerank properly." Two
facts kill the obvious version of that.

**A cross-encoder does not run on vectors.** The current search is a **bi-encoder**: resume and
**Doc** are embedded *separately*, and only the two finished vectors are compared (cosine). That
separation is exactly what makes precomputing possible — a Job's vector can be built once, months
before anyone searches. A **cross-encoder** inverts this: it feeds **both raw texts into one model
together**, so every resume token can attend to every job-description token, and it emits one
relevance score for the pair. That joint attention is the whole accuracy gain, and it is also why
nothing can be precomputed — the score exists only for a *pair*, and there are N pairs per search.
The general form of the trap: **anything computed from the stored vectors alone cannot know more
than cosine already knows**, because the vector is a lossy summary. More signal means going back to
the text.

**The text isn't there.** `index._schema()` stores id/ats/company/title/location/remote/
employment_type/experience/min_years/max_years/experience_source/salary/department/url/posted_at,
plus the vector. No description — by design, per ADR-0005/0006: a Doc is a transient string at embed
time, encoded, and dropped. `title` is the only surviving text. So a cross-encoder in the Space
would first need descriptions added to the index (~5 KB/Job × ~20k ≈ **~100 MB** on a snapshot the
Space downloads at startup, ADR-0020 free tier), or a per-search fetch from the tech JSONL.

**And the CPU budget is the harder wall.** Extrapolating from the measured 558 tok/s for a 137M
model on 2 cores (ADR-0029 thread sweep): a pair is ~100 resume tokens + the ~1,040-token median
description ≈ 1,140 tokens, so N=30 is ~34k tokens ≈ **~60 s** at nomic-class speed, worse for a
278M reranker (bge-reranker-base) at ~120 s. Only a small reranker (MiniLM-class, ~22M) *and*
truncating the description to ~256 tokens gets it to roughly 4 s — and that combination discards
much of the accuracy the cross-encoder was adopted for. Estimates from a base rate, not measured on
the Space; measure before committing.

## Cosine similarity is a ranking signal, not a match percentage (2026-07-26)

The UI renders `round(1 - _distance, 3)` as a score pill. Next to a typed query nobody over-reads
it; next to a pasted **resume** every user will read 0.62 as "I'm a 62% match." That reading is
wrong in three separate ways, and the fix is not rescaling.

**The range never reaches 0.** Vectors are L2-normalized, so cosine is the angle between two points
on a sphere. But every Job description and every resume is professional English about work — shared
vocabulary, shared sentence shapes — so nothing in the corpus is ever near-perpendicular. Scores
bunch into a narrow band near the top. Like measuring only adults' heights: everyone lands in
150–200 cm, so rescaling that to "0–100% tall" makes an average person read as 40%.

**Nothing was ever calibrated.** A calibrated score promises *of everything I scored 0.7, about 70%
really are good fits*. The model was trained only so that **more relevant ranks higher** — ordering
is the guarantee, magnitude is a by-product. No training step and no eval ever checked magnitude
against outcomes, so a percent sign is a promise the number cannot keep.

**The scale moves with the query.** Score depends on both texts, so a long resume and a short one
produce different distributions against the same Job. Two equally good candidates can see 0.55 and
0.71 purely from how their resume was worded — a number that shifts for reasons unrelated to fit
cannot be a fit percentage.

Practical consequences: rank gaps are tiny (top-1 to top-20 can be ~0.04, mostly noise), so
displaying three decimals implies resolution that isn't there. If a real number is wanted, it has to
come from a different operation than retrieval — a per-pair score, or a **coverage count** (met *k*
of *n* stated requirements), which is defensible because it counts concrete things instead of
predicting. Worth measuring your own index's score distribution (50 queries, spread of rank 1 vs
rank 20) before designing around any assumed band; the 0.4–0.8 figure is the typical pattern for
same-domain normalized embeddings, not a measurement of this corpus.

## Workable's Cloudflare is burst-tolerant but challenges sustained probing (2026-07-04)

Re-probing workable's 16.6k unknowns through the new per-host gate (8 in-flight, 4 req/s): a
**200-board burst validated clean** (50s, zero 429s), but the full run flipped Cloudflare into
**`cf-mitigated: challenge`** after ~2 minutes (~500 requests) — every response a 429 JS-challenge
page with **no Retry-After header**, so the original retry-after-only breaker never tripped and the
run burned 2k more requests scoring UNKNOWN. So its mitigation is quota-ish over a short window,
not a simple rate cap: staying under 4 req/s does not make sustained scraping safe.

No endpoint dodge exists: `{slug}.workable.com` and `www.workable.com/api/accounts/{slug}` are
wildcard 301/302s into the same `apply.workable.com` bucket (both redirect for garbage slugs too, so
they carry no liveness signal of their own). The breaker now also trips on `cf-mitigated: challenge`
and on plain-429 streaks (no headers needed). The run still recovered **~700 settled verdicts**
before the trip thanks to the 60s mid-pass ledger checkpoints. The remaining ~14k unknowns need a
challenge solve (clearance cookie) or a multi-day sub-quota trickle — both deferred.

## MPS embedding leaks driver memory per unique batch *shape* — pin the shapes (2026-07-04)

Embedding the tech corpus (nomic, fp16, MPS) kept wedging: `MPS backend out of memory … other
allocations: 50.17 GiB … Tried to allocate 19.00 KiB`. Once "other allocations" crossed the
watermark, **every** allocation was refused for the rest of the process — even 19 KiB — so the run
marched on marking all remaining docs failed. A controlled experiment isolated the mechanism:
encoding the **same (batch, seq) shape** repeatedly holds driver memory flat (7.8 GiB across 6
batches), while **each new shape adds ~2–3 GiB that is never freed** (per-shape compiled-graph
workspace, immune to `torch.mps.empty_cache()`). Sentence-transformers pads every batch to its own
longest doc, so naturally-batched corpora make almost every batch a fresh shape → guaranteed wedge.

Fix in `embed_run.py`: group docs into **token-length buckets** (512/1024/2048/4096, measured with
the real tokenizer), fixed batch size per bucket from the attention budget (n × seq² ≤ ~128M), pad
each batch's count with repeats of its first doc, and ride a **pin doc of exactly the bucket's token
length** in every batch so the tokenizer always pads to the bucket. Shapes per run: 4. Verified
numerically faithful (cosine 0.9995 vs plain encode; fp16 noise).

Related traps hit on the way: (1) a single full-context 8,192-token forward transiently demands
~50 GB on this stack — cap sequences (4,096 is the proven envelope; only ~0.01% of docs are longer);
(2) `PYTORCH_MPS_HIGH_WATERMARK_RATIO` must be ≥ the LOW ratio (default 1.4) or torch dies with
"invalid low watermark ratio"; (3) chars÷4 token estimates undershoot badly on bilingual docs whose
CJK tails tokenize at ~1 token/char — measure with the tokenizer, don't estimate; (4) the ~9 GB of
stale swap observed at session start was this same leak from earlier embed runs, hidden by the
default 1.7 watermark letting the driver grow into compressed memory/swap.

## Two recurring liveness failure modes across ATSes: 200 soft-404s and shared-host latency (2026-07-03)

Diagnosing the big "unknown" piles (workable, workday, zoho, keka, join, lever, teamtailor) surfaced
two failure modes that recur across unrelated ATSes — worth checking first for any new one.

**1. Soft-404 served as HTTP 200.** Several ATSes return 200 with their own error page instead of a
404 for a gone/unpublished board, so a count-based prober reads "no jobs → UNKNOWN" when the truth is
DEAD. Confirmed: keka ("Invalid Tenant" / "Forbidden Access" HTML), zoho (`cl-error-block` "Page does
not exist"), join (Next.js `pageProps.statusCode` 404 "Entity not found" / 410 "Resource deleted").
Fix per ATS: detect the vendor's error marker on a 200 → DEAD. **When adding an ATS prober, assume a
nonexistent tenant may 200 with an error page — test it explicitly.**

**2. Shared-host latency under our own concurrency.** ATSes whose tenants all sit behind one host
degrade when we probe them at high concurrency: workable's `apply.workable.com` hard-429s (Cloudflare,
~20h ban), lever's `api.lever.co` and join's `join.com` just get *slower* (lever p50 1.6s→9.2s at
8→120 workers, all timeouts, **no** 429s), and zoho's 1.7MB pages saturate bandwidth. Per-tenant-host
ATSes (teamtailor `{t}.teamtailor.com`) barely move under the same load. This isn't the ATS blocking
us — it's contention we create. Mitigation used here: re-probe the unknowns at **moderate concurrency
+ a longer timeout**; the real fix is a systemic per-shared-host concurrency cap (deferred).

## Zoho liveness unknowns are two things: 200 soft-404s + bandwidth-timeouts on its 1.7MB page (2026-07-03)

A `--force` liveness pass left Zoho at **6,375 unknown** (of ~7,900) — alarming until decomposed.
Zoho Recruit's careers page is a single ~**1.7MB** HTML blob (the whole job list is an
HTML-entity-encoded JSON array in an `<input value="[…]" id="jobs">` at the *end* of the page), and
the unknowns split ~evenly into two unrelated causes:

- **~56% are soft-404s.** A gone tenant or an unpublished careers site is served as **HTTP 200** with
  a 2.5KB error page (`cl-error-block`, "Page does not exist"). `_zoho_count` finds no jobs `<input>`
  → `None` → UNKNOWN, but these are definitively **DEAD**. Fix: detect `cl-error-block` on a 200 →
  DEAD (same shape as Keka's "Invalid Tenant" 200 soft-404). This mirrors a recurring ATS pattern:
  **treat a 200 that renders the vendor's own error page as DEAD, not unknown.**

- **~44% are live boards that timed out — from bandwidth saturation, not rate-limiting.** Controlled
  test (same boards, low vs high concurrency): verdict mix is *identical* at 8 vs 250 workers (no
  board is rejected), but live-page latency rises p50 2.9s→6.2s and **max 4.5s→11.8s** — right at the
  pass-1 12s timeout. 85 concurrent × 1.7MB ≈ 145MB in flight share the pipe and slow each other
  down; in the real 432-worker run (all ATSes competing) latency tips past 12s → timeout → UNKNOWN.
  It is *not* Zoho throttling us — rate-limiting would change the verdict mix at high concurrency; it
  didn't. Probed patiently (or at moderate concurrency) they all resolve in ~3s → LIVE.

**Takeaways:** (1) a large "unknown" pile on one ATS is usually a *mix* — decompose before reacting.
(2) Payload size × concurrency is a real liveness failure mode: Zoho's 1.7MB page is ~500× a
Greenhouse JSON response, so it needs **lower concurrency / a longer timeout than the default**, not
the same knob as everyone else. Re-probing the unknowns at moderate concurrency recovers the live
half; the soft-404 fix settles the dead half.

## Dropped freshteam / greythr / jobsoid / peoplestrong — dead weight for tech coverage (2026-06-21)

Removed these four from the active pipeline (merged CSVs + every provider list in
fingerprint/verify/merge/feeder). Big tenant counts (freshteam 2699, greythr 886, jobsoid 823,
peoplestrong 422) but those are **historical/dead/auth-walled boards, not live in-scope value**.
Web- + probe-confirmed reasons:
- **freshteam** — Freshworks is **sunsetting** it; renewals ended 2026-03-07, usable to ~2027.
- **greythr** — it's an **HR/payroll HRMS**; the `.greythr.com` tenants are login portals, not
  public careers boards. "greytHR Recruit" is a minor add-on.
- **jobsoid** — still an active product (NOT shut down), but its tenant base is **non-tech SMBs**
  (salons, agencies, small retail) and the tech cos that were on it (Cuemath, Urban Company) now
  return **0 live jobs** (migrated off). Useless for the project's tech-role scope specifically.
- **peoplestrong** — candidate portals are **login-walled** (`abfrl` -> `secureSloginRedirect.jsf`,
  `aavashrms` -> `altLogin.jsf`); can't read jobs without auth. Broad hire-to-exit HCM skewed to
  non-tech (logistics/retail/manufacturing). Kept **ripplehire** though — its `/candidate/` portal
  is token-public (no login) and its tenants are IT-heavy (LTIMindtree, Mphasis, UST, Tata Steel).

General lesson: a provider's **wayback/CC tenant count is not a measure of live, in-scope value**
— a 13-year union accumulates dead boards, sunset products, and off-scope (non-tech) tenants.
Weight providers by live + tech-relevant tenants, not raw historical count.

## Trakstar Hire is DataDome-protected — scrape the document, not the API (2026-06-21)

Trakstar boards (`{slug}.hire.trakstar.com`) sit behind **DataDome** bot protection. The HAR
shows the tells: the job-data XHRs go to **obfuscated, rotating URL paths** (random token
segments, not `/api/jobs`), and the page fires `POST` requests with a `DOMIdentifiers` body
(DataDome's client-side fingerprinting) that come back as `application/octet-stream` /
`{"result":0}`. Probing the obvious endpoints (`/api/jobs`, `/jobs.json`, …) all 404 because the
real API path is per-session and signed. So there is **no clean JSON endpoint to hit**.

The way through: **DataDome only guards the XHR layer, not the initial document GET.** A plain
`GET https://{slug}.hire.trakstar.com/` (even with urllib + a basic UA) returns the full ~85KB
careers page with the openings **server-rendered** into the HTML as
`<div class="… js-careers-page-job-list-item" data-href="/jobs/{code}/">` cards (title in the
`<h3 class="… js-job-list-opening-name" title="…">`, location in `js-job-list-opening-loc`). So
`scrapers/trakstar.py` parses that HTML (same shape as the Zoho scraper) instead of chasing the
API — no DataDome solve required. If Trakstar ever moves the listing behind the bot wall too,
this breaks and would need the full DataDome challenge solve (see `experiment/wellfound-datadome`).

Contrast with the other India-tier ATSes built the same week: **Keka** has a clean unauth embed
API (`/careers/api/embedjobs/default/active/{tenantUUID}`, urllib-friendly), and **SenseHQ** has
a clean public JSON feed (`/careers/api/jobs?page=N`). **Darwinbox** is the hard one — its job
API needs Basic-Auth + API key (request-only), so it's a render/HTML route, not a JSON call.

## Bulk-run throughput is bounded by unique-host DNS/connect, not the ATS APIs (2026-06-19)

Spent many cycles chasing why the 396-company run crawled at ~4/min despite a fast network. The
diagnosis matters:
- A concurrent test of 16 workers × 32 calls to **one** host (greenhouse API) finished in 4s,
  zero fails — so the thread pool and bandwidth are fine.
- Instrumenting a single company showed ~16-23s *serially* (heavy SPA homepage 9-11s + /careers
  + 8 slug-probes), but under full concurrency per-company time inflated ~10×.
The cause is **unique-host fan-out**: the real run touches hundreds of distinct hosts (every
company domain + 4 ATS APIs), each needing a fresh DNS lookup + TLS handshake, and a home
router's DNS forwarder chokes on many concurrent *unique* lookups. The single-host test hides
this because the connection is warm. Throughput is gated by the slowest tier (the company's own
heavy SPA homepage), not the ATS APIs.

Levers that helped: trim `careers_html` to 2 fetches (homepage + /careers) instead of 5
(homepage + scraped links + /careers + /jobs) — company homepages are the slow tier; and keep
the expensive subdomain title-probe OUT of the per-company hot path (it's ~20 fetches/company;
at full concurrency it caused self-inflicted congestion collapse). It lives in the separate
`verify_misses.py` pass instead. Net: main run ~15-20 min for 396, verify ~12 min. The per-fetch
wall-clock deadline + per-company budget guarantee completion regardless; they don't make it
fast, they make it *finish*.

## Why the fingerprinter missed catchable boards — three fixes (2026-06-19)

Ran the fingerprinter on all 396 seed companies (76 hits), then a verify pass over the 320
misses recovered **39** more — meaning the main pass was dropping boards it could catch. Web
research on each recovery showed three distinct causes, each with a fix:

1. **Budget starvation (the big one).** `run()` called the heavy `careers_html` homepage/careers
   scan *before* the fast slug-probe, sharing one 45s per-company budget. A heavy SPA homepage
   (phonepe.com) ate ~25s, so the slug-probe hit the deadline and exited early — dropping a clean
   `greenhouse/phonepe` that resolves in one fast call. Fix: **reorder** `run()` so the cheap,
   high-precision slug-probe runs first, then careers scan, then the slow subdomain probe.

2. **No retries on transient failures.** A single timed-out ATS-API call read as "no board," so
   Postman/Druva/Atlan/Freshworks (all clean greenhouse/lever/ashby/SR boards) were dropped on a
   network blip. Fix: **retry transient failures** (timeout/reset/5xx/429) in the probe path;
   never retry a 4xx (greenhouse 404 = definitively no board).

3. **No subdomain probe for the India tier.** The main pass only found darwinbox/keka/jobsoid
   boards if their link happened to be in the careers HTML it fetched. Boards reachable only via
   a deep subpage, a custom domain, or JS were invisible (Delhivery, Perfios, CarDekho, MoEngage,
   …). Fix: fold the **subdomain title-test probe** into `run()` as a last resort — fetch
   `{slug}.darwinbox.in` etc. and confirm by the company name in `<title>`.

**FP caveat surfaced by web research:** the title test trusts enterprise namesakes too easily.
`wipro.jobsoid.com` ("Wipro Ltd. Jobs Portal") and `infosys.zohorecruit.in` ("Jobs at Infosys")
match, but Wipro/Infosys are far too large for those SMB ATSes and use their own portals
(careers.wipro.com on SuccessFactors; infosys.com/careers) — these are namesakes/squats. A
job-marker content guard does NOT separate them (real darwinbox boards are SPA login pages with
zero job markers). Enterprise-name-on-SMB-ATS hits need an eyeball; the self-reference filter
(`PROVIDER_DOMAINS`) already drops the clean provider-self-match case (darwinbox→darwinbox).

## Verifying ATS hits: slug-probe beats crawling; wildcard + cross-company traps (2026-06-18)

**Careers-page crawling is fragile; slug-probing is the high-recall catch.** PhonePe and slice
both *are* on Greenhouse, but the fingerprinter missed them: PhonePe's board link sits one
subpage deep (`/careers/` → `/careers/job-openings/`, which even exposes `?department=engineering`),
and slice fronts its greenhouse board with a custom domain (`slice.careers`). Neither shows on
the careers page we scanned. Probing the clean-JSON APIs with candidate slugs derived from the
name/domain (`greenhouse/phonepe`, `greenhouse/slice`) finds both in one call, regardless of page
structure — so the fingerprinter now unions a slug-probe pass with the careers-page scan. For
*known* companies, recall+cost order is: slug-probe > careers-page fingerprint > headless render.

**Wildcard subdomains make HTTP 200 a useless liveness signal.** The India-tier subdomain ATSes
(darwinbox, keka, zohorecruit, freshteam, turbohire) answer **200 for any subdomain**, real or
fake — `zzfakenonexist.keka.com` returns the same bytes as a real tenant. So a blind subdomain
probe confirms nothing. The reliable content signal: a real tenant renders the **company name**
in the page `<title>` / body (`pinelabsgroup.turbohire.co` → "Pine Labs Group"), while a
nonexistent tenant renders the **generic provider name** ("TurboHire"). Confirm by content, never
by status code. (This also means the earlier 200-based liveness checks on darwinbox/keka hits
proved nothing — those hits were valid only because the company's *own* page named the exact
subdomain.)

**Cross-company false positives.** A rendered careers page can embed *another* company's board.
Setu (acquired by Pine Labs) surfaces `pinelabsgroup.turbohire.co`, so a naive render tags
Setu as `turbohire:pinelabsgroup` — a real board, but the parent's, not Setu's. Flag any hit
whose tenant doesn't relate to the company name/domain; it's usually a parent/partner/template
reference. GCCs and subsidiaries legitimately resolve to the parent's ATS.

**"In-house" is a real, correct outcome — not a tool failure.** Some companies run a custom or
email-only careers flow with no public ATS at all: Zerodha (no homepage careers link), Cashfree
("email us your resume"), Juspay (empty in-house portal), Open, M2P (custom static site on S3 at
`careers.m2pfintech.com`). No rendering trick fingerprints them because there's nothing to find;
the right answer is "no public ATS."

## Faster-than-Playwright headless for SPA careers pages (2026-06-18)

**Context.** The embed/SPA fingerprinter (`scripts/resolve/fingerprint.py`) catches ATS boards baked
into a company's careers HTML or its same-origin JS bundles, but misses boards a single-page
app loads at runtime via XHR (PhonePe, Juspay, slice, Open, Setu in the fintech seed). Those
need a real browser that executes JS. Question: what's faster than Playwright?

**Finding — go CDP-direct, skip the WebDriver/Playwright layer.** The fastest modern Python
headless libraries (pydoll, nodriver, zendriver) drive the *system* Chrome straight over the
Chrome DevTools Protocol (CDP) on its WebSocket debug port. They beat Playwright/Selenium not
because the browser renders faster — it's the same Chrome — but because they remove two layers
of overhead: the WebDriver intermediary process (chromedriver) and Playwright's control-plane
shim. CDP also lets you batch commands in one message, cutting round-trips. Per-page navigation
speed is comparable across all of them; the wins are startup time, install footprint, and
control-plane overhead.

**The contenders.**
- **pydoll** — async, CDP-native, ~2 MB, uses the Chrome you already have (Playwright bundles
  ~500 MB of browsers). Built-in network-event capture (`get_network_logs()`) and Cloudflare
  handling. Stable 1.0 (Feb 2026). Billed as the cleanest python-first Playwright replacement.
- **nodriver** — successor to undetected-chromedriver; async, direct CDP, fastest and
  stealthiest in anti-bot benchmarks. Best when a page sits behind a hard bot wall.
- **zendriver** — community-maintained fork of nodriver; tops several Cloudflare-bypass
  benchmarks.
- **Playwright** — still the safe default for cross-browser (Firefox/WebKit) automation;
  heavier, a known automation signature, but the most batteries-included.

**Decision for HeadStart: pydoll**, with nodriver as the fallback. Reasons: it drives the
existing Windows Chrome (no half-GB download), tiny install, async CDP with native network
capture, and handles Cloudflare. Public careers pages don't need heavy stealth; if a fintech
wall blocks pydoll, switch that one host to nodriver.

**The real lever isn't the library — it's the technique.** For "which ATS does this SPA use,"
don't render-and-reparse the DOM. Enable network capture, navigate, and grep the *request URLs*
for ATS hosts (`boards-api.greenhouse.io`, `api.lever.co`, `*.darwinbox.in`, …): you catch the
ATS the instant the SPA calls it, before the DOM settles. Then amortize browser startup by
reusing one browser across all companies, open a tab per company, **early-exit and close the
tab on the first ATS hit**, and run tabs concurrently. Library choice saves milliseconds;
network-capture + browser-reuse + early-exit save seconds per company.

**Not headless, for contrast.** TLS-impersonation HTTP clients (curl_cffi, rnet) are far faster
than any browser but don't execute JS, so they only help once you already know the runtime API
endpoint to replay — useless for *discovering* an unknown SPA's ATS. They're a step-2
optimization (replay the captured endpoint), not a discovery tool.

Sources: [pydoll](https://github.com/autoscrape-labs/pydoll),
[nodriver](https://pypi.org/project/nodriver/),
[Playwright→CDP rationale (browser-use)](https://browser-use.com/posts/playwright-to-cdp),
[anti-detect benchmark 2026](https://ianlpaterson.com/blog/anti-detect-browser-benchmark-patchright-nodriver-curl-cffi/),
[pydoll vs playwright (ByteTunnels)](https://bytetunnels.com/posts/pydoll-vs-playwright-lightweight-python-browser-control/),
[Python headless browsers 2026 (Scrape.do)](https://scrape.do/blog/python-headless-browser/).
