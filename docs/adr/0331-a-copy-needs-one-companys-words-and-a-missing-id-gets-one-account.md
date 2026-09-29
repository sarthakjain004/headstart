# ADR-0331: A copy needs one company's words, and a missing id gets one account of why

**Status:** accepted · **Date:** 2026-09-29 · **Amends:**
[ADR-0323](0323-an-agent-sees-one-posting-once-under-a-company-name.md) (copies on a page, the
directory name, `get_job`'s missing ids, a profile's places) · **Relates to:**
[ADR-0023](0023-prune-stale-and-duplicate-index-rows.md),
[ADR-0049](0049-match-boards-by-prefix-not-by-parsing.md),
[ADR-0083](0083-evict-only-on-a-second-consecutive-absence.md),
[ADR-0250](0250-a-board-silent-for-two-years-is-dormant-and-leaves-the-tech-subset.md)

## Context

The code review of #897, which shipped ADR-0323, found four bugs and a loose rule:

- **Loose copy rule.** Two company names counted as one when one name's words began the other's,
  and only the first city was compared. So "GE" grouped with "GE HealthCare", and "Meta" with
  "Meta Financial Group". Over the served table (v475) that rule grouped 3,285 row pairs across
  150 spelling pairs. One was wrong: a GE Vernova row served as "GE". One was unclear: "Flow" and
  "Flow Traders".
- **Unnamed rows on one Board stopped grouping.** Rows naming no company never grouped, so an
  Oracle pod's per-country copies no longer did. ADR-0274's rule had grouped them, and ADR-0323
  said that rule stays.
- **A failed lookup dropped names.** When the directory lookup failed, every unnamed row read "no
  company name", including hosts that were at least informative. So did a Board past the tenth,
  which was never asked. ADR-0323 had said the served names stay.
- **Held Boards read as never held.** "Not a HeadStart id" was decided by the index serving no
  job on the Board now. A held Board whose last posting had closed, or which had gone Dormant,
  was reported as never held.
- **The missing-id account was incomplete.** It read "has closed, or was never an id" and was
  written four times. A row also leaves at once, with no grace period, in three more ways:
  - `index prune` drops a duplicate of another row, which stays served under its own id, and a
    row whose Board is no longer read (ADR-0023);
  - a Dormant Board's rows go (ADR-0250);
  - the tech filter can stop counting a row as tech.

It also found smaller faults:

- `get_job`'s shared budget assumed links of at most 300 characters. Links are never cut, so five
  full descriptions with 2,000-character links reached 35,110 characters against the tool's
  30,000.
- The "first N shown" count included the answer's own ellipsis.
- A profile still printed "remote: 0".
- "Dublin" and "Dublin, Ireland" were still two places under Ireland.
- `space_mcp/company_names.py` was a near-homograph of the `company_names` cache and of
  `boards/company_name.py`.

## Decision

**Two spellings are one company only when they are the same words once legal forms and three
generic words drop:** "Group", "Technologies" and "Energy" (`space_mcp.posting_copies`). They must
also share the title stem, the first city, and the countries that the `country` filter's gazetteer
reads in the whole location. A list with more generic words was tried: "Systems", "Solutions",
"Services", "Global" and "International" also joined "Acme Systems" to "Acme Solutions". Only the
three words that measured copies needed were kept.

Rows naming no company are copies only on one Board, which is ADR-0274's rule restored for
unnamed pods.

Measured on 2026-09-29, the tighter rule groups 1,141 row pairs across 35 spelling pairs over the
whole table. I read every spelling pair, and all 35 are one employer ("L3Harris" and "L3Harris
Technologies", "Rakuten" and "Rakuten Group", "EVERSOURCE" and "Eversource Energy"). On 16 live
pages of 40 rows it added the same two true pairs as the prefix rule and removed none of the
exact-name rule's 78.

The module's move and rename are PR #909's, so this change leaves `posting_copies` where it is.

**A company named only by its Board keeps its served name unless the directory answered**
(`space_mcp.shown_company`, renamed from `company_names`). When the directory holds the Board, its
name is shown. When the directory was asked and names none, the row reads "no company name". When
the Space could not be asked about a Board, or the Board is past the tenth, the served name stays.

**One account of a missing id, and "not a HeadStart id" only when HeadStart holds no Board it
names.**

- `serving/job_absence.WHY_NOT_SERVED` is the one sentence both `/search?like=` and `get_job`
  give: most often it has closed; it is also removed when it repeats another listing, when its
  Board went dormant or is no longer read, or when the tech filter no longer counts it as tech; or
  it was never an id. It is a constant with no imports, so the MCP server can import it.
- `get_job` calls an id "not a HeadStart id" in two cases:
  - it is not shaped `ats:board:posting`;
  - no Board it names is held.
- A Board is held when the Company directory holds it, or failing that, when the index serves a
  job on it. An unnamed Oracle pod has no directory entry.
- A native id can hold a colon ("REQ: 228", ADR-0049), so up to three `ats:slug…` prefixes are
  tried, longest first. The id is called none only when every prefix is unheld.

**A description's share leaves room for its link, and counts only its own characters.** Each
character a link runs past 300 comes out of the descriptions' budget. The count of characters
shown leaves out the ellipsis (`scraped_text.CUT_MARK`). The share and its "read more" advice are
one type, `_DescriptionShare`.

**A profile merges a place with the others that begin with its first part, within a country.**
The first part is the text before its first comma, after its first `;` or `|` place. They merge
when that first part names no other country. The merged place is spelled as its commonest place
writes it. So "Dublin" 15 and "Dublin, Ireland" 4 are "Dublin" 19, while "London, Dublin" stays
whole under Ireland. This changes what `/companies/locations` serves, so the agent contract goes to
8. A one-count line whose count is 0 ("remote: 0") is left out, as the multi-count lines already
were. The internship band stays in the level line: the critique asked for the Trends level bands,
and Trends has that band.

## Alternatives

- **Keep the prefix rule and add the country check alone.** The gazetteer reads "Berlin, CT" as
  Germany, so on bare state abbreviations the country check cannot separate the two Berlins. The
  company rule has to do the separating.
- **Decide "held" from the directory alone.** The directory leaves out Boards it cannot name
  (`oracle:egud.fa.us2.oraclecloud.com`), which the index does serve.
- **Clip long links.** A clipped link is a broken one.

## Consequences

- "Booz Allen" and "Booz Allen Hamilton", and 114 other spelling pairs (2,144 row pairs in all,
  the wrong GE pair among them), list separately again.
- A generic word still joins a namesake: "Siemens" and "Siemens Energy", or "Hitachi" and "Hitachi
  Energy", group if they post one title in one city and the same countries.
- The Berlin, CT gazetteer bug belongs to ADR-0273's owner. It is noted here because the
  country check leans on it.
- A merged place can be a region or "Remote" when that is what a place names first ("Remote,
  Ireland"). The answer calls them places, not cities.
- `get_job` asks `/companies/lookup` about up to three prefixes per missing id, and `/facets` about
  each prefix the directory lacks.
- The scan that `level_counts` shares still lives in `location_counts` (`scoped_rows`). It moves
  only if a third reader appears.
