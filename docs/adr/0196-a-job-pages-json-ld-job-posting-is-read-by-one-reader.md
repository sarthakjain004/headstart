# ADR-0196: A job page's JSON-LD JobPosting is read by one reader

**Status:** accepted · **Date:** 2026-09-24 · **Relates to:**
[ADR-0099](0099-a-404d-workday-detail-falls-back-to-the-public-pages-json-ld.md) (Workday's
public-page fallback, one of the readers replaced),
[ADR-0184](0184-a-pinpoint-board-is-read-from-its-listing-and-dated-from-its-page.md) (Pinpoint's
page date, another), [ADR-0157](0157-a-scrapers-job-url-is-declared-once-not-authored-three-times.md)
(the same move for job URLs: one declaration instead of copies that drift)

## Context

Nine scrapers read a schema.org `JobPosting` out of a job page's
`<script type="application/ld+json">` blocks: eightfold (the sitemap fallback), icims, meta,
successfactors, workday (ADR-0099), jazzhr, trakstar, pinpoint and jobvite. Each carried its own
copy of the reader, and nothing outside `scrapers/` shared one:

- the block regex was written nine times as six distinct patterns, three of which (eightfold,
  successfactors, jazzhr) matched only the exact `<script type="application/ld+json">` form;
- four copies (eightfold, icims, meta, successfactors) mapped the same six fields the same way —
  the same loop, list-`@type` check, `employmentType` join and `TELECOMMUTE` → remote — in about
  thirty lines each;
- workday and pinpoint mapped fields of their own, and jazzhr, trakstar and jobvite returned the
  raw node; across all nine the finding drifted: only trakstar parsed with `strict=False`;
  jazzhr, trakstar and pinpoint rejected a list `@type`; jazzhr, pinpoint and jobvite rejected a
  top-level array; jobvite read only the first block and checked no type at all; none read
  `@graph`.

Turning a `jobLocation`'s PostalAddress into "Locality, Region, Country" was written seven times.
Meta's and successfactors' bodies were the same rule (one took the `jobLocation`, the other the
node); icims also drops iCIMS's `UNAVAILABLE` placeholder; eightfold drops a part an earlier part
already holds ("Telangana,IN" holds "IN"); teamtailor reads only a list `jobLocation`, does not
strip and does not unwrap a `Country` node; jobvite dedupes case-insensitively and strips trailing
commas, with a fallback to its HTML meta line; ashby builds a list of every name, the headline
string included, across several entries.

`taleo_be` also reads one JobPosting field, `datePosted`, but by a regex over the raw page rather
than by parsing a block — its own test page states no `@type` at all — so it is not one of these
readers and is left as it is.

A fix to one copy reached one scraper. Trakstar's `strict=False` was found because its pages embed
literal newlines in strings; any other ATS whose pages started doing the same would have silently
lost every field.

## Decision

**One module, `headstart.scrapers.job_posting_jsonld`, finds and reads the JobPosting; each
scraper keeps only its own choices.** It is a helper, not a scraper, and the registry does not
name it. Its interface:

- `jsonld_nodes(page, schema_type)` — every node of one type, in document order; and
  `find_job_posting(page)`, the first `JobPosting`. Every ld+json block is read, whatever the
  tag's attributes; JSON is parsed with `strict=False`; `@type` may be a string or a list; a block
  may be one node, an array, or carry a `@graph`. A block that does not parse is skipped.
- `job_posting_fields(node)` — the six fields the four near-identical copies mapped, as a
  `JobPostingFields` TypedDict: `title`, `description`, `location`, `posted_at`,
  `employment_type` (a list joined with ", "), `remote` (True on `TELECOMMUTE`, else None).
- `job_location_text(job_location, *, placeholders=(), drop_repeats=False)` — the first Place's
  "Locality, Region, Country" (what becomes `Job.location`), parts stripped and empties dropped.
  The two options are exactly the two rules that differed among the four copies that share it:
  icims passes `placeholders={"UNAVAILABLE"}`, eightfold `drop_repeats=True`.

