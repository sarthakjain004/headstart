# ADR-0302: An Oracle Board whose title names no one is named by its site's SEO name

**Status:** accepted · **Date:** 2026-09-29 · **Extends:**
[ADR-0217](0217-a-board-is-named-by-what-its-postings-agree-on.md) (Oracle's source was the
Candidate Experience root title alone) · **Relates to:**
[ADR-0212](0212-a-board-is-named-by-a-curated-stated-or-humanised-name-never-its-slug.md)
(the naming policy: curated, then stated, then humanised; Oracle has no humanised fallback)

## Context

An Oracle tenant is a code (`edel.fa.us2`), so ADR-0212 gives it no humanised fallback. A Board
whose default site title names no one ("Candidate Experience site", "Global", "Home Page") serves
an empty company. On served table v320 (2026-09-29) that was 62 Oracle Boards, 2,507 rows. Issue
#703 asked whether a structural source names them, rather than one curated row each.

The Candidate Experience app reads two public endpoints that state names:

- `hcmRestApi/CandidateExperience/en/siteSettings/{site}`: the default site's settings, with
  `seoConfiguration.name` and a talent-community box.
- `hcmRestApi/resources/latest/recruitingCESites`: every site of the host, each with its
  `SiteName` and `SeoOrganizationName`.

## Measurements

Each source was read live on 2026-09-29 for the 61 empty-company Boards that still answer (the
62nd serves "Subscription Suspension Outage"). A candidate counts as correct when it names the
employer the Board's own postings state (5 detail payloads read per Board).

| Source | Boards it names | Correct |
| --- | ---: | ---: |
| `siteSettings` `seoConfiguration.name`, where it differs from `siteName` | 3 | 3 |
| `recruitingCESites` `SeoOrganizationName`, any site | 5 | 3 |
| `siteSettings` talent-community title ("Join the {X} Talent Community") | 17 | 10 |
| `recruitingCESites` names of the other sites | 23 | 2 |

- **The SEO name repeats the site name unless the tenant set one.** The template fills it with
  the site's own name, so 710 of the 751 named Oracle Boards read the same day carry the title
  again. The other 41 all name their employer through the title guards below, as do the 3
  empty-company Boards (onsemi, Legrand Group, Patterson-UTI). That is 44 of 44.

  > **Corrected 2026-09-29 (code review of #878).** A census of all 1,753 Oracle Scrapable
  > Boards, fetched through the scraper's own request, found 73 whose SEO name differs from the
  > site name. The guards passed 72 of them, and two were not employers: "SEO Optimization"
  > (`iaiigs`, a placeholder) and "Carreiras Magazine Luiza" (`iaaywd`, "Careers" in Portuguese).
  > Neither Board reached the SEO name, because each one's title names it first. `_LEFTOVER` now
  > refuses both, along with the page labels in other languages that the same census found
  > served whole from titles: "Sitio de experiencia de candidatos", "Portal de Empleo", "Sito
  > Carriere BPER", "Werken bij Profource". That change renames 26 Boards, none with a served row
  > on table v45. 22 lose a label they were named by. Four move to an SEO name that their own
  > postings state: Profource, Coocique R.L, Tajin and Andreani.
- **Tenants typed page labels around the SEO name.** "The Kroger Co. Careers", "Macy's Jobs" and
  "St. Olaf College | Careers" would be served as they stand through `from_field`. The Oracle
  title guards (`from_title`, `_PAGE_TITLE`, `_LEFTOVER`) turn them into "The Kroger Co.",
  "Macy's" and "St. Olaf College".
- **Other sites name subsidiaries, communities and test copies.** `hcwx` (RPM International)
  lists 22 brand sites; one carries the SEO name "TCI Powder Coatings". `eexs` lists 95 senior-living
  communities, and `eiej` spells its own name "REDCON Constructionr". Other sites also carry
  names like "sample for testing", "Duplicate" and "Staff and Faculty".
- **The talent-community box needs a pattern, and the pattern reads prose.** It gives "Ascendion
  Mexico", "SWG", "AGS", "Croydon", "Enroll into the" and "Not finding a job, join our".

## Decision

1. `OracleScraper.company_from_page` reads the root title first, as before, because it is the
   brand ("Nokia" against "Nokia Corporation", "Stanford" against "Stanford University").
2. Only when the title names no one, it reads the default site's `siteSettings`. The site number
   comes from the root page's `data-sitenumber`. It takes `seoConfiguration.name` when it differs
   from `siteName`, and reads it through the same guards as the title.
3. The other three sources are not read. Their correct names are in the curated map instead
   (`config/company_names.csv`, 42 Oracle rows added with this change).

## Consequences

- One more GET per Board, only on Boards whose title names no one: about 1 in 13 of the Oracle
  Boards serving on 2026-09-29.
- It names few Boards today: Patterson-UTI is new, and onsemi and Legrand were already curated.
  The value is in Boards landed later, which get a name without a curated row.
- A name the SEO field states is stated during the fetch (ADR-0217 §3), so `settled` keeps it,
  and a curated name still overrides it.
- Three Boards stay empty. `hdrc.fa.ca3` serves several employers under one site (OSCO,
  Acadia Broadcasting and others behind a "hiringhub" logo). `edzt.fa.em4`'s subscription is
  suspended. `hdbc.fa.em2` states "FirstBank" only in its logo, and `jobvite:firstbank`, a
  different Board, is served under that name, so the directory would merge them.
