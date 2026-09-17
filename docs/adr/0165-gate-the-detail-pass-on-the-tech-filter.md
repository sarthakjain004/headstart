# ADR-0165: Gate the detail pass on the tech filter, behind one seam

**Status:** accepted · **Date:** 2026-09-17 · **Amends:** [ADR-0048](0048-skip-details-we-already-hold.md)
(its 2026-09-16 amendments described two hand-rolled gates; this replaces both with one seam and
states which scrapers may take it) · **Implements:**
[#500](https://github.com/sarthakjain004/headstart/issues/500)

## Context

A **detail pass** costs one request per posting against a per-origin budget. It is what makes an
ATS expensive: detail-pass scrapers are **94.5% of all board-seconds** against 80.2% of postings,
and of the 13 ATSes that produced a `slow board` line in run `35193130454`, all 13 were
detail-pass scrapers and none of the 17 listing-only ones were.

Most of that traffic buys nothing. A posting the **Tech filter** (ADR-0017) drops is never
embedded, indexed or shown, so its description is fetched and discarded. Two gates already
existed — eightfold's, exact and listing-derived, and successfactors', approximate and
slug-derived — and with n=2 they had already drifted on three axes: their log lines disagreed,
their truncation handling disagreed, and eightfold honoured the pipeline-only default while
successfactors did not. Extending that by hand to nine call sites would extend the drift.

Measurements are in
[`docs/pipeline/2026-09-17_pre-detail-tech-gate-measurement.md`](../pipeline/2026-09-17_pre-detail-tech-gate-measurement.md).

## Decision

### 1. One seam: `BaseScraper.tech_detail_wanted(items, title_of, department_of=None)`

It takes the listing items and two accessors, and returns the subset still worth a detail fetch.
Behind it: the `is_tech` call, the per-Board log line, the `tech_gated_details` telemetry, the
kill switch, and the pipeline-only default. A call site learns one method.

Named to pair with its neighbour `needs_detail` rather than against it — the two answer different
questions about the same fetch ("would the index want this posting" vs "do we already hold its
text"), and zwayam and eightfold ask both.

`BaseScraper.attach_details` is its companion, because a gate turns ADR-0048's **alignment trap**
from a one-scraper hazard into a nine-scraper one: once the fan-out covers a subset,
`zip(items, results)` pairs each result with the wrong item, silently, since both are lists of the
right shape. Three scrapers had hand-written the same three lines; three copies of an alignment
trap is where the fourth gets it wrong.

### 2. The gate is on by default, off outside the pipeline

`HEADSTART_TECH_GATE=0` is the kill switch, matching `HEADSTART_ASYNC_FANOUT`'s shape. **On** by
default because two call sites were already gating in production; a default of off would have
switched both back off in the commit that routed them through the seam.

It stays off for any caller with `have_details is None` — every caller outside the pipeline.
Eight scripts construct scrapers directly and three read a Board's completeness as a health
metric, so a gate firing for them would have those three report a collapse that is not real. This
also **fixes a regression in #503**: successfactors' gate shipped unconditional, and the cost was
measurable — run `35193130454`'s `filter_tech` reported `successfactors 32,891/33,035 = 99.6%
tech`, because a non-tech posting never reached the corpus, so that ATS's real tech share had
stopped being readable from the pipeline's own data.

### 3. Who may take it, and who may not

The test is not "does the listing carry a title". It is **does the gate read the same two strings
`filter_tech` will read** — because `tech_filter` rule 4 promotes a vague title on a technical
department, so a gate blind to `department` drops those postings silently.

**Exact** (`parse` reads both fields off the listing, detail overrides neither, so no sampling is
needed — the proof is the call site): workday, smartrecruiters, apple, trakstar, zwayam, rippling,
eightfold.

**Measured tolerance** (re-check if the surface changes): successfactors, off the URL slug —
403/403 and 400/400 agreement on two structurally different tenants (ADR-0048's amendment); and
jazzhr, whose detail may override `department` — 1,748 postings across the ten Boards the corpus
says lean hardest on `department`, zero disagreements.

**Unmeasured, so not yet wired** ([#510](https://github.com/sarthakjain004/headstart/issues/510)):
taleo_enterprise, taleo_be, gem, phenom. All four carry `title` *and* `department` on the listing,
but `parse` lets the detail override the department, so they are the jazzhr shape and need the
same paired sample before shipping. Together ~2.2% of board-seconds, which is why they did not
block this. `meta` is a different case and is simply out: its sitemap URLs are bare numeric ids
(994 of 994 probed live), so it has no pre-detail signal of any kind — not a listing field, not a
slug.

**Excluded, on measured recall**: oracle (46.0%), zoho (47.4%), jobvite (40.6%), icims (25.8%),
bamboohr (13.6%) — the share of each ATS's *tech* postings a department-blind gate would drop,
over the real 2026-09-17 pre-filter corpus. Oracle is the one worth stating twice: #500 ruled it
out because `Category`/`JobFunction` are 0.0% on the listing and its ids are not slugs, but
`Title` **is** on the listing (600/600 across three hosts, probed live), so a title-only gate is
buildable. It must not be built. On `ejwl.fa.us2.oraclecloud.com` it drops 61.5% of that Board's
tech postings, and `eofd.fa.us6` loses 0.0% — a per-tenant recall cliff is worse than a uniform
one, not better. iCIMS is the same shape from the other side: its URLs *do* carry a clean title
slug, so successfactors' pattern transfers mechanically, and it still fails because the slug
recovers the title and nothing recovers the department.

## Consequences

**The truncation denominator does not move for the seven listing-derived scrapers**, contrary to
what #500 states as universal. They call `mark_truncated*` on the **listing** walk, before the
gate, and a gated posting still becomes a **Job** with `description=None` — the Board's list is
whole. The trap is real only where `parse` *drops* a Job that lost its detail, because the title
came from the detail: successfactors, icims, meta, jobvite. That is the same set where the gate is
approximate or impossible anyway, so the two problems never have to be reasoned about separately.

**A gated posting reaches the corpus with a null description**, which is how eightfold's gate
already behaved, and is why `filter_tech`'s per-ATS `kept%` stays meaningful for these seven —
unlike successfactors, where the posting never arrives at all.

**Measured saving: ~44–49% of all board-seconds**, discounting the request saving by the ~0.9
wall-clock ratio measured on the runner. On `ubuntu-latest`, 25 A/B pairs, **zero tech jobs lost
in every pair**: `workday:greystar` 1,597 → 12 requests and 14.4x, `apple:jobs.apple.com` 1.52x,
`smartrecruiters:soprasteria1` 1.55x (the least favourable). In production the same technique on
successfactors already delivered **3.03x** across four Boards.

**It does not fix the floor.** The scrape stage is floor-bound — 95–98% of the straggler shard is
one Board — and after the gate that floor is `oracle:ejwl` (1,354s) and `oracle:ejwl-dev7` (977s),
neither of which may take it, plus apple, which is 70.8% tech and gives up only ~29%. This removes
roughly half the scrape *work*, which the packer converts into a lower even-share term; the floor
needs Oracle's `categoriesFacet` idea (#500) or parking, not this gate.

**A/B/A/B, and churn is not loss.** `scripts/bench/tech_gate_bench.py` interleaves the arms
because two consecutive scrapes of one Board are not two draws from one distribution. It also
subtracts Board churn before calling anything a loss: a posting listed in one arm and not the
other is not evidence about the gate, and reading it as such produced two false `tech_lost=1`
reports before it was fixed — on `thehartford` and on apple, both on titles `is_tech` keeps.

## Alternatives considered

**A free function in `tech_gate.py`.** Testable with no scraper instance, but the per-Board log,
the telemetry and the pipeline-only default come back out to every caller — which is exactly the
set of things the two existing adapters had already drifted on. Shallower where depth was the
point.

**No seam, inline at each call site** — what eightfold and successfactors do today. Smallest diff,
and it is what produced the three-axis drift at n=2.

**Folding the gate into `fan_out`.** The gate is always immediately before a fan-out, so the
placement is tempting, but `fan_out` also carries page walks (amazon, google) and detail passes
that must not be gated. It would put a job-semantics concept into a generic transport helper.
