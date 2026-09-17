# ADR-0166: Gate the detail pass on the tech filter, behind one seam

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
from a one-scraper hazard into one every gated call site carries: once the fan-out covers a subset,
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

**Exact** (`parse` reads both fields off the listing, the detail overrides neither, so no sampling
is needed — the proof is the call site): workday, smartrecruiters, apple, trakstar, zwayam,
eightfold.

**Measured tolerance** (re-check if the surface changes):

- successfactors, off the URL slug — 403/403 and 400/400 agreement on two structurally different
  tenants (ADR-0048's amendment).
- jazzhr, whose detail page may override `department` — 1,748 postings across the ten Boards the
  corpus says lean hardest on `department`, zero disagreements.
- **gem** and **phenom**, measured for [#510](https://github.com/sarthakjain004/headstart/issues/510):
  1,304 postings over gem's 40 most department-dependent Boards and 15,321 over all ten of
  phenom's, zero disagreements on either. Neither is quite jazzhr's shape, and the difference is
  worth stating because #510 grouped all four as "the detail overrides `department`": gem's detail
  is a *fallback* on both fields (`row.title or detail.title`, listing department first), the
  rippling shape; phenom's is a fallback on `department` but a true **override** on `title`
  (`detail.title or row.title`). That override arm was probed where it can fire — on
  `careers.dhl.com`, the one Board leaving `category` empty on most rows (7,269 of 9,515 on the
  live listing), 300 of those rows sampled at random supplied **0** departments the listing
  lacked and **0** different titles.
- **rippling**, which reads as exact and is not: `parse` is
  `_department_of(it) or _department_of(detail)`, so the detail *is* a fallback and can state a
  department the gate never saw. It is safe only because nothing ever does — `department` is
  populated on **0 of 1,515** rippling postings in the 2026-09-17 corpus, listing and detail
  alike, so the fallback is inert and the gate is title-only on both sides of the seam. A
  tenant that started stating one on the detail alone would break that silently, which is why
  this is a measurement with a date on it and not a property of the call site.

**Deferred**: ripplehire. Its `department` is detail-only, so a gate there is title-only, and
that measures at 1.8% recall loss — 7 of 395 tech postings, a real loss rather than a rounding
error. At 0.3% of board-seconds it does not pay for the per-tenant sample that would be needed to
accept it. Revisit if ripplehire grows.

**Measured and refused** ([#510](https://github.com/sarthakjain004/headstart/issues/510)):
taleo_enterprise and taleo_be. These two really are jazzhr's shape — `detail.get("department") or
listing` — so both were sampled the jazzhr way, and unlike jazzhr both disagree.

- **taleo_enterprise: 68 of 133 tech postings lost (51.1%), on 10 of 10 Boards.** Its listing is
  a tenant-configured result *table*, and `_column` can only read a department where the tenant
  put one in it: many career sections do not, so the gate sees `None` while `parse` takes
  `reqlistitem.jobfield` off the detail page. On `dasstateoh.taleo.net/careersection/oh_ext` that
  costs 15 of 23. A uniform refusal, not a per-tenant cliff, but a refusal either way.
- **taleo_be: 5 of 351 tech postings lost, all 5 on one of 10 Boards** — 5 of that Board's 16.
  `_listing` reads the header fields *positionally* (`fields[0]` department, `fields[1]`
  location), and on `phf.tbe.taleo.net/phf01/…?org=JSHR6E&cws=53` the tenant emits them the
  other way round, so the listing's "department" is a location ("NH, Nashua", "Hal Far, Malta")
  and the detail's labelled `Department` is what `parse` actually serves. That is exactly the
  per-tenant recall cliff this ADR refuses oracle for, at 0.2% of board-seconds. The positional
  read is a pre-existing listing defect, not one this gate introduces — the detail pass has
  always corrected it — and it is left alone here.

`meta` is a different case and is simply out: its sitemap URLs are bare numeric ids
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
unlike successfactors, where the posting never arrives at all. On **gem** it is null in three
fields, not one: `posted_at` and `compensationHtml` are detail-only there, which is exactly why
gem refuses ADR-0048's skip. The gate is still right to fire — a posting `filter_tech` drops has
no `posted_at` worth keeping either — but the blanket sentence above has that exception.

**gem and phenom add 16 more A/B pairs** (run `35219838067`), `tech_lost=0`, `desc_lost=0` and
`churned=0` on all 16. `phenom:careers.allianz.com` 1,691 → 252 requests and 4.1–5.2x;
`gem:coupa-software-inc-ats-1` 4 → 3 requests and 0.94–1.06x, because gem batches 100 details per
request and has almost nothing left for this gate to save.

**Measured saving: ~44–49% of all board-seconds**, discounting the request saving by the ~0.9
wall-clock ratio measured on the runner. On `ubuntu-latest`, **51 A/B pairs across three runs**,
`tech_lost=0` on 49 — the two exceptions both predate the churn fix below and are both churn, on
titles `is_tech` keeps. `workday:greystar` 1,597 → 12 requests and 14.4x, `workday:jll` 4.1–5.3x,
`apple:jobs.apple.com` 1.52x, `smartrecruiters:soprasteria1` 1.55x (the least favourable). In
production the same technique on successfactors already delivered **3.03x** across four Boards.

**One Board shows a second loss signal that is not yet explained.** `jll` returned `desc_lost` of
2, 47, 7 and 59 across the four pairs it produced — tech Jobs described in the control arm and not
in the gated one — against `desc_lost=0` on every pair of every other Board. It is not the gate
declining work: on an exact-gate Board a tech posting is always in `wanted`, so it is always
fetched, and `tech_lost=0` across all 51 pairs agrees. But four positive draws out of four is not
symmetric flakiness either, and the harness had a confound that fits it exactly — it ran the gated
arm second in every repeat, immediately after the control had put 3,466 requests through the same
origin. The harness now counterbalances arm order by repeat. Until that has run on jll, this is
an open question about one flaky Board, recorded rather than closed.

**It does not fix the floor.** The scrape stage is floor-bound — 95–98% of the straggler shard is
one Board. In run `35193130454` that floor was `jazzhr:amadaseniorcarenorthshore` (850s),
`oracle:ejwl` (1,354s), `apple:jobs.apple.com` (1,179s) and `oracle:ejwl-dev7` (977s). This change
reaches two of the four: **jazzhr**, which was the slowest Board of that run, and **apple**, which
gives up only ~29% at 70.8% tech. Neither Oracle Board can take the gate — and `oracle:ejwl` was
separately **parked** on `main` in `c5984f38` while this was in review, for an unrelated WARP-path
slowdown, which takes it off the floor without addressing the class. `ejwl-dev7` remains. So this
removes roughly half the scrape *work*, which the packer converts into a lower even-share term,
and takes one floor Board with it; the Oracle class still needs `categoriesFacet` (#500) or more
parking.

**A/B/B/A, and churn is not loss.** `scripts/bench/tech_gate_bench.py` interleaves the arms
because two consecutive scrapes of one Board are not two draws from one distribution, and
alternates which arm leads each repeat (`arm_order`) because interleaving repeats does not control
the order *within* one — see the jll paragraph above for what that cost. It also
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
