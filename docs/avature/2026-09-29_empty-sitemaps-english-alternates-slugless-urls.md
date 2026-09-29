# Avature: empty sitemap bodies, English alternates and slugless URLs (2026-09-29)

What the scraper learned after its first measurement (`2026-09-26_listing-measurement.md`), from
#860, #880 and the code-review follow-up to both. The rules are in ADR-0245's 2026-09-29
amendment and in `src/headstart/scrapers/avature.py`'s module docstring. Every read below went
through the scraper's own HTTP stack at 1 request per 2 s.

## An empty sitemap body

Avature answers a sitemap request with an empty `200` body at random. ea's `careers` sitemap read
645,183 bytes, then 0, then 0. One full listing of ea got an empty body on 173 of about 340 reads.
emiratesjobs's only job portal, `careersmarketplace`, listed 1,470 postings at 10:38 and then
answered 0 bytes on three reads in a row that afternoon.

A utility portal answers an empty body on every read: `CalendarInvitation`, `timeslots`,
`esignature` and the like. So an empty body alone cannot tell a lost listing from a portal with
nothing to list.

**A portal's locale sitemaps list the same postings.** tsmc's `careers` index names four locale
sitemaps (`de_DE`, `ja_JP`, `zh_TW`, `en_US`). Each is 700,726 bytes and lists the same 801 ids. So
one locale that answered covers the portal, and a portal counts as read empty only when its index,
or every child sitemap read from it, came back empty. metlife's `ml` portal read 26 of 64 bodies
empty in one listing. Its other locales answered, so it still listed all 448 postings.

**The portal's `SearchJobs` page settles an empty portal.** The #879 sweep read 1,199 search pages
on Avature Boards that listed nothing:

| what the search page answered | pages |
|---|---:|
| gone: `404` (400 and 410 read the same) | 1,027 |
| a login: the portal's `/Login/`, or the tenant's SSO host (okta, `login.microsoftonline.com`) | 103 |
| the portal's own search page, stating results with no `/JobDetail/` link (rendered client-side) | 34 |
| a `202` | 15 |
| the portal's own search page, linking postings | 11 |
| a page elsewhere: the employer's own careers site or an error page | 5 |
| a transport error | 4 |

Only "gone" and "a login" say that the portal lists nothing. The #880 fix treated every answer as
"lists nothing" except a `200` that linked a posting. Two live reads showed where that fails:

- **emiratesjobs** (215 served rows). `careersmarketplace` read empty, and its `SearchJobs`
  redirected to `www.emiratesgroupcareers.com/search-and-apply/`, which links no posting. So the
  Board read as empty, and two such runs evict every row (ADR-0200). This happened on two runs
  in a row on 2026-09-29.
- **maximus** and **rgp** (no served rows). Their job portals read empty, and their search
  pages state "402 results" and "115 results" but link none.

A search page that returns `406` (Avature's own wall), `429` or a `5xx` has said nothing either.
#702's rule counts those as unread.

**A Board whose other portals still list postings is unread too.** deloitteus's `careers` sitemap
read 0 bytes twice and then 1,304 ids, while `careersDOT` listed 45. On a run where `careers` read
empty, the 45 would have stood for the whole Board. On the probe run below it was `careersDOT`
that read empty, and its search page links postings. The run is now truncated (ADR-0053).

### Live listing check (branch code, no job pages fetched)

Fifteen Boards that serve rows, read once each on 2026-09-29. hilticareers hit the per-IP `406`
wall and woolworths answered `403`, so they are not counted.

| Board | empty public portals | verdict |
|---|---:|---|
| emiratesjobs | 8 | `BoardUnreadable`: `careersmarketplace` hands off to the employer's site (twice) |
| deloitteus | 7 | truncated: `careersDOT` read empty and its search page links postings |
| mantech | 12 | authoritative: 11 answer `404` and `hiringmanager` lands on `/Login/` |
| metlife | 8 | authoritative: all answer `404` |
| tesco | 3 | authoritative: two answer `404` and `storemanager` lands on `/Login/` |
| tsmc, tql, dth, mgl, synopsys, bloomberg, slalom, stjude | 0 | authoritative, and no extra request |

The check asks one search page per empty public portal and stops at the first unread answer. It
cost 39 requests across these 13 Boards, from 0 to 12 per Board. A Board whose sitemaps all
answered costs nothing more. A Board that listed nothing now asks only its empty portals, not
every public portal as #880 did.

## The English alternate (#860)

Each locale sitemap lists every posting under its own locale and names the other locales' URLs as
`xhtml:link` alternates. The scraper kept the first URL it saw for an id, so the locale that
answered first named the posting. ea's `en_US` sitemap read empty, so 124 of its 126 served rows
came from `es_ES` pages. Those pages carry Spanish labels ("Información general Ubicaciones") and
failed the English gate. metlife's `fr_FR` pages did the same.

A `<loc>` that names no locale, or an English one, is kept. Otherwise the posting's first
alternate whose URL names an English locale (`en_US`, `en_GB`) is taken. The locale is read off
the URL path, which each alternate's `hreflang` states too. A non-English `<loc>` stays only when
no English alternate is stated: `manpowergroupco` lists `es-CO` only.

A `<loc>` with no locale is the tenant's default page. On served table v45, 2,308 Avature rows
have such URLs and all pass the English gate. That count only includes rows that already passed
the gate. An alternate, though, is taken only when its URL names an English locale, because an
`x-default` alternate can name the default page. If a posting's alternates listed both `en_US`
and `en_GB`, the first one listed would win. No such posting was seen.

## A URL with no title slug

A title with no Latin letter mints no slug. 28 of tsmc's 801 JobDetail URLs are
`…/careers/JobDetail/389`, and the three read were titled "製程整合工程師 (台南)",
"製程工程師 (台南)" and "儲備模組副工程師 (台南)". The URL pattern required a slug, so these
postings were never listed. metlife has one too. They are listed now, with an empty slug title.
The tech gate therefore skips their job pages, which is also what the tech filter's English
vocabulary would do with their titles. `url_shape` allows the missing slug.

## Job-page text

- **workmyway.** Its `article--details` block holds one `<style>` block and nothing else. The
  posting sits in collapsible `<details class="article article--details …">` sections ("Role
  Highlights", "About the Role"). Once #885 dropped style blocks, every page read as having no
  description, and the store kept the held CSS text (25 of 25 served rows). The page is now read
  from the first surface that has text. That surface is the collapsible sections, which are read
  after the JSON-LD, the details block and the rich-text field. Across 122 live job pages (one per
  Board that serves rows, plus workmyway's 25 and metlife's 15) and the test fixtures, only
  workmyway's 25 descriptions changed. All 25 now have text, and 20 pass the English gate.
- **metlife's call to action.** `og:title` ends in "| Apply Now" (`fr_FR`: "| Postuler"). #885
  used the JSON-LD title where `og:title` wrapped it. But 15 of metlife's 80 served titles have a
  JSON-LD title worded differently ("Head of Platform | Apply Now" against "AVP, Head of Platform")
  or one that does not parse (its title holds an unescaped backslash). The suffix is now dropped
  whatever the JSON-LD says. On the same 122 pages, only those 15 titles changed.
