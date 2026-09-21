# Near-duplicate-spam Boards: two parks, and why no general detector

**Date:** 2026-09-21. **Outcome:** `recruitee:rebootmonkey` and `smartrecruiters:EndeavorITSolution`
added to `config.PARKED_BOARDS`; no near-duplicate gate built on the index path, on the evidence
below.

Origin: `docs/pipeline/2026-09-21_ten-run-log-review.md` §6, which found both Boards holding top-5
priority slots while contributing thousands of near-identical postings. Everything here re-measures
that finding from scratch rather than trusting it.

## How these were measured

Every figure comes from the real registered scraper against the live host:
`registry.get_scraper(ats, slug, slug)` → `.fetch_raw()` → `.parse(raw, now)`. Constructed directly,
so `have_details is None` and the ADR-0017 detail tech-gate is **off** — the Board's whole set is
read, which is what "the Board's true shape" means.

Run with `PYTHONPATH=src` from this worktree. That matters: a bare `python` here resolves
`headstart` from the *main* checkout's editable install, which is on a different branch with
uncommitted scraper edits. The first pass of every measurement below was taken that way and
re-taken; the numbers were identical, but the pin is what makes them reproducible.

Artifacts in `artifacts/`:

| file | what |
| --- | --- |
| `2026-09-21_recruitee-rebootmonkey-shape.json` | full scrape, title/location census |
| `2026-09-21_recruitee-rebootmonkey-title-stems.json` | listing-only, adds the title-stem census |
| `2026-09-21_smartrecruiters-endeavoritsolution-shape.json` | full scrape, title/location census |
| `2026-09-21_smartrecruiters-endeavoritsolution-title-stems.json` | listing-only, adds the title-stem census |
| `2026-09-21_top30-priority-duplication-census.jsonl` | the prevalence sample, one line per Board |

## 1. `recruitee:rebootmonkey` — one role, 2,352 cities

**4,377 postings, 4,377 distinct ids, 2,562 distinct titles, 2,352 distinct locations.**

The ten-run review's figures reproduce exactly. But the interesting number is one it did not have:
**no exact title repeats more than 4 times.** A distinct-title ratio reads this Board as 58.5%
unique and therefore ordinary — it is the wrong instrument entirely.

Strip each title's per-city tail — everything from the first " - ", " – ", " (" or ", " — and the
Board collapses to **70 stems, of which one — "data center technician" — holds 4,249 of the 4,377
postings (97.1%)**. The runner-up stem, "delivery manager", holds 36.

```text
Data Center Technician - Saudi Arabia - Khobar - On-site
Data Center Technician - Nigeria - Lagos - On-site
Data Center Technician - PR - Guaynabo - On-site
Delivery Manager (Datacenter Field Services) - Dominican Republic
```

Priority rank **#3** in `data/state/board_priority.csv` (pulled fresh from HF the same day),
credited **4,334 tech rows** — **40.6% of every tech row that ledger credits to recruitee at all**,
across its 1,304 scored recruitee Boards.

## 2. `smartrecruiters:EndeavorITSolution` — one city, the same intake reposted

**8,478 postings, 8,478 distinct ids, 5,050 distinct titles, 5 distinct locations.**
**8,454 of the 8,478 (99.7%) are "Indore, MP, India"**; the other four locations hold 19, 2, 2 and 1.

Top exact-title repeats: `Internship / Training for PHP` ×49, `Fresher Android Developer Training
Program` ×43, `Internship For IOS from an IT solution` ×40, `Android Developer` ×39, `PHP Developer`
×38, `Internship for Fresher` ×38.

Priority rank **#5**, credited **4,252 tech rows**.

**One number in the originating review does not reproduce.** It says this Board is "8.4% of all
smartrecruiters tech jobs"; against the 2026-09-21 priority ledger it is **6.5%** (4,252 of the
65,444 tech rows credited to smartrecruiters across 3,606 scored Boards). Trust the 6.5%.

Content read before parking, per `EXCLUDED_BOARDS`' own rule: these are real postings from a real
Indore IT-training shop advertising the same trainee intake over and over — not a vendor sandbox.
That is why this is a Park and not an Exclusion.

Note the ledger also carries six sibling `EndeavorIt…` tenants (`EndeavorITSolution10` at 808
postings, `EndeavorItSolution9` at 158, four more at 0–10). They are separate Boards and are left
alone: only this one is large enough to have been measured, and parking a Board on a guess is what
`EXCLUDED_BOARDS`' rule exists to prevent.

## 3. Prevalence: is this shape common enough to justify a detector?

