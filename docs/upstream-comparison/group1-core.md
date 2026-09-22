# Group 1 (core ATSes) — OURS vs `kalil0321/ats-scrapers` @ 6b44a1b

Scope: workday, greenhouse, lever, ashby, smartrecruiters, workable.
Paths below are relative to the two worktree roots:

- OURS = `.../scratchpad/wt-main/src/headstart/`
- THEIRS = `.../scratchpad/kalil/src/ats_scrapers/`

Live probes run for this report (2026-09-22, single small requests, no bulk sweeps) are
labelled inline with their sample size.

---

## Headline

Two findings are worth acting on. Everything else is either already ours, not applicable to
our index shape, or worse than what we do.

1. **Workable serves a multi-location posting as N rows sharing one `shortcode`; we keep one
   and throw the other locations away.** 34.0% of rows on an 11-board live sample.
2. **SmartRecruiters `function.label` fills `department` on the 54.7% of postings that have
   none** — measured purely additive for the tech gate (+22 tech postings, −0, on 422).

A third is a field *neither* side reads but their `raw` capture surfaced: Greenhouse
`offices[]`.

---

## 1. Workable — multi-location postings collapse to one location · ADOPT · CODE-EVIDENCED + live-measured

**They do:** key jobs by `ats_id` into a dict and, on a repeat key, merge the two rows'
locations instead of adding a second row —
`scrapers/workable.py:93-105` (`jobs_by_id`, the `existing.location = _combine_locations(...)`
branch) with `scrapers/workable.py:231-239` (`_combine_locations`, pipe-joined, order-preserving
dedupe).

**We do:** emit one `Job` per listing row, all with the same id —
`scrapers/workable.py:58-83` (`for j in listed: ... id=self.job_id(j["shortcode"])`). The
duplicates then die in the harvest's within-board first-wins dedupe,
`harvest.py:314-318` (`if job.id not in seen_ids`), so the posting is served with **only its
first row's `city/state/country`** and every other location is silently lost.

**Why it matters here:** the served `location` string *is* the filter substrate — `geo.where()`
and the free-text box are substring matches over it (ADR-0024) — so a posting open in São
Paulo, Montevideo and Buenos Aires is findable only under whichever of the three came first in
the payload. This is the identical defect class the ashby (`scrapers/ashby.py:75-102`) and lever
(`scrapers/lever.py:313-333`) location audits already fixed on their own ATSes; workable never
got that pass.

**Measured 2026-09-22**, `GET apply.workable.com/api/v1/widget/accounts/{slug}?details=true`,
11 live boards from `data/validate/liveness/workable.csv` (10 random with `jobs>=15`, plus
`zyte`):

| | |
|---|---|
| rows returned | 432 |
| distinct `shortcode`s | 285 |
| redundant rows (extra locations) | 147 — **34.0% of rows** |
| distinct postings affected | 64 — **22.5% of postings** |

Worst boards: `crewbloom` 105 rows / 45 postings, `lawofficesofsabrinali` 43 / 13,
`aptus-health-care` 44 / 19. Verified the duplicates are genuinely the same posting: on
`zyte` shortcode `6DCFF04CD6` appears 3x with identical `url` and identical 2,962-char
`description`, differing only in `city/state/country` (São Paulo BR / Montevideo UY /
Buenos Aires AR).

