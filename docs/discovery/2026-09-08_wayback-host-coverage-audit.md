# Is the Wayback feeder sweeping every host our ATSes serve boards from?

**2026-09-08.** Two questions, asked together because they have the same failure mode — a board
that exists, that we could scrape, and that discovery never sees, with nothing in any log to say
so.

1. Does re-fetching CDX pages already marked *done* recover boards? (Yes — measured.)
2. Is `wayback_feeder.ATS_HOSTS` missing hosts entirely? (Yes — **four** real gaps.)

All 20 ATSes audited. The four missing hosts, ranked by measured yield: Trakstar's
`recruiterbox.com` (§7.1, twice the archive of the host we sweep), Workday's `myworkdaysite.com`
(§3, 31+ unknown Boards, needs a new extractor style), Oracle's pod-less `fa.oraclecloud.com`
(§7.2, one live 7,323-job board both miners are blind to), and Greenhouse's ANZ region (§4). Two
further slug shapes are reachable but unreadable by `extract` (§5), and one question is left open
by CDX throttling (§7.5).

Ten ATSes are verified complete, and one plausible-looking gap — the `cc_miner` API hosts — is
**refuted by measurement** (§6) so it is not re-opened.

## 1. The resume markers were hiding boards

`wayback_pages.py` records completed page numbers in `data/wayback-ats/.{ats}_{host}_pages_done`
and skips them on a re-run. That is right for resuming an interrupted sweep and wrong for a
periodic one: **CDX pages are ordered by urlkey, so a new capture is *inserted* into an existing
page rather than appended at the end.** A page finished months ago can hold slugs today.

8,295 pages across the 51 hosts were marked done. Clearing every marker and re-sweeping:

| ats | before | after | new |
|---|---|---|---|
| ashby | 6,442 | 6,567 | **+125** |
| greenhouse | 18,449 | 18,545 | **+96** |
| darwinbox | 578 | 581 | +3 |
| eightfold | 584 | 586 | +2 |
| freshteam | 2,648 | 2,648 | 0 |

Ashby gained 1.9% from 40 pages that were *all* previously complete, and the new slugs are real
companies (`Feegow`, `ResolveAI`, `Sentradel`, `Intelligence-Security-Laboratories`). Greenhouse's
two hosts diverge in a way worth noting: `job-boards.greenhouse.io` gained 77 steadily across 153
pages while `boards.greenhouse.io` gained 15 and then flatlined for 150 — the older host is
frozen, new boards land on the newer one.

**Operational consequence:** the markers should be cleared before any periodic full sweep, not
just when resuming. They remain correct for their original purpose.

## 2. Why the liveness ledger cannot answer the coverage question

The obvious check — take every board URL in `data/validate/liveness/{ats}.csv`, and find hosts no
`ATS_HOSTS` entry matches — returns almost nothing: 25 Greenhouse, 21 Workday, 2 strays out of
~60,000 rows.

That is not reassurance, it is **circularity**. The ledger was populated by these feeders, so it
can only contain hosts they already sweep. Oracle is the worked example: its ledger showed 15 pods
and looked complete, and an independent CDX probe found a 16th (`fa.ap4.oraclecloud.com`) carrying
five real boards and **zero** ledger rows.

Every finding below therefore rests on sources independent of our own discovery: the scrapers' URL
construction, `cc_miner.py`'s separate target lists, **Common Crawl** (`CC-MAIN-2026-34`),
certificate transparency (certspotter), DNS, and live HTTP against the vendors.

## 3. Workday serves from a second domain we do not sweep — the big one

`ATS_HOSTS` carries only `myworkdayjobs.com` (`wayback_feeder.py:329`). Workday runs the same
career sites from a second domain with a **different URL shape**:

```
https://wd{N}.myworkdaysite.com/[{locale}/]recruiting/{tenant}/{site}
```

The tenant is in the **path**, not a subdomain. 20 production pods resolve (`wd1,3,5,10,12,102-105,
107-109,115-117,120,501-504`); CT confirms that grid plus `impl-`/`perf-`/`wcpdev-` non-production
prefixes. The CXS API answers there directly — `POST https://wd5.myworkdaysite.com/wday/cxs/uw/
UWHires/jobs` returns 544 postings.

