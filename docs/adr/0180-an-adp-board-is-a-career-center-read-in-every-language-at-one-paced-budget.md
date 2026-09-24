# ADR-0180: An ADP Board is a career center, read in every language it posts in, at one paced budget

**Status:** accepted · **Date:** 2026-09-23 · **Relates to:** [ADR-0001](0001-per-ats-slug-derivation.md) (a scraper's slug is its own to define), [ADR-0012](0012-liveness-ledger.md) (the ledger is the scrape list), [ADR-0048](0048-skip-details-we-already-hold.md) (skipping a held detail), [ADR-0053](0053-scope-eviction-on-scrape-outcome.md) / [ADR-0121](0121-a-negligible-shortfall-is-still-an-authoritative-list.md) (a short list is not a complete one), [ADR-0114](0114-a-board-states-its-company-name-in-its-page-title.md) (company names), [ADR-0158](0158-jazzhr-and-jobvite-are-worth-their-storage.md) (the storage bar), [ADR-0166](0166-gate-the-detail-pass-on-the-tech-filter.md) (the pre-detail tech gate)

## Context

ADP Workforce Now is ADP's payroll-bundled ATS, overwhelmingly small and mid-sized US and
Canadian employers. A career center is a client-rendered page on one fixed host,
`workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=…&ccId=…`, that calls
an unauthenticated JSON API under `/mascsr/default/careercenter/public/events/staffing/`. The
starting hypotheses were a third-party implementation (`kalil0321/ats-scrapers`, MIT) and its
342-row seed list. Measuring the live host on 2026-09-23
(`docs/adp/2026-09-23_careercenter-measurement.md`) found five of its assumptions wrong and one
constraint no earlier scraper has had to design around: a hard, IP-wide request budget.

## Decision

**1. The Board is the career center, `{cid}/{ccId}`.** Both keys are query values on one host.
`cid` is case-sensitive (an uppercased real GUID answers 404). `ccId` is an address, not a filter:
of 8 hiring clients measured with more than one center, 4 had disjoint posting sets, 3 had a
center wholly inside the default `19000101_000001`, and 1 overlapped partly (13 of 31 shared).
`lang` is **not** part of the identity (next point). Every discovery source — the Wayback style
`adp`, `cc_miner`'s `adp` kind, the fingerprinter and `scripts/discover/mine_adp.py` — reads a URL
through `wayback_feeder.extract` and emits the same `{cid}/{ccId}` and the same canonical URL.
Recruiting Management links (`recruiting.adp.com`, `myjobs.adp.com`) are filed by the
fingerprinter under their own unsupported key, `adp_recruiting`, so a link to that platform is
never mistaken for a Workforce Now Board.

**2. Every language the center lists is walked, merged by `ExternalJobID`, English first.** `lang`
is a filter: a posting is listed only under its own language, and a language the center does not
use answers `{"jobRequisitions": []}` with no `meta` — byte-identical to an empty Board. Lifemark
(the largest seed Board) reads 0 under `en_US` and 460 under `en_CA`. The content-links endpoint's
`Locale` list names a center's languages; it covered the posting languages on 45 of 45 Boards
checked (26 of them multi-language or not `en_US`) and does not depend on the `lang` asked. A
translated posting keeps its `ExternalJobID` across languages, so the merge keeps one Job per id,
in its English version where there is one; a posting in French or Spanish only is kept in that
language (2.9% of postings over 155 Boards). Between two English variants the order is
alphabetical (`en_CA` before `en_US`) — both are used on 2 of 155 Boards, both render the same
posting, and the detail is asked in whichever won, so the tie-break is arbitrary but stable. CLAUDE.md scopes English-only to the search index,
not the scrape or the feed, so those are scraped and held out of the index downstream.

**3. `ExternalJobID` is the native id**, all-digit and unique within its Board on 2,069 of 2,069
rows. Upstream's `itemID` carries `:` on 8 rows (`HRB:2048292:11346772`), which
`board_identity.board_of` would split wrong.

**4. The walk is 1-based, 20 at a time, to the stated total.** `$top` clamps at 20 silently (47 of
195 seed Boards returned 20 when asked for 100); `$skip` is 1-based (`meta.startSequence` echoes
it, and `$skip=0` drops a row — upstream's `0, 19, 39, …` reads one row twice); `meta.totalNumber`
ends the walk (460 of 460 read twice). Progress counts unique `ExternalJobID`s, so a posting that
shifts between pages cannot end the walk early. A shortfall goes to
`mark_truncated_unless_negligible`; rows with no positive total — never observed, but it would
leave no way to know the list is whole — go to `mark_truncated`.

**5. One process-wide pacer, and a 429 never ends a Board short.** F5 BigIP refuses the 201st
request in a fixed 60-second window, counted across every tenant, with a bare 429 and no
Retry-After: rested runs at 5 and 8 req/s were refused on exactly request #201, a probe polled
every ~3 s stayed refused until ~60 s after the window opened, and 450 requests at 3 req/s ran
clean. `harvest` scrapes Boards concurrently in one process, so a per-Board delay would multiply
by the Board count. `adp._Pacer` is one lock and one next-free slot shared by every instance and
by both the sync and async detail paths, spacing request starts at 0.4 s (150/minute, 25% under
the budget). The fetch seam's own 429 retries are turned off for this host — its ~5 s of backoff
cannot outlast a 60 s window and would spend budget the pacer is guarding. A 429 anyway (another
process on the same IP) rests every request for the window and retries, three attempts in all;
a Board still refused is `mark_truncated`, never returned short as complete. A request that
claimed its slot before a rest re-claims one when it wakes inside the rest, so queued work does
not fire into the refused window. The company-name lookup is one try: its 429 rests the pacer
but is not retried.

**No `egress_fallback_on`, though the limit looks per IP.** With the direct address refused, 4 of
20 requests through the local Cloudflare WARP proxy answered 200 inside the same window — the
meter is not global. But WARP cannot carry ADP traffic. Across two WARP addresses (one before
and one after `spare_egress.rotate()`) 5 of 60 requests succeeded at all; the rest were
"connection to proxy closed", proxy errors or timeouts, while google and www.adp.com through the
same proxy answered 3 of 3 and WARP's IPv4 egress (104.28.220.175) worked. An independent check
the same evening agreed: through `socks5h://` one run of 8 got 4 x 200 and the next run of 12 got
0, every failure a 5 s timeout; plain `socks5://` got 0 of 8. The cause is not established —
`workforcenow.adp.com` publishes no IPv6 address, and an edge block on Cloudflare's ranges would
explain the same result — but the outcome is: opting in would move a refused Board onto a route
that failed on ~90% of requests. The user's instruction was to opt in if the limit proved per IP;
this measurement is why it was not, confirmed with the orchestrator on 2026-09-23. Whether CI's
Linux WARP reaches the host is unmeasured, and is the measurement a future opt-in needs.

**6. The detail pass is gated and skippable.** The detail (`…/job-requisitions/{ExternalJobID}`)
is the listing row plus `requisitionDescription` and nothing else (120 of 120). No department
exists on either surface (`HomeDepartment` empty on 2,069 of 2,069 rows), so the ADR-0166 gate
is **exact** on `is_tech(title, None)`, and the ADR-0048 skip blanks nothing. The detail must be
asked in the posting's own language: under any other, or for a closed id, it answers 200 with a
~1.2 KB skeleton — no title, no description — counted as a loss, never an error.

**7. Dead versus empty.** An unknown `cid` is a 404 (2 random GUIDs, a malformed one, an
uppercased real one). A `ccId` the client does not have is a 200 whose content-links states
`PublishedIndicator` false, where every real center measured — hiring or empty — states true.
Everything else is live, its count the sum of each language's `totalNumber` (a translated posting
counts twice; the `jobs >= 1` hiring cut does not mind).

**8. The company name is ADP's `ClientName`.** Nothing a browser renders names the employer: the
page title is the literal "Recruitment" on every center, there is no og: tag or JSON-LD, and no
posting field carries it — 5 centers rendered in Chromium through the board, job and apply steps.
The `client-features` JSON the page loads does, as `ClientName`, on 120 of 120 centers sampled;
`ADPScraper.resolve_company` reads it once per Board through `company_name.from_title`'s guards
(119 of 120 pass; one 65-character bilingual legal name exceeds the length cap and keeps its
slug). It is ADP's payroll-client record, often the legal entity: of 60 centers whose seed-list
brand name was known, 18 read the same, 16 differ only by a suffix or case ("CHM Hotels Inc",
"VETPRIDE SERVICES INC"), and 26 name a parent or legal entity ("KERRIDGE COMMERCIAL SYSTEMS
CORP" for Klipboard, "The Center for Investigative Reporting, Inc." for Mother Jones). It is
served as stated — the company's own claim, ADR-0114's floor — including the 27 of 120 written in
capitals: re-casing "AMVAC CHEMICAL CORP" would also re-case "LLC" into "Llc", a spelling nobody
wrote. The one refusal is a known miss, kept rather than special-cased: the 60-character cap is
ADR-0114's guard against serving a sentence, one Board in 120 pays it, and ADR-0114 already
records a longer legal name refused by the same cap. **Wired without a `board_page`.** ADR-0114's
mechanism reads one page's `<title>`; ADP's name is a JSON field, so `ADPScraper` overrides
`resolve_company` itself — one request, `attempts=1`, `marks_wall=False`, and a single try
through the pacer — and `adp` joins taleo_enterprise in `tests/test_scrapers.py`'s
`_NO_BOARD_PAGE`, with its `PATTERNS` catch-all and `_VENDOR_ALIASES` entry still required and its
behaviour pinned in `tests/test_adp.py` instead of `_RESOLVE_ROWS`. `ClientClassification` ("Client
Live" / "Client Term") is not a liveness signal: all 8 "Client Term" centers sampled still listed
postings.

**9. Enabled on arrival.** The ledger holds every one of the pool's **23,619 candidate
centers**: 22,027 live, 1,005 dead, 587 unknown — **15,318 hiring, with 287,619 postings** (the sum
of each language's `totalNumber`, so a translated posting counts twice). It was probed two ways,
with identical logic: a local paced pass on one IP settled 13,762 centers, and 15 GitHub Actions
runners — 15 IPs, each under its own 200-per-minute window — probed the other 11,260 in 12.7
minutes with no 429. The two agreed on all 1,403 centers both probed (status) and on every job
count among them. Actions IPs reach ADP; WARP's do not (§5). Checked against the host afterwards:
all 495 dead rows of the first local snapshot and 5 of the Actions half re-probed dead, and 15
live rows (10 local, 5 Actions) re-probed live, 14 with the same count (one 5 -> 4). The 587 unknowns
are ADP answering 500 on both content-links and the listing (32 of 40 sampled), re-probed on their
TTL rather than buried. ADR-0158's bar is ~2 MB fetched per tech Job. Per posting ADP costs
~2.5 KB of listing; per tech posting (5.6%, 116 of 2,069) a ~16.8 KB detail; per Board ~7 KB of
content-links and client-features. Over the ledger: 287,619 x 2.5 KB + ~16,107 tech x 16.8 KB +
15,318 x 7 KB = **~1.1 GB for ~16,100 tech Jobs, ~0.07 MB each — about 30x under the bar**.

**10. Two more dead-versus-unknown rules, found probing the whole pool.** A DNS failure is
UNKNOWN, not DEAD: mid-pass the local resolver failed `workforcenow.adp.com` while the host kept
answering, and the prober's generic rule reads `dns` as a departed tenant, which for a single fixed
host is never true (breezy's lesson, ADR-0181); the first snapshot's 495 dead rows were re-probed
with the fixed probe and all 495 stayed dead. And a center whose listing answers **403 "Job listing
is not allowed for external candidates."** is DEAD: its client keeps it internal, so nothing on it
is public (8 of 40 sampled first-pass unknowns). The Actions half's 744 unknowns were re-probed
under that rule: 157 became dead, 587 stay unknown. The local half had no unknowns left to apply it
to, and a center it settled live answered its listings with 200s, so the rule holds across the
whole ledger.

## Alternatives considered

- **Read only `en_US`**, or English only. Rejected: Canadian centers post in `en_CA` and read
  empty under `en_US` (10 of 155 post only in `en_CA`), and CLAUDE.md keeps non-English postings
  in the scrape and the feed.
- **Walk every language the page supports** (six, per its own `langMap`) blind. Rejected:
  `de_DE` and `ko_KR` held no posting on any of 45 Boards, and three blind requests per Board
  cost real budget under a 150/minute ceiling; the `Locale` list is one request and was exact.
- **A committed name file built from discovery sources** (the seed list, Indeed rows). Rejected
  once `ClientName` was found: it covers every Board, including the Wayback- and CC-only ones that
  no named source reaches, at one request per Board.
- **Per-Board pacing.** Rejected: it multiplies by the number of Boards `harvest` runs at once.
- **ADP Recruiting Management** (`myjobs.adp.com`, `recruiting.adp.com`) in the same scraper.
  Rejected: a different platform — its own host, a path slug, and a listing
  (`my.adp.com/…/job-requisitions/apply-custom-filters`) that needs an `orgoid` header and a
  posting-channel id not yet found. Recorded as a follow-up; the fingerprinter now files it under
  its own key, `adp_recruiting`, rather than as a Workforce Now Board. *Since built* (ADR-0191):
  ADP Recruiting Management is its own scraper, `adp_recruiting`, and the missing channel id is
  the career-site record's `myJobsToken`, sent as a `myjobstoken` header.

## Consequences

**One employer can be served twice.** 3.4% of pooled clients run more than one career center,
and 4 of 8 measured share postings between centers (3 contained, 1 partial). ADR-0023's dedupe
groups within a Board, so a shared posting is two Jobs. Accepted, measured, and small.

**Time, not storage, is ADP's cost.** A Board costs 2 requests (content-links, client-features)
plus one listing page per 20 postings per language plus its tech details. Over the 15,318 Hiring
Boards that is ~74,300 requests for one full pass: **~8.3 hours on one IP at 150/minute**, or ~33
minutes of ADP pacing per shard when spread over 15 shards (each its own runner and IP). A run's
Slice takes only a share: ADP is ~15% of the Hiring Boards, so a 20,000-Board Slice carries ~3,000
of them, ~15,000 requests, ~7 minutes a shard. The largest Board states 3,486 postings, well
inside the 500-page cap.

**Board cost reads high.** `board_cost` measures a Board's wall seconds, and under a shared pacer
those include the queue behind every other ADP Board in the same shard, so ADP's measured cost
overstates its own work and the LPT pack spreads ADP Boards more thinly than their requests alone
would need. That errs toward using more shards' budgets, which is the direction ADR-0047 wants.
