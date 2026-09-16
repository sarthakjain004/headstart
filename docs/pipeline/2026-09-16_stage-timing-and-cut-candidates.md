# Where the pipeline's wall-clock goes, and what would cut it — 2026-09-16

**Window:** the six successful `nightly-pipeline` runs of 2026-09-16 up to 12:00 UTC —
`35058831217`, `35067130555`, `35071940457`, `35078301418`, `35083167773`, `35088621664`.
Five ran `d928946`, the last ran `22512a0`. Mean wall clock **57.3 min** (min 52, max 61).

This is a timing analysis. No pipeline was dispatched for it, no code was changed, and the one
live probe it ran (against `jobs.apple.com`, 106 requests) is described and bounded below.

## 1. The critical path

`run_stats.py` reports `Σ of stage maxima = wall clock (+0 min queueing)` on all six runs, so the
pipeline is genuinely a chain of stage maxima with no scheduling slack to reclaim.

| stage | mean max (min) | share of wall | shape |
| --- | ---: | ---: | --- |
| `scrape-plan` | 0.7 | 1% | serial, trivial |
| **`scrape`** (15 shards) | **28.7** | **50%** | fan-out, **floor-bound** |
| **`join`** | **14.9** | **26%** | **serial, un-bankable** |
| `embed` (2–14 shards) | 4.0 | 7% | fan-out, balanced |
| `merge` | 8.8 | 15% | serial, un-bankable |
| `chain` | 0.2 | <1% | serial, trivial |

Two things this overturns, both worth saying plainly because the repo's own budget comment and
the project memory still describe the old shape:

- **Embed no longer dominates.** `pipeline.yml`'s time budget was sized 2026-07-25 around embed
  and projected "~126 min of embed across 15 shards". Embed now costs **4.0 min**, 7% of the run.
  The backlog is drained: run `35088621664` planned **1,943 new Docs** out of 421,854 tech jobs
  scanned, and three of the six runs fanned out to only 2–4 shards. Embed is healthy and is not
  where time is left.
- **Scrape and join together own 76% of the run.** Everything below is about those two, plus the
  handoff between them.

Per-shard overhead is not the problem anywhere. The straggler scrape shard of `35088621664` spent
**27 s** on setup, checkout, install, WARP and upload against **1,542 s** of actual scraping; the
uv migration (ADR-0122) already took that cost out.

## 2. `scrape` is floor-bound on four recurring boards

`wall = max(Σ work ÷ concurrency, slowest single item)`, and the second term decides it. Across all
six runs the slowest shard was **95–98% one board**:

| run | scrape max | floor% | the board |
| --- | ---: | ---: | --- |
| `35058831217` | 32.7 min | 98% | `oracle:ejwl.fa.us2.oraclecloud.com` (1,924 s) |
| `35071940457` | 31.5 min | 98% | `apple:jobs.apple.com` (1,848 s) |
| `35083167773` | 31.4 min | 98% | `apple:jobs.apple.com` (1,846 s) |
| `35088621664` | 26.4 min | 97% | `successfactors:careers.hcltech.com` (1,540 s) |
| `35067130555` | 25.5 min | 97% | `apple:jobs.apple.com` (1,480 s) |
| `35078301418` | 24.5 min | — | (mixed) |

The recurring set is four boards: `apple:jobs.apple.com` (1,480–1,848 s),
`oracle:ejwl.fa.us2.oraclecloud.com` (1,070–1,924 s), `successfactors:careers.hcltech.com`
(1,014–1,540 s) and `oracle:ejwl-dev7.fa.us2.oraclecloud.com` (853–1,147 s).

**`scrape_plan` already predicts this** — it logged `single-board floor 27.1 min` against a
`15.5 min even share` before a single shard ran, and warned. The packer is working; the packer is
not the lever. The measured spread of the *plan* is 1.09x mean, against an actual 1.41–1.90x.

### Why those four boards cost what they do

