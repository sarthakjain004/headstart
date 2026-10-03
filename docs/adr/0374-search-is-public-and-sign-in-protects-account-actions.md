# ADR-0374: Search is public and sign-in protects Account actions

**Status:** accepted · **Date:** 2026-10-02 · **Amends:** [ADR-0042](0042-signed-in-ui-saved-sets.md), [ADR-0112](0112-the-door-earns-the-sign-in-before-it-asks.md)

## Context

The launch review found that visitors could read the product pitch but could not try Search
without Google sign-in. The read routes already answer anonymous callers for MCP; Account
records still require a signed session. The owner chose public browsing before announcement.

## Decision

`/` renders Home and Search without a session, with the existing per-address read limits.
Public UI assets are accessible. `/signin` hosts the existing Google sign-in page, labels its
purpose as saving jobs and searches, and returns to the selected tab after successful sign-in.
The public header links to it. The active query, filters and scope stay in tab-local
session storage during sign-in and are restored once on return. Account tabs and résumé sync controls render only for a signed-in
Account when their backing store is configured. Every Account API retains its existing wall.
A browser-only résumé draft remains available without sign-in, as before.

The README reads searchable-job count through a Shields endpoint (`/badges/jobs`), using the
same count as the product. The count is cached for five minutes at the endpoint; Shields and
GitHub may keep it longer. Other changing coverage figures are replaced with links to the
registry, ADR directory and tested Board glossary. There is no automated README commit per
pipeline run. Historical measurement and worked schema examples remain labelled snapshots.

The measured retired Masimo Oracle Board is parked under #873: its API lists jobs whose public
application pages redirect to Oracle's 404 page. Prune removes its held rows. Unpark only after
public applications work; this is no general rule about Oracle hosts or empty career sites.

## Alternatives considered

- Keep sign-in mandatory: protects no additional read data, and prevents a visitor trying Search.
- Make Account APIs public: rejected; saving and syncing belong to the caller's Account.
- Commit regenerated README numbers after every ingest: creates documentation churn and another
  writer. The live badge and source links require no manual count updates.
- Infer that every Oracle root saying “Page not found” is dead: unmeasured across tenants;
  park the confirmed Board instead.

## Validation

Route tests exercise anonymous browsing, public static assets, the optional sign-in page and
all protected Account routes. Badge tests change the searchable count between requests. The
local browser preview runs Search signed out. Production capacity findings are recorded in
`docs/release-readiness/2026-10-02-launch-verification.md`; they are not a release pass.
