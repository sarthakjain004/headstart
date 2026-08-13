# ADR-0049: Match a Job id to its Board by prefix, not by parsing

**Status:** accepted · **Date:** 2026-08-13 · **Amends:** ADR-0023, ADR-0014

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
sync` is unaffected, because it runs both the fresh id and the indexed id through the same
function — the phantom Board is produced identically on both sides, so the scope check still pairs
them. A consistent error that cancels, next to an inconsistent one that loops.

## Decision

**Both planners** stop parsing ids and resolve them against `keep` by **prefix**, through one
shared `resolve_board`: the longest Board in the keep-set that prefixes an id is the Board that
owns it, falling back to `board_of` when none matches (an unlisted or disabled Board — and an
empty ledger, which reproduces the prior rule exactly).

Fixing prune alone would have been worse than the bug. Prune used to evict closed colon-native rows
*by accident*, as off-Board; teaching it to match by prefix correctly stops that, but sync could not
take over, because its scope was built from `board_of` and the phantom Board (`…:OT221`) is unique
per requisition — no fresh sibling ever recreates it, so a closed posting became reachable by
neither planner and would have been served forever as a dead link. Verified before fixing: with
prune prefix-matching and sync unchanged, a closed `otis` row is evicted by neither. Resolving both
sides through the same function puts the closed row and its live siblings on one real Board, so
sync evicts it the run it closes. `keep` is the set of Boards that
actually exist, so this is exact by construction rather than by assumption. The native id becomes
whatever follows that prefix, which fixes the duplicate-grouping key for the same rows.

Longest-match rather than first-match is **defence in depth, not a current need**: candidates
form only at colons, and no two live Board keys nest at a colon today — Workday's `co/site` nests
at a *slash*, which is never a candidate position — so first-match would give the same answer on
every id in the table. Taking the longest keeps the answer right if a Board key ever gains a colon,
and the difference is pinned on the helper directly, since `plan_prune`'s output cannot currently
tell the two apart.

The scan returns a **position in the original id**, not the length of the matched key. `live`'s
keys are lowercased and `str.lower()` is not length-preserving (`'İ'.lower()` is two characters),
so slicing the original by the key's length would silently eat a character of the native id — and
on this path that means two distinct Jobs collapsing into one duplicate group and a live row being
deleted. Unreachable with today's keep-set, which is all ASCII, and one line to make exact.

`board_of` keeps its split-based implementation and gains a docstring that states the limit
honestly and says where it is safe. Rewriting it to take a keep-set would change every caller for
the benefit of one, and `index sync` genuinely does not need it.

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

The scan walks the id once and does a slice plus a `lower()` per colon, against one `rsplit` for
the old rule — measured at 0.16 s → 0.30 s over 300,000 ids, which is not measurable next to the
LanceDB work around it. It now runs on the sync path too, not just prune.

This does **not** re-key anything. The ambiguous ids stay ambiguous; the fix is that the one place
which compared a parsed key against a real one no longer parses. Making ids unambiguous at the
source — escaping `:` in the native id — would be the durable fix, and would also invalidate every
id in the store and the served table, forcing a full re-embed. Not worth it for seven rows.
**Revisit if a second consumer needs to parse ids**, because at that point the guess starts being
made in more than one place.
