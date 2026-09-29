# ADR-0347: The India country tag reads whole words and the shapes only India writes

**Status:** accepted · **Date:** 2026-09-29 · **Amends:**
[ADR-0086](0086-country-tag-signals-in-the-india-gazetteer.md),
[ADR-0138](0138-a-materialized-country-column-serves-the-india-filter.md)

## Context

`country` is a pure function of `location` (`india_gazetteer.classify`, ADR-0138) and is `"IN"` on 82,768
rows of the audited 500,167-row table (v18, read 2026-09-29). The audit of that table found the rule wrong
in both directions, and it recorded two limits of its own: its list of Indian towns was written from
memory, and it named a bare "IN" as India on 230 rows without deciding what to do with the string.

I rebuilt the evidence from the data. The false positives come from reading the 6,898 distinct strings
the rule tags (one trigger per string, then the strings whose trigger sits mid-word, whose only trigger
is a state or city alias, or that name another country). The misses come from reading the 3,032 distinct
strings that are country-less, name no other country and are not blank, and then the 285 rows where the
world gazetteer gave the string to another country but the posting names India (all of them foreign
locations). Each candidate was checked against the posting, not the string alone.

## Decision

**"india" is a whole word.** It needs a non-letter, or the edge of the string, on each side. This drops
"Xindian District, New Taipei" (14 rows), "Florida; Indian Harbour Beach" (3) and every place whose name
contains it, so `INDIA_EXCLUDE` shrinks to the one neighbourhood that is a whole word, "Little India,
Singapore". The test is on letters, not `\b`, so "IN_India_WFH" (an underscore around it) keeps its row.
It loses no real row: of the 6,898 tagged strings only three fail it, and the third
("... British Indian Ocean Territory") is tagged by its state anyway. It gains two rows whose string names
India beside an Indiana or British Indian Ocean word that used to veto the whole term.

**A guard can sit on a state name.** `EXCLUDE` already vetoes a city alias where a specific other place
carries it; it now also takes a state, so `bihar` no longer claims Hungary's Hajdú-Bihar (17 rows).
The other vetoes, each read against the posting: Delhi, New York (7 rows) and the one Louisiana plant string
"Delhi, LA (Delhi Plant)" (1 row; the veto carries its "(" so "Delhi, Laxmi Nagar" survives, which means a
plain "Delhi, LA" is not vetoed), Inashiki
in Japan against `nashik` (5), Madras, Oregon against `chennai`, Kagithane in Istanbul against `thane`,
Maladzyechna in Belarus against `malad`, and the typo "Cananda" against `anand`. "Little India, Singapore"
(2 rows) is the `INDIA_EXCLUDE` entry.

**The ISO country code is read from the shapes only India writes.** Three, all measured:

- a subdivision code opening the string ("KA, IN", 12 rows), and an Indian PIN after the country
  ("MH, IN, 410208", "Jamnagar, GJ, IN, 361004": 440 rows carry this shape, 423 were already tagged and
  the other 17 are all India). A US ZIP has five digits, so "Whitestown, IN, 46077" is Indiana.
- "Town, Plant, IN": exactly three parts, the middle one three or more letters, the code in capitals
  ("Singahalli, Autoliv Asia - AAS, IN", 26 rows; Cheyyar 14; Chakan 14). 1,425 rows have this shape,
  1,357 already tagged and the other 68 all India, with no Indiana among them, because an Indiana row's
  middle part is a two-letter state ("Indianapolis, IN, IN"). It reads the raw string and capitals only:
  SuccessFactors cuts every part to four letters, so its "In" is India *or* Indonesia ("Others, Bant, In",
  "Jakarta, Othe, In"), and three rows of that family are Indonesian (two of the three postings say so).
- "IND," as a prefix ("IND, Alwaye-South 1"), beside the `IND-`, `IND ` forms, and "Remote, IN" as a whole
  string (23 rows on six employers, 10 postings name India and none Indiana; one employer writes "Remote, US"
  on 43 other rows and another "Remote, CR" on 2, so after "Remote," the code is the country).

