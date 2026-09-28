# ADR-0263: The search bar can match words in the job title

**Status:** accepted · **Date:** 2026-09-28 · **Relates to:**
[ADR-0104](0104-a-keyword-filter-with-a-scope-map-and-a-stored-description-column.md) (the Keyword
filter and its substring rule), [ADR-0084](0084-facet-counts-are-filter-shaped-not-query-shaped.md)
(a count is a where-clause), [ADR-0173](0173-rebuild-the-search-indexes-with-the-table.md) (the
indexes, NGRAM FTS measured), [ADR-0168](0168-delete-the-orphaned-blobs-dont-ask-for-them-to-be-collected.md) (HF
storage) · adds `SearchFilters.title_words`; no schema, index or stored-data change

## Context

The search bar always searched by meaning: the words were embedded and every job ranked by how
close it was to them. The owner asked for a way to search the job title by plain words from the
bar itself, keeping the rail's Keyword filter (issue #756, item 2). CLAUDE.md keeps the query free
of an LLM parser, so the choice must be explicit, made by the user.

## Options

**A. A two-way switch under the bar: "By meaning" / "Words in the job title".** In title mode the
bar's words become a title filter (every word, as a substring, ADR-0104's rule) and the same words
rank what is left by meaning. No new index.

**B. Quoted text is an exact title match** (`"rust" backend`). No new control, but nothing on the
page says the syntax exists, and a quote typed for another reason changes the results.

**C. Real hybrid ranking: an FTS (BM25) index on `title` fused with the vector ranking (RRF).**
ADR-0173 measured an NGRAM FTS index at 29 MB, fast for selective terms, but it answers with a
BM25-ranked top k, not the whole set of matching titles, so the count beside the list could not
be a where-clause (ADR-0084), and every run's index rebuild and upload would carry it (HF storage
is the binding cost). Two searches and a fusion per query on the Space's 2 vCPUs, and a ranking
nobody can explain in one line.

## Decision

**A.** The switch is one row of native radios under the bar, beside the words it changes, so it
takes one click, walks with the arrow keys, and is in view whenever the bar is. Switching re-runs
what is typed. The mode lives in the URL (`#search?q=rust&match=title`), so a reload or a shared
link searches the same way, and a Saved Set keeps it as `title_words` among its filters. The
filter is also on the Subscription allowlist, so an emailed set's digest lists titles holding
every word, as the set does.

Title words are their own `SearchFilters` field, not the rail's `kw`: the two can be set together
(title words from the bar, a description keyword from the rail) and each keeps its own control.
They compile through the same `_keyword_clauses` as a title-scoped keyword. They are never named
as the Blocking filter (the rail cannot clear the bar); the empty state names them itself: "No job
title has every word of “zzqx quantum”. Try fewer words, or match by meaning instead."

The page sends both `q` and `title_words`, so a title match is still ordered by meaning. That was
measured before choosing it, because an ANN search with a narrow prefilter can lose rows: on the
488,427-row table pulled 2026-09-28, an IVF-SQ search at the Space's operating point (80 probes,
refine 2) prefiltered by the title clause returned every match for all 15 queries tried, where
there were fewer than 2,000 (rust 722 of 722, staff frontend 158 of 158, embedded firmware 316 of
316), and a full 2,000 where there were more.

## Measurements (2026-09-28, this Mac, fresh HF table, real encoder, real app over HTTP)

Median of 5 warm requests over 13 queries; `/facets` timed on its first, uncached call.

| Path | Before | After |
| --- | --- | --- |
| `/search` by meaning | 31 ms | 32 ms |
| `/search` title words (filter + rank) | — | 65 ms |
| `/facets` with title words (≈46 counts) | — | 173 ms |

Meaning results were identical before and after for all 13 queries. Every title-mode row held
every word (0 of 240), and each `/facets` total equals the title-match count. The Space's CPU ran
Hot's ranking 3.6x slower than this Mac (137 s against 37.6 s, measured 2026-09-28 for
issue #755), so expect about 240 ms for a
title search there; `/facets` is fetched beside `/search` and paints after the rows.

## Consequences

- Substring, not whole word, exactly as the rail's keyword: "ios" also finds "Studios", "java"
  also finds "JavaScript". The switch's note says so. "c++" works, which a word-boundary regex
  could not (ADR-0104).
- The rail's "Must contain … Look in: Title" and the bar's title mode overlap. Both stay: the
  owner asked for the rail filter to be kept, and they combine.
- A "Try" example and Home's search box always run by meaning, since they describe roles.
- `scripts/eval/verify_filters.py` checks `title_words` alone and combined with Remote.

## Options not taken

B and C, above. C stays the path if title search should ever rank by term weight rather than by
meaning; it needs an answer for the count first.
