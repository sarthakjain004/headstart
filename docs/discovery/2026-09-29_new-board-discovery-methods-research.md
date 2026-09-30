# New Board-discovery methods for ATSes with no enumeration oracle (research, 2026-09-29)

_Researched and measured on 2026-09-29. Primary sources only: vendor docs and bundles, dataset owners'
own documentation, the datasets themselves, and live responses. Every number carries its sample size,
and anything traced only through a search summary is marked **unverified**. Raw captures and the probe
scripts are under `experiment/new-discovery-methods-2026-09-29/` (gitignored, per the repo's experiment
rule); the running log is `LIVE_research.md` there, and each of the five surface families has its own
`RESULT_{family}.md`. No ledger was written, nothing was committed, and nothing under `scripts/` was added._

## The answer in one screen

Twenty-one ATSes with no DNS, certificate or CNAME oracle still leak their Board lists through **five
kinds of surface nobody here had read**. Measured against the committed ledgers (2026-09-29):

1. **The vendor's own cross-tenant listing, on hosts nobody had mined,** returned the largest, cleanest
   gaps: SmartRecruiters' job-search page calls a company-name lookup (59.8% unheld of 11,255
   identifiers, 72 of 72 sampled unheld live), JazzHR's app host publishes five Google sitemaps (36.0%
   unheld of 7,540 tenants), and Pinpoint's app host publishes a sitemap index (15.1% unheld of 1,042).
2. **Common Crawl's host-level web graph** lists hosts that were only ever *linked to*, which the CDX index
   our miners read cannot show. One release names about 5,300 unheld tenant hosts across ten ATSes
   (BambooHR 1,284, Teamtailor 1,699, Personio 722, JazzHR 705, Breezy 327, Recruitee 243, ...), 39 of 43
   probed live.
3. **The CrUX origin list beyond its top million** (18.3M origins) names unheld tenants of which 35% to 50%
   are not in the graph, and it exposes a Teamtailor regional pod (`{slug}.na.teamtailor.com`) that the
   ledger holds 6 hosts of and the graph holds 261.
4. **Public rosters kept by others** (a Hugging Face dataset, two MIT GitHub repos) carry unheld Boards on the
   *small* ledgers: Gem 13.6% unheld, Pinpoint and Recruitee dozens each, and the SmartRecruiters roster
   that reproduces item 1 as one file.
5. **A national job-board API** (Sweden's Platsbanken) names Teamtailor Boards at 16.1% unheld.

**What does not work is popularity-biased sampling.** Aggregators, social feeds, GitHub new-grad lists,
Wikipedia/Wikidata links, structured-data dumps and most public job datasets measured 0% to 2.2% unheld
overall (Mastodon 0 of 8 Boards, our own description store 0.7%, Hacker News 1.5%, Simplify 1.3%,
Wikimedia 2.2%, Web Data Commons 0 of 583), because they see the Boards everyone already sees. The
exceptions are rosters of *small* ledgers (Gem, Rippling, Ashby 4% to 14% in one dataset) and one national
board. The measurements that enumerate a namespace, by the vendor's own index or by infrastructure, saw 2%
to 60% unheld (SmartRecruiters 59.8%, JazzHR feed 36.0%, Teamtailor in the graph 29.9%, Pinpoint 15.1%,
BambooHR 10.2%, Trakstar 2.0%). That gradient is the finding: **enumerate the namespace, do not sample the
traffic.**

Adding the measured unheld counts (overlaps between surfaces are not removed, so read it as an order of
magnitude) gives roughly **14,000 new live Boards** against 133,242 live ledger rows on these ATSes
(+10.5%), dominated by SmartRecruiters (about 6,400) and JazzHR (about 2,800), whose tails are
mostly non-tech. Only SmartRecruiters (5 of 20 Boards with a tech posting, 25%) and JazzHR (a title-slug
regex, 8.4% of unheld tenants) have a measured tech share; the tech gate runs after the scrape, so every landed Board still costs
a scrape.

## Scope, method, and what "unheld" means

ATSes: BambooHR, Breezy, ClearCompany/HRM Direct, Freshteam, iCIMS, JazzHR, Personio, SenseHQ, Teamtailor,
Trakstar, Pinpoint, Recruitee, Workable, Ashby, Lever, Greenhouse, Gem, Join, Rippling, SmartRecruiters,
Jobvite. Companies worldwide.

- **Unheld** means no ledger row of any status (live, dead, unknown) shares the candidate's `board_key`,
  read through each scraper's own `slug_from`/`board_key` (CLAUDE.md landing rules), by
  `experiment/new-discovery-methods-2026-09-29/tools/heldness.py`. `wayback_feeder.ATS_HOSTS` lacks
  JazzHR (`applytojob.com`), Jobvite and Join and drops a dotted Teamtailor label (`{slug}.na`), so the
  agents wrapped it (`ext_classify.py`/`classify_ext.py`) through the same `company_from_row` path.
- **Live** means `check_liveness.PROBES[ats](tenant, url)` answered `live`, called directly, no ledger
  writes. `unknown` with no job count means the request failed and is counted **inconclusive**, never dead.
- **Network.** The owner's link was slow for most of the session (the OS resolver took 15 to 20 s per new
  label and several vendor hosts timed out). Every probe was sequential, at most 1 request per second per
  host, about 1,900 local requests in all across five sub-agents and the coordinating agent's own probes.
  A timeout is inconclusive throughout, and the places it touched a number are listed in section 6.
- **Replication.** "Mine" below means the coordinating agent's own probes (`own-probes/`). I re-ran the
  headline measurement of each top method independently of the sub-agent that found it (marked
  "replicated"). Every replicate matched or was close to the original.

## 1. Ranked methods

Ranked by expected new live Boards, discounted for evidence quality, cost and risk. The arithmetic column
reads `unheld measured x live share`; the live shares are from small samples (stated) and carry wide
intervals. Overlaps between rows are in section 2.

