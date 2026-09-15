# ADR-0155: One module for Board identity, two failure policies by name

**Status:** accepted · **Date:** 2026-09-15 · **Builds on:**
[ADR-0023](0023-prune-stale-and-duplicate-index-rows.md) (the `board_key()` seam),
[ADR-0049](0049-match-boards-by-prefix-not-by-parsing.md) (`board_of` is a guess, prefix-match
where a real keep-set exists), [ADR-0059](0059-two-board-keyspaces.md) /
[ADR-0096](0096-one-key-for-both-board-ledgers.md) (one key, `board_identity`, for both ledgers)

## Context

"Which Board does this belong to" had five independent implementations, none of them wrong on its
own, all of them the same nine lines rewritten:

1. `config.board_identity()` — `SCRAPERS[ats](slug).board_key()`, falls back to `f"{ats}:{slug}"`
   on any exception, with ~40 lines of bounded-logging machinery around the fallback.
2. `ingest.board_failures.board_key_of()` — the same call from a raw `"{ats}:{slug}"` string,
   returns `None` on failure. Its own docstring already said it was "the same conversion... that
   `scrape_join.write_unauthoritative_boards` applies."
3. `ingest.scrape_join.write_unauthoritative_boards` — that conversion inlined a second time,
   drops-and-collects-for-a-warning on failure.
4. `ingest.index_plan.live_keep_set` — the same call again, on a `CompanyRef`, drops the Board
   from the keep-set (no fallback key) and warns via a bounded logger.
5. `harvest.py` — `{f"{c.ats}:{c.slug}": board_identity(c) for c in companies}`, a dict built by
   calling implementation #1 again.

Alongside it, `corpus.board_of(job_id)` answers a genuinely different question — guessing a Board
by splitting a Job id on its last colon, documented since ADR-0049 as sometimes wrong (a Workday
native id can itself contain a colon) — and the ATS prefix of a key (`x.split(":", 1)[0]`) and its
case-fold (`x.lower()`) were each hand-rolled at a dozen-plus call sites, inconsistently: one
`.lower()` site in `scrape_plan.py` compared two fresh calls to the same function (never needed
folding at all) while a neighbouring one three lines up compared a persisted ledger's casing
against a fresh one (needed it, per ADR-0049).

## Decision

