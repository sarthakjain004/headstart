# Trimming the served company set: measurements and design options

*Research date: 2026-09-21. Question asked: "trim the company number down to the ones the user
actually cares about, might care about, or is better off seeing." No code written; this is the
evidence and the option set that a decision should be made from.*

Reproduce every number here with `experiment/company-curation/measure_company_shape.py`
(raw output: `experiment/company-curation/artifacts/2026-09-21_company-shape.txt`).

## Freshness of these numbers

Two sources, two freshness levels, stated because CLAUDE.md forbids passing a stale figure off
as current:

- **`board_priority.csv` — live.** Pulled from HF on 2026-09-21; newest row dated the same day.
- **`data/lancedb` — a local snapshot last pulled 2026-09-15**, holding 459,291 rows. The live
  served table is larger. Every claim drawn from it below is a **shape** claim (concentration,
  identity, nameability, fragmentation) that six days of drift does not move. Do not quote its
  row count as the index size.

## What the measurements say

**The population is enormous and almost entirely long tail.** 40,004 hiring Boards in the live
ledger hold 604,634 tech jobs, but the **median hiring Board has 3**. The top 1,000 Boards carry
55% of all jobs; the remaining 39,000 carry the other 45%. In the served table the same shape
holds: 27,770 Boards, of which **14,957 — 54% — serve three rows or fewer**.

**One ordinary query already spans thousands of companies.** A plain title match for `backend`
returns 6,333 jobs across **2,807 distinct companies**, and the median company contributes
exactly **one** job. `data engineer` spans 3,586 companies. This is the user-facing problem stated
numerically: there is no "company" unit in the experience at all, just an undifferentiated stream
in which nearly every row is a different employer.

**Most companies cannot currently be named.** Only **19.7%** of served rows sit on an ATS whose
scraper resolves a real company name (ADR-0114's `company_name.PATTERNS`: ashby, eightfold,
jobvite, keka, lever, ripplehire, phenom, taleo_enterprise). The other four rows in five display
an ATS slug — `jslhrms`, `EndeavorITSolution`, `squircleitconsultingservicespvtltd`. **A feature
about companies cannot ship on a field that is a slug 80% of the time.**

**One employer is routinely several Boards.** 335 board_key groups collide case-insensitively —
670 Boards, 43,067 served rows (9.4%). The pattern is lopsided and looks like the stale-casing
duplication CLAUDE.md already documents for the *ledgers*, except these reached the *index*:

| | |
| --- | --- |
| `workday:ngc/northrop_grumman_external_site` | 13 rows |
| `workday:ngc/Northrop_Grumman_External_Site` | 1,951 rows |

Sampled small groups share **no native job ids** across the two casings, so this is not duplicated
jobs — it is **split identity**, and the small side looks like rows no current scrape refreshes.
Worth its own check: whether those minority rows are evictable or are being held by ADR-0053 scope
exclusion, which has no drain.

Beyond casing, the same employer appears across ATSes with nothing to join it: `micron` on
`workday:micron/External` and `eightfold:careers.micron.com`; `nvidia` on Workday and Eightfold;
`amazon` on its own single-source scraper and on `trakstar:amazon`. The Phenom entry in CLAUDE.md
already records that **cross-ATS dedup does not exist** — `index_plan.evict_duplicate` groups
*within* a Board — and that a whole ledger was narrowed by hand to work around it.