Sampled the **top 30 Boards by `data/state/board_priority.csv`** (freshly pulled; 30 keys dedupe to
28 Boards — nvidia's and micron's Workday sites each appear twice under two priority keys).

Run listing-only: each scraper was handed a `have_details` container that claims every detail is
already held, so `needs_detail` is False everywhere and no per-Job request is made (ADR-0048).
Titles and locations are read off the *listing* by every scraper in this sample, so no ratio here
depends on detail data — with one asymmetry, below. **Control:** the listing-only path reproduced the full-scrape figures for
`recruitee:rebootmonkey` exactly (4,377 / 2,562 / 2,352 / max-per-title 4) in 9s instead of minutes.

**23 of 28 measured.** Three are the Boards this PR parks plus
`successfactors:hcltech.jobs.hr.cloud.sap`, all now absent from `load_active_companies` — which is
itself the check that the park takes effect. Two errored:
`workday:https://micron.wd1.myworkdayjobs.com/External` (a Tomcat `HTTP Status 500` HTML page where
JSON was expected — cause not investigated; a status code does not imply its mechanism) and
`eightfold:ngc.eightfold.ai` (`HTTP Error 405` on `_job_urls`).

### The one asymmetry in this sample, and its direction

A non-None `have_details` also re-enables the ADR-0017 detail tech-gate
(`base.py:452`: `if not self.tech_gate_enabled() or self.have_details is None`), so the census is
not uniformly a full-Board read the way the two per-Board measurements above are. Checked at every
call site rather than assumed:

- **Inert on counts for 19 of the 23.** `amazon`, `oracle`, `tesla`, `lever` and `google` never call
  `tech_detail_wanted` at all. `smartrecruiters`, `workday`, `apple` and `eightfold` do, but each
  one's `parse` walks the **whole listing** and a gated posting still becomes a Job with
  `description=None` — `smartrecruiters.parse` iterates `raw["content"]`, `apple.parse` iterates
  `raw["searchResults"]`, `eightfold` builds `records` from `positions`, and `workday.py:872` says
  so in as many words ("the Board's list stays whole, so no truncation denominator moves").
