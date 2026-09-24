# ADR-0198: TikTok and ByteDance share one scraper and keep two `ats` values

**Status:** accepted · **Date:** 2026-09-24 · **Relates to:**
[ADR-0139](0139-a-single-source-board-is-its-own-ats.md) (each Single source scraper is its own
`ats`, which this keeps),
[ADR-0053](0053-scope-eviction-on-scrape-outcome.md) and
[ADR-0121](0121-a-negligible-shortfall-is-still-an-authoritative-list.md) (the truncation verdicts
the shared walk reports),
[ADR-0023](0023-prune-stale-and-duplicate-index-rows.md) (why no Job id may change)

## Context

`tiktok.py` and `bytedance.py` were built in parallel on 2026-09-11 and each concluded the other
was probably a different system. `bytedance.py`'s docstring said the two should be reconsidered as
one scraper if TikTok's host turned out to serve the same `/api/v1/public/supplier` routes.
`tiktok.py`'s docstring said it did, and also that the two had "separate request shapes" and each
needed its own scraper. Both copies POSTed to `/api/v1/public/supplier/search/job/posts`, read the
same `{code, data: {job_post_list, count}}` envelope and parsed the same record fields, but they had
drifted in five places:

| | `tiktok.py` | `bytedance.py` |
| --- | --- | --- |
| envelope `code` not 0 | marked truncated (and a missing `code` read as success) | raised `RuntimeError` |
| next offset | `+= len(batch)` | `+= _PAGE_SIZE` |
| walk ends on | an empty page, a short page, or `offset >= count` | an empty page or `len(posts) >= count` |
| location dedupe | a name already kept anywhere | only the name just kept |
| `i18n_name` fallback | none; `job_subject` stood in for `department` | on location, `department` and `employment_type` |

The headers (`_headers()` method against a module `_HEADERS`), the request bodies, the page sizes
(100 against 200) and the page caps (500 against 50) differed too.

## The measurement

All live, 2026-09-24, both hosts, this repo's own User-Agent.

**It is one backend, and the `website-path` header picks the Board, not the host.**

| host | `website-path` | answer |
| --- | --- | --- |
| `api.lifeattiktok.com` | `tiktok` | 200, count 4,301 (TikTok) |
| `api.lifeattiktok.com` | `en` | 200, count 1,420 (ByteDance) |
| `jobs.bytedance.com` | `tiktok` | 200, count 4,301 (TikTok) |
| `jobs.bytedance.com` | `en` | 200, count 1,420 (ByteDance) |
| either | none, or `bytedance` | 400 `invalid request` |

A string `offset` fails on both hosts with the same Go type name,
`json: cannot unmarshal string into Go struct field BizListJobPostReq.offset of type int32`.
`tiktok.py`'s 2026-09-11 test sent `website-path: bytedance`, which no host accepts, and read the 400
as proof the two were separate systems.

**The request body does not matter.** Each host was sent TikTok's body, ByteDance's body and one
holding only `keyword`, `limit` and `offset`. All three got the same `count` and the same first ids
on both hosts. `accept-language` is not required by either host, which contradicts
`bytedance.py`'s "required" claim. On the ByteDance Board it sets the language of every
`i18n_name`: without the header, or with `zh-CN`, `i18n_name` is Chinese (`西雅图`, `研发`), and
with `en-US` it matches `en_name`. `Origin` and `Referer` change nothing on TikTok's host.

