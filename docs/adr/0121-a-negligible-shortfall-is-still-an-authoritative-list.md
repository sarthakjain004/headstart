# ADR-0121: A negligible shortfall is still an authoritative list — let the per-Job grace period have it

**Status:** accepted · **Date:** 2026-09-09 · **Amends:**
[ADR-0053](0053-scope-eviction-on-scrape-outcome.md) (adds the tolerance its gate never had) ·
**Relates to:** [ADR-0083](0083-evict-only-on-a-second-consecutive-absence.md) (the per-Job
mechanism this hands small shortfalls to),
[ADR-0088](0088-a-lost-detail-is-not-a-truncation.md) (which priced this exclusion and declined to
add a Workday arm to it),
[ADR-0101](0101-remove-the-collapse-guard-the-grace-period-is-the-line.md) (which left these two
as the only withholding mechanisms)

## Context

ADR-0053's gate is binary. A Board whose scraped list came back **any** amount short is
Unauthoritative and leaves the eviction scope entirely — and that exclusion has **no bound and no
drain**, as ADR-0088 already recorded: a Board short on every run never re-enters scope, and its
closed postings are served indefinitely.

The live ledger says what "any amount" means in practice. From
`data/state/unauthoritative_boards.json` (133 Boards, pulled 2026-09-09):

| Board | shortfall | read |
| --- | --- | --- |
| `successfactors:careers.te.com` | 1 of 2,130 job pages unreadable | 99.953% |
| `successfactors:careers.bureauveritas.com` | 1 of 2,032 | 99.951% |
| `successfactors:basf.jobs` | 1 of 708 | 99.859% |
| `successfactors:careers.wipro.com` | 7 of 4,757 | 99.853% |
| `eightfold:careers.qualcomm.com` | got 1,919 of 1,932 postings | 99.327% |

Each of those Boards leaves the eviction scope, on every run, to protect one id.

Measured across the four real pipeline runs of 2026-09-09 (all on SHA `fd15455` —
`34316866965`, `34321068300`, `34327339789`, `34332773221`; a fifth same-day run was a
stand-down with no merge job): **52–66 Boards excluded per run, withholding 2,881–3,063
eviction-candidate rows.** The accretion is visible on the worst offender, whose shielded rows
climb monotonically across the four: `successfactors:careers.hcltech.com` at 1,525 → 1,530 →
1,533 → 1,538.

### The two ADRs now overlap, and only one of them drains

This is the substance, not the row count.

**ADR-0053 (2026-08-13) was designed before ADR-0083 (2026-08-23) existed.** When it shipped, an
id absent from one scrape was deleted immediately and permanently — so removing the whole Board
from the eviction scope was the *only* way to protect a posting whose page happened not to load.
Ten days later ADR-0083 made a first absence cost nothing: a missing id is recorded Unconfirmed
and evicted only if the **next** scrape of that Board misses it too.

The two now cover the same ground at different granularities:

- **ADR-0053 is per-Board.** One short page ⇒ the whole Board's rows are untouchable.
- **ADR-0083 is per-Job.** One missing id ⇒ that id is withheld for a scrape.

A 1-in-2,130 shortfall is precisely the shape the per-Job mechanism exists for. Using the
per-Board instrument on it is not merely heavier than necessary — it is heavier in the one
direction that never reverses, because ADR-0083 drains (an id either reappears or is evicted on
the second look) and ADR-0053 does not.

## Decision

**A shortfall a scraper can measure against the Board's own stated total leaves the list
authoritative when at least `MIN_AUTHORITATIVE_SHARE` of it was read; below that the Board is
Unauthoritative exactly as before.** The few missing ids go to ADR-0083.

`BaseScraper` gains one method beside `mark_truncated`:

- `mark_truncated(why)` stays the **unconditional** verdict, for a shortfall that is
  *unreachable* (a hard cap) or *unmeasurable* (no stated total).
- `mark_truncated_unless_negligible(read, expected, why)` is the **measured** one. It applies
  the tolerance and passes `why` through unchanged, so `unauthoritative_boards.json` reads as it
  always did.