| # | Method | ATSes | Primary source | Measured evidence (date, sample) | Expected new live Boards (arithmetic) | Verification | Cost / politeness | Risk / ToS |
|---|---|---|---|---|---|---|---|---|
| 1 | **SmartRecruiters roster and `company-lookup` walk** | SmartRecruiters | `jobs.smartrecruiters.com/sr-jobs/company-lookup?q=` (called by the vendor's own job-search SPA bundle); MIT roster `amikai/openings-mcp` `internal/provider/smartrecruiters/companies.yaml` | Roster 11,258 identifiers, 4,519 held, **6,736 unheld (59.8%)**. Lookup: agent 1,367 distinct / 780 unheld (57.1%, 39 requests); mine 975 / 585 (60.0%, 10 requests, replicated). Roster covers 96.3% of 668 rows from 40 random 4-gram lookups (mine). Live 40/40 + 12/12 + 20/20 | 6,736 x 0.95 (72/72 live, lower 95% bound) = **about 6,400** measured, not extrapolated. Tech: 5 of 20 Boards (25%, Wilson 11-47%) = about 1,600 (750-3,100) | `cl.PROBES["smartrecruiters"]`; drop `SRTest*`; land the case-sensitive identifier in ledger spelling | One GET, 772 KB; or a prefix walk of a few thousand requests at 1/s | Roster MIT. Lookup is an unofficial SPA endpoint, no robots.txt on that host, vendor terms ambiguous (section 2.1). Owner's call |
| 2 | **Common Crawl host-graph vertices** (hosts linked to but never fetched) | BambooHR, Teamtailor, Personio, JazzHR, Breezy, Recruitee, Pinpoint, ClearCompany, Freshteam, Trakstar (iCIMS hyphenated and SenseHQ: mostly dead; path-style ATSes: not measurable) | `data.commoncrawl.org/projects/hyperlinkgraph/cc-main-2026-jul-aug-sep/host/` (48 vertices shards, sorted by reversed host); `commoncrawl.org/web-graphs` | One release, 263 MB read over 14 scans: 5,328 unheld distinct hosts on ten ATSes (table in 2.2). Live 39/43 (91%, 32/43 with jobs). Replicated: trakstar 506/9, freshteam 810/24, teamtailor 5,420+261 `.na` / 1,441+256; 12/13 live | 5,328 x 0.91 = **about 4,850** (95% low about 4,150); hiring 5,328 x 0.74 = about 3,950. Per ATS in 2.2 | `cl.PROBES[ats]` per host; `.na` hosts land as tenant `slug.na` | One streaming pass of all shards is 1.77 GiB (2-3 minutes at 5-7 MB/s); 14 prefix scans took 2-31 s each. No key | Common Crawl ToU (2024-03-07) allows access, bars harvesting PII (hostnames are not PII); CI stage, not a laptop job |
| 3 | **JazzHR app-host Google feeds** | JazzHR | `app.jazz.co/robots.txt` names `Sitemap: http://app.jazz.co/feeds/google/xml/{0..4}` | 2026-09-29: 114,182 URLs, 7,540 tenants, **2,715 unheld (36.0%)**; live 18/20; 10/10 held-but-dead probe dead | Union with host graph: 3,112 unheld. 3,112 x 0.90 = **about 2,800** (2,200-3,000); tech-looking about 8% of tenants = about 260 | `cl.PROBES["jazzhr"]`; "Inactive Career Page" zombies are about 10% of the unheld side | 5 GETs, about 4.1 MB each; robots.txt invites it | Documented 2026-09-07 (`docs/jazzhr/...surface-investigation.md` section 10), never landed. Small non-tech tail |
| 4 | **CrUX origins beyond the top million** | Teamtailor, JazzHR, Breezy, BambooHR, Personio, Recruitee, Pinpoint | `github.com/crissyfield/crux-dumps` (18.29M origins, 202608), Google CrUX methodology page | 5M and 10M buckets whole plus 43% of the 50M bucket: 1,263 Teamtailor Boards / 369 unheld; 35-50% of unheld not in the graph; live 23/24 | Full-bucket unheld estimate about 604 Teamtailor, 244 JazzHR, 136 Breezy, 90 BambooHR, 87 Personio, 68 Recruitee; x 0.92 x 0.4-0.5 not in graph = **about 500-600 additional** | `cl.PROBES[ats]` | 27 + 21 + 45 MB xz files, one GET each | Google CrUX datasets are CC BY 4.0 (fetched 2026-09-29). Origin needs to be publicly discoverable and "sufficiently popular" |
| 5 | **Hugging Face `edwarddgao/open-apply-jobs`** (daily ATS crawl, MIT) | Gem, Ashby, Rippling (Greenhouse, Lever, SmartRecruiters saturated) | `huggingface.co/datasets/edwarddgao/open-apply-jobs` | 2026-09-29: gem 906 / **123 unheld (13.6%)**, ashby 3,858 / 165 (4.3%), rippling 1,453 / 73 (5.0%); replicated gem and rippling exactly; live 16/19 (agent) and 9/10 (mine) | 123 x 5/7 + 165 x 8/8 + 73 x 5/6 = **about 314** (188-349) | `cl.PROBES`; Ashby names with spaces need `%20` (27 of the 165) | Column-pruned read, about 70 s and a few MB in all | MIT, not gated. It is a crawled slug list, but not subsumed on small ledgers |
| 6 | **Pinpoint app-host sitemap index** | Pinpoint | `app.pinpointhq.com/robots.txt` (`Allow: /integrations/google/sitemap_index.xml`) | 2026-09-29: 1,042 tenants, 157 unheld (15.1%), replicated exactly; live 19/20 | 157 x 19/20 = **about 149** (120-156). With graph: union 276 unheld | `cl.PROBES["pinpoint"]` | One GET, 8.6 KB on the wire | robots.txt allows exactly this path. The 2026-09-23 doc checked only `www.` |
| 7 | **`colophon-group/jobseek` `boards.csv`** | Pinpoint, Recruitee (and a few more) | `github.com/colophon-group/jobseek` `apps/crawler/data/boards.csv` | 2026-09-29: 7,885 boards; 129 unheld on target ATSes (pinpoint 74 of 99, recruitee 23 of 92); live 19/20 pinpoint, 7/10 recruitee | 74 x 0.95 + 23 x 0.7 = **about 85**, largely overlapping rows 2 and 6 | `cl.PROBES` | One 2 MB GET, changes daily | MIT |
| 8 | **Sweden JobTech JobSearch** | Teamtailor | `jobsearch.api.jobtechdev.se/search` (43,131 active ads) | 2026-09-29: 886 ads, 56 `*.teamtailor.com` slugs, 9 unheld (16.1%), 8/9 live | Floor about 36 (Chao1 x 8/9); plausible 100-400. About 70% of Teamtailor ads are on vanity hosts and unmeasured | `cl.PROBES["teamtailor"]` | About 430 requests of about 1 MB, single-day windows (offset stops at 2,000) | Terms: "free for anyone to use". Keyless answer observed; the news item says a general published key exists (unread page) |
| 9 | Sourcegraph anonymous stream API | Teamtailor 22, Workable 11, Breezy 8, Recruitee 5, Rippling 4 | `sourcegraph.com/.api/search/stream` | 2026-09-29: 36 queries, 7,786 distinct URLs excluding two roster repos; live teamtailor 7/7, breezy 5/6, pinpoint 19/20 | About 100 real unheld, about 75 live outside Pinpoint | `cl.PROBES` | 36 requests, 3 s apart | robots.txt disallows only `/search?q=*`; AUP silent on API automation. **Owner decision** |
| 10 | OpenINTEL anonymous S3 (CrUX/Umbrella/Tranco CNAME chains, apex TXT) | Teamtailor vanity fronts | `object.openintel.nl`, bucket `openintel-public` | 2026-09-29: 11 Teamtailor fronts in one third of one 211 MB day-file; slug absent from the record; derived slug right 4/11 | About 33 fronts per global CrUX day-file x 0.36 x 3/4 unheld = about 9 | Page read or slug guess, then probe | 7.6 MB column-pruned read | CC BY-NC-SA 4.0 (non-commercial): owner decision |
| 11 | Hacker News "Who is hiring" (Algolia) | Ashby, Breezy, Gem, Rippling | `hn.algolia.com/api/v1` | 2026-09-29: last 12 threads, 476 Boards, 7 real unheld (1.5%), 4 hiring | About 4 per 12 requests, monthly | `cl.PROBES` | 1 request per thread | Algolia terms unread |

The per-ATS view, best measured unheld count per ATS (sources overlap where noted, so these are not sums):

| ATS | Measured unheld | Sources (overlap) | Live share sampled |
|---|---|---|---|
| SmartRecruiters | 6,736 | roster = lookup (96.3% coverage) | 72/72 |
| JazzHR | 3,112 | feed 2,715 + graph 705 (308 shared) | 18/20 feed, 3/4 graph |
| Teamtailor | 1,697 + about 130 more | graph 1,441 plain + 256 `.na`; CrUX adds about 129 in the partial read; JobTech 9 and Sourcegraph 22 overlap unknown | 25/27 |
| BambooHR | 1,284 (1,517 over two releases) | graph; CrUX adds about 30 | 9/9 |
| Personio | 722 | graph; CrUX adds about 15 | 5/7 graph, 2/2 CrUX |
| Breezy | 327 | graph; CrUX adds about 34 | 6/6 |
| Pinpoint | 276 | sitemap 157 + graph 164 (45 shared); jobseek 74 and Sourcegraph 75 overlap unknown | 19/20 + 4/4 |
| Recruitee | 243 + 23 | graph; jobseek 23; Sourcegraph 5 | 6/6 graph, 7/10 jobseek |
| ClearCompany | 149 | graph | 3/3 |
| Gem / Rippling / Ashby | 123 / 73 / 165 | HF open-apply | 4/5 (mine), 5/5 (mine), 8/8 (agent) |
| Freshteam / Trakstar | 25 (62 over three releases) / 10 (40 over four) | graph | 1/2, 2/2; old-release-only Trakstar 5/10 |
| Workable | 28 in the older `{slug}.workable.com` namespace | graph (mine); the aggregator API is unverifiable from this IP | not measurable (429) |
| Greenhouse / Lever | about 0 (26 / 10 in the HF snapshot) | saturated | not sampled |
| iCIMS | 511 hyphenated in graph | graph | 2/6, and 41 of 86 hrjobs-held are opt-out `Disallow: /` |
| Jobvite, Join, SenseHQ | nothing usable | Join tenant sitemap is Cloudflare-walled; SenseHQ 144 unheld in the graph but 0/12 live (mine) and 1/10 via CrUX | n/a |

## 2. Per-method detail

### 2.1 SmartRecruiters: the vendor's own company lookup, and a public roster of it

**What it is.** `https://jobs.smartrecruiters.com/` is SmartRecruiters' own "SmartRecruiters Job Search"
Angular app (bundle `sr-jobs-client-app`). Its 6 KB `main.<hash>.js` shows the calls it makes:
`sr-jobs/search` (`limit`, `keyword`, `company`), `sr-jobs/company-lookup?q=` and
`sr-jobs/company-name-lookup?q=` (vendor-owned-a, 2026-09-29, saved decoded in
`vendor-owned-a/artifacts/2026-09-29_sr_main.decoded.js`). The official developer docs list only
`/postings` and `/postings/{id}` as public (`developers.smartrecruiters.com/docs/endpoints`), which is why
this went unseen; `git grep` finds no `company-lookup` or `sr-jobs` in `scripts/`, `src/`, `docs/` or
`.claude/`.

**Probe (mine, replicated, 2026-09-29):**

```
curl -s -m 45 --compressed 'https://jobs.smartrecruiters.com/sr-jobs/company-lookup?q=ab'
# 200, {"results":[{"identifier":"AbesGarden","name":"Abe's Garden"},{"identifier":"EducantaAB",...}]}
# capped at 100 rows; typo-tolerant word-prefix search; identifier = the case-sensitive Board slug
python3 experiment/new-discovery-methods-2026-09-29/own-probes/sr/sr_lookup_replicate.py ba bo ca co di el mo pa ro st
# 10 requests, 1.2 s apart: 100,100,100,100,100,98,100,100,100,100 rows -> 975 distinct identifiers
PYTHONPATH=$PWD/src .venv/bin/python experiment/new-discovery-methods-2026-09-29/tools/heldness.py \
  --ats smartrecruiters experiment/new-discovery-methods-2026-09-29/own-probes/sr/tenants.txt
# smartrecruiters 975 distinct, 390 held, 585 unheld = 60.0%
```

vendor-owned-a's independent draw: 39 requests, three slices (hand-picked, 20 random 3-grams, 12 random
2-grams) gave 1,367 distinct Boards, 587 held, 780 unheld (57.1%); the slices agreed within 3 points.
Every held row was `live` in the ledger.