**The error envelope is the same on both hosts.** A negative `offset` or `limit` answers HTTP 200
`{"code": -4000001, "data": null, "message": ...}` (`System error` in English, `系统出现问题` on
TikTok's host). A string `offset` is HTTP 400 with no JSON. No valid request produced a non-zero
`code`.

**No page short of the limit came before the last one.** A full walk of each Board read 45 pages at
100 rows for TikTok (4,301 ids, all unique) and 9 pages at 200 rows for ByteDance (1,420, all
unique). `count` held one value for the whole walk on each Board. Both hosts served `limit=1,000`
without a clamp. The empty page at `offset = count` still reports the real `count`.

**The result window is 10,000.** Once `offset + limit` passes 10,000 the backend returns 0 rows and
reports `count` as 10,000, even for a request that overlaps real rows (`offset=4200, limit=5801`
returned 0 rows; `limit=5800` returned the last 101).

**Record fields.** Across all 5,721 postings of both Boards: every location chain is 3 levels deep
and has the shape ABC, AAA or AAB, never ABA; every `i18n_name` equals its `en_name`;
`job_category.en_name` is on every row, so `job_subject` never stood in for `department`; every row
has both a `description` and a `requirement`; every id is a string.

## Decision

**One implementation, `headstart.scrapers.supplier_search.SupplierSearchScraper`, and two
subclasses.** `TikTokScraper` and `ByteDanceScraper` keep their modules, `ats` values, registry
entries, ledger rows, `COMPANY`, `url_shape`, `slug`, `url()` and `job_url()`. Each also states two
class attributes: `search_url` (its own host) and `website_path` (`tiktok`, `en`). The module is
named for the API route both hosts serve, because no product name covers both brands. A
brand-scoped name such as `bytedance_supplier` was considered and rejected: next to `bytedance.py`
it would be a near-homograph (CLAUDE.md §3), and it would read as the ByteDance Board's alone.
ADR-0139 rejected one dispatch key, not shared code, and still holds: this is one class tree, not
one `ats`.

For each difference:

- **Envelope `code`: anything but 0 marks the Board truncated and keeps what was read.** A missing
  `code` counts as an error. Measurement could not settle this: no valid request produced an
  error. So the conservative choice stands. Raising dropped every posting already read for the run.
  Marking truncated keeps those postings, and `update_ledgers` treats the Board exactly as it treats
  a raised one ("truncated or raised", ADR-0053). `tiktok.py`'s `if code:` let a missing `code`
  through as success, so that half of its rule is not kept.
- **Next offset: the number of rows already read (TikTok's `+= len(batch)`), and a short page does
  not end the walk.** The walk ends on an empty page, at `count`, on a non-zero `code` or at the
  result window below. No mid-walk short page was measured on either Board, so the
  two copies read the same rows today. Under a silent clamp or a transient short page, this is the
  only one of the three rules that neither skips rows (`+= _PAGE_SIZE`) nor stops early (TikTok's
  short-page stop). A walk that still ends short of `count` is reported by
  `mark_truncated_unless_negligible`, as both copies did before.
- **Location: each place named once.** Both rules give the same string on every chain measured,
  because an ABA chain never occurs. Naming each place once keeps every distinct name and never
  repeats one.
- **`i18n_name` stands in for an empty `en_name`** on location levels, `department` and
  `employment_type`. It changed no measured row. It is safe only because `accept-language: en-US`
  is now sent to both hosts, which makes `i18n_name` English.
- **`department` reads only `job_category`.** `job_subject` is a campus-cohort label ("PhD
  Graduates - 2027 Start"), not a team, and TikTok's fallback to it never fired.
- **Every label is stripped.** Neither copy stripped `department` or `employment_type`. Stripping
  them changed no measured row.

The request becomes one shape: the minimal body; headers `User-Agent`, `Content-Type`,
`accept-language: en-US` and `website-path`; page size 200 (ByteDance's). **The page caps are
replaced by the result window.** No request asks past row 10,000: the last page's limit is cut to
fit, because a request crossing the window answers 0 rows and would read as the end. A walk that
reaches row 10,000 is marked truncated whatever `count` says, because `count` itself may read
10,000 there. A Board of exactly 10,000 postings would therefore be marked truncated when it is
complete. That is the conservative error, and the largest Board today is 4,301. The window also
holds under a silent clamp: pages of 50 still walk to row 10,000, where a page cap would stop at
2,500. The cost is that nothing bounds the number of requests any more, only the rows. Under a
clamp to 10 rows, TikTok's 4,298 postings would take about 430 requests, 15 to 30 minutes at the 2
to 4 seconds a ByteDance request took on 2026-09-11. Both hosts served 1,000 rows unclamped, so
this is hypothetical today.

## Proof that no Job changed

The pipeline's own `get_scraper` path ran live against both Boards before and after the change. The
Jobs were compared field by field, ignoring `scraped_at`.

| Board | before | after | ids only before | ids only after | changed fields | URLs changed |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `tiktok:lifeattiktok.com` | 4,301 | 4,301 | 0 | 0 | 0 | 0 |
| `bytedance:jobs.bytedance.com` | 1,420 | 1,420 | 0 | 0 | 0 | 0 |

The *before* run's raw postings were also parsed again with the new code. Every one of the 5,721
Jobs came out identical. Neither run was truncated.

After each review round (the result window replaced the page caps), the merge-base classes and
the merged ones ran back to back against the live Boards. On the last run both sides read the same
4,298 TikTok and 1,421 ByteDance postings. No id appeared on one side only, and no Job differed.

## Alternatives considered

- **Keep two copies.** Rejected: they had already drifted in five places. The bug `tiktok.py` fixed
  on 2026-09-12 (a non-zero `code`) had the opposite fix in `bytedance.py`. A second copy is how the
  next fix reaches only one Board.
- **One `ats` that dispatches on `website-path`.** Rejected by ADR-0139 for reasons that still hold.
  Two `ats` values keep two ledgers, two `url_shape`s and independent tuning. Changing the `ats`
  would also change every Job id and evict both Boards' rows (ADR-0023).
- **Keep each site's own request body.** Rejected: every body got the same answer on both hosts.
  Keeping two would be configuration that nothing reads.

## Consequences

- TikTok's walk halves, from 44 requests to 22, because it now pages at 200.
- A Board past 10,000 postings is marked truncated at the window, not read short in silence. That
  holds whether `count` states its real total or stops at 10,000; the second case is unmeasured,
  and it is why reaching the window is a verdict of its own. Neither Board is within twice that
  size: the largest today is TikTok, at 4,301. A Board that stayed past the window would be
  scope-excluded on every run (ADR-0053), which has no drain, so its closed postings would be
  served indefinitely. The old page caps behaved the same way.
- A non-zero `code` on ByteDance is now an INFO truncation line. Before, it raised an unexpected
  exception, and `harvest` prints a traceback only for the first of those in a run (`log.FirstOnly`)
  and an INFO line for each later one. The Board's authority is decided the same way either way.
- `tiktok.py`'s claim of "separate request shapes" and `bytedance.py`'s "`accept-language` is
  required" and "reconsider as one scraper" are gone from the docstrings. The 2026-09-11 measurement
  docs now carry a note pointing here.