- **Not inert for the 4 `successfactors` Boards.** `successfactors.fetch_raw` returns
  `zip(tech_listed, fields)` — only the gated subset — so `careers.wipro.com`,
  `careers.hcltech.com`, `careers.capgemini.com` and `lockheed.jobs.hr.cloud.sap` are measured on
  their **tech subset**, not their whole Board. (Their liveness rows are no use as a denominator:
  the ledger's stale July counts, 1,426 / 1,456 / 1,338, are *smaller* than what was measured.)

Two of those four are the counter-example the conclusion below rests on, so the direction matters.
For **max copies of one exact title** it is safe and in the argument's favour: restricting to a
subset can only *lower* that count, so Wipro's 404 and HCLTech's 399 are floors on their full
Boards, against Endeavor's whole-Board 49. For **top-stem share** a subset can move the ratio
either way, so treat Wipro's 18.3% and HCLTech's 17.2% as indicative rather than bounded — Reboot
Monkey's 97.1% is a whole-Board figure and 5.3x clear of them, which no plausible distortion closes.
Re-measuring those four un-gated was rejected on cost, not principle: `successfactors` fans a detail
fetch per posting and is this repo's slowest ATS (`careers.ey.com` is parked for taking 19–37 min).

Ranked by the share of the Board held by its single largest title stem:

| Board | postings | titles | max/title | stems | max/stem | top-stem share | locations |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **recruitee:rebootmonkey** *(parked)* | 4,377 | 2,562 | 4 | 70 | 4,249 | **97.1%** | 2,352 |
| successfactors:careers.wipro.com | 2,480 | 618 | 404 | 490 | 455 | 18.3% | 170 |
| successfactors:careers.hcltech.com | 2,975 | 583 | 399 | 333 | 513 | 17.2% | **4** |
| oracle:jpmc-test.fa.oraclecloud.com | 10,000 | 9,004 | 29 | 4,167 | 1,203 | 12.0% | 1,003 |
| eightfold:careers.micron.com | 1,094 | 967 | 11 | 575 | 88 | 8.0% | 37 |
| oracle:jpmc.fa.oraclecloud.com | 7,314 | 6,517 | 44 | 2,534 | 527 | 7.2% | 1,145 |
| oracle:jpmc-dev1.fa.oraclecloud.com | 7,501 | 6,616 | 27 | 3,199 | 491 | 6.5% | 1,069 |
| oracle:jpmc-dev5.fa.oraclecloud.com | 7,560 | 6,641 | 27 | 3,226 | 491 | 6.5% | 1,072 |
| oracle:jpmc-dev9.fa.oraclecloud.com | 7,691 | 6,695 | 27 | 3,271 | 494 | 6.4% | 1,072 |
| google:careers.google.com | 3,201 | 2,981 | 7 | 1,198 | 169 | 5.3% | 720 |
| workday:ngc/Northrop_Grumman_External_Site | 3,776 | 3,137 | 15 | 2,176 | 188 | 5.0% | 322 |
| workday:nvidia/NVIDIAExternalCareerSite | 2,693 | 2,255 | 15 | 1,250 | 129 | 4.8% | 384 |
| eightfold:jobs.nvidia.com | 2,687 | 2,253 | 15 | 1,251 | 128 | 4.8% | 81 |
| eightfold:nvidia-sandbox2.eightfold.ai | 2,673 | 2,237 | 15 | 1,243 | 124 | 4.6% | 81 |
| successfactors:lockheed.jobs.hr.cloud.sap | 2,473 | 1,261 | 72 | 890 | 106 | 4.3% | 142 |
| tesla:www.tesla.com | 8,212 | 5,792 | 159 | 2,574 | 302 | 3.7% | 946 |
| amazon:www.amazon.jobs | 22,642 | 14,696 | 241 | 5,637 | 802 | 3.5% | 1,391 |
| apple:jobs.apple.com | 6,269 | 3,613 | 48 | 2,314 | 213 | 3.4% | 434 |
| **smartrecruiters:EndeavorITSolution** *(parked)* | 8,478 | 5,050 | 49 | 3,523 | 244 | **2.9%** | **5** |
| successfactors:careers.capgemini.com | 2,776 | 2,042 | 61 | 1,578 | 79 | 2.8% | 203 |
| workday:globalhr/REC_RTX_Ext_Gateway | 4,678 | 4,018 | 18 | 2,647 | 106 | 2.3% | 822 |
| lever:jobgether | 4,482 | 2,014 | 43 | 1,632 | 100 | 2.2% | 44 |
| eightfold:lockheedmartin.eightfold.ai | 4,720 | 2,585 | 63 | 1,926 | 102 | 2.2% | 194 |
| eightfold:careers.qualcomm.com | 1,306 | 1,235 | 6 | 946 | 27 | 2.1% | 68 |
| smartrecruiters:SonsoftInc | 6,519 | 3,187 | 56 | 2,244 | 88 | 1.3% | 290 |

### What the sample actually says

**The Reboot Monkey shape is rare, and cleanly so.** Its 97.1% top-stem share is **5.3x** the worst
of the other 23 (Wipro, 18.3%). Nothing else in the top of the ledger is one role wearing 2,352
city names. A detector keyed on that single number would fire on exactly one Board in this sample —
which is another way of saying it would be a hardcoded rule with extra steps.

**The Endeavor shape is not separable by any cheap ratio at all, and this is the important
finding.** Endeavor sits *nineteenth* of 25 on top-stem share (2.9%). Its real markers are 99.7% of
postings in one city and 49 copies of one exact title — and on both of those,
`successfactors:careers.hcltech.com` looks **worse**: 2,975 postings across **4** locations with
**399** copies of one exact title, and `careers.wipro.com` has 404. Those two counts are *floors*
(their Boards were measured on the tech subset — see the asymmetry note above), so the gap is at
least this wide. Both are real employers doing genuine bulk requisition hiring that this index
wants.

So every cheap ratio strong enough to catch Endeavor evicts HCLTech and Wipro first. The Endeavor
park does not rest on a ratio; it rests on reading the postings (trainee intakes reposted in one
city). That is a judgement a gate on the index path cannot make from title and location alone.

**Conclusion:** no general near-duplicate detector, and specifically not one built on these signals.
Revisit only with a signal that can tell a reposted advert from a genuine second requisition — the
descriptions themselves are the obvious candidate (`data/descriptions/`, ADR-0050), and they are not
cheap. Until then, parking measured Boards one at a time is the honest instrument.

### One adjacent observation, not acted on

Four JPMC Oracle pods (`jpmc-test`, `jpmc-dev1`, `jpmc-dev5`, `jpmc-dev9`) sit in the top 30, each
~7.5–10k postings, with near-identical stem profiles (491/491/494 max-per-stem). `jpmc-test` is
offset-capped at exactly 10,000. That is a *different* duplication problem — one board replicated
across dev/test pods, which ADR-0023's identity dedupe cannot see either — and CLAUDE.md already
records Oracle's own load-test instance being excluded on the same grounds. Flagged, not touched.
