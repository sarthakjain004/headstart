# Discovery techniques

Angles for finding Boards, with measured yields. Run several — each is blind to what the others
surface, and the winning one differs per ATS.

## Ranked by measured yield

**urlscan.io — highest yield found so far.** 127 of one run's 269 new Boards came from it *and
nothing else*, and it was previously unused. `scripts/discover/urlscan_miner.py` takes domains as
arguments.

Its free search API **silently truncates at 100 results per query**, so a plain `domain:` query
looks exhausted while hiding most of the cohort. Slicing the same query by scan date walks past the
cap: half-year buckets took one sweep from 117 to 434 hosts. Widen buckets only when a slice returns
far fewer than 100.

**Embedded-board fingerprints — 86% of one ATS's win.** A Board embedded in a company's own careers
page has no `{host}/{slug}` URL at all, so a miner reading the first path segment cannot see it. But
archives capture the embed **subresource**, putting the Slug in the CDX **query string**. Greenhouse:
71,271 archived URLs → 7,195 Slugs → 3,542 new → 875 live.

Generalise the lesson: when a surface is embedded or parameterised, search query strings, not paths.

**Subdomain enumeration** — only where the ATS uses a shared domain (`{slug}.ripplehire.com`,
`{slug}.eightfold.ai`). Certificate-transparency logs (crt.sh), passive DNS, and wordlist probing all
apply. For a shared-domain ATS this is usually the single best source. Note crt.sh returns
wildcard-only records for some providers — verify per provider rather than assuming.

**Regional and data-centre enumeration.** Providers run separate namespaces per region and each is a
**separate namespace** — the same label in two regions is usually two different companies. Zoho's
docstring claimed five data centres; probing found ten, three of which held unmined Boards. Enumerate
by DNS and HTTP probe; never trust a hardcoded list.

**Careers-page redirect chasing** — the route to **non-derivable Slugs**, where the identifier is a
parent, legal or brand variant no guess produces: Dream11 → `lever:dreamsports`, Zomato →
`smartrecruiters:Zomato1`, Razorpay → `greenhouse:razorpaysoftwareprivatelimited`. Follow a company's
careers link to where it actually lands and read the Slug off the destination.

**Directories matched to the ATS's market.** Match the source to who buys the product: startup-facing
ATSes (Greenhouse, Lever, Ashby) are dense in VC and accelerator portfolios; enterprise ones (Workday,
SuccessFactors, Eightfold) in index constituent lists, GCC directories, and the vendor's own
customer and case-study pages; India-focused ones (Zoho, RippleHire, Keka) in regional and
association member lists.

**Wayback CDX and Common Crawl** — the established feeders. Check whether an existing miner ran to
exhaustion before assuming they are mined out; several had not.

## Measured duds

Stop early when a technique proves out. Both of these were killed mid-run on measurement rather than
finished on principle.

**Name-derived Slug probing: 0.10%** (3 live in 3,268 attempts on a mature ATS). The archives already
hold every *guessable* Slug, so guessing re-finds what you have. The exception is an ATS never mined
before, where the archives are empty of it — measure the hit rate early and stop if it is a dud.

**Public crawled Slug lists: 0 new.** Two aggregated lists (8,333 and 633 Slugs) were 100% subsumed
by an existing ledger. Exhausted for mature ATSes.

**Short-link resolution: 0 new.** Resolving 4,875 `grnh.se` codes yielded 117 distinct Slugs, all
known — short links cluster on a few heavily-promoted Boards.

**DNS/CNAME custom-domain fingerprinting: no signal.** Custom domains resolve into shared CDN ranges
indistinguishable from unrelated hosts. Custom domains need an HTTP fingerprint, not a DNS one.

**Careers-page fingerprinting at scale: stopped on ROI.** 2.2% of companies exposed a Slug and only
1 in 6 was new — extrapolating to ~22 Boards for ~24,000 requests across 6,000 third-party hosts.
Worth it as a targeted pass against a curated list, not as a sweep.

## Read errors correctly

**Common Crawl returns HTTP 400 for "page exhausted"**, which is a normal end-of-results signal, not
a block. One run misread it, aborted on its first crawl, and left 38 crawls unmined. Distinguish it
from a genuine block before backing off.