**Caveat on how to adopt:** don't copy their `" | "` join blindly — our other scrapers use
`", "` (workable's own current join) and `"; "` (workday). Pick one of ours. The fix belongs in
`parse` (group by shortcode, join the distinct `city, state, country` triples), not in harvest.

**No truncation consequence:** these are *duplicate* rows, not missing ones, so nothing here
moves an authoritativeness denominator.

---

## 2. SmartRecruiters — `function.label` as the `department` fallback · ADOPT · live-measured

**They do:** fall back to `function.label` when `department.label` is empty, with the comment
"~65% of rows had no dept" — `scrapers/smartrecruiters.py:156-166`.

**We do:** read `department.label` only — `scrapers/smartrecruiters.py:270`
(`department=(p.get("department") or {}).get("label")`), and feed that same accessor to the
pre-detail tech gate at `scrapers/smartrecruiters.py:139-143`.

**Why it matters here:** `department` is load-bearing twice. `tech_filter.is_tech(title,
department)`'s rule 4 promotes a vague title on a technical department
(`tech_filter.py` docstring; `tech_filter.py:246` lists `information technology|\bit\b|r&d|...`),
and `BaseScraper.tech_detail_wanted` uses the same pair to decide whether a detail fetch is
worth making (`scrapers/base.py:449-491`). A null department means neither can fire.

**Measured 2026-09-22**, `GET api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100`,
8 live boards with `jobs>=40`:

| | |
|---|---|
| postings | 772 |
| `department.label` null | 422 — **54.7%** (their ~65% is the right order of magnitude) |
| of those, `function.label` present | 422 — **100%** |

Then running `is_tech(title, None)` vs `is_tech(title, function)` over those 422:

| | |
|---|---|
| verdict unchanged | 400 |
| **gained** (now tech) | **22** |
| **lost** (no longer tech) | **0** |

Gains are real tech roles the title alone can't settle: `Junior Security` / Engineering,
`Clearcase Administrator` / Information Technology, `Pentaho-ETL/BI Consultant` /
Information Technology, `Java Lead` / Information Technology, `UX Designer with Android` /
Information Technology.

**The risk I went looking for, and did not find:** `department` is a *disqualifier* as well as a
promoter — verified directly, `is_tech("Engineer", None)` is True but `is_tech("Engineer",
"Sales")` and `is_tech("Engineer", "Supply Chain")` are both False. So filling a null department
with a non-tech `function` ("Sales", "Supply Chain", "Distribution", "Education" all appear in
the sample) could *cost* recall in a recall-biased pipeline. On this 422-posting sample it cost
zero. **That is 8 boards, not the fleet** — re-run the same diff over a wider sample before
shipping, and report the loss column, not just the gain.

**Adopt it as `department or function`, never as a `team` field** — our `Job` has no `team`, and
the whole value here is that `filter_tech` reads `department`.

---

## 3. Greenhouse — `offices[]` names places our served `location` doesn't · ADOPT-THE-FIELD · live-measured

**They do:** capture `offices[].name` verbatim into `raw` — `scrapers/greenhouse.py:66-67`.
They do *not* use it for `location` either (`scrapers/greenhouse.py:102` reads
`location.name`), so this is not "their implementation is better"; their `raw` dump is just what
made me look.

**We do:** read `location.name` only — `scrapers/greenhouse.py:128`.

**Measured 2026-09-22**, 3 boards, 954 postings sampled (databricks 400 of 884, figma 154,
stripe 400 of 670): **619 postings (64.9%) carry an office name that does not appear in the
served location string**; 51 postings list more than one office. Concrete shapes:

- `figma`: 49 of 154 postings have `offices: ["Canada", "US"]` while `location.name` is
  `"San Francisco, CA • New York, NY • United States"` — **Canada is open and unfindable**.
- `databricks`: `offices: ["Remote - Japan"]` against `location.name: "Tokyo, Japan"` — the
  remote signal is in the office label and nowhere else.
- `stripe`: `offices: ["Ireland Locations"]` against `location.name: "Dublin"` — this one is
  noise, not a place.

So the field is **mixed**: real extra countries on some tenants, board-grouping labels on
others. This is the same additive-location shape lever/ashby already shipped
(`scrapers/lever.py:275-333`, `scrapers/ashby.py:59-102`), including the whole-word guard those
two needed. Worth its own measured pass; do **not** append it unconditionally.

---

## 4. Workable — their per-job Markdown endpoint is a real second surface, and we don't need it · THEIRS-IS-WORSE · live-measured

**They do:** `GET apply.workable.com/{slug}/jobs/view/{shortcode}.md` per job, at concurrency 4,
best-effort — `scrapers/workable.py:126-141`, wired at `scrapers/workable.py:107-112`. Their
listing call omits `details=true` entirely (`scrapers/workable.py:42`), so they have no choice.

**We do:** one request, `?details=true` — `scrapers/workable.py:35`, description read straight
off the listing at `scrapers/workable.py:78`.

**Measured 2026-09-22**, same 11 boards / 432 rows as finding 1: **0 rows came back without a
description** with `details=true`; the same boards fetched without it return the identical rows
with the `description` key absent entirely (zyte: 5,493 chars vs 0).

So the `.md` surface buys nothing and would cost N requests against the one origin in this group
we have *measured* to meter per client IP (our `scrapers/workable.py:20-32` / ADR-0063 opt-in on
429). Worth recording only as a documented fallback if `details=true` ever regresses.

Their `_extract_location` (`scrapers/workable.py:207-228`) reads `locations[0]` and the nested
`location` dict before the flat fields. On the sampled boards `locations[0]` is exactly the flat
`city/state/country` and the nested `location` key is `null`, so it is equivalent to ours, not
richer — the multi-location information lives in the duplicate *rows* (finding 1), which their
richer-looking accessor does not reach either.

---

## 5. Workday — description fallback keys · NEEDS-LIVE-CHECK · low value

**They do:** try `jobDescription`, then `externalJobDescription`, then `description` —
`scrapers/workday.py:624-632`.

**We do:** `info.get("jobDescription")` only — `scrapers/workday.py:938-947`.

I have not measured whether any tenant populates the other two, and I'm not going to assert it
from their comment ("a few tenants expose closely named fallback fields") — that is exactly the
unverified-host-claim this repo treats as a defect. It is a two-key `or` chain if someone wants
to settle it with a sweep of detail responses; our `_report_detail_losses`
(`scrapers/workday.py:1178`) already has the per-cause tally that would show whether the gap is
real ("no description on a 200" would be its shape).

---

## 6. Workday — a per-Board wall clock · NOT-APPLICABLE (but worth knowing)

**They do:** `max_fetch_seconds` with a deadline checked before every page, subdivision child and
request — `scrapers/workday.py:134`, `:186-191`, `:346`, `:387`, `:407`, `:411`, `:441`. A tenant
that blows the budget raises.

**We do:** no per-Board time limit anywhere in `scrapers/workday.py`; a straggling tenant is
bounded run-to-run by the cost ledger and ADR-0064's tech-per-minute gate instead.

Different mechanism for a different failure (theirs protects one process, ours protects the next
run's slice), and ours has the better property — a slow Board still ships what it read.
Recording it because if a Workday tenant ever owns a shard's critical path *within* a run, this
is the shape of the missing lever.

---

## 7. Shared infra — their `raw` overflow + the schema fields we don't carry · NOT-APPLICABLE, one exception

Their `Job` carries `requisition_id`, `apply_url`, `team`, `country_iso`, `region`, `lat`/`lon`,
`language`, `application_deadline`, `commitment` and a free-form `raw` dict
(`models.py:154-518`). Our `Job` is 14 fields (`models.py:12-34`) and every one of them is read
by the index, so most of theirs would be dead weight.

The one with a stated use we actually have a gap for is **`requisition_id`** — their docstring
calls it a "strong cross-ATS dedup signal" (`models.py:423-434`), and cross-ATS dedup is exactly
the thing CLAUDE.md says the Phenom ledger is deliberately narrowed to 16 of 91 tenants for want
of. Not a scraper change and not this task, but it is the field that would unblock it.

Their `commitment`/`employment_type` split (raw label + normalized enum) is a schema preference
our `search.ETYPE_CLAUSES` substring matching makes unnecessary — we keep the provider's own
wording, deliberately (`models.py:26-31`).

---

## 8. Shared infra — fetch layer · ALREADY-HAVE (ours strictly stronger)

- **TLS impersonation.** Theirs escalates to `httpcloak` on a 403/406 when a scraper opts in
  (`fetch.py:311-338`, `scrapers/base.py:61-66`). Ours impersonates Chrome on **every** request,
  sync and async, via `curl_cffi` — `http.py:92` (`_requests.Session(impersonate="chrome")`) and
  `scrapers/base.py:914` (`AsyncSession(impersonate="chrome")`). Superset.
- **Retries / `Retry-After`.** Theirs: 408/429/5xx with exponential backoff and numeric
  `Retry-After` (`fetch.py:339-355`). Ours: `http.TRANSIENT = {403,405,429,500,502,503,504}`
  (`http.py:60`) plus the spare-egress rotation ADR-0063 adds on top (`scrapers/base.py:661-690`).
  Superset.
- **200-with-HTML bodies.** Theirs raises a typed `MalformedJSONError` (`fetch.py:62-69`,
  `:137-143`). Ours raises a bare `ValueError` out of `json.loads` in `fetch_raw`
  (`scrapers/base.py:757`) — caught as "unexpected" by `harvest.py`. Workday already has the
  better treatment (`scrapers/workday.py:62` `UnexpectedListingResponse` + the redacted body
  prefix at `:113-136`). Generalising that is a small, genuine improvement, but it is a
  diagnostics improvement, not a data one.

---

## Where OURS is clearly ahead (one line each)

**Workday**
- Data-centre migration: we sweep 18 `wdN` instances to recover a moved tenant
  (`scrapers/workday.py:205-224`, `_resolve_instance` at `:566`); theirs parses `wdN` out of the
  URL once and a migrated tenant just reads empty (`scrapers/workday.py:160-170`).
- `posted_at`: theirs is **always `None`** — `_parse_workday_date` returns `None`
  unconditionally (`scrapers/workday.py:583-587`) and the detail's `startDate` is stashed in
  `raw` but never promoted (`:314-322`); ours serves it (`scrapers/workday.py:1559`).
- Job identity: ours validates the candidate against a measured req-id shape
  (`scrapers/workday.py:1584-1600`, `_posting_key` at `:1636`); theirs trusts `bulletFields[0]`
  (`scrapers/workday.py:502-503`), which our own docstring records collapsing 228 real postings
  to 15 distinct ids.
- Truncation: we mark the Board unauthoritative on a 2,000-cap with no facet left
  (`scrapers/workday.py:1332-1341`) and on lost pages (`:1402-1419`); theirs silently takes the
  capped 2,000 (`scrapers/workday.py:376-382`) with no shortfall signal at all.
- Stale-session 400s: cookie-jar clear + one refetch (`scrapers/workday.py:995-1003`, ADR-0103);
  theirs treats 400 as fatal.
- Sub-site 404s: JSON-LD recovery off the public job page
  (`scrapers/workday.py:149-187`, `_page_detail` at `:1004`); theirs gives up.
- Nested facet groups (`locationMainGroup` wrapping `primaryLocation`) are expanded
  (`scrapers/workday.py:1808-1820`); theirs reads them as empty and loses that subdivision axis
  (`scrapers/workday.py:650-664`).
- Location: detail `location` + `additionalLocations` repair of `"N Locations"` rollups **plus**
  the country append (`scrapers/workday.py:1700-1766`); theirs repairs the rollup only
  (`scrapers/workday.py:303-308`) and never reads `country`.
- Remote: rollup-aware — we refuse to let `is_remote("3 Locations")` assert on-site
  (`scrapers/workday.py:1544-1547`); theirs has no such guard.

**Greenhouse**
- `meta.total` vs `len(jobs)` tripwire for silently-short responses
  (`scrapers/greenhouse.py:91-120`); theirs reads `jobs` and nothing else.
- Native `metadata` compensation → `Job.salary`, with equity exclusion and range-over-point
  precedence (`scrapers/greenhouse.py:148-204`); theirs parses no Greenhouse salary at all.
- `location.name` whitespace strip (`scrapers/greenhouse.py:128`) — measured 22/178 rows ship
  trailing padding.

**Lever**
- EU instance fallback `api.eu.lever.co` with a real raise when both 404
  (`scrapers/lever.py:371-386`); theirs is `api.lever.co` only, so every EU board reads dead.
- `allLocations` + top-level `country` with a whole-word no-duplicate guard
  (`scrapers/lever.py:275-333`); theirs reads `categories.location` — which our audit found is
  just `allLocations[0]` — and files `country` in `raw`.
- Description also appends `additionalPlain` (`scrapers/lever.py:336-349`); theirs stops at
  `description` + `lists`.

**Ashby**
- Unlisted postings are skipped (`scrapers/ashby.py:143-144`); theirs serves them.
- `remote` from `workplaceType`, not `isRemote` — theirs falls back to `isRemote` first
  (`scrapers/ashby.py:73`), and our measurement (16,138 jobs) found `isRemote` reports **Hybrid
  as remote** on 4,183 of them (`scrapers/ashby.py:12-34`).
- `secondaryLocations` + `address.postalAddress` composed into the served location
  (`scrapers/ashby.py:37-102`); theirs files both in `raw` (`scrapers/ashby.py:102-108`).
- Salary from the structured Salary component with `is not None` bounds and the `"1 TIME"`
  exclusion (`scrapers/ashby.py:166-211`); theirs additionally surfaces
  `compensationTierSummary` as `salary_summary`, which mixes bonus/equity language back in.

**SmartRecruiters**
- `totalFound` as the terminator and the truncation signal
  (`scrapers/smartrecruiters.py:118-137`); theirs stops on a short page
  (`scrapers/smartrecruiters.py:102-104`), which cannot detect a short read at all.
- Native `compensation` block off the detail response → `Job.salary`, with the max-only decline
  (`scrapers/smartrecruiters.py:282-321`); theirs fetches the same response and reads only the
  description and `applyUrl` from it.
- `location.fullLocation` preferred over re-joining the parts
  (`scrapers/smartrecruiters.py:233-256`); `experienceLevel.label` → `Job.experience`
  (`:275`), which theirs files in `raw`.
- `companyDescription` deliberately excluded from the assembled body
  (`scrapers/smartrecruiters.py:182-186`); theirs concatenates it (`:220-226`), diluting the
  embedding with per-tenant boilerplate.
- A compensation-shaped `customField` is appended to the description so the salary cascade can
  mine it (`scrapers/smartrecruiters.py:60-72`); theirs stores `customField` in `raw`.

**Workable**
- One request for listing + descriptions (finding 4) against their N+1.
- `note_unreadable_board` on a payload with no `jobs` key (`scrapers/workable.py:44-57`) — the
  distinction between "read nothing" and "has nothing", which decides whether a Board's rows get
  evicted two runs later.

**All of them**
- `mark_truncated` / `mark_truncated_unless_negligible` (`scrapers/base.py:312-385`): every
  scraper here can say its list came back short, and ADR-0053/0121 give that a defined meaning.
  Nothing on their side has any concept of an incomplete read — the closest is workday silently
  accepting a capped 2,000.
- The ADR-0048 `have_details` skip and the ADR-0017 pre-detail tech gate
  (`scrapers/base.py:436-491`): a steady-state run makes no detail request it already holds the
  answer to. Theirs re-fetches every description every time (`scrapers/base.py:134-147`).

---

## Nothing-to-do list (checked, no action)

| ATS | Their thing | Verdict |
|---|---|---|
| greenhouse | `?content=true` single-request descriptions | ALREADY-HAVE (`scrapers/greenhouse.py:83`) |
| greenhouse | `requisition_id` placeholder filter ("See Opening ID"/"TBD") | NOT-APPLICABLE — no such field in our `Job` |
| greenhouse | keeps HTML in the description for a later markdownify step | NOT-APPLICABLE — we store plain text for the embedding (`models.py:56-65`) |
| lever | `commitment` → normalized `EmploymentType` enum | NOT-APPLICABLE — we keep provider wording, `search.ETYPE_CLAUSES` matches by substring |
| lever | `workplaceType` onsite → `is_remote=False` | ALREADY-HAVE in effect (`scrapers/lever.py:393-394`) |
| ashby | `url = jobUrl or applyUrl` | NEEDS-LIVE-CHECK, low value — never observed `jobUrl` absent; ours would emit `""` if it happened |
| smartrecruiters | `location.remote is False` → explicit non-remote | equivalent; ours reaches the same answer via `is_remote(location)` |
| workday | `_SUBDIVISION_FACETS`, `QUERY_TOTAL_CAP=2000`, `PAGE_LIMIT=20`, depth 4 | ALREADY-HAVE, identical constants |
| workday | freeform `remoteType` mapping with hybrid→None | ALREADY-HAVE (`scrapers/workday.py:1768-1781`) |
| all | httpcloak escalation, retry/backoff, `Retry-After` | ALREADY-HAVE, ours is a superset (§8) |
