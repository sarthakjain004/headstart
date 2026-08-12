# ADR-0043: Saved sets are per-record files; the Subscription is the emailing set's projection

**Status:** accepted · **Date:** 2026-08-12

## Context

ADR-0042 decided the product shape: an Account keeps several named **Saved sets** (Query +
Search filters), the Matches tab runs one live, and exactly one set per Account may have
email on — "that set *is* the ADR-0035 Subscription." This ADR decides how that sentence is
implemented in storage, because the naive readings conflict.

The Subscription record already carries machinery that must not be casually rewritten: the
**Watermark** (advance it wrongly and someone is double-mailed or skipped) and the
**unsubscribe token** (rotate it and every mailed link 404s). Two writers already touch it —
the Space on subscribe, the alerts run on every Watermark advance — which is why ADR-0035
made records per-file. The alerts run, the Telegram bot, and `/unsubscribe` all enumerate or
address `subscriptions/` records directly.

## Decision

**Saved sets are their own per-record files** — `sets/{account}/{set_id}.json`, where
`account` is `subscription_id(email)` (the same address-hiding hash, one namespace per
address) and `set_id` is a short random hex. One file per set extends ADR-0035's
disjoint-writes argument to the new writers. `MAX_SETS = 10` per Account bounds abuse.

**The Subscription record is the delivery projection of the emailing set.** Turning email ON
for a set (invite-gated, ADR-0035 unchanged) copies its Query and filters into the existing
Subscription record — `revised()` when one exists, so the Watermark and unsubscribe token
survive; `create()` when none does, so the Watermark starts now and nobody is mailed the
backlog. Editing the emailing set re-projects in the same request; moving email to another
set flips both `emails` flags and re-projects. **The sets endpoints in the Space app are the
only writer that keeps the two in step**; the alerts run does not know sets exist.

**Deleting the emailing set removes its Subscription.** A set that no longer exists must not
keep mailing. The cost: previously mailed unsubscribe links die, and a later re-enable mints
a fresh token and a fresh now-Watermark. Accepted — the alternative (an orphan Subscription
that keeps delivering a query the user deleted) is worse in exactly the way users notice.
Symmetrically, **an /unsubscribe click clears the set's `emails` flag** — the Subscription id
*is* the sets namespace for that address, so the route can find them; without this, the tab
would keep showing ✉ on and the next edit of that set would silently re-subscribe the person.
And **toggling OFF removes the Subscription only if the toggled set actually carried email**,
so a stale tab acting on an outdated strip cannot stop someone's mail.

**A Saved set may carry `seen_within`; the projection drops it.** ADR-0035 bans it from
Subscriptions because it fights the Watermark — but a set run live in the Matches tab has no
Watermark, and "Save this search" promises to keep the filters the user was looking at. So
sets keep it (`SET_SEARCH_FILTERS`), and it falls away only where the Watermark takes over.

**Pre-sets Subscriptions are adopted.** An address that subscribed before sets existed has a
live Subscription and no set showing ✉ on — the split-brain this design exists to prevent.
The first `GET /sets` for such an account materializes the Subscription as its emailing set
(same query and filters); machinery untouched.

**While sets are live, `/subscribe` refuses (409).** The projection invariant holds only if
the sets endpoints are the sole Subscription writer, so the pre-sets endpoint stays for the
wall-off configuration and is actively gated, not merely hidden, when `_SETS_ON`.

**CSRF for the new session-authenticated writes** rides the session design: the cookie is
`SameSite=Lax`, so it never accompanies a cross-site POST/DELETE, and the JSON bodies force
a failing CORS preflight besides. No token machinery until something weakens either property.

**Races accepted, same shape as ADR-0035:** editing the emailing set while a digest run
advances its Watermark can lose one of the two writes to the *same* record; per-record files
keep it from ever touching a different one. The email toggle writes up to two set files plus
the projection — not atomic, and a crash between writes can briefly leave two `emails` flags
or a stale projection; every one of these states is self-healed by the next toggle or edit,
and the alerts run reads only the single projection, so it can never double-mail. One
adoption-specific residual: a crash between flipping `emails` off and removing the
Subscription leaves an orphan record that the next `GET /sets` adopts back as a visible
emailing set — turned-off mail resurrected, but visibly (✉ on in the tab), one toggle to
re-stop, and only through a one-request crash window. Accepted.

## Options rejected

- **Merge Subscription into SavedSet (one record type):** conceptually purest, but it
  rewrites the store enumeration, the alerts run, `/unsubscribe`, and the Telegram path in
  one change, plus a live-data migration — the largest blast radius in the code where a bug
  means duplicate or skipped mail, for no user-visible gain over the projection.
- **Ship sets without the email linkage:** smallest step, but leaves two disconnected UIs
  ("Email me new matches" panel vs the sets tab) describing what gets delivered — a
  split-brain the projection exists to prevent. The old subscribe panel is instead hidden
  when sets are live, and `/subscribe` stays for the wall-off configuration.