`MIN_AUTHORITATIVE_SHARE = 0.99`, a module constant in `base.py` — deliberately **not** a class
attribute, so a scraper cannot quietly hold itself to a different bar.

### Why 0.99

Measured on run `34327339789`'s own per-Board breakdown — the **full** breakdown from the merge
log's INFO lines (44 Boards holding all 3,055 shielded rows), not the warning's 10-Board sample:

| threshold | rows released (of 3,055) | share |
| --- | --- | --- |
| ≥90.0% | 2,794 | 91.5% |
| ≥95.0% | 1,259 | 41.2% |
| ≥98.0% | 1,246 | 40.8% |
| **≥99.0%** | **1,204** | **39.4%** |
| ≥99.5% | 1,076 | 35.2% |

The same shape holds on all four runs (≥99% releases 1,188 / 1,209 / 1,204 / 1,242 rows —
39–41% each time).

0.99 is the knee. Loosening to 0.95 buys **55 more rows, +1.8pp**, for five times the tolerated
loss: the 95–99% band is nearly empty, so there is almost nothing down there to buy. Tightening
to 0.995 costs 128 rows. Independently, Oracle's pre-existing `_SLACK_PER_PAGE = 2` on a 200-row
page is already a ~1% per-Board tolerance arrived at separately, which is weak corroboration that
this is the right order of magnitude.

**`successfactors:careers.hcltech.com` is deliberately out of reach and out of scope.** On run
`34327339789` it read 91.20% (995 unreadable of 11,265) and was, alone, 1,533 rows — 50.2% of that
run's shielded
set, and 1,533 of the 1,590 rows that separate the 39.4% row from the 91.5% row above.
Chasing it by
setting the threshold near 0.90 would declare a Board authoritative while **one Job in eleven is
missing**, and send those ids to eviction. It is a scraper defect, not a gate-calibration
problem: its unreadable pages grow 971 → 985 → 995 → 1,022 across the four runs against a flat
~11.3k board (90.957% on the last of them), and it shares a root cause — detail-fetch throughput
and failure on one host — with the pipeline's #1 scrape straggler. It needs that fix, not a
looser gate.

### The benefit is ~40%, and it is worth stating why it is not more

A ≥99% tolerance releases **about 40%** of the shielded rows, not "most" of them. The reason is
concentration, not calibration: one Board is half the total and fails any safe threshold, and a
further 126–207 rows per run sit behind hard caps that must never be released. The honest framing
is that **a tolerance releases ~40% cheaply and safely, and the other ~50% is one board's
detail-fetch failure rate.**

### Two classes no share can rescue

- **Hard caps.** `oracle:*.oraclecloud.com` reads at most 10,000 because "the API serves no offset
  past 10,000"; a Workday query can cap "at 2,000 with no facet left to split"; eightfold and
  Oracle both have their own page ceilings. The unread remainder is unreachable *identically on
  every run*, so it is not a transient miss and ADR-0083 can never resolve it — a Board stating
  10,050 that reads exactly 10,000 is 99.5% complete and must still be excluded. These call
  `mark_truncated` directly.
- **Shortfalls with no total.** 55 of the ledger's 133 Boards carry no read ratio at all. 54 are
  raised or never-asked scrapes — 24 `HTTPError`, 21 keka `no org uuid on the portal`, 4
  `CertificateVerifyError`, 3 `ConnectionError`, 2 `JSONDecodeError`. (The 55th,
  `workday:kindercare/externalcareersite`, has no ratio only because its reason names the facets
  rather than counts; it is the 2,000-facet cap and belongs to the hard-cap class above. The
  breakdown sums to 54, and an earlier draft of this ADR misfiled it here.) A raised scrape
  produces no list to measure. So does a
  surface that came up short without knowing its own total: SuccessFactors' RSS stream reports
  "aborted 31,457,280 bytes in" because, in its own words, there is "no total to compare against
  in a feed". A ratio here would be fabricated. These call `mark_truncated` too.

Both classes are covered by tests, including the case that matters most — a hard cap at 99.5%
read still marking the Board Unauthoritative.

## The counter-argument, and the position

