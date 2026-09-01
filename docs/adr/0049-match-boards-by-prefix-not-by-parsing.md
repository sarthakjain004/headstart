# ADR-0049: Match a Job id to its Board by prefix, not by parsing

**Status:** accepted · **Date:** 2026-08-13 · **Amends:** ADR-0023, ADR-0014

> **Amended 2026-08-28 by [ADR-0096](0096-one-key-for-both-board-ledgers.md).** The *pairing* this ADR introduced is retired: both ledgers now key on
> `board_identity`, so there is no second keyspace to pair against. The lesson stands — reading
> one ledger with the other's key scored 4,611 Boards 0.0 — and is why the change moved the
> writer rather than only the reader.

## Context

The prune sweep evicts the same rows every run and sync puts them straight back. The logs prove the
loop rather than suggesting it: the same four `workday:dmainc/DMA` rows and the same three
`workday:otis/REC_Ext_Gateway` rows are pruned in run 31617329226, again in 31645669556, and again
in 31653231046.

A Job id is `{ats}:{slug}:{native}`, and `corpus.board_of` recovers the Board by splitting off the
last colon-separated segment. Its docstring stated the assumption and asserted it held: *"this is
exact as long as the ATS native id carries no colon, which holds."* It does not. Real ids in the
served table:

- `workday:dmainc/DMA:REQ: 228` — the native id is `REQ: 228`, a colon and a space
- `workday:otis/REC_Ext_Gateway:OT221: GD - NEW YORK, NY One Penn Plaza, New York, NY, 10119`
- `workday:campaignmonitor/marigold:https://campaignmonitor.wd5.myworkdayjobs.com/marigold/job/…`

Both halves of the key can contain `:` — the Workday tenant is itself `co/site` — so the last colon
is not a delimiter. For these rows `board_of` returns `workday:dmainc/DMA:REQ`, a Board that does
not exist.

That is only fatal where the result meets a *real* Board key. `plan_prune` compares it against
`keep`, which is built from each scraper's `board_key()` and is correct, so the row is classified
off-Board and evicted; the next sync re-adds it, because it is in fact on a live Board. `index
sync`, **as it stood before this ADR**, was unaffected: it ran both the fresh id and the indexed id
through the same function, so the phantom Board was produced identically on both sides and the
scope check still paired them. A consistent error that cancels, next to an inconsistent one that
loops. That symmetry is exactly what fixing prune alone would have broken — see the Decision.

## Decision

**Both planners** stop parsing ids and resolve them against `keep` by **prefix**, through one
shared `resolve_board`: the longest Board in the keep-set that prefixes an id is the Board that
owns it, falling back to `board_of` when none matches (an unlisted or disabled Board — and an
empty ledger, which reproduces the prior rule exactly).

Fixing prune alone would have been worse than the bug, for one of the three id shapes. Prune used to
evict closed colon-native rows *by accident*, as off-Board; teaching it to match by prefix correctly
stops that, and whether sync can take over depends on where the last colon falls:

| id | phantom `board_of` returns | shared by |
|---|---|---|
| `…/DMA:REQ: 228` | `…/DMA:REQ` | every `REQ: N` on the Board |
| `…/marigold:https://…/job/R2454` | `…/marigold:https` | every URL-native row on the Board |
| `…/REC_Ext_Gateway:OT221: GD - NEW YORK…` | `…:OT221` | **nothing — the req number precedes the colon** |

For the first two, a live sibling keeps recreating the phantom, so it stays in sync's scope and sync
would still evict a closed row. For the `otis` shape it does not: the phantom is unique per
requisition, no fresh sibling ever recreates it, and a closed posting would have been reachable by
neither planner and served forever as a dead link. Verified before fixing: with prune
prefix-matching and sync unchanged, a closed `otis` row is evicted by neither. Resolving both sides
through the same function puts the closed row and its live siblings on one real Board, so sync
evicts it the run it closes — for every shape, rather than for two of three by luck. `keep` is the set of Boards that
actually exist, so the answer is grounded in real keys rather than in an assumption about id shape:
unambiguous against every Board key in use today, where no key nests inside another at a colon. The
native id becomes whatever follows that prefix, which fixes the duplicate-grouping key for the same
rows.