What stays in each scraper: icims's field allowlist, its fabricated-date filter and its salary;
meta's extra description sections; eightfold's `department: None`; workday's choice to skip a
`JobPosting` without a description, take the *first* `employmentType` and map it onto its own
`timeType` wording (every one of 50 live Workday pages sampled states a single string, so joining
a list is not measured to be right for it); pinpoint's `applicantLocationRequirements` country;
jazzhr's `Organization` name (read through `jsonld_nodes`); jobvite's HTML fallback, and its own
`_location`, whose case-insensitive, comma-stripping dedupe is a different rule, not an option of
this one. ashby's `_place_names` is a different function altogether and stays.

Teamtailor's `_location` stays too. Its output matched `job_location_text` on 963 of 963 items of
10 live Boards' feeds, but its code differs on shapes that sample never held (a dict
`jobLocation`, padded parts, a `Country` node), so moving it would change output nobody measured.

## Evidence: outputs did not change

Each migrated reader was run in its pre-change form and its new form over the same pages, and the
outputs compared as whole values:

| ATS | Fixture pages identical / changed | Live pages identical / changed (Boards) |
| --- | --- | --- |
| eightfold | — | 50 / 0 (10) |
| icims | 4 / 0 | 50 / 0 (10) |
| meta | 3 / 0 | 50 / 0 (1) |
| successfactors (whole `_page_fields`) | — | 50 / 0 (10), none carrying JSON-LD |
| workday | — | 50 / 0 (10) |
| jazzhr (detail) | 1 / 0 | 50 / 0 (10) |
| jazzhr (listing `Organization`) | 1 / 0 | 10 / 0 (10) |
| trakstar | — | 50 / 0 (10) |
| pinpoint | 1 / 0 | 50 / 0 (10) |
| jobvite | — | 50 / 0 (10) |

Live pages were fetched 2026-09-24 from live ledger rows, five per Board, exactly as each
scraper's detail pass fetches them: 450 job pages and 10 JazzHR listing pages. Every per-scraper
test passes unchanged.

Then every reader, old and new, was run over 499 pages at once — the fixtures, the live pages of
every ATS, each ld+json string literal in `tests/`, and one synthetic page of each shape the
reader accepts — so that a reader meets shapes its own ATS never served. On the real pages, no
reader lost a posting or changed a value it had read from JSON-LD. Every difference there is a
page the old copy could not read and the shared one can, and each traces to one of the three
shapes below: eightfold and jazzhr now read Meta's attributed tags (52 pages), every reader but
trakstar now reads Trakstar's lenient JSON (45), and jobvite now reads JazzHR's JobPosting in a
second block (33). SuccessFactors, whose own pages gave its reader nothing to read, returned the
same fields as before on every JSON-LD page its old copy could parse; on the Meta and Trakstar
pages it could not, its fields now come from the JSON-LD instead of its page-markup fallbacks.

**Two differences are not gains, and both sit on shapes no live page carried.** Each follows
from a scraper reading more than before, and each was accepted rather than coded around:

- *Two JobPostings, the first unreadable to the old copy.* The old copy served the second; the
  shared reader serves the first. None of the 460 live pages carried more than one `JobPosting`.
- *A markup fallback displaced.* icims's classic-template reader, jobvite's rendered blocks and
  successfactors' CSB markup run only when no `JobPosting` is found. A page whose JSON-LD only the
  shared reader can parse now yields that JSON-LD's fields instead of the fallback's. Every live
  icims and jobvite JSON-LD block parsed under the old copy's rules (50 of 50, 25 of 25).

`tests/fixtures/job_posting_jsonld_pages.json` keeps one live page of each of those three shapes,
and `tests/test_job_posting_jsonld.py` reads a posting from each.

**No recall gain was measured, and none is claimed.** On the live sample, no page used `@graph`,
a list `@type` or a top-level array; the leniencies that did occur were each already handled by
the one scraper that met them — trakstar's `strict=False` (45 of its 45 JSON-LD pages need it),
meta's attribute-tolerant tag (50 of 50), jazzhr's later block (33 of 33). What changes is that
every scraper now has all of them.

