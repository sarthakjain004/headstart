# The pre-detail tech gate: which scrapers can take it, and what it is worth — 2026-09-17

Measurement for [#500](https://github.com/sarthakjain004/headstart/issues/500). Three questions,
each answered from data rather than from reading the code alone:

1. **How many scrapers do a detail pass?** 21 of 39.
2. **Which of them can take the gate safely?** Six can, by construction. Six cannot, and the
   reason is not the one #500 gives. Five need one more measurement.
3. **What is it worth?** ~48.7% of all board-seconds from the safe six alone, and the gate already
   shipped on one ATS is delivering **3.03x** on four real production Boards.

## Sources

| what | where | when |
| --- | --- | --- |
| per-Board cost | `data/state/board_cost.csv`, pulled from HF | rows dated ≥2026-09-16 (115,927 of 119,643) |
| per-ATS tech share | `filter_tech`'s own log, run `35193130454` | 2026-09-17 07:38 UTC |
| pre-filter corpus | `scrape-fragment-{0,1,2,4}` of that run — 490,417 postings | 2026-09-17 |
| A/B wall clock | `bench-tech-gate.yml` runs `35198692670` / `35199442248`, `ubuntu-latest` | 2026-09-17 |
| production before/after | scrape logs of runs `35116292689`, `35129140615` (pre-gate) vs `35193130454` | 2026-09-16/17 |

Board seconds are one sweep of every Board, not one run — `board_cost.csv` holds each Board's most
recent measurement, and a run scrapes ~20k of the Scrapable Boards. Read the shares, not the total.

## 1. Twenty-one scrapers do a detail pass

`registry.detail_pass_atses()` is authoritative (it reads each scraper's own `has_detail_pass`), so
this is not a grep: `apple, bamboohr, eightfold, gem, icims, jazzhr, jobvite, join, meta, oracle,
phenom, ripplehire, rippling, smartrecruiters, successfactors, taleo_be, taleo_enterprise,
trakstar, workday, zoho, zwayam`. `join` is in `DISABLED_ATS`, so 20 are live.

They are **94.5% of all board-seconds** against 80.2% of all postings — the detail pass is where
scrape time goes. Corroborating that from a different direction: of the 13 ATSes that produced a
`slow board` line in run `35193130454`, **all 13 are detail-pass scrapers**. Not one of the 17
listing-only ATSes did.

## 2. The grading #500 asks for — and two places it is wrong

What decides safety is not "does the listing carry a title" but **does the gate read the same two
strings `filter_tech` will read**. `is_tech(title, department)` has a rule 4 that promotes a vague
title on a technical department, so a gate blind to `department` silently drops those postings.

### Tier 1 — exact by construction (ship-ready)

`parse` reads both fields off the listing item and the detail overrides neither, so the gate's
verdict *is* the filter's verdict. No sampling needed; the proof is the call site.

| ATS | title from | department from | board-sec share | non-tech |
| --- | --- | --- | ---: | ---: |
| `workday` | `item["title"]` | `item["jobFamilyGroup"]` | 48.4% | 84.4% |
| `smartrecruiters` | `p["name"]` | `p["department"]["label"]` | 7.2% | 77.6% |
| `zwayam` | `source["jobTitle"]` | `source["departmentName"]` | 1.7% | 77.1% |
| `trakstar` | listing card `_TITLE` | listing card `_DEPT` | 0.7% | 84.9% |
| `apple` | `item["postingTitle"]` | `item["team"]["teamName"]` | 0.4% | 29.2% |
| `rippling` | `it["name"]` | `it["department"]` (detail only as fallback) | 0.4% | 77.8% |

Worth knowing: **`workday`'s `department` is empty on 131,347 of 131,347 postings.** The
`jobFamilyGroup` facet is a *query* parameter; the returned item never carries it. Workday jobs are
already classified on title alone by `filter_tech`, so the gate changes nothing about the verdict —
measured recall loss 0 of 20,517 tech postings. Same for `rippling` (0.0% department).

### Tier 2 — blocked, and not for the reason #500 states

These lose `department` at gate time. Measured over the real pre-filter corpus, as the share of
*tech* postings a department-blind gate would drop:

| ATS | board-sec share | recall lost | #500 says |
| --- | ---: | ---: | --- |
| `zoho` | 5.9% | **47.4%** | "implies title/department are already on the listing" — title yes, department no (`Industry` is detail-only) |
| `oracle` | 6.7% | **46.0%** | "not viable" — correct verdict, wrong reason |
| `jobvite` | 0.4% | **40.6%** | "implies title/department are already on the listing" — **neither** is; the listing is ids only |
| `icims` | 2.8% | **25.8%** | "check whether their job URLs carry a recoverable slug" — they do (`/jobs/{id}/{title-slug}/job`), and it does not help |
| `bamboohr` | 3.4% | 13.6% | not listed (post-dates the issue) |
| `ripplehire` | 0.3% | 1.8% | "title might still support a title-only gate" — plausibly yes, the only Tier-2 ATS where it is |

**Oracle is the important correction.** #500 rules it out because `Category`/`JobFunction` are 0.0%
on the listing and ids are not slugs. But `Title` *is* on the listing — 600/600 across three hosts
probed live 2026-09-17 — so a title-only gate is buildable. It should still not be built, and the
reason is recall: on `ejwl.fa.us2.oraclecloud.com` (Marriott) a title-only gate drops **61.5%** of
that Board's tech postings, because titles like `System Technician` and `IT Technician` are rescued
only by `Information Technology` in the department. Corpus-wide: 46.0%. Tenant-dependent —
`eofd.fa.us6` loses 0.0% — which makes it worse, not better, since a per-tenant gate is a per-tenant
recall cliff.

**iCIMS is the other correction.** Its URLs do carry a clean title slug, so the successfactors
pattern transfers mechanically. It is still blocked, because the slug recovers the title and
nothing recovers the department.

### Tier 3 — needs one more measurement

The listing carries a department, but the detail can override it (`detail.get("department") or
listing`). The gate sees only the listing's, so the two can disagree. The department-blind figure
below is the **worst case**, not the answer.

| ATS | board-sec share | worst case | status |
| --- | ---: | ---: | --- |
| `jazzhr` | 9.5% | 9.3% | **MEASURED CLEAN — 0 of 845 tech postings lost** |
| `taleo_enterprise` | 2.1% | 42.1% | unmeasured |
| `taleo_be` | 0.2% | 2.8% | unmeasured |
| `gem` | 0.1% | 10.2% | unmeasured |
| `phenom` | 0.1% | 53.9% | unmeasured |

jazzhr is now **the pipeline's straggler Board**: `jazzhr:amadaseniorcarenorthshore` took 850s in
run `35193130454`, owning the slowest shard. Its own code comment already measures the override at
117 additions / 8 changes per 1,526 paired postings, so the disagreement surface is ~8%.

**Measured live, 2026-09-17, 10 Boards / 1,748 postings: the listing department carries the gate
completely — 845 kept by the gate, 845 kept by the filter, zero disagreements.** The Boards were
chosen adversarially from the corpus, as the ones where `department` does the most work: on
`vyvebroadband` a department-blind gate would drop 30 of 31 tech postings and on `idsinternational`
24 of 44, and the listing row states that department in every one of those cases. This is a
measured tolerance, not exactness — `parse` still lets the detail override, so a tenant whose
listing omits a department its detail supplies could disagree — the same epistemic status as the
successfactors slug gate, and it should be re-checked if jazzhr's listing markup changes.

### Already shipped

`eightfold` (exact, listing-derived) and `successfactors` (approximate, slug-derived). Both were
live in run `35193130454`: eightfold fetched **19 of 61,241** details, skipping 36,915 as non-tech;
successfactors logged its slug gate on every Board, e.g. `jobs.bombardier.com: skipping 1068/1278`.

## 3. What it is worth

### Already delivered, in production

The successfactors gate merged 2026-09-16 (#503). Same Boards, runs either side of it:

| Board | jobs before | s before | jobs after | s after | speedup |
| --- | ---: | ---: | ---: | ---: | ---: |
| `careers.hcltech.com` | 9,308 | 1,110 | 2,432 | 318 | 3.49x |
| `careers.wipro.com` | 5,400 | 623 | 1,989 | 197 | 3.16x |
| `careers.capgemini.com` | 6,724 | 428 | 2,383 | 156 | 2.74x |
| `lockheed.jobs.hr.cloud.sap` | 4,722 | 306 | 2,308 | 142 | 2.15x |

**2,466s → 813s, 3.03x, 67% of that time gone.** `careers.hcltech.com` was one of the four Boards
the 2026-09-16 timing analysis named as owning the scrape maximum; it is no longer in that set.

### Measured on a runner, for the unshipped Tier 1

`bench-tech-gate.yml`, `ubuntu-latest`, A/B/A/B, two independent runs. 24 A/B pairs, **`tech_lost=0`
in every one**:

| Board | postings | tech | requests | wall clock |
| --- | ---: | ---: | --- | --- |
| `workday:greystar/External` | 1,596 | 11 | 1,597 → 12 (−99.2%) | 170.8s → 11.8s (**14.4x**) |
| `smartrecruiters:pilotcompany` | 1,764 | 5 | 1,782 → 23 (−98.7%) | ~96s → ~8.6s (**10.8–11.4x**) |
| `smartrecruiters:RamsayHealthCare1` | 329 | 2 | 333 → 6 (−98.2%) | ~31s → ~3.8s (**6.1–9.4x**) |
| `workday:aveva/AVEVA_careers` | 253 | 72 | 254 → 73 (−71.3%) | ~22s → ~9.2s (**1.8–3.4x**) |
| `workday:thehartford/Careers_External` | 238 | 88 | ~239 → 89 (−63%) | ~19s → ~8.6s (**1.7–4.2x**) |
| `workday:trendmicro/External` | 208 | 103 | 209 → 104 (−50.2%) | ~19s → ~10s (**1.5–2.1x**) |
| `smartrecruiters:soprasteria1` | 1,978 | 1,206 | 1,998 → 1,226 (−38.6%) | ~109s → ~70s (**1.54–1.59x**) |

Repeat-to-repeat spread is under 1% on the large Boards, which is what makes the A/B/A/B worth its
cost. Wall-clock saving tracks request saving at ~0.9x on large Boards and falls off on small ones,
where the listing walk is the floor. `workday:jll/jllcareers` produced no pair — its *control* arm
died on an origin HTTP 500, which is an ATS flake, not the gate.

### Projected, across every Board

`board_cost.csv` seconds × the corpus non-tech share, discounted by the measured ~0.9 wall-clock/
request ratio:

- **Tier 1 alone: ~176,000 of 361,318 board-seconds, ≈44–49% of all scrape work.** Workday is
  147,000s of that.
- **jazzhr, now measured clean: a further ~24,600s (6.8%)** — and it owns the current straggler
  shard, so this one buys critical path, not just work.
- The rest of Tier 3, if it measures clean: ~7,800s (2.2%).
- Tier 2: ~53,000s (14.7%) that this technique cannot have.

### What it does *not* fix

The scrape stage is floor-bound: 95–98% of the straggler shard is one Board. After the gate, the
floor set of run `35193130454` is `jazzhr:amadaseniorcarenorthshore` (850s), `oracle:ejwl` (1,354s),
`apple:jobs.apple.com` (1,179s), `oracle:ejwl-dev7` (977s). The gate takes **apple** down by only
~29% (it is 70.8% tech) and cannot touch **either Oracle Board at all**. So this removes roughly
half the total scrape *work* — which the packer converts into a lower even-share term — while the
**floor stays Oracle-bound**. Oracle needs the separate `categoriesFacet` idea in #500 §"Known not
viable", not this gate.

## 4. Two claims in #500 that did not survive

**"Both variants share the same correctness trap"** — the truncation denominator. True of the
successfactors *shape*, where a lost detail is a lost Job because the title comes from the detail.
Not true of the listing-derived shape: `workday`, `smartrecruiters`, `apple`, `trakstar` and
`zwayam` all call `mark_truncated*` on the **listing** walk, before the detail pass, and a gated
posting still becomes a Job with `description=None`. The denominator never moves. The trap and the
"no pre-detail title" problem are the same set — `successfactors`, `icims`, `meta`, `jobvite` — which
is exactly the set where the gate is approximate anyway.

**"eightfold is exact and free, successfactors is approximate"** — right, but the two adapters had
already drifted on a third axis nobody named: eightfold's gate is conditional on `have_details`
(i.e. only inside the pipeline, so `verify_scraper.py` and the enrichment samplers still see whole
Boards), and successfactors' is unconditional. One consequence is visible in production today:
`filter_tech` reports `successfactors 32,891/33,035 = 99.6% tech`, because non-tech postings never
reach the corpus. That ATS's real tech share is no longer observable from the pipeline's own data.

## 5. Reproducing

```bash
python -u scripts/bench/tech_gate_bench.py --repeats 2 \
  "workday:https://aveva.wd3.myworkdayjobs.com/AVEVA_careers" smartrecruiters:pilotcompany
```

`HEADSTART_TECH_GATE=1` turns the gate on; it is off by default and off for any caller with
`have_details is None`. Push to a `bench/**` branch to run it on a runner — `workflow_dispatch`
alone will not fire until the workflow is on the default branch.
