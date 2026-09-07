# ADR-0115: One User-Agent, chosen to identify — and its shape is set by hosts, not by taste

**Status:** accepted · **Date:** 2026-09-07 · **Relates to:** ADR-0063 (the spare egress this
string cannot fall back on, since `successfactors` sets no `egress_fallback_on`), ADR-0064 (the
value gate that would otherwise have dropped the affected Boards), ADR-0056 (darwinbox's browser
escalation — the one place a non-`headstart` agent is legitimately sent)

## Context

`headstart.scrapers.base.USER_AGENT` is a single string every scraper sends on every request —
~20,000 Board fetches a run across 22 registered ATSes. Nothing about it looked like a decision. It
had never been written down, and it read as cosmetic, which is precisely how it became a defect.

On 2026-09-07 a five-run log review found **102 SuccessFactors Boards returning 0 jobs in every
run** — 56,120 postings listed and none ingested, 631 board-minutes a run spent for nothing, and
the worst of them owning 97% of its shard's wall clock. The cause was the string: a SuccessFactors
edge policy denylists the **exact literal** `headstart/0.1 (job-board reader)`, while
`headstart/0.1 (job-board)`, `headstart/0.1 (reader)`, `curl/8.7.1` and `python-requests/2.32.3`
are all served on the same URL. Full measurements:
`docs/successfactors/2026-09-07_user-agent-denylist.md`.

Two questions had to be settled before the string could move, and neither is a coding question.

**Is changing it legitimate?** A block naming our crawler deserves that question. All three hosts
tested — two blocked, one control — serve the byte-identical stock SuccessFactors robots.txt,
`/job/` is explicitly allowed on every one, and there is no `User-agent: headstart` stanza
anywhere. The one place these tenants express crawler policy permits exactly what we do; the block
lives only in an edge bot-management rule, and the control host with identical policy does not
apply it.

**What may the replacement be?** The obvious answer — a contact URL, so a host that dislikes our
traffic can reach a human rather than guess — turned out to be unavailable, and not for a reason
anyone would predict.

## Decision

**One shared agent, and it identifies rather than impersonates.** No `Mozilla/`, no `Chrome/`. A
browser string would work, and it is refused anyway: an honest agent was *measured* sufficient
(bare `headstart/0.1` is served 200 by the SuccessFactors policy — it is a row in the
bisection table of the writeup above, not an inference from the variants around it), so impersonation would buy
nothing and cost the honesty. Identification is also what makes a block legible to whoever imposed
it — a host that decides it dislikes us can say so about a name.

**Its shape is decided by measurement, not by preference.** Three live constraints, and their
intersection is narrow:

1. **Not the denylisted literal.** SuccessFactors 403s `headstart/0.1 (job-board reader)`
   specifically.
2. **No domain and no email.** zwayam's edge answers `curl (92) HTTP/2 stream error` to any agent
   carrying one — measured 2 of 2 attempts on each of four candidates, while `(a/b)`,
   `(contact: maintainer)` and a long domainless phrase were served. **This is what forbids the
   contact URL**, which is the part of this ADR most likely to be re-proposed by someone who has
   not read it.
3. **Not a stock tool default.** zwayam **blackholes** `curl`'s and `python-requests`' own defaults
   — they time out rather than refusing, so a retry ladder reads that as transient and repeats
   forever. Falling back to a library default is worse than any of the above.

`headstart/0.1` sits in the intersection.

**A per-scraper override is a last resort, not the first move.** One was written for zwayam and
then deleted: bare `headstart/0.1` satisfies every host measured, so the override was machinery for
a problem that no longer existed. A future host that genuinely cannot take the shared string earns
one — with its measurement in the file, the way `egress_fallback_on` and `detail_streams` diverge
per scraper today.

**Moving it requires a sweep first.** `scripts/validate/user_agent_sweep.py` runs the real
`fetch_raw()` + `parse()` against live Boards under both strings. This is not ceremony: the first
candidate replacement passed every SuccessFactors check and **broke zwayam**, and only scraping
under both strings found it. The sweep covers 20 of the 22 registered ATSes and **prints the ones
it cannot reach** — `oracle` and `sensehq` have no liveness ledger, so no Board to sample; a
verdict from it is evidence about 20 ATSes, not proof about all of them.

## Consequences

- Hosts get a name and no contact route. That is a real cost, accepted because constraint 2 leaves
  no alternative. If zwayam's edge ever stops rejecting domains, revisit — a contact URL is the
  better string and the only thing standing in its way is measurable.
- `tests/test_user_agent.py` pins all three constraints as **properties**, not as a fixed value, so
  the string can still move. Its `_LOOKS_LIKE_HOST` detector is checked against the exact strings
  zwayam accepted and refused, so it cannot drift into a TLD allowlist — which an earlier draft
  was, wrongly in both directions.
- A repo-wide scan fails if the denylisted literal reappears in any `.py` file outside a short
  allowlist of files that discuss it. This catches the shapes the module-scan cannot: a standalone
  script's hand-synced copy (`scripts/validate/confirm_smartrecruiters_boards.py` keeps one on
  purpose), or a literal inlined into a headers dict.
- The deeper defect is untouched and worth naming: `SuccessFactorsScraper._job_fields` maps a 403
  and an unparseable 200 onto the same `None`, so no downstream log could name the cause. Until
  `report_detail_gaps` carries a status histogram — the way Workday's `failed mid-crawl` line
  already does — the next origin-side refusal will be just as silent as this one.

## Alternatives considered

- **A browser User-Agent.** Works. Rejected: impersonation rather than identification, and
  unnecessary once an honest string was measured sufficient.
- **Leave the string, gate the Boards.** Take the 403 at face value, let the ADR-0064 value gate
  drop the 102 Boards. Rejected: robots.txt permits what we fetch, so there is no stated wish being
  honoured — only ~56,120 postings a run being abandoned to an automated classifier.
- **A per-ATS override for SuccessFactors only.** Smallest blast radius, rejected: it leaves every
  other ATS exposed to the same managed rule, and the way that surfaces is another silent multi-run
  loss on whichever ATS is hit next.
