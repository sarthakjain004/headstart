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

**Parked**:
A real, Live Board deliberately withheld from the scrape for now, because scraping it costs more than the run can afford (`config.PARKED_BOARDS`). Distinct from **Excluded** (`config.EXCLUDED_BOARDS`), which names Boards that are not genuine Boards at all — vendor test and sandbox tenants. A Park is temporary and carries the condition that lifts it; an Exclusion is permanent.
_Avoid_: disabled (that names a whole ATS, `registry.DISABLED_ATS`), blocked, banned.

**Feed**:
The assembled `docs/jobs.json` the dashboard consumes. It is *derived* — built by reading the per-board `{ats}.jsonl` (the source of truth) back and deduping by Slug-aware id — and is the small **served curated subset**: the millions-scale harvest produces only the `.jsonl`, never a single feed.

### Alerts

**Subscription** (ADR-0035, ADR-0038, ADR-0042):
The one **Saved set** per **Account** whose email is turned on — its Query and Search filters plus the delivery machinery: its own **Watermark** and exactly one **Transport** to reach them by. Identified by a verified address or, for someone the bot enrolled, by their Telegram chat. Email delivery stays invite-only even though sign-up is open.
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
The one message a Subscription receives after a pipeline run — its best new matches capped at 30, each with its semantic score and apply link, plus a spreadsheet attachment carrying **every** new match, not just the capped few shown. One Digest regardless of **Transport**, though Telegram splits it across several messages to stay under the platform's size cap. No matches sends no Digest.
_Avoid_: alert, notification — a Digest is one batched message per run, not one per Job. Delivered over either **Transport**, so "email" is wrong for it too.

### Search

**Tech filter**:
The recall-biased gate that keeps only software/tech Jobs (ADR-0017). The scrape writes every Job to `data/jobs/{ats}.jsonl`; the filter derives the **Tech subset** in `data/jobs/tech/{ats}.jsonl`, which is what the Feed, the embedding, the **Search index**, and the UI read. Recall-first: a non-tech Job creeping in is tolerated, dropping a tech Job is not — a hard rule the verification gate guards.
_Avoid_: category filter, keyword filter — it is a role classifier, not a taxonomy lookup.