**The roster.** `amikai/openings-mcp` (MIT, 92 stars, pushed 2026-09-22) keeps
`internal/provider/smartrecruiters/companies.yaml`; its commit message says it was built by "public
company-lookup prefix enumeration and Posting API verification" (2026-07-17). I fetched it
(`curl https://raw.githubusercontent.com/amikai/openings-mcp/HEAD/internal/provider/smartrecruiters/companies.yaml`,
772,265 bytes): 11,258 identifiers. Datasets-agent: 4,519 held, **6,736 unheld (59.8%)**. **Coverage
check (mine):** 40 random 4-gram lookups (drawn from ledger slug substrings, 4 of 40 hit the 100 cap)
returned 668 rows, of which **643 (96.3%) are in the roster**; the 36 uncapped prefixes returned 268 rows, 259
(96.6%) in the roster. So the roster is close to the lookup's whole reachable population, and one GET
replaces the prefix walk. The 975-identifier lookup sample I drew earlier is 97.5% in it (951 of 975).

**Live verification.** Random unheld identifiers through `cl.PROBES["smartrecruiters"]`:
vendor-owned-a 40/40 live (jobs 1-25); mine 12/12 live (jobs 1,1,1,1,1,1,3,3,4,5,6,1, seed 424242);
datasets-agent 20/20 live from the roster (mean 2.25 jobs, median 1) and 10/10 from its numbered-id
collisions file (`Abbott5`, `GoldmanSachs2`, ...). One of vendor-owned-a's 40 is SmartRecruiters' own test
client (`SRTestPersonalPlan...`), so a landing pass drops `SRTest*`.

**Tech share.** 20 unheld Boards, titles via the SPA's own `sr-jobs/search?company=`: 69 postings, 16
tech (23%), 5 of 20 Boards with at least one tech posting. Much of the tail is limousine services, movers
and staffing agencies. For 4 of the 20 the SPA's search showed 0 postings where the ledger prober counted
1, 2, 25, 25; the cause is unexplained.

**Arithmetic.** 6,736 unheld x 0.95 (72 of 72 live; the 95% lower bound by the rule of three is about
0.96) = about **6,400** live Boards, a measured count and not an extrapolation. At 25% with a tech
posting: about 1,600 Boards (Wilson 11% to 47% on n=20: 750 to 3,100). At 2.25 jobs a Board: about
15,000 postings, about 3,500 of them tech at 23%. The ledger holds 5,848 live SmartRecruiters rows with
at least one job, so the roster more than doubles the hiring side.

**Risk and terms.** `jobs.smartrecruiters.com/robots.txt` answers 404, so there is no crawl rule. I
fetched SmartRecruiters' legal pages on 2026-09-29. The Candidate Terms of Use
(`smartrecruiters.com/legal/terms-of-use/`) list among prohibited uses: "Harvest, collect, gather or
assemble information or data regarding other users without their consent; Use automatic means to access
content or data from other users". That governs candidate accounts; the lookup returns company names and
ids, not user data, but the clause is why this is the owner's call. The Customer terms
(`.../legal/terms-and-conditions/`) bar "automated agents or scripts ... to strip or mine data from the
SmartRecruiters Applications" for Customers, which HeadStart is not. I found no separate website-visitor
terms (`/legal/website-terms-of-use/` is 404). The endpoint is unofficial and can change. The **roster
route avoids the question for the first landing** (an MIT-licensed public file), and the Posting API the
scraper already uses is documented and public.

**Rejected sibling.** `sr-jobs/search?limit=100&keyword=software%20engineer` returns
`totalFound: 48730` but ignores `offset`, `page` and `from` (the same 98 posting ids came back at six
offsets), and the first page's 41 companies were 40 held (97.6%).

**Reproduce the landing input.** `curl -sS <roster URL> | grep company_identifier`, then
`heldness.py --ats smartrecruiters`, then `cl.PROBES["smartrecruiters"]`. Candidate list with the 40
verified marked: `vendor-owned-a/artifacts/2026-09-29_sr_lookup_UNHELD_candidates.tsv`.

### 2.2 Common Crawl host-level web graph

**Why it is new.** `cc_miner` and the Wayback feeders read URLs that a crawler *fetched*. The host graph
lists every hostname that appears as a link target, fetched or not. Common Crawl's own index page for the
latest release says 183.5 million of its 245,776,589 host nodes (74.65%) are dangling, which is exactly
the population a CDX query cannot return. `https://commoncrawl.org/web-graphs` states the host graphs use
"only hostnames with a valid IANA TLD"; `https://index.commoncrawl.org/graphinfo.json` lists 54 releases
(latest `cc-main-2026-jul-aug-sep`, 2.73 billion arcs).

**The property that makes it cheap.** The 48 vertices shards
(`.../host/cc-main-2026-jul-aug-sep-host-vertices.paths.gz`, `id<TAB>reversed host`, 18 to 66 MB gzip
each, 1.77 GiB in all) are **sorted by reversed host**, so `com.bamboohr.*` sits in one shard. Gzip is not
seekable, but a range read from byte 0 streamed through `zlib` reaches the prefix and stops at the first
row past it. The traffic-dns agent wrote `2026-09-29_cc_hostgraph_prefix_scan.py SHARD PREFIX OUT CAP`
(copied and re-run by me):

