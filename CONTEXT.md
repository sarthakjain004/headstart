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
_Avoid_: handle, id, key, **token** — Greenhouse's own API spells it `boards-api.greenhouse.io/v1/boards/{token}`, so it leaks in easily; the thing it names is still a Slug, and the thing it locates is a **Board**.

**Scraper**:
The module for one ATS that reads a Board and normalizes its raw postings into Jobs; one per ATS, selected from the registry by `ats`.
_Avoid_: adapter, parser, client.

**Company**:
The employer listed on an ATS, behind a Board; a `CompanyRef` (`ats`, `slug`, `name`) is the reference that tells the scrape step which Board to read.

**Required experience**:
The years of prior experience a Job asks for, as a whole-year range — a floor with an optional ceiling, open-ended when only a minimum is stated. Extracted to a number so it can be filtered on ("at most N years"); the raw phrasing the ATS gave is kept separately. The number is either **stated** (read from a field or the description) or, when none is stated, a lower-confidence **floor estimated from the Job's seniority level** (its title/level suffix, e.g. "Senior" → 5; ADR-0018) — the `source` records which. Only a Job with **neither** a stated number **nor** a seniority signal is **unknown**, and unknown is deliberately not treated as too senior — it passes the "at most N years" filter rather than being hidden; a seniority-estimated floor, by contrast, can place a Job above the filter.
_Avoid_: conflating **seniority** (a title level) with required experience — seniority only *estimates* a year count as a fallback, it is not itself the requirement.

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
The assembled `docs/jobs.json` the dashboard consumes. It is *derived* — built by reading the per-board `{ats}.jsonl` (the source of truth) back and deduping by Slug-aware id — and is the small **served curated subset**: the millions-scale harvest produces only the `.jsonl`, never a single feed.

### Alerts

**Subscription** (ADR-0035, ADR-0038):
One person's standing request for alerts — one **Query**, a set of **Search filters**, its own **Watermark**, and exactly one **Transport** to reach them by. Identified by a verified address or, for someone the bot enrolled, by their Telegram chat.
_Avoid_: subscriber — the retired keyword bot's term for a chat matched by keyword against the **Feed**. That path is gone (ADR-0038); there is one ranking now, and everyone on it has a Subscription.

**Transport** (ADR-0038):
The channel one Subscription is delivered by — email or a Telegram DM. Exactly one per Subscription, because a single **Watermark** can only be correct if one thing decides whether a **Digest** arrived.

**Invite** (ADR-0035):
One entry in the allowlist: an address the owner has permitted, optionally carrying the **Query** and **Search filters** to run for it. An Invite is what a human writes by hand; the **Subscription** is the state it produces, adding the **Watermark** and unsubscribe token that nobody should have to hand-edit. An Invite naming no Query means self-serve — permitted to sign in and choose one. `alerts.run.subscription_for` is the only place one becomes the other.
_Avoid_: "allowlist entry" as a distinct concept — the allowlist _is_ the set of Invites.

**Master** (ADR-0038):
The Telegram chat that approves everyone else — claimed by the first `/start` the bot ever sees. Telegram's counterpart to the allowlist: the Invite path is the owner editing a file, the Master path is the owner answering `/allow` in a chat.

**Watermark**:
The instant a Subscription was last sent a **Digest**. The next Digest carries only Jobs whose `first_seen` is strictly after it, so an irregular pipeline cadence can neither double-send nor skip a window. Advanced only once a Digest has been accepted for delivery.

**Digest**:
The one message a Subscription receives after a pipeline run — its best new matches capped at 30, each with its semantic score and apply link, plus the same rows as a spreadsheet attachment. One Digest regardless of **Transport**, though Telegram splits it across several messages to stay under the platform's size cap. No matches sends no Digest.
_Avoid_: alert, notification — a Digest is one batched message per run, not one per Job. Delivered over either **Transport**, so "email" is wrong for it too.

### Search

**Tech filter**:
The recall-biased gate that keeps only software/tech Jobs (ADR-0017). The scrape writes every Job to `data/jobs/{ats}.jsonl`; the filter derives the **Tech subset** in `data/jobs/tech/{ats}.jsonl`, which is what the Feed, the embedding, the **Search index**, and the UI read. Recall-first: a non-tech Job creeping in is tolerated, dropping a tech Job is not — a hard rule the verification gate guards.
_Avoid_: category filter, keyword filter — it is a role classifier, not a taxonomy lookup.

