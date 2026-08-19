# ADR-0069: Sets own their projection; the allowlist yields to them

- Status: Accepted
- Date: 2026-08-19
- **Resolves a conflict between [ADR-0035](0035-email-job-alerts.md) and
  [ADR-0043](0043-saved-sets-subscription-projection.md)** in ADR-0043's favour
- **Amends [ADR-0035](0035-email-job-alerts.md)**: an Invite's Query is authoritative only
  while the Account keeps no Saved sets

## Context

Two accepted ADRs claimed the same writer, and the code implemented only one side.

ADR-0043: *"**The sets endpoints in the Space app are the only writer that keeps the two in
step**; the alerts run does not know sets exist."* The Space enforces its half — `/subscribe`
returns **409** whenever `_SETS_ON`, with the comment *"a direct subscribe here could desync the
projection from the emailing set"*.

ADR-0035, amended earlier: *"An entry's own Query is authoritative; the file's `default_query` is
only a seed."* `run.subscription_for` implemented exactly that, re-projecting Query and filters
from the Invite on **every run**.

Nothing reconciled them, and the paths meet in production. Enabling email on a set is
allowlist-gated, so **every account that emails a set is also an Invite**. If that person's
Invite is the object form carrying `query`, each alerts run overwrote the emailing set's
projection — permanently, and invisibly: the Matches tab kept showing ✉ on set A while the Digest
delivered query B, and re-editing the set in the UI fixed it only until the next run. The
allowlist writer never got the guard `/subscribe` did.

The bug is asymmetric in a way that decides the resolution. A person can see and edit their sets;
the allowlist is a file only the owner can touch. Letting the invisible writer win means the
visible one lies.

## Decision

**Once an Account keeps at least one Saved set, the Space's sets endpoints own that Account's
Subscription content, and the alerts run treats the record as read-only.**

`subscription_for` takes the set of accounts that own sets and returns the stored Subscription
untouched for those, rather than revising it from the Invite. This is the same rule `/subscribe`
already enforces with its 409, applied to the other writer.

Three consequences follow directly, and each is deliberate:

- **An Account with sets but no Subscription is not minted one.** Turning email on is a
  Matches-tab action; an Invite must not start mail for somebody who signed in, built sets, and
  never enabled delivery.
- **The allowlist remains the owner's edit path for everyone who never signed in.** No sets means
  no other writer, so ADR-0035's behaviour is unchanged for them — which is most invited people.
- **The transport is no longer re-projected either, for accounts with sets.** ADR-0035 gave the
  allowlist the Telegram chat id *"outright"*, since there is no self-serve way to set one. That
  is still true, and this is the one real cost of the decision: an owner who edits a chat id for
  an account that owns sets will find it ignored. Accepted rather than carved out, because a
  partial write is what made this class of bug hard to see — one writer per record is the property
  worth keeping. If chat ids need editing for such an account, the honest fix is a self-serve
  transport control, not a second writer.

**Existence is answered from the file listing, once per run.** `Store.accounts_with_sets()` reads
no records — the caller only needs existence, and the path shape carries it. It runs once and is
passed down, because `_list_files` is a live API call and the previous shape would have repeated
it for every invited address.

## Consequences

**ADR-0035's "an entry's own Query is authoritative" is now conditional** — true while the Account
keeps no sets, superseded by the set's projection once it does. The sentence is left in place there
with a pointer here, since it was correct when written and is still correct for the population it
describes.

**ADR-0043's "the alerts run does not know sets exist" is no longer literally true**, and could not
remain so: a writer that cannot see the other writer's records is precisely how the two desynced.
It now knows exactly one thing — whether an Account has any — and nothing about their contents.
`store.SavedSet`'s docstring carried the same claim and is corrected.

**The guarding test was self-confirming, in the same shape as the `/allow` defect on #192.**
`tests/test_alerts_run.py` asserted the re-projection worked, which it did; nothing asserted what
happened when the Account also had sets, because no test constructed that state. The new tests
drive both populations; the two that exercise the gated path were confirmed to fail without it.
The third pins the *un*gated population and passes either way by design — it is a guard against
over-reach, not a proof of the fix, and saying so is the point of this paragraph.

**Not addressed: the Telegram enrolment path.** A chat-created Subscription is delivered directly
and never passes through `subscription_for`, so it was never exposed to this. If sets ever become
reachable from a chat account, the same question returns.