**Search index**:
The embedded, deduped set of Jobs the semantic query runs against — the corpus the AI search actually serves. Built from the **Tech subset** (`data/jobs/tech/{ats}.jsonl`) and kept current by **Eviction**. Distinct from the **Feed** (the dashboard's assembled JSON) and from the **eval benchmark** — a frozen, labelled slice of Jobs used to *measure* search quality, deliberately held stable and *not* the live served corpus.
_Avoid_: database, vector store — those name the storage, not the served set.

**Eviction**:
Removing a Job from the **Search index** once its posting has closed, so a stale opening can never be a search result. Keyed on the fresh scrape: a Job whose id is absent from its Board's latest scrape is gone — but only where that scrape is authoritative. An **Unauthoritative Board** is subtracted from the scope outright, so nothing on it is ever evicted that run (ADR-0053); of what remains, a Board that would lose more than a quarter of its rows at once reads as a silent truncation rather than a wave of closures and its evictions are **held** instead (ADR-0046). The freshness counterpart to embedding newly-seen Jobs.

**Unauthoritative Board** (ADR-0053):
A **Board** whose scraped list this run cannot be read as its complete set of openings — the scraper gave up mid-crawl and reported the list truncated, or the scrape raised. A property of the run, not of the Board: the same Board is authoritative again on the next scrape that finishes. This set is the one thing the scrape tells **Eviction**, written afresh every run to `data/state/unauthoritative_boards.json` (`scrape_join.write_unauthoritative_boards`, read back by `index_plan.read_unauthoritative_boards`) and subtracted from the eviction scope.
_Avoid_: failed Board, partial Board — a truncated Board still returned real Jobs and they are still indexed; it is only the absences from its list that cannot be trusted.

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
Text a user pastes or uploads to have their **Profile** extracted from it. The document itself is never stored or logged — it is read once by the extraction call and discarded; the **Profile** is the only thing that survives, and contact details are never part of it (ADR-0041, which superseded the earlier "nothing survives at all" rule).
_Avoid_: CV. And keep it apart from **Profile** — the Résumé is the transient input, the Profile is the stored extraction.

**Résumé query**:
The role sentence an LLM writes from a **Résumé** — stored as the **Profile**'s sentence, editable there, and shown in the search box before it runs. Subject to the same rule as any Query: it names a role and must not carry years, salary, or location, however loudly the **Résumé** states them.

### Accounts

**Account** (ADR-0042):
A signed-in person, identified by the verified address their Google sign-in proves. The whole UI sits behind sign-in and anyone may create an Account; the costly paths keep their own gates — **Digest** delivery stays invite-only, **Résumé** parsing is capped per Account.
_Avoid_: user, subscriber — an Account is the identity; whether it receives email is the **Subscription**'s question.

**Profile** (ADR-0041):
The stored, structured extraction of an Account's career: one role sentence (the **Résumé query**) plus facts — current title, years of experience, skills, past roles, education, location. Built by one LLM call from a **Résumé** or edited by hand; the document it came from is discarded, and contact details are never kept. Split by purpose: the sentence drives ranking, the facts pre-fill **Search filters** — a Profile never smuggles years or location into the **Query**.
_Avoid_: résumé — that names the transient input, not this stored record.

**Saved set** (ADR-0042):
One named **Query** + set of **Search filters** an Account keeps. The Matches tab runs a Saved set live — every matching Job, ranked, "new" marked where `first_seen` is known. Created from a working search ("Save this search"), never from a blank form. An Account may keep several; the one with email turned on is its **Subscription**.
_Avoid_: alert — a Saved set is a page first; email is one optional outlet on one of them.

**Saved job** (ADR-0042):
A **Job** an Account starred, kept as a copy of its display fields from the moment of starring so **Eviction** cannot erase it; once the posting leaves the **Search index** it shows as "closed" rather than vanishing.
_Avoid_: bookmark, favourite.

**Match ring**:
The match percentage displayed on a search result — the raw cosine score stretched through two fixed anchors (≈0.60 → 0%, ≈0.85 → 100%, tuned once against real queries, revisited only when the embedding model changes). Display only: ranking orders by the raw score.
_Avoid_: reading it as a probability, or re-scaling it per results page — the same Job must show the same percentage wherever it appears.

### Pipeline scheduling and sharding

**Board-priority ledger (EWMA)**:
The per-Board tech-yield score in `data/state/board_priority.csv`, kept as an **EWMA** — an Exponentially-Weighted Moving Average. Each run blends a Board's fresh tech-Job count into its prior score (`0.7·now + 0.3·history`), so recent nights dominate while older counts decay rather than being forgotten or weighted equally (ADR-0022). Boards the run didn't scrape keep their score unchanged — a partial harvest must not decay what it never looked at — and a score decayed below a floor drops out. The ledger orders both the scrape slice (high-yield Boards first) and the within-**Bucket** embed order.
_Avoid_: reading it as an exact count — it is a decaying average, an estimate of a Board's tech yield. _Avoid_: using it as a **cost** estimate. It answers "is this Board worth scraping?", not "how long will it take" — ADR-0026 conflated the two and the resulting pack was measurably useless. Cost lives in the **Board-cost ledger**.

**Board-cost ledger (measured seconds)**:
The per-Board _measured_ scrape wall time in `data/state/board_cost.csv`, the cost estimate the scrape planner bin-packs on (ADR-0027). Every scrape shard times each Board it scrapes and streams the row to its fragment; the join EWMA-blends them (`0.5·now + 0.5·history` — wall time is noisier than a Job count, so it leans on history harder than the priority ledger does). A Board with no measurement yet is estimated from its ATS's median, never from one global constant.
_Avoid_: confusing it with the **Board-priority ledger** — priority is a product question (which Boards deserve the slice), cost is an operational one (how to balance the shards). They decay differently and are read by different stages.

**Description-gap ledger (unsettled counts)**:
Per **Board**, how many of its already-embedded **Jobs** the **Description store** has never settled — `data/state/board_description_gap.csv`, recomputed from scratch each run (ADR-0062). Those Jobs' derived columns cannot be repaired, because re-deriving without the text a value came from could only downgrade it, so the only fix is to scrape the Board again — and the priority ordering never reaches Boards that have not earned a score. `scrape_plan` therefore reserves `GAP_FRAC` of the _exploration_ tail for them, cheapest ATS class first. Keyed on the **board_key** shape like the **Board-priority ledger**, but **lowercased** (via `key_for`): 1,693 of 13,708 gap Boards matched the live slice only case-insensitively, and folding ADR-0023's case-variant pairs keeps one Board from becoming two half-counts.
_Avoid_: reading it as a backlog the slice can fully drain — 166 of its Boards are dead, excluded or parked and can never be picked, and `join`'s rows leave by eviction, not repair. _Avoid_: treating a Board's presence in the slice as evidence the quota picked it; with ~12k gap Boards against a ~14k random tail, coincidence dominates the ~700 reserved slots.

**Re-derivation queue**:
`data/state/pending_rederive.txt` — **Job** ids whose description settled _this run_, so their stored metadata still carries numbers derived without that text (ADR-0062). `update_descriptions` appends, `update_meta` re-runs the extraction cascade for exactly those rows and empties the file. Only ids the embedding store already holds are queued: a Job first embedded this run had its metadata written from this very description.
_Avoid_: deleting the file to clear it — the merge uploads `data/state` without `--delete`, so an unlink never reaches the dataset and the queue would be re-fetched and re-appended forever. Truncate instead.

**Fan-out speedup ledger (measured ratio)**:
How much faster a scrape shard runs than the **sum** of its Boards' measured seconds, in `data/state/shard_speedup.csv` — one row for the whole fan-out, not one per Board (ADR-0054). A shard scrapes its Boards concurrently, so its wall clock is that sum divided by the concurrency the run actually achieves; the planner predicted the sum itself and over-stated every run by ~3x, which left the "exceeds the shard budget" warning firing on all of them and signalling nothing. The ratio is an EWMA the join blends from each run's shard reports, and the makespan prediction is `max(slowest Board on the shard, serial ÷ speedup)`.
_Avoid_: assuming it is the worker count — nominal concurrency is 16, the measured speedup ~2.8x, because per-host politeness, rate limits and stragglers eat the rest. _Avoid_: measuring it against a shard's own predicted minutes (that figure is derived from this ledger, so the estimate would chase its own tail) or against a shard killed by the budget (its wall clock measures the budget, not the work).

**Bin-packing**:
Splitting work items of uneven cost across a fixed number of parallel shards so each shard's total cost — its *makespan* — is as even as possible, since a fan-out run is only as fast as its slowest shard. The nightly-pipeline planners bin-pack Boards (weighted by the **Board-cost ledger**'s measured seconds) across scrape shards and **Docs** (weighted by per-**Bucket** embed cost) across embed shards (ADR-0025, ADR-0027). The scrape pack balances cost *subject to* a ceiling on how many Boards of one ATS a single shard may take, so no shard exhausts one ATS's **Origin budget** while other shards leave theirs unspent (ADR-0047).
_Avoid_: conflating with **Bucket** — a Bucket is a token-length class for one Doc; bin-packing distributes many items across shards, and one shard mixes Docs from several Buckets.