**Search index**:
The embedded, deduped set of Jobs the semantic query runs against — the corpus the AI search actually serves. Built from the **Tech subset** (`data/jobs/tech/{ats}.jsonl`) and kept current by **Eviction**. Distinct from the **Feed** (the dashboard's assembled JSON) and from the **eval benchmark** — a frozen, labelled slice of Jobs used to *measure* search quality, deliberately held stable and *not* the live served corpus.
_Avoid_: database, vector store — those name the storage, not the served set.

**Eviction**:
Removing a Job from the **Search index** once its posting has closed, so a stale opening can never be a search result. Keyed on the fresh scrape: a Job whose id is absent from its Board's latest scrape is gone. The freshness counterpart to embedding newly-seen Jobs.

**Doc**:
The one string built per **Job** for embedding — its `title` + cleaned `description`, prefixed `search_document:` (ADR-0005) — encoded into a single vector. A Doc is a transient in-memory string assembled at embed time, not a file; the Job's other fields still ride alongside the vector as **Search index** metadata (ADR-0006).
_Avoid_: document — reads as a file; the embedding code's own vocabulary (`build_doc`, `docs`) already settled on "doc".

**Bucket**:
A fixed token-length ceiling (512 / 1024 / 2048 / 4096) that Docs are sorted into before encoding, measured with the real tokenizer rather than estimated by character count. Buckets exist because attention cost scales with sequence length squared: without a shared ceiling, one long Doc batched alongside short ones would balloon memory for the whole batch. Every batch within a Bucket is padded to that Bucket's exact length, so a run only ever presents a small, fixed set of compute shapes instead of one per Doc.

**Batch size**:
How many Docs are encoded together in one pass, fixed per **Bucket** so batch size × bucket² stays roughly constant (a fixed attention-memory budget) — the larger the Bucket, the smaller its Batch. On the CI CPU runner the ≤4096-token Bucket's Batch size is 1: no batching efficiency survives at the top Bucket.

**Throughput (jobs/s, s/doc)**:
The measured embedding rate — not a fixed constant. The embed step logs a running `jobs/s` average per batch straight to the CI log; its reciprocal, seconds-per-Doc, is the more useful number for predicting run length. Throughput differs sharply by **Bucket** (short Docs batch efficiently; the ≤4096 Bucket, Batch size 1, costs roughly 20x longer per Doc) and by which runner executes the run — so there is no single "embedding speed," only a per-Bucket rate read off real logs.

**Query**:
The natural-language sentence a user types describing *only the role they want* ("backend engineer at a climate startup"). It drives the embedding and nothing else — every structured constraint belongs to a **Search filter** instead, which is what makes the hybrid split explicit at the UI. A Query that carries years, salary, or a location is a Query doing a **Search filter**'s job.
_Avoid_: search term, keywords — it is a sentence describing a role, not a bag of terms to match.

**Search filter**:
A structured constraint the user sets themselves on the **Search index** — remote, employment type, `min_years`, recency — compiled into a deterministic where-clause that runs *before* ranking. A **Subscription** carries a set of these, and they are the only kind of filter left: the retired keyword bot's per-Subscriber `Filter` over the **Feed** went with it (ADR-0038).

**Résumé**:
Text a user pastes to have a **Résumé query** written from it. It is never stored, never logged, and never leaves the request that carried it — the derived Query is the only thing that survives.
_Avoid_: CV, profile — "profile" implies something persisted, which this deliberately is not.

**Résumé query**:
The **Query** an LLM writes from a **Résumé**, shown to the user in the search box and editable before it runs. Subject to the same rule as any Query: it names a role and must not carry years, salary, or location, however loudly the **Résumé** states them.

### Pipeline scheduling and sharding

**Board-priority ledger (EWMA)**:
The per-Board tech-yield score in `data/state/board_priority.csv`, kept as an **EWMA** — an Exponentially-Weighted Moving Average. Each run blends a Board's fresh tech-Job count into its prior score (`0.7·now + 0.3·history`), so recent nights dominate while older counts decay rather than being forgotten or weighted equally (ADR-0022). Boards the run didn't scrape keep their score unchanged — a partial harvest must not decay what it never looked at — and a score decayed below a floor drops out. The ledger orders both the scrape slice (high-yield Boards first) and the within-**Bucket** embed order.
_Avoid_: reading it as an exact count — it is a decaying average, an estimate of a Board's tech yield. _Avoid_: using it as a **cost** estimate. It answers "is this Board worth scraping?", not "how long will it take" — ADR-0026 conflated the two and the resulting pack was measurably useless. Cost lives in the **Board-cost ledger**.

**Board-cost ledger (measured seconds)**:
The per-Board _measured_ scrape wall time in `data/state/board_cost.csv`, the cost estimate the scrape planner bin-packs on (ADR-0027). Every scrape shard times each Board it scrapes and streams the row to its fragment; the join EWMA-blends them (`0.5·now + 0.5·history` — wall time is noisier than a Job count, so it leans on history harder than the priority ledger does). A Board with no measurement yet is estimated from its ATS's median, never from one global constant.
_Avoid_: confusing it with the **Board-priority ledger** — priority is a product question (which Boards deserve the slice), cost is an operational one (how to balance the shards). They decay differently and are read by different stages.

**Bin-packing**:
Splitting work items of uneven cost across a fixed number of parallel shards so each shard's total cost — its *makespan* — is as even as possible, since a fan-out run is only as fast as its slowest shard. The nightly-pipeline planners bin-pack Boards (weighted by the **Board-cost ledger**'s measured seconds) across scrape shards and **Docs** (weighted by per-**Bucket** embed cost) across embed shards (ADR-0025, ADR-0027).
_Avoid_: conflating with **Bucket** — a Bucket is a token-length class for one Doc; bin-packing distributes many items across shards, and one shard mixes Docs from several Buckets.

