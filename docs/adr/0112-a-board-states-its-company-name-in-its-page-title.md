# ADR-0112: A Board states its company name in its page title — read it, don't infer it

**Status:** accepted · **Date:** 2026-09-07 · **Relates to:** ADR-0031 (the filter compiler that
serves `company`), ADR-0007 (the typed Job projection), ADR-0063 (the spare egress this request
deliberately cannot wall), ADR-0034 (the vendor-Board blocklist that already removes placeholder
names)

## Context

`BaseScraper.__init__` has always done `self.company = company or slug`. The slug is an
*identifier* — `wipro`, `1password`, `jobs.vodafone.com`, `nttltd` — and using it as a *display
name* was never a decision, just the fallback nobody revisited. Measured on the served table
2026-09-07: 150,626 of 318,003 rows are *literally* the slug (47.4%), and **186,798 — 58.7% —
are slug-shaped** once the Boards whose ledger name is itself an identifier are counted. The
ledger holds "wipro" and "gamuda"; Workday's holds "citi" and "dick-s-sporting-goods". The wider
figure is the honest denominator, and the narrow one flattered an early draft of this.

The ledger's `name` column fixes this where someone curated one, which does not scale to thousands
of Boards.

## Decision

Read the name from the Board's own page `<title>`, for the ATSes that state it there uniformly
enough to strip — and for no others.

`headstart.company_name` holds the per-ATS patterns and the rejection rules;
`BaseScraper.resolve_company()` fetches the page named by a new `board_page()` hook and applies
them, between `fetch_raw()` and `parse()` in `fetch()`. `parse` stays pure, which is what keeps the
parse tests running against recorded fixtures.

**Which ATSes is a measurement.** 30 live Boards per ATS before any code shipped, and larger
samples since where the first one proved too small to trust — the sample size is part of each row:

| ATS | title shape | yields a name |
| --- | --- | --- |
| ashby | `{Name} Jobs` | ~92% (n=120) |
| eightfold | `Careers at {Name}` / `{Name} Careers` | ~93% (n=100) |
| ripplehire | `{Name} Careers \| Latest jobs at …` | ~94% (all 52 Hiring Boards) |
| lever | `{Name}` — no wrapper at all | ~88% (352/400) |
| keka | `Careers at {Name}` / `{Name} Careers` | ~11% (92 of 819, a full census) |

