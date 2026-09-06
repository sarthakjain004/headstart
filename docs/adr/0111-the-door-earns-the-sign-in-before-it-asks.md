# ADR-0111: The door earns the sign-in before it asks for it

**Status:** accepted · **Date:** 2026-09-07 · **Extends ADR-0042 (the wall itself is unchanged)**

## Context

ADR-0042 put the whole app behind Google sign-in and recorded the cost honestly: *"Search and
Trends are no longer anonymous — accepted deliberately, knowing it costs casual visitors."* That
decision is not in question here. What is in question is the page a stranger actually meets.

Fetched live on 2026-09-07, `GET https://imposeidon-headstart-search.hf.space/` returns
`signin.html`: a centred card carrying a logo, a heading, **39 words** of copy, and a Google
button. It does not say who runs it, where the jobs come from, what happens to a Google identity
once handed over, or that the entire pipeline behind it is public source code.

That shape is the problem. Asking for an identity provider's credential before showing any
evidence of value is the *exact* interaction shape of a credential-harvesting page, and users have
been trained — correctly — to distrust it. The wall is doing what ADR-0042 asked, but the door in
front of it converts on trust the visitor has no reason to extend yet.

The asymmetry is what makes this worth fixing rather than accepting. HeadStart is unusually well
placed to answer every question that door leaves open:

- **Provenance** is the product. Every Job is read from a company's own ATS board (22 scrapers),
  and the job link goes to the employer, not to an aggregator.
- **The reasoning is already written down.** 100+ ADRs, a domain glossary, a public CI badge.
- **The claims are externally checkable.** The repository, the pipeline runs, and the ADR that
  justifies any given sentence are all public. A visitor does not have to believe us.

None of that reaches the door. The strongest trust asset the project owns is invisible at the one
moment it decides whether a stranger stays.

## Decision

**The door states what HeadStart is, proves it with numbers it can actually measure, says exactly
what signing in costs the visitor, and links to the evidence — then asks.**

Four blocks, in this order, because the order *is* the argument:

1. **What this is**, in one sentence a stranger can evaluate.
2. **Proof, in live numbers** read from the served table at request time — jobs indexed,
   employers, ATS providers. Not marketing copy: `count_rows()` and two counts off the searcher's
   own boot scan, so a shrunk index shrinks the claim. Every tile must be a number the running
   product can produce; a first cut carried a typed-in "~6h between index refreshes" that was
   roughly 5x the measured cadence (`pipeline.yml`: mean run 74.1 min, chained back-to-back) and
   contradicted the app's own footer. **A tile that cannot be counted does not go on this page.**
3. **What signing in does**, stated as a limit rather than a promise: the session stores an email
   address and nothing else; no posting, no email unless a Saved set asks for it; sign-out drops
   the cookie. Written so that the sentence a visitor most wants — *what do you take from me* —
   is answered before the button, not in a policy page behind it.
4. **Why it can be checked**, with links out to the source, the ADRs, and the pipeline runs.

### Rejected: show live sample jobs on the door

The most persuasive proof would be three real rows. It was rejected because it re-opens exactly
what ADR-0042 closed — anonymous access to the index — through a side door, and one worth having
would grow into an anonymous search. If that trade is ever wanted, it belongs in an amendment to
ADR-0042, decided as an access question, not smuggled in as a design tweak.

### Rejected: soften the wall to "browse anonymously, sign in to save"

The better product, and out of scope. It changes who may read the index, which is ADR-0042's
subject, and it changes the cost model of the free-tier Space (every anonymous search is an
encoder call). Recorded here so the option is not lost.

### Rejected: trust badges, testimonials, counters that only go up

The generic pattern, and wrong for this product. HeadStart has no users to quote and no
certifications to display, and a fabricated one would be the only dishonest pixel on the page.
What it has instead is verifiability, which is stronger and costs nothing to keep true.

## Consequences

- The door renders numbers, so `index()` must pass them on the signed-out path too — previously it
  passed the Google client id alone. The ATS count is read off the searcher's existing boot scan;
  the other two are **per-request table queries** (`count_rows()`, and one filtered count for the
  freshness window, ~5 ms each). An earlier draft of this ADR claimed the door "costs no new
  query", which was wrong, and a later one said one query when there are two. The signed-out path
  is the one with no auth in front of it, so the cost is worth stating plainly rather than
  rounding to zero.
- **Exactly countable, or it is not a tile.** Three drafts failed that bar. A typed-in "~6h
  between index refreshes" was ~5x the measured cadence. An "employers" count of distinct
  `company` values was argued here as a conservative floor and is the opposite — a ceiling.
  Recasting it as a Board count did not save it either: `company` is a board-supplied display
  name on greenhouse, workday, oracle, smartrecruiters, recruitee, workable and teamtailor, and
  ADR-0023's Board key is `{ats}:{slug}` (Workday: `{ats}:{company}/{site}`), so the pair
  `(ats, company)` collapses Workday sites while personio's per-job `subcompany` splits one
  board — the error's **sign is undetermined**, which is worse than either direction.
  `corpus.board_of` is no rescue: ADR-0049 calls it a guess. The tile is now the count of Jobs
  first seen in the last seven days, which is exact — a row without `first_seen` predates the
  column (ADR-0031) and therefore cannot be new, so the window has no unknown bucket — and it
  proves the thing a stranger actually doubts, that the index is alive.
- **A claim the door makes to earn the sign-in cannot be evidenced only behind the sign-in.**
  The eviction point originally ended "the Data tab inside says how, and for how long", which
  puts the proof on the far side of the decision it is meant to inform. The measured figure now
  appears on the door itself.
- The door still cannot fetch `/static` (the wall gates it, and `signin.html` is deliberately
  self-contained), so its styles stay inline. That constraint is now load-bearing on a much larger
  page; the inline block is kept to the tokens the door actually uses.
- The embedded-iframe escape hatch (a Space rendered inside `huggingface.co/spaces/…` cannot
  complete Google sign-in, and its `Lax` cookie would not survive) is unchanged and still runs
  before Google's script loads.
- A claim on the door is a claim the repo must keep true. Every sentence there is either measured
  at request time or backed by a linked ADR; a fourth block of unverifiable copy would undo the
  point of the first three.
