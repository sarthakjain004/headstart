# Carrier: gr8people lists more records; Workday has the fresher public job set

Live comparison on **2026-10-02**, begun at 16:26 IST, of Carrier's
[Workday jobs Board](https://carrier.wd5.myworkdayjobs.com/jobs) and
[gr8people Board](https://carriernoam.workgr8.com/jobs). This tests the freshness
question raised while building #970 / PR #1017. The investigation itself changed
no Board eligibility. The owner subsequently approved parking Carrier's gr8people
Board; ADR-0373 records that landing decision.

## Result

Workday lists fewer postings, but substantially more recently dated ones.
The two sources **do not carry the same most recent job set**.

| Measure | Workday | gr8people |
| --- | ---: | ---: |
| Listed postings | 1,054 | 4,167 |
| Distinct stated requisitions | 1,054 | 4,162 |
| Dated September 26–October 2 (seven calendar dates) | 366 | 41 |
| Dated September 3–October 2 (30 calendar dates) | 1,050 | 281 |

Dated counts compare **source-stated publication dates**, deduplicated by
requisition id. Workday exposes a day-only `startDate`; gr8people exposes UTC
`postedOn` timestamps. These are not proof that the requisitions were first
created then: a posting can be republished. Calendar boundaries also depend on
timezone. Converting gr8people timestamps to India gives 61 instead of 41 in
the seven-calendar-date window and 283 instead of 281 in the 30-date window;
**all 61 India-window requisitions are still present in Workday**. Neither
timezone interpretation changes the coverage conclusion.

- All **41** recently dated gr8people requisitions in the source-date seven-day
  window are present in Workday and recently dated there too.
- Of Workday's **366** recently dated requisitions, **268 are missing from the
  entire gr8people listing**, not merely dated differently. The other 98 are
  present there; 57 of those carry an older gr8people date.
- Workday has **27** details dated October 2. Every one of those requisition ids
  is absent from the full gr8people listing. Any 20 drawn from that latest
  Workday date cohort therefore share **zero** ids with gr8people's newest 20.
- gr8people's newest timestamp is October 1, 22:00:09.693 UTC, or **October 2,
  03:30:09 IST**. Calling that "no jobs today" would be misleading for an Indian
  reader; the meaningful difference is the missing requisition ids.
- **2,039** gr8people records carry a posting date before 2024. Their age alone
  does not prove closure, but it explains much of the larger record count.

One verified Workday-only current tech example is
[Senior AI Engineer, Test Automation, requisition 30219335](https://carrier.wd5.myworkdayjobs.com/jobs/job/Viessmannstrae-1-35107-Allendorf-Eder-Germany/Senior-AI-Engineer--Test-Automation--m-w-d-_30219335).
Its detail states `startDate=2026-10-02`, `posted=true`, `canApply=true`; its
requisition id does not occur among gr8people's 4,167 records.

## Check the apparently recent gr8people-only extras

In the 30-source-date window, **36** gr8people requisitions are not in Workday's
current listing. Each public gr8people Apply / Skip & Continue redirect was
checked: all 36 go to the **same Carrier Workday jobs Board**, not a second ATS
or an uncounted sibling Board. The backing CXS detail requests returned
**HTTP 403 with error S22 for 36/36**, without posting details.

This is not classified from a status code alone. Three positive controls from
gr8people's newest records, which are in Workday, followed the same redirect
and API derivation and returned **200, the correct requisition id, and
canApply=true (3/3)**. The public browser was also opened on one failed extra,
[Senior Data Engineer, requisition 30202262](https://carrier.wd5.myworkdayjobs.com/jobs/job/CAFLO-Carrier-Home-Florida-Remote-Location-Remote-City-FL-33412-USA/Senior-Data-Engineer_30202262-1):
Workday displayed **“The page you are looking for doesn't exist.”** Its
gr8people record still carries a September 16 posting timestamp.

These results are strong evidence that the 36 sampled recent extras do not
provide additional usable public applications. Only one was browser-confirmed;
the other 35 share the same API outcome and were not individually opened in a
browser. The remaining older gr8people-only records were not exhaustively
checked. The evidence supports using Workday for Carrier and parking Carrier's
gr8people mirror pending its stale-feed audit; it is not a platform-wide verdict
against other gr8people customers.

## Completeness and provenance

Both full lists were fetched directly in this session. Workday's first serial
read saw the count rise from 1,052 to 1,054, so it was discarded for the comparison.
A parallel page read yielded **1,054 unique paths**, with the first-page total
still **1,054 before and after**. All **1,054** Workday details were then read
successfully, with no detail errors, to obtain exact `startDate` values rather
than infer timestamps from relative labels. The gr8people scraper returned all
**4,167** records against its own stated total, without truncation.

The matching key is Workday `jobReqId` / listing `bulletFields` against the
gr8people `structuredDataJSON.identifier`, not a title or employer-name heuristic.
There are **439 shared requisitions**, **615 Workday-only**, and **3,723
gr8people-only** distinct requisitions. For the shared ones, 208 source-stated
calendar dates agree; dates on the others differ, so a date comparison alone
would be insufficient.

Read-only sources:

- Workday listing: `POST https://carrier.wd5.myworkdayjobs.com/wday/cxs/carrier/jobs/jobs`,
  20 rows per offset, empty appliedFacets and searchText.
- Workday details: `GET https://carrier.wd5.myworkdayjobs.com/wday/cxs/carrier/jobs{externalPath}`.
- gr8people: its public `/jobs` page and anonymous `/graphql` search, paged by
  the scraper in this branch.
- Public external-ATS redirect: the Board's own `cappLogin.submitToThirdParty`
  link; no applicant data or application form was submitted.

Reproduction scripts and captures are local, uncommitted, under
`experiment/gr8people-carrier-freshness/`: `compare.py` and
`check_recent_extra_links.py`, plus `artifacts/2026-10-02_freshness_comparison.json`,
the complete listings, Workday date JSONL and the 39 redirect/detail checks
(36 extras plus three positive controls). No HF state, pipeline workflow,
parking configuration, alias ledger or scraper code was changed for this check.