successfactors, darwinbox and freshteam scored **0/30 against the registered patterns**. For
darwinbox and freshteam that is genuine — they render client-side and serve nothing to read.
Successfactors is the interesting exclusion: it *does* serve titles ("Careers at Bachem"), but
they are marketing copy in several languages with no shared wrapper ("Life@MOHH - people, culture,
and values | MOHH", "Trabaja en Volaris"), so a pattern wide enough to catch the third mangles the
first two. That is a quality bar, not a cost one.

**Keka was scored 0/30 too, and that was simply wrong.** A 40-Board sweep found 5 serving a
`<title>`, and a full 819-Board census settled it at **92 names from 103 titles — ~11%** (the rest
render client-side), every one in a wrapper eightfold's patterns already read ("Careers at Skylark
Drones", "Entropik Careers"). It is wired. Be clear about the size: ~1,600 of 1,429,908 ledger
jobs, **~+0.11%**. It earns its place not on volume but on cost and floor — the page returns in
0.12s, and *every* keka Board serves a slug today, so the downside is a request that yields
nothing eight times in nine.
Successfactors stays out on a different and firmer ground: its titles are real but heterogeneous
marketing copy in several languages, so no single wrapper strips them safely.

**Workday is excluded, and it is the largest single block** (51,861 rows). Its board page is an
empty SPA, and neither its listing nor its detail response carries a name — verified by driving the
real scraper. Its public job page's JSON-LD *does* carry `hiringOrganization`, but that is the
**per-posting legal entity**, so it varies **within a single Board** and is frequently worse than
the slug — nvidia alone returns "IL00 Mellanox Technologies, Ltd.",
"IN01 NVIDIA Graphics Bengaluru" and "2100 NVIDIA USA" across three postings, and `nc` returns
"Adult Correction" and "Department of Transportation". Its board SPA does serve an `og:title`, but
sampled live that is correct on well under half the Boards carrying one and otherwise junk these
rules would accept ("Careers", "Job Opportunities"). A name we invent is worse than a slug we admit
to.

## Consequences

**It never substitutes a non-name for a slug, which is a narrower promise than "it can only
improve".** Every failure path leaves `self.company` untouched: no `board_page`, a request that
raises, a non-200, a title no pattern reads, a title that is exactly the slug. A ledger-supplied
name outranks a page title and skips the request entirely. Scrapers that do not opt in make
**zero** extra requests — measured, not assumed.

What the rules cannot promise is that the name a Board states is the one a user would search for.
A 452-Board sweep across all five wired ATSes found two recurring classes. These are **examples,
not the complete set** — the sweep was a sample, and more Boards of both shapes certainly exist.

An acronym or short form less findable than the slug: `ripplehire:ltimindtree` serves **"LTM"**,
`eightfold:puertoricogov.eightfold.ai` serves **"OATRH"**. And a parent or acquiring entity
displacing a familiar brand: `eightfold:gotinder.eightfold.ai` -> "Match Group", `keka:abcoffee`
-> "Brewbay Innovations", `lever:silhouette` -> "DNAM Brands", `lever:developintelligence` ->
"Pluralsight", `eightfold:grupobimbo.eightfold.ai` -> "Bimbo Bakeries USA".

The narrowed floor held across all 418 names that sweep resolved: not one was a non-name.

That last class is the same shape as the `hiringOrganization` field Workday is excluded over, so
the distinction has to be stated rather than assumed. It is this: Workday's legal entity varies
**per posting inside one Board**, so no single value is even self-consistent; these are one stable
name per Board, and each is the company's own claim about itself. A stable parent name is a
defensible answer to "who is hiring"; three different legal entities on three postings of one
Board is not. An earlier draft of this ADR asserted no Board could end up worse. That was wrong.

**A page label is never a company name.** `lever:destinationknot` titles itself "Destination
Careers" — the page-label shape this ADR refuses Workday's `og:title` over, arriving through the
front door. Every pattern that models "{Name} Careers" strips it, so a title still ending that way
means the title wore the wrapper twice and what is left is a label. Anchored at **both** ends,
because both happen: `lever:destinationknot` serves "Destination Careers" (trailing, reachable
because lever's pattern matches anything) and `keka:enpro` serves "Careers at Careers at Enpro
Industries" (leading, which a tail-only rule served to users as the employer until round 6 caught
it). Anchored rather than matching anywhere, because "Jobsoid" and "Careers24 Group" are names.

It costs recall, and the honest number is not zero: across 400 lever and all 819 keka Boards it
refused `enpro`, and an independent 700-Board lever sweep refused `lever:pmaconsultants` ("PMA
Consultants Careers", 29 real postings). Refusing is still the right trade — stripping the word
instead would turn "Destination Careers" into "Destination", a confident wrong name, where
refusing costs only an upgrade and leaves the slug.

**A vendor's own name is never a company.** `ripplehire:trampolinetech` titles itself "RippleHire
Careers | …", which shipped as the employer until a rule rejected it — the failure ADR-0034
blocklists Boards for, arriving through a title instead.

**One extra request per Board, and it is the cheapest possible one.** `attempts=1`, so it never
spends the retry ladder (three attempts against a walled origin is ~90s for one Board), and
`marks_wall=False`, so a 403 on an HTML careers page can never be what routes an entire ATS onto
the spare egress. Measured added latency per Board, median of three: 0.05s (ashby), 0.12s
(keka), 0.20s (ripplehire), 0.40s (lever), 0.66s (eightfold); worst single request 1.13s.

**Existing rows are renamed without a backfill, but not quickly.** `company` is in
`doc_prep.META_FIELDS`, so `update_meta` re-observes it and `index sync` rewrites the stored row —
no migration needed. The horizon is the scrape cadence, not one run: a run's slice is ~20k Boards
out of the Scrapable set, so full propagation takes many runs and is unmeasured. Say "converges
over days", not "fixed on the next run".

**`company` is now two things at once**, which the README says plainly: a real name where a Board
states one, the ATS slug everywhere else. That is honest rather than tidy, and it is the shape any
incremental fix to this has.

**These are ceilings, not achieved coverage.** Every figure below counts rows on a Board whose
ATS is wired — not rows that actually gain a name. The per-ATS hit rates run 88-94% (and keka's
~11%), so the realised share lands near 27.5%, not 31.6%. Quote the ceiling only as a ceiling.

**Coverage is 5 of the affected ATSes.** The four measured on the served table are 59,123 rows —
39% of the narrow denominator, but **31.6% of the 186,798 slug-shaped rows**, and the second
number is the one to quote. Keka was wired after that measurement and its *served* rows were never
counted, so 31.6% excludes it; on the ledger it is ~1,820 jobs, which moves the figure by well
under a point. Do not read 59,123 as a five-ATS number. The remainder
is not a rollout waiting to happen; it needs a per-ATS source that the evidence does not currently
support, and Workday's case shows that "some name" is not automatically better than none.


## Two things this change surfaced

**Three vendor Boards had to be blocklisted, not renamed.** Reading titles is also a way of
*finding* fake tenants: `ripplehire:itcinfotech` titles itself "ITC Infotech Demo" and
`ripplehire:labs-axisqa` is a QA tenant with 1,226 postings. The sharp one is
`ripplehire:tenant1-mph`, which titles itself "Mphasis" — so this change would have *stopped it
looking fake*, turning a visibly-bogus slug into a real employer's name in front of users. A
title rule cannot catch that; only ADR-0034's blocklist can, and all three went there. The
`_PLACEHOLDER` rule catches the self-declaring ones a blocklist has not reached yet.

**Lever's board page is assumed to be `jobs.lever.co`.** A Board on Lever's EU host resolves no
name and keeps its slug — the same no-worse-than-today floor as every other miss, recorded here
because the next person to see a Lever Board unnamed should look here first.


## Known misses, recorded rather than fixed

`ripplehire:7-eleven-gsc` titles itself "7 - Eleven Careers | …", which `_SEPARATORS` refuses over
the `" - "` it contains — a real employer losing a real name. The floor holds (it keeps its slug)
and the separator rule earns its place elsewhere, so this is left as a recall miss rather than
narrowed around one Board.

`ripplehire:labs-mph` is in the same `labs-` family as the newly blocklisted `labs-axisqa` and
looks like another RippleHire QA tenant, but it currently 502s and exposes no title, so there is
no evidence to blocklist it on. Named here so the next person meets it with the context.
