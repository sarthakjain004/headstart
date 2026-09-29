# ADR-0306: A hidden family is counted and folded into Other, never listed

**Status:** accepted · **Date:** 2026-09-29 · **Extends:**
[ADR-0220](0220-a-trained-title-classifier-decides-a-role-family.md) (the head abstains into
`unclassified-tech`) · **Relates to:**
[ADR-0233](0233-trends-serves-reconciled-line-readings-and-the-page-only-formats.md) (the line reading and
its Other row), [ADR-0305](0305-an-abstained-row-whose-title-names-a-developer-is-software-engineering.md)
(the abstained bucket after the developer-title rule)

## Context

Unclassified tech is the head's abstain bucket: about 23,000 open Jobs (6%) after ADR-0305, of
which, on a blind sample of 250, roughly 10% are non-tech and a third cannot be told from the
title. As a line on the Trends tab it read as an unfinished taxonomy, and it ranked third by
size. The owner asked for it out of the reader's sight, kept in the logs and the implementation.

## Decision

Option 1 of two put to the owner (2026-09-29): **hide the line, keep its rows in every total.**

1. **`"hidden": true` in `config/role_families.json`** marks a family the readers count but never
   list (`role_taxonomy.hidden_families`). It sets `unclassified-tech`. The family list
   fingerprint hashes names only, so the flag is not a counting change, and the classifier, the
   ledger, the reassignment ledger and the run log are untouched: the rows are still decided,
   stored and logged as `unclassified-tech`.
2. **The Space's answer keeps the line, last, as "Other".** In a category split the hidden
   family's series sorts after every listed one whatever its size, wears the label `Other`, and is
   named in the answer's `unlisted_series`. Every total, the rows-add-up check and `openings` still
   include it.
3. **It always folds into the Other row.** The reading gains `charted`, the count of lines drawn
   one by one: `LINES_CHARTED`, or fewer where the answer lists fewer lines. `other` is the lines
   past `charted`, so a hidden line is in Other even for a company with only five categories.
   The page's `checkReading` and `check_reading` read `charted` (default `LINES_CHARTED`, so an
   older reading is unchanged). The 101 golden readings gained one line, `"charted": n`, and a new
   golden covers a hidden line among fewer than eight.
4. **The page counts only what a reader could name.** "N categories" and "Categories tracked"
   leave the hidden line out; Other says "Other (N smaller categories)" for the listed ones it
   folds, and a bare "Other" when it holds only hidden lines. A hidden line never drills.
5. **The agent tools follow.** `category` on `search_jobs` and `read_trends` no longer offers it
   and refuses it by name. `read_trends` and `company_profile` print its line last as "Other", so
   an agent's category lines still add up to the whole.

## Alternatives

- **Leave the rows out of the tab's numbers, like non-tech.** Simpler to build, and every figure
  adds up without a fold, but the Total falls by about 23,000 openings in one step, Search's count
  stops matching the tab, and the "left out as non-tech" wording would name tech Jobs. Rejected by
  the owner's choice of option 1.
- **Fold the family in the writer** (count it as another family, or non-tech). It would lose the
  logged and ledgered series the owner asked to keep.

## Consequences

- The tab's category lines add up to the Total through Other; nothing reads "Unclassified tech".
- A link straight to `?family=unclassified-tech` on Search still answers: the family stays known
  to the Space, only no list offers it.
- Un-hiding is one line in the config. Changing what is hidden moves no count and declares no
  counting change.
