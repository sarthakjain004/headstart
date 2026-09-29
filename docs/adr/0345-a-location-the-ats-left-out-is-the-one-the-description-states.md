# ADR-0345: A location the ATS left out is the one the description states

**Status:** accepted · **Date:** 2026-09-29

## Context

The 2026-09-29 audit of the served table (v18, 500,167 rows) found 8,482 rows (1.70%) with no
`location`: Zoho 3,514, Avature 1,551, wp_job_openings 803, Keka 733, then iCIMS 378, Workday 345, ADP
241, Jobvite 227, Radancy 207, Teamtailor 173 and Taleo BE 151. It found 15,371 rows whose location
says a token next to itself (`Mumbai, Mumbai, India`, `Singapore, Singapore, Singapore`), 268 that list
the same place twice, and 17 Greenhouse rows carrying the template token
(`BLANK,BLANK,Multiple Locations`).

The repeats are two different things. 11,572 of the 15,371 rows are a city and the region of its name
(`New York, New York, United States`, `Delhi, Delhi, India`, `Berlin, Berlin, Germany`): the source states
a city and a region field, and a filter for `New York, New York` matches 3,335 served rows. The others
are a country said twice at the end of a place (`Singapore, Singapore`, 2,094 rows; `London, United Kingdom,
United Kingdom`, `Aguadilla, PR, PR`). Where a source states a real region of a city-state it spells it
otherwise (`Singapore, Central Singapore`, `Luxembourg, Luxembourg District, Luxembourg`, `Panama City,
Panamá Province, Panama`), so `Hong Kong, Hong Kong`, `Luxembourg, Luxembourg` and `Panama City, Panama,
Panama` are the country name twice, not a region.

Each null bucket was classified by reading its source (60 requests, one a second per host, all
answered 200) and its descriptions:

- **Zoho, 3,514 (3,461 `Remote_Job: true`).** Four tenants' detail records carry `City: null` and no
  other location-like key, as the 2026-09-25 measurement in `scrapers/zoho.py` (3,571 records) found.
  The source states no place. Legitimate.
- **Keka, 733.** The active-jobs API answers `jobLocations: []` for 253 of 253 empty-location jobs on
  three tenants, and no other key on the record names a place. The source states none.
- **Avature, 1,551, ten tenants at 100%.** The job page states the place on a surface the scraper did
  not read. Deloitte US (522) lists it under "Same job available in N locations"; Macquarie (150) and
  Electronic Arts (127) put it in the row Avature classes `field--location(s)`, labelled "Additional
  office locations" or with no label element; MetLife (80), Frequentis (47), Talent Solutions (25) and
  AESC (8) write it in the page `<title>` (`Title - Kuala Lumpur, Malaysia - 19849 - MetLife`);
  TotalEnergies (53) uses `<dt>`/`<dd>` rows. Emirates (215), Delta Global Technology Hub (85), DB
  Group (57), Koch, Gensler, Platinion, Baufest, Discovermgs and van Oord state none (or state it only
  inside the job title). Three more tenants (Lululemon 35, Bravura 4, Colorado 5) were fixed by #946
  after the audit and are not counted here.
- **wp_job_openings, 803.** WebSenor (100) states a `PostalAddress` object in its JobPosting JSON-LD,
  and the scraper read only string addresses. Of 13 other tenants read, 12 carry no `jobLocation` in their JSON-LD and iLevelUP (26 rows) states
  only the country an applicant must live in (`applicantLocationRequirements`), which is not read.
- **Not null, but no place.** 5,386 rows say only `Remote` or `Hybrid` and 25,150 name only a country,
  which a company that states only a country is entitled to. Neither is changed.

The descriptions of 1,544 of the 8,482 null rows carry an explicit `Location: <place>` line in the
first 1,500 characters. `country` is derived from `location` alone, so a place that is not served is
also a country that is not served.

## Decision

