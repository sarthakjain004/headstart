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
request on *every* Board of the ATS for a name on few of them. That is a cost decision, open to
revisiting with its own measurement — not an absence of data, as an earlier draft claimed.

**Workday is excluded, and it is the largest single block** (51,861 rows). Its board page is an
empty SPA, and neither its listing nor its detail response carries a name — verified by driving the
real scraper. Its public job page's JSON-LD *does* carry `hiringOrganization`, but that is the
**per-posting legal entity**: it varies within one Board and is frequently worse than the slug —
it varies **within a single Board** — nvidia alone returns "IL00 Mellanox Technologies, Ltd.",
"IN01 NVIDIA Graphics Bengaluru" and "2100 NVIDIA USA" across three postings, and `nc` returns
"Adult Correction" and "Department of Transportation". Its board SPA does serve an `og:title`, but
sampled live that is correct on well under half the Boards carrying one and otherwise junk these
rules would accept ("Careers", "Job Opportunities"). A name we invent is worse than a slug we admit
to.

## Consequences

**It can only improve a name.** Every failure path leaves `self.company` untouched: no
`board_page`, a request that raises, a non-200, a title no pattern reads, a title that is exactly
the slug. A ledger-supplied name outranks a page title and skips the request entirely. Scrapers
that do not opt in make **zero** extra requests — measured, not assumed.

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

**Coverage is 4 of the affected ATSes** — 59,123 rows, which is 39% of the narrow denominator but
**31.6% of the 186,798 slug-shaped rows**, and the second number is the one to quote. The remainder
is not a rollout waiting to happen; it needs a per-ATS source that the evidence does not currently
support, and Workday's case shows that "some name" is not automatically better than none.