**Some Boards are not employers.** Measured, not assumed: 23,508 rows (5.1%) sit on Boards that
repeat the same job title **three or more times on average**, topped by
`oracle:eofd.fa.us6.oraclecloud.com` at 68.8× (1,375 rows, 20 distinct titles) and
`lever:bluelightconsulting` at 65.5×. A deliberately crude vocabulary probe on the slug
(`staffing|recruit|consult|manpower|talent|…`) marks 46,726 rows (10.2%) — an **upper bound**,
since it also fires on Lockheed and RTX, whose board hosts contain "recruiting". Named examples
worth eyeballing: `oracle:jpmc-test.fa.oraclecloud.com` (4,222 rows, a test pod — see
`docs/oracle/2026-09-16_are-the-ejwl-and-jpmc-pods-real.md`), `eightfold:nvidia-sandbox2.eightfold.ai`
(2,001 rows against `eightfold:jobs.nvidia.com`'s 2,000 — a sandbox mirroring the real Board), and
`lever:jobgether`, which tops the `frontend` query and is an **aggregator**, not an employer.

**A firmographic join is the expensive option, and the cost is the join, not the data.** Reaching
an employer *domain* from a board_key works for only **13.7%** of rows (the Board is the employer's
own host, e.g. `careers.hcltech.com`). 24.1% are vendor-hosted tenants (`tenant.icims.com`) and
**62.1% are bare labels with no host at all** — Workday, Greenhouse, SmartRecruiters, Ashby, Lever.
So the blocker on "startups under 200 people" or "not IT services" is company→domain resolution,
not the availability of company data. On the data itself: People Data Labs publishes a free
22M-company dataset under **CC BY 4.0** with name, website, LinkedIn URL, industry, size band,
founding year and HQ location, downloadable as CSV/JSON — permissive enough to use, and the
licensing notes on public datasets (kept outside this public repo) already cover how this repo
treats third-party company data.

## Six options

These are separable; several are worth doing, and the order matters more than the menu.

### 1. Corpus hygiene — evict the Boards that are not companies

Test pods, sandboxes mirroring a real Board, HRMS demo tenants, aggregators, and the minority side
of the 335 split identities. Objectively wrong data, not merely uninteresting data. No new data
source, no product decision, no personalization. Also returns scrape and embed budget, which is the
system's binding constraint. Risk: a hand-built exclusion list rots; it wants a rule plus a ledger
column, not a hardcoded set.

### 2. Per-company result capping in the ranked page

Cap N rows per Board in `JobSearch.run` and collapse the rest into one line — "HCLTech · 238 more".
Zero new data, one file, ships immediately, and it is the direct fix for "the same three IT
services firms fill my screen". The measurements say it is also *honest*: since the median company
contributes one job to a query, capping costs almost every company nothing. Limit: it re-orders the
page, it does not tell the user anything about who the company is.

### 3. Employer-type classification — direct employer / staffing and services / aggregator

The single largest quality lever, and the thing every competitor is bad at (HideJobs exists as an
entire product just to hide agencies on LinkedIn, Indeed and five other boards). Build it from
signals already held — title repetition, one title across many locations, description boilerplate
("our client", "C2C", "walk-in"), company-name vocabulary — with an LLM adjudication pass through
the router on the ambiguous middle, stored as one column on the Board ledger. Keep it recall-biased
in the repo's own idiom: **flag and demote, never drop**, because a staffing firm genuinely is the
only route into some roles. Cost: a classifier plus a labelled sample to measure it against, and
the honest version reports precision and recall rather than a coverage percentage.

### 4. Per-account follow and hide lists

Hide is the feature users actually ask for; follow is the one HeadStart is uniquely positioned to
serve, because it reads Boards directly and stamps `first_seen` — "tell me the hour Stripe opens a
backend role" is something LinkedIn does badly and this index does well. The account infrastructure
already exists (`Store`, `SavedSet`, `Subscription`, alerts by email and Telegram), so this is
mostly a new record type and a filter clause. Hard dependency: it is unusable while 80% of
companies render as slugs, and a follow list keyed to a board_key silently misses the same employer
on its other ATS.

### 5. Firmographic enrichment — size, industry, stage

This is the "might care about" tier: "Series A–C, under 200 people, not consulting". The data is
available and permissively licensed (PDL, CC BY 4.0). The work is the join, and the measurement
above says 62% of rows offer no domain to join on. Recoverable signal exists — the careers-page
fingerprint files carry `name,domain,ats,slug` for ~2,000 Boards, Common Crawl tenant mining kept
the referring URL, and a Board's own page or job descriptions often state the company site — but
each is its own pass. Prototype the join on one ATS and report a measured hit rate **before**
committing to the feature.

### 6. Company affinity from behaviour

"You saved three jobs at infrastructure startups; here are twelve companies like them." Cheap in
principle because the vectors already exist and `role_centroids` shows the shape (a centroid per
company over its jobs' embeddings). But it is worthless before 3 and 4 — recommending companies
that render as `squircleitconsultingservicespvtltd` helps nobody.

## Recommendation

**Do 1 and 2 first**, in that order or together. Both are cheap, need no new data, carry no product
risk, and improve every query for every user on the day they land. Option 1 additionally pays the
system back in scrape and embed minutes.

**Then 3**, because it is the largest quality lever and because it is what makes 4 and 6 mean
anything.

**Treat company identity as the real prerequisite for anything user-facing.** Options 4, 5 and 6
all presuppose a Company that can be named and that survives being on two ATSes; the glossary has
`Company` as "the employer listed on an ATS, behind a Board", which is a Board-scoped definition,
not an identity that spans Boards. Widening `company_name.PATTERNS` to more ATSes is the cheap half;
a cross-Board Company identity is an ADR-sized decision, and — on the Phenom evidence — one this
repo has already had to work around once.

## Open questions for the product owner

1. Is the goal fewer companies **scraped** (a cost problem) or fewer companies **shown** (a
   relevance problem)? The measurements support both readings, and they lead to different work.
2. Of the three tiers named in the request — *cares about* (explicit follow), *might care about*
   (recommendation and firmographics), *better for him* (a quality gate on employer type) — which
   is the one that actually hurts today?
3. Is a company ever to be **removed from the index**, or only ever demoted and filterable? The
   repo's stated value is recall over precision; option 1 argues some Boards are wrong rather than
   uninteresting, which is the one case that breaks the tie.

## Decisions taken (2026-09-21)

**In scope, all three:** the quality gate (*better for him*), per-account follow/hide (*cares
about*), and trimming what gets **scraped** (cost). **Out of scope:** the *might care about* tier —
firmographics and affinity-based company discovery. That drops option 5's company→domain join, the
single most expensive item measured here, and option 6 with it.

**Removal policy: demote and filter only.** Nothing leaves the served index. Option 1 above is
therefore amended: aggregators get a **type label and a demotion**, not an eviction.

*Amended 2026-09-23:* test pods and sandboxes are **evicted**, not demoted. They are non-production
tenants, not employers, and serve postings that are closed on production or synthetic, so the
ADR-0034 non-prod rule marks them dead — widened to Oracle's `-test`/`-dev{N}` pods and numbered
tokens like `nvidia-sandbox2` by
[ADR-0034's 2026-09-23 amendment](../adr/0034-nonprod-boards-dead-by-convention.md#amendment-2026-09-23-testdev-earn-a-slot-on-oracle-only).

### The conflict this creates, and where it has to be settled

Trimming the **scrape** while never **evicting** from the index is not two independent choices. A
Board that stops being scraped keeps every row it has: ADR-0083's grace period only evicts an id
that two *consecutive scrapes of that Board* missed, so a Board nothing reads again is never
evidence of anything and its rows are served indefinitely. That is exactly ADR-0053's
scope-exclusion accretion — no bound, no drain, measured at 105 dead rows on `careers.qualcomm.com`
with the oldest 22 days — except deliberately, and at the scale of the 39,000 Boards holding three
jobs each.

So one of the two needs a rule:

- **Age becomes a demotion signal.** A row whose Board has not been confirmed in N days ranks
  lower and says so, which honours "never evict" while stopping stale rows from surfacing. This is
  the option that fits the stated policy.
- **Or "never evict" takes one narrow exception** for rows on a Board deliberately retired from the
  scrape slice — an explicit retirement, not a missed scrape.

Doing neither means the cost saving is paid for in silently stale results, which is the failure
mode this repo already has a scar from.

### The shape this suggests

All three selected tiers consume the **same** per-Board judgement — an employer-type and quality
label on the Board ledger. The scrape planner reads it to deprioritize; Search reads it to demote
and to back a filter; the follow/hide lists read it to seed sensible defaults. One computed thing,
three consumers, rather than three heuristics that disagree. Company identity (naming, and an
identity that survives one employer sitting on two ATSes) remains the prerequisite for the
follow/hide half specifically.
