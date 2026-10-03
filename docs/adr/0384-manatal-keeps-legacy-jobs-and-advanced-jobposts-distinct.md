# Manatal keeps legacy Jobs and advanced JobPosts distinct

**Status:** accepted · **Date:** 2026-10-03 · **Relates to:** 0012, 0053, 0158, 0201

## Context

Manatal has two public publication systems. Legacy `www.careers-page.com/{slug}` has an
anonymous full-description JSON API. Advanced `{company}.careers-page.com` publishes UUID
JobPosts in paginated HTML. The vendor documents many-to-many Job/JobPost relationships;
its own two sites returned 32 legacy Jobs and 40 advanced posts. See [the measurement](../manatal/2026-10-03_public-api-measurement.md).

## Decision

Retain a legacy label or the full advanced host as separate Board coordinates. Never silently
translate an advanced host to the legacy API, and never merge titles as if they were ids.
Initial activation prefers the employer-endorsed surface where two known surfaces overlap.
The vendor's own `/careers` currently redirects to the legacy Board; its alternative advanced
Board is held pending an explicit decision to include that overlapping collection. Archive
expansion found 13 further same-label live legacy/advanced pairs. Their advanced surfaces
are held pending employer endorsement and overlap reconciliation; this is a conservative
initial-source choice, not an assertion that their complete posting sets are equal.

Legacy listing requests use page size 100, the measured clamp, and increment on the known
API host. Repeated/failed pages make the read incomplete. A 404 with the measured missing
ClientPortalSettings message is dead; an Invalid page 404 is not a dead-company verdict.
The list contains the full description, so legacy Jobs require no per-job request. Read the
Board title for its employer. `organization_name` is a department only when the public page
explicitly names that dimension department; it can otherwise be an agency client.

Advanced HTML follows actual next-page links and checks distinct ids against the stated
total. It reads full public detail content without executing scripts. No pre-detail tech gate
or held-description skip is asserted: the detail can supply type and department metadata.
Listing refusals retain known rows with truncation; failures before a listing is established
raise. A 429/challenge is not an empty or dead Board. Requests are paced conservatively and
the advanced path does not retry challenge responses within its detail requests.

Keep dates and experience unknown where the source provides none. Native salary amounts
omit a period, including an observed daily rate; leave structured salary unset and let the
shared description extractor use explicit currency/period wording. Consultancy maps to
Contract so the existing employment filter does not mistake it for full-time employment.

The ATS lands enabled: the legacy sample's 921 Jobs included 223 tech matches, at about
0.019 MB decoded listing data per tech Job. Advanced validation is narrower: two Boards were
read completely, while the 1,967-post Board was incomplete after Cloudflare challenges.
The limitation is preserved in measurements and liveness; it is not represented as a full read.

## Alternatives and consequences

JSON-LD-only parsing fails on most sampled pages. Assuming every compensation number is
annual misprices daily contracts. Combining old and new publication identities loses posts
or creates false equivalences. Custom advanced domains without a confirmed provider namespace
remain outside this adapter's measured URL contract.
