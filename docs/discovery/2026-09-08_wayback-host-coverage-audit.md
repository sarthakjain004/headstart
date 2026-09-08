# Is the Wayback feeder sweeping every host our ATSes serve boards from?

**2026-09-08.** Two questions, asked together because they have the same failure mode — a board
that exists, that we could scrape, and that discovery never sees, with nothing in any log to say
so.

1. Does re-fetching CDX pages already marked *done* recover boards? (Yes — measured.)
2. Is `wayback_feeder.ATS_HOSTS` missing hosts entirely? (Yes — two real gaps, one large.)

Group B of the audit (icims, zoho, oracle, successfactors, keka, freshteam, trakstar, darwinbox,
ripplehire, eightfold) was still running when this was written; §7 is the placeholder.

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

*Group B's remaining ATSes (icims, oracle, successfactors, keka, freshteam, trakstar, darwinbox,
ripplehire, eightfold) were still under audit; this section is incomplete.*

## 8. `ATS_HOSTS`'s own docstring is wrong

It claims the table is "derived from the scrapers' own URL construction". For six of the ten ATSes
audited in group A the scraper's *primary* host is absent — greenhouse, lever, ashby,
smartrecruiters and rippling all talk to an `api.`/`boards-api.` host, and Workday cannot reach
`myworkdaysite.com` at all. Given §6, the right fix is to reword the docstring: `ATS_HOSTS` is the
set of hosts that serve **archivable board pages carrying a slug**, which is not the same set as
the hosts the scrapers fetch from.

## 9. Ranked actions

1. **Add a `myworkdaysite` style and host** (§3) — a floor of 31 unknown Boards, 12 sampled live
   with 2,389 jobs, and 23 CDX pages left entirely unswept. Needs a new extractor; no scraper
   change.
2. **Add the two Greenhouse ANZ hosts** (§4) — two lines, `extract()` already handles them.
3. **Teach `extract()` Teamtailor's `utm_content` slug** (§5) — 53 known tenants, 51 live, that
   the harvest has never seen.
4. **Clear resume markers before every periodic sweep** (§1), or give `wayback_pages.py` a
   `--refresh` flag so this is not a manual step that gets forgotten.
5. **Reword the `ATS_HOSTS` docstring** (§8) and drop `cc_miner`'s NXDOMAIN target (§6).

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