**A source that states a place is read (scraper level).** Avature reads the four surfaces above after
its label rows and JSON-LD, in this order: `<dt>`/`<dd>` rows (for the location only, since the same
rows state a "Type of contract" no scraper has read there), the `field--location(s)` row under any
label, the header list, and the page `<title>` when what follows the title is a place the gazetteer
knows (an empty slot, `EA SPORTS NHL` and `Python, SQL` are not). wp_job_openings reads a `PostalAddress`
object through `job_location_text`, as five other scrapers do.

**A description that states a place is read where the ATS stated none (fact level).**
`headstart.jobs.location.from_description` reads the first `Location:` line of the description head
(`Location`, `Locations`, `Job/Work/Office/Primary/Preferred/Posting location`, `Location(s)`) and
returns its place. `doc_prep.stored_facts` calls it when the scrape's location is empty, so the value
enters both `to_meta` (a new Job) and `update_meta.corpus_facts` (a Job already held). It is gated
because a wrong place is worse than none: the value must be nothing but places (each piece four words
or fewer, every word capitalised, none spare: `Cairo Type` is `Cairo` and a label word); a bare name
several countries share (`Melbourne`, `Perth`) is refused unless the value carries a country, a
two-letter code or an Indian place; a bare code (`PAN`, `IT`, `US or`) and an adjective (`Indian`) are
refused; a list that stops at a name the gazetteers do not know is refused, not cut short. `Remote`,
`Hybrid` and a workplace type never become a place, and nothing is invented.

**A stated location is tidied where every Job is built.** `location.tidy`, called from
`Job.__post_init__`, drops `BLANK` and empty tokens (`Mohali,, PB, India`), lists a place once when it
is listed twice, and says a **country** once when it closes a place twice: the last two tokens are equal
and name a country (its name or ISO code in the country gazetteer). It keeps a city and the region of its
name, wherever it sits, and a country shared by two places of a list. What stays is written exactly as it
was stated (separators and spacing are kept: 4,000 served rows have no space after the comma and are not
rewritten for it).

## Alternatives considered

- **The reader in `Job.__post_init__`.** It is where the tidy lives, but a held Job's scrape carries no
  description (ADR-0208, Zoho and others skip the detail), so the reader would see `None` on the
  Jobs that most need it. `update_descriptions` writes the stored text back into the corpus row, and
  `stored_facts` reads that row.
- **A derivation, swept by `DERIVATIONS_VERSION`, like `remote`'s overlay (ADR-0118).** `remote` needs it
  because a raw fact and a JD reading compete and the overlay must not revert between sweeps. Here
  the description is read only where the fact is empty, and `location` is already re-observed every run
  (`FACT_FIELDS`), so a fact fallback reaches a row when its Board is next scraped with no sweep and no
  bump, and `refresh_row` already re-derives `country` when `location` moves.
- **A separate served column for the description's place.** The brief adds no column; the filter and the
  display read `location`, and a second column would leave a job placeless to both.
- **Reading a line without a colon (`Location Hyderabad, Telangana Working Schedule`).** MetLife and
  TotalEnergies flatten label rows so; the value's end cannot be told without the next label. Their
  scraper reads serve them.
- **Dropping a placeholder location (`N/A`, `NA`).** 67 rows, but SuccessFactors writes ISO codes and `NA` is
  Namibia (23 rows). Not touched.
- **Collapsing every neighbouring repeat (the first version of this change).** It rewrote about four times
  as many rows and dropped the region of `New York, New York, United States` (3,171 rows): a filter for `New York, New
  York` would have fallen from 3,335 matches to about 170. The region is a level of its own, so only a
  country said twice is a stutter.
- **Collapsing a repeat that does not touch.** `Bengaluru, Karnataka, India, Pune, Maharashtra, India` and
  `Boston, Massachusetts, USA; Irvine, California, USA` state each country for its own place.
