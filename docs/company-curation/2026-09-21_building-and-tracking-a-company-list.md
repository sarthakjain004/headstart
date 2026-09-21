# Building a company list, and tracking jobs across it

*Research date: 2026-09-21. Question: how does a user assemble the set of companies they care
about, and then follow openings across it, with as little friction as possible? Follows
[the trimming research](2026-09-21_trimming-the-served-company-set.md), whose decisions scope this:
the quality gate, follow/hide, and scrape trimming are in; firmographic discovery is out; nothing
is ever evicted from the index.*

Numbers below come from the same sources and carry the same freshness caveat: the live
`board_priority.csv` (2026-09-21) and a local `data/lancedb` snapshot pulled 2026-09-15.

## What the measurements force

### A name is not a lookup key here

Typing a company name and substring-matching the `company`/slug field — the obvious picker — does
not work on this corpus. Measured against 28 well-known employers:

| typed | what substring matching returns |
| --- | --- |
| `ramp` | 13 Boards, including `successfactors:careers.grampianshealth.com`, `greenhouse:rampanttechnologies`, `recruitee:onramper` |
| `cred` | 37 Boards, mostly farm-credit unions (`greenhouse:capitalfarmcredit`) |
| `uber` | 13 Boards, including `darwinbox:bitkuber` and `greenhouse:schubergphilis` |

`search.py` already documents this failure mode on its `_like` helper — company `"100%"` matched 30
rows where exactly one was right. Six of the 28 (`zerodha`, `swiggy`, `flipkart`, `shopify`,
`github`, `hashicorp`) return **nothing**, a mix of genuine coverage gaps and the non-derivable-slug
class CLAUDE.md already tracks. So the picker fails in both directions at once: it offers the user
farm-credit unions and it cannot offer them Shopify.

### One company is several Boards, and the user must never see that

Of the 22 that were reachable: NVIDIA sits on 4 Boards, Canva on 5 (including a casing duplicate),
OpenAI on 3, Atlassian on 3 separate iCIMS tenants (`careers-apac-`, `globalcareers-`,
`campus-globalcareers-`). A follow list keyed to a `board_key` silently misses most of an
employer's openings. **Following "Atlassian" has to mean all three tenants**, and that grouping has
to exist before the picker does.

### A small hand-picked list is sparse, and a big-name list is a firehose

Measured on the snapshot: **40.9%** of Boards gained a row in the last 7 days; **25.2% gained
nothing in 30 days**. Among Boards that did move, the median added **2** jobs in the week (p90: 17).
A user who hand-picks 30 ordinary companies will watch a quarter of their list sit silent for a
month — which reads as a broken product, not a quiet market. A user who picks 30 giants gets
thousands of rows.

*Caveat on those figures:* `first_seen` is when **we** indexed a row, not when the employer posted
it, and ADR-0143 records that a newly discovered Board contributes its whole existing inventory at
once. Per-Board "new" therefore over-counts on recently added Boards. The design consequence —
sparse lists look dead, big lists flood — holds regardless of the inflation.

The consequence is the important one: **following a company is the wrong unit.** The unit is
*company × the role you actually want*. Following NVIDIA cannot mean 1,931 rows. This is already
the shape of ADR-0043's Saved Sets — a query plus filters, re-run on each visit — so a watchlist is
a **company constraint on a Saved Set**, not a new subsystem.

### Tracking only became viable recently

`first_seen` is now **87% non-null** on the snapshot. The README still records it as 77% *null* on a
1,000-row sample from 2026-08-18. A "new since you last looked" feature was not buildable a month
ago and is buildable now. That README figure should be re-measured and corrected.

## The resolver is the feature; the picker is the easy part

Everything below assumes a **Company directory** built offline, once: one row per real employer,
carrying a display name, aliases, a domain where known, and the set of `board_key`s that belong to
it. The picker then searches *that*, never the live job rows. Building it is the same work the
trimming research already identified as the prerequisite — widening `company_name.PATTERNS` beyond
today's 19.7% of rows, an LLM naming pass through the router for the rest, and a cross-Board
grouping rule. Without it every idea below degrades into the farm-credit-union problem.

## Ways to put a company on the list, ranked by friction

**1. From a result row — one tap, zero typing.** Every row already knows exactly which Board it
came from, so "＋ Track this company" has *no resolution risk at all*. It is the only mechanism
that is immune to the naming problem, and it builds the list as a by-product of searching, which
the user is doing anyway. This should be the default path and it is nearly free to build.

