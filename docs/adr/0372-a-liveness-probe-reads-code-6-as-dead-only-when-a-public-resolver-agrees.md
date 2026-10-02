# ADR-0372: A liveness probe reads curl's code 6 as a dead host only when a public resolver agrees

**Status:** accepted · **Date:** 2026-09-30 · **Relates to:** [ADR-0012](0012-liveness-ledger.md) (the ledger is the scrape list), [ADR-0181](0181-a-breezy-board-is-one-verbose-json-listing.md) (a DNS failure on a wildcard host is UNKNOWN), [ADR-0189](0189-a-jibe-board-is-a-client-read-under-its-own-robots-rules.md) (Jibe's public-resolver check)

## Context

`check_liveness.py` settled a probe DEAD on curl error 6, "could not resolve host", and a DEAD
verdict is not re-probed for 90 days. Code 6 says the machine's own resolver returned no address,
which is not the same as the host not existing: a router's resolver that stalls under the prober's
workers reads every probe as "could not resolve". Four agents landing the 2026-09-29/30 infrastructure
batch hit it on the owner's wifi (Cornerstone, Avature, Darwinbox, PeopleStrong) and each had to land
through a DNS-over-HTTPS wrapper. Two places had already met it one at a time: ADR-0181 made a failed
lookup UNKNOWN on wildcard hosts, after 41 live Breezy Boards were written dead off the local
resolver, and Avature and Jibe ask a public resolver before they call a host dead. Every other probe,
and Darwinbox (whose `.in` and `.com` both answer every label, so DNS can never prove a tenant absent),
still trusted code 6.

Measured 2026-09-30, with the local resolver simulated down (every request raises code 6) and the real
1.1.1.1 and 8.8.8.8 answering, one live ledger row per probe: **24 of the 45 probes wrote a live Board
DEAD** (ashby, cornerstone, darwinbox, eightfold, gem, greenhouse, happydance, join, lever, oracle,
personio, phenom, pyjamahr, radancy, ripplehire, rippling, smartrecruiters, taleo_be, taleo_enterprise,
teamtailor, trakstar, workable, wp_job_openings, zwayam).

## Decision

**`_is_dns` is true only when the error is code 6, names its host, and a public resolver says that host
has no address.** It reads the host from curl's own message and asks `_public_resolver_has_no_a_record`,
the check Avature and Jibe already used: NXDOMAIN or an empty NOERROR from the first of 1.1.1.1 and
8.8.8.8 that answers. A public resolver that resolves the host means the local one failed, and neither
answering is no answer, so the probe is UNKNOWN and is re-probed on the next pass. The same 45-probe
sweep after the change: no probe wrote a live Board DEAD. Genuinely absent hosts still settle DEAD
(`danaher.taleo.net`, an invented `csod.com` label: NXDOMAIN on both resolvers).

**`p_darwinbox` no longer counts a failed lookup at all.** 1.1.1.1 and 8.8.8.8 resolved an invented
label on `darwinbox.in` and on `darwinbox.com`, so a Darwinbox Board is dead only on the body's own
"Invalid subdomain" or "Error while getting tenant info".

## Consequences

- A run on a machine whose public resolvers are blocked settles no Board dead by DNS: they stay UNKNOWN,
  and the pass report shows `dns-unconfirmed` and `dns` counts, which is the visible failure, not a
  silent wrong DEAD.
- Each DNS-dead host costs one more UDP exchange, up to 5 s per resolver on a timeout.
- Avature and Jibe ask the public resolver a second time for a host `_is_dns` has just confirmed; the
  answer is the same and the query is cheap, so their own checks stay.
- The other DNS-failure hazard in the infrastructure batch is not covered: `eightfold_dns_sweep.py`, a
  discovery script, still counts four timeouts as a label that does not exist.