```
python3 -u 2026-09-29_cc_hostgraph_prefix_scan.py 27 com.trakstar.   rep_trakstar.txt   8000000    # 4.5 MB, 4 s, 546 rows
python3 -u 2026-09-29_cc_hostgraph_prefix_scan.py 14 com.freshteam.  rep_freshteam.txt  16000000   # 13.5 MB, 2 s, 814 rows
python3 -u 2026-09-29_cc_hostgraph_prefix_scan.py 25 com.teamtailor. rep_teamtailor.txt 30000000   # 29.8 MB, 3 s, 5,705 rows
```

**Measured (agent, one release, 2026-09-29; distinct `board_key`s after dropping vendor labels):**

| ATS | prefix, shard | MB read | distinct hosts | held | unheld | unheld % |
|---|---|---|---|---|---|---|
| bamboohr | `com.bamboohr.`, 08 | 16.1 | 12,584 | 11,300 | **1,284** | 10.2 |
| teamtailor | `com.teamtailor.`, 25 | 29.8 | 5,682 | 3,983 | **1,699** | 29.9 |
| jazzhr | `com.applytojob.`, 07 | 22.3 | 5,747 | 5,042 | **705** | 12.3 |
| personio | `com.personio.` 22 + `de.personio.` 31 | 43.6 | 6,946 | 6,224 | **722** | 10.4 |
| breezy | `hr.breezy.`, 34 | 21.0 | 4,712 | 4,385 | **327** | 6.9 |
| recruitee | `com.recruitee.`, 23 | 30.0 | 3,633 | 3,390 | **243** | 6.7 |
| pinpoint | `com.pinpointhq.`, 22 | 33.6 | 967 | 803 | **164** | 17.0 |
| clearcompany | `com.hrmdirect.`, 16 | 16.1 | 1,283 | 1,134 | **149** | 11.6 |
| freshteam | `com.freshteam.`, 14 | 13.5 | 811 | 786 | **25** | 3.1 |
| trakstar | `com.trakstar.`, 27 | 4.5 | 507 | 497 | **10** | 2.0 |
| icims | `com.icims.`, 16 | 16.1 | 7,746 | 6,434 | 1,312 (801 single-word vendor hosts; 511 hyphenated) | 16.9 |

**Replicated (mine, same release, my own heldness tool):** trakstar 506 distinct / 9 unheld (agent
507/10); freshteam 810 / 24 (agent 811/25); teamtailor 5,420 plain hosts / 3,979 held / **1,441 unheld
(26.6%)** plus 261 `{slug}.na.teamtailor.com` hosts of which 5 are held (the ledger has 6 `.na` rows) =
1,697 (agent 1,699). Workable's older `{slug}.workable.com` namespace (mine, shard 29, 15.7 MB): 1,308
distinct, 1,280 held, 28 unheld (2.1%), so nothing there.

**Live verification.** Agent: 39 of 43 live (bamboohr 6/6, teamtailor 8/8, recruitee 4/4, personio 5/7,
breezy 3/3, jazzhr 3/4, pinpoint 4/4, clearcompany 3/3, freshteam 1/2, trakstar 2/2), 32 of 43 with at least
one job, counts varying inside every ATS; iCIMS 2/6. **Mine:** 13 random unheld Teamtailor hosts (8 plain,
5 `.na`), 12 live and 1 dead (`acquisition.na`), 9 with at least one job (plain: 0,2,1,1,0,5,5,3; `.na`:
10,5,0,dead,4).

**Arithmetic.** Non-iCIMS unheld = 1,284 + 1,699 + 705 + 722 + 327 + 243 + 164 + 149 + 25 + 10 = **5,328**;
x 0.91 (39/43) = about **4,850**, 95% Wilson lower bound on 39/43 about 0.78 gives about 4,150; x 0.74
(32/43 hiring) = about 3,950. iCIMS is left out: 511 hyphenated x 2/6 is about 170, and 41 of 86 held
hrjobs Boards are the vendor's own `Disallow: /` opt-out (`probe_icims.py` documents it).

