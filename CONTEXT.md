# HeadStart

HeadStart reads software-engineering openings straight from the boards companies run on
third-party Applicant Tracking Systems, normalizes them into one feed, and (optionally) alerts
subscribers. This is the domain glossary; architecture decisions live in
[`docs/design-choices.md`](./docs/design-choices.md).

## Language

### Jobs and boards

**Job**:
A single opening after normalization to HeadStart's ATS-agnostic shape (the `Job` record).
_Avoid_: posting, listing, opening, vacancy — those name the *raw* record an ATS returns, before normalization.

**ATS**:
The third-party Applicant Tracking System a company runs its hiring on (Greenhouse, Lever, Workday, Darwinbox, …); the `ats` key selects a scraper.
_Avoid_: provider, platform.

**Board**:
One company's set of openings as hosted by its ATS — what a scraper actually reads.
_Avoid_: job board, careers board.

**Careers page**:
A company's own web page that links to or embeds its Board; the input to careers-page discovery, distinct from the Board itself.

**Tenant**:
One company's presence on an ATS — an `(ATS, slug)` pair; the unit the discovery pipeline collects.
_Avoid_: instance, account, org.

**Slug**:
The identifier that locates a Tenant within its ATS (`boards.greenhouse.io/{slug}`). Its form is ATS-specific — a bare label for most, a host for Zoho, a full URL for Workday.
_Avoid_: handle, id, key.

**Scraper**:
The module for one ATS that reads a Board and normalizes its raw postings into Jobs; one per ATS, selected from the registry by `ats`.
_Avoid_: adapter, parser, client.

**Company**:
The employer behind a Board; a `CompanyRef` (`ats`, `slug`, `name`) is the reference that tells the scrape step which Board to read.

### Discovery and validation

**Discovery**:
Finding Tenants — which companies sit on which ATS — without already having a company list.
_Avoid_: crawling, harvesting.

**Feeder**:
A discovery source that surfaces Tenants (the Common Crawl miner, the Wayback feeder); each emits `(ats, tenant, url)` rows.

**Resolve**:
The inverse of Discovery — mapping a *known* Company to its `(ATS, slug)`.

**Liveness**:
Probing a Tenant's Board to decide whether it's real and counting its open Jobs, yielding a per-Tenant verdict.

**Live / Dead / Unknown**:
The three Liveness verdicts — **Live** (the Board answered with a parseable job count), **Dead** (definitive: 404, or the host doesn't resolve), **Unknown** (couldn't tell — a transient or ambiguous response, re-probed).
_Avoid_: up/down, valid/invalid.

**Unresolved**:
A Tenant still **Unknown** after every Liveness pass — surfaced for review, never silently dropped.

**Active list**:
The Liveness-validated Boards (`active/{ats}.csv`) — the Tenants that answered **Live**. "Currently hiring" is the further subset whose job count is above zero.

**Feed**:
The assembled JSON of every scraped Job (`docs/jobs.json`) — the artifact the dashboard and the alert bot consume.

### Alerts

**Subscriber**:
A Telegram chat registered for job alerts, with its own Filter and seen-Job state.

**Filter**:
A Subscriber's match criteria (e.g. location) deciding which Jobs to send.

**Notification**:
A message sent to a Subscriber for a Job that matches its Filter and hasn't been seen before.

## Relationships

- A **Company** runs its **Board** on exactly one **ATS**, located by its **Slug**; that `(ATS, Slug)` pair is a **Tenant**.
- A **Scraper** (one per **ATS**) reads a **Board** and produces **Jobs**.
- **Discovery** collects **Tenants** via **Feeders**; **Liveness** sorts them into Live / Dead / Unknown and writes the Live ones to the **Active list**; **Resolve** maps a known **Company** to its **Tenant**.
- The scrape step runs **Scrapers** over the **Active list** and assembles the **Feed**.
- The alert bot matches **Jobs** from the **Feed** against each **Subscriber**'s **Filter** to emit **Notifications**.

## Example dialogue

> **Dev:** "When Liveness marks a Tenant **Active**, does that mean the Company is hiring?"
> **Domain expert:** "No — **Active** just means the **Board** answered and we could read a count. A company with an open board but zero openings is still **Live**; 'currently hiring' is the subset with a count above zero. And don't conflate the **Board** with the **Careers page**: the careers page is the company's own HTML, the board is what the ATS serves — Discovery scans careers pages to *find* boards."

## Flagged ambiguities

- **"provider" vs "ATS"** — used interchangeably across code and docs; resolved: **ATS** is canonical.
- **"board" vs "careers page"** — distinct: **Board** is the ATS-hosted listing; **Careers page** is the company's own page that links or embeds it.
- **"posting/opening" vs "Job"** — resolved: **Job** is the normalized record; "posting" names the raw ATS record before normalization.
- **"active"** — overloaded between "the board responds" (**Live**) and "currently hiring" (Live with count > 0); resolved: the **Active list** is the Live set, and "hiring" is the count-filtered subset.