**A town is a whole word too.** `TOWNS` is 76 Indian towns and plants read off country-less rows (Mundra
24 rows, Sahnewal 9, Siliguri 8, Dadra 7, Pantnagar 6, Korba 5) and checked against the posting or the
employer's other rows. They match as whole words, unlike the substring city aliases, so a short name
cannot hide inside another place ("Korbach", Germany). A town enters only if it recovers a row no other
rule does, bar two kinds of exception. The canonical spelling of a town that did (`kutch`, `hubli`,
`dombivli`, `pantnagar`, which match no row today). And `chakan`, which the plant-tail rule already tags
("Chakan, Chakan, IN" 6 rows, "Chakan, Chakan_MahTower, IN" 7) but which a tidy of a repeated first part,
as #955 makes, turns into "Chakan, IN", a bare "Town, IN" that no shape reads. The town keeps those 6
rows tagged; the other 14 rows of that shape ("Bengaluru, Bengaluru, IN", "India, India, IN") stay tagged by their
city or the country's name. A name that is also a place, a person or a word elsewhere stays out: `kota` (Kota Kinabalu,
Kota Bharu, Kota Cilegon), `parsa` (Nepal), `shalimar` (Florida), `patan` (Nepal), `mirzapur`
(Bangladesh), `hassan`, `kalina` (a Polish village) and `blore` (an English village). Eleven typos of
cities already held, and the new spelling "Sambhajinagar" of Aurangabad, join those cities' aliases
("gurugarm", "gaziabad", "Bengalore"), since a typo that long cannot collide. Two typos short enough to
collide with a word ("nodia" for Noida, "coachin" for Cochin, the latter inside "coaching") are `TOWNS`
names instead, so the Noida and Delhi NCR filters do not reach them. Two state names join `STATES`
("arunachal pradesh", 2 rows, and the misspelling "kerela", 1 row).

**A bare "IN" stays India.** 230 rows read exactly "IN". 228 come from feeds that write a bare ISO
country code as the whole location (SuccessFactors 175, iCIMS 29, ADP 20, Cornerstone 4), the same feeds
that write "US", "ES" and "DE" that way, and the employers' other rows are India (Yash Technologies 213 of
213 rows tagged, Mahindra 112 of 139). The two Indiana rows are JazzHR (one posting is an
Indianapolis address, the other "within the state of Indiana"). The rule stays as it is, now measured on 230
rows rather than 885.

## What was measured, on the audited table

500,167 rows. Old is main's `classify()` (82,756 rows tagged, 12 fewer than the stored column because
ADR-0322's Pakistan guard is already in the code); new is this change.

- 392 rows move: 339 null to "IN", 53 "IN" to null. Against the stored column, 339 gained and 65 lost.
- Gained by rule: 172 on a `TOWNS` name, 68 on "Town, Plant, IN", 32 on a subdivision code or PIN, 23 on
  "Remote, IN", 37 on a city alias, 3 on a state name, 2 on "IND,", 2 on the whole-word "india".
- Lost: 17 Hajdú-Bihar, 17 "india" inside a longer word, 8 Delhi, 5 Inashiki, 2 Little India, 4 one each.
- Read: the posting behind one row of each of 45 of the 122 gained strings, all India. In 16 the posting
  itself names the town, its state or the country ("Location: Waluj, Chh. Sambhajinagar, Maharashtra";
  "Vapi, Gujrat, India"; a rupee salary); in the other 29 it does not, and the evidence is the employer
  (Adani Group, Mahindra & Mahindra, ACG, Sansera, Sun Pharma, Autoliv India, ACME HR Consulting) running a
  plant or office in a town that has no namesake elsewhere. And one row of each of the 17 lost strings, all
  outside India ("Location: Kennewick, WA"; "located in Tsukuba, Japan"; Molex in Xindian District). One
  candidate I first vetoed came back: "Hyderabad, PK" reads as Pakistan, but its one posting requires
  authorization to work in India, so the `, pk` veto I first wrote is not in the rule.
- SQL and Python agree: `where("india")` and `classify` select the same 7,007 of the 85,828 distinct
  location strings in the table (symmetric difference 0), and each of the 69 city and region clauses
  selects only strings `classify` tags.