**One module, `headstart.board_identity`**, in `headstart` proper (not `ingest/`) because the
curated-feed path (`harvest.py`) needs it and that path may not import from `ingest`
(ADR-0028's layering). It owns both directions:

- **Construct** — `CompanyRef` → canonical key, via `board_key()` (ADR-0023, unchanged).
- **Parse** — Job id → guessed Board, via `board_of` (ADR-0049's caveat, unchanged, and kept as
  a named function distinct from the exact prefix-match `index_plan.resolve_board` does against a
  real keep-set — ADR-0049 already rejected folding those into one function with a mode).

Plus `ats_of` (the ATS prefix of any `{ats}:...`-shaped key) and `lower_key` (the case-fold every
Board-key comparison uses — plain `str.lower()`, not `str.casefold()`, because every site this
replaces already used `.lower()` and the two differ on some Unicode input).

**The failure-policy question, and why it stays two policies, not one.** The five call sites split
cleanly into two populations by what a raise *means* to them, and collapsing either direction
changes real behaviour:

- Callers that need a name for **every** Board, unconditionally — dedup (`config._dedupe_boards`),
  the parked-Board check, the cost/priority keys, `harvest`'s cost-key map. Dropping a Board here
  silently shrinks the scrape list or loses its cost history. These keep the **lenient** form,
  `board_identity()`: never raises, falls back to the plain `ats:slug`, logs the fallback once per
  distinct Board (bounded — ADR-0039's annotation budget).
- Callers pairing against a **real**, `board_key`-keyed set — `index_plan.live_keep_set`'s
  prefix-matched keep-set, and the raw-string form `board_key_of` that `board_failures` and
  `scrape_join` both need. Here a synthetic fallback key is not merely unhelpful, it is actively
  wrong: nothing a real scrape ever produces carries the `f"{ats}:{slug}"` spelling once a scraper
  overrides `board_key()` (Workday, Personio), so keeping such an entry would silently reintroduce
  exactly the two-keyspace bug ADR-0059/ADR-0096 spent two ADRs removing. These keep the **strict**
  form — `board_key()` (raises, for a `CompanyRef`) and `board_key_of()` (returns `None`, for a raw
  string a shard report carries, which has no `CompanyRef` to hand back).

A single unified policy was evaluated and rejected both ways: lenient-everywhere reintroduces the
wrong-keyspace bug at the keep-set and the failure ledgers; strict-everywhere means one malformed
liveness-ledger slug crashes `scrape_plan`/`harvest` outright, or silently drops a Board from
dedup consideration that the old code named and kept. **Two named forms, chosen per call site,
changes no observable behaviour anywhere** — it is a pure consolidation, which is why it is the
right default over forcing a single policy that would have to change one.

## Consequences

- Five duplicate conversions become one call each; `scrape_join.write_unauthoritative_boards`
  loses ~15 lines of inlined partition-and-catch logic to a two-line call.
- `board_of` moves out of `corpus.py` (which never really described it — that module's docstring
  is about reading a job corpus, not Board identity) into `board_identity.py`, alongside the
  direction it complements. Every caller's import line moves with it; `corpus.py` keeps only
  `iter_jobs`.
- `config.py` keeps `_drop_parked`/`_dedupe_boards` but no longer defines `board_identity` itself;
  both do a local (lazy) import of `headstart.board_identity`, mirroring the lazy
  `SCRAPERS`/`get_scraper` imports already in that file, so there is no import cycle between the
  two modules (`board_identity.py` imports `CompanyRef` from `config` at module level; `config.py`
  never imports `board_identity` at its own module level).
- `scrapers/base.py`'s `BaseScraper.board_key()` is untouched — this module wraps it, it does not
  replace it.
- One naming near-miss avoided deliberately: the case-fold helper is `lower_key`, not `canon`,
  even though "canon" is the term this codebase already uses for a lowercased Board key
  (`index_plan.boards_by_canon`, a local variable in `config._dedupe_boards`). Naming the function
  `canon` would have shadowed that exact local variable in both `config._dedupe_boards` and
  `index_plan.plan_prune` — `canon = canon(key)` raises `UnboundLocalError` in Python, since an
  assignment anywhere in a function makes that name local for the whole function body. `lower_key`
  reads a little more plainly and collides with nothing.
- One pre-existing non-bug, checked and left alone: `scrape_plan.py` compares
  `board_identity(c) not in gated` without lowercasing, three lines below a quarantine check that
  does lowercase. Not a bug — `gated`'s keys and the comparison are both built from the *same*
  fresh `board_identity(c)` call on the *same* `CompanyRef` in the same function, so they always
  agree in casing by construction; the neighbouring quarantine check lowercases because it compares
  against a *persisted* ledger's casing, which can disagree. Left unchanged.

## Alternatives considered

- **One function, one policy (always fall back).** Rejected: would have added a synthetic,
  never-matching key to `index_plan.live_keep_set`'s keep-set and to the failure/unauthoritative
  ledgers, reopening ADR-0059/ADR-0096.
- **One function, one policy (always raise/return `None`).** Rejected: `config._dedupe_boards`,
  `_drop_parked`, `board_priority`, `harvest` all need a name for every Board to keep dedup,
  parking and cost-keying working across a slug the scraper can't parse; today's fallback-and-log
  behaviour is deliberate (see `board_identity`'s own docstring) and callers rely on it.
- **A single function taking a `strict: bool` flag.** Rejected for the same reason ADR-0049
  rejected folding `board_of` and prefix-matching into one function with a mode: the two answers
  are used by disjoint call sites for disjoint reasons, and a boolean parameter hides which one a
  given call site chose instead of naming it.