**`extract()` returns `None` for every real URL of this shape.** The `workday` style
(`wayback_feeder.py:423-438`) takes the first host label as the company and requires it to match
`wd\d+`; here the first label *is* `wd\d+` and the company is two path segments in. Tested against
four real URLs — all `None`. So this is not a missing table entry, it needs a new style.

Sizing, from one Common Crawl snapshot and independent of our ledger: 1,380 distinct URLs → **108
Boards**, 77 already known, **31 not**. Sampling 14 of the 31 against the live CXS API: **12 live,
2,389 jobs** — `gflenv/Careers` 867, `elcorteingles/Ext` 371, `cws/CWS_Jobs` 293, `daher/Daher`
288, `clorox/Clorox` 175, `vxi/Careers` 114, plus six smaller. One 422, one empty board.

Wayback holds **23 CDX pages** of `myworkdaysite.com`, against 11 for `job-boards.eu.greenhouse.io`
— a host that contributed 906 ledger boards, 522 of them live. One CC snapshot found 31 unknown
Boards; a Wayback sweep should find materially more. **That 31 is a floor, not an estimate.**

No scraper change is needed: an extractor can emit the `myworkdayjobs.com` spelling that
`WorkdayScraper._URL_PATTERN` (`src/headstart/scrapers/workday.py:123-125`) already accepts —

```
https://wd5.myworkdaysite.com/recruiting/uw/UWHires
  -> ('uw/UWHires', 'https://uw.wd5.myworkdayjobs.com/UWHires')
```

## 4. Greenhouse has a third region

`ATS_HOSTS` (`wayback_feeder.py:245-251`) covers US and EU. CT shows Greenhouse runs exactly three
production regions — `prod-use1-0`/`prod-usw2-0`, `prod-euc1-0`/`prod-euw1-0`, and
**`prod-apse2-0`/`prod-apse4-0`** — with `anz.greenhouse.io` present.

It is a distinct board set, not an alias: `boards.anz.greenhouse.io/octopusdeploy` 301s
region-preservingly to `job-boards.anz.greenhouse.io/...`, while the equivalent `us` host 301s to
the unprefixed global one; and 12/12 EU-ledger live slugs return **404 on ANZ**.

`extract()` handles both hosts unchanged under the existing `path` style, including the
`embed?for=` route. The scraper needs nothing — `greenhouse.py:22-24` uses the global API, which
serves ANZ boards fine. Common Crawl found 47 ANZ URLs → 6 tenants, **2 unknown and both live**
(`bhindilabspteltd` 5 jobs, `phoenixdx` 4). Small, but the EU precedent is exact: all 906
EU-recorded slugs exist only under the EU spelling.

*Doc drift, minor:* the comment at `wayback_feeder.py:242-244` says `*.us.greenhouse.io` 301/302s
to the unprefixed host. That now holds only for `boards.us.`; `job-boards.us.` serves 200 directly.
The conclusion is unchanged — `us` is the global set, no gain from adding it.

## 5. Two slugs that are reachable but unreadable

Both are on hosts already swept, so they are extractor gaps rather than table gaps.

**Teamtailor `utm_content` backlink.** Every Teamtailor career site emits a "powered by" link,
`https://www.teamtailor.com/?utm_campaign=poweredby&utm_content={slug}.teamtailor.com&…`.
`extract()` returns `None` because the label `www` is in `INFRA` and the slug lives in the query —
structurally the same case as Greenhouse's `embed?for=`, already special-cased at
`wayback_feeder.py:407-415`. Measured: 183 ledger rows carry this shape (174 live), and **53 of
those tenants — 51 live — appear nowhere in `data/wayback-ats/teamtailor.csv`**.

**Workable widget API.** `apply.workable.com/api/v1/widget/accounts/{slug}` returns `None` (`api`
is in `INFRA`). `cc_miner` matches this shape so it does occur, but the same slug almost certainly
also appears as a plain `apply.workable.com/{slug}`. Marginal, and unmeasured.