- Against the rows a location-and-posting reading can identify as India (the tagged ones I could not
  find fault with, plus the 37 India-plausible rows left untagged below), precision goes from 99.92%
  to 99.99% (70 known wrong rows before, 5 after) and recall from 99.55% to 99.96% (339 recovered, 37
  left). That is a proxy, not the population: it does not count the 8,482 rows with no location or the
  description-side misses, which the country column cannot fix without a description fallback (FL-09's).

Left untagged on purpose: 44 rows the string does not settle. 37 I judge India-plausible (`Kota` 2,
`Parsa` 2, "Anywhere, IN" 3, "GGN-Infinity" 3, "Sun House - Corporate Office" 4, single towns whose
spelling I could not verify); 7 ("Karela Business Park Building", a Greek employer) I do not. Left tagged
though foreign: 3 rows that name an Indian place beside another country ("Noida, California, United
States"), which only the world gazetteer can settle.

## Consequences

`DERIVATIONS_VERSION` is 24. `location` is unchanged on the 392 rows, so only the `update_meta` sweep
reaches them; `_refresh_metadata` rewrites a row whose served value differs, about 5.9 KB a row, so about
2.3 MB once (ADR-0341 measured 0.1 s per 2,048 rows on a copy of the served table). No column and no API field
changes.

`where("india")` grows from 3,419 to 4,350 characters and is compiled from the same pattern text
`classify` runs (`_india_pattern`, `_towns_pattern`, `_subdivision_pattern`, `_PLANT_TAIL_PATTERN`), so the two
engines can differ only on a trailing newline, which no served location carries. The plant-tail rule reads
the raw column, not the lowercased one. A table without the `country` column takes the fallback clause and
gets the same verdicts.

`country_gazetteer.INDIA.words` reads `CITIES` and `STATES` directly, so the new city aliases reach the
world gazetteer's India guard and `TOWNS` and the state guard do not. That file is not touched here.

`scripts/eval/location_filter_audit.py` models `where("india")` clause by clause and stops when the model
disagrees with the SQL. It already omitted the whole-string `IN` (ADR-0086's amendment) and so did not
reproduce the clause before this change, and its docstring still calls the country term a substring; it
is left as it was. `scripts/eval/verify_filters.py`'s `_india_ok` pools only "india", the city aliases and
the state names, so on the live Space it reads a row tagged through a town, a shape or the bare "IN" as a
violation (bare "IN" rows already did); it too is left, for the combined review.

The plant-tail rule has no Indiana veto: it is safe on the 1,425 rows that carry the shape because an Indiana
row's middle part is a state, but "Muncie, Delaware, IN" (a county in the middle) would tag India if a
tenant wrote it. The measurement is the guard, not a list, and a tenant that writes Indiana that way is the
signal to add a veto.

## Alternatives

**A blanket ", IN" tail with an Indiana veto.** The obvious rule. Of 314 untagged rows with an IN segment
and no US word, the bare "Town, IN" shape is Indiana about fifteen times for every India row (JazzHR,
Breezy, Greenhouse, Rippling, Lever against SuccessFactors, Teamtailor). A veto list of Indiana towns
written from general knowledge, before looking at which appear, leaks 34 of the 167 Indiana rows (20%:
Muscatatuck, Crane, Butlerville, Edinburgh). A list built from the data would score
perfectly and say nothing about the next tenant. So the shapes above take the rows that are India by
structure, and a bare "Town, IN" is India only when the town is a `TOWNS` name (Kanchipuram, Shillong,
Mehsana, Pithampur, Lonand).

**Word-boundary matching for every city alias.** It would fix the mid-word hits (Inashiki, Kagithane,
Cananda, Maladzyechna) in one rule, but it also changes what the city filters match ("hyderaba" is a
deliberate prefix of "Hyderabad" and its typos), on data that shows only four mid-word victims. The four
get a veto each.

**A "Hyderabad, PK" veto, and dropping the bare "IN" rule.** Both were tried against the postings and lose
real rows (above).

**A description fallback.** Not here: 689 of the audit's candidates have no location at all, and reading
descriptions for them is a separate change.
