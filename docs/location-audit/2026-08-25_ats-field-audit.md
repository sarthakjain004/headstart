# ATS field audit — every live scraper, location and everything else

**Date:** 2026-08-25 · **Scope:** all 18 ATSes with live Boards · **Method:** 500–1000 live Boards
per ATS through each scraper's own `fetch_raw()`/`parse()` path, plus a full key census of the raw
API response to find fields nothing reads.

This is the follow-up to `2026-08-24_location-field-audit.md`, and it exists because that pass was
wrong in both directions: it called recruitee **clean** (recruitee had a localized remote marker
swallowing the city on 476 of 1,922 offers) and it asserted successfactors' residual nulls had
"no location signal anywhere" (all 70 in a re-measure were recoverable). Treat *these* verdicts the
same way — as measurements with sample sizes attached, not as settled fact.

Full per-ATS reports with raw captures live in `experiment/location-audit-2026-08-25/`, which is
**gitignored**. That is why this summary is here: an earlier fix in this same series was measured,
reviewed, committed and then lost because it only ever existed outside the tracked tree.

## The two findings that are not about location at all

Both are **corpus truncation** — jobs that are never scraped, so no downstream fix can reach them.
They outrank every field defect below.

| ATS | What | Measured |
|---|---|---|
| **teamtailor** | `jobs.json` caps at 100 items and the scraper never paginates | 27 of 766 Boards sit at exactly 100; paginating them found **4,046 Jobs — 26.4% of the sample's true corpus — never scraped**. `lovisacareers` serves 779 real Jobs, we read 100. |
| **trakstar** | the HTML card listing caps at 25 per Board | **48.7% of all Jobs hidden.** An RSS path (`fetch_via_feed()`, PR #256) already exists and is untied. |

## Verdicts

| ATS | Location | The rest |
|---|---|---|
| **ashby** | **FIXED** (PR #296) — 79.43% omitted a component of their own `address.postalAddress`, 69.55% no country; `secondaryLocations[]` (17.5%) never opened, 1,295 naming a different country | **FIXED** — `remote` was `True` for **all 4,183 Hybrid Jobs** (25.9%); `isRemote` is exactly `workplaceType != "OnSite"`. `team` unread (100%, differs from `department` on 35.69%) |
| **workday** | **FIXABLE** — `jobPostingInfo.country.descriptor` (99.06%) never copied by `_extract_detail`; **81.45% of rows name no country**, 26.37% end in a bare US-state code. 72 of 139 India Jobs never say "india". Zero extra requests, largest ATS in the ledger | Rollup repair re-confirmed at 99.64%. **No salary and no experience field exist anywhere** — a ceiling, now measured not assumed. `endDate` (19.07%), `hiringOrganization.name` (93.52%) unread |
| **smartrecruiters** | **CEILING** — `fullLocation` 100% populated, strict superset, never a sentinel; the `or` fallback is dead code. Only 10.54% carry `", ,"` (cosmetic) | **`compensation: {min,max,currency,period}` on the detail already fetched, entirely unread** (10.48%). Description-mining misses **81.7%** of them; reading it doubles coverage 7.40%→16.33% at zero request cost. `language.code` 100% (19.36% non-English) answers `doc_prep.is_english()` natively. `department` null on 68.95% while `function.label` (99.98%) sits unread |
| **teamtailor** | **FIXABLE** — `jobLocation[0]` only; 9.83% drop 3,602 Places, 180 span a second country. The 4.83% null rate is a genuine ceiling (verified twice) | **The 100-item cap above.** `/jobs.rss` has exact item parity and carries `remoteStatus` (100% — `hybrid` 25.1%) against a `remote` flag true on **0.23%** today, plus `tt:department` (87%, currently hardcoded `None`). `_jobposting.employmentType` absent on all 11,297 — dead code |
| **personio** | **FIXABLE** — `<additionalOffices>` read by nothing: 18.31%, 3,924 real places dropped; on 126 the served location is a *localized placeless marker* (`Home Office`→Bremen, `Mobil`→Hürth) — the recruitee shape one field over | **FIXABLE, larger** — `experience = seniority or yearsOfExperience` prefers a four-value word over a native years range present on **70.03%**. Through the real cascade: 67.24% change answer and **37.46% currently carry a `min_years` too high** (`experienced`→5 where the ATS says `1-2`), excluding them from every junior/mid search. Needs a `DERIVATIONS_VERSION` bump. `occupationCategory` **99.87%** populated with a literal `it_software` value — a free recall booster for the ADR-0017 tech gate |
| **lever** | **FIXABLE** — `categories.allLocations` has >1 entry on 7.96%, dropping 7,927 places (34 hide an India location); top-level `country` (ISO-2, 88.60%) read nowhere, absent from the location string on 71.8% of those | `location == allLocations[0]` on all 36,565 records — **no sentinel-precedence bug**. `salaryDescription` and `categories.level` measured and **not** worth building |
| **zoho** | **FIXABLE** — read `State`/`Country` *in addition to* `City`, not as a fallback for it | (see report) |
| **ripplehire** | **FIXABLE** — reads `jobLocation` (the **country** field) instead of `locations` (the city field) | |
| **freshteam** | **FIXABLE** — `preferred_remote_job_locations` read only for the remote flag, its geography discarded: **653 of 8,007 Jobs (8.16%) across 190 tenants serve a country the job is not hiring in** (`airboxr` served `Singapore, Singapore`, actually hiring India/Vietnam/Ukraine/Poland). 1.91% null is a separate genuine ceiling | **`job_type` 100% populated and unmapped, so `employment_type` is `None` on every freshteam Job.** Enum resolved against 48 real job pages, 6 tenants per code, unanimous: 2=Full Time, 1=Contract, 3=Internship, 8=Fixed Term, 4=Part Time, 7=Volunteer, 6=Seasonal, 5=Temporary. A static dict, zero extra requests |
| **trakstar** | **CEILING** for the field — 0.00% null, 0.14% dirt over 5,586 Jobs, and the card's spans beat the `title=` attribute on 1 of 5,586 | **The 25-card cap above.** `baseSalary` absent on 249/249 postings — reconfirms the 0.00% Tier-1 salary ceiling independently |
| **rippling** | **CEILING** — `workLocation.label` non-null on **26,077/26,077** and *richer* than `_detail.workLocations`: the listing fans out one entry per location, the detail abbreviates (`Remote (Wisconsin, US)`→`Remote (WI, US)`). The `wls[0]` fallback is dead code | **`employmentType`'s subfields are inverted from their names** — the scraper reads `.id` (**347 distinct tenant spellings, 130 singletons**, six spellings of "salaried full-time" in the top 20) while `.label` is a clean 6-value enum on 94.2%. `payRangeDetails[0]` only: **47 of 2,057 salaried Jobs (2.29%) understate the max** — `docs/salary-extraction/rippling.md` called this flat from a 105-Job sample; at 82× that it is not |
| **workable** | **CEILING** — the scraper reads everything the widget's geography carries | |
| **recruitee** | **FIXED** 2026-08-25 — localized remote marker swallowed the structured city/country | |
| **successfactors** | **FIXED** 2026-08-25 — slug-encoder glue, plus a `streetAddress` tier | |
| **greenhouse, keka, darwinbox** | **FIXED** 2026-08-24 — untrimmed values, embedded `\r`, trailing spaces | |
| **eightfold** | `_first_location` truncation known and left alone; ashby's was strictly worse and is now fixed | |

## The claim that was wrong, recorded so it is not repeated

An investigation feeding this work asserted that `location_filter_audit.py`'s `_PLACELESS` tuple
contains `us`/`usa`, inflating the reported problem by 15–20%. **It does not** — verified directly:
`is_placeless('us')` and `is_placeless('usa')` both return `False`, and neither string is in the
tuple. The real distortion runs the opposite way: the tuple listed only the English `remote job`,
so 269 French and 80 German markers in one sample were scored as *valid places*.

## Cross-cutting patterns worth naming

**Truthy-sentinel precedence** — `location = a or b` where `a` can be a placeless marker that is
truthy and discards a real `b`. Found in recruitee (fixed), suspected in zoho, present in personio
one field over. The recruitee fix detects it *structurally* — a `location` that does not name the
record's own `city` is not a place — because enumerating marker strings is always one locale behind.

**Discarded richness** — the mapping takes one field when the record carries more. This is the most
common defect in this audit by far: ashby, workday, lever, teamtailor, personio, zoho, freshteam.
It matters because `geo.where()` is a raw substring `LIKE`, so anything absent from the string is
unfilterable however well the record knows it.

**No native experience field exists on rippling, freshteam or trakstar** — exhaustively enumerated
(rippling all 18 detail keys; freshteam `position_level` null on 8,008/8,008; trakstar 10 feed
elements + 9 JSON-LD keys). That derivation stays a regex ceiling on those three, now measured
rather than assumed.

**A native field beating a derived one** — smartrecruiters' `compensation`, personio's
`yearsOfExperience`, teamtailor's `remoteStatus`, smartrecruiters' `language.code`. The project
derives all four from text; where an ATS returns them structurally, the regex is strictly worse and
applies to fewer rows.