## 6. Refuted by measurement: the six `cc_miner` API hosts

`cc_miner.py` targets six API hosts `ATS_HOSTS` omits, which looked like an obvious gap since the
slug sits in the path. It is not one, and the point is worth recording so nobody re-opens it.

First, none survives `extract()` — the leading path segment is a version, not a slug:

| cc-only target | `extract(url, host, "path")` |
|---|---|
| `boards-api.greenhouse.io` | `('v1', …)` |
| `boards-api-eu.greenhouse.io` | `('v1', …)` — and the host is **NXDOMAIN** |
| `api.lever.co`, `api.eu.lever.co` | `('v0', …)` |
| `api.ashbyhq.com` | `('posting-api', …)` |
| `api.smartrecruiters.com` | `('v1', …)` |
| `api.rippling.com` | `('platform', …)` |

Second — and this is what settles it — Common Crawl shows they hold **no novel boards at all**.
`boards-api.greenhouse.io` has 2 URLs total (`robots.txt` and one `/v1/boards/xai/jobs`, already
known); its 10 CDX pages are `.well-known/` bot traffic. `api.lever.co` has 5 URLs / 3 slugs, all
three already in the ledger. `api.ashbyhq.com` and `api.rippling.com` have **zero**. So writing an
offset extractor for them would buy nothing. Their absence from `ATS_HOSTS` is correct.

Worth fixing separately: `cc_miner.py:71`'s `boards-api-eu.greenhouse.io` target is a dead host.

## 7. Verified complete

Ruled out by CT records, DNS and live probes rather than assumption:

- **lever** — the two entries match the scraper's two instances (`lever.py:381`); no other public
  board host in CT (`sites.`/`jobsitebuilder.` are marketing), `jobs.lever.eu` NXDOMAIN.
- **ashby** — `jobs.ashbyhq.com` only; apex 301s to marketing; no EU pod.
- **recruitee** — the `recruitee.com` entry is swept `matchType=domain`, covering every
  `{slug}.recruitee.com`; no path form; `jobs.recruitee.com` 302s to `careers.tellent.com`.
- **personio** — CT shows exactly the two listed hosts; `.es` is marketing, the rest NXDOMAIN.
- **workable** — `jobs.workable.com` is the job-seeker aggregator, keyed by UUID with no account
  slug; `careers.` 301s to `apply.`; `brandedboards.` is HubSpot.
- **rippling** — `ats.rippling.com` only; `rippling-ats.com` 302s into recruiter SSO.
- **zoho** — 8 TLDs, established earlier: 19 further candidates probed, only `zohorecruit.ae`
  exists and it archives just `insights.zohorecruit.ae`, marketing rather than a board namespace.

- **darwinbox** — `.in` + `.com` matches the scraper's own `_TLDS = ("in", "com")`
  (`darwinbox.py:49`). `.sa`, `.ae`, `.my`, `.co.id`, `.com.sa` NXDOMAIN; `darwinbox.id` resolves
  but is a parked Hostinger page.
- **freshteam** — `freshteam.com` only, matching `freshteam.py:96`. `myfreshteam.com`,
  `freshteam.in/.eu/.io` all NXDOMAIN.
- **ripplehire** — `ripplehire.com` only, matching `ripplehire.py:67`. `.in/.co/.io` NXDOMAIN.
- **icims** — no regional or legacy host confirmed: `icims.eu` and `icims.net` 301 to
  `www.icims.com`, `icims.co.uk` shares the `icims.eu` wildcard IP with no board. Agrees with the
  481,000-URL census in `docs/icims/2026-09-08_url-formats-and-the-build-decision.md:44-52`, which
  finds `{tenant}.icims.com` is the only board suffix. **One candidate unresolved** — see §7.5.


## 7.1 Trakstar is missing the larger half of its own archive

**The highest-yield finding in the audit.** `ATS_HOSTS` carries only `hire.trakstar.com`
(`wayback_feeder.py:334`). Trakstar Hire *is* Recruiterbox, renamed — and the legacy per-tenant
namespace is still live, still resolving, and still archived.

