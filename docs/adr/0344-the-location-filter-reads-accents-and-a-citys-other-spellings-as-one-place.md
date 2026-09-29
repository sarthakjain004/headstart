# ADR-0344: The location filter reads accents and a city's other spellings as one place

**Status:** accepted · **Date:** 2026-09-29 · **Amends:**
[ADR-0149](0149-search-filters-and-index-capabilities-are-two-objects.md)

## Context

The free-text `location` filter compiled to `lower(location) LIKE '%term%'`: the letters typed and nothing
else. The 2026-09-29 audit (finding FL-08) counted what that misses on the served table (v18, 500,167
rows). Both spellings of a place are real rows, and each search reached only its own:

| term | reached | the place's rows |
|---|---|---|
| Bangalore / Bengaluru | 15,035 / 14,370 | 29,224 |
| Gurgaon / Gurugram | 1,415 / 2,251 | 3,645 |
| Munich / München | 1,474 / 549 | 2,000 |
| Zurich / Zürich | 474 / 358 | 777 (55 rows carry both) |
| Krakow / Kraków | 709 / 593 | 1,302 |
| istanbul | 314 | 390 (`lower('İ')` is two characters) |

The audit's "a Zurich search reaches 474 of 832 Zurich rows" added the two spellings; the union is 777.
11,032 rows (2.2%) carry a word spelled with an accented Latin letter, 868 distinct words. Lance SQL
has `lower`, `regexp_like`, `translate` and `replace`; it has no `unaccent` and no `LIKE ... ESCAPE`
other than backslash. No served column and no response field may be added for this.

## Decision

`headstart.search_filters.location_spelling` reads a location term three ways, and the compiler turns
what it reads into one predicate:

1. **Folded.** The term loses its accents, and each letter of a word the table spells with accents
   becomes a regex class of every spelling of that letter (`z[uüú…]rich`), so an accented term finds
   the plain rows and a plain term the accented ones. The classes come from Unicode's Latin blocks
   (`ø ł đ ð ħ ı İ æ œ ß` are spelled out by hand, all seen in the table), so no new accented place needs
   an entry.
2. **As the city's other names.** A whole word of the term that is one spelling of a renamed or
   re-spelled place is also tried as its others, each as a whole word of the row (`GROUPS`, `ONE_WAY`):
   Bengaluru/Bangalore, Gurugram/Gurgaon, Mumbai/Bombay, Chennai/Madras, Kolkata/Calcutta, Pune/Poona,
   Kochi/Cochin, Thiruvananthapuram/Trivandrum, Vadodara/Baroda, Prayagraj/Allahabad, Ho Chi
   Minh/Saigon, Kyiv/Kiev, Prague/Praha, Cologne/Köln, Vienna/Wien, Nuremberg/Nürnberg, and the others
   the table shows in 100 or more rows. Whole-word, so "Madras" for Chennai is not "Madrasa". The
   term's own text stays a plain substring, as it was. A spelling that also names another place is
   asked for one way only: "Vienna" finds "Wien", but "Wien" does not find Vienna, Virginia.
