# Avature Boards that read zero Jobs where postings exist (2026-09-29)

The audit of 2026-09-29 counted 30 Avature Boards whose ledger row lists postings and whose last
complete scrape returned none (12,913 ledger postings), and three of them (`deloitteus` 522 served
rows, `mantech` 420, `emiratesjobs` 215) sit Unconfirmed for 14 days behind that zero. This note
classifies the 30 by cause from a live read of every one of them, and records what the
`SearchJobs` fallback of ADR-0358 recovers. Every request below was a listing surface (`robots.txt`,
a sitemap, a search page) or a single job page, one at a time through one process at no more than
one a second, from a plain client. A host that answered a challenge was stopped and recorded.

## What "read zero" means

`board_cost.csv`'s `jobs` is the count of Jobs the scraper returned, which for Avature is the
postings that passed ADR-0245's tech gate on the URL slug and whose job page was fetched. It is
not the count of postings. A Board that lists 663 postings and no tech title reads `0`, and the
ledger's `jobs` (what the liveness probe counted in the sitemaps) says 663. So the 30 split into
Boards that are empty for us and Boards that are not.

## The 30 Boards, by cause

Today's read of each Board's sitemaps, with `ids` the distinct `JobDetail` ids they list and
`tech` those the tech gate passes on the slug.

| Cause | Boards | Ledger postings | Ids listed today | With a tech title |
|---|---:|---:|---:|---:|
| lists postings, none with a tech title | 21 | 2,382 | 2,409 | 0 |
| lists tech titles today | 3 | 1,896 | 2,149 | 79 |
| slugs state no title (the gate is blind) | 3 | 1,839 | 1,812 | 0 |
| WAF challenge on the sitemap host | 2 | 6,787 | 0 | 0 |
| sitemaps list no posting | 1 | 9 | 0 | 0 |
| all | 30 | 12,913 | 6,370 | 79 |

- **Lists postings, none with a tech title (21):** `advocateaurorahealth` 663, `fortrex` 326,
  `emilfrey` 255, `intercaretherapy` 194, `optavise` 165, `ashfieldhealthcare` 136, `bradyplus` 118,
  `ciusss` 117, `regis` 115, `leonardcheshire` 105, `laplanduk` 71, `lindner` 29, `lawson` 27,
  `cyclecarriage` 25, `pontoonsolutions` 22, `recruitlink` 16, `santos` 10, `zungfu` 6, `a2milkkf` 4,
  `gsowstalentmatching` 3, `fmlogistic` 2. Healthcare, retail, hospitality and construction employers:
  a Board that reads `0` here is right, and the tech gate is not the cause: of their 2,409 titles, a
  whole-word scan for software, engineer, developer, devops, architect, cyber, programmer, data
  engineer/scientist/analyst, cloud, IT, network and database matched two dropped titles, both
  "IT Recruitment" recruiter roles at `pontoonsolutions`.