- Trakstar's own support article states the hosted careers site is `[company].recruiterbox.com`
  and that "you can't change the `.recruiterbox.com` part".
- Live today: `1lattice.recruiterbox.com` 301s to `1lattice.hire.trakstar.com`; likewise
  `1stopasia`, `recruiterbox`. A 1:1 label mapping, which is exactly the slug `trakstar.py:126`
  wants.
- **Archive size: `recruiterbox.com` holds 40 CDX pages against `hire.trakstar.com`'s 20.** The
  legacy domain is the *bigger* half of Trakstar's archive and nothing sweeps it.
- Two alphabetical slices gave 10 distinct tenant labels, of which **6 are in neither the liveness
  ledger nor the candidate pool** — and all 6 answer 200 live on `hire.trakstar.com`
  (`1000megabytes`, `100marks` 1 opening, `10strings` 2 openings, `1boc`, `macaulaygidado`,
  `macfoodservices`).

The first independent signal was in our own code: `trakstar.py:104` still carries
`_FEED_NS = {"job": "https://recruiterbox.com/rss/job/"}`. The scraper remembers the rename; the
host table does not.

**One caveat the fix must handle.** `dedupe_key` (`wayback_feeder.py:484-495`) keys non-`path`
styles on the **URL**, so `https://acme.recruiterbox.com` will not collapse against
`https://acme.hire.trakstar.com`. Zoho's TLDs are regional *pods* and must not collapse; this pair
is an **alias** and must. So adding the host naively double-counts every tenant in
`data/wayback-ats/trakstar.csv`. Either dedupe alias hosts on the label, or sweep it ad-hoc
(`--domain recruiterbox.com --style sub`) and merge on the label. The liveness prober is unaffected
— `check_liveness.py:1525` builds from the tenant label and ignores the url column.

## 7.2 Oracle has a seventeenth shape with no pod at all

All 16 entries added earlier carry a pod segment (`fa.{pod}.oraclecloud.com`). There is a
**pod-less vanity tier** that matches none of them:

`data/validate/liveness/oracle.csv:1090` — `jpmc.fa.oraclecloud.com`, **live, 7,323 jobs**. It is
not a duplicate of a known pod: it CNAMEs to `eino.fa.ocs.oraclecloud.com`, which has zero ledger
rows. The scraper's own endpoint returns a well-formed `recruitingCEJobRequisitions` envelope for
it, and `extract(…, "fa.oraclecloud.com", "host")` yields the right slug unchanged.

The namespace is genuinely tiny — crt.sh for `%.fa.oraclecloud.com` returns 19 names, essentially
`jpmc` plus its dev/test siblings and two Oracle-internal `faaasvanitypod` hosts — but its one
production member is a 7,323-job board. `*.fa.oraclecloud.com` publishes no wildcard, so DNS
enumeration works here as it does for Eightfold.

**`cc_miner` misses it too**, and this is the one place the two miners fail together:
`cc_miner.py:255-260`'s regex `([a-z0-9-]+\.fa\.[a-z0-9-]+\.oraclecloud\.com)` *requires* a pod
segment. So neither discovery path can see this tier.

Worth stating plainly for the future: cc_miner mines Oracle from the apex with a full-host regex
and is therefore **pod-agnostic — it finds pod N+1 for free**. The feeder enumerates, so **every
new Oracle pod is a permanent silent miss until a human adds it**. That is an argument for giving
`extract` an oracle-shaped style rather than growing the list.

And the `fa.` prefix is *not* an assumption: Oracle documents the career site as
`{instance}.fa.{pod}.oraclecloud.com/hcmUI/…`. Oracle's actual vanity-URL feature is not an
`oraclecloud.com` host at all — it is a customer subdomain behind a customer-run reverse proxy, so
those boards are off the vendor namespace entirely, unenumerable by any domain sweep.

## 7.3 Keka has a second domain, but it buys nothing today