**The honest objection.** ADR-0083 evicts on the *second* consecutive absence. If a job page is
**persistently** unreadable rather than transiently, the id is missed every scrape, so the
two-strike rule fires and the posting is evicted **even though it is still live**. ADR-0053's
per-Board exclusion, whatever else is wrong with it, does not have that failure mode. This
change therefore trades a permanent, unbounded shield for a small, real eviction risk.

**The reappear rates say the grace period rescues little of what it withholds**, so the
objection cannot be waved away with "ADR-0083 will catch it". From the same four runs' own
`grace period:` lines:

| run | carried in | reappeared | unconfirmed again | of those re-looked-at |
| --- | --- | --- | --- | --- |
| `34316866965` | 752 | 26 | 420 | 26/332 = 7.8% |
| `34321068300` | 773 | 17 | 435 | 17/338 = 5.0% |
| `34327339789` | 739 | 27 | 414 | 27/325 = 8.3% |
| `34332773221` | 823 | 16 | 380 | 16/443 = 3.6% |

Against the carried-in cohort that is 1.9–3.7%; against the cohort whose Board actually got a
second authoritative look — the meaningful denominator — it is 3.6–8.3%. Either way, **the large
majority of withheld absences are not transient.** A first absence usually means the posting
really is gone.

**Measured against the live ATS.** Whether that is good or bad depends entirely on what the
unreadable pages *are*, and that is an empirical question, so it was measured rather than
reasoned about. Eleven Boards this change newly declares authoritative were re-scraped live on
2026-09-09 and every page whose detail fetch came back empty was then fetched again by hand and
read:

| Board | listed | unreadable | read | verdict on the lost pages |
| --- | --- | --- | --- | --- |
| `jobs.pirelli.com` | 128 | 1 | 99.22% | closed |
| `jobs.bt.com` | 178 | 1 | 99.44% | closed ("position has been filled") |
| `careers.gamuda.com.my` | 211 | 1 | 99.53% | closed |
| `careers.kbc-group.com` | 261 | 1 | 99.62% | closed |
| `jobs.dormakaba.com` | 344 | 1 | 99.71% | closed |
| `jobs.danfoss.com` | 676 | 1 | 99.85% | closed |
| `basf.jobs` | 709 | 1 | 99.86% | closed |
| `careers.a-star.edu.sg` | 715 | 1 | 99.86% | closed |
| `jobs.bbraun.com` | 907 | 3 | 99.67% | closed (3 of 3) |
| `jobs.standardchartered.com` | 1,099 | 8 | 99.27% | 7 closed, 1 indeterminate |
| `jobs.mtu.de` | 401 | 0 | 100% | — short on its last pipeline scrape, whole today |

**11 Boards, 19 lost pages; 18 of the 19 were postings the tenant has already closed.** Each
200s and redirects to a "this position is not available / has been filled" page — the parser
returns `None` for *both* "we could not read this page" and "the tenant says this posting is
closed", exactly the classifier conflation ADR-0088 identified. So on this sample ADR-0053 was
overwhelmingly shielding **dead rows**, which is precisely the accretion complaint.

`jobs.mtu.de` is its own small datapoint: the ledger has it 1 short, and today it reads whole.
That shortfall was transient, and under ADR-0053 it still cost the Board its entire eviction
scope for a run.

**The sample's limits, stated rather than glossed.** `basf.jobs` is one of the Boards named in
the table at the top of this ADR, so this is not only convenient small Boards — but
`careers.te.com` and `careers.wipro.com` were **not** sampled: at 2,130 and 4,757 detail pages
they were too slow to finish here. The finding therefore rests on Boards of 128–1,099 pages and
is not proven on the largest ones.

**The eightfold arm is a different risk and was measured separately.** SuccessFactors loses an
unreadable *detail* page; eightfold loses a *listing* entry the PCSX replica never dealt — and
that missing id is plausibly a live, fully-populatable posting, so the rebuttal below (a Job whose
page never reads cannot be populated) does not cover it. What matters there is whether the same id
is missed *twice consecutively*, since that is all ADR-0083 needs. Three Boards the ledger records
as short by one posting each — `ascendion` (190 of 191), `fortive` (298 of 299), `trinet` (185 of
186) — were crawled twice, back to back, on 2026-09-09:

| Board | crawl A | crawl B | in A not B | in B not A | truncated |
| --- | --- | --- | --- | --- | --- |
| `ascendion.eightfold.ai` | 196 | 196 | 0 | 0 | neither |
| `fortive.eightfold.ai` | 297 | 297 | 0 | 0 | neither |
| `trinet.eightfold.ai` | 186 | 186 | 0 | 0 | neither |

**Be precise about what this does and does not show, because it is weaker than it first looks.**
All three crawls reached the Board's stated total, so *no shortfall was reproduced at all* — and
once both crawls are complete, "zero disagreement" follows automatically and proves nothing on its
own. What the probe does establish is that the shortfall the ledger recorded on these Boards is
**not a stable property**: the same crawl that came back short in the pipeline run now comes back
whole, twice. That is consistent with ADR-0053's own #142 replica-flutter context and rules out a
fixed set of unreachable ids, which is the thing that would defeat the grace period.

What it does **not** establish is the quantity the argument actually turns on — how often the
*same* id is missed on two consecutive scrapes. Reproducing that needs a Board caught mid-flutter,
and none of the three obliged. So the eightfold arm is suggestive, not decisive, and it is the
weakest evidence in this ADR.

The 1 indeterminate case is the counter-argument made concrete and is reported rather than
buried: a Korean-language Standard Chartered posting whose page is CSB-rendered
(`body{display:none}`, content built by JS), so no static fetch can classify it and it may well
be live. That is a known SuccessFactors gap — CSB-only tenants are documented as unsupported —
and the remedy is a CSB parser, not the eviction gate.

**Position.** Ship the tolerance. Three things carry it:

1. On measurement, 18 of 19 (95%) of the ids it exposes to eviction are already-closed postings
   that ADR-0053 was shielding forever.
2. For the residual, the rebuttal holds: **a Job whose page never reads cannot be populated.**
   Every SuccessFactors field comes from the job page, so `parse` drops it; it would be served
   with null fields and no description, and the ADR-0050 store has no answer for it either. A row
   that can never be refreshed and can never be enriched is not obviously better served than
   evicted, and if the page becomes readable again the posting simply returns as a new listing.
3. The cost is symmetric and bounded, whereas the status quo is not. Getting this wrong evicts a
   handful of unpopulatable rows that can come back; leaving it as-is serves thousands of closed
   postings forever, with no mechanism that can ever reach them.

The residual risk is real and is accepted knowingly, not dismissed.

## Where the policy lives, and why there

The tolerance is one constant and one method on `BaseScraper`. Four placements were considered.

**A. On the scraper base class — chosen.** The counts are known exactly where the crawl gives up,
and — decisively — **only the scraper knows which kind of shortfall it has.** A hard cap and a
transient miss are indistinguishable downstream: "read 10000 of 11549 — the API serves no offset
past 10,000" and "read 166 of 171 — the rest is unread" are both prose in a `str`. Any consumer
would have to match wording to tell a never-tolerable cap from a tolerable miss, which is a
parser built on sentences the scrapers are free to reword. At the scraper they are already
*separate code paths*, so the distinction is expressed by which method the call site calls, and
is greppable.

**B. In `scrape_join`,** where the outcome maps are unioned into `unauthoritative_boards.json`.
This is the seam ADR-0053 names, and in the abstract it is the tidier home. Rejected: `truncated`
is a free-text `str`, so this needs it to become a structured record carrying counts *and* a
never-tolerate flag, touching every one of the ~15 scrapers that set it — a much larger change
for the same behaviour, and it still ends up asking each scraper to classify its own shortfall.
Worth revisiting if the reason strings ever need to be machine-read for another purpose.

**C. In `index sync`,** the consumer. Rejected for B's reason plus a worse one: it would put the
policy furthest from the evidence, and `sync` would be re-deriving counts from prose.

**D. Per-scraper thresholds.** Rejected outright. The gate must not mean different things on
different ATSes, and an ATS-local constant is how they silently diverge. This is also why the
constant is module-level rather than an overridable class attribute.

## Consequences

- **The scope exclusion drains for the first time on the common case.** ~40% of the shielded rows
  per run re-enter eviction scope, and a Board short by a page or two no longer accretes dead
  rows forever.
