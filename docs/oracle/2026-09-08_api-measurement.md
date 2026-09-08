# Oracle Recruiting Cloud: what the API actually returns

Measured live 2026-09-08 against the 670 `*.oraclecloud.com` hosts in
`data/ats-tenants-merged/oracle.csv` that carry a host in the pool's URL column. Every figure
below is a measurement, not a reading of the code.

The probe scripts live under `experiment/oracle-api/`, which is **gitignored** — the same call
jobvite's investigation made, keeping the analysis and dropping the captures. Each numbered
section below names the probe and its sample size, so any figure here can be re-derived without
them.

Evidence base: a 670-host sweep, a 15,189-requisition listing census over 200 tenants, 1,030
detail payloads, 6,351 rate-limit requests against one host, 454 detail calls across five
regional pods, and a Playwright HAR of the real careers UI.

## 0. The scraper is not wired to anything

**Before the change this document accompanies**, there was no `data/validate/liveness/oracle.csv`.
`config.load_active_companies` globs `{ledger_dir}/*.csv`, so **oracle had never been scraped in
production** — it was registered in `SCRAPERS`, absent from `DISABLED_ATS`, and unreachable all
the same. The same change adds the ledger, so this section describes the state it fixed. It also has no test file.
Both defects below have therefore never cost a served row; they would have, the moment a ledger
appeared.

## 1. The listing is nearly empty, and the description it does carry is capped at 1,000 chars

Across **15,189 requisitions** from 200 tenants, the fields `parse()` reads:

| field the parser reads | non-null in the listing |
|---|---|
| `LegalEmployer` → `company` | **0.0%** |
| `Department`, `JobFunction` → `department` | **0.0%** |
| `JobType` (0.0%) / `JobSchedule` (1.1%) → `employment_type` | **1.1%** |
| `WorkplaceType`/`WorkplaceTypeCode` → `remote` | 30.1% |
| `ShortDescriptionStr` → `description` | 44.4% |

So today a job gets a title, a location, a URL, a date, and — for fewer than half of them — a
teaser. `company` silently falls back to the raw pod hostname.

`ShortDescriptionStr` is **hard-capped at exactly 1,000 characters**: p100 = 1,000 and 17 of
6,745 samples sit precisely on it. It is a truncated summary by construction, not a description.

## 2. The full description exists, but only on the detail endpoint

```
GET /hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails
    ?onlyData=true&expand=all&finder=ById;Id="{id}"
```

The finder name is `ById` and the id must be **quoted** — taken from the careers UI's own HAR,
not guessed (`findReqDetailById` returns HTTP 400).

`ExternalDescriptionStr` is present on **93.1%** of 576 detail payloads, p50 **4,138** chars,
max 108,010 — against the listing teaser's p50 of 234. It has **no cap**: the length
distribution is smooth with no spike at any value, and the most common exact length occurs 3
times in 536 samples. `models.html_to_text` renders it cleanly (6,027 HTML → 2,818 text).

The detail payload also carries what the listing does not: `JobSchedule` 80.6%, `Category`
76.2%, `JobFunction` 45.0%, `WorkplaceType` 27.8%, plus `requisitionFlexFields` (26.0%) whose
prompts include Minimum/Maximum Salary and Pay Range. `JobType` is **0.7%** even here, which is
why `employment_type` reads `JobSchedule` first and `JobGrade`/`JobLevel` (1.6%/1.0%) are not
read at all.

**The listing can never carry it.** `expand=all` on the listing does not add
`ExternalDescriptionStr` — the key is absent at every expand tried. A detail pass is therefore
mandatory, and `has_detail_pass` must be True.

## 3. `siteNumber` filters the listing; omitting it returns the whole host

The single most useful finding, and the one that removes most of the complexity.

An invalid `siteNumber` is **not** rejected and does **not** return zero — it is ignored, and the
listing returns the host's entire job set. On `alleghenycollege-ibwwjb`: `CX_1` → 9, `CX_2` → 31,
and garbage / empty / omitted → **40**. The sites *partition* the host.

Tested on all **596** hosts with a hiring board: the unfiltered call is a superset of the union
of every site's ids, with **zero** genuine counter-examples. All 35 apparent misses have
`unfiltered == 200` against a larger true total — the page-0 cap, not a missed job.