- **Lists tech titles today (3):** `loa` (L'Oréal) 1,967 ids, 48 with a tech title, job pages
  answer `200` with an `og:title`; `tennet` 136 ids, 21; `resourcebank` 46 ids, 10. Each has its own
  reason for reading zero; see below.
- **Slugs state no title (3):** `ucsf` 949, `mt` 531, `pomerleau` 332. The slug the tech gate reads is
  `1` on 521 of mt's 531 postings, a location on 81% of ucsf's (`San-Francisco-CA-United-States`) and a
  number on pomerleau's (`…/JobDetail/7155/3880`), so every title read as non-tech.
- **WAF challenge (2):** `cbreglobal` and `jacobs`. IBM's `ibmglobal` is the same and holds no ledger
  posting (its ledger row says `live`, 0).
- **Sitemaps list no posting (1):** `dttl`. Its one job portal, `applyglobal.deloitte.com/careers`, answers
  `302` to its own `errorpage/?errortype=404`, and the six others are talent-community and
  management portals whose sitemaps list five page names each.

Two more findings on the 3 that list tech titles:

- **tennet reads zero because a login-walled portal shadows the public one.** Its `robots.txt` names
  `tobedeleted1` before `careers`, both list the same 136 ids, and the first portal to list an id
  names its URL, so all 136 job pages are `…/tobedeleted1/JobDetail/…`, and 2 of 2 sampled answer
  `302` to `…/tobedeleted1/Login`. The same posting on the public portal
  (`careers.tennet.eu/en_US/careers/JobDetail/Enterprise-Architect-Enabling-Functions-m-w-d/113521`)
  answers `200` with its title and a 9,072-character description. The scraper settles a login-walled
  portal with one request, but only one whose name reads `internal`, `employee` or `referral`.
  Dropping that condition would cost one request per portal that lists postings (Bloomberg: 3) and
  hand `tobedeleted1`'s ids to `careers`. Not done here.
- **resourcebank's job pages redirect to `…?source=Careers+Portal`.** `_moved_job_page` follows a
  redirect only when its target ends at the posting id, so the query string stops it, and the page
  behind it (`200`) has an empty `og:title`, no JSON-LD and its title only in `<title>`. It needs a
  layout reader as well as the redirect. Not done here.

## Boards the fallback reads

ADR-0358's fallback, run on each Board through the scraper's own listing code with the detail pass
stubbed out (robots, sitemaps and search pages only, one request at a time at about 1.05 s apart, from
a plain client), on 2026-09-29:

| Board | Why | Rows read | Pass the tech gate | Requests | Paging time | Truncated |
|---|---|---:|---:|---|---|---|
| `siemens` | sitemap lists 58 pages, no posting | 2,001 | 446 (22.3%) | 335 search + 16 | 1,177 s, 3.5 s a page | yes: "999+ results" |
| `mt` | slug `1` | 520 | 72 (13.8%) | 54 search + 11 | 163 s total | yes: 520 of 531 results (98%) |
| `pomerleau` | slug is a number | 332 | 12 (3.6%) | 56 search + 9, over its job portals | 119 s total | no |
| `ucsf` | slug is a location | none | none | 1 search + 15 | 38 s total | no: its sitemap rows stand |

So 530 postings pass the pre-detail gate where 0 did, for 445 search requests a run over the four
Boards, and 0 for every Board whose slugs state titles. Two things set the cost:

- **A search page takes 2.5 to 3.5 s here**, so a listing at the Board's 1 request a second pace is
  latency-bound: 6 rows a page on Siemens (whatever page size is asked) is 334 pages. Avature serves
  no page past its 2,000th result: offset 2000 answered 6 rows, and 2004, 2040, 2100, 2250, 2500,
  3000, 6000 and 12000 answered none, on a page that says "1 - 6 of 999+ results". Siemens is
  therefore read newest first and truncated every run.
- **Siemens's job pages are 1.3 to 2.4 MB each** (3 of 3 tech pages sampled: 2,415,773, 1,334,104 and
  1,427,487 bytes), against 20 to 60 KB elsewhere. The three read a title, company, employment type
  and a description of 4,805 to 6,662 characters, and a location on two. Its `Work mode: Hybrid
  (Remote/Office)` reads `remote: true` through the existing reader, a fact this note leaves alone.

**ucsf** lists nothing to page: `careers.ucsf.edu/careers/SearchJobs` answers `200` with no result and no
`JobDetail` link (the page names `SearchJobsData/` in its script, the client-rendered shape maximus's
search page has too). Its titles are on its 949 job pages. Fetching them costs 949 pages of about 60 KB
at 1 a second for a university's handful of tech titles; the owner's call.

## Walled by a WAF challenge

IBM `ibmglobal` (ledger row `live`, 0 postings), CBRE `cbreglobal` (4,629 ledger postings) and
Jacobs (`jacobs`, 2,158): the sitemap host each robots.txt names is the customer's vanity host,
`careers.ibm.com`, `careers.cbre.com`, `careers.jacobs.com`, and it answers a plain client:

```text
HTTP/2 202 ... x-amzn-waf-action: challenge      (empty body)
```

on the sitemap index (CBRE, Jacobs) or the first locale sitemap (IBM). The Avature host
(`cbreglobal.avature.net`) serves the same index (200, 2,282 bytes), whose children name the vanity
host again, and a job page there answers `301` to the vanity host (`2026-09-28_full-ledger-note.md`),
so no job page is left that is not behind the challenge. `ibmglobal.avature.net` answers `200` with 0 bytes. Nothing is built around
it: a challenge is the customer's choice, not our client's shape. IBM's own search API
(`www-api.ibm.com/search/api/v2`, 1,936 hits per the coverage probe) is another surface and another
reader. CBRE and Jacobs will stay `BoardUnreadable` once ADR-0145's 14 days lapse (#880 raises
instead of returning `0`), and their cost rows keep their old `0` until then.

## Frozen behind a zero that is not one

ADR-0145's value gate does not scrape a Board whose last complete read found `0` Jobs and took over 120 s,
until that measurement is 14 days old. 14 Avature Boards are gated now, the 11 gated ones of the 30
and `deloitteus`, `emiratesjobs` and `mantech`. Today's listing sorts them:

