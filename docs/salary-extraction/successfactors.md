# successfactors

## Methods tried

- **Live board count re-measured, not assumed**: the plan's 2,204 figure was close but still
  stale (as every ATS measured so far has been). Current, properly deduplicated count: **2,164
  live boards**, comfortably below the 3,000 sampling cap. This is the first "remaining
  unexamined, mid/small" ATS — no prior salary work to build on, unlike every ATS since teamtailor.
- **Detail-pass, fan-out baked into `fetch_raw()` — needed a new bounded adapter**: like every
  detail-pass ATS before it, `successfactors.py`'s own `fetch_raw()` fans out a per-job detail
  fetch (6 workers, one host) over every listed posting — unsafe to call directly for a bounded
  sample. `BaseScraper` was checked first (lesson 40): no generic listing/detail primitive exists
  there for this shape, so the adapter reuses the scraper's OWN existing methods instead —
  `_fetch_sitemap()` (already bounded by design: it reads a urlset to completion but *abandons* an
  RSS feed after ~64 KB so the real feed's trickling generator, confirmed live at 16.6 MB for
  jobs.sap.com, never stalls a caller) and the module-level `_job_urls_from()` (shared across all
  three listing surfaces by construction — the underlying regex just looks for `/job/.../{id}/`,
  independent of urlset/RSS/search-page markup). Confirmed directly that the abandoned-early RSS
  prefix still carries real job links (4 found in SAP's own ~75 KB prefix) before trusting the
  design. Detail-fetches only the first `_DETAIL_FETCH_CAP` (3) postings via the scraper's own
  `_job_fields()`. Deliberately does not replicate `fetch_raw()`'s `_search_job_urls()`/
  `_rss_job_urls()` fallbacks (used in production when the sitemap surface alone is empty) — an
  acceptable sampling loss given 2000+ other boards, not worth the complexity for a bounded pass.
- **Checked for structure one level deeper, from three independent angles, before sampling at
  scale** (asked a ninth time now): the scraper's own `_jsonld_fields()` only extracts `title`/
  `description`/`location`/`posted_at`/`employment_type`/`remote` from a classic RMK page's
  schema.org `JobPosting` JSON-LD — never checked for `baseSalary`, a well-known standard property
  of that same schema.org type. Checked directly: **zero** of 23 real job pages sampled across 23
  distinct companies (20 randomly sampled + 3 hand-picked) carry any `application/ld+json` block
  at all — every tenant checked renders via CSB-style microdata instead (`itemprop="..."` spans/
  meta tags), not classic JSON-LD, despite the scraper's own docstring describing JSON-LD as the
  "classic" shape. Checked the microdata path too: the complete `itemprop` set on a real page
  (`address`, `addressCountry`, `addressLocality`, `addressRegion`, `datePosted`, `description`,
  `hiringOrganization`, `jobLocation`, `postalCode`, `title`) carries nothing salary-related, and
  `baseSalary` never appears as literal text either. Checked the scraper's own third mechanism,
  the per-tenant customizable `joblayouttoken` label/value system (already used for City/State/
  Posting-Date) — surveyed 24 distinct real labels across 10 more tenants, in English, German, and
  French, and none names salary/compensation/pay in any language. **Confirmed-flat-dead** across
  all three of this ATS's own structured-data mechanisms, not just one — the most thoroughly
  cross-checked "flat-dead" finding in this initiative so far.
- **A real Tier-2 candidate was built, then found to be fully redundant, then reverted — a new,
  more rigorous verification step this pass added**: "Rate of Pay: $X - $Y" looked like a clean,
  evidence-backed fix in isolation — the regex change correctly matched the target text once
  built, and an initial frequency count suggested real value (19 occurrences, 4 companies "not
  already extracted"). Built it, then ran the *actual* verification this initiative's own
  discipline requires — a full old-vs-new `extract()` diff over every real "rate of pay"
  occurrence in the corpus (`repr()`-compared, per lesson 11, not bare `!=`) — and found **zero**
  net change. Reverted immediately once measured properly.
  The premise behind building it — "a reversed-order label `_LABELED` couldn't reach" — was itself
  wrong, not just the fix. `_LABELED` is UNCHANGED on main and already MATCHES all 19 real
  occurrences on its own: `re.search()` doesn't anchor to string start, so its existing
  `pay(?:ing)?` keyword matches "Pay: $X..." starting mid-string, with "Rate of" simply unconsumed
  leading text it never has to reach. Of those 19, 12 go on to RESOLVE via `_LABELED`'s own tier —
  the first one tried in the `from_description()` cascade, so no lower tier is even reached once it
  returns a definitive answer. The other 7 decline for a reason unrelated to any label: no period
  marker (hr/day/mo/yr) sits near the match, so `_period_from_window` defaults to an annual
  multiplier and the resulting figure (e.g. "$33.35" read as $33/year) falls below `_bounded()`'s
  USD floor ($10,000) — verified directly against the real text, not inferred (the footnoted "Rate
  of Pay: $31.94 - $35.93*" example under Patterns found below is one of these 7). `_BARE_RANGE`
  plays no role either way: cascade order makes it moot for the 12 that already resolve via
  `_LABELED`, and on the 2 of the 7 fallthrough texts where it separately matches, it hits the
  identical period-defaulting/bounds rejection, since `_span_from_match`/`_bounded` are shared by
  every Tier-2 scanner regardless of which regex fed them. Across all 19 real occurrences,
  `_BARE_RANGE` never once supplies the resolved value — crediting it as the redundant mechanism
  (a first attempt at this explanation did) is as wrong as the original "label gap" premise.
  The lesson this sharpens, in two parts: a sub-pattern matching correctly in isolation
  (`_LABELED.search()` on the *new* pattern) is necessary but NOT sufficient evidence a gap is
  real — test the unmodified cascade against the same real text first; and even a correct
  full-pipeline diff proving zero net change only establishes the aggregate, not which tier of the
  cascade actually resolves each case — that takes tracing the cascade itself (which tier fires,
  and whether a lower one is even reached), not assuming a plausible-sounding pattern elsewhere in
  the file is the mechanism.
- **A second Tier-2 candidate measured and declined for thinner reasons**: a label immediately
  followed by a parenthesized range ("Base Salary ($87,199 - $95,482)") — real evidence narrows to
  2 genuine companies (both NSW, Australia government agencies) once a confirmed false positive is
  excluded from the same small sample ("Company paid life insurance of 1x annual base pay ($50,000
  minimum)" — the parenthesized figure is an insurance-payout minimum, not the job's salary, from
  British American Tobacco's benefits text). Below this initiative's multi-company bar, and
  carrying a demonstrated false-positive risk the "rate of pay" candidate didn't.
- **Read real no-signal misses and audited the full no-signal bucket for currency-shaped content**
  (the mandatory audit): of 5,547 no-signal jobs (90.2%), 731 (13.2% of no-signal) have
  currency-shaped content that wasn't extracted — read a 30-example random sample directly; see
  Patterns found for the full breakdown.
- **No `salary.py` changes ship this pass** — both real Tier-2 candidates were measured and
  declined (one after being built and reverted, the other upfront); Tier 1 is confirmed dead
  across all three of the ATS's own structured-data mechanisms. The mandatory full cross-ATS diff
  is correctly N/A, matching lever's/ripplehire's own precedent.
- **Live-verified twice**: the full 2,164-board sample itself (using the new adapter), plus a
  fresh, differently-seeded 25-board re-sample (seed=313) against real current successfactors
  hosts, zero errors, consistent with the full sample's shape (0% field, ~18% description-hint).

## Instruction-adherence self-assessment

- Sampled up to 3000 or the full live-CSV count: **yes** — 2,164, the full live population,
  comfortably below the cap (2,138 boards succeeded, 26 errored — a real, low ~1.2% failure rate;
  the one type directly confirmed was an expired SSL certificate on a genuinely misconfigured
  host, not an adapter bug).
- Measured both required percentages: **yes** — 0.00% field, 9.8% overall (Tier1+Tier2), against
  6,151 jobs.
- Live-verified after code changes: **yes** — the code change here is the sampling adapter itself
  (`salary.py` ships unchanged); the full sample plus a fresh 25-board reseed both confirm it
  works correctly against real current hosts.
- **Audited the no-signal bucket for language-independent currency-shaped content before trusting
  the coverage number as a ceiling**: 90.2% no-signal; 13.2% of those (731 jobs, 11.9% of all)
  currency-shaped, read directly and traced to specific reasons (see Patterns found) — including
  one real, valuable side-finding: this project's explicit English-only search-corpus scope means
  the German/Dutch/French/Portuguese/Chinese salary mentions found in this audit (all real,
  genuine disclosures) are correctly out of scope for pattern-building, since a non-English
  posting never reaches the served index regardless of what salary data could be extracted from it
  — extracting from text nothing will ever serve would be wasted, not merely low-priority, work.
- Went beyond the ask: checked all THREE of the scraper's own structured-data mechanisms for a
  hidden salary field (JSON-LD, microdata, joblayouttoken), not just the one the scraper already
  reads from; built and then properly reverted a Tier-2 candidate after discovering — through the
  full verification this initiative's own discipline demands, not skipped as "obviously fine
  since it compiles" — that it was fully redundant with existing coverage.
- Did not: register a Tier-1 parser (confirmed dead across three independent mechanisms) or ship
  any Tier-2 pattern (both real candidates measured and declined, one after being built).

## Live-verification review

Two rounds, against real current successfactors hosts each time, never a replay of the frozen
capture:

1. The full 2,164-board sample itself, using the new `_fetch_successfactors` adapter — 2,138
   boards succeeded (26 errored, ~1.2%), 6,151 jobs, consistent shape throughout the run.
2. A fresh, differently-seeded 25-board re-sample (seed=313): `careers.hysan.com.hk`,
   `careers.austin.org.au`, `career.hayco.com`, `talento.ctnotariado.com`, `karriere.swmh.de`,
   `careers.originenergy.com.au`, `jobs.kronosww.com`, `career.ekato.com`, `careers.hiab.com`,
   `jobs.kalmarglobal.com`, `jobs.denodo.com`, `careers.gracekennedy.com`,
   `careers.coastcapitalsavings.com`, `jobs.bmwgroup.com`, `ace1958.jobs2web.com`, `jobs.orf.at`,
   `jobs.deere.com`, `karriere.bethel.de`, `talento.christus.mx`, `www.careers.pluspetrol.net`,
   plus 6 more — 0 errors, 73 jobs, 0 field hits, 17.8% description-hint rate — consistent with
   the full sample's own 18.1%.

## Patterns found

- **No structured Tier-1 signal exists anywhere in the scraper's own reach** — the defining,
  triple-checked negative finding of this pass (JSON-LD, microdata, and the customizable
  joblayouttoken label system all checked directly, all confirmed absent).
- **The currency-shaped no-signal audit (731 jobs) broke down as** (a representative 30-example
  random sample):
  - **CAD acronym collisions** (Computer-Aided Design, in engineering/manufacturing postings —
    DLR, Tata Motors, GreenWorks Tools) — the same false-alarm class established on keka's and
    darwinbox's passes, correctly unextracted.
  - **Correctly-guarded company revenue/valuation mentions** ("USD 19.3 billion" portfolio value,
    "€3.7/5.3 billion in combined revenue" x2, "$1 billion in annual sales", "US$8.3 bilhões"
    valuation in Portuguese, "€400 million annually" procurement portfolio) — the existing
    revenue/funding guard already excludes all of these regardless of language.
  - **Non-English genuine salary disclosures, correctly out of scope** (German: "€3,200
    brutto/Monat" minimum wage, tiered apprenticeship pay by year, "1,012 EUR" monthly stipend;
    Dutch: "€3.471 tot €6.114" salary range) — real, would extract if built, but this project's
    English-only search-corpus scope (CLAUDE.md) means the underlying posting never reaches the
    served index, so building language-specific patterns for it would produce unused data.
  - **Two genuine, real, but too-thin-to-build gaps** (see Methods tried): "rate of pay" (built,
    verified fully redundant, reverted) and a label-then-parenthesized-range shape (declined for
    thin evidence plus a demonstrated false-positive risk).
  - **A small number of genuinely ambiguous cases correctly declined** — bare dollar ranges with
    no period marker anywhere in the surrounding text (e.g. "Rate of Pay: $31.94 - $35.93*" with
    a footnote that never states hourly vs. annual) — the no-fabrication principle correctly
    declines rather than guessing, even though the shape READS as very likely hourly given the
    role type (a tourist-attraction retail/hospitality posting).
- **The rich, real US/Canada/Australia/New Zealand disclosure rate this pass DID find (9.8%
  overall, entirely via the pre-existing shared cascade)** is itself a notable, positive finding —
  successfactors' global company mix includes many employers in pay-transparency jurisdictions
  (California, Colorado, New York, Ontario, NSW/NZ public sector), and the mature cascade already
  built across 10+ prior ATS passes handles their real disclosure language well without needing
  any successfactors-specific extension.

## Coverage

| metric | value |
|---|---:|
| boards sampled (of 2,164 live) | 2,164 attempted, 2,138 clean |
| jobs seen | 6,151 |
| jobs with a structured salary field (`Job.salary`) | 0 |
| extracted via Tier 1 | 0 (0.00% of all jobs) |
| extracted via Tier 2 (description, no usable field) | 604 (9.8%) |
| **overall Tier1+Tier2 coverage** | **604 (9.8%)** |

Squarely mid-pack for this initiative (workable 15.4%, workday 27.6%, greenhouse 36.1%,
smartrecruiters 10.0%, zoho 10.0%, teamtailor 14.1%, ashby 49.7%, recruitee 38.2%, personio 10.5%,
rippling 46.4%, lever 41.3%, keka 29.1%, darwinbox 13.4%, ripplehire 0.08%, **successfactors
9.8%**) — close to smartrecruiters'/zoho's own 10.0%, and entirely a Tier-2 story: every single
extraction comes from the existing shared description-mining cascade, with zero structured-field
contribution and zero successfactors-specific pattern additions.

## What changed in code, and why

- **`src/headstart/salary.py`: no functional changes.** Tier 1 is confirmed dead across all three
  of the scraper's own structured-data mechanisms; both real Tier-2 candidates were measured and
  declined (one after being built, fully verified via a proper old-vs-new diff, and found
  redundant with existing coverage). A comment was added documenting both findings for future
  passes, but the actual extraction logic is byte-identical to `main`.
- **`scripts/enrich/salary_sample.py`**: added `_fetch_successfactors`, a bounded sampling adapter
  (`_DETAIL_ADAPTERS["successfactors"]`) — the actual code change this pass produced. Reuses the
  scraper's own `_fetch_sitemap()` (already cheap-by-design for this exact purpose) and the
  module-level `_job_urls_from()`, detail-fetching only the first `_DETAIL_FETCH_CAP` postings via
  the scraper's own `_job_fields()`.

### Cross-ATS impact

**Not applicable — `salary.py` was not functionally touched this pass**, so the mandatory full
cross-ATS diff doesn't apply here, matching lever's and ripplehire's own precedent. A deliberate,
evidence-based non-event: Tier 1 is confirmed dead and no Tier-2 pattern survived full
verification, so there is no shared-code change that could have moved any other ATS's numbers.

## Known gaps, left honestly unresolved rather than guessed at

- **The two measured-and-declined Tier-2 candidates** (Patterns found above) — both real,
  structurally confirmed, one demonstrated fully redundant and one below the evidence bar with a
  known false-positive risk. Neither is a gap in disguise; both were properly closed out by
  measurement, not left open for a future pass to re-litigate without new evidence.
- **Non-English genuine salary disclosures** (German, Dutch, French, Portuguese, Chinese) are real
  and were found in this pass's own audit, but are correctly out of scope per this project's
  English-only search-corpus decision — not re-opened here, and shouldn't be built for by any
  future pass either unless that scope decision itself changes.
- **The ~1.2% board error rate** (26/2,164) was not individually triaged beyond confirming one
  real cause (an expired SSL certificate on a genuinely misconfigured host) — consistent with
  every prior ATS's own experience of some real-world host failures, not investigated further
  since it's well within the range other passes have seen and shown no sign of being an adapter-
  side bug.

## Carried forward from workable through ripplehire — and new lessons

- **Applied**: the "check for structure one level deeper" question, asked a ninth time (ashby:
  hit, recruitee: confirmed-flat-miss, personio: hit, rippling: confirmed-flat-miss, lever:
  confirmed-flat-miss, keka: confirmed undecodable, darwinbox: confirmed flat-hit-with-nothing-
  more, ripplehire: confirmed flat-dead, **successfactors: confirmed flat-dead across THREE
  independent mechanisms** — the most thoroughly cross-checked negative finding yet, not just a
  repeat of ripplehire's single-field check).
- **Applied**: check `BaseScraper` for a reusable primitive before writing a new `http.fetch` call
  (lesson 40) — found none generic enough for this shape, so the adapter reuses the SCRAPER's own
  existing methods instead (`_fetch_sitemap()`, module-level `_job_urls_from()`), consistent with
  the spirit of the lesson even where the letter (a `BaseScraper` primitive specifically) doesn't
  apply.
- **Applied**: the mandatory "audit the no-signal bucket" methodology (personio's lesson) — 90.2%
  no-signal, 13.2% of those currency-shaped, read directly and traced to specific reasons,
  including a genuinely new one (non-English disclosures correctly out of scope).
- **Applied, and reinforced**: measuring every candidate pattern at full-corpus scale before
  building OR declining it (lever's lesson) — but this pass sharpens WHEN that measurement must
  happen: not just before building, but as the FINAL gate before shipping, since "rate of pay"
  passed an initial frequency check and an isolated match test, and still turned out to be fully
  redundant once the real, mandatory full-pipeline diff was run.
- **New**: a sub-pattern matching its target text correctly in isolation (e.g. `_LABELED.search()`
  returning the expected groups on a *proposed new* pattern) is necessary but not sufficient
  evidence that a fix adds value — and confirming "zero net change" via a full `extract()` pipeline
  diff (old vs. new, `repr()`-compared per lesson 11) is necessary but not sufficient to explain
  WHY. This pass's own explanation for that finding took THREE attempts to get right (see Methods
  tried), and the failure mode was different each time: attempt one credited a different, later
  pattern in the cascade (`_BARE_RANGE`) with the redundancy, on the strength of it matching the
  same *kind* of text in isolation — never checked against the real 19 occurrences at all. Attempt
  two correctly identified that the SAME sub-pattern the new candidate was meant to extend
  (`_LABELED`) was the real mechanism, via its own pre-existing keyword and `re.search()`'s
  non-anchored matching — but still asserted a specific split ("`_BARE_RANGE` independently
  succeeds on 3 of those 12") that a raw regex-match count made plausible without ever tracing
  which tier of `from_description()`'s cascade actually produces the resolved value. Only running
  every one of the 19 real occurrences through the actual cascade — which tier's `_resolve()` fires,
  whether it returns a value, `_AMBIGUOUS`, or plain `None`, and whether a lower tier is even
  reached — showed `_BARE_RANGE` contributes to zero of the 19, not 3: cascade order (it runs after
  `_LABELED`) makes it moot wherever `_LABELED` already resolves, and where it separately matches
  on a fallthrough text, it hits the identical `_bounded()` plausibility floor and still fails.
  Before writing down why a candidate is redundant, don't stop at "does pattern X also match this
  text" — trace the real cascade against the real corpus and read which tier, if any, actually
  resolves each case.
- **New**: when a scraper's own docstring characterizes a listing/detail-page format ("classic RMK
  pages embed JSON-LD") that this pass's own direct sampling doesn't reproduce (zero JSON-LD found
  across 23 checked tenants), trust the fresh measurement over the inherited characterization for
  THIS pass's own conclusions, while not assuming the original characterization was wrong — it may
  simply reflect that the checked sample didn't include whichever tenants still use that shape, or
  that the ecosystem has shifted since the scraper was first researched. State the measured finding
  precisely rather than either blindly repeating or silently contradicting the existing docstring.
- **New**: this project's English-only search-corpus scope (CLAUDE.md) is not just a search-index
  concern — it's a salary-extraction-pattern-building concern too. A real, genuine salary
  disclosure in a non-English posting is correctly out of scope for ANY future ATS pass to build a
  pattern for, since the posting itself never reaches the served table regardless of what a
  language-specific pattern could extract from it. Worth checking explicitly on any future pass
  where a no-signal audit surfaces non-English currency-shaped content, the same way this pass did.