It is not a *strict* superset either: on the 424 hosts small enough to fit one page, 8 looked
like they carried jobs no site showed, and all 8 were an artefact of my own 6-site sampling cap.
Re-run with all 36 of `eozs`'s sites, `extra` = 0.

This matters because **929 of 1,331 hiring boards are not on `CX_1`**, the scraper's hardcoded
default, and a wrong site number fails *silently* — HTTP 200, a well-formed envelope, and
whatever job set the filter happened to leave.

## 4. The site in a job URL is cosmetic

Browser-verified: `/hcmUI/CandidateExperience/en/sites/{site}/job/315` renders the job with its
Apply controls for the correct site, a wrong site, and a nonexistent `CX_9999`. All three 302 to
`CX_1` and resolve the job by id. `siteNumber` is likewise ignored on the *detail* API call —
omitting it entirely returns the job on all 454 cross-pod calls.

## 5. Pagination is correct; `limit` clamps to 200; `hasMore` lies

`limit` is silently clamped to 200 (requesting 300/500/1000 all return 200 and echo
`"limit": 200`). Offset paging is clean: page 0 and page 200 on a 248-job board had **0 overlap
and 0 missing**, with and without `sortBy`.

`hasMore` returned **false** on a board with `TotalJobsCount` 248 — do not use it as the
pagination terminator. `TotalJobsCount` is the honest signal, and the current loop is right to
use it.

**A short page is not the end of the Board** (found 2026-09-08 from the first production run,
after this document's first draft). Oracle serves under-full pages mid-walk: `ebxr.fa.us2`
answers offset 0 with **199** rows against a stated total of 420 — reproducibly, 3 of 3 attempts
— then offset 200 with a full 200 and offset 400 with the remaining 20. A loop that stops on a
short page reads 199 of 420. The run log showed it plainly: `read 199 of 420`,
`read 599 of 2184`, `read 5963 of 13424`.

**How much this costs depends heavily on which Boards you sample, so both measurements are
here.** A 40-Board sample found 5 (12.5%) hitting an early short page and 3,421 of 28,715
postings lost (11.9%); an independent 60-Board review sample found 12 (20%) and 18,974 of
63,397 (29.9%). The loss concentrates in large Boards, so the second sample is the more
alarming and the more representative of where the rows are. Either way the direction is the
same and the fix is the same.

**The end is an empty page — with one exception that matters.** The API refuses to read past
row 10,000: on `ejwl.fa.us2` (13,430 postings) `offset=9800` returns a full 200 while
`offset=9900` returns zero rows *and* a `TotalJobsCount` of 0, the envelope going blank rather
than erroring. So a Board above ~10,000 postings ends its walk at that ceiling, not at its true
end, and the shortfall check is what reports it. **That ceiling binds long before
`_MAX_PAGES = 100`** (20,000 rows), which is why no real Board reaches the page cap — the
earlier draft of §5 had this backwards.

**The total is slightly inflated, and by an amount that grows with the walk.** Across 55
multi-page Boards, 46 landed exactly on `TotalJobsCount`, no Board ever repeated an id, and the
9 that fell short were short by 1 to 7 rows — the three worst being 7 over 15 pages, 5 over 20
and 4 over 8, i.e. **0.25 to 0.5 rows per page**. Whether the counter over-counts or a row
exists that offset paging cannot reach, the measurement cannot say, and it does not decide
anything: on `ebxr.fa.us2`, `offset=199` returns 200 rows containing **no id** the ordinary
page-size walk already had, so the row is unreachable either way.

Hence `_SLACK_PER_PAGE`, and hence per-page rather than flat. A flat slack of 2 was the first
attempt and a review measured it wrong in the place that matters: it fits small Boards and
**falsely truncates large ones** — `fa-ermg` (1,280 of 1,284) and `hcml` (3,605 of 3,610) every
run, permanently, into ADR-0053's exclusion scope, which has no drain. One row per page is
double the worst ratio observed and still separates a real loss by more than an order of
magnitude: `egud` reads 10,000 of 11,056 (20.7 rows/page), `ejwl` 9,926 of 13,430 (~70) and
`etud` 89 of 114 (25) are all still reported.

**How many Boards actually paginate** — read against the committed ledger, not the sweep, for the
reason §8 gives: **262 of 991** hiring Boards exceed one 200-row page. The largest real employer
Board is Marriott at **13,430** postings — but it cannot be read whole: the 10,000-row offset
ceiling stops its walk at 9,926, which the shortfall check reports. `_MAX_PAGES = 100` is
therefore never the binding limit on a real Board — the offset ceiling is. Whichever stops the
walk, `mark_truncated` says so rather than serving a short list silently. (The exploratory sweep's own figures — 199 of 1,331 over one page, largest
4,947 — count *sites*, and are not the ones to size a page cap against.)

## 6. No rate limit found, and concurrency above ~32 is counter-productive

6,351 requests to one host, **zero non-200s**: a concurrency ramp of 1→64 (762 requests, up to
109 req/s), a 512-request burst at 128, and 4,315 requests sustained at 77 req/s for 60 s with
flat latency. No `ratelimit-*` or `retry-after` header is ever sent.

Concurrency 128 is **past the knee** — 64.6 req/s at p50 0.71 s, against conc=32's 77 req/s at
p50 0.40 s. Detail calls across five pods (us2, ocs, em3, em2, us6) scale linearly to conc=32
at 46–65 req/s, 454/454 returning items.

## 7. Company names come from `recruitingCESites`

```
GET /hcmRestApi/resources/latest/recruitingCESites?onlyData=true&fields=SiteNumber,SiteName,StatusCode
```

Unauthenticated, and it answered on **664 of 670** hosts (99%). It returns the real company name
— "Airtel Career Website", "King Ranch", "City of Memphis", "Zensar" — against the parser's
current fallback of the raw pod host. Only 151 of 1,941 site names are the generic "Candidate
Experience site".

This is the right source for `company`, and it is the only reason to enumerate sites at all,
since §3 removes the scraping need and §4 the URL need.

**Not carried into the scraper.** `company` still falls back to the slug. The measurement says
which source to use, not that the change made the switch — at ADR-0114's bar this needs its own
pass: `SiteName` is non-generic on 97% of 150 hosts against the `<title>` route's 94%, but the
names carry wrappers that ADR-0114's rules exist to strip ("Ciklum General referral", "daa All
Open Jobs", "SDU Career Site", "Job Listings at Liquidity Services Inc."). That is a
`company_name.py` rule set and its own sample, not a line in this scraper.

## 8. Is it worth wiring?

42,373 listing rows across 596 boards: **7.3% tech** by the recall-biased title gate, comparable
to Jobvite's 7.0%. But India is the **#2 country both overall (3,596 rows) and for tech** (504,
16.2% of all tech rows) — a far better India share than BambooHR (0 of 294), iCIMS (~0.6%) or
JazzHR. Titles are real: "Software Engineer (iOS)", "Senior Machine Learning Engineer", "SDET".