3. **Literally.** `%`, `_`, `\` and every regex metacharacter mean themselves, as `_like` made them.

**A term that needs none of this keeps `lower(location) LIKE '%term%'`.** `pattern()` answers None for a
term with no accent of its own, no renamed place, and no word that can meet an accent the table's
words carry, and the compiler emits the old clause for it, byte for byte. This is the cost decision (see
Options): on a ranked page a `regexp_like` costs more than a `LIKE`, and most terms gain nothing.

"Can meet an accent" is read from the table itself. `JobSearch` reads every `location` holding a
non-ASCII character (12,167 rows, about 350 ms of the 1.1 s boot) as it reads the ATS and currency
lists, and keeps each word spelled with an accented Latin letter (868), with the letters the accent
made in capitals (`zUrich`, `hauptstraSSe`), in `IndexCapabilities.accented_words`. A term word is classed only where it can align
with such a word over a capital: as a substring when the term has no separator on either side of it, as
a suffix or prefix when it has one, and as the whole word between two. So "san" of "san francisco" is
not the "sán" of "Sánchez", and "berlin" is not the "berlin" inside "Überlingen".

## Options considered

- **Fold the data side**, `translate(lower(location), 'üö…', 'uo…') LIKE '%zurich%'`. Correct, needs
  no vocabulary, and handles every accent. Measured warm on the table, a count costs 250 ms against
  33 ms for `LIKE`: 7.5 times, on every location search. Rejected.
- **Expand the term into an OR of `LIKE`s**, the spellings taken from a committed vocabulary of the
  table's accented tokens. Two `LIKE`s cost 57–66 ms a count, and a ranked page 71 against 47: each extra
  predicate over 500,167 rows is about 30 ms there. It also cannot make `Madras` whole-word.
- **A committed vocabulary file** for the gate, refreshed by a script. It works, and was built first
  (861 words, 8 KB, a refresh script), then dropped: it goes stale silently as boards land, and the
  index is already read at boot for exactly this kind of whitelist. The table's own read cannot drift.
- **Classes for every term, no gate.** Simplest, and a count costs the same as `LIKE` (30 ms against
  33). A ranked page, where the engine filters before it ranks, costs 18–39 ms more for every location
  search, plain ones included (CPU of the query: `london` 78 ms as `LIKE`, 100 as `(?i)` regex). Kept
  only for the terms that gain rows.
- **Punctuation-insensitive matching** ("San Francisco CA" for "San Francisco, CA") and **containing
  places** ("New York City" for the state's rows) were not built: the first is not a spelling of a
  place, the second finds different places. Both are follow-ups if wanted.

## Measured

Recall, on the audited table (v18), the production clause against the old one, 122 terms: no term lost a
row (`lost=0` for 122 of 122), every gained row was read through the country gazetteer, and the terms
asked in the spelling with fewer rows gain the most.

| term | before | after | | term | before | after |
|---|---|---|---|---|---|---|
| Bangalore | 15,035 | 29,224 | | Zurich | 474 | 777 |
| Bengaluru | 14,370 | 29,224 | | Krakow | 709 | 1,336 |
| Gurgaon | 1,415 | 3,645 | | istanbul | 314 | 390 |
| Madras | 54 | 6,873 | | NYC | 453 | 7,660 |
| Munich | 1,474 | 2,000 | | Washington DC | 276 | 3,407 |
| Kiev | 21 | 377 | | St. Louis | 751 | 1,042 |

The plain terms `london`, `remote`, `berlin`, `dublin`, `toronto`, `india` and `united states` gained 0
and compile to the old clause. Of the 2,000 most common places (first part of a location), 79% stay
`LIKE` (69% weighted by rows); of the top 100, 74.

**Same place.** The gained rows of each term were classified by the country gazetteer against the
place's country: 122 terms, every gained row a match except where the row names no country. Read by
hand (40 sampled rows each for Bangalore, Zurich, Kraków, Gurgaon, Munich, Madras, NYC, Rome, Vienna, St.
Louis; all 40 the same place, except one Rome row). The false positives found: "Rome" pulls "Roma, QLD,
AU" (1 of 138 gained), and a term that already matched "St. Louis" as a substring keeps matching "Saint
Louis Park, MN" and "Bay St Louis, MS" (an alias only adds the same spelling). "Warszawa" would have
pulled the 100 Warsaw, Indiana rows of 2,219 (4.5%), so Warsaw is one way. "Canton" for Guangzhou is US in
121 of 150 sampled rows and was not added. The table holds no "Madrasa" row; the guard is a unit test,
and every one of the 54 "Madras" rows is the whole word.

**Cost.** Warm on the table, 15 runs interleaved, medians (the machine was loaded, so the columns are
read against each other, not as absolutes). A plain term is the same clause. A ranked page (vector search
with the filter applied first), CPU ms of the query, old to new: Zurich 71 to 90, Bangalore 79 to 122,
Munich 70 to 130, NYC 71 to 130, St. Louis 80 to 147. A count (the facet strip's 46 counts) is 8 ms
faster to 4 ms slower for every term. The clause is 31–38 characters for a plain term and 78–216 for a
changed one; the worst case found (two renamed places, "saint louis fort worth", eight spellings) is
1,297 characters and 71 ms a count against 43 for `LIKE`, and `MAX_VARIANTS` caps it at eight.

**Soundness of the gate.** For 1,596 terms (the 1,200 most common places and 400 of the table's accented
words), the gated clause and an always-classed one counted the same rows on the real table: 0
differences. The unit test does the same for every substring of a fixture of accented rows, and a test
runs the SQL and the Python rule against each other on a Lance table.

## Consequences

- A location search reaches every spelling of a place in the table: 29,224 rows for Bangalore, not
  15,035. The compiled clause is a regex only for a term that gains rows, so most searches cost what they
  did.
- The rows reached by a search changed with the table, not with a list: a new accented place is learned
  at the next boot. A hand-built `IndexCapabilities` (the bench script) has no accented words and reads
  an unaccented term as typed, which is the old behavior; an accent typed or a renamed place still folds.
- The Python rule and the SQL are one regex string, as the country gazetteer's are, so `matches` is a
  reference for any caller that holds a location and a term.
- The facet strip and the MCP `location` argument compile through the same `build_filter`; on the
  real table the facet total equals `count_rows` of the compiled filter for 11 of 11 terms tried.
- `serving/location_counts.py` is unchanged: it lists the places a Board's jobs carry as the employers
  wrote them and reads no location term, so a profile still lists "Bangalore" and "Bengaluru" apart. Merging
  them there would change a response value; it is a follow-up.
- `headstart.search_filters.compiler._like` no longer serves `location`; it serves `company` and the
  keyword filters. No served column, schema or response field changed, and no served row is rewritten.
