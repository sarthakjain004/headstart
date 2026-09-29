# ADR-0343: A country needs fifteen unplaced Jobs, and a cut country is read whole

**Status:** accepted · **Date:** 2026-09-29 · **Amends:**
[ADR-0273](0273-a-country-filter-matches-every-way-a-location-names-a-country.md) (whose coverage rule
was "named by at least 50 Jobs") and [ADR-0322](0322-a-category-spans-the-index-and-an-agent-filters-by-age-experience-and-employer.md)
(whose Georgia and Berlin guards this extends)

## Context

The 2026-09-29 audit of the served table (v18, 500,167 rows) measured the world gazetteer behind the
`country` filter at 95.53% of rows placed. That figure counts the 8,482 rows with no location at all.
Of the 491,685 rows with a location, 13,863 named no country, and reading them gave these causes
(counts are rows; the cause regexes are approximate, the counts of the two largest are exact):

| Cause | Rows |
|---|---|
| The literal "Remote" or "Hybrid" | 5,387 |
| "N Locations" | 463 |
| Other words naming no place ("Worldwide", "Home based - EMEA", "Multiple Countries") | 1,802 |
| A region only ("EMEA", "Europe", "LATAM") | 552 |
| A SuccessFactors tail cut to two letters ("Cali, Un") | 134 |
| India-shaped strings ("KA, IN", "Crane, IN"): the India filter's rules, not read here | 156 |
| A country or city of one the gazetteer does not list (Suriname, Algeria, Iraq, Nepal, ...) | 1,074 |
| One-off towns, organisation units ("COLLEGE OF DESIGN ENG") and typos | 4,295 |

The first four rows, 8,204 rows, name no place and stay unplaced. Reading the placed rows found
misreads that all came from a code or name shared with another place: "Baku, AZ" was Arizona (31
rows), "Gendalo Gendang, Kali, ID" Idaho (38), "Sala Al Jadida, MA" Massachusetts (21), "Vancouver,
Brit, CA" California (35 with the other cut provinces), "Kingston, Jamaica" Canada (17),
"Al-Bireh/Ramallah" Alabama (4), and "100 Ottawa Ave Sw - GRAND RAPIDS, MI" Canada as well as the US.
Tbilisi and Batumi (157 rows) were already Georgia the country since ADR-0322.

Job pages of SuccessFactors tenants that fill the region and country fields with names carry them
cut by their own microdata to four and two letters: `addressRegion "Mich"`, `addressCountry "Un"`.
1,380 served rows end in a country cut to two letters, on 35 Boards.

## Decision

**A country is listed when at least 15 served Jobs name it and no other country.** ADR-0273's rule was
50 Jobs naming it, chosen because below that the rows are mostly lists of every country a remote job
allows. Counting the rows that name no other country (a list names several) removes that caveat, so the
floor can drop. 33 countries meet it, most rows first: Guyana, Suriname, Iraq, Algeria, Kosovo, Nepal,
Zambia, Uganda, Belize, Cambodia, Iceland, Belarus, Rwanda, Zimbabwe, Uzbekistan, Cayman Islands,
Maldives, Ivory Coast, Libya, Syria, Myanmar, Madagascar, Ethiopia, Cameroon, Bahamas, Tonga, Brunei,
Papua New Guinea, Trinidad and Tobago, Liechtenstein, Jamaica, Palestine and Azerbaijan. The last three
are listed for what they take back from another country, not for their own count (below). Their names,
capitals and the towns the rows carry are sure words; a code is a shared segment only where no US state,
Canadian province or other country already owns it (`iq`, `dz`, `xk`, `rw`, `uz`, `kh`), and there is
no alpha-3 unless the table wrote it. `CODES` goes from 95 to 128, so the MCP `country` enum and the
page's dropdown grow by 33.

**A name a country takes back from another is a sure word of the country that owns it.** Sure terms veto
the shared terms of other countries, which is the mechanism ADR-0273 built:

- Palestine's own names ("ramallah", "al-bireh", "west bank") veto the "AL-" that the hyphen rule reads as
  Alabama. The hyphen-at-either-end rule stays for every shared code: switching it off would lose 88 rows
  ("KR-Osan-04", "GB-Ipswich", "CA-NS-Halifax").
- Azerbaijan has "baku" and no code ("AZ" is Arizona); "Baku, AZ" is Azerbaijan.
- Jamaica has "kingston, jamaica", "kingston 10" and "kingston, saint andrew" as sure words, which
  veto Canada's shared "kingston". The bare "jamaica" is shared, so "Jamaica, NY" stays US.
- Canada's sure segments gain the four-letter provinces SuccessFactors cuts ("brit, ca", "onta, ca",
  "nova, ca", and the other five), so "Abbotsford, Brit, CA" is no longer California.
- "ottawa" moves to Canada's shared words, so a US row naming an Ottawa street, town or hospital is not
  Canada as well (10 rows); "ottawa, ontario" and "ottawa, ca" are sure, so the 22 rows that state the
  province or the code keep Canada. "elgin" and "andover" (US) and "cheltenham", "ipswich" and "bradford"
  (GB) are shared words: each is 92 to 99% one country among the served rows that name it with a country.
- Indonesia gains "kalimantan" and "gendalo gendang", Morocco "sala al jadida", Argentina "caba", South
  Africa "melrose arch", Nigeria the four states one Board names (kaduna, sokoto, kebbi, zamfara).