**2. Paste the list you already have.** Most people looking for work already keep 20–60 target
companies somewhere. A textarea taking "Stripe, Ramp, Vercel, …" and returning three buckets —
matched, ambiguous (pick one), not found — converts an existing artifact in one action. The
ambiguity bucket is where the resolver's honesty shows: offer the three candidates, never guess.

**3. Paste a careers URL or a domain.** `scripts/discover/fingerprint_careers.py` already resolves
a careers page to an `ats:slug`. Exposing it means a user can follow **any** company, including one
we do not yet cover — and their request becomes a discovery lead. Nobody else can do this, because
nobody else's index is built by fingerprinting careers pages. It also converts the six misses above
from dead ends into intake.

**4. Curated bundles, one tap for fifty companies.** "AI infrastructure", "YC-backed", "Indian
fintech", "remote-first", "FAANG and peers". This is the direct answer to the sparsity measurement:
it gets a list past the density where the feed looks alive, in a single action, on day one. Bundles
are also cheap to maintain by hand and easy to make honest ("47 of 60 have a Board we can read").

**5. Seed from the résumé already parsed.** The Profile extraction (ADR-0041, via the router)
already pulls employers and skills. Proposing a starting list from past employers and their obvious
peers turns an empty state into a reviewable draft. Propose, never auto-follow.

**6. Expand from what they already track.** "You track Stripe — also Adyen, Checkout.com,
Razorpay?" Company centroids over the job vectors already stored would do it, and `role_centroids`
shows the shape. Cheap *given* the directory; meaningless without it.

**7. Subtractive curation — hide, don't pick.** A "hide this company" control on every row builds
the inverse list at zero effort and needs no directory at all, since hiding is Board-scoped by
nature. It is the one mechanism that works *today*, unchanged, and it is what users of other
products actually reach for (HideJobs exists solely to do this on LinkedIn, Indeed and five other
boards).

## Tracking, once the list exists

**Reuse the Saved Set, don't invent a Watchlist object.** A tracked set is an existing Saved Set
plus a company constraint; it inherits the Matches tab, the re-run-on-visit semantics, and email
and Telegram alerts. The new storage is a per-account company list and a filter clause.

**Lead with what changed, using `first_seen`.** "6 new at 3 of your 40 companies this week" is the
whole product in one line, and it is exactly what Teal, Huntr and Simplify cannot do — they track
applications the user already found, not companies the user is waiting on.

**Say "quiet", never show an empty box.** A quarter of Boards add nothing in a month. A company
that posted nothing must read as *quiet since 12 Aug*, distinct from *we could not read this board*
and from *no match for your role filter*. Three different silences, three different messages — this
is the repo's own "publish the limits next to the result" commitment applied to a tracking feed.

**A company page that unifies its Boards.** Atlassian's three iCIMS tenants and NVIDIA's four
Boards must read as one employer, or the directory work buys nothing the user can see.

## Recommendation

Build **1** and **7** first — track-from-row and hide-from-row. Both are one control on a result
row, both are immune to the naming problem, both work before the Company directory exists, and
together they cover the two things users actually do. Ship the tracked set as a company constraint
on a Saved Set so alerts come free.

Then the **Company directory**, because 2, 3, 4, 5 and 6 are each small features on top of it and
impossible without it.

Then **bundles** (4) and the **careers-URL intake** (3) — the first fixes the cold start the
sparsity data predicts, the second is the genuine differentiator and feeds discovery.

## Open question

Which entry path is the primary one to design around: the user who **discovers companies while
searching** (mechanism 1, list grows slowly, always accurate), or the user who **arrives with a
list already in mind** (mechanisms 2–4, list is complete on day one but every name must be
resolved)? Both get built eventually; the answer decides whether the Company directory is a
prerequisite or a follow-up.

## Status (2026-09-21)

Superseded in part by [the Hot-companies tab](2026-09-21_hot-companies-actively-hiring.md), which
shipped first and is the **front door** this document called for: a user with no list browses
ranked companies and tracks from a row, instead of typing a name into a picker that returns
Grampian Health for "ramp". Mechanisms 1 (track from a row) and 7 (hide from a row) remain the
next build; the Company directory is still the prerequisite for 2-6.