| Board | Cost row written | Read again from | Listing today | Verdict |
|---|---|---|---|---|
| `deloitteus` | 09-26 20:50 | 10-10 | 1,306 ids, 513 tech-titled (522 rows served) | frozen wrongly |
| `emiratesjobs` | 09-26 19:20 | 10-10 | 1,481, 252 (215 rows served); job pages `200` | frozen wrongly |
| `mantech` | 09-28 12:19 | 10-12 | 852, 418 (420 rows served) | frozen wrongly |
| `loa` | 09-29 00:20 | 10-13 | 1,967, 48; job pages `200` | frozen wrongly, cause of the zero not visible |
| `tennet` | 09-26 18:16 | 10-10 | 136, 21 | reads zero for the shadowing above |
| `cbreglobal`, `jacobs` | 09-28 09:44 | 10-12 | WAF challenge | unreadable either way |
| `advocateaurorahealth`, `ciusss`, `fortrex`, `regis` | 09-26 18:16 | 10-10 | 117 to 663 ids, no tech title | the gate is right |
| `laplanduk`, `recruitlink` | 09-28 09:44 | 10-12 | 71 and 16 ids, no tech title | the gate is right |
| `dttl` | 09-26 18:16 | 10-10 | no public job portal | the gate is right |

The zeros of `deloitteus`, `emiratesjobs`, `mantech` and `loa` were all measured before #880 (merged
2026-09-29 04:50 UTC), when an empty sitemap body counted as a portal with nothing to list;
`deloitteus`'s `careers` sitemap read 0 bytes twice and then 1,304 ids (#880's own measurement).
Since #880 the same read raises `BoardUnreadable`, which the cost ledger records as an error and
which keeps the Board's last known count, so it cannot freeze a Board again. What is left is the
four old rows, 1,157 served rows among them (522, 420, 215; `loa` serves none).

**Proposed, not done.** Blank `jobs` on those four rows of `data/state/board_cost.csv`
(`avature:deloitteus`, `avature:emiratesjobs`, `avature:mantech`, `avature:loa`), through
`cost_ledger.load` and `save` under `state_guard`. A `None` is "never measured", so
`_gated_boards` applies the 600 s floor, which all four (261, 268, 413 and 172 s) sit under, and they
are read in the next Slice instead of on 10-10 to 10-13. No code changes. It is a write to HF state,
which is why it is a proposal here and not a change.

## A listing that is not `SearchJobs`

**Two Sigma** (`twosigma`, `careers.twosigma.com/careers`). Its sitemap lists 30 page URLs, among
them `JobDetail`, `OpenRoles`, `ExperiencedRoles` and `InternshipsAndEarlyCareers`, and no posting.
`/careers/SearchJobs/` answers `404`. The postings are on `OpenRoles`, paged 10 a page by
`jobOffset` (offset 50 holds 2 ids, offset 100 none: about 52 postings), with the same
`paginationNextLink` the fallback follows and the same result header. Reading it needs the page name
for this one tenant, `careers/OpenRoles`, which nothing in the sitemap states as the listing;
ADR-0358 leaves it to the owner (`_FIXED_FACETS_BY_SLUG`'s shape, five lines and a test).

**McKinsey** (`mckinsey`): four utility portals, no job portal; its jobs are on a separate gateway.
Not this scraper's.

## Census of the zero-ledger rows

The ledger holds 195 live rows that list no posting. Eight of them (`siemens`, `twosigma`, `ibmglobal`,
`epic`, `mckinsey`, `deloitteus`, `mantech`, `emiratesjobs`) were read for the tables above. A seeded
random 60 of the other 187, and `siemens`, `twosigma`, `epic`, `mckinsey` and `dttl` re-read with the
job-page test, were read the same way: robots, each public
portal's sitemap index and its first locale sitemap, and the search page of every portal that names a
bare `/JobDetail` page and lists no posting. 65 Boards:

| What the Board is | Boards |
|---|---:|
| names no job page in any sitemap (onboarding, events, talent-community portals; `epic`, `mckinsey`, `dttl`) | 33 |
| its `robots.txt` names only portals whose names read `internal`, `employee` or `referral` | 20 |
| names a job page and lists no posting | 11 |
| unreachable (`gpshospitality`, a connection error) | 1 |
| all | 65 |

Of the 11 that name a job page, the search page reads:

| Search page | Boards |
|---|---:|
| `200` with a result on the page (`siemens`, the one the fallback reads) | 1 |
| gone (`404`): `twosigma`, `govca`, `henrico` | 3 |
| lands on a login: `maximus`, `vccs`, `cfafranchise` (its other portal answers `404`) | 3 |
| `200`, no result on the page: `ascareers`, `ocasata`, `pepsicoglobalpontoon` ("0 results") | 3 |
| `200`, "999+ results" and a next link, and `PipelineDetail` links in place of `JobDetail` | 1 |

The last is `healthfirst`: its postings are `…/careers/PipelineDetail/{slug}/{id}`, a record type the
scraper's URL shape does not read, so it is another zero the ledger shows as `live, 0`. Not built here.

So of the 60 random Boards, none reads through a search page (`siemens` and `twosigma` are the named
ones, and `twosigma`'s does not exist). What the fallback costs a Board that reads zero is one search
request per job portal that lists none: 11 requests for the 9 sampled Boards that name one, about 35
for the 187 over a rotation, and nothing for the 53 that name no job page.