`kekahire.com` is real: `*.kekahire.com` has a valid cert, tenants get provisioned pod records
(`10decoders` → `cin01.career.kekahire.com`, not the wildcard), and
`https://10decoders.kekahire.com/careers` 302s to `10decoders.keka.com`. `extract` handles it as
`sub` unchanged. But it is only 3 CDX pages against `keka.com`'s 23, and of 18 tenant labels
sampled, **16 are already known and the 2 new ones are dead** (`adwitiya` → 403,
`alokin` → TenantNotFound). Add it if the table is being touched anyway; it does not justify its
own change. Same alias/`dedupe_key` caveat as Trakstar.

## 7.4 SuccessFactors and Eightfold: the table is right, the tail is structural

Neither has a missing host. Both have a customer-vanity tail no host sweep can reach, now measured:

| provider | on swept hosts | on customer vanity domains |
|---|---|---|
| successfactors | 113 Boards / 13,133 jobs | **2,091 Boards / 250,895 jobs across 2,044 apexes** |
| eightfold | 97 Boards / 75,099 jobs | 5 Boards / 10,252 jobs (12.0%) |

**95% of live SuccessFactors jobs sit on customer domains with no shared namespace.** The prefix
distribution of those live hosts — `careers.` 969, `jobs.` 658, `career.` 51, `karriere.` 37,
`www.` 34, `empleos.` 28 — is exactly what `sf_derive_hosts.py:37-45` already exploits, so the tail
belongs to `mine_successfactors.py` / `sf_derive_hosts.py` / `sf_cname_probe.py`, not to
`ATS_HOSTS`. (`jobs2web.eu` NXDOMAIN; `hr.cloud.sap` as a table host is unusable anyway — the dot
guard makes `extract` return `None`.)

Eightfold's tail is just five customer apexes — micron, nvidia, qualcomm, hsbc, vodafone — which
confirms the comment at `wayback_feeder.py:238-239`. The right instrument already exists and is not
a host sweep: `eightfold_portal_sweep.py` enumerates customers via `app.eightfold.ai`'s `?domain=`
param across the regional portals, and `eightfold.ai` publishes no wildcard DNS, so a resolving
label *is* a provisioned Board.

## 7.5 One question the throttling left open

**`jibeapply.com`** — iCIMS acquired Jibe in 2019, owns the domain, and hosts recruitment-marketing
career sites on it. The apex 301s to `www.icims.com`; crt.sh returns 93 names, all infrastructure
behind a `*.jibeapply.com` wildcard, so CT cannot reveal customer hosts;
`careers.icims.com.jibeapply.com` resolves and answers 400 to a bare request. `extract` would
handle it as `host`. **The CDX probe 429'd on four attempts** and the question is unresolved — it
needs one probe once the endpoint is free. Proviso: `icims.py` is sitemap-only, so a Jibe career
host may not be scrapable even if it archives.

## 7.6 Miner asymmetries worth knowing

The feeder and `cc_miner` are each ahead of the other in places, so neither is a superset:

- `cc_miner` has **no `successfactors` or `freshteam` entry at all** — those live only in the feeder.
- `cc_miner` mines only 4 Zoho TLDs (com/eu/in/ca) against the feeder's 8; the 4 it omits
  (`.com.au`, `.sa`, `.jp`, `.com.cn`) hold **120 live Boards**.
- `mine_zoho.py:39-49` mines 10 domains, two of which (`zohorecruit.uk`, `zohorecruit.sg`) have zero
  ledger rows and nothing archived — harmless, but neither the feeder nor reality supports them.
- Only cc_miner is pod-agnostic on Oracle (§7.2).

## 8. `ATS_HOSTS`'s own docstring is wrong

It claims the table is "derived from the scrapers' own URL construction". For six of the ten ATSes
audited in group A the scraper's *primary* host is absent — greenhouse, lever, ashby,
smartrecruiters and rippling all talk to an `api.`/`boards-api.` host, and Workday cannot reach
`myworkdaysite.com` at all. Given §6, the right fix is to reword the docstring: `ATS_HOSTS` is the
set of hosts that serve **archivable board pages carrying a slug**, which is not the same set as
the hosts the scrapers fetch from.

## 9. Ranked actions

