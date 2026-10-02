# Flapping root cause investigation — 2026-10-02

## What is confirmed

The original audit's **83 unique IDs / 86 actual return events** all came through ordinary `sync` eviction. There were no duplicate-prune or off-Board-prune paths among these events. Counts: Radancy 67, Workday 11, Happydance 3, Greenhouse 1, Oracle 1. This is not the larger already-known-add rate, which includes previously observed additions without a deletion. All 86 events have the exact ID in their eviction run's premerge Unconfirmed state: the two-scrape grace period is functioning, not being bypassed.

**The confirmed architectural failure is that upstream listing identity and successful Job parsing are conflated.** Radancy reads an ID and URL from its sitemap, but a missing detail yields no parsed Job. Its `fetch_raw` retains the ID in an internal `items` list; `parse` discards the item if no title can be obtained. The raw corpus, tech corpus, and facts ledger therefore lose the listing identity before downstream lifetime decisions can distinguish "listed but unread" from "not listed". When the detail loss is <=1% of the Board's total, the Board remains authoritative. Two such misses of an indexed Job become an eviction, even when both fetched sitemaps contained its ID.

This is reproduced deterministically through **production `RadancyScraper.parse`, `mark_truncated_unless_negligible`, and `index_plan.plan_sync`**. The fixture takes the actual recorded URL/identity `radancy:jobs.citi.com:101076861632` from a historical Job fact, removes its detail, and models 100 of 101 detail successes. First scrape marks it Unconfirmed; second scrape deletes it. The script intentionally exits nonzero on the unsafe deletion. Run:

```sh
PYTHONPATH=$PWD/src /Users/sarthakjain/Projects/HeadStart/.venv/bin/python experiment/pipeline-reliability-fixes-2026-10-02/flapping_reproduce.py
```

The fixture is a replay of the decision mechanism with an injected missing detail; it is **not a captured historical HTTP failure for this particular ID**. A second replay, `flapping_captured_reproduce.py`, uses a real captured HTTP404 response for `radancy:careers.arm.com:99112111664`, which the current sitemap lists. That replay produces the same two-scrape deletion through production parsing/planning. The404 HTML, status, URL and capture timestamp are retained as `listed-404-response.*`; its 101-row denominator is synthetic. These prove the failure mode, not that all 67 Radancy postings stayed listed at every historical eviction.

## Exhaustive per-ID classification

Selective ZIP member extraction completed for **all eight corpus snapshots**, reading only five provider tech corpora and the Listed-state parquet; 2,311,248,686 compressed range bytes were read, no full 26 GB archives were downloaded. Only the 83 target IDs' records were retained. Every event's Board-read is authoritative. **85/86 events (82 unique IDs) have neither a parsed-Job Listed-state entry nor a tech Job at eviction**. Thus they disappeared before the tech filter/embedding eligibility boundary. **The remaining event is Jabil `99989006256`**, which remains parsed and tech throughout and instead fails the English embedding gate. No other flapping ID occurs in the captured pending_non_english sets.

The per-ID run histories are in `all-id-timelines.json`; `per-id-matrix.md` lists all 83 ID, eviction run, return run, tech/Listed-state presence and authority. Listed-state presence is evaluated alongside Board authority because nonauthoritative Boards retain unobserved old rows. A Listed-state row is a parsed Job identity, not the original sitemap/API row.

## Historical observations and competing explanations

All eight completed executions' per-Job fact partitions and Board-read partitions were read. The resulting 278 fact events for the 83 IDs are retained. Of the 86 eviction→return events, **53 return with exactly the same raw-field hash as the preceding unlisted fact**, two have changed hashes, and 31 have no comparable unlisted fact within the captured window. The 53 identical comparisons comprise49 Radancy events,3 Happydance events,1 Greenhouse event. The two changes are `oracle:ecyq.fa.em2.oraclecloud.com:7520` and `workday:bbinsurance/Careers:R26_0000002771`. These hashes cover non-description posting fields (`job_facts.RAW_FIELDS`); description text is explicitly excluded. They supply no evidence of a change to those fields on return, but do not rule out a description change or a genuine upstream temporary withdrawal/republication.

Ranked hypotheses tested:

1. **Details disappear before identity retention.** Production replay confirms this failure mechanism. Historical Board-level loss lines confirm affected Boards lost detail pages, commonly HTTP 404. Logs do not name each failed page, so a Board count cannot identify a particular ID.
2. **The upstream listing temporarily delists/re-lists a posting.** Not ruled out. Original sitemap bodies/native row IDs were not retained in published artifacts. Current sitemap/detail disagreement demonstrates ambiguity; historical uninterrupted existence cannot be established retroactively.
3. **The tech filter flips on changed or degraded fields.** All 82 missing-parsed IDs were absent before the tech filter; this rules out tech-filter-only rejection as their disappearance path. The remaining Jabil ID stayed tech but lost English eligibility on a changed description (see below). The 53 identical-hash return events additionally have no observed non-description field edit to explain a title/department rule flip; that hash does not cover description text. A parser-dropped row cannot be classified as a tech-filter rejection merely because it is missing from tech.
4. **Dedup/identity rotation.** The actual eviction path disproves duplicate-prune/off-Board-prune as the recorded cause of these 86 events. A separate already-known-add analysis must not be substituted for this result.

## Current Radancy evidence: every affected ID checked

On 2026-10-02, direct HTTP GETs read all 15 affected Radancy sitemaps (all HTTP 200), then checked **all 67 affected Radancy IDs**. For an ID present in its current sitemap the current sitemap URL was used; otherwise the historical recorded detail URL was used. No WARP configuration was changed. `RadancyScraper.read_detail` parsed every HTTP 200 response.

| Current observation | IDs |
|---|---:|
| Sitemap present, readable detail HTTP 200 | 32 |
| Sitemap present, detail HTTP 404 | 9 |
| Sitemap absent, historical detail URL readable HTTP 200 | 20 |
| Sitemap absent, historical detail URL HTTP 404 | 6 |
| **Total** | **67** |

Thus **52/67 currently have readable detail pages**, 41/67 remain in the sitemap, and **9/41 sitemap-listed IDs return HTTP 404**. This rejects the proposed simplification "a detail 404 proves the listing closed." It also shows why using today's sitemap absence to prove a posting was closed in the audited hours would be unjustified: 20 sitemap-absent postings still return full JobPosting fields at the recorded page URL.

The nine listed-but-404 IDs are:

- `radancy:careers.arm.com:99112111664`
- `radancy:jobs.boeing.com:98710336704`
- `radancy:jobs.citi.com:100178752608`
- `radancy:jobs.citi.com:100430326352`
- `radancy:jobs.citi.com:100957978176`
- `radancy:jobs.citi.com:101394281136`
- `radancy:jobs.citi.com:101394281184`
- `radancy:jobs.citi.com:96525870256`
- `radancy:jobs.heraeus.com:42006480768`

These are evidence of present upstream inconsistency. A 404 can reflect real closure with a stale sitemap, temporary backend replication, or a edge/application error. No additional HTTP response evidence discriminates those causes, and the report does not call them 404 IP blocks.

## Provider cohorts

### Radancy: 67 IDs

The largest cohorts are Citi 25, Boeing 16, and Sanofi 8. Others: IKEA 3, Comcast 3, Empower 2, GPC 2, and one each from Staples, Arm, Jabil, CCEP, Heraeus, Veolia, Hackensack Meridian Health, Alter Domus. Citi has 23/3161 unread details on run 36956808836 (22 HTTP404, one HTTP403) and remains authoritative at 99.272%; run 36969207861 has23/3161, all404. Boeing loses3/1109,3/1109,6/1109,1/1109 on the first four runs and remains within tolerance. This is direct evidence of the reproduced vulnerable Board path.

### Jabil: one confirmed language-eligibility flip

`radancy:jobs.jabil.com:99989006256` remains in both parsed Listed state and tech corpus on all eight snapshots. Runs 36947999423→36956808836 contain a 9,212-character English network/security description and Full Time employment. Runs 36961106691 and36965091289 instead contain a 2,168-character Spanish industrial-engineering description and Part Time employment; title/URL still identify the English Lead Network Security/Fortinet role. The exact ID is written to `pending_non_english.txt` on36961106691, and its vector is dropped. The first run grants index grace, the second evicts it. Run36969207861 restores the 9,212-character English body/Full Time and the row returns.

