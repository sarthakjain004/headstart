# ADR-0124: Résumés sync to Postgres on the Oracle box, and only when asked

**Status:** accepted · **Date:** 2026-09-09 · **Amends ADR-0041's "the Résumé is read once and discarded" for the Résumé document only, and says so out loud. Extends ADR-0123.**

> Two things are settled here rather than one, deliberately: the **tailoring model** (§Context) and
> the **store** (§The database). They are recorded together because the first is the only reason
> the second is not a single blob — the schema exists to hold per-component variants, and splitting
> them across two ADRs would leave each half unmotivated.

## Context

ADR-0123 put the **Résumé document** in the Account's browser and nowhere else. That is the
strongest possible privacy position and it has one failure mode people actually hit: a résumé lives
on one machine and is one cleared cache from gone. The owner asked for server storage, and for a
model that keeps **several versions of each component's words** so one résumé can be tailored per
job application without being copied.

The versioning requirement is the part that constrains the schema, so it is settled first.

**Tailoring stores differences, not copies.** A **Tailoring** is one job application's version of a
résumé: a map of node → variant for the blocks whose words it changes, plus the blocks it leaves
out. Everything it does not mention falls through to the master. That is what makes the feature
worth having rather than a folder of near-identical files: fixing a typo in the master reaches
every version that never disagreed with it. Editing under a Tailoring forks a variant on the first
keystroke — copy-on-write — so tailoring a bullet can never edit the master by accident. This is
implemented and tested in `resume_document.js` and `tests/js/resume_tailoring.test.js`; the schema
below is that model at rest.

## The database

| | option | for | against |
|---|---|---|---|
| **A ✅** | **Postgres on the existing Oracle box**, over the SSH tunnel the llm-router already uses | no new vendor and no new trust boundary; real transactions and real deletes; `jsonb` for content with indexes on what is queried; `ON DELETE CASCADE` makes "delete this version" the database's job rather than application code's; the same posture the project already accepted for the llm-router — private box, tunnel, degrade rather than die | the tunnel is a single point of failure; one more daemon to run and back up; credentials in the Space |
| B | Keep the HF dataset repo, as the Profile and Saved sets do | zero new infrastructure; already authenticated and backed up; free | **fatal on two counts.** A builder autosaves, so every save is a Git commit — thousands per user. And Git remembers: a deleted résumé's words stay in repository history behind any "delete", recoverable only by `super_squash_history` on a schedule. Résumé text is the most identity-laden data in the product; that is the wrong storage for it |
| C | SQLite on the Oracle box | one file; transactional; trivial to back up | one writer at a time under concurrent Space workers, and it needs a service in front of it anyway — at which point Postgres costs the same to run |
| D | Managed free tier (Neon / Supabase / Turso) | reachable from the Space with no tunnel; purpose-built; generous free tiers | a third party holding résumés and employment history; a free tier that may not stay free; another credential; cold starts |
| E | Stay browser-only (ADR-0123 as shipped) | the strongest privacy story; zero infrastructure | one device, and one cleared cache from empty — which is the problem being solved |

**Decision: A.** It adds no vendor and no new party to the data, it gives deletes that are deletes,
and it is the same box, the same tunnel and the same failure posture the project already accepted
for the llm-router. B is disqualified on write volume and on deletion semantics, not on taste.

## Decisions

1. **Postgres 16 on the Oracle box**, reached over the existing SSH tunnel. Schema:
   `deploy/resume-store/schema.sql`. Three tables, because the three have genuinely different
   lifetimes and different queries: `resume_document` (structure, layout, theme), `resume_content`
   (the words — one row per document/node/**variant**, with `'base'` as the master's wording), and
   `resume_tailoring` (one row per version, carrying the `job_id` it was written for).
2. **Syncing is opt-in, per document, and off by default.** ADR-0041 promises that
   HeadStart's servers never hold a résumé. Storing one is a reversal of that promise, so it is a
   thing the Account switches on for a particular résumé, with the consequence stated in the same
   sentence as the switch — never a silent upgrade of what the product keeps. A résumé that is
   never switched on behaves exactly as ADR-0123 shipped it. The **Profile** rule is untouched: it
   still holds no contact details, and the extraction path still discards its input.
3. **Offline-first.** The browser stays the working copy and the source of edits; the server is a
   sync target. A tunnel outage, a Space restart or a signed-out session degrades the tab to
   exactly ADR-0123's behaviour rather than breaking it. Nothing in the Résumé tab may block on the
   network.
4. **Optimistic concurrency, and a conflict is never resolved by discarding.** The **store**
   assigns each document a `rev` and a push must carry the one it last saw; a push with a stale one
   is refused with the server's copy. The client document has no `rev` today and does not need one
   until it syncs — it is the store's counter, not a field of the model. The client then
   keeps **both** — the loser is saved beside it as "… (this device)" — because a résumé edited on
   two machines is someone's afternoon, and silently picking a winner is how it disappears. A real
   three-way merge is not attempted.
5. **Deletion is deletion, within a stated window.** Rows are soft-deleted so an accident is
   recoverable, then really deleted by a scheduled sweep; the window is stated in the product.
   `ON DELETE CASCADE` means removing a document removes its words and its versions with it, and
   removing a Tailoring removes the wordings only it referenced.
6. **`job_id` is a plain column, not a foreign key.** A **Job** lives in the LanceDB search index,
   which this database knows nothing about, and Jobs are evicted (ADR-0023/0083) while the record
   of having applied with a given version must outlive the posting. It is indexed, nullable, and
   never joined across stores.

## Consequences

The client holds a document as one immutable value and the store holds it as rows, so the sync
layer computes a per-node diff on push. That is real work — perhaps forty lines — and it is the
price of the two things the split buys: cascade deletes that cannot leak orphaned wordings, and
two devices editing different bullets merging instead of one losing.

The Résumé tab gains a dependency the rest of the product does not have, with the same failure mode
auto-apply has: "syncing is off just now", never a broken tab. Nothing here is wired up yet —
this ADR fixes the schema and the posture so that the client model, which is built and tested, is
not built against a shape the store cannot hold.