**LPT (Longest Processing Time first)**:
The greedy heuristic the planners bin-pack with: sort the items by descending cost, then hand each next item to whichever shard is currently least-loaded. Chosen over hashing or round-robin because per-item cost is heavy-tailed — embed cost spans ~20× from the ≤512 to the ≤4096 **Bucket** — so a cost-blind split reliably saddles one shard with the heavy items and it straggles while the rest idle (ADR-0025).

**Detail pass**:
The second fetch a scraper makes per **Job**, after the listing endpoint — the one that fills fields the listing omits, usually `description`. It is what makes an ATS expensive: one request per Job rather than one per **Board**, which is how a single provider comes to spend a whole **Origin budget**. A Job whose detail we already hold is skipped (ADR-0048); the scrape layer is told only *that* we hold it, never that it is embedded, so scrapers never depend on the embedding stage. "Already hold" means the **Description store** has the text, or has recorded that this posting has none — until ADR-0050 it meant merely that the Job had been embedded, which is why a Job embedded without a description was skipped forever and could never be repaired. Only some ATSes have a detail pass; where there is none, every field arrived with the listing and so cannot go missing.
_Avoid_: "enrichment" — the detail pass fetches primary fields, it does not derive them.

**Role watchlist**:
A curated set of specific roles tracked as their own trend lines, matched on **Job** title patterns rather than by embedding (ADR-0051, amended by ADR-0052). It exists for any role the centroid fit cannot express — whether too small to earn a cluster (a Forward Deployed Engineer is under 0.2% of the rows the fit clustered, so it smears across general software clusters) or large but unclusterable, like Backend, which k-means cannot separate because it split the software-engineering catch-all by seniority and phrasing rather than by domain. A pattern buys two things a centroid cannot: you can say exactly why a Job counted, and a centroid refit leaves it untouched. A watched role is counted *in addition to* the family it belongs to, never instead of it.
_Avoid_: calling a watched role a **Role family** — a family partitions the corpus and sums to it; a watched role is an observation laid over the top and deliberately overlaps.

**Description store**:
The persisted text of every tech **Job**'s description, kept so it survives a run whose **Detail pass** failed (ADR-0050). Before it, a description was read once — at embed time, to build the **Doc** — and then discarded, so a Job scraped without one was embedded from its title alone and stayed that way permanently. It records two distinct facts, not one: *we hold this description*, and *this posting has none*, the second settled only by a detail fetch that actually answered. A Job in neither state has never been settled and is fetched again. Written and read on CI in the join stage, never shipped to a scrape shard.
_Avoid_: "cache" — a cache may be cleared, and clearing this one un-skips every **Detail pass** at once. Also distinguish it from the *embedding store*, which holds vectors; this holds text.

