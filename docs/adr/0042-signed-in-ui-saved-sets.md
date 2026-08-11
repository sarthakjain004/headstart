# ADR-0042: Sign-in-required UI — Accounts, Saved sets, Saved jobs on the per-record store

**Status:** accepted · **Date:** 2026-08-11

## Context

Today the Space is one anonymous scrolling page: search box, filter rail, trends panel, and an
invite-only email-alerts panel (ADR-0035). The redesign wants a personal product: sign in,
keep a Profile (ADR-0041), save searches and see them as a live "Matches" page, star
individual jobs, all behind a sidebar. That forces decisions about who gets in, what a saved
thing is, where user data lives, and how sessions work.

Facts that constrained the shape: the Google-identity path already exists
(`alerts.identity.verify`, ADR-0035) but is used per-request, and a Google ID token expires in
about an hour. Subscriptions already are "one person's Query + Search filters", stored one
file per record in the private `headstart-subscribers` dataset — one-file-per-record being a
*correctness* choice (two writers, disjoint paths; ADR-0035). The index churns hard (one run:
+3,846 / −4,673 rows), and ~83% of served rows still lack `first_seen` (ADR-0031 backfills
only as jobs re-embed). The UI exists twice — a ~1,100-line HTML string inside
`deploy/hf-space/app.py` and a long-stale twin in `scripts/ui/serve.py`.

## Decision

**The whole app sits behind Google sign-in; sign-up is open; the gates stay where the cost
is.** Anyone with a Google account may create an Account. Email Digests remain invite-only
(ADR-0035's allowlist, unchanged); Résumé parsing is open but capped per Account (ADR-0041).
Search and Trends are no longer anonymous — accepted deliberately, knowing it costs casual
visitors.

**Sessions are a signed Flask cookie.** The Google credential is verified once at sign-in;
the server then sets its own signed cookie (weeks-long). One new secret (`SECRET_KEY`) joins
the Space. Re-sending the Google token per request was rejected: its ~1h expiry would bounce
users mid-use.

**A Saved set is the unit of personalisation, and the Subscription is the one with email
on.** An Account keeps several named Saved sets (Query + Search filters), each its own file —
extending ADR-0035's one-file-per-record argument to the new writers. Exactly one set per
Account may have email enabled; that set *is* the ADR-0035 Subscription, carrying the
Watermark and Transport, so the digest machinery is reused rather than duplicated. Known,
accepted race: editing the emailing set while a digest run advances its Watermark can lose
one of the two writes — the same narrow window the revise path has today; per-record files
keep it from ever losing a *different* record.

**Saved sets are created from a working search.** A "Save this search" button on the Search
tab names and keeps the query + filters the user is looking at. A blank-form builder was
rejected (people save guesses that match nothing); auto-creation from the Profile was
rejected as the *only* path (it can't keep a good search a user stumbled into).

**Matches shows everything ranked, with "new" marked where known.** An
only-what's-new inbox was rejected while most rows lack `first_seen` — it would hide most
matches and look broken; the tag sharpens on its own as coverage grows.

**A Saved job stores a copy of its display fields.** Starring keeps title, company, link,
salary, and the star time alongside the job id, so the Saved tab survives Eviction and can
mark a closed posting "closed" instead of losing it. Id-only pointers were rejected: at the
measured churn, stars would silently vanish within days. Stars update the UI optimistically
and persist in the background — an HF write is ~1s, too slow for a click.

**User data stays on the per-record HF store.** Profiles, Saved sets, and Saved-job lists are
files in the existing private dataset behind the existing `Store` seam — no new accounts,
secrets, or cost. A hosted Postgres was rejected *for now*: real setup work ahead of any UI,
and the `Store` seam keeps the swap one-file-sized if write latency ever hurts.

**The match score is displayed as a ring with a fixed two-anchor stretch.** Raw cosine scores
live in a narrow band (measured on the live index: a strong on-topic query tops out ≈0.78; a
deliberately absurd one still scores ≈0.66), so the raw number is meaningless as a percent.
Display maps ≈0.60 → 0% and ≈0.85 → 100%, anchors tuned once against real queries and
revisited only when the embedding model changes (ADR-0005). Ranking still orders by raw
score. Page-relative scaling was rejected: it shows junk as 100% and gives the same job a
different percent depending on its neighbours.

**The UI leaves the Python string: Flask templates + static files, one copy.** One HTML
template per tab, shared CSS/JS, rendered by both the Space app and the local dev server;
the deploy workflow syncs them like it already syncs `geo.py` and `alerts/`. The stale
`scripts/ui/serve.py` page dies as a fork. React/Vite was rejected for now (a build step and
Node tooling landing at the same time as the redesign); the giant-string status quo was
rejected as the option that makes the UI untouchable.

**Rollout is staged PRs, each leaving the app working:** (1) template split, zero visual
change; (2) sign-in + sessions; (3) sidebar shell with Search + Trends; then Matches, Saved,
Profile one PR each.

## Options rejected

- **Optional sign-in (public search)** — the user chose sign-in-first explicitly; noted here
  because it inverts ADR-0020's "public demo" posture and is the decision most worth
  revisiting if sign-ups stall.
- **Invite-only sign-in** — matches ADR-0035 as written but shuts the site to anyone not
  hand-added, which contradicts the point of the redesign.
- **One saved set doubling as the email record** — matches today's storage but was explicitly
  outgrown: people track more than one kind of role at once.
- **Per-set independent email schedules** — each needs its own Watermark; the most
  duplicate-prone corner of the alerts code, deferred until wanted.