**Two posting totals appear in this document and they count different things.** The 1,331
hiring boards and **198,269 postings** above come from the exploratory sweep: 670 hosts (only
those carrying an oraclecloud host in the pool's URL column), counted *per site*, so a
multi-site host contributes several boards. The committed ledger counts **991 hiring Boards**
and **395,265 postings** because it probes 1,107 hosts — every pool row whose host is
recoverable, including the bare-label rows — and counts each host **once, unfiltered**, which
is the number the scraper will actually read. The ledger figure is the one to quote; the sweep's
is kept here because the per-site breakdown is what established §3.

Net of Oracle's own 78,431-posting load-test instance (excluded in `config.EXCLUDED_BOARDS`,
content-confirmed) that is **316,834 postings**. At 7.3% tech, roughly 23,000 tech jobs against
a served table of ~287k rows.

Wiring it carries a real cost: descriptions require one detail call per posting, and that cost
has not been measured against the pipeline's wall-clock budget. One line in `DISABLED_ATS` holds
the ATS back if it proves too expensive, without reverting anything.

**Not carried into the scraper.** `requisitionFlexFields` (26.0% of detail payloads) includes
prompts named Minimum Salary, Maximum Salary, Pay Range and Salary, so a salary field is
reachable here. It is deliberately not read: the prompts are tenant-defined free text with no
shared vocabulary, the values are unnormalised, and `salary.extract()`'s contract wants a period
and a currency this data does not state. That is its own pass, with its own measurement — and
per ADR-0061 it would need a `DERIVATIONS_VERSION` bump, which this change does not (no field
`extract()` derives changes for already-scraped input; oracle has no already-scraped input at
all).
