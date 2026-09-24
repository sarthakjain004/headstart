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
- the rest returned the raw node, and drifted: only trakstar parsed with `strict=False`; jazzhr,
  trakstar and pinpoint rejected a list `@type`; jazzhr, pinpoint and jobvite rejected a
  top-level array; jobvite read only the first block and checked no type at all; none read
  `@graph`.

Turning a `jobLocation`'s PostalAddress into "Locality, Region, Country" was written seven times.
Two bodies were identical (meta, successfactors); icims also drops iCIMS's `UNAVAILABLE`
placeholder; eightfold drops a part an earlier part already holds ("Telangana,IN" holds "IN");
teamtailor did not strip; jobvite dedupes case-insensitively and strips trailing commas, with a
fallback to its HTML meta line; ashby builds a list of every name, the headline string included,
across several entries.

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
- `place_of(job_location, *, placeholders=(), drop_repeats=False)` — the first Place's
  "Locality, Region, Country", parts stripped and empties dropped. The two options are exactly the
  two rules that differed among the copies that could share it: icims passes
  `placeholders={"UNAVAILABLE"}`, eightfold `drop_repeats=True`.

What stays in each scraper: icims's field allowlist, its fabricated-date filter and its salary;
meta's extra description sections; eightfold's `department: None`; workday's choice to skip a
`JobPosting` without a description, take the *first* `employmentType` and map it onto its own
`timeType` wording (every one of 50 live Workday pages sampled states a single string, so joining
a list is not measured to be right for it); pinpoint's `applicantLocationRequirements` country;
jazzhr's `Organization` name (read through `jsonld_nodes`); jobvite's HTML fallback, and its own
`_location`, whose case-insensitive, comma-stripping dedupe is a different rule, not an option of
this one. ashby's `_place_names` is a different function altogether and stays.

Teamtailor reads the same PostalAddress from its JSON feed, not a page, and moved onto
`place_of`: the code differed (no strip, no `Country` node) but the output did not, on 963 of 963
feed items across 10 Boards.

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
| teamtailor (parsed Jobs, from feeds) | 2 / 0 | 963 / 0 (10) |

Live pages were fetched 2026-09-24 from live ledger rows, five per Board, exactly as each
scraper's detail pass fetches them: 450 job pages and 10 JazzHR listing pages. Every per-scraper
test passes unchanged, which covers the pages written inline in the tests.

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
  writing a tenth copy.
- A fix to how JSON-LD is found reaches every scraper at once. So does a regression: the table
  above is the measurement to repeat before changing the finder.
- Job ids, URLs and every served field are unchanged, so no `DERIVATIONS_VERSION` bump and no
  re-scrape is needed.