0. **Add `recruiterbox.com` to trakstar** (§7.1) — the legacy domain is *twice* the archive of
   the one we sweep, and 6 of 10 sampled tenants were unknown and live. Needs the alias-dedupe
   decision, not just a table line.
1. **Add `fa.oraclecloud.com`** (§7.2) — one line, `extract` handles it, and it recovers a live
   7,323-job board that both miners are currently blind to.
2. **Add a `myworkdaysite` style and host** (§3) — a floor of 31 unknown Boards, 12 sampled live
   with 2,389 jobs, and 23 CDX pages left entirely unswept. Needs a new extractor; no scraper
   change.
3. **Add the two Greenhouse ANZ hosts** (§4) — two lines, `extract()` already handles them.
4. **Teach `extract()` Teamtailor's `utm_content` slug** (§5) — 53 known tenants, 51 live, that
   the harvest has never seen.
5. **Clear resume markers before every periodic sweep** (§1), or give `wayback_pages.py` a
   `--refresh` flag so this is not a manual step that gets forgotten.
6. **Reword the `ATS_HOSTS` docstring** (§8), add cc_miner's 4 missing Zoho TLDs (§7.6), and drop `cc_miner`'s NXDOMAIN target (§6).

## 9b. The sweep, run to completion — and the concurrency cliff that nearly stopped it

**2026-09-09.** All 20 ATSes swept with every resume marker cleared, zero failed pages, and
**+2,171 new tenants** (141,009 -> 143,153, +1.5%). Oracle +1,130 (38.5%, its 16 pods getting a
full sweep rather than two pages each), workday +495, ashby +125, greenhouse +96.

Getting there needed a correction worth recording, because the intuition is backwards and
`wayback_pages.py`'s own default (`--workers 10`) points the wrong way.

The first attempt ran `--workers 6` and fell into sustained `HTTP 429 ... gave up after 6
attempts`. It was not merely lossy — it was *catastrophically slow*: keka spent **six hours on 23
pages**, and 83 pages were dropped across eightfold, greenhouse and icims. Measured against
`jobs.lever.co`, six pages per setting, after stopping the sweep so the numbers were not
contaminated:

| workers | 6 pages | per page | errors |
|---|---|---|---|
| 1 | 17.8 s | 3.0 s | none |
| 2 | 17.3 s | 2.9 s | none |
| **4** | **8,419 s** | **1,403 s** | 2 timeouts |

**Four workers is ~470x slower than two, and two is no faster than one.** The endpoint is
effectively serial per client: it appears to *queue* excess concurrency rather than reject it, so
every worker above ~2 buys nothing and pushes the whole sweep into backoff. Re-running the
identical remaining work at `--workers 2` finished **3 h 21 m**, against a trajectory that had
keka alone at six hours.

Two operational consequences:

- **Sweep with `--workers 2`.** The default of 10 is not safe for a full sweep, and the failure it
  produces looks like a slow archive rather than self-inflicted throttling.
- **A restart is free and doubles as the retry pass.** `wayback_pages` leaves a failed page out of
  `pages_done` deliberately, so re-running skips completed pages instantly (`153 done, 0 to fetch`)
  while retrying exactly the failures (`254 done, 32 to fetch`). Nothing already harvested is lost.

This also explains §10's throttling entirely: the research agents were not competing with a busy
archive, they were competing with a sweep that had put itself into backoff.

## 10. A measurement caveat that shaped this audit

Wayback's CDX endpoint throttles hard. The 6-worker sweep running during this audit produced
persistent 429s, and Greenhouse lost **32 of 286 pages** to them (`wayback_pages` leaves failed
pages unmarked, so a re-run retries them — but the sweep is not complete until a pass reports
zero). It also forced the audit onto Common Crawl for sizing, which is why the figures above come
from `CC-MAIN-2026-34` rather than Wayback.

The related trap, hit earlier the same day: **concurrent CDX probes return `URLError` or an empty
body that is indistinguishable from "this host has no archived rows"** — a false negative that
nearly buried the `fa.ap4` finding. Probe sequentially with a delay, and re-probe anything that
errors before believing it.
