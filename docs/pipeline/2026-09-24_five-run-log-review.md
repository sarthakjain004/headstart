# Five-run log review — runs 35971969417 … 35998606646 (2026-09-24)

Five completed `pipeline.yml` runs, 07:52–13:09 UTC. Two dispatches in between (`35975874804`,
`35977533064`) stood down because `cleanup-index` `35973517680` held 08:08–08:49. That is correct
behaviour, so neither is counted here. Run `36003741124` was still in progress and is not counted
either.

| run | SHA | slice | wall | owner | scrape max | join | embed max | merge |
|---|---|---|---|---|---|---|---|---|
| 35971969417 | 8d4a2c3 | 20k | 40.6m | merge 13.5m | 9.8m | 12.3m | 3.7m | 13.5m |
| 35981088863 | c23826c | **30k** | 80.3m | scrape 30.4m | 30.4m | 11.2m | 12.9m | 24.7m |
| 35986858550 | 650dcc2 | 20k | 50.4m | scrape 19.6m | 19.6m | 11.4m | 8.4m | 9.8m |
| 35993916859 | 650dcc2 | 20k | 46.5m | scrape 19.7m | 19.7m | 10.1m | 5.0m | 10.2m |
| 35998606646 | 650dcc2 | 20k | 47.3m | scrape 20.1m | 20.1m | 11.3m | 4.6m | 10.1m |

