# PageUp: complete public RSS with descriptions, and legacy-link migration checks

**Measured:** 2026-10-03. **Recommended surface:** public RSS, `GET https://careers.pageuppeople.com/{account}/{channel}/{locale}/rss`. **ATS key:** `pageup`. The initial measurement below is followed by implementation and validation evidence at the end.

The upstream implementation uses paginated HTML and one detail request per job. Live measurement found a cheaper, more complete public RSS feed with full descriptions and category metadata. Across **17 initially selected eligible Boards**, RSS returned **4,354 jobs**, all with namespaced full descriptions, for **56,414,149 uncompressed response bytes**. The shared tech filter retained **261** from title/category: **216,146 bytes per tech Job**, about **0.216 MB**, below ADR-0158's roughly 2 MB comparison point. This is a measured sample cost, not a forecast for every PageUp Board.

The [provider robots](https://careers.pageuppeople.com/robots.txt) allows these public paths and excludes admin/internal/test channel routes. The document was read before PageUp listings. No authenticated or disallowed path was requested. The initial research used public HTTPS requests with HTML Accept. The implementation follow-up also verified the generated Kinetic job route in a rendered browser.

## Sources and reproducibility

- Hypotheses: [upstream scraper](https://github.com/kalil0321/ats-scrapers/blob/main/src/ats_scrapers/scrapers/pageup.py), [21 upstream seed Boards](https://github.com/kalil0321/ats-scrapers/blob/main/ats-companies/pageup.csv), the local provider catalogue (gitignored research).
- Primary measured endpoints: [Kinetic listing](https://careers.pageuppeople.com/1083/cw/en/listing/), [Kinetic RSS](https://careers.pageuppeople.com/1083/cw/en/rss), [CSU RSS](https://careers.pageuppeople.com/873/cw/en-us/rss), [Virginia RSS](https://careers.pageuppeople.com/1125/cw/en-us/rss), [empty CPB RSS](https://careers.pageuppeople.com/434/cbw/en/rss).
- Local ignored notebook: `experiment/pageup-public-api/LOG.md`; `measure.py`, `run_listings.py`, `run_followup.py`, `run_rss.py`, `run_final_checks.py`, `analyse.py`. Every response body, final URL, redirect chain, status, SHA-256 and elapsed time is saved in `artifacts/`; `request-log.jsonl` appends after each completed request.
- Small, lossless fixture: `artifacts/fixture-pageup-kinetic-3-items.xml` (18,076 bytes), cut from three real RSS items. Empty fixture: `rss-cpb.txt` (338 bytes), repeated byte-identically. Broken legacy job routes: `rss-moved-*-first-detail.meta.json` plus captured HTML. Synthetic 404s are labeled separately and are not misrepresented as real departed tenants.

Reproduction for an allowed small feed:

```bash
PYTHONPATH=src /Users/sarthakjain/Projects/HeadStart/.venv/bin/python \
  experiment/pageup-public-api/measure.py kinetic-rss-recheck \
  'https://careers.pageuppeople.com/1083/cw/en/rss'
```

The upstream 1,000-item HTML page size and eight detail workers were hypotheses. We tested the page size, but RSS removes the detail pass entirely. Current research used at most four concurrent small rate probes, then at most two ongoing provider requests.

## 1–2. Board identity, discovery spelling, aliases and site overlap

A public Board address contains **numeric account, channel, locale**. Initial seeds use `cw`; other public channels exist (`434/cbw/en`, `873/sf/en-us`). Preserve these components until equivalence is measured. Strip the optional leading `/mob/` only when resolving to the corresponding observed desktop Board. Strip listing/job/RSS suffixes and query filters after extracting identity; a job URL also carries a separate numeric job ID.

All **4,354 initial-sample job IDs were numeric and colon-free**. The displayed external requisition may differ: Kinetic `/job/495865` displays `Job no: WWREQ0034912`. Use the URL/RSS `job:refNo` for native identity; do not replace it with the external requisition label.

Measured comparisons:

| Comparison | Result | Identity implication |
|---|---|---|
| `1083/cw/en` vs uppercase `1083/CW/EN` HTML | Same 29 listing IDs and byte-identical body | Case-insensitive on this measured Board; preserve lowercase convention |
| Kinetic `en-us` vs `en` | HTML `en-us` 302s to `en`; RSS both contain 31 identical IDs and byte-identical bodies | Canonical locale is `en` for this Board; feed channel link agrees |
| `873/cw/en-us` vs `873/sf/en-us` | `sf` RSS 63 jobs; all 63 are in `cw` RSS 2,487 | Channel is meaningful as a view; this specific site is a subset, not an extra 63 unique jobs |
| Kinetic `/mob/1083/cw/en/listing/` | HTTP 200, different mobile markup; desktop parser finds no jobs in it | Normalize source spelling to supported desktop view, rather than call the mobile body empty |
| WWU `793/cw/en` vs `793/cw/en-us` | Public employer embed uses `en`; HTML redirects to byte-identical `en-us` listing; RSS channel link names `en-us` | Preserve canonical employer-linked locale; see audience correction below |

Do not drop locale or channel globally from Board identity merely because these examples overlap. The 25-row research candidate pool contains no invented controls, no mobile aliases and no added locale aliases; it contains the measured CSU subset as a candidate requiring reconciliation, not an instruction to serve it twice.

## 3–7. Public surfaces, pagination, full descriptions and additional feed postings

### RSS schema

XML namespace `job = http://pageuppeople.com/`; Atom namespace `http://www.w3.org/2005/Atom`.

| RSS field | Meaning measured / proposed Job use |
|---|---|
| `item/title` | Job title |
| `item/link` and permalink `guid` | Public `/account/channel/locale/job/{numeric_id}`; title slug is optional |
| `job:refNo` | Numeric native ID, matching link ID |
| Plain `description` | Short listing teaser; **not full description** |
| `job:description` | Complete HTML description; use shared HTML-to-text processing |
| `pubDate` | Posting time in RFC-style text; parse as UTC where `Z` appears |
| `a10:updated` | Present; do not infer a distinct update meaning without further evidence |
| `job:category` | Category metadata; repeated twice in observed items, often parent/child encoded |
| `job:location` | All locations in one string, sometimes `parent|child` plus comma-separated groups |
| `job:workType` | Employment label(s), sometimes multiple labels |
| `job:closingDate` | Optional closing timestamp |
| `job:applyLink` | Public application destination, generally `secure.dc*.pageuppeople.com/apply/...` |
| `job:businessLayer1` | Organizational grouping; varies between campus, division, agency, department and employer; **not a universal company-name field** |

All 17 RSS requests completed as one XML document; no next-page token was present. The largest measured feed contained 2,487 items, so there is no 1,000-item cap on that feed. A parseable XML document alone cannot mathematically prove no unpublished/omitted jobs; the independent HTML comparisons supply the measured completeness check.

### Independent HTML walk

HTML listings use `.../listing/?page=1&page-items=1000`. Scope job links to `#search-results-content`; the full document duplicates desktop/mobile tables. Some rows also contain an “Apply Now” link with the same job ID. Dedupe IDs and choose the descriptive title.

CSU page one returned 1,000 unique jobs plus “More Jobs 1477”; page two returned 1,000 plus “More Jobs 477”; page three returned 477 and no more link. The union is **2,477 with no duplicates**, matching the first page's implied total. Kinetic at page size 10 returned the first ten, then ten with nine remaining; page four was empty. Its full size-1,000 listing contained 29. No clamp above 1,000 or extremely large hard window was tested; RSS makes those unneeded for the selected normal path.

### RSS versus visible listing

| Board | Complete HTML IDs | RSS IDs | Missing HTML IDs from RSS | Additional RSS IDs |
|---|---:|---:|---:|---:|
| Kinetic IT | 29 | 31 | 0 | 2 |
| CSU | 2,477 | 2,487 | 0 | 10 |
| Commonwealth of Virginia | 692 | 692 | 0 | 0 |
| CPB zero-job control | 0 | 0 | 0 | 0 |

Across all 17 compared Boards, RSS missed **zero** of the complete HTML-listing IDs and added **19** public-feed records. Other measured differences include DHA 1 HTML / 4 RSS, Lehigh 33 / 34 and Dayton 81 / 84. These extra records must not be silently erased by an HTML-ID allowlist. Kinetic's two extras were explicitly verified: [Service Desk Technician 495904](https://careers.pageuppeople.com/1083/cw/en/job/495904) is an ordinary public posting; [495406](https://careers.pageuppeople.com/1083/cw/en/job/495406) is an explicitly titled expression-of-interest posting. Both return full anonymous detail pages with application links and no noindex metadata. CSU's extra sample contains several named applicant pools. The implementation uses RSS as the public posting source and retained source-published pools under existing feed semantics.

Full-description comparison used 36 real detail pages across varied templates. **29/36** normalized RSS descriptions exactly matched `#job-details` text. **Four** custom templates (DHA and three Monash examples) lacked that standard container, but the entire RSS description appeared within their wider public job content. **Three** NSW detail containers added only the 17-character attachment-link label ` Role Description`. No RSS description truncation was found in these 36 comparisons.

The root sitemap request returned 406 with HTML-only Accept; alternative Accept negotiation was not pursued once the complete RSS surface was established. Do not describe that as a proven absent sitemap. RSS itself returned 200 under the initial HTML Accept and under explicit XML/`*/*` Accept.

## 8–9. Real empty, dead-route and migration behavior

A recognized empty RSS envelope is not a dead Board. CPB `434/cbw/en/rss` returned a 338-byte channel with no items twice, with identical hashes; its branded listing also twice showed no jobs. DSTA and Adelaide branded public listings showed no jobs, but their feeds were not yet separately validated in the initial cohort. Thus CPB is a real empty **public surface**, not proof the employer will hire again.

Two synthetic invalid identities (`99999999/cw/en`, `1083/not-a-real-channel/en`) returned 404 and the same 891-byte “Page not found” HTML on both RSS/listing routes. They test unknown addresses, not real historical tenant departures. No real formerly valid account returning that exact 404 was established in this bounded pass.

The more dangerous real condition is **nonempty RSS whose job links no longer work**:

| Historical Board | RSS jobs | Actual first feed job click | Recommendation for this URL adapter |
|---|---:|---|---|
| Arts Centre Melbourne `902/cw/en` | 9 | 302 to `careers.artscentremelbourne.com.au/`, then generic `/jobs/search` | Hold legacy route |
| Bed Bath N' Table `940/cw/en` | 329 | 302 to `careers.bedbathntable.com.au/`, then generic `/jobs/search` | Hold legacy route |
| North Idaho College `1027/cw/en` | 20 | 302 to `north-idaho-college-careers.career-pages.com/`, then generic `/jobs/search` | Hold legacy route |

The RSS endpoint itself does not redirect. A feed-only liveness probe would falsely approve all three old routing surfaces. Check the first real public detail or the listing redirect without losing the intermediate `Location`; do not accept the destination's 200 search homepage as the posting. Some destination requests also returned 202 challenge HTML. This is not a claim the employers are dead or that their new career fronts have no jobs.

### Audience correction: WWU

The initial `793/cw/en-us` template title says “Internal Job Openings” and includes `noindex`. That initially suggested a hold. However, the university's own [public careers page](https://hr.wwu.edu/careers), which addresses first-time applicants, embeds `PU.Widgets.jobListing(..., 'https://careers.pageuppeople.com/793/', 'cw', 'en', ...)`. Its exact `en` listing redirects to the same `en-us` body with 85 jobs. Its RSS carries 85 jobs and canonical `en-us` links. This is direct employer evidence that the channel is public; the template title alone was misleading. The initial 17-Board figures exclude this follow-up, so they remain reproducible. Including WWU gives **18 Boards, 4,439 RSS jobs, 270 title/category tech matches, 57,623,537 bytes, or about 0.213 MB per tech Job**. This employer-endorsed public Board is included in the implementation.

## 10–12. Detail, tokens and tech filtering

RSS already carries full description, categories, work type, locations and dates. No per-job request is needed for the chosen extraction path. Consequently there is **no pre-detail tech gate** and no detail-worker fan-out to configure. Existing post-hoc title/category filtering can use the feed's richer categories; the HTML-only listing frequently omits them. In the initial cohort, title-only filtering kept 219 versus 261 with categories, so a title-only gate would drop 42 records that the current shared title/category gate accepts.

No auth headers, CSRF token, login or preexisting session was needed for the measured feeds or public details. A no-title-slug link [495865](https://careers.pageuppeople.com/1083/cw/en/job/495865) returned the same ServiceNow posting with HTTP 200. Thirty-six selected details plus the two RSS extras were read successfully as public HTML; the three migrated-route checks above are the counterexamples. Token lifetime/expiry is not applicable to this observed surface, but unsupported deployments should remain unknown rather than have a token invented.

## 13–18. Field coverage and traps

Coverage below is over the **4,354-job, 17-Board initial RSS cohort**, not all PageUp installations.

| Field | Nonempty | Interpretation / implementation constraint |
|---|---:|---|
| Title, numeric ID, job link | 4,354 / 4,354 | Every native ID is numeric and colon-free |
| Namespaced full description | 4,354 / 4,354 | Plain RSS description is only summary |
| Category | 4,331 / 4,354 | Remove repeated identical category elements; preserve useful category labels |
| Location | 4,349 / 4,354 | Preserve all values; 88 rows have multiple `parent|child` groups, maximum 168 pipe separators |
| Employment label | 4,354 / 4,354 | 92 distinct raw labels; current shared `flags()` produced at least one flag on all 92 |
| Posted date | 4,354 / 4,354 | All parsed successfully; no future dates in this capture |
| Closing date | 1,132 / 4,354 | All parsed; none was already past at measurement time |
| Organizational business layer | 4,351 / 4,354 | Cannot universally be used as employer or department |
| Native remote boolean | 0 / 4,354 | Infer conservatively from explicit location/work-arrangement text |
| Native salary field | 0 / 4,354 | Salary text appears in full descriptions; shared description extraction remains responsible |
| Native experience field | 0 / 4,354 | Experience statements appear in full descriptions; no invented minimum years |

Kinetic's complete RSS repeated byte-identically later in the session, including all posted and updated timestamps. All 4,354 `pubDate` values and all nonempty closing dates parsed successfully with `email.utils.parsedate_to_datetime`. This proves stability for one repeated feed, not every date semantic across the platform.

Location example: `VIC|Melbourne,WA|Perth`; another includes multiple named states/cities. Do not keep only the first location. Some strings use plain commas inside a city/state value (`Marion, IN`), so blindly treating every comma as a location boundary is also wrong. Preserve the complete source string and normalize parent/child separators conservatively.

Remote caveat: **12 rows** explicitly contain `Partial Remote` and five category strings explicitly contain `Work Arrangement|Hybrid`. They are not fully remote. The current shared `is_remote('Partial Remote, Bethlehem')` returns true, so the adapter needs to guard that phrase before using the shared fallback. Other measured locations include `Remote (within United States)`, `Bethlehem,Fully Remote`, plain `Remote`, and `Hybrid (2 Office / 3 Remote)`. Mixed location options must not be flattened into an unconditional full-remote promise.

Employment examples include `Full Time`, `Full Time,Part Time`, `Instructional Faculty - Temporary/Lecturer`, `Staff`, `Management (MPP)`, `Fixed-term (Full-time)`, `Wage (Hourly)`, and `Faculty (Part Time or Adjunct)`. Preserve the provider's phrase and let the shared employment classifier operate; the fact it returns a flag is not a new claim that every ambiguous employer label has a universal meaning. Detailed counts and classifier outputs are in `artifacts/employment-flags.json`.

Company naming requires public Board evidence or a reviewed cache for opaque numeric accounts. Most templates have useful title text, but several do not:

- DHA `1041/cw/en`: generic title, but public footer says **Defence Housing Australia**.
- JWU `859/cw/en-us`: generic title, but employer name appears in public posting text.
- Mercedes `920/cw/en`: generic title, but logo alt text says **Mercedes AMG High Performance Powertrains**.
- NSW `795/cw/en`: generic title and several organizational groups. Verify an appropriate umbrella employer/operator name rather than treating any business-layer value as the whole Board's name.
- Virginia `1125/cw/en-us`: template title names VITA/DHRM, while postings span state agencies. **Commonwealth of Virginia (DHRM)** is the upstream Board label; preserve the distinction between Board operator and job-specific agency.
- UNT `1151/cw/en-us`: business layers name different universities; other Boards' business layers name departments, cities or divisions. There is no universal business-layer-to-company mapping.

`artifacts/company-name-evidence.json` records all 21 seed labels, first-party Board URLs, current page titles, exact-name text hits and logo alt evidence. The numeric slug is not an acceptable user-facing substitute when a reviewed name is available. The implementation subsequently added 18 names with the cited public evidence to `config/company_names.csv`.

## 19–22. Operational envelope, population and cost

- Initial Kinetic HTML serial baseline: **3/3 200**, 1.172 / 0.402 / 0.417 s, identical bodies.
- Same-Board small HTML requests at concurrency four: **4/4 200**, 1.282 s batch, about 3.12 requests/s.
- Four different Boards: **4/4 final 200**, 4.564 s batch; two redirected to customer frontends. Later continued probing used only two concurrent requests. No 403/429 refusal occurred; later direct ramps reached128 and showed throughput flattening between64 and128; the spare route timed out at8. Do not reuse upstream eight detail workers as a measured constant.
- RSS User-Agent comparison on Knox: headstart, curl-like and python-requests-like UA strings all returned identical 48,619-byte RSS with 200. These requests used the same Python HTTP client with different header strings; they do not claim TLS-client equivalence to real curl.
- Largest RSS: CSU, **37,182,445 bytes / 2,487 jobs**, 25.107 s. It is one request but should not be assigned an unrealistically short timeout. Virginia: **7,681,965 bytes / 692 jobs**, 7.209 s. Small Knox: **48,619 bytes / 5 jobs**.
- Initial sample total: **56,414,149 bytes / 4,354 records**, 261 tech matches (5.99%), **0.216 MB per tech Job**. Adding independently validated WWU changes this to about **0.213 MB**. These numbers include entire feeds rather than only tech descriptions, so they account for the selected surface's actual transfer volume.
- This cost is below the cited comparison bar by roughly ninefold. Recommend enabling an RSS scraper if the parent completes naming, redirect-route safeguards, identity reconciliation and required repository checks. Do not extrapolate seed coverage or English share to all PageUp customers.

## Candidate pool and remaining checks

The local ignored `data/ats-tenants-merged/pageup.csv` has **25 candidates**: 21 upstream plus three real branded empty-listing leads (CPB, Adelaide, DSTA) and one CSU subset channel found in page links. All `url` values are public listing URLs; RSS endpoints are probe/scrape surfaces only. The two synthetic invalid controls are excluded. This was the initial pool; the final archive expansion is recorded below.

The initial browser, concurrency and archive gaps were addressed in the follow-up. The adapter remains scoped to the measured classic public channels and verifies migrated Job routes rather than assuming every new frontend is compatible. Parsed, nonempty RSS alone is insufficient when user-facing links redirect to a generic career homepage.

## Implementation and archive follow-up (2026-10-03)

The enabled RSS adapter, liveness probe, archive/fingerprint hooks and subset reconciler are implemented (ADR-0383). CC-MAIN-2026-39 returned 122 candidates from its complete one-page index;108 are exclusive to that source. The bounded Wayback run read 1/84 pages, yielding 29 candidates (23 exclusive), then stopped on a 20 s timeout. This is not an exhausted Wayback census. The union contains 160 candidates:112 live,24 dead,24 unknown. The public-feed audit compared all 112 live Boards and wrote 22 same-account subset/equality aliases.

`verify_scraper.py pageup 20` read all 17 live sample Boards:3,646 Jobs with 3,646 full descriptions. Three candidate errors were known migrated Boards. The largest original feed, CSU, returned 2,487 unique Jobs without truncation;2,482 had locations and all had titles,departments,dates,employment labels,publicURLs and descriptions. The implementation validates channel links and matching namespaced native ids, so unrelated RSS cannot impersonate an empty Board.

Mobile `/mob/` routes normalize through every discovery entry point. `Partial Remote` locations and the measured `Work Arrangement|Hybrid` category both become `remote=None`. Browser verification of `/1083/cw/en/job/495865` displayed Senior ServiceNow Developer, Kinetic IT, Full Time, Sydney and full description. Names are resolved from conservative measured title wrappers plus 56 cited overrides (18 initial,38 after archive expansion); the 103 hiring-page audit captured titles/logo alt/body evidence.

Raw expansion captures, failed-page evidence, complete snapshots and verification logs remain local under `experiment/three-ats-build/` and `experiment/ats-gap-pageup/`. Other languages/channels and employer migrations remain a bounded coverage claim.

## Final verification after archive and overlap recovery

Archive coverage is complete for all 33 requested Common Crawl collections and 84/84 Wayback pages. The pool has 885 candidates. The refreshed feeds exposed 93 same-account aliases; content review also excluded 13 demonstration/template Boards. Confirmed G8 Education and The Star HTTP 200 meta-refresh migrations are now dead, while uncertain destinations remain unresolved. One closed sample Job cannot declare a whole externally fronted Board dead.

Direct RSS ramps reached 128 without HTTP refusals. Across Boards, throughput moved from 21.9 requests/s at 64 to 18.3 at 128, while p95 rose 2.9→7.4 seconds. Use 8 shared starts/s. Spare bursts timed out at 8, so spare is limited to one request after exhausted transport failures. Four controlled-primary-failure tests returned and parsed live spare responses across PageUp/Recruiterflow and sync/async.

The full employer/title comparison and source-quality scan are retained in the ignored experiment folders. Demo exclusions are backed by actual test/lorem-ipsum/automation posting content; real employers with leftover template chrome were retained.