**Overlap with other surfaces (mine, local, from the agents' files).** JazzHR: graph unheld 705, feed
unheld 2,715, **308 shared**, union 3,112. Pinpoint: graph 164, sitemap 157, **45 shared**, union 276.
CrUX: 240 of 369 unheld Teamtailor hosts are also in the graph (65%).

**More releases add more.** The agent's bamboohr union of the Sep 2026 and Jan-Mar 2026 releases: 1,284 ->
1,517 unheld (+18%). Mine, on small prefixes: Trakstar unheld across four releases (Sep 2026, Mar-May 2026,
Dec-Feb 2025/26, Sep-Nov 2025) 9, 12, 12, 19, **union 40** of 896 distinct; Freshteam 24 -> **62** over three
releases (1,291 distinct). But the part that exists only in older releases is stale: 10 random of Trakstar's
31 old-release-only unheld probed **5 live, 5 dead** (jobs 2,1,3,12,3). Five of my ten release reads
returned 0 rows (my bisect landed on the wrong shard or the 30 MB cap ended before the prefix); those are
inconclusive, not empty. I re-ran them with a retrying bisect and a 45 MB cap and they read 0 rows again;
the 2025 releases have **16 shards** (heads: `aaa.0`, `com.276-c`, `jp.fudejichirashi`, `ua.pp...` in
`cc-main-2025-jun-jul-aug`), not 48, so a shard is about three times larger and `com.trakstar.` sits deeper
than my cap. A CI stage must stream whole shards for older releases. There are 54 releases back to 2017;
only three or four were tried.

**Why the ledger missed them (mine, measured).** For 20 random unheld and 20 random held Teamtailor hosts
from the graph I asked the Wayback CDX for any capture of the host
(`web.archive.org/cdx/search/cdx?url={host}.teamtailor.com&matchType=host&limit=1`, one request each, retry
once): **unheld 0 of 20 have a capture** (20 conclusive), **held 17 of 17 conclusive have one** (3 timeouts,
inconclusive). The hosts the graph adds are hosts no archive ever fetched, which is why the archive-driven
miners (`cc_miner`, `wayback_pages`, `urlscan_miner`) could not find them. (The same check against
Common Crawl's own CDX was inconclusive, see section 6.)

**Teamtailor `.na`.** `{slug}.na.teamtailor.com` is a regional pod whose hosts the CDX sweeps cannot
extract, because `wayback_feeder.extract` drops any subdomain label containing a dot. The ledger holds
6 `.na` rows (all landed 2026-09-28, tenant spelled `slug.na`, url `https://slug.na.teamtailor.com`); the
graph holds 261, 256 of them unheld; 7 of 8 probed live (agent 3/3: `humanqualityhqp.na` 78 jobs; mine
4/5). It is a namespace hole in our own extractor, not only a data gap, and the same guard may hide other
regional pods.

**Cost.** Agent: 263 MB in total for 14 scans, 2 to 31 s each, no key, no account, no rate-limit answer.
A CI stage should read all 48 shards once (1.77 GiB, 2 to 3 minutes at the measured 5 to 7 MB/s) and
match every host in `ATS_HOSTS` plus the JazzHR, Jobvite and Join hosts in one pass instead of ten prefix
scans.

**SenseHQ (mine, shard 23; the agent stopped at its 30 MB cap):** 206 distinct hosts, 62 held, 144 unheld
(69.9%), but 0 of 12 probed live (11 dead, 1 unknown), so it is not a source: the hosts exist and the
career sites are gone. **Not measurable:** every path-style ATS (the graph node is the shared vendor host).
**Not measured:** the tech share of the live hosts.

**Terms.** Common Crawl Terms of Use (`commoncrawl.org/terms-of-use`, last updated 2024-03-07,
fetched 2026-09-29): the Service is offered "for anyone to access"; prohibited conduct includes harvesting
personal information for separate use; it "strongly recommends" legal advice before commercial use. A
company's careers hostname is not personal data.

### 2.3 JazzHR app-host Google feeds

`app.jazz.co/robots.txt` (fetched 2026-09-29): `User-agent: *` is `Disallow: /cb`, and it names
`Sitemap: http://app.jazz.co/feeds/google/xml/0` through `/4`; feed 5 is an empty `<urlset>`.
`docs/jazzhr/2026-09-07_surface-investigation.md` section 10 documented this as a side finding
("114,963 job URLs across 7,602 tenants ... 3,933 tenants the pool does not have"). Nobody landed it.

vendor-owned-b, 2026-09-29: 5 GETs (about 4.1 MB each; the server ignored a `Range` header), 114,182 URLs,
7,540 distinct `{tenant}.applytojob.com` labels; 4,825 held (4,640 live rows, 185 dead) and **2,715 unheld
(36.0%)**. 20 random unheld: **18 live** (1 to 37 jobs, 142 in all), 2 dead. Ten random held-but-dead
tenants the feed still lists: 10 of 10 dead, and one feed URL returns 200 with "JazzHR - Inactive Career
Page", so zombies are about 10% of the unheld side. Unheld tenants carry a median 2 jobs (held: 6); a
title-slug regex finds tech-looking titles at 229 of the 2,715 (8.4%; the slug is decorative on JazzHR, so
this is rough).

Arithmetic: union with the graph 3,112 unheld x 0.90 (18/20 feed; graph 3/4) = about **2,800** (Wilson on
18/20: 70-97% gives 2,180 to 3,020); tech-looking about 260. The feeds are a daily snapshot, so a weekly
re-read finds new tenants. Land through `check_liveness.py`, not straight from the feed.

### 2.4 CrUX origin list beyond the top million

Google's own methodology page (`developer.chrome.com/docs/crux/methodology`, fetched 2026-09-29): an origin is
in the dataset only if it is "publicly discoverable" (the search engines' indexability criteria, so a
`noindex` career page is out) and "sufficiently popular" ("a minimum number of visitors across all of its
pages", exact number not disclosed); "CrUX datasets by Google are licensed under a Creative Commons
Attribution 4.0 International License". `zakird/crux-top-lists` keeps only the top 1M of about 15M
origins; `github.com/crissyfield/crux-dumps` (exported from BigQuery per its README) keeps every bucket:
202608 has 5M (4,000,000 origins, 21.0 MB xz), 10M (5,000,000, 27.1 MB) and 50M (8,294,881, 45.5 MB),
18.29M in total.

Traffic-dns agent, 2026-09-29: top-1M alone carries 1,202 ATS-host origins but only about 37 target-ATS
Boards (4 unheld) and is rejected. The 5M and 10M buckets whole plus the first 30 MB of the 50M bucket
(3,567,155 origins, 43%; origins inside a bucket are listed randomly, so the prefix is a random sample)
gave, per bucket top1M / 5M / 10M / 50M-prefix, distinct Boards (unheld): teamtailor 6 (3) / 175 (70) /
474 (119) / 608 (177), jazzhr 2 (0) / 159 (2) / 441 (12) / 1,035 (99), bamboohr 1 (0) / 198 (15) / 210 (19) /
243 (24), breezy 9 (1) / 147 (3) / 333 (27) / 452 (45); recruitee 35, personio 43, pinpoint 24 unheld overall.
Teamtailor: 1,263 distinct, 369 unheld, 240 of which are in the graph (65%). 24 random unheld probed: **23
live, 18 with jobs**.

Arithmetic (agent): full-bucket unheld scaled x 8,294,881 / 3,567,155 = teamtailor about 604, jazzhr 244,
breezy 136, bamboohr 90, personio 87, recruitee 68, pinpoint 41; x 0.92 live x 0.4-0.5 not already in the
graph = **about 500-600 additional** live Boards, one month. Not measured: month-to-month union (67
monthly dumps exist; about 100 MB a month).

**Vanity fronts.** `careers.`/`jobs.`/`apply.`/`join.`/`career.`/`recruit.` origins number 2,770 (top-1M),
9,855 (5M), 8,757 (10M) and 11,604 (50M prefix), about 48,000 across the list. A 40-name DNS-over-HTTPS
sample: 24 have a CNAME; 4 point at Teamtailor (`ext.teamtailor.com` or `{id}.ext.teamtailor.com`), 1 at
Pinpoint, 2 SuccessFactors, 2 Phenom, 1 Zoho. The CNAME does not carry the slug, and neither the vanity page
nor its `jobs.json` feed names `{slug}.teamtailor.com`; a slug guessed from the domain label was right for
4 of 11 Teamtailor domains, 3 of them unheld and live, and 0 of 1 for Pinpoint. So the 48k sweep is a
lead, not a method, until a slug source exists.

SenseHQ via CrUX: 56 unheld but 1 of 10 live, not a source. Umbrella: 44 ATS-host FQDNs, 2 real tenants,
both held. Majestic (first 30 MB, 377,138 rows): 0 tenant hosts.

### 2.5 Hugging Face `edwarddgao/open-apply-jobs`

Public dataset, `license: mit`, `gated: False` (`huggingface.co/api/datasets/edwarddgao/open-apply-jobs`,
2026-09-29). Card: "A daily-refreshed open dataset of active job postings sourced directly from public ATS APIs
(Greenhouse, Lever, Ashby, Rippling, Gem, Workday, SmartRecruiters)"; columns include `source`,
`source_slug`, `apply_url`; 462 files, 40.7 GB, one full snapshot per `date=` folder (gem, rippling,
smartrecruiters, workday only from 2026-09-26).

Probe (a column-pruned read of `source_slug`, `HF_HUB_DISABLE_XET=1`, 2 to 23 s each):

```
PYTHONPATH=$PWD/src .venv/bin/python - <<'EOF'   # mine, replicated
fs.ls('datasets/edwarddgao/open-apply-jobs/data/date=2026-09-29/source=gem'); pq.ParquetFile(f, filesystem=fs).read(columns=['source_slug'])
EOF
heldness.py --ats gem gem_slugs.txt      # gem 906 distinct, 783 held, 123 unheld (13.6%)   [agent identical]
heldness.py --ats rippling rippling_slugs.txt   # rippling 1,453, 1,380 held, 73 unheld (5.0%) [agent identical]
```

Agent's full table (2026-09-29): gem 906/123 (13.6%), ashby 3,858/165 (4.3%), rippling 1,453/73 (5.0%),
smartrecruiters 1,043/10, lever 1,674/10, greenhouse 4,598/26. Live: agent 16 of 19 conclusive (gem 5/7,
ashby 6/6 + 2 spaced names live by direct API, rippling 5/6); **mine 9 of 10** (gem 4/5 with 2,2,4,5 jobs,
one hit is `onetrust-llc-sandbox`; rippling 5/5 with 18,1,9,10,6 jobs). Arithmetic: 123 x 5/7 + 165 x 8/8 +
73 x 5/6 = about **314** (188 to 349), roughly +9% gem, +3% ashby, +2% rippling on their live rows. Older
snapshots add nothing (5 of 269 and 4 of 257 unheld among slugs that left the latest snapshot). 27 of the 165
unheld Ashby slugs contain a space (`Sine Engineering` 33 jobs, `Applied Compute` 13 jobs live per
`api.ashbyhq.com`), and the ledger prober errors on them; whether our Ashby scraper handles a spaced slug is
unchecked. `docs/discovery/technique-ranking.md` and `techniques.md` record public crawled slug lists as 0
new on Greenhouse/Lever/Ashby/Workday; this one is not subsumed on Gem and Rippling.

### 2.6 Pinpoint app-host sitemap index

`app.pinpointhq.com/robots.txt` (mine, 2026-09-29): `User-agent: *`, `Disallow: /`,
`Allow: /integrations/google/sitemap_index.xml`, `Allow: /rails/active_storage/representations/`,
`Sitemap: https://app.pinpointhq.com/integrations/google/sitemap_index.xml`. The vendor explicitly allows
that one path. One GET (200, 8,638 bytes on the wire, 90 KB decoded) is a `<sitemapindex>` of
`https://{tenant}.pinpointhq.com/sitemap.xml`. **Replicated exactly:** 1,042 distinct tenants, 885 held, 157
unheld (15.1%). The index covers 821 of the ledger's 867 live rows (95%), so it is close to the whole roster.
vendor-owned-b probed 20 random unheld: 19 live (16 with jobs, 3 live with 0), 1 dead; 10 of 10 held-but-dead
tenants it lists probe dead (stale entries). Arithmetic: 157 x 19/20 = about **149** (Wilson 120 to 156),
about +14% on 867 live rows. `docs/pinpoint/2026-09-23_postings-api-measurement.md` line 244 says "Vendor
roster: none found" but checked `www.pinpointhq.com`, not the `app.` host.

The pattern worth stating: two of the twelve group-B vendors publish a cross-tenant sitemap on their **app**
host and name it in that host's robots.txt (`app.jazz.co`, `app.pinpointhq.com`). The Teamtailor, Recruitee,
Breezy, Personio, ClearCompany and Trakstar app hosts named none. I checked 14 more app-ish hosts for the
other nine ATSes (`app.greenhouse.io`, `hire.lever.co`, `app.ashbyhq.com`, `app.gem.com`, `app.rippling.com`,
`app.jobvite.com`, `app.workable.com`, `www.workable.com`, `my/app.smartrecruiters.com`, `www.bamboohr.com`,
`app.bamboohr.com`, `app.freshteam.com`, `app.join.com`; 2026-09-29, one GET each): the only cross-tenant
sitemap is Join's known `join.com/companies/sitemap-jobs-index.xml`, which answers Cloudflare 403 (not
bypassed) and belongs to a disabled scraper.

### 2.7 `colophon-group/jobseek` `boards.csv`

MIT (checked via the GitHub API, 2026-09-29), 200 stars, last push 2026-09-29 07:37Z, agent-driven PRs
numbered past #10,000, so it grows daily. `apps/crawler/data/boards.csv` (2.06 MB, 7,885 boards with
`board_url`). Datasets-agent: on our ATSes, pinpoint 99 distinct / **74 unheld**, recruitee 92 / **23**,
greenhouse 2,540 / 8, ashby 915 / 6, smartrecruiters 120 / 5, icims 139 / 4, teamtailor 53 / 3, trakstar 9 / 2,
rippling 10 / 1, sensehq 2 / 1, personio 56 / 1, workable 75 / 1; breezy, bamboohr, lever, gem 0. Live:
pinpoint 19/20 (the same names as the Sourcegraph sample), recruitee 7/10, greenhouse 3/4, trakstar 2/2,
sensehq `turner` 23 jobs. It overlaps rows 2 and 6 for Pinpoint and Recruitee; treat it as a weekly
cross-check, not an independent source. The same agent's other rosters are saturated:
`kalil0321/ats-scrapers` `ats-companies/*.csv` 63,452 URLs, 0 unheld outside 142 iCIMS zero-job hosts;
`amikai` rosters for ashby 0/1,419, bamboohr 0/1,000, greenhouse 0/2,598, lever 0/2,114, rippling 0/1,169,
workable 0/3,918.

### 2.8 Sweden JobTech JobSearch (Teamtailor)

`https://jobsearch.api.jobtechdev.se/search?limit=100&offset=N` answered keyless on 2026-09-29 (the GitLab
getting-started doc still says a key is needed). Terms: "Our open data, open APIs and open source code is
free for anyone to use" (`arbetsformedlingen.se/other-languages/english-engelska/about-the-website/apis-and-open-data`).
`limit=0` reports 43,131 ads. Key status (**unverified**): a WebSearch summary of JobTech's news item "We No
Longer Use API Keys" (`jobtechdev.se/en/news/vi-slutar-anvaenda-api-nycklar`, unread: the apex `jobtechdev.se`
did not resolve from 1.1.1.1, 8.8.8.8 or WebFetch on 2026-09-29) says unique keys were dropped in March 2022
but JobSearch and JobStream still take one general published key (`developer`). Our probe sent none and got
200; a build should send the documented general key. In 500 sampled ads 74.6% carry an apply URL, 19.2% have
a `reference` starting `teamtailor-{job}-{promotion}`, 6.6% apply on a `*.teamtailor.com` host. Pooled 886 ads: 56 distinct
slugs, **9 unheld (16.1%)**; verified: 8 live (4, 14, 25, 159, 8, 19, 5, 3 jobs), 1 dead.