All four are **detail-pass** boards: one HTTP request per posting on top of the listing walk. That
is structural and documented — Oracle's real body is reachable only from
`recruitingCEJobRequisitionDetails?finder=ById` (CLAUDE.md's Oracle entry), and SuccessFactors'
fields come from a per-job detail pass (its entry). For Apple it was measured directly.

**Live measurement, `jobs.apple.com`, 2026-09-16** (106 requests total: 11 listing pages and two
96-fetch detail batches; from a laptop in this session, *not* a CI runner, so treat the absolute
minutes as indicative and the ratio as the finding):

| phase | measured | extrapolated to the full board (6,157 postings) |
| --- | --- | ---: |
| listing walk | 0.35 s/page, 308 pages | **1.8 min** |
| detail pass, 16 streams | 7.8/s, 96/96 HTTP 200, p50 1.91 s | **13.2 min** |
| detail pass, 32 streams | 13.5/s, 96/96 HTTP 200, p50 1.98 s | **7.6 min** |

**The detail pass is 88% of the board.** The listing is nearly free.

### The finding: only two of the detail-pass scrapers use the ADR-0048 skip

`BaseScraper.needs_detail()` exists precisely to skip a detail fetch for a posting whose content we
already hold. Grepping every scraper, it is consulted by **eightfold** (`eightfold.py:429`) and
**phenom** (`phenom.py:331`) — and by nobody else. `apple.py:187`, `oracle.py:235`,
`jazzhr.py:261` and `jobvite.py:80` each carry a comment deliberately declining it, with the same
reason:

> That optimisation is only safe where the detail fetch supplies the description and nothing
> else — true for eightfold, false here: this payload is also the only source of
> `employment_type` […], `department` […] and most of `remote`. Skipping it for an
> already-described Job would blank three fields that had values.

That reasoning is correct given the ADR-0050 store's record shape, which is `{id, description}` and
nothing more. **The cost of that shape is the pipeline's critical path.** Every one of the four
boards that owns the `scrape` maximum is in the declining group; the one ATS that does use the skip
is also the one supplying 94% of all `filled` descriptions (24,222 of 25,696 in `35088621664`).

The corollary is that these boards re-fetch ~30,000 detail pages per run to recover fields that
almost never change on a posting that has not changed.

## 3. `join` — 930 s, serial, three steps own 62% of it

Step timings from `35088621664`:

| step | seconds | share |
| --- | ---: | ---: |
| **Upload corpus + state for the merge job** | **256** | 28% |
| **Tech filter** | **185** | 20% |
| **Plan the embed fan-out** | **134** | 14% |
| Reconcile descriptions with the store | 81 | 9% |
| Update board description-gap ledger | 75 | 8% |
| Update board priority ledger | 51 | 5% |
| Update board failures ledger | 43 | 5% |
| Union the scrape fragments | 34 | 4% |
| downloads + uv install | 60 | 6% |

### 3a. The handoff artifact is 2.4 GB, and 56% of it is never read

`corpus-state` measured **2,475.8 MiB** for this run (Actions artifacts API). The 15 `scrape-fragment`
artifacts — the same raw rows, the same zip encoding — sum to **1,398 MiB**, so raw
`data/jobs/*.jsonl` is **56% of the handoff**. The remainder is `data/jobs/tech/`,
`data/descriptions/` (617 MiB on HF) and `data/state/` (195 MiB on HF).

`merge` reads that raw corpus in exactly one place. `index.py:242`:

```python
def _scraped_boards(scraped, corpus_ids, live) -> set[str]:
    ...
    return {resolve_board(job["id"], live) for job in iter_jobs(path)}
```

It JSON-decodes **2,095,569 rows** — descriptions and all — to build a set of a few thousand Board
keys. Nothing else in the merge job touches raw `data/jobs`: `index sync` is the only command run
with its `--scraped` default, `update_meta` reads the tech corpus (its log says
`corpus facts for 421854 Jobs`), and `prune`/`role_trends` read the table.

Cost of carrying it: **256 s** uploading in `join` + **104 s** downloading in `merge`, plus an
unmeasured share of `index sync`'s 112 s spent on the parse.

### 3b. The tech filter is single-threaded

`tech_filter.filter_jobs` is a plain `for src in sorted(src_dir.glob("*.jsonl"))` loop that
`json.loads` every row to read two keys. Production rate: 2,095,569 rows in 185 s = **11,327
rows/s**.

The files are independent, so this is embarrassingly parallel. LPT-scheduling the 33 real per-ATS
row counts from this run across a public-repo `ubuntu-latest` (4 vCPU):

| workers | makespan | even share | largest-file floor (`workday`, 500,679 rows) |
| ---: | ---: | ---: | ---: |
| 1 | 185.0 s | — | — |
| 2 | 92.5 s | 92.5 s | 44.2 s |
| **4** | **46.3 s** | 46.2 s | 44.2 s |

It is sum-bound, not floor-bound — `workday` at 500,679 rows sits just under the 523,892-row even
share — so 4 workers land on the even share almost exactly. **Projected saving ~139 s.** The caveat
is that this models a row as costing the same on every ATS, which description length makes only
approximately true.

The pattern is already in the repo: `update_meta.py:574` fans its sweep out over
`ProcessPoolExecutor(max_workers=os.cpu_count())`.

A second, smaller win in the same function: it re-serializes every kept row with `json.dumps`
rather than echoing the original line. Benchmarked locally on a real `greenhouse.jsonl`, that is
worth **7%** (27,494 → 29,510 rows/s) — real but minor next to parallelism, and it is only safe if
nothing upstream is relying on the re-encoding to normalise the line.

### 3c. `embed_plan` scans 421,854 Docs to find 1,943

134 s to produce a plan for 1,943 new Docs: 397,850 of the scanned rows were already embedded and
22,061 were non-English. 97.8% of the scan is discarded.

**This one is not yet diagnosed** — I have the totals, not the internal split between iterating
the corpus, the set-membership diff against `meta.jsonl`'s 816,640 ids, and the English gate plus
tokenizer on the surviving 24,004. Profile it before assuming an id-only pre-pass helps; it is the
least-substantiated item in this document.

## 4. `merge` — 486 s

| step | seconds |
| --- | ---: |
| Sync the jobs table | 112 |
| Download corpus + refreshed state | 104 |
| Upload index state | 71 |
| Download the prior store + LanceDB | 57 |
| Refresh stored metadata (ADR-0061) | 51 |
| Append role-trend counts | 29 |
| Merge shard fragments into the store | 26 |
| uv install | 17 |
| Prune | 7 |

`update_meta` took the fast path (`derivations v11 stored, v11 in code — no sweep`), so 51 s is the
cheap case; a `DERIVATIONS_VERSION` bump makes this job much slower for one run and that is a known
confound, not a regression. `index sync`'s 112 s covers a 464,040-row table taking +2,023/−350,
*plus* the 2.1M-row raw-corpus parse from §3a.

## 5. Ranked candidates

Projections are labelled. Only the measurements in §2 and §3 are measurements.

| # | change | stage | est. saving | confidence |
| --- | --- | --- | ---: | --- |
| 1 | Make the ADR-0048 detail skip safe for the non-eightfold detail scrapers | `scrape` | **~12–17 min** | high on the diagnosis, medium on the size |
| 2 | Drop raw `data/jobs/*.jsonl` from the `corpus-state` artifact; pass a `scraped_boards` file instead | `join`+`merge` | **~3.5 min** | high |
| 3 | Parallelise the tech filter across ATS files | `join` | **~2.3 min** | medium-high |
| 4 | Raise `apple._DETAIL_WORKERS` 16 → 32 | `scrape` | ~1–2 min | medium (96-request sample) |
| 5 | Profile, then trim, `embed_plan`'s 421,854-row scan | `join` | ≤2 min | low — undiagnosed |

Candidates 1 + 2 + 3 together would take the mean run from **~57 min toward ~38 min**, with
`scrape` ceasing to be floor-bound and becoming sum-bound near its even share.

### Notes on candidate 1

This is the architectural one and needs a decision, not a patch. The blocker is that ADR-0050's
store holds `{id, description}`, so skipping a detail blanks `employment_type`, `department` and
`remote`. Three ways out, each with a different cost:

1. **Widen the store's record** to carry the detail-derived fields, not just the description. Most
   direct; changes the store's schema and its HF footprint (617 MiB today).
2. **Preserve rather than blank** — make the merge keep an already-indexed row's existing value for
   a field the current scrape did not supply. Touches nothing in the scrapers, but weakens the
   pipeline's "the scrape is the truth" property and needs care around genuine field *removals*.
3. **Skip the detail only for postings unchanged since the last scrape**, where the listing exposes
   a usable change signal. Narrowest and safest; only works per-ATS where such a signal exists, and
   Apple's own `postDateInGMT` is known to move on evergreen `PIPE` rows without the posting
   changing (`apple.py` module docstring).

### Note on `oracle:ejwl`

`ejwl` is one of the four floors, reads 9,926 of 13,642 every run (Oracle serves no offset past
10,000, so it is ~27% incomplete by construction), is scope-excluded from eviction on every run,
and takes 12% of all spare-egress rotations. Dropping it would remove a floor for free. But
`docs/oracle/2026-09-16_are-the-ejwl-and-jpmc-pods-real.md` establishes it serves real postings and
argues the decision belongs on coverage/value terms, not performance terms. Flagged here, not
recommended here.

## 6. What this does not establish

- Six runs on two commits over eight hours is a window, not a trend. The stage *shares* were stable
  across all six; the absolute minutes were not (52–61).
- The Apple measurement is from a laptop, not an `ubuntu-latest` runner behind WARP. CI's implied
  per-detail cost is ~4.75 s against the 1.91 s p50 measured here — a ~2.5x gap that is itself
  worth a look, since if egress is the cause it would apply to every detail-pass board at once.
- The 4-worker tech-filter makespan is a schedule computed from real row counts, not a timed run.
- Candidate 5 is a total, not a diagnosis.

---

# Addendum: measuring candidate 1 before choosing a design (2026-09-16, same day)

Written after the body above, in response to "measure more first". Two things changed the
recommendation. Everything here is live measurement against the real hosts, using the repo's own
scraper classes so the request shape matches CI.

## A fourth design option appeared, and it is better than the three above

`c055a626` — **PR #484, merged the same day** — added a *pre-detail tech gate* to eightfold: skip
the detail fetch for postings the ADR-0017 filter would drop anyway, since `title` and `department`
are already on the listing. Its commit message: "~34,700 of ~34,900 fetches".

That sidesteps §2's blocker entirely. The objection to ADR-0048's skip was that blanking
`employment_type`/`department`/`remote` corrupts an **indexed** Job. A **non-tech** posting is never
indexed, so blanking costs nothing. No store schema change, no merge-side preservation rule.

**But it is not free to extend, and the reason is per-ATS.** The gate needs the tech filter's inputs
*on the listing*, and the two boards below state only one of them.

## `oracle:ejwl` — the gate would be title-only, and that is not a small difference

Oracle's listing states no department at all (`Category`/`JobFunction` are 0.0% there — CLAUDE.md's
Oracle entry, confirmed here). So the gate could only read `title`.