`index sync` therefore gains a `--ledger` (defaulting, like prune's, to `data/validate/liveness`)
and reads the keep-set on every run. It gets **no** `_MIN_KEEP_BOARDS` guard of its own, unlike
prune: a broken or empty ledger degrades resolution to `board_of` on *both* sides of sync's scope
comparison, which is the pre-ADR-0049 rule and evicts no more than it did before. Prune's guard
exists because there a small keep-set means mass eviction; here it means the old behaviour.

Longest-match rather than first-match is **defence in depth, not a current need**: candidates
form only at colons, and no two live Board keys nest at a colon today — Workday's `co/site` nests
at a *slash*, which is never a candidate position — so first-match would give the same answer on
every id in the table. Taking the longest keeps the answer right if a Board key ever gains a colon,
and the difference is pinned on the helper directly, since `plan_prune`'s output cannot currently
tell the two apart.

Note what that concedes. Should two live Board keys ever nest at a colon, an id on the shorter
Board whose native part begins with the longer key's tail is **genuinely ambiguous** — the
information to resolve it is not in the id. Longest-match is then a declared tie-break, not a
recovered fact. It is the right default (the longer key is the more specific claim), and today it
is unreachable; but "exact by construction" holds against the current keep-set, not for all
possible ones.

The scan returns a **position in the original id**, not the length of the matched key. `live`'s
keys are lowercased and `str.lower()` is not length-preserving (`'İ'.lower()` is two characters),
so slicing the original by the key's length would silently eat a character of the native id — and
on this path that means two distinct Jobs collapsing into one duplicate group and a live row being
deleted. Unreachable with today's keep-set, which is all ASCII, and one line to make exact.

`board_of` keeps its split-based implementation and gains a docstring that states the limit
honestly and says where it is safe. Rewriting it to take a keep-set would change every caller —
including the ones with no keep-set to give it — to serve the two that now resolve through
`resolve_board` instead; the guess and the exact answer are better as two named functions than as
one function with a mode.

## Consequences

Seven rows stop cycling. That is the whole measurable effect, and it is small — but it is small
*per run*, forever, and it was invisible until someone counted the same ids across five runs.

**The class is what matters more than the count.** ADR-0046 fixed the same shape at a different
scale: a comparison that looks reasonable, is wrong for a minority of inputs, and expresses itself
as an eviction that silently reverses next run. Both were only findable by diffing what left the
index against what came back. Any future code that needs "which Board does this id belong to" and
has a real Board set in hand should match by prefix; only code with no such set should reach for
`board_of`, and then only to compare against itself.

The two planners are what this fixes, and where the loop was observed. Other `board_of` callers
are **not** audited here and at least two compare a derived key against a real one:
`board_priority.pick_boards` keys on a real `{ats}:{slug}` against a ledger whose keys come from
`board_of`, and `embed_run.order_by_priority` looks up `board_of(id)` in that same ledger. Both
mis-*score* a colon-bearing Board rather than evicting anything — it sinks to the tail of a
priority order — so neither is urgent, and neither is fixed here.

> **Update (2026-08-18, ADR-0059):** the `pick_boards` half turned out to be worse than
> "mis-scores a colon-bearing Board". `{ats}:{slug}` is not the real key *at all* for the two
> ATSes that override `board_key()`, so no Workday or Personio row ever matched — 20.1% of the
> scrape list, scored 0.0 whatever it had earned. It now keys on `config.board_identity`. What
> remains of the caveat here is only the colon-bearing native id, which writes a phantom ledger
> Board no real key matches. `embed_run.order_by_priority` was already self-consistent.

The scan walks the id once and does a slice plus a `lower()` per colon, against one `rsplit` for
the old rule — measured at 0.16 s → 0.30 s over 300,000 ids, which is not measurable next to the
LanceDB work around it. It now runs on the sync path too, not just prune. Sync also pays for the
keep-set it did not previously build: 0.26 s to read the ledger into 60,691 Boards, plus 0.013 s to
index them by canonical key. Both are noise beside the store download and the table write.

Colon-bearing rows now group onto a **real** Board rather than a phantom, which changes how
ADR-0046's collapse guard sees them (that guard was removed by
[ADR-0101](0101-remove-the-collapse-guard-the-grace-period-is-the-line.md); this paragraph
describes how it behaved): they join their Board's full row count instead of forming
their own small group. The observed phantoms were below the guard's `COLLAPSE_FLOOR` of 20 — the
four `dmainc` rows shared one `…/DMA:REQ` phantom, the `otis` ones were singletons — so those were
exempt and are now protected. It is not a blanket "protected for the first time": a Board with 20
or more URL-native rows already exceeded the floor under its shared `…:https` phantom. Either way
the direction is intended, they are ordinary rows on a real Board; the effect is that a throttled
Workday scrape can now withhold their eviction along with the rest of the Board's.

This does **not** re-key anything. The ambiguous ids stay ambiguous; the fix is that the places
which compared a parsed key against a real one no longer parse.

The reason for not re-keying is **not** that it would force a full re-embed — an earlier draft of
this ADR said so, and that is wrong. Escaping `:` in the native id changes only the ids that
contain one; every other id stays byte-identical, and the embed skip-list is keyed by id, so only
the affected rows would re-embed. The real cost is that an id is a join key across several surfaces
at once — the skip-list, `first_seen` stamps, saved-set references, the served table — so re-keying
is a coordinated migration over all of them, for a population this ADR has only ever observed at
seven rows. Anyone revisiting it should count that population against the **served table**, not a
local snapshot.

The durable fix is better than either: **stamp the Board on the Job at scrape time**. The scraper
composes the id out of `board_key()` and the native id in a single expression (`workday.py`'s
`f"{ats}:{company}/{site}:{ats_id}"`), so it holds the exact answer and throws it away; everything
downstream then reconstructs it by matching strings against mutable ledger state. A `board` field
carried through to the index would make every consumer exact and permanent — including
`board_priority.pick_boards` and `embed_run.order_by_priority`, named above as parsing and
mis-scoring *today*. The cost is a schema change (`index._schema()`, the README's served-table
section, the metas plumbing) plus a backfill, for which `resolve_board` is the right one-time tool.
**Revisit when the next consumer needs to know an id's Board** — on the count above that trigger is
arguably already met, and it is the migration cost, not the design, that defers it.
