# HeadStart

HeadStart reads software-engineering openings straight from the boards companies run on
third-party Applicant Tracking Systems, normalizes them into one feed, and (optionally) alerts
subscribers. This is the domain glossary; architecture decisions live in
[`docs/adr/`](./docs/adr/).

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

**Slug**:
The identifier that locates a Company within its ATS (`boards.greenhouse.io/{slug}`). Its form is ATS-specific — a bare label for most, a host for Zoho, a full URL for Workday. A Company's presence on an ATS is just its `(ATS, slug)`.
_Avoid_: handle, id, key.

**Scraper**:
The module for one ATS that reads a Board and normalizes its raw postings into Jobs; one per ATS, selected from the registry by `ats`.
_Avoid_: adapter, parser, client.

**Company**:
The employer listed on an ATS, behind a Board; a `CompanyRef` (`ats`, `slug`, `name`) is the reference that tells the scrape step which Board to read.

**Required experience**:
The years of prior experience a Job asks for, as a whole-year range — a floor with an optional ceiling, open-ended when only a minimum is stated. Extracted to a number so it can be filtered on ("at most N years"); the raw phrasing the ATS gave is kept separately. A Job whose requirement can't be read is **unknown**, and unknown is deliberately not treated as too senior — it passes the "at most N years" filter rather than being hidden.
_Avoid_: seniority — a title-level notion ("Senior engineer"), not a year count.

### Discovery and validation

**Discovery**:
Finding Companies on ATSes — which companies sit on which ATS — without already having a company list.
_Avoid_: crawling, harvesting.

**Feeder**:
A discovery source that surfaces Companies on ATSes (the Common Crawl miner, the Wayback feeder); each emits an `(ats, slug, url)` row (the slug column is still named `tenant` in the data files).

**Resolve**:
The inverse of Discovery — mapping a *known* Company to its `(ATS, slug)`.

**Liveness**:
Probing a Company's Board to decide whether it's real and counting its open Jobs, yielding a per-Board verdict.

**Live / Dead / Unknown**:
The three Liveness verdicts — **Live** (the Board answered with a parseable job count), **Dead** (definitive: 404, or the host doesn't resolve), **Unknown** (couldn't tell — a transient or ambiguous response, re-probed).
_Avoid_: up/down, valid/invalid.

**Unresolved**:
A Board still **Unknown** after every Liveness pass — surfaced for review, never silently dropped.

**Active list**:
The Live Boards — the Companies whose Board answered **Live**, read as the `status == live` rows of the Liveness ledger (`data/validate/liveness/{ats}.csv`, ADR-0012; supersedes the old `active/{ats}.csv`). "Currently hiring" is the further subset whose job count is above zero.

**Feed**:
The assembled `docs/jobs.json` the dashboard (and alert bot) consume. It is *derived* — built by reading the per-board `{ats}.jsonl` (the source of truth) back and deduping by Slug-aware id — and is the small **served curated subset**: the millions-scale harvest produces only the `.jsonl`, never a single feed.

### Alerts

**Subscriber**:
A Telegram chat registered for job alerts, with its own Filter and seen-Job state.

**Filter**:
A Subscriber's match criteria (e.g. location) deciding which Jobs to send.

**Notification**:
A message sent to a Subscriber for a Job that matches its Filter and hasn't been seen before.

### Search

**Search index**:
The embedded, deduped set of Jobs the semantic query runs against — the corpus the AI search actually serves. Built from the scraped `{ats}.jsonl` (the same source of truth as the Feed) and kept current by **Eviction**. Distinct from the **Feed** (the dashboard's assembled JSON) and from the **eval benchmark** — a frozen, labelled slice of Jobs used to *measure* search quality, deliberately held stable and *not* the live served corpus.
_Avoid_: database, vector store — those name the storage, not the served set.

**Eviction**:
Removing a Job from the **Search index** once its posting has closed, so a stale opening can never be a search result. Keyed on the fresh scrape: a Job whose id is absent from its Board's latest scrape is gone. The freshness counterpart to embedding newly-seen Jobs.

## Relationships

- A **Company** runs its **Board** on exactly one **ATS**, located by its **Slug**.
- A **Scraper** (one per **ATS**) reads a **Board** and produces **Jobs**.
- **Discovery** collects **Companies** (each as an `(ATS, slug)`) via **Feeders**; **Liveness** sorts their **Boards** into Live / Dead / Unknown and writes the Live ones to the **Active list**; **Resolve** maps a known **Company** to its `(ATS, slug)`.
- The scrape step runs **Scrapers** over the **Active list** and assembles the **Feed**.
- The alert bot matches **Jobs** from the **Feed** against each **Subscriber**'s **Filter** to emit **Notifications**.

## Example dialogue

> **Dev:** "When Liveness marks a Company's Board **Live**, does that mean the Company is hiring?"
> **Domain expert:** "No — **Active** just means the **Board** answered and we could read a count. A company with an open board but zero openings is still **Live**; 'currently hiring' is the subset with a count above zero. And don't conflate the **Board** with the **Careers page**: the careers page is the company's own HTML, the board is what the ATS serves — Discovery scans careers pages to *find* boards."

## Flagged ambiguities

- **"provider" vs "ATS"** — used interchangeably across code and docs; resolved: **ATS** is canonical.
- **"board" vs "careers page"** — distinct: **Board** is the ATS-hosted listing; **Careers page** is the company's own page that links or embeds it.
- **"posting/opening" vs "Job"** — resolved: **Job** is the normalized record; "posting" names the raw ATS record before normalization.
- **"active"** — overloaded between "the board responds" (**Live**) and "currently hiring" (Live with count > 0); resolved: the **Active list** is the Live set, and "hiring" is the count-filtered subset.
- **"Tenant" (retired)** — previously the `(ATS, slug)` pair. Dropped as a term: a **Company** *is* the thing on an ATS, located by its **Slug**, so we just say "a Company's slug on an ATS." The data still carries a `tenant` column (and the `data/ats-tenants-merged/` dir, `slug_from(tenant, …)` param keep the name) — a code/data rename is a separate change, not yet done.
