# Are Oracle's `ejwl*` and `jpmc-test` pods real employers? Mostly yes — so don't drop them for that reason

**Date:** 2026-09-16. **Question:** the five-run log review proposed removing
`oracle:ejwl.fa.us2.oraclecloud.com`, `oracle:ejwl-dev7.fa.us2.oraclecloud.com` and
`oracle:jpmc-test.fa.oraclecloud.com` from the scrape on the grounds that they are demo/load-test
tenants. `CLAUDE.md`'s own Oracle entry describes `ejwl` as "Oracle's own 78,431-posting load-test
instance". Is that true of what they serve today?

**Verdict: no, and the proposed change should not be made on that basis.** Sampling 300 titles per
pod across three offsets (0 / 3,000 / 6,000), live:

| pod | `TotalJobsCount` | sampled | test-marked | share |
| --- | ---: | ---: | ---: | ---: |
| `ejwl.fa.us2.oraclecloud.com` | 13,642 | 300 | **0** | **0.0%** |
| `ejwl-dev7.fa.us2.oraclecloud.com` | 7,977 | 300 | 2 | 0.7% |
| `jpmc-test.fa.oraclecloud.com` | 10,150 | 300 | 9 | 3.0% |

("test-marked" = title matching `test|testing|dummy|sample|demo|do not apply|xxx|zz|TEST\d|CWB`.)

`ejwl` serves ordinary hospitality postings — *Commis (Uzbek national)* in Tashkent, *Junior Sous
Chef* in Bucharest, *Agent-Front Desk* in Bali, *Ekspert_ka ds. HR* in Warsaw. Nothing about them
reads as synthetic.

## The trap this walked into, and nearly published

The **first page** of `ejwl-dev7` and `jpmc-test` looks damning: `Operator-Room Service - TEST`,
`9/4 CWB TESTING`, `VP-Technology Product Mgmt - TEST1`, and five consecutive
`… - enable auto approval for testing` titles on `jpmc-test`. Reading that page alone, both pods
are obviously fake.

Across 300 titles they are 0.7% and 3.0%. The synthetic postings are **clustered at the head of the
default sort** (`POSTING_DATES_DESC` — recent test requisitions sort first), which is exactly the
page a quick check looks at. This is the repo's own standing lesson about generalising a provider-
wide claim from one sample, hit again in a new place.

## What is still true, and is a different argument

The *cost* case against `ejwl` is untouched by this and rests on its own measurements:

- It reads **9,926 of 13,642** every run — Oracle's API serves no offset past 10,000, so the Board
  is ~27% incomplete **by construction** and can never be complete however often it is scraped.
- It is therefore **scope-excluded on every run**, contributing nothing to eviction scope.
- It is the single most expensive Board in the pipeline (1,070-1,924 Board-seconds, the `max` of
  its shard in every run it appears) and takes **12% of all spare-egress rotations**.

That is a coverage/value decision — "is a permanently-27%-incomplete Board worth the most expensive
slot in the run?" — and it should be argued and decided on those terms. It is **not** the decision
the review proposed, and it does not follow from the pods being fake, because they are not.

## Action taken

None to the ledger. `CLAUDE.md`'s "78,431-posting load-test instance" gloss for `ejwl` does not
match what the pod serves today and should be re-checked by whoever wrote it, but I have not edited
that line: the original measurement may have been accurate when made, and overwriting it from a
single later sample would repeat the error this note is about.
