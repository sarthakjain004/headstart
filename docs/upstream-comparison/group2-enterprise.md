# Group 2 (enterprise ATSes): ours vs. kalil0321/ats-scrapers

Scope: icims, oracle, phenom, taleo_enterprise (+ taleo_be), successfactors, eightfold.
OURS = `wt-main/src/headstart/scrapers/` @ origin/main 8efb9a85. THEIRS = `kalil/src/ats_scrapers/scrapers/` @ 6b44a1b.
Both files read in full on each side. Live probes were run 2026-09-22 and are labelled with their sample size.

---

## Ranked findings

### 1. successfactors — `/sitemal.xml` is a *second* surface on the same tenant: the full Google-jobs RSS, with inline full descriptions. ADOPT. CODE-EVIDENCED + LIVE-MEASURED.

**Theirs:** `successfactors.py:189` — `_resolve_feed_url()` returns `f"{base}/sitemal.xml"` (the typo path), unconditionally, for every non-legacy tenant. Docstring line 10 calls it "the canonical (typo-included, undocumented but stable) path".

**Ours:** `successfactors.py:97` — `url()` returns `https://{slug}/sitemap.xml` and nothing else. Our module docstring (lines 10–22) treats *sitemap.xml* as having two possible **kinds** — urlset or RSS — and builds a three-surface cascade around that, then fetches **one job page per posting** to get any field at all (`fetch_raw`, `successfactors.py:330-342`).

**What I measured** (4 tenants, 2026-09-22):

| tenant | `/sitemap.xml` | `/sitemal.xml` | ids in sitemap | ids in RSS | diff |
|---|---|---|---|---|---|
| `basf.jobs` | urlset, 167 KB, 2.3 s | RSS, 8.17 MB, 36.8 s | 789 | 789 | **0 / 0** |
| `ace1950.jobs2web.com` | urlset, 12 KB | RSS, 212 KB | 60 | 60 | **0 / 0** |
| `apply.careers.hsbc.com` | (not fetched) | RSS, ≥6.6 MB streaming | – | ~825 items | – |
| `aramarkcareers.com` | (not fetched) | RSS, ≥5.9 MB streaming | – | ~697 items | – |