**LPT (Longest Processing Time first)**:
The greedy heuristic the planners bin-pack with: sort the items by descending cost, then hand each next item to whichever shard is currently least-loaded. Chosen over hashing or round-robin because per-item cost is heavy-tailed — embed cost spans ~20× from the ≤512 to the ≤4096 **Bucket** — so a cost-blind split reliably saddles one shard with the heavy items and it straggles while the rest idle (ADR-0025).

**GitHub VM**:
The term for the GitHub Actions jobs a matrix fan-out spreads across separate machines — the scrape and embed shards of ADR-0025/ADR-0026 each run on their own GitHub VM. Say "GitHub VM" whenever the point is the _machine_: its own IP (why per-host scrape politeness is unchanged), its own cold filesystem (why every shard re-runs checkout, `pip install`, and a model download), and its own memory and disk (why shard state moves as artifacts, never shared storage).
_Avoid_: using it for a **shard**, which is the unit of _work_ a planner assigns — one shard runs on one GitHub VM, but the shard is the board list or Doc list, not the machine.

## Relationships

- A **Company** runs its **Board** on exactly one **ATS**, located by its **Slug**.
- A **Scraper** (one per **ATS**) reads a **Board** and produces **Jobs**.
- **Discovery** collects **Companies** (each as an `(ATS, slug)`) via **Feeders**; **Liveness** sorts their **Boards** into Live / Dead / Unknown and writes the Live ones to the **Active list**; **Resolve** maps a known **Company** to its `(ATS, slug)`.
- The scrape step runs **Scrapers** over the **Active list** and assembles the **Feed**.
- The alerts run ranks **Jobs** from the **Search index** against each **Subscription**'s **Query**, and delivers the ones past its **Watermark** as one **Digest** over that Subscription's **Transport**.

## Example dialogue

> **Dev:** "When Liveness marks a Company's Board **Live**, does that mean the Company is hiring?"
> **Domain expert:** "No — **Active** just means the **Board** answered and we could read a count. A company with an open board but zero openings is still **Live**; 'currently hiring' is the subset with a count above zero. And don't conflate the **Board** with the **Careers page**: the careers page is the company's own HTML, the board is what the ATS serves — Discovery scans careers pages to *find* boards."

## Flagged ambiguities

- **"provider" vs "ATS"** — used interchangeably across code and docs; resolved: **ATS** is canonical.
- **"board" vs "careers page"** — distinct: **Board** is the ATS-hosted listing; **Careers page** is the company's own page that links or embeds it.
- **"posting/opening" vs "Job"** — resolved: **Job** is the normalized record; "posting" names the raw ATS record before normalization.
- **"active"** — overloaded between "the board responds" (**Live**) and "currently hiring" (Live with count > 0); resolved: the **Active list** is the Live set, and "hiring" is the count-filtered subset.
- **"Tenant" (retired)** — previously the `(ATS, slug)` pair. Dropped as a term: a **Company** *is* the thing on an ATS, located by its **Slug**, so we just say "a Company's slug on an ATS." The data still carries a `tenant` column (and the `data/ats-tenants-merged/` dir, `slug_from(tenant, …)` param keep the name) — a code/data rename is a separate change, not yet done.
