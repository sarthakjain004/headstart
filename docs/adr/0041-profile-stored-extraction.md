# ADR-0041: Profile — store the LLM extraction, discard the Résumé

**Status:** accepted · **Date:** 2026-08-11

## Context

Until now the rule was absolute: a Résumé "is never stored, never logged, and never leaves the
request that carried it" (CONTEXT.md), and the glossary banned the word "profile" *because* it
implies persistence. That rule was right for the world it was written in — an anonymous search
page with no accounts, where anything kept would have belonged to nobody and been deletable by
nobody.

That world is ending: the UI is moving behind Google sign-in (ADR-0042), and the product wants
per-person matching — ranking against what a person is good at, and filters pre-filled from
their real years and location. Both need career facts that survive the request. The existing
`resume_query.query_for` seam (ADR-0032) already turns a Résumé into one role sentence via the
llm-router; what's missing is the structured remainder and somewhere for it to live.

Two constraints shape what may be kept. A résumé is among the most sensitive documents a person
has — phone number, home address, sometimes date of birth — and this runs on a free-tier host
with no compliance story. And CONTEXT.md's Query rule splits search into a fuzzy half and an
exact half: the typed sentence names *only a role*; years, salary, and location belong to
Search filters, because a constraint buried in the sentence merely nudges ranking instead of
excluding.

## Decision

**Store the extraction, never the document.** A signed-in user pastes or uploads a Résumé
once; one LLM call extracts a **Profile**: a role sentence plus structured facts — current
title, years of experience, skills, past roles, education, location. The Profile is stored
(one file per Account, ADR-0042); the raw document is discarded after the call, exactly as
today, and contact details are never part of the extraction. The Profile is editable by hand
afterwards, so the LLM is a convenience, not a gate.

**The Profile is split by what each half is for.** The sentence drives ranking — it is the
Résumé query, now persistent and editable in one place. The facts pre-fill Search filters.
This keeps the Query rule intact: a Profile never smuggles years or location into the
embedding, however loudly the Résumé states them.

**The gate moves from a shared password to a per-Account cap.** ADR-0032's password + IP set
existed because callers were anonymous; a verified account is a better identity than an IP.
Any signed-in user may parse a Résumé, capped at **3 parses per Account** (a lifetime cap,
raised by hand if it ever pinches). Worst-case router spend becomes `accounts × 3` calls —
predictable — instead of unbounded. The password and `_RESUME_OK_IPS` retire with the old
flow.

**Deletion is one file.** Because only the extraction is kept and it lives in one per-Account
record, "delete my data" is removing that file plus the Account's Saved sets and Saved jobs —
no document archive to scrub.

## Implementation notes (2026-08-12)

**The parse counter lives in its own file** — `profiles/{account}.parses.json`, beside the
Profile record — **written only by the parse route.** Keeping it inside the record was tried
first and reviewed out: every save is a read-modify-write of the record, so a save racing a
parse writes back the stale counter it read, and interleaving the two on purpose refills the
cap indefinitely. Disjoint files make that impossible by construction (ADR-0035's argument);
"delete my profile" removes the career record and leaves the counter, so deletion never
resets the cap. The counter read **fails closed**: an absent file is 0, but an unreadable or
corrupt one answers 503 rather than 0 — a transient failure must not reset the cap either.

**What counts as a parse:** any call that reached the router and got an answer — including
one whose reply held no usable Profile (the user sees a 502; the read is spent). Refused
before the router (empty or oversized paste) spends nothing. `RouterUnavailable` spends
nothing: it is usually the router being down, and burning a user's reads on our outage is
worse than the residual it accepts — a timeout after the router took the request may still
complete upstream, uncounted. Accepted: that path is rare, throttled by the timeout itself,
and bounded by retries a human is willing to make. Two concurrent parses under the cap can
also both pass the check — one-record overshoot per race, same acceptance as ADR-0044's cap.

**The scrub guards every door.** The Query rule is enforced in code on the extracted
sentence *and* on hand-edited saves (`profile_extract.scrub_query` runs in both routes), so
a Profile cannot smuggle years or salary into ranking whichever way the sentence arrived.

## Options rejected

- **Typed fields only, no upload/LLM**: safest, no router cost — but a form most people skip,
  leaving matches generic; the feature exists to be filled in.
- **Store the raw Résumé too** (embed the whole document for nearest-job matching): the best
  matching signal, rejected because it makes us custodian of strangers' full personal
  documents on a free-tier host with no delete story — and it would bury years/location in
  the fuzzy half, breaking the Query rule.
- **Keep the LLM invite-only and let everyone else type**: two profile experiences to build
  and explain, with most users landed on the tedious one; parsing is a one-off cost, so a cap
  bounds it well enough.
- **Embed the whole career summary for ranking**: rejected for the same Query-rule reason —
  "senior" leaks into ranking where it can only nudge, instead of into a filter where it
  excludes.
