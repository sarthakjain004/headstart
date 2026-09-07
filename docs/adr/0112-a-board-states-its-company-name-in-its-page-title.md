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
| eightfold | `Careers at {Name}` / `{Name} Careers` | ~93% (all 100 Hiring Boards) |
| ripplehire | `{Name} Careers \| Latest jobs at …` | ~96% (all 51 Hiring Boards) |
| lever | `{Name}` — no wrapper at all | ~88% (352/400) |
| keka | `Careers at {Name}` / `{Name} Careers` | ~11% (92 of 819, a full census) |

successfactors, darwinbox and freshteam scored **0/30 against the registered patterns**. For
darwinbox and freshteam that is genuine — they render client-side and serve nothing to read.
Successfactors is the interesting exclusion: it *does* serve titles ("Careers at Bachem"), but
they are marketing copy in several languages with no shared wrapper ("Life@MOHH - people, culture,
and values | MOHH", "Trabaja en Volaris"), so a pattern wide enough to catch the third mangles the
first two. That is a quality bar, not a cost one.

**Keka was scored 0/30 too, and that was simply wrong.** A 40-Board sweep found 5 serving a
`<title>`, and a full 819-Board census settled it at **92 names from 101 titles — ~11%** (the rest
render client-side), every one in a wrapper eightfold's patterns already read ("Careers at Skylark
Drones", "Entropik Careers"). It is wired. Be clear about the size: those 92 Boards carry
**1,964 ledger jobs**. An absolute number rather than a share, but not for the reason an
earlier draft of this line gave. It claimed 15,262 Hiring Boards "do not join to a ledger row",
which cannot be true — `load_active_companies` *builds* each Board from a ledger row. The join
that failed was keyed on the raw `tenant` column while a Board's slug is `scraper.slug_from(tenant,
url)`; the gap was in the key, not the data. The real reason to prefer the absolute is duller: the
denominator moves with which ATSes are enabled (`DISABLED_ATS` alone swings it by ~19,000 Boards),
so a percentage quoted today misleads tomorrow. For scale, it is well under 0.1%. It earns its
place not on volume but on cost and floor — the page returns in
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

The floor — never a *non-name* — is the claim this ADR actually makes, and it has been falsified
twice by measurement and repaired twice, most recently by a 1,971-Board sweep that found
`lever:schmidt-entities` serving "jobs". Both repairs are pinned by tests. State it as a claim
that has survived its latest attempt, not as one nothing could break: 1,111 names resolved in that
sweep and, after the fix, none is a non-name. A wider 8,210-Board census resolving 6,531 names
then found exactly one: `lever:pip`, recorded under Known misses below.

That last class is the same shape as the `hiringOrganization` field Workday is excluded over, so
the distinction has to be stated rather than assumed. It is this: Workday's legal entity varies
**per posting inside one Board**, so no single value is even self-consistent; these are one stable
name per Board, and each is the company's own claim about itself. A stable parent name is a
defensible answer to "who is hiring"; three different legal entities on three postings of one
Board is not. An earlier draft of this ADR asserted no Board could end up worse. That was wrong.

**A page label is never a company name.** `lever:destinationknot` titles itself "Destination
Careers" — the page-label shape this ADR refuses Workday's `og:title` over, arriving through the
front door. Every pattern that models "{Name} Careers" strips it, so a title still ending that way
means the title wore the wrapper twice and what is left is a label. Three live shapes, and they
were found one at a time, each after the previous fix had already shipped:

- **trailing** — `lever:destinationknot` serves "Destination Careers", reachable because lever's
  pattern matches anything.
- **leading** — `keka:enpro` serves "Careers at Careers at Enpro Industries"; the pattern strips
  one wrapper and a tail-only rule served the survivor as the employer.
- **the whole string** — `lever:schmidt-entities` serves "jobs", which reached 16 real Jobs as
  their company before this branch caught it.

An earlier draft of this section claimed those three "exhaust the positions a token can occupy",
so the set was closed. That is false, and worth recording as the kind of claim to distrust: a
token can also sit medially ("Acme Careers Portal"), the leading alternative matches one phrasing
rather than a position ("Jobs at Acme" passes), and the singular passes ("Acme Career"). None has
been observed across those 2,998 Boards, so none is handled — this module rejects only shapes
someone really serves. Expect a fourth shape rather than assuming there cannot be one.

Anchored rather than matching on word boundaries, because "Career Group" and "Job&Talent" are real
employers a `\b`-bounded rule would refuse.

It costs recall, and the honest number is not zero. A census of every lever and keka Hiring Board
(2,998) fires the rule six times: three page labels it exists for, and **three real employers** it
refuses — `lever:pmaconsultants` ("PMA Consultants Careers", 29 real postings), `lever:bananajobs`
("Banana Jobs") and `lever:assurance` ("Assurance Careers"). All three keep their slug. Refusing is
still the right trade — stripping the word
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
counted, so 31.6% excludes it; on the ledger it is the 1,964 jobs counted above. Do not read
59,123 as a five-ATS number. The remainder
is not a rollout waiting to happen; it needs a per-ATS source that the evidence does not currently
support, and Workday's case shows that "some name" is not automatically better than none.


## Three things this change surfaced

**A fourth was found later, on a different ATS, and that is the lesson.** `ashby:krakensandbox`
titles itself "Kraken Sandbox Jobs" and serves three template postings ("Basic Job Template",
"Admin Assistant Testing"). It went undetected for eight review rounds because the fake-tenant
hunt had only ever been run against **ripplehire** — the ATS where the first one turned up. The
same pass had also justified dropping `sandbox` from `_PLACEHOLDER` as "never observed", which was
a claim about where we had looked, not about what exists. Both are corrected. Its two siblings
(`ashby:bento`, `ripplehire:tenant1`) serve 0 postings, so ADR-0034's content-confirmation rule
has nothing to read and they are left to `_PLACEHOLDER` instead.

**Five vendor Boards had to be blocklisted, not renamed.** Reading titles is also a way of
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

**`lever:pip` is a live floor exception.** It titles itself "Jobs have moved to our Accenture Job
Site" — a notice, not a name — and nothing here refuses it: 41 characters is well inside
`_MAX_LEN`, and its leading "Jobs" is not the phrasing `_PAGE_LABEL`'s leading branch models
(`^careers? at`), which is the position-versus-phrasing distinction drawn above. It has 0 postings
today, so nothing reaches a user,
but the floor should be read with this in mind. It is *not* fixed here on purpose: telling prose
from a name in a 40-character string needs a judgement this module cannot make from a regex, and
a rule fitted to this one Board would refuse real names for no measured gain.

`eightfold:whirlpool.eightfold.ai` titles itself "Whirlpool Corporation" — a real, clean company
name that no eightfold pattern matches, because the wrapper this ATS is registered for ("Careers
at …", "… Careers") simply is not there. A recall miss caused by the patterns being anchored,
which is the same anchoring that keeps them safe.

`lever:springrecruits` loses a 70-character title to `_MAX_LEN`, and `lever:bananajobs`
("Banana Jobs") loses a real name to `_PAGE_LABEL`'s trailing "Jobs". Both keep their slug.
Recorded because each rejection rule's cost belongs here as well as in its own comment.

`ripplehire:labs` is the same 0-posting placeholder class as `ashby:bento` and
`ripplehire:tenant1` — it serves "Your Company" and `_PLACEHOLDER` refuses it, with no content for
ADR-0034 to confirm.

`ripplehire:labs-mph` was recorded here as un-blocklistable because it 502'd and exposed no
title. That held for about a day. It answers 200 now, titles itself "Mphasis Careers | …", and
serves **486 postings** as "Mphasis" — more than the genuine `ripplehire:mphasis` board's 189.
It is blocklisted. The lesson is about the shape of the evidence, not the Board: "no title today"
is a reading, not a property, and a Known-miss entry resting on one should be re-checked rather
than trusted.