Arithmetic and its weakness: ads on a Teamtailor host 6.6% x 43,131 = about 2,850, but the sample is 2% of
ads and 36 of 56 slugs were seen once, so the slug count is unbounded above; Chao1 on the unheld class
gives 41 x 8/9 = about **36** as the only defensible floor, and 100 to 400 as a plausible range. About 70% of
Teamtailor ads sit on vanity hosts (CNAME to `ext.teamtailor.com`) whose slug the page does not expose, so
those Boards are unmeasured. A full pass is 43,131 / 100 = 432 requests of about 1 MB, sliced by
single-day `published-after/before` windows because `offset` stops at 2,000. Sweden's Historical Ads zips
(797 MB for 2025) have `application_details.url` null on 6,057 of 6,057 ads and are useless here. Two default-sort
pages timed out (inconclusive, not counted).

### 2.9 Sourcegraph anonymous stream API (owner decision)

`https://sourcegraph.com/.api/search/stream?v=V3&q=<regex> -repo:^github\.com/kalil0321/ats-scrapers$
-repo:^github\.com/amikai/openings-mcp$ count:1000 patternType:regexp` answered 200 with no login
(datasets-agent, 2026-09-29, 36 queries 3 s apart). `robots.txt` disallows only `/search?q=*`; the AUP says
nothing about API automation (WebFetch of `/terms` and `/terms/aup` negative), so it is permitted by silence,
not by licence. 7,786 distinct URLs from 40 to 120 repos per pattern; after dropping placeholders (`acme`,
`example`, `globex`) the real unheld are pinpoint 75, teamtailor 22, workable 11, breezy 8, recruitee 5,
smartrecruiters 5, rippling 4, greenhouse 3. Verified: pinpoint 19/20 live, teamtailor 7/7, breezy 5/6,
rippling 1/2. grep.app (429 with a bot checkpoint on robots.txt and `/`), PublicWWW (browser interstitial),
searchcode ("no cross-repository or global search") and Software Heritage (origin search only) were skipped.

### 2.10 OpenINTEL anonymous S3

`openintel.nl/data/forward-dns/top-lists/` (fetched by the traffic-dns agent): once a day for Cisco Umbrella,
Tranco, Cloudflare Radar, Majestic and Google CrUX, with SOA, NS, A, AAAA, MX, TXT, "full CNAME expansions",
and "some lists use fully qualified domain names directly (Cisco Umbrella, Majestic, Google CrUX)". Anonymous
S3 (`object.openintel.nl`, bucket `openintel-public`), licence **CC BY-NC-SA 4.0**. The CrUX `global` day
2026-09-28 is one 211 MB parquet (10.05M rows); a column-pruned read of one row group (7.6 MB, 4.5 s) found 79
names on or behind an ATS host, 68 on the host itself and **11 vanity fronts, all Teamtailor**; apex TXT
(14 MB): `mg-spf.greenhouse.io` on 35 names, a Teamtailor verification TXT on 18. The tenant slug is not in the
record. Email-record attestation was measured separately and rejected (section 3). Use: a Teamtailor
vanity-front list only, after the licence question.

### 2.11 Hacker News "Who is hiring"

`hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring` gave 166 threads and 114,560
comments; one request per thread (2026-09-29). The last 12 threads (3,822 top-level comments): 476 distinct
Boards, 11 unheld of which 4 are Workday link-text artifacts, so 7 real; 4 verified hiring (breezy
`valkyrie-aero` 3 jobs, rippling `mondrio` 7, gem `tessellate` 1, ashby `kiloforge` 1). Older threads add
about nothing. Worth one monthly request; Algolia's terms are unread.