On `basf.jobs` every RSS item carried `title`, `description`, `link`, `guid`, `id`, `expiration_date`, `employer`, `job_function`, `location` — **789/789 on all nine**. Description plaintext length p10/p50/p90 = **2,133 / 4,203 / 6,384** — full bodies, not teasers (matches our own docstring's claim that "the feed carries full descriptions").

**Why this matters more than it looks.** We currently pay **one detail HTTP fetch per posting** on this ATS, for every tenant, because the urlset "carries no indexable fields" (our docstring, line 24). But the RSS at the *sibling path* carries every field we extract — on a urlset tenant, which is the majority. For BASF that is **1 GET (8 MB, 37 s) instead of 789 GETs (~40 KB each ≈ 31 MB + 789 round trips)**. The RSS is not a *different* board: id parity was exact on both tenants I could check whole.

**Our own docstring is what hid this.** It says the RSS "trickles at ~30 KB/s, so it is never read whole up-front", and that is why surface 3 is a last resort. Measured today the same generator ran at **~220 KB/s (basf), ~165 KB/s (hsbc), ~148 KB/s (aramark)** — 5–7× the recorded figure. That number is from 2026-07-21 and should be re-measured before it is used to justify anything.

**Adopt as:** a cheap-first *field* surface, not a replacement for the listing. Keep `/sitemap.xml` as the authority on *which* ids exist (that is what ADR-0053's completeness check keys on), then read `/sitemal.xml` once and fill fields from it, falling back to the per-job page only for ids the feed missed. Caveats that must be settled first: (a) our docstring records a tenant (Voith) whose feed aborts ~2 MB in — `_rss_job_urls`'s existing partial-read handling already covers that shape; (b) id parity must be re-checked on a tenant with >5k postings, which I did not fetch whole.

---

### 2. successfactors — an entire tenant class we do not scrape at all: the legacy Recruiting Management `Job-Listing` XML. ADOPT (new coverage). CODE-EVIDENCED + LIVE-MEASURED.

**Theirs:** `successfactors.py:146-190` (`_resolve_feed_target`), `:191-269` (`_parse_legacy_feed`), `:537` (`_is_legacy_host`). Second surface:
`GET https://career{N}.successfactors.{com,eu}/career?company={ID}&career_ns=job_listing_summary&resultType=XML`.

**Ours:** no such code path. Our docstring says "RMK only" (`successfactors.py:4`), and `url()` can only build `https://{vanity-host}/sitemap.xml`. Our liveness ledger confirms the gap: `grep -i "successfactors.com\|sapsf" data/validate/liveness/successfactors.csv` returns **7 rows, all `jobs2web.com` hosts** — not one `career{N}.successfactors.*` tenant exists in the ledger.

**What I measured** (2026-09-22): the endpoint is live and returns real data.
- `company=americairP` → HTTP 200, **950,617 bytes**, `<Job-Listing>` root, **106 `<Job>` elements** (American Airlines).
- `company=C0000161245P` → HTTP 200, **1,310,999 bytes**, **112 `<Job>`** (PG&E — "ADMS Specialist", Rocklin CA).
- `company=TESTCO` (invalid) → HTTP 200 serving an **HTML page**, not XML. A soft-404: their `_parse_legacy_feed` guards it by checking `root.tag != "Job-Listing"` (`:204`), which is correct and is a trap worth keeping.

Per-job element set measured on PG&E: `JobTitle`, `Job-Description`, `ReqId`, `Division`, `Department`, `Location`, `Posted-Date`, plus `filter{N}`/`mfield{N}` elements each holding a `<label>`/`<value>` pair. Descriptions are **inline and full** — no detail pass needed at all.

**Two real traps their code already handles, worth copying if we build this:**
- **`[[token]]` templating.** Measured on both tenants: `Job-Description` bodies contain literal `[[location]]`, `[[id]]`, `[[mfield1]]`, `[[division]]`, `[[custpositiontype]]`, `[[sfstd_jobLocation_obj]]`. A naive read serves those placeholders verbatim into the embedding corpus. Theirs substitutes from sibling elements at `:239-250` + `_render_template:560`.
- **Ambiguous date order.** `Posted-Date` is `09/16/2026` — theirs votes day-first vs month-first across the *whole feed* before parsing (`_legacy_dates_are_day_first:611`). That is a genuinely good idea and we have no equivalent anywhere.

**One thing their implementation does NOT have, and we would need:** any pagination or terminator. `_parse_legacy_feed` reads one response and stops. 106 postings for American Airlines is implausibly low for that employer, so either the portal is a narrow sub-board or the feed is capped — I did not settle which. Under our semantics an unbounded, unverifiable read is exactly the silent-short-read shape ADR-0053 exists to catch, so **establishing a terminator is a precondition**, not a follow-up.

**Prerequisite before any of this is worth building:** a discovery pass. We hold zero `career{N}` tenants, so the scraper would have nothing to run on until `company` codes are mined (they appear in the wild as `career4.successfactors.com/career?company=X` links on employer sites).

---

### 3. successfactors — `department` is hardcoded `None` on our side; the RSS states `g:job_function`. ADOPT (with a gate). CODE-EVIDENCED + LIVE-MEASURED.

**Theirs:** `successfactors.py:356` — `department = _first_text(item.findtext("g:job_function", ...))`.
**Ours:** `successfactors.py:429` — `department=None,` literally, in `parse()`. Nothing else in the module ever sets it; `_jsonld_fields` (`:604-613`) does not read `occupationalCategory` either, though our own icims scraper does (`icims.py:366`).

This is not cosmetic: `tech_filter.classify` reads **title + department**, so on this ATS the tech gate has been running on title alone for every posting.

**Measured presence and quality** (4 tenants):

| tenant | items | `g:job_function` present | top values |
|---|---|---|---|
| `apply.careers.hsbc.com` | ~825 | 464 | Branch and Retail Banking (133), **Technology (103)**, Commercial Banking (51) |
| `aramarkcareers.com` | ~697 | 696 | Food Service (365), Culinary (101), Custodial (44) |
| `basf.jobs` | 789 | 789 | **`ATS_WCMS_WEBFORM` (670), `ATS_WEBFORM` (116), `ATS_TALEO_APAC` (3)** |
| `ace1950.jobs2web.com` | 60 | 0 | – |

So the field is **tenant-configured and sometimes internal junk** — BASF publishes ATS routing codes in it. Adopt it, but with a sanity gate (reject `ATS_*`-shaped / all-caps-underscore tokens) rather than passing it straight through, or the tech filter starts classifying on `ATS_WCMS_WEBFORM`.

---

### 4. icims — their HTML surface *does* reach boards our sitemap cannot. It reaches exactly the boards that said not to. NOT-APPLICABLE (deliberate). LIVE-MEASURED.

**Theirs:** `icims.py:217-220` — the only surface is `GET {base}/jobs/search?ss=1&pr={page}&in_iframe=1`, paginated 0-indexed to `MAX_PAGES = 200` (`:58`), stopping when a page yields no new ids (`:180-182`). No sitemap at all.
**Ours:** `icims.py:157` — `/sitemap.xml` only; the HTML walk is refused on the measured grounds in the docstring (`icims.py:7-16`).

**I tested the exact claim.** Sampled 18 rows our ledger marks `dead`; 3 answered `/sitemap.xml` with **403**. On all three:

| host | robots.txt | `/jobs/search?ss=1&pr=0&in_iframe=1` | `iCIMS_JobCardItem` count |
|---|---|---|---|
| `careers-pappaspt.icims.com` | `User-agent: * / Disallow: /` | **200**, 43,380 B | **11** |
| `franchise-hrblock.icims.com` | `User-agent: * / Disallow: /` | **200**, 156,175 B | **50** |
| `internal-gvh.icims.com` | `User-agent: * / Disallow: /` | 200, 21,121 B | 0 (internal board) |

**Verdict:** the reach is real, not hypothetical — their scraper would ingest 2 of these 3 boards where ours records `dead`. And the robots correlation our CLAUDE.md asserts held **3/3**. So this is a policy decision we are making knowingly, and the documented reasoning survives contact: the only boards the HTML walk adds are boards serving `Disallow: /`. Nothing to adopt; worth recording that the claim was re-verified.

(Separately, their ledger identity is weaker: `_resolve_base_url` assumes `careers-{slug}.icims.com` with a `uscareers-` special case (`icims.py:212-215`), where we key on the host itself (`icims.py:146-154`). Ours generalises; theirs breaks on the third prefix.)

---

### 5. oracle — `ORA_HYBRID` is served as `remote=False` by us, contradicting our own stated convention. ADOPT (their mapping). CODE-EVIDENCED.

**Ours:** `oracle.py:89` — `return code == _REMOTE_CODE`. For `ORA_HYBRID` that is `False`.
**Theirs:** `oracle.py:53-58` — `_REMOTE_BY_CODE` maps `ORA_REMOTE`/`ORA_FULL_TIME_REMOTE`→True, `ORA_ON_SITE`/`ORA_ONSITE`→False, and **`ORA_HYBRID` is deliberately absent** so `.get()` returns `None`. Their comment says so explicitly.

Theirs matches **our own house convention**, stated in three of our other scrapers in almost identical words:
- `phenom.py:484` — "``Hybrid`` maps to None rather than False, matching `workday._remote_from`'s convention: a hybrid posting is not the remote role a remote filter is looking for, but calling it explicitly non-remote overstates what the Board said."
- `taleo_be.py:135-143` — `_WORKPLACE_ARRANGEMENT_PATTERNS` maps `"hybrid": None`, citing workday, ashby and eightfold.

Oracle is the outlier, and its own docstring (`oracle.py:69-76`) never addresses hybrid — it only justifies using the code over the label. This looks like an oversight rather than a decision. Note `ORA_FULL_TIME_REMOTE` is also unhandled by ours (`!= "ORA_REMOTE"` → False), which would serve a fully-remote posting as non-remote if any tenant uses that code — unmeasured on our side, worth checking in the same pass.

---

### 6. oracle — their `siteNumber=CX_1` default and their pagination are both worse, and two of their "richer" fields do not exist. THEIRS-IS-WORSE. CODE-EVIDENCED + LIVE-MEASURED.

Four separate defects, listed because each is a trap we already paid for:

- **`siteNumber` default.** Theirs: `oracle.py:45` `DEFAULT_SITE = "CX_1"`, injected into the finder at `:258`. Ours omits it entirely (`oracle.py:142-146`), on the measurement that `CX_1` was wrong for **929 of 1,331** hiring boards and wrong silently. THEIRS-IS-WORSE.
- **No offset ceiling, no truncation signal.** Theirs computes `offsets = range(page_size, total, page_size)` (`:176`) and breaks on an empty page (`:181`). It has no notion of the 10,000-row wall our `_OFFSET_CEILING` (`oracle.py:59`) encodes, and no `mark_truncated` equivalent — so a 78,431-posting board reads 10,000 and reports success. Under our eviction semantics that is a Board-wide false delisting.
- **`ExternalURL` as `Job.url`.** Theirs: `oracle.py:345`, falling back to a fragment URL `{base}/?keyword=&mode=jobs&...#{id}`. **I measured the listing key set on `aqa.fa.us1.oraclecloud.com`: 41 keys, `ExternalURL` is not one of them.** So on that tenant every job link would be the fragment form, which does not address a posting. Ours builds `/hcmUI/CandidateExperience/en/sites/CX_1/job/{id}` (`oracle.py:301`), browser-verified.
- **Concatenating three description sections.** Theirs: `oracle.py:234-243` joins `ExternalDescriptionStr` + `ExternalResponsibilitiesStr` + `ExternalQualificationsStr`. **Measured, 2026-09-22, 2 pods / 50 listing rows / 8 detail payloads: `ExternalResponsibilitiesStr` and `ExternalQualificationsStr` were non-empty on 0 of 50 listing rows and 0 of 8 detail payloads.** They exist as keys and are empty strings. No measurable gain. The same sweep found `WorkDurationYears`, `StudyLevel`, `ManagerLevel`, `LegalEmployer`, `Department`, `JobFunction`, `JobType`, `JobSchedule` all **0/50** on the listing — an independent confirmation of CLAUDE.md's "the listing carries almost nothing" measurement.

One detail-payload key neither side reads: `CorporateDescriptionStr` (1,212 chars) and `OrganizationDescriptionStr` (619) on the one posting I dumped. Both look like employer boilerplate — probably right to leave out, but nobody has looked.

---

### 7. taleo — theirs is Taleo **Business Edition**, it reads one page only, and it has no Enterprise scraper at all. THEIRS-IS-WORSE. CODE-EVIDENCED.

The task framing said "theirs is named `taleo`" as the counterpart to our `taleo_enterprise`. It is not: their `taleo.py:1` is Taleo **Business Edition** (`{ph}.tbe.taleo.net/.../searchResults`), the counterpart to our `taleo_be.py`. Their docstring (`taleo.py:3-4`) claims Enterprise "now lives on oraclecloud.com and is handled by `OracleScraper`", which is wrong — Taleo Enterprise Career Sections are `{zone}.taleo.net/careersection/...`, a different platform from Oracle Recruiting Cloud. **They have no Taleo Enterprise scraper. Ours (`taleo_enterprise.py`, 556 live boards) has no counterpart on their side at all.**

On TBE itself, the decisive gap: **theirs fetches exactly one listing page.** `afetch` (`taleo.py:111-121`) does one `get_text(url)` and parses it; there is no next-link follow anywhere in the file. Ours walks `jscroll-next` with a session cookie and a loop guard (`taleo_be.py:236-277`). I measured the first page of `org=NBF1199&cws=41`: **40 job links, and `jscroll-next` present** — against the ledger's 45 for that board. So theirs silently under-reads.

Two smaller things theirs gets right that are worth noting: it `html.unescape`s the href (`:162`, we do too at `taleo_be.py:261`), and it dedups the redundant "View" anchor by `rid` (`:157-160`, we dedup by id too at `taleo_be.py:244`). No gap.

---

### 8. taleo_be — TBE detail pages *do* carry JobPosting JSON-LD; our docstring implies otherwise. Marginal ADOPT (field fallback only). LIVE-MEASURED.

**Theirs:** `taleo.py:181-234` `_apply_jsonld_to_job` — reads `description`, `employmentType`, `datePosted`, `jobLocation`, `hiringOrganization.name` from JSON-LD.
**Ours:** `taleo_be.py:8-9` — "The detail HTML, rather than the JSON-LD assumed by several third-party clients, supplies the job description and labelled metadata."

**Measured on 3 boards, 2026-09-22:**

| board | JSON-LD `JobPosting` | our `cwsJobDescription` anchor |
|---|---|---|
| `org=NBF1199&cws=41` rid=11231 | **yes** — desc **1,587** chars, `datePosted: 2026-08-12 00:00:00.0`, `employmentType: Full time`, full `PostalAddress`, `hiringOrganization.name: "1199SEIU Funds"` | yes — desc **1,587** chars (identical) |
| `org=CLINIPACE&cws=41` rid=8094 (Caidya) | **no** | **no** |
| `org=YKHC&cws=41` rid=18951 | **no** | **no** |

So: where our anchor works, JSON-LD is exactly equivalent (1,587 = 1,587) — no gain. And it does **not** rescue the second layout our docstring names as the remaining loss (Caidya/YKHC), which carries neither. **This downgrades the finding from "free fix for the known gap" to "redundant path"** — worth stating, because reading only their code would suggest the opposite.

The residual value is narrow but real: `datePosted`, `employmentType` and the company name come from JSON-LD in one fixed place, whereas we read them from **tenant-configured label spans** (`_field(labels, "Date Posted", "Posting Date")`, `"Employment Type", "Job Type"` — `taleo_be.py:313-314`). A tenant that spells its labels differently loses those fields today and would not with a JSON-LD fallback. Also note their `datePosted` parser handles the `"2026-08-12 00:00:00.0"` form (`taleo.py:215-222`) — which is exactly what the live page served, and which our `_posted_at` format list (`taleo_be.py:164`) does **not** cover (`%Y-%m-%d %H:%M:%S` fails on the trailing `.0`).

---

### 9. eightfold — everything of theirs we already have, and more. ALREADY-HAVE. CODE-EVIDENCED + LIVE-MEASURED.

- **SmartApply fallback.** Theirs: `eightfold.py:196-224` + `_is_pcsx_unavailable:590` keying on the **English** strings `"pcsx is not enabled"` / `"not authorized for pcsx"`. Ours: `eightfold.py:351-411`, with `_PCSX_DISABLED = re.compile(r"pcsx", re.IGNORECASE)` (`:76`) precisely because a Spanish-locale variant exists on 2 of 23 measured tenants — **their English-phrase check silently misses those**. Ours also has a third surface (sitemap → per-job JSON-LD) that theirs lacks entirely.
- **`employmentType` / `yearsOfExperience`.** Theirs speculatively captures them into `raw` (`eightfold.py:521-527`). Ours writes `"employment_type": None,  # not exposed by the PCSX API` (`eightfold.py:504`). **Measured on `arcadis.eightfold.ai` (count=1347, first page of 10): positions carry exactly 11 keys — `atsJobId, creationTs, department, displayJobId, id, locations, name, positionUrl, postedTs, standardizedLocations, workLocationOption`.** No `employmentType`, no `yearsOfExperience`, no `category`/`team`/`businessUnit`/`skills`, and no `job_description` (which their `_parse_job:544` reads from the *listing*). Our comment is correct; their extra keys are dead code on this sample.
- **Retries.** Theirs: 3 attempts, `Retry-After` honoured, exponential on 429 / linear on 5xx (`eightfold.py:311-335`). Ours: `headstart/network/http.py` retries 403/405/429/5xx with jittered backoff, a capped honoured `Retry-After`, per-reason counters, **plus** a WARP second-egress escalation (`egress_fallback_on = frozenset({403, 405, 429})`, `eightfold.py:120`). Ours is strictly richer.
- **`ats_id`.** Theirs prefers `displayJobId` (`:487-494`) and then reverse-engineers the numeric position id back out of the URL for the detail call (`_position_id_from_url:599`). Ours keys on `id` directly. Ours is less fragile.

**The one thing theirs has that we do not:** `httpcloak` TLS-fingerprint impersonation as a 403 escalation (`fetch.py:312-335`, `base.py:62-64`, `eightfold.py:438-469`). We escalate a 403 onto a second egress IP instead. Different mechanism for the same wall; whether JA3 impersonation beats an IP change on Cloudflare-fronted eightfold tenants is unmeasured on our side. Worth a bounded experiment, not a code change.

---

### 10. phenom — every load-bearing thing theirs does differently, we already measured false. ALREADY-HAVE. CODE-EVIDENCED.

Our module docstring (`phenom.py:14-36`) already names all four, so this is confirmation rather than news:
- **CSRF/session.** Theirs spends a GET per board to seed cookies and scrape a token (`phenom.py:155-171`, `:221-222`). Ours doesn't (`phenom.py:14-18`: a bare client 200s).
- **Page size.** Theirs `PAGE_SIZE = 100` (`:40`). Ours 500 (`phenom.py:49`), the silent clamp ceiling — 5× fewer calls.
- **Description.** Theirs `item.get("description") or item.get("descriptionTeaser")` (`:338`). The listing has no `description` key, so that always resolves to the teaser (~350 chars vs the detail's 5,121) — **and marks the Job described**, so nothing ever fetches the real body. Ours reads the detail only (`phenom.py:422`).
- **The 10,000 result window.** Theirs fans out `range(len(jobs_first), total, PAGE_SIZE)` (`:137`) with no window check; past the wall `totalHits` comes back 0 and the pages come back empty, so a 19,649-posting board completes silently at ~9,500. Ours reads `total` once and marks the board truncated (`phenom.py:264-270`).
- **Locale prefix.** Theirs takes `locale`/`country` as constructor args defaulting to `en_us`/`us` (`:96-97`) and omits the `cc`/`lang` segments from its job URLs entirely (`:284`). Ours derives the prefix per board from the redirect (`phenom.py:145-178`) — 30 of 91 tenants are not `us`, and a wrong prefix 200s onto the landing page rather than 404ing.

**The one marginal idea worth stealing:** theirs fans the remaining offsets out concurrently at 8-way (`:138-150`) where our `_listing` is a serial `while` loop (`phenom.py:241-262`). With `size=500` and our largest shipped board at ~9.4k that is ≤20 sequential calls, so the saving is small and it would complicate the dedupe-by-id + shortfall accounting. Low priority; mentioned for completeness.

---

### 11. successfactors — two small robustness tricks worth copying *if* we take surfaces 1 or 2. Marginal ADOPT. CODE-EVIDENCED.

- **Malformed-XML sanitiser.** `successfactors.py:550-557` — on `ET.ParseError`, strip `<>…</>` invalid empty elements and retry once. SAP feeds really do emit these; today a torn feed raises for us.
- **Day-first date voting.** `_legacy_dates_are_day_first:611` — decide `%d/%m/%Y` vs `%m/%d/%Y` by majority vote across the whole feed before parsing any row. We have no equivalent anywhere in the repo, and our `taleo_be._posted_at` (`:164`) hardcodes `%m/%d/%Y`, which silently mis-parses a European board's `03/09/2026`.

**Not worth adopting:** their `g:salary` → salary. Measured presence is sparse and the field is a **bare number with no currency and no period** — `76000.0` (yearly), `16.88` (hourly) and `2608.84` (monthly) all appear, across ace1950 (57/60) and aramark (14/697), with 0/789 on basf and 0/825 on hsbc. That is the exact ambiguity `icims._salary` (`icims.py:462-501`) refuses to guess at, minus the currency it at least had. Leaving `_salary_field` returning None on this ATS is the right call.

---

## Where ours is clearly ahead (one line each)

- **taleo_enterprise**: we have a whole platform they don't cover at all (556 live boards), and they wrongly document it as covered by their Oracle scraper.
- **ADR-0053 accounting**: every one of our six scrapers reports *why* a list came up short (`mark_truncated` / `mark_truncated_unless_negligible`); theirs has no concept of an incomplete read anywhere.
- **Detail-loss attribution**: `note_detail_loss` / `note_detail_exception` separate "origin refused us" from "parser didn't recognise the page"; theirs swallows both with a bare `except ScraperError: return`.
- **oracle**: no `siteNumber` (the union of every site) vs their `CX_1` default that was wrong on 929 of 1,331 boards.
- **oracle/phenom**: hard offset/result-window ceilings detected and reported, where theirs reads a truncated board as a complete one.
- **phenom**: per-board locale prefix derived from the redirect, where theirs hardcodes `us` and omits the segment from job URLs entirely.
- **icims**: `datePosted` fabrication filtered on the millisecond field with `lastmod` as fallback; theirs takes any `datePosted` and any card-title timestamp at face value.
- **icims**: `baseSalary` read from the node's own (spec-violating) shape, with ceiling-only and period-straddling nodes refused rather than guessed.
- **eightfold**: three surfaces to their two, plus a locale-agnostic PCSX-disabled discriminator where theirs matches an English phrase.
- **eightfold/successfactors/phenom**: the ADR-0048 description skip and the ADR-0017 tech gate cut detail volume by more than half; theirs re-fetches every description every run.
- **taleo_be**: session-backed `jscroll-next` pagination where theirs reads page 1 and stops.
- **successfactors**: `_location_from_slug`, `streetAddress` and CSB-microdata location tiers recover locations on tenants whose pages render none; theirs has only a trailing-parens title heuristic.
- **http layer**: jittered backoff, capped honoured `Retry-After`, per-reason retry counters and a second-egress escalation, against their fixed 3-attempt loop.
- **Identity**: `board_key()`/`slug_from()` host normalisation shared with the prober and ledger repair; theirs reconstructs hosts from slug templates that break on the third prefix variant.

## Live probes run (all 2026-09-22, single requests unless noted)

successfactors: `career4.successfactors.com`/`career5.successfactors.eu` legacy feed ×3 (incl. one invalid-company control); `sitemap.xml`+`sitemal.xml` on `ace1950.jobs2web.com` and `basf.jobs`; `sitemal.xml` head on `apply.careers.hsbc.com`, `aramarkcareers.com`; one RMK job page. icims: `sitemap.xml` status on 8 live + 18 dead rows, then robots + `/jobs/search` on the 3 that 403'd. oracle: listing + 4 details on `aqa.fa.us1`, listing + 4 details on `cbct.fa.em2`. eightfold: PCSX search page 1 on `arcadis.eightfold.ai` (and a 403 on `alnylam.eightfold.ai`). taleo_be: listing + 1 detail on each of `NBF1199`, `CLINIPACE`, `YKHC`. No bulk sweeps.