Listing walk: **9,926 requisitions in 124.8 s**. Then a 500-requisition random sample
(`seed=20260916`, drawn across the whole board, not the head — the head is sorted newest-first and
is not representative), details fetched at 16 streams, 462/500 returned:

| verdict | keeps | of 500 |
| --- | ---: | ---: |
| full (title + the detail's department) — what ships today | 42 | 8.4% |
| title-only (what a pre-detail gate could see) | 14 | 2.8% |

**28 of the 42 would be dropped — 66.7% of what the pipeline currently calls tech on this board.**

**Read the 28 before believing that number.** They are:

```
Housekeeper                      dept='Engineering & Facilities'     (generic-tech-token)
Plant Operator                   dept='Engineering & Facilities'     (generic-tech-token)
Loss Prevention Associate        dept='Loss Prevention & Security'   (tech-department)
Security Officer                 dept='Loss Prevention & Security'   (tech-department)
Refrigeration Technician         dept='Engineering & Facilities'     (generic-tech-token)
Mgr-Security I                   dept='Loss Prevention & Security'   (tech-department)
IT Technician                    dept='Information Technology'       (tech-department)
```

Almost every one is a **false positive of the current filter**, not a tech job: a hotel's
housekeeping and security staff, promoted by the department tier because "Engineering & Facilities"
carries a generic tech token and "Loss Prevention & Security" matches `_TECH_DEPT`. Of the 28, one
— `IT Technician` — reads as a genuine loss.

So the honest statement is: **the 66.7% figure is a measurement of the gate against the current
filter, not against the truth.** A title-only gate on `ejwl` would ship a *more* precise corpus and
lose roughly one real tech job per 500 postings. That is a product call about what the index should
contain, not a performance call, and it should be decided on those terms.

It also surfaces a **separate defect worth its own look**: on a non-tech company, the ADR-0017
department tiers over-promote. `is_tech("Security Officer", "Loss Prevention & Security")` is True
today. That is recall-bias working as designed, but `ejwl`'s 8.4% "tech" is mostly this.

### Concurrency is *not* a lever on Oracle — measured, and 64 is actively harmful

192 details per width, same sample:

| streams | rate | HTTP 200s | extrapolated to all 9,926 |
| ---: | ---: | ---: | ---: |
| 16 (today) | 19.5/s | 192/192 | 8.5 min |
| 32 | 21.7/s | 192/192 | 7.6 min |
| **64** | **2.8/s** | 192/192 | **59.4 min** |

32 buys 11%. **64 is 7x slower than 16** — it collapses without ever returning a non-200, so nothing
in the logs would say why. Candidate 4 in the body above (raising detail workers) should **not** be
generalised past Apple; on Oracle the knee is real and below 64, matching CLAUDE.md's own "conc=32
is the knee" note.

## `successfactors:careers.hcltech.com` — the title is in the URL, so a gate *is* possible

The SuccessFactors listing is `(url, id)` — `_job_urls_from` returns no title, so at first reading a
pre-detail gate is impossible here. It isn't: **the title is in the URL slug.**

Sitemap: **11,201 job URLs in 1.9 s**, shaped
`https://careers.hcltech.com/job/{Title-Slug}/{id}/`:

```
.../job/Technical-Specialist/1357856755/
.../job/Senior-Administrator-Network-LANWAN/1362930755/
.../job/SME-Red-Hat-Enterprise-Linux%2C-Red-Hat-Satellite%2C-Red-Hat-Cluster/1350308355/
.../job/Domain-Consultant%28Sales-Support%29/1347075955/
```

URL-decode, swap hyphens for spaces, and the tech filter has a usable title. The listing is 1.9 s
against a board that costs 1,014–1,540 s in CI, so essentially **the entire board is its detail
pass** — and it is the board that owned the scrape maximum on the most recent run.

**Not yet measured, and it must be before this ships:** the recall cost of title-only gating *here*.
HCLTech is an IT-services company, so its titles are descriptive and the loss should be far smaller
than Oracle's — but "should be" is exactly what this addendum exists to refuse. The same 500-sample
method applies.

## Revised recommendation for candidate 1

Not the three options in §5. Instead: **extend PR #484's pre-detail tech gate, one ATS at a time,
each gated on its own measured recall cost.**

| board | gate input available | listing cost | detail cost | measured next step |
| --- | --- | ---: | ---: | --- |
| `successfactors:careers.hcltech.com` | title, from the URL slug | 1.9 s | ~all of 1,014–1,540 s | measure recall on a 500-sample, then ship |
| `oracle:ejwl` | title only (no department anywhere on the listing) | 124.8 s | 7.6–8.5 min | needs the product call above first |
| `apple:jobs.apple.com` | title **and** department, both on the listing | 1.8 min | 13.2 min | 70.7% tech, so the gate saves ~29% — least valuable of the three |

Note the ordering this produces is the reverse of the boards' cost ranking: Apple is the most
expensive board but the *worst* candidate for a tech gate, because it is 70.7% tech. The saving
from a tech gate scales with how much of a board the filter throws away, not with how big it is.

## Correction to the body above

§5's candidate 4 ("Raise `apple._DETAIL_WORKERS` 16 → 32") stands only for Apple, on a 96-request
sample. The Oracle measurement above shows the same change would be catastrophic on a different
host. Any concurrency change is per-ATS and needs its own measurement.

---

# Addendum 2: candidate 2 shipped independently, same day (2026-09-16)

While building candidate 2 (drop raw `data/jobs` from the `corpus-state` artifact) in a worktree,
`main` gained **PR #487 / ADR-0161, "Carry the eviction scope as Board keys, not as the corpus"**
(`d0d7923a`) — merged before this worktree's version was ready to open, with the identical design:
`scrape_join` derives and writes `data/state/scraped_boards.json` (a JSON list of `resolve_board()`
keys) while it's already touching every line; `index sync` reads that via a new `--scraped-boards`
flag instead of re-parsing 2.1M raw rows; the artifact ships `data/jobs/tech` in place of
`data/jobs`. ADR-0161's own measurement of the *input* matches this document's §3a exactly
(2,596,032,826 → a projected ~1,030,600,000 compressed bytes, −60.3%). The redundant worktree and
branch were discarded rather than opening a competing PR.

**Not yet confirmed against a real run** — no completed pipeline run yet has `d0d7923a` as its
head commit (checked via `gh run list`; the run in flight at the time of writing is still on an
older SHA). ADR-0161 says as much: "stay projections until a real run reports its own `Final size
is`." Whoever reads this next should pull the first completed run on or after `d0d7923a` and check
`join`'s upload step and `merge`'s download step against the ~189 s / 314 s projection, the same
way §1–§3 above were built from six real runs rather than asserted.

Candidate 3 (this document's own tech-filter parallelisation) is open as **PR #489**, code-reviewed
and fixed (a fork-safety hazard on the `__main__.py` caller, an overstated docstring claim, and a
test gap in the pooled-submission-order guard — all caught by the Standards/Spec review and
corrected before this line was written). Real measurement: 332,383 rows, 10.69 s inline vs 3.03 s
pooled at 4 workers (3.53x), byte-identical output.
