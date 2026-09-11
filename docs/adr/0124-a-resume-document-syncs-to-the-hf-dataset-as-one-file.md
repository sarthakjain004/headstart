# ADR-0124: A Résumé document syncs to the HF dataset, as one file, and only when asked

**Status:** accepted, amended by [ADR-0131](0131-forgetting-a-resume-costs-the-subscriptions-dataset-its-history.md) (which settles the four things this one left open, and builds the scheduled squash decision 5 makes the feature wait for — so decision 5's "syncing does not ship" is satisfied, not outstanding) · **Date:** 2026-09-10 · **Amends ADR-0041's "the Résumé is read once and discarded" for the Résumé document only, and says so out loud. Extends ADR-0123.**

> Two things are settled here rather than one, deliberately: the **tailoring model** (§Context) and
> where a **Résumé document** is stored (§Where it lives). They are recorded together because the
> first is the only reason the second has a shape at all — the record has to hold per-component
> variants, and splitting them across two ADRs would leave each half unmotivated.

## Context

ADR-0123 put the **Résumé document** in the Account's browser and nowhere else. That is the
strongest possible privacy position and it has one failure mode people actually hit: a résumé lives
on one machine and is one cleared cache from gone. The owner asked for server storage, and for a
model that keeps **several versions of each component's words** so one résumé can be tailored per
job application without being copied.

The versioning requirement is the part that constrains the record, so it is settled first.

**Tailoring stores differences, not copies.** A **Tailoring** is one job application's version of a
résumé: a map of node → variant for the blocks whose words it changes, plus the blocks it leaves
out. Everything it does not mention falls through to the master. That is what makes the feature
worth having rather than a folder of near-identical files: fixing a typo in the master reaches
every version that never disagreed with it. Editing under a Tailoring forks a variant on the first
keystroke — copy-on-write — so tailoring a bullet can never edit the master by accident. This is
implemented and tested in `resume_document.js` and `tests/js/resume_tailoring.test.js`.

Because a Tailoring is a difference set rather than a document, the whole thing — tree, content,
variants, tailorings — is one small JSON object. That is what the client already serialises for its
own backup, and it is what makes the storage question much smaller than it first looked.

## Where it lives

**The private HF dataset the Account's other records already live in**, as one file per document:

```
resumes/{account}/{document_id}.json
```

`{account}` is `subscription_id(email)`, the same identifier the **Profile** (`profiles/`), **Saved
sets** (`sets/`) and **Saved jobs** (`saved/`) are filed under, and the same `_ID.fullmatch`
traversal guard applies. The file's contents are exactly the document the client holds — the JSON
export, unchanged — so there is no second schema to keep in step with the model, and no diffing
layer between them.

This is the owner's decision, and it is the one this repo has already taken twice: ADR-0042 put
Profiles, Saved sets and Saved-job lists on this same store and rejected a hosted Postgres *for
now* on exactly these grounds — "no new accounts, secrets, or cost", with the `Store` seam keeping
the swap one-file-sized if write latency ever hurts. Putting résumés somewhere else would have made
this the only user record in the product that lived apart from the rest of an Account.

It was taken against a recommendation for a transactional store, and the two objections that
recommendation rested on are recorded here rather than dropped, because each one now carries a
mitigation that has to actually be built:

**1. A builder autosaves; Git commits are not free.** Mirroring the browser's autosave to HF would
be thousands of commits a day per Account. It is not mirrored. **The browser stays the working
copy** (ADR-0123's repository, unchanged) and HF is a *sync target* written on coarse events only:
an explicit "Save to my account", tab-hide, and at most once every few minutes while editing. That
is the same order of write frequency as starring a job, which this dataset already carries. The
debounce that guards `localStorage` is the wrong instrument here and must not be reused for it.

**2. Git remembers, so "delete" is not deletion.** Deleting the file removes it from the tree
immediately, but the content stays in repository history. `HfApi.super_squash_history` makes
forgetting real, and it is the only thing that does. **Retention is therefore a stated number, not
a sentiment:** a deleted résumé is gone from the tree at once and out of history within **30 days**,
enforced by a scheduled squash of the branch. Until that job exists, the product must not claim
deletion is immediate. This is the one piece of work the decision creates that the alternative
would not have.

**3. Contention.** Every write is a commit on one repo head, shared with every other Account. At
this write frequency that is what Profile and Saved sets already do and it has not been a problem;
it is recorded as a thing to watch, not a thing to solve now.

### The options that were weighed

| | option | for | against |
|---|---|---|---|
| **A ✅ (chosen)** | **The existing private HF dataset**, one JSON file per document | no new infrastructure, no new vendor, no new credential, no second schema; the same auth, the same backup and the same identifier as the Profile; the stored record IS the client's model | commits, not transactions; deletion needs a scheduled squash to be real; one repo head shared by every Account |
| B | Postgres on the existing Oracle box, over the router's SSH tunnel | real transactions and real deletes; `jsonb` with indexes; cascade deletes | a daemon to run and back up, a tunnel that is a single point of failure, credentials in the Space — and, for a record this small and this rarely written, a transactional store solves a problem this feature does not have. ADR-0042 rejected hosted Postgres for this product's user records already |
| C | SQLite on the Oracle box | one file, transactional, trivial to back up | one writer at a time under concurrent Space workers, and it needs a service in front of it anyway |
| D | Managed free tier (Neon / Supabase / Turso) | purpose-built; reachable from the Space with no tunnel | a third party holding résumés and employment history; a free tier that may not stay free |
| E | Stay browser-only (ADR-0123 as shipped) | the strongest privacy story; zero infrastructure | one device, one cleared cache from empty — the problem being solved |
| F | A file the user controls (File System Access API, or their own Drive) | keeps "we never hold your résumé" intact while solving single-device | patchy browser support; the user carries the file; no cross-device sync without a service anyway |

## Decisions

1. **One file per document at `resumes/{account}/{document_id}.json`**, contents identical to the
   client's own JSON export. No second schema.
2. **Syncing is opt-in, per document, and off by default.** ADR-0041 promises that HeadStart's
   servers never hold a résumé. Storing one reverses that promise, so it is a thing the Account
   switches on for a particular résumé, with the consequence stated in the same sentence as the
   switch — never a silent upgrade of what the product keeps. A résumé that is never switched on
   behaves exactly as ADR-0123 shipped it. The **Profile** rule is untouched: it still holds no
   contact details, and the extraction path still discards its input.
3. **Offline-first.** The browser is the working copy and the source of edits; HF is a sync target
   written on coarse events, never on a keystroke. A signed-out session, a Hub outage or a missing
   token degrades the tab to exactly ADR-0123's behaviour rather than breaking it. Nothing in the
   Résumé tab may block on the network.
4. **A conflict is never resolved by discarding.** The synced document will carry a `rev` the
   client increments — it has none today, and gains one when sync is built, because a revision
   counter on a record that is never pushed anywhere counts nothing. A push whose `rev` is not one
   past what is stored is refused, and the client then keeps **both** — the loser saved beside it as "… (this device)". A résumé edited on two machines
   is someone's afternoon, and silently picking a winner is how it disappears. Whole-document
   last-write-wins: unlike a row-per-field store, two devices editing *different bullets* still
   conflict. That limit is real and is stated in the product rather than papered over.
5. **Deletion is deletion within 30 days.** The file is removed at once; a scheduled
   `super_squash_history` puts it beyond recovery inside the window. The number appears in the
   product. **Until that scheduled job exists, syncing does not ship** — a promise of deletion the
   infrastructure cannot keep is worse than no sync at all.
6. **The Job a version was written for** is a plain field on the Tailoring, not a reference.
   A **Job** lives in the LanceDB search index this dataset knows nothing about, and Jobs are
   evicted (ADR-0023/0083) while the record of having applied must outlive the posting.

## Consequences

The storage layer becomes almost nothing: `Store` gains a prefix and three methods shaped exactly
like `get_profile`/`put_profile`/`remove_profile`, and the record is the model. That is the real
prize of this decision, and it is larger than it looks — the transactional design needed a schema,
a per-node diff on every push, and a mapping between two shapes that could drift.

What it costs is bounded and named: no transactions, coarse sync rather than continuous, and a
scheduled squash that has to be built before the feature can honestly claim deletion. Two devices
editing the same résumé conflict at document granularity rather than merging per field.

Nothing here is wired up yet. This ADR fixes the record and the posture so that the client model,
which is built and tested, is not built against a shape the store cannot hold.

## Amendment (2026-09-11): a stored copy that cannot be READ is refused, and the loser's way out is offered before the refusal

**Status:** accepted. Extends decision 4 ("a conflict is never resolved by discarding") to the two
cases the original decision did not name: a read that does not answer, and a device that already
knows it is behind.

Decision 4 was implemented as `stored = store.get_resume(...)`, and `get_resume` answers `None` for
an absent record **and** for a Hub that did not answer. The push route read that `None` as "nothing
stored, so nothing to lose" and accepted any revision on it — so one unanswered read let a stale
push overwrite a newer copy and answered `200`, with the losing revision surviving only in the
dataset's git history, which decision 5's squash exists to erase. That is decision 4 failing open
through an ambiguity one level down, and it is the same conflation `Store.get` was already fixed
for after it minted a replacement Subscription during an outage.

1. **Absent and unreadable are different answers on the push path.** `Store.resume_revision` answers
   `None` only for a document the Account does not hold, and raises for one it holds and could not
   read — the fail-closed shape `parses_used` already has. Absence is decided by the listing, which
   is what the delete route checks too, so the two routes agree on what "no such résumé" means.
2. **The refusal is a 409 with no `stored` body, not a 503.** The client maps 503 to "this
   deployment keeps no account copies at all" and turns the feature off; a bodyless 409 is already
   its "refused, and nothing here was overwritten" path. The cost is that a Hub outage reads to the
   user as a conflict. That is the safe direction: both sentences end in "nothing here was
   overwritten", and only one of them is reversible if it is wrong.
3. **Both-copies-kept is reachable deliberately, not only through a refused push.** The Résumés list
   compares each local row's revision to the Account's and says when it is behind, offering the
   Account's copy through the same resolution a refusal gets — this device's copy is kept beside it
   as "… (this device)". Discovering you were behind *after* an afternoon's work, when the push is
   refused, was decision 4 arriving too late to help.