## 3. Checked and rejected, with the measurement that killed each

| Surface | Killing measurement (date 2026-09-29 unless stated) |
|---|---|
| Any aggregator or social feed (We Work Remotely, Surely Remote) | Prior study 2026-09-28: 86 of 88 resolved employers held (98%); WWR never carries the employer apply URL (`docs/remote-job-boards/2026-09-28_wwr-and-surelyremote-full-corpus.md`) |
| Mastodon `#hiring` timeline (mine) | 40 posts, 216 KB, 27.5 s: 8 distinct Boards on ATS hosts, **0 unheld** |
| Descriptions on held Boards (mine, local) | 842 MB store scanned: 370 ATS URLs (excluding Taleo self-links) = 279 Boards, 2 unheld (0.7%) |
| Greenhouse job id to Board oracle (mine) | `job-boards.greenhouse.io/embed/job_app?token=8122755` 302s to `job_board?for=&error=true`; `boards.greenhouse.io/jobs/8122755` 404 |
| Vendor sitemaps/robots/root of Greenhouse, Lever, Ashby, Gem, Rippling, Jobvite job hosts | 404, SPA shell (Ashby returns the app shell at `/sitemap.xml` with HTTP 200), or redirect to marketing; Ashby robots disallows `/api/` |
| Vendor customer and case-study pages (12 vendors) | 9 to 52 pages per vendor; 0 of 6 story pages link the Board; Recruitee end to end: 26 story pages gave 4 new live Boards after I re-ran the 5 labels the agent's DNS shim had left inconclusive (`solutions4delivery` 10 jobs, `vancranenbroek` 61; the other 3 dead), so about 15% of story pages, or roughly 2 to 8 Boards per vendor (11 to 52 pages each) |
| iCIMS `hrjobs.icims.com` (real iCIMS-owned aggregator, 1,685 jobs) | 110 jobs, 94 distinct Boards, 8 unheld = 6 demo sandboxes + 2 real (2.1%), 1 live; 41 of 86 held are `Disallow: /` opt-outs |
| Vendor aggregator subdomains by DNS guessing | Wildcard DNS on 11 of 15 vendor domains; the control label resolves |
| Teamtailor partner XML feed | Vendor doc: "a unique url to the XML feed" per partner; no public roster |
| Workable aggregator `jobs.workable.com/api/v1/jobs` (169,447 jobs) | Public, but no `apply.workable.com` slug anywhere (list, detail, company, `.md`, page HTML; also noted 2026-09-08 in `wayback-host-coverage-audit.md`); slug guesses matched the ledger for 202 of 296 companies (68%), 94 unresolved; verification blocked: `apply.workable.com` answers 429 `Retry-After: 24255` for this IP. **Inconclusive** |
| Join tenant sitemap | `join.com/companies/sitemap-jobs-index.xml` Cloudflare 403, not bypassed; customers sitemap 95 URLs; scraper disabled |
| Gem GraphQL | `__schema: null`, introspection off |
| Wikipedia and Wikidata external links | 8 wikis, about 25 host patterns, about 55 requests: 90 distinct Boards, 2 real unheld (2.2%); WDQS: no external-identifier property points at an ATS host |
| Web Data Commons JobPosting (2024-12) | 26 MiB prefix of `part_4.gz`: 583 iCIMS tenants, **583 held**; 9 of 9 ATS Boards named on company pages held; same Common Crawl population; full read is about 2.7 GB. `JobPosting_domain_stats.csv` (13 MB, 63,320 domains) is a seed list only |
| GitHub new-grad and internship lists | Simplify + vanshb03 + speedyapply + others: 2,927 Boards, 56 unheld (1.9%); Simplify Summer2026 non-Workday about 1.3% |
| Other roster repos | `kalil0321/ats-scrapers` 0 unheld outside iCIMS zero-job hosts (63,452 URLs); `amikai` non-SmartRecruiters rosters 0 to 3 of 1,000+ |
| YC seed-and-probe | 30 companies, 147 probes: 7 live, 7 held, 0 unheld |
| `security.txt` `Hiring:` | 0 of 38 answering domains (RFC 9116 section 2.5.6 scopes it to security jobs) |
| Registries as seeds | GLEIF, OpenCorporates, Product Hunt, EU-Startups fields unread (**unverified**); Companies House snapshot page does not state a website field |
| Tech-lookup sites | BuiltWith bot check, Wappalyzer no response, StackShare 403, Enlyft a form; TheirStack lists about 27.8k Greenhouse companies but export needs a sign-up and its terms URL is 404 |
| HF datasets other than open-apply | `makenomistakesllc/tech-job-postings-9-ats` 3 of 237 unheld; `NextGig-Rocks/global-job-postings-multi-ats` has no application URLs by its own card |
| National feeds | Germany BA: 0 of 40 details carry `externeUrl`, 741,270 ads at one detail request each, terms reserve content rights; Norway NAV: 0 of 30 on Teamtailor, ads on Webcruiter/Easycruit/Jobbnorge; Czech MPSV: 0 ATS URLs in 1,263 records; Canada Job Bank: no employer or URL column; Singapore MCF: 1 ATS Board in 300 ads, dead; Sweden historical ads: apply URL null on 6,057 of 6,057 |
| Free aggregator APIs | Remotive, RemoteOK, Himalayas, Jobicy, Working Nomads, Arbeitnow, The Muse: every `url`/`applicationLink` is the aggregator's own page (Himalayas docs say so); Arbeitnow descriptions held 2 Boards, both held |
| Email attestation (SPF/DKIM/TXT/CNAME) | 51 DoH TXT queries: Greenhouse SPF recall 4/20, Teamtailor root TXT 3/11, controls 0/20; no slug in the record; Greenhouse domain-derived slug right 19/20 but all 19 already held; Teamtailor 4/11 |
| CrUX top-1M alone | 1,202 ATS-host origins, about 37 target Boards, 4 unheld |
| Cisco Umbrella top-1M | 44 ATS FQDNs, 2 real tenants, both held |
| Majestic Million (first 30 MB, 377,138 rows) | 0 tenant hosts |
| Tranco and Cloudflare Radar | Pay-level domains by their own docs (Tranco's separate "with subdomains" file was not fetched) |
| HTTP Archive | BigQuery only ("a Google account"); anonymous `storage.googleapis.com/httparchive/` returns 403 |
| Passive-DNS services | OTX 429 "Anonymous access to this endpoint is limited. Please authenticate."; Columbus no DNS; ThreatMiner 522; crt.sh 502; Robtex per IP 37 names, 4 unheld; InternetDB 1 name |
| Host graph for Workable `{slug}.workable.com` (mine) | 1,308 distinct, 28 unheld (2.1%) |
| SenseHQ (CrUX and host graph) | CrUX: 56 unheld, 1 of 10 live. Host graph (mine, shard 23, 46.4 MB read): 206 distinct, 144 unheld (69.9%), **0 of 12 live** (11 dead, 1 unknown). The hosts exist but the career sites are dead |

## 4. What the repo already tried

| Method | Prior work | Difference |
|---|---|---|
| JazzHR Google feeds | `docs/jazzhr/2026-09-07_surface-investigation.md` section 10 found it (3,933 tenants missing then) | Documented, never built into a miner or landed; 2,715 unheld remain |
| Common Crawl | `cc_miner.py`, `docs/discovery/common-crawl-mining.md`: CDX index of captured URLs, 119 crawls | The host graph adds linked-but-never-fetched hosts (74.65% of nodes are dangling) |
| Wayback CDX, urlscan, embed query strings | `docs/discovery/overview.md`, `techniques.md` | Fetched-page archives; same blind spot as the CDX index |
| "Public crawled slug lists: 0 new" | `techniques.md`: 8,333 and 633 Greenhouse/Lever/Ashby/Workday slugs, 100% held | Measured on the mature four; not true for SmartRecruiters (59.8%) or Gem (13.6%) |
| GitHub lists | Workday only, 95% held (`2026-09-26_new-board-discovery-techniques.md` section 6) | Non-Workday now 98.1% held (1.9% unheld, n=2,927), consistent |
| Rosters of others | `amikai/openings-mcp` ADP roster (431, 7 unique) in `docs/adp_recruiting/2026-09-24_myjobs-measurement.md`; `kalil0321/ats-scrapers` seed lists at scraper-build time | The SmartRecruiters roster in the same repo was not read |
| Aggregators | WWR and Surely Remote, 98% held (`docs/remote-job-boards/`); Getro/Consider 76-89% held (`2026-09-28_career-fronts-...`); Indeed sweep | Every other aggregator and public dataset measured agrees on the mature ATSes (0% to 2.2% unheld) |
| Reverse-IP | HackerTarget, 500 names per IP (`2026-09-26_new-board-discovery-techniques.md`) | Robtex per IP: 37 names, 4 unheld |
| DNS/CNAME sieves, TLS SANs, CT | Same doc, `shared-cert-tenant-rosters.md`, `overview.md` | Not retried; CrUX/OpenINTEL find vanity fronts only |
| Sitemap oracles | Used on a held tenant (CLAUDE.md) | The vendor's own app-host sitemap (Pinpoint) is new |
| `jobs.workable.com` | Wayback audit 2026-09-08: "keyed by UUID with no account slug" | Confirmed; also lists 169,447 jobs |

## 5. Recommended build order

1. **Land the one-GET sources through `ats-gap-search`, no new code.** In this order: the `amikai`
   SmartRecruiters roster (about 6,400 live), the JazzHR feeds (about 2,800 with the graph's 705), the
   Pinpoint sitemap index (about 150), HF `open-apply-jobs` gem/ashby/rippling (about 314), and the jobseek
   cross-check. Run each through `check_liveness.py` per the landing rules: SmartRecruiters in its
   case-sensitive spelling and without `SRTest*`; Pinpoint drops `*-old` names and the one dead
   (`restrata`); JazzHR skips "Inactive Career Page" zombies; Ashby needs the spaced-name question settled.
   Decide the SmartRecruiters terms question before building any prefix walk; the roster does not need it.
2. **A Common Crawl host-graph stage on CI.** One streaming pass over all 48 vertices shards
   (1.77 GiB, minutes), match every host in `ATS_HOSTS` plus the JazzHR, Jobvite and Join hosts, dedupe by
   `slug_from`/`board_key`, verify, land. About 4,850 live from one release; add the last three or four
   releases and expect stale hosts (old-only live share 5/10). This also needs the extractor fix below.
3. **Fix the extractor's blind spots** in `wayback_feeder.py`: add JazzHR (`applytojob.com`), Jobvite and
   Join to `ATS_HOSTS` so heldness and the CDX sweeps see them, and stop dropping a dotted label for
   Teamtailor's `.na` pod (and check other regional pods with the same guard). That alone re-opens the 256
   unheld `.na` hosts to the existing miners.
4. **CrUX tail buckets from `crux-dumps`, monthly on CI** (about 500-600 more live beyond the graph), and
   keep the `careers.`/`jobs.` origin list (about 48,000) as the seed for a vanity-front pass once a slug
   source exists.
5. **A weekly re-read of the cheap listings**: JazzHR feeds, Pinpoint index, the roster, jobseek, HF
   snapshot, and one HN thread a month.
6. **JobTech Sweden for Teamtailor** (about 430 requests, single-day windows) if the Teamtailor gap is still
   open after step 2.
7. **Owner decisions, not builds:** SmartRecruiters lookup terms (row 1); Sourcegraph's silent AUP (row 9);
   OpenINTEL's non-commercial licence (row 10); Workable's aggregator, which needs a name-to-slug resolver
   and a probe from an IP with budget (the Actions runner), since this machine is behind a 6.7 hour 429.

## 6. What could not be verified, and session notes

- **Slow link.** The OS resolver took 15 to 20 s per new label most of the session and `check_liveness`
  probes read `unknown None` for live hosts until a public-resolver shim or a longer `cl.TIMEOUT` was used.
  Those first runs are kept in the artifacts (`*FIRST_RUN*`, `*SECOND_RUN*`) and count as inconclusive.
  The link recovered for stretches late in the session; I used them to re-run what timeouts had left open
  (the five Recruitee labels, SenseHQ on the graph, the release reads, the licence and terms pages).
  WebFetch failed with `getaddrinfo ENOTFOUND` on this link, so docs were read with small `curl` fetches
  and WebSearch summaries; claims that rest only on a search summary are marked unverified.
- **Common Crawl's own CDX index as a mechanism check** (does a host-graph-only Teamtailor host appear in the
  latest crawls' CDX?): two runs, 30 hosts x 2 crawls and 20 hosts x 3 crawls, and `index.commoncrawl.org`
  timed out on most requests even at full link speed (24 of 30 hosts had a timeout in the first run), so it
  is **inconclusive**. The Wayback CDX check in section 2.2 replaced it and was conclusive.
- **Workable** (the aggregator's 94 unresolved companies, the size of its company universe, any slug
  source): blocked by the per-IP 429 wall, not by the link.
- **Tech share** is measured only for SmartRecruiters (5 of 20 Boards, 16 of 69 postings) and, roughly,
  for JazzHR (a title-slug regex); it is unmeasured for the host-graph and CrUX Boards, and no measurement
  checked that a live host is English.
- **Live shares** are from small samples: per ATS on the host graph n is 2 to 8 (39 of 43 overall), Pinpoint
  19/20, JazzHR 18/20, HF 16/19 and 9/10. Read per-ATS products as an order of magnitude.
- **Not measured:** every path-style ATS on the host graph (Greenhouse, Lever, Ashby, Gem, Rippling, SmartRecruiters, Workable's `apply.`, Jobvite, Join); 50 of 54
  graph releases; the second 57% of the CrUX 50M bucket and month-to-month union; the 48,000-origin vanity
  sweep; the other 238 OpenINTEL country files; `speedyapply` 2027 repos, `hacker-job`, `awesome-career-pages`,
  `remoteintech`, `freehire`; JobStream (about 300 MB); EURES (its "no screen scraping" clause is from a
  search summary and the legal notice page I fetched does not contain it).
- **Terms unread or unverified:** SmartRecruiters site-visitor terms (none found), HN Algolia, MyCareersFuture
  (JavaScript app), Umbrella licence, WDC licence, GLEIF/OpenCorporates/Product Hunt/EU-Startups fields.
- **Five of ten** of my release-union reads returned 0 rows, and a re-run with retries and a 45 MB cap did
  too: the 2025 releases have 16 larger shards and the prefix sits past the cap. Inconclusive, not empty.
- **Licence of the `crissyfield/crux-dumps` repo itself:** the GitHub API returns `license: null` (none
  declared, 2026-09-29); the underlying CrUX data is CC BY 4.0 per Google.
- **Sub-agent request budget:** the datasets agent made about 550 local requests (guideline 300), all
  sequential; the others 250 to 330 each. My own probes were about 250 requests plus one 30 MB stream per
  host-graph scan.
- **A stray empty file, `LIVE_x`, sits in the repo root** (created 20:33, untracked, not mine that I know of);
  I did not delete it.

## Files

Everything is under `experiment/new-discovery-methods-2026-09-29/` (gitignored):
`COMMON_BRIEF.md`, `LIVE_research.md`, `tools/heldness.py`; `vendor-owned-a/`, `vendor-owned-b/`,
`public-job-feeds/`, `datasets-and-knowledge-graphs/`, `traffic-dns-and-email-infra/` (each with
`RESULT_{family}.md`, `LIVE_{family}.md` and `artifacts/`); and `own-probes/` with `sr/`, `pinpoint/`,
`hostgraph/`, `hf_open_apply/`, `app_hosts/`, `terms/` and the local description scan.
