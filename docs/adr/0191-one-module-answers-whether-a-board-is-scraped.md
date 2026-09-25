# ADR-0191: One module answers whether a Board is scraped

**Status:** accepted · **Date:** 2026-09-24 · **Builds on:**
[ADR-0012](0012-liveness-ledger.md) (the liveness ledger is the scrape list's source),
[ADR-0023](0023-prune-stale-and-duplicate-index-rows.md) (the case-insensitive dedupe and its
lex-min tie-break), [ADR-0111](0111-duplicate-boards-resolve-the-board-surface.md) (the alias
ledger), [ADR-0155](0155-one-module-for-board-identity-two-failure-policies-by-name.md) (the lenient
`board_identity`)

## Context

"Is Board X scraped?" was answered by `config.load_active_companies`, which filtered on three
keyspaces: `EXCLUDED_BOARDS` on the lowercased `ats:slug`, the alias ledger on the bare lowercased
slug, and `PARKED_BOARDS` on the lowercased `board_identity` after `_dedupe_boards`. The order those
run in decides the answer: the first two are keyed on the slug and must see every spelling of a
Board, and the park is keyed on the identity and must see the dedupe's one survivor. That contract
was written down only in comments spread over five modules. `config` imported `board_identity`
inside functions, because `board_identity` imports `CompanyRef` from `config` at module level.

What the function returned was a list of `CompanyRef`, which carries no identity, so every consumer
derived it again. `scrape_plan` called `board_identity` up to eight times per Board (the
quarantine, the value gate, its filter, `pick_boards`' head, sort and tail, the priority count, the
cost keys and the shard sort) and spelled the result two ways: the quarantine compared
`lower_key(board_identity(c))` and the value gate the raw `board_identity(c)`.

Six places outside the function re-implemented parts of the filter:
`index_plan.workday_site_jobs`, `check_liveness._drop_alias_duplicates`,
`salary_sample._candidates_without_ledger`, `taleo_enterprise_subset_sections.write_aliases`,
`dedupe_boards.main` and `run_scrapers._load_rows`.

## Decision

1. **A module named for the concept, `headstart.boards.scrapable_boards`.** CONTEXT.md already names what
   the function returns: a **Scrapable Board** (`min_jobs=0`), or its **Hiring Board** subset
   (`min_jobs=1`, the default). `load(ledger_dir, *, min_jobs=1)` applies every rule in its fixed
   order, and the module docstring states that order and why it matters. `config` keeps
   `CompanyRef`, the curated seed and the two hand-kept lists; it no longer imports
   `board_identity`, so the cycle is gone. The name is plural, like `board_aliases`, because the
   module holds a list; it does not share the `board_*` prefix because it is not a per-Board ledger
   like `board_cost` or `board_priority`.
2. **Each entry is a `ScrapableBoard`, a frozen subclass of `CompanyRef`** with two fields computed
   on construction: `identity` (`board_identity`, ADR-0155's lenient form, in its scraper's casing)
   and `lowercase_identity`. Neither can be passed in, so they always agree with the `ats` and
   `slug` beside them. A subclass rather than a new field on `CompanyRef`, because `CompanyRef` is
   built in many places that have no reason to resolve a scraper (the curated seed, the shard files
   `scrape_run` reads, discovery scripts); rather than a separate `Board` type, because
   `get_scraper`, `harvest.scrape_all` and the shard writer take a `CompanyRef` and now take a
   `ScrapableBoard` unchanged. A test stub builds one the way it built a `CompanyRef`. Equality is
   still the dataclass's: a `ScrapableBoard` never equals the `CompanyRef` it was built from and
   hashes differently, so the two must not be mixed in one set or comparison. No caller does; the
   shard path reads plain `CompanyRef`s and never meets a `ScrapableBoard`.
3. **Consumers read the stored identity.** `scrape_plan` (quarantine, value gate, slice count, cost
   keys, shard sort), `pick_boards` and its gap quota, `scrape_run`'s monolith path and
   `description_gap_ledger.key_for` take a `ScrapableBoard`. After this change `board_identity` has
   two callers: `ScrapableBoard` and `harvest`'s cost-key map. `index_plan.live_keep_set` keeps its
   own strict `board_key` call: it must drop a Board whose slug will not parse rather than keep the
   fallback key (ADR-0155), and the stored identity cannot say which case it holds.
4. **One predicate for scripts, `is_excluded(ats, slug)`.** It is the only filter step a raw
   candidate list can apply on its own. The alias ledger is already its own interface
   (`alias_ledger.load_for`), and the dedupe and the park need identities.
5. **`EXCLUDED_BOARDS` and `PARKED_BOARDS` stay in `config`**, where CONTEXT.md, README's funnel and
   `tests/test_board_counts.py`'s sentence regexes name them. The module reads them as
   `excluded_and_parked.EXCLUDED_BOARDS`/`excluded_and_parked.PARKED_BOARDS` at call time, so the tests that patch them on
   `config` still do what they say.

## Which re-implementations moved

Each was migrated only where its filter matched the loader's step exactly.

- **Migrated:** `salary_sample._candidates_without_ledger` and
  `taleo_enterprise_subset_sections.write_aliases` both test the lowercased `ats:slug` against
  `EXCLUDED_BOARDS` and now call `is_excluded`. So does `tests/test_board_counts.py`.
- **Not migrated, `taleo_enterprise_subset_sections` beyond that:** it must read the sections the
  alias ledger has buried, because re-reading them is its job (CLAUDE.md, ADR-0186). The loader's
  alias step would hide them.
- **Not migrated, `index_plan.workday_site_jobs`:** it keeps the largest job count across a
  Board's case-variant rows. The dedupe keeps the lex-min row and a `ScrapableBoard` carries no
  count, so the ranking could change.
- **Not migrated, `check_liveness._drop_alias_duplicates`:** the prober must still re-probe
  excluded and parked Boards so their verdicts stay current; only buried aliases are skipped.
- **Not migrated, `dedupe_boards.main`:** it needs every live slug, aliases included, because the
  aliases are what it is looking for.
- **Not migrated, `run_scrapers._load_rows`:** it reads the merged discovery lists, not the
  liveness ledger, and applies no filter today. Adding one would change what the script scrapes.

## Verification

Measured on this branch and on its merge-base (`12d45409`), over the committed
`data/validate/liveness/`: the ordered list of `(ats, slug, name, identity)` is byte-identical for
`min_jobs=0` (153,216 Boards) and `min_jobs=1` (100,795). `scrape_plan` run on both over HF state
pulled 2026-09-24 with the shuffle seeded writes identical shard files and `plan.json`, with the
same quarantine (785 of 910), value gate (27 Boards) and slice (20,000 Boards) log lines.

## Consequences

- Filtering to Scrapable Boards now means calling `scrapable_boards.load`; the order of its rules
  lives in one docstring.
- `pick_boards` takes `ScrapableBoard`s. A caller holding plain `CompanyRef`s wraps each one, which
  resolves its identity once.
- Dated measurement write-ups under `docs/` and earlier ADRs still say `load_active_companies`,
  because they record what was run then. Living docs (CLAUDE.md, CONTEXT.md, README, the skills)
  and the workflows name the new module.