**One narrowing, measured inert.** Jobvite's copy took the first block whatever its `@type`; the
reader takes a `JobPosting`. Every one of the 25 live Jobvite pages with JSON-LD states
`"@type": "JobPosting"` in its first block, so nothing changed; a block without it now falls to
the HTML fallback rather than being served as a posting.

**SuccessFactors pages carry no JSON-LD today.** Its reader is migrated like the others, but the
sample never exercised it: none of the 50 pages from its 10 largest Boards, nor the probe page of
195 more live Boards, has an `application/ld+json` block — every field came from the CSB
microdata and label fallbacks. The JSON-LD branch stays, since it costs nothing where absent; its
module docstring's "classic RMK pages embed a JSON-LD `JobPosting`" is not borne out by this
sample.

## Consequences

- A new page-reading scraper calls `find_job_posting` and `job_posting_fields` rather than
  writing another copy.
- A fix to how JSON-LD is found reaches every scraper at once. So does a regression: the table
  above is the measurement to repeat before changing the finder.
- Job ids, URLs and every served field are unchanged, so no `DERIVATIONS_VERSION` bump and no
  re-scrape is needed.

## Amendment (2026-09-28): every Place is kept

`job_location_text` now returns every Place's "Locality, Region, Country", "; "-joined in order
without repeats; a Place with no address is skipped rather than ending the read, and `placeholders`
and `drop_repeats` apply within each Place. Gem's own `_location` does the same over its
`locations`. The first-Place rule dropped a real location on a large share of postings, and the
location filter is a substring match that should find a posting under each place it is open in.
"; " is the separator the scrapers that already join places use (Workday, Workable, Radancy and
the rest).

Measured live 2026-09-28: a seeded sample of four Boards per ATS, 20-150 postings each, read
through each scraper's own `fetch()` on `main` and on this change and compared by id. Meta is one
Board, so 120 of its 1,019 sitemap pages were captured once and parsed by both.

| ATS | postings compared | location gained places | most places |
| --- | ---: | ---: | ---: |
| Meta | 120 | 62 | 12 |
| Gem | 212 | 46 | 5 |
| iCIMS | 134 | 31 | 12 |
| Eightfold | 323 | 0 | 1 |
| SuccessFactors | 270 | 0 | 1 |
| Avature | 193 | 0 | 1 |

In every changed row the old value is the new one's first place, so the change only adds places.
iCIMS's "4 of 207 sampled" understated it: 31 of 134 here. On Eightfold, SuccessFactors and
Avature the change is inert on the path the sample read, which says nothing about how many
locations their postings have. Eightfold reads JSON-LD only in its sitemap fallback, which no
sampled Board took. SuccessFactors pages carry no JSON-LD (above).

Eightfold's API path, which is not this reader, kept only the first non-empty place of its
`locations` in the same way. It now keeps every one, each repaired from its own
`standardizedLocations` entry as before (`eightfold._location`). Measured the same way on four
seeded Boards (symetra, corteva, vialto, paypal): 168 of 624 postings gained places, up to 19;
every old value was the new one's first place. `remote` comes from `workLocationOption`, else
from `is_remote(location)`, so that fallback now sees every place too; it changed on none of the
624, and it reaches an already-indexed row only on a sweep (ADR-0118), as for Meta and iCIMS.

Meta and iCIMS fall back to `is_remote(location)` when the page states no remote type, and that
check now sees every place, so a posting with a "Remote" place among others now reads as remote.
`remote` changed on none of the sampled postings.

`location` is a fact that `update_meta` re-observes on every scrape (ADR-0061), and `country` is
re-derived when it moves (ADR-0138), so an already-indexed row picks this up the next time its
Board is scraped; no `DERIVATIONS_VERSION` bump. `remote` is refreshed only on a sweep or
re-derive (ADR-0118). A place in India anywhere in the string now classifies the row as India,
where before only the first place counted.