**About 190 towns, read by hand.** The candidates came from co-occurrence: a segment of a no-country row
that appears, in at least three other rows, with exactly one country. That list was wrong often enough
("Basra, Iraq" read US, "Trinidad and Tobago" read Spain) that nothing was added unread. Ambiguous bare
towns stay unplaced ("Gloucester", "Chelmsford", "Stratford": the US and the UK each carry them), and two
collisions the diff caught were removed before they shipped ("wexford" is Wexford, PA; "watford" is
Watford City, ND). Words from the phrases a remote job is written in: "in the us", "within the us",
"us only", "us-based" (not "the us", which would take "Outside the US").

**A segment may end at " or remote".** "Franklin, TN or Remote" names Tennessee. The end is
`(?:[^a-z0-9]|$)`, not `\b`: Rust's regex crate treats `\b` as Unicode, which takes the engine off its DFA
fast path on every non-ASCII row, and the first version of this change measured 1.6 to 1.9 times slower
for it (US 0.67 to 1.06 s over the 500,167 rows).

**A country cut to two letters is read whole from the page, else from the feed.** The page's "Location:"
line (`<span class="jobGeoLocation">Warren, MI, Michigan, United States</span>`) states the whole value on
the tenants probed that carry it, and `_csb_location` takes it when the assembled microdata ends in a
segment shaped `Xx` (an ISO code is upper case) and the line says more. Where the page has no such line
(Boehringer Ingelheim, Sonae), a cut location counts as placeless, so the run fetches `/sitemal.xml` and
`_with_feed_location` replaces it. On the four hosts probed the feed had an item for every id served cut
(HII 321 of 321, Super Micro 180 of 180, Boehringer 68 of 68, Sonae 21 of 21), and the four values read
per host were whole ("Ridgefield, CT, United States, Connecticut", "Maia, Porto, Portugal, Porto").

## Consequences

On the audited table the gazetteer places 479,967 rows (95.96%, from 477,822 or 95.53%), which is 97.62%
of the rows with a location (from 97.18%). Rows with a location that name no country fall from 13,863 to
11,718. 813 distinct strings change, 2,559 rows: 2,145 gain a country, 247 gain a second one (a list that
names one of the new countries), 155 swap a wrong country for the right one, and 12 lose one (the Ottawa
rows and "Breda, NB, NL", each a wrong Canada). Rows naming two or more countries go from 9,180 to 9,247.
The remaining 11,718 by the same causes: 5,387 + 463 + 1,702 + 530 named no place, 123 are cut tails, 148
India-shaped, 261 name a country or city not listed (Aruba 10, Kwajalein 8), and 3,104 are one-offs.

100 changed strings drawn at random by row weight and read with the text in front of the reader: 97 are
right, 2 are bare US towns the data cannot check ("Andover", "Courtland"; the same words are 95 to 100%
US where they carry a state), and 1 ("Port of Spain, N/A, Trinidad and Tobago") gains the right country
while keeping Spain, which "spain" in "port of spain" already gave it.

This is a query-time change: the SQL clause is compiled from the same regexes the Python rule runs, and
they agree on all 85,828 distinct served locations for all 127 compiled codes (0 differ, on a LanceDB
table of them). No served row is rewritten. Measured over the 500,167-row location column, interleaved,
15 repetitions, median: US 0.66 to 0.73 s, DE 0.67 to 0.84 s; the towns and the new countries each add
under the run-to-run noise of about 0.1 s.

The scraper fix reaches a row when its Board is next scraped, and that row's location is rewritten then
(about 5.9 KB). By the probes, the tenants whose pages carry the whole line (HII, Iris, Super Micro,
Entergy, Chart, Metallus, Singtel, Mohawk) hold 884 of the 1,380 rows, and Boehringer and Sonae hold
90 more through the feed; the rest were not probed. The 328 Atlas Copco rows cannot heal: the Board's
served host fails its TLS hostname check today.

Left as they were, with their counts: "Sydney, NS, CA" (13 rows, Australia and Canada: "sydney" is sure and
there is no way to say "unless followed by NS" without lookahead, which DataFusion's regex lacks); "Port
of Spain" (7); 17 names of a European or other country that are also US towns ("Ghent, KY", "Milan, OH",
"Lake Zurich, IL": 105 rows read as that country too); "Hayward CA" and "Avenel Nj", whose state follows a
space, not a comma (31 rows); and the region and no-place rows above. The Indiana towns "Crane, IN" and
"Carmel, IN" (39 rows) are the India filter's to read: ", IN" is its tail.

## Alternatives

Moving those 17 names to `city_words` (ADR-0322's mechanism for Berlin) was built and measured: it removed
367 rows, 105 of them the US-town shapes and 262 list or multi-place rows that lost a country they name
("Lisbon, PT; Alcobendas, ES" lost Portugal, because a city word yields to any other country's code).
Reverted.

Reading `Cali`, `Loui`, `Un` as prefixes in the gazetteer was rejected: the whole value exists on the page
or in the feed, and 123 rows are all that read nothing after this change; a prefix table would be data
the scraper fix makes dead within a scrape.

Dropping the curated towns would save under 0.1 s a query and lose about 1,100 placed rows; kept.