**Origin budget**:
How much an ATS's edge will serve one network origin before it starts refusing — first 429, then, on Eightfold, a hard 405. It is a *rate*, not a fixed quota, and it is metered per origin across **all** of that ATS's tenants, so hammering one Board spends the allowance for every other Board on the same provider. Because each **GitHub VM** has its own IP, a fan-out run holds one budget per shard; spreading an ATS's Boards across shards spends all of them, while clustering wastes all but a few (ADR-0047).
_Avoid_: calling it a quota or a limit per Board — both suggest a fixed per-tenant count, and it is neither.

**GitHub VM**:
The term for the GitHub Actions jobs a matrix fan-out spreads across separate machines — the scrape and embed shards of ADR-0025/ADR-0026 each run on their own GitHub VM. Say "GitHub VM" whenever the point is the _machine_: its own IP (which is both why per-host scrape politeness is unchanged and, per **Origin budget**, a rate-limit allowance the planner now spends deliberately), its own cold filesystem (why every shard re-runs checkout, `pip install`, and a model download), and its own memory and disk (why shard state moves as artifacts, never shared storage).
_Avoid_: using it for a **shard**, which is the unit of _work_ a planner assigns — one shard runs on one GitHub VM, but the shard is the board list or Doc list, not the machine.

## Relationships

- A **Company** runs its **Board** on exactly one **ATS**, located by its **Slug**.
- A **Scraper** (one per **ATS**) reads a **Board** and produces **Jobs**.
- **Discovery** collects **Companies** (each as an `(ATS, slug)`) via **Feeders**; **Liveness** sorts their **Boards** into Live / Dead / Unknown and writes the Live ones to the **Active list**; **Resolve** maps a known **Company** to its `(ATS, slug)`.
- The scrape step runs **Scrapers** over the **Active list** and assembles the **Feed**.
- The alerts run ranks **Jobs** from the **Search index** against each **Subscription**'s **Query**, and delivers the ones past its **Watermark** as one **Digest** over that Subscription's **Transport**.
- An **Account** keeps one **Profile**, any number of **Saved sets**, and its **Saved jobs**; at most one Saved set per Account is its **Subscription**.

## Example dialogue

> **Dev:** "When Liveness marks a Company's Board **Live**, does that mean the Company is hiring?"
> **Domain expert:** "No — **Active** just means the **Board** answered and we could read a count. A company with an open board but zero openings is still **Live**; 'currently hiring' is the subset with a count above zero. And don't conflate the **Board** with the **Careers page**: the careers page is the company's own HTML, the board is what the ATS serves — Discovery scans careers pages to *find* boards."

## Flagged ambiguities

- **"provider" vs "ATS"** — used interchangeably across code and docs; resolved: **ATS** is canonical.
- **"board" vs "careers page"** — distinct: **Board** is the ATS-hosted listing; **Careers page** is the company's own page that links or embeds it.
- **"posting/opening" vs "Job"** — resolved: **Job** is the normalized record; "posting" names the raw ATS record before normalization.
- **"active"** — overloaded between "the board responds" (**Live**) and "currently hiring" (Live with count > 0); resolved: the **Active list** is the Live set, and "hiring" is the count-filtered subset.
- **"ATS provider" (UI label only)** — the search rail's ATS dropdown is labelled "ATS provider" for job-seekers (its values are greenhouse, lever, …). It briefly said "Board", which the glossary makes wrong — a **Board** is one company's listing, not the system hosting it. Internally the term stays **ATS** and the param stays `ats`; "provider" remains avoided in code and docs.
- **"Discover" (rejected tab name)** — the Search tab is called Search, not Discover: **Discovery** already names finding Companies on ATSes, and a UI label colliding with a glossary term would make every future conversation disambiguate.
- **"match"** — three related things: a *match* is a **Job** a **Saved set**'s Query and filters admit; the Matches *tab* is that set run live; the **Match ring** is only the displayed score. None of them is the **Subscription**, which is the emailing Saved set.
- **"Tenant" (retired)** — previously the `(ATS, slug)` pair. Dropped as a term: a **Company** *is* the thing on an ATS, located by its **Slug**, so we just say "a Company's slug on an ATS." The data still carries a `tenant` column (and the `data/ats-tenants-merged/` dir, `slug_from(tenant, …)` param keep the name) — a code/data rename is a separate change, not yet done.