- **`unauthoritative_boards.json` reads exactly as before.** Reasons pass through verbatim; only
  membership changes.
- **A tolerated shortfall logs one INFO line** naming the share and the ids handed to ADR-0083.
  INFO, not WARNING: it fires once per tolerated Board per run, and under Actions a WARNING is an
  annotation against a hard quota (ADR-0039). But it is logged, because a tolerance nobody can
  see working is the same blindness ADR-0053's own row-count line had to be added to fix.
- **Two tolerated shortfalls on one Board compound.** A listing 99.5% complete feeding a detail
  pass 99.5% complete is ~1% lost overall with neither call tripping the gate. The worst case is
  bounded at roughly twice the tolerance and no accumulator is introduced for it; if a Board is
  ever observed in that state it is a reason to revisit, not to pre-build machinery.
- **Oracle's behaviour is unchanged today.** Its `_SLACK_PER_PAGE` already absorbs almost exactly
  1%, so anything that reaches the new call is already under 0.99 and still truncates. Routing it
  through the central method anyway is the point of the change — the two can no longer drift —
  but this ADR should not be read as claiming a row of Oracle benefit.
- **One latent hole had to be closed for the hard-cap guarantee to be true.** eightfold's
`_MAX_INDEX_CHILDREN` bounds how much of a sitemap index is followed, and what it drops never
reaches `listed` — the denominator the detail pass measures against. A Board whose index is twice
the cap could therefore lose half its postings and still report every *listed* detail as read,
which is a hard cap reaching the tolerance by the back door. Before this change a stray unreadable
detail page usually excluded such a Board anyway; that accident is gone, so the cap now marks the
Board itself. Found by review, not by the ledger — no Board is currently over the cap.

**One policy, but not yet every call site.** `icims`, `jobvite`, `smartrecruiters` and `zwayam`
also report a measured shortfall and keep calling `mark_truncated` unconditionally. That is a
measured boundary rather than an inconsistency: all 13 excluded `icims` Boards read **0.000%** (a
total detail-pass failure — a broken scrape, not a marginal shortfall), the single
`smartrecruiters` Board raised `HTTP 401` and states no total, and `jobvite` and `zwayam` have no
excluded Boards at all. The gate would never fire on any of them, so converting them would change
nothing and is deferred until a Board of theirs is seen coming back marginally short.

**Workday is untouched**, by ownership rather than by judgement. `workday.py:1080` reports a
  measured listing shortfall in the same shape and would qualify; adopting `mark_truncated_unless_negligible`
  there would release a further 37–49 rows per run — 1.2–1.6% of the shielded set, about 3–4% on
  top of what this change releases. Left as a follow-up.

### Follow-ups this deliberately does not do

- **`successfactors:careers.hcltech.com`'s detail-fetch failure rate** — half the shielded rows,
  and the same root cause as the #1 scrape straggler. Fixed at the scraper, not the gate.
- **Split SuccessFactors' `None`** into "unreadable" and "the tenant says this is closed". The
  live sample says 18 of 19 are the second, and a closed posting is not a shortfall at all — this
  is ADR-0088's Option-A classifier defect, and fixing it would shrink the numerator rather than
  tolerate it.
- **keka: the 21 Boards reporting `no org uuid on the portal — the jobs array was never
  requested`.** Not a threshold problem; the scrape never asked for a list. (Earlier drafts of
  this ADR, and the review that prompted it, called this an *eightfold* class. It is keka —
  21 of 21, checked against the ledger.)
- **icims: 13 Boards whose detail pass fails outright** — `1515/1515`, `1374/1374`, `12/12` job
  pages unreadable. Every one reads **0.000%**, so no tolerance can ever release them; it is a
  broken scrape, not a shortfall.
- **teamtailor: 12 excluded Boards**, 10 raising `HTTP 400` and 2 raising `HTTP 404`.

  Those three classes are 46 of the 133 excluded Boards. None is a gate-calibration problem and
  this change neither touches nor should touch them.
- **A CSB-rendered SuccessFactors detail parser**, which is what the one indeterminate page above
  actually needs.