Production `doc_prep.is_english` replay on every captured description returns True on the six full English bodies and False on both Spanish bodies. Hashes and detector results are retained in `jabil-language-gate.json`. This is a **confirmed representation change triggering the intentional English policy**, not a random language-detector error or sitemap loss. The original detailHTML is absent, so whether the server returned mixed JobPosting content or the parser chose an inconsistent node cannot be distinguished. It deserves identity-to-detail validation separately from lifetime protection.

### Workday: 11 IDs

Walmart4, Thales6, BBInsurance1. Walmart's first audited scrape explicitly reports **four no-title/no-externalPath stubs among 846 listings**. Production `WorkdayScraper.parse` skips those stubs under ADR-0348; a sub1% unread-row loss does not remove the Board from authority. This is the same listing-identity loss shape as Radancy, with missing metadata instead of unread HTML. However, the logs do not identify the four stub IDs, so equal cohort counts alone are not proof that all four flapping Walmart IDs were those stubs.

Thales has one no-title/no-externalPath stub among 2,592 rows on run 36961106691. Its other logs record small detail losses, but unlike Radancy, a Workday posting with a title/path survives ordinary detail failure and remains a Job. Therefore assigning all six Thales flaps to generic detail errors would be false. Their precise upstream listing absence requires native listing rows, which were not retained.

BBInsurance's return has a changed raw hash. No precise upstream cause is established by its Board's aggregate logs.

### Happydance: 3 IDs, 3 actual return events

Two SAP IDs and one Cognizant ID each have one actual eviction→return event. Their facts histories also contain additional unlisted→listed transitions that did not necessarily evict from the served index; the two-scrape grace period explains why these counts differ. SAP's Board reports4/41 pages missing as **"closed (a shell page with no posting)"** on runs 36952453763,36961106691,36965091289. `HappydanceScraper.fetch_raw` subtracts these shell outcomes before its unread-loss authority calculation, so even four shell results remain authoritative rather than scope-excluded. The recorded SAP unlisted/listed fact sequences and unchanged return hashes match a repeated upstream/front representation disappearance. The three extra actual return events above 83 unique IDs belong to three IKEA IDs, each evicted and re-added twice. Failed-detail identities are not logged, so shell attribution is strong cohort evidence rather than per-ID proof. Cognizant has repeated listing-count changes but no logged unread-detail failures naming the target ID; its exact upstream cause remains unresolved.

### Greenhouse and Oracle: one ID each

Greenhouse `evismart:4364503009` is unlisted on36965091289 and returns36978136408 as Senior Network & IT Support Engineer. Oracle `7520` is unlisted36947999423 and returns36969207861 as IT Systems Engineer with a changed hash. No detail-failure trace names either ID, so a genuine upstream withdrawal/republication remains possible.

## Recommended correction and observability

Keep **upstream observed listing IDs separately from parsed/tech Job IDs**. Native IDs should be recorded before a title/detail requirement; unread identity must not be mistaken for a confirmed closure. Do not synthesize a new untitled Job, infer tech eligibility from a slug, or silently refresh stale fields. Preserve the old indexed record when the listing says the ID still exists but its new metadata cannot be read.

Scope authority measures completeness of the **listing**, not the percentage of full detail bodies recovered. A sub1% Board-wide detail tolerance can still remove a large share of a small tech subgroup; adjusting its threshold alone does not solve the missing identity distinction.

For future proof, retain failed detail IDs/URLs and structured loss class, native listing IDs, parser-rejected IDs/reasons, tech-rejected IDs/reasons, and sync eviction reasons. Then a per-ID lifetime report can distinguish native-list absent, listed-but-unread, tech rejection, language rejection, and true off-Board/dedup removal. The current logs omit the failed detail identities, so no analysis can honestly reconstruct each historical HTTP cause from aggregate counts alone.

No production flapping fix is made in this diagnostic work; the user asked to find the cause. A cross-provider identity channel changes lifetime policy and should be reviewed as a separate implementation.

## Evidence files

All supporting scripts/artifacts live under `experiment/pipeline-reliability-fixes-2026-10-02/`. In `artifacts/flapping/`: `fact-events.json`, `return-field-comparison.json`, `all-id-timelines.json`, `live-all-radancy.json`, `two-scrape-reproduction.json`, `reproduction.log`, and `LOG.md`. Timeline entries include Board-read authority, scope, premerge Unconfirmed membership, facts, exact source log file/line, and selective corpus membership for all eight completed snapshots.