- **Keeping `Hong Kong, Hong Kong`, `Luxembourg, Luxembourg` and `Panama, Panama` as city and region
  pairs.** No row states a region for them under that name (see Context), and the rule would then need a
  list of names instead of the gazetteer's countries. A literal `hong kong, hong kong` filter falls from
  302 matches to 82, `luxembourg, luxembourg` from 122 to 39, `singapore, singapore` from 2,385 to 297;
  `hong kong`, `luxembourg` and `singapore` alone match the same rows as before.

## Consequences

Measured on the audited table, simulating the change over its descriptions and, for the scrapers, by
per-tenant page probes:

- **Null location: 8,482 to 6,898.** 1,584 resolve: 44 already fixed on main by #946 (Lululemon,
  Bravura, Colorado), 1,112 by the scrapers (Avature 1,012 projected from 25 pages on eight tenants, every
  page read a place; WebSenor 100 from 3 of 3 pages), and 428 more by the description reader, whose 572
  rows overlap the scrapers' on 107 (Electronic Arts and the Avature tenants). What stays null: Zoho
  3,403 (remote-only, no place at source), Keka 606 (empty at source), wp_job_openings 586, Avature 492,
  iCIMS 374, Workday 335, ADP 231, Jobvite 205, Radancy 199, Teamtailor 170, Taleo BE 149.
- **Precision of the reader.** 100 random resolved null rows read by hand (seed 33): no wrong place; 11
  incomplete (6 Lululemon rows give the country only, 3 Electronic Arts rows the first of several
  offices, one Jobvite list stops at `Okinawa`, one at `Agra`). Control on the 19,641 rows that already
  have a location: the reader's place agrees with the served one on 19,403 (98.8%), disagrees on 119
  (0.6%), and the served one resolves to no country on 119. The disagreements read as a description naming
  another office or a remote-eligible region (`US or Canada` against `Atlanta, GA`), not as a misread, which
  bounds the wrong-place rate on the null rows, where there is no field to conflict with.
- **`country`.** The reader's places give 316 rows `country = "IN"`; `tidy` moves `country` on none of
  the rows it changes.
- **Tidy.** 4,347 rows of the 491,685 that have a location change (3,885 a country said twice, 267 a place
  listed twice, 178 an empty token, 17 `BLANK`; one becomes no place: `,; ,; ,; ,`). `New York, New York`
  still matches 3,335 rows, `Delhi, Delhi` 439, `Berlin, Berlin` 251, `Dubai, Dubai` 443; 11,572 rows keep a
  city and its same-named region.
- **Rows rewritten once.** 4,347 by `tidy` and 1,540 newly located, 5,887 in all, about 35 MB at 5.9 KB a
  row, each when its Board is next scraped. A location that changes is a raw-field change, so `job_facts`
  records the same rows as `changed` once.
- **With #954 (India towns) and #958 (33 countries) merged**, measured on a scratch merge of the two
  heads: `tidy` changes 4,367 rows (the 33 countries add 20 country repeats) and still moves `country` on
  none of them; the reader resolves 584 null rows (12 more, all read as correct: `Dahej, Gujarat`,
  `Algiers, Algeria`, `Kurla, Mumbai`), 326 of them `country = "IN"`, and null location ends at 6,886; the
  control on the 19,912 located rows reads 19,674 agree (98.8%), 120 disagree, 118 served unresolved.
- **Known incomplete readings**, all true and none wrong: a list cut at a name the gazetteers do not
  know keeps the places before it; a multi-office posting reads its first office; a bare
  ambiguous city with no country is not read (273 country-only rows whose description names a more
  specific place are left alone: the reader runs only where `location` is empty).
- **Not fixed, by design.** Remote-only Zoho postings, Keka's empty `jobLocations`, and Avature tenants
  that state no place are unfixable from the source; `Remote`, `Hybrid` and country-only values keep their
  meaning. 25,150 rows name only a country.