Code confounds: Jibe (#604) landed between `8d4a2c3` and `c23826c`. That is why the first run has
no 19-minute floor and every later run does. The Taleo section aliasing (#602, ADR-0186) and the
Workday once-per-tenant rule (#603, ADR-0187) landed between `c23826c` and `650dcc2`, and they
account for the one-time prune in `35986858550` (14,179 taleo_enterprise off-Board + 7,401 Workday
duplicate rows). `35981088863` was the first run after compaction. Its 30k slice is why it is the
outlier, and its merge carried a 963 s upload plus a 25.56 GB reclaim. It is not a regression.

Evidence: `scripts/runlog/*` over all five runs, the `analyse-fanout-run` skill's `run_stats.py`,
cleaned per-job logs, and live probes from this session (named with sample sizes per finding).
Captures are in `experiment/pipeline-review-2026-09-24/` (local, not committed).

## Findings

### 1. The scrape floor is Costco's cashiers (HIGH — wall clock and search quality)

`jibe:costco` takes 1,143–1,148 s on every run since Jibe landed. That is 95–97% of its shard, and
it owns the scrape stage in 4 of 5 runs, while the other 14 shards finish in 8–10 minutes. The
floor itself cannot be beaten by packing. The Jibe API rejects any `limit` above 100 (`limit=500`
returned zero rows live), and the client's robots.txt sets `crawl-delay: 5`, so 201 pages × 5 s is
about 17 minutes by construction.

Costco's value to the index is not real. The priority ledger credits it with **1,581 tech jobs**.
A live sample of 300 listings (pages 1, 50 and 120) put 22 through `tech_filter.is_tech`, and every
one was a cashier: `Cashier Assistant (Front End)` ×13 and `Cashier (Front End)` ×9. Rule 0
(`_STRONG_NOT`) sets aside `front end cashier` but only in that word order. The parenthesised
retail suffix passes as a front-end developer. Those rows are embedded and served as tech jobs.

Fixing the rule removes the rows. It also drops Costco to near-zero tech jobs per minute, and the
existing ADR-0064 value gate (over 15 min, under 2 tech jobs/min) would then skip it. The next
floor would be `jibe:petsmart`: 760 s for **4** tech jobs, which the gate misses only because 12.7
minutes is under its 15-minute floor. With both gone the scrape stage falls from ~20 min to
~10 min (the 14-shard mean is 8.5–9.9 min, the next-largest single Board ~9 min). That takes wall
clock from ~47 to ~37 minutes. That figure is a projection from these runs' own shard times, not a
measurement.

### 2. HCLTech: the `sitemal.xml` fallback serves ~1,210 unavailable postings per run (HIGH)

`successfactors:careers.hcltech.com` is priority #2 (4,648 tech jobs). Every run, 1,204–1,225 of
its detail pages come back as `200 without a parseable title`, and every run the log then says
`sitemal.xml filled 1225 of 1225 unreadable job pages`.

Probed live, 120 random sitemap URLs: 26 (22%) failed to parse. All 26 302-redirect from the numeric
id to a `{req}-en_US` URL, which renders the shell *"You can't view this job because it's not
available at this time."* A second sample of 120 split the same way across 9 countries (21/100 IN,
3/9 US, 1 each NZ, GB, MX, CA), so this is not geo-gating. The sitemap and `sitemal.xml` both
still list these ids, and the feed's `g:expiration_date` is a synthetic today+30 on all 10,962
items, so neither surface carries a closure signal.

The fallback (#564, 2026-09-22) was written for pages that *failed*. Here the page succeeded and
said the job is unavailable. So a user clicking these ~1,210 rows lands on that shell, and each
row is indexed without a `posted_at`. This replaced the 2026-09-21 review's finding 5, where the
same pages kept ~2,364 HCLTech rows out of eviction scope, with a different defect. Recognising
the unavailable shell as a closure would stop the fill, avoid counting it as a loss, and remove
~1,200 wasted detail fetches from one of the slowest Boards (356–530 s).

### 3. Zoho serves postings its own detail page says are gone (HIGH)

`zoho.py` keeps every listed record and uses the detail page only to enrich it. When the detail
page says `This job posting is no longer available.`, the loss is labelled
`posting explicitly unavailable` and the Job is still built from the listing. That happened
1,247–1,953 times per run on 23–31 Boards. Live on `harrisonconsultingsolutions.zohorecruit.com`
(740 listed), 15 of 25 sampled postings return the unavailable page. `resourceit` serves the
Portuguese shell for every sampled posting. These are dead links served as live.

### 4. Zoho `no jobs blob on the page`: CI-only and unexplained (MEDIUM — observability)

This is 3,191–5,965 losses per run on 150–264 Boards (29,589 events over five runs across 785
Boards). It **did not reproduce from this session**: 150/150 through the scraper's own
`fan_out_async` path on `tmievan` (a Board that loses ~42% in CI), 64/64 at 32-way threads, and
40/40 across `jobberman`, `biztekpeople` and `mvc-resources`. The loss is therefore specific to
CI's egress or timing, and the logs cannot say which. The scraper records only the label, never the
page it got. A one-line sample per Board (status, byte count, `<title>`/`<h4>`), in the shape
Workday's `UnexpectedListingResponse` already uses, is the step that makes it diagnosable. Some of
these may be unavailable shells in a language the two-string matcher lacks.

### 5. taleo_be's second layout loses every description (MEDIUM — recoverable)

This is 1,031–1,100 losses per run (~20% of taleo_be details) on 13–17 Boards, and 100% on INVXIS,
COVESTIC2 (×2 sections), ORBIS and MATERIALISE. The module docstring already names this layout
(`YKHC, INVXIS`) as follow-up work. Live (`org=INVXIS rid=3477`), the full description is on the
page after "Job Brief". The `div.well.oracletaleocwsv2-job-description` container alone holds only
the header block (113 characters), so the selector needs measuring across tenants rather than a
swap to that class.

### 6. ADR-0121's tolerance is missing in two scrapers (MEDIUM — small fix)

`icims.py:194` and `smartrecruiters.py:162` call `mark_truncated` for a *measured* shortfall. So a
Board goes unauthoritative over `1/9,199` (`securitycareers-alliedbarton`), `1/1,633`
(`frfrench-equans`), `read 6378 of 6379` (`accorhotel`) or `read 4809 of 4810` (`boschgroup`).
`mark_truncated_unless_negligible` exists for exactly this case, and eightfold and meta already
use it.

### 7. One failed Workday page makes a whole Board unauthoritative (MEDIUM)

71 of the 132 ADR-0053 exclusions across the five runs (54%) are Workday Boards with `1 of 51` or
`2 of 242` pages failed mid-crawl (ConnectionError or HTTP 500), and those offsets are never
retried. A single second pass over just the failed offsets, after the fan-out and on a fresh
session or egress, would keep most of them authoritative. It would also stop their unsettled
descriptions piling into the gap ledger (`+2,663` on `ngc`, `+2,047` on `globalhr` in single runs).

### 8. meta is unauthoritative on every run, so it never drains (MEDIUM)

`meta:www.metacareers.com` is scope-excluded in 5 of 5 runs: 13 `no JSON-LD on a 200` pages every
run (98.7%, just under the 99% bar), plus 429 bursts (98 in `35993916859`). Under ADR-0053 its
closed postings are never evicted (48 rows withheld in one run). The same 13 ids each time suggests
unavailable pages rather than a parse gap. Worth one probe before choosing between classifying
them as closures and adjusting the tolerance.

### 9. The embedding store is re-uploaded whole on every run (MEDIUM — HF risk)

Every merge orphans `embeddings.f32` (3.13 GB) and `meta.jsonl` (0.69 GB) to append ~1,500
vectors (~5 MB). That is ~100 s of upload plus ~48 s of `reclaim_storage` per run. At ~24–30 runs
a day it is ~90–115 GB of LFS churn daily, safe only because reclaim succeeds every time. That is
the quota path of ADR-0168. An append-only delta file per run, folded into the base by
`cleanup-index`, would cut both the time and the exposure. Separately, live LanceDB grows ~0.44 GB
and ~115 files per run between compactions (8.61 → 9.94 GB over three runs; 22.27 GB just before
the 08:08 compaction).

### 10. The 11-minute join redoes the same work every run (MEDIUM — wall clock)

After Finding 1, join is the largest stage. Its measured steps:

| step | seconds | note |
|---|---|---|
| state fetch | 45–89 | ~300 per-run trend parquet files (<0.1 MB each) + 693 MB `meta.jsonl` |
| `filter_tech` | 74–115 | reclassifies all 1.78M rows each run |
| `update_descriptions` | 95–110 | |
| gap ledger | 60–70 | |
| `embed_plan` | 111–150 | runs `langdetect` on ~29.5k non-English Jobs each run; the verdict is never cached (`embed_plan.py:209`) |
| upload | 63–75 | |

Caching the language verdict per id and content hash would take `embed_plan` to seconds. Caching
the tech verdict by (title, department) would do the same for `filter_tech`, since the listing
barely changes between runs.

### 11. Boards that fail every run and are never quarantined (LOW-MEDIUM)

Only 404/410 count as gone (by design), so a steady 403 or ValueError is retried forever:

- **iCIMS 403 on 9 tenants, 5 of 5 runs.** `careers-kimley-horn` scrapes fine from this session
  (1,348 rows), so the 403 is CI-egress-specific and the Board is recoverable through spare
  egress. `careers-peraton` 403s from here too. `peraton-perspecta` 301s to `careers-peraton`, so
  it is a duplicate ledger row.
- **`taleo_enterprise:fa009…/careersection/ex`** raises `Career Section shell has no portalNo`
  in 5 of 5 runs.
- **freshteam soft 404:** ~17 Boards per run log `read no jobs — got HTML (the soft 404 page)`.

### 12. Smaller, recurring, or watch-only (LOW)

- **ADR-0053 stock is flat, not draining:** 1,044–1,238 rows withheld per run. `freshteam:abnhire`
  (729, 1,000-job cap) and `oracle:eluq` (240, the 10,000-offset cap; `egud` lists 12,192) appear
  in every run. Amazon's facet split is the precedent for reading past Oracle's cap.
- **Non-tech share** is 21.4% of served rows (was 23.3% on 2026-09-21). Costco's cashiers are part
  of it.
- **Rate-limited details:** jazzhr detail 429s of 285, 63 and 49 per run
  (`brightvisiontechnologies`), and meta 429s.
- **Retries are not attributed to an ATS.** ~8,000 5xx and ~1,300 429 retries per run are logged
  per shard only, so which origin burns them is unreadable from logs.
- **Workday concurrency** says `throughput scaled — room to widen` on 15 of 15 shards. That is
  irrelevant while the Costco floor stands, and worth trying once it is gone.
- **Cost model runs high:** actual/predicted median 0.70–0.93 on 20k slices (1.44 on the 30k run).
- **Tesla:** 1,815 of 1,815 Jobs carry no description every run (the Akamai wall, as documented).
  Oracle adds 1,350 unrecorded per run.

## Checked and healthy

- Board error rate: 0.3–0.5% of attempted Boards, and no shard hit its time budget.
- Embed: 0 failed documents, all shards on CPU, all fragments merged (14/14, 15/15, 5/5).
- The ADR-0083 grace period works as designed. Carried-in ids that were scraped again either
  reappeared (33–142) or were evicted (355–648). The rest (~1,000) sit on Boards outside the slice.
- The quarantine ledger's `0 cleared` is the documented parole shape (ADR-0162), not a stuck counter.
- The +23 min queue on `35986858550` is its cron trigger waiting on the chained 80-minute run ahead
  of it.
