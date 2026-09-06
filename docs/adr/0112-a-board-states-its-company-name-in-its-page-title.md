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

**Which ATSes is a measurement.** 30 live Boards were sampled per ATS before any code shipped:

| ATS | title shape | yields a name |
| --- | --- | --- |
| ashby | `{Name} Jobs` | 28/30 |
| eightfold | `Careers at {Name}` / `{Name} Careers` | 28/30 |
| ripplehire | `{Name} Careers \| Latest jobs at …` | 28/30 |
| lever | `{Name}` — no wrapper at all | 25/30 |

successfactors, keka, darwinbox and freshteam scored **0/30 against the registered patterns**,
which is not the same as having nothing to read: a later sweep found roughly one keka Board in
eight already serving an eightfold-shaped title ("Entropik Careers"), and successfactors serves
parseable ones too ("Careers at Bachem"). They are excluded because a hit rate that low buys a
request on *every* Board of the ATS for a name on few of them.

**Keka was that revisiting, and it changed the answer.** A 40-Board sweep found 5 serving a
`<title>` — 12.5%, not the 0/30 the first draft asserted — and every one of the five in a wrapper
eightfold's patterns already read ("Careers at Skylark Drones", "Entropik Careers"). It is wired.
Be clear about the size: ~1,820 of 1,429,908 ledger jobs, **+0.13%**, across ~102 of 819 Boards.
It earns its place not on volume but on cost and floor — the page returns in 0.14s, *every* keka
Board serves a slug today, so the downside is a request that yields nothing seven times in eight.
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
A 60-Board sweep found the exceptions and they are worth naming: `ripplehire:ltimindtree` titles
itself "LTM Careers | …" and serves **"LTM"**, plainly less recognisable than the slug; and a
parent or acquiring entity can displace a familiar brand — `keka:abcoffee` -> "Brewbay
Innovations", `lever:silhouette` -> "DNAM Brands", `lever:developintelligence` -> "Pluralsight".

That last class is the same shape as the `hiringOrganization` field Workday is excluded over, so
the distinction has to be stated rather than assumed. It is this: Workday's legal entity varies
**per posting inside one Board**, so no single value is even self-consistent; these are one stable
name per Board, and each is the company's own claim about itself. A stable parent name is a
defensible answer to "who is hiring"; three different legal entities on three postings of one
Board is not. An earlier draft of this ADR asserted no Board could end up worse. That was wrong.

**A vendor's own name is never a company.** `ripplehire:trampolinetech` titles itself "RippleHire
Careers | …", which shipped as the employer until a rule rejected it — the failure ADR-0034
blocklists Boards for, arriving through a title instead.

**One extra request per Board, and it is the cheapest possible one.** `attempts=1`, so it never
spends the retry ladder (three attempts against a walled origin is ~90s for one Board), and
`marks_wall=False`, so a 403 on an HTML careers page can never be what routes an entire ATS onto
the spare egress. Measured added latency per Board: 0.4s–1.2s mean.

**Existing rows are renamed without a backfill, but not quickly.** `company` is in
`doc_prep.META_FIELDS`, so `update_meta` re-observes it and `index sync` rewrites the stored row —
no migration needed. The horizon is the scrape cadence, not one run: a run's slice is ~20k Boards
out of the Scrapable set, so full propagation takes many runs and is unmeasured. Say "converges
over days", not "fixed on the next run".

**`company` is now two things at once**, which the README says plainly: a real name where a Board
states one, the ATS slug everywhere else. That is honest rather than tidy, and it is the shape any
incremental fix to this has.

**These are ceilings, not achieved coverage.** Every figure below counts rows on a Board whose
ATS is wired — not rows that actually gain a name. The per-ATS hit rates are 25-28/30 (and keka's
5/40), so the realised share lands near 27.5%, not 31.6%. Quote the ceiling only as a ceiling.

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
