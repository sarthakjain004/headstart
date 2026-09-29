# ADR-0333: Visa sponsorship and relocation are read from descriptions by rules, at query time

**Status:** accepted · **Date:** 2026-09-29 · **Relates to:**
[ADR-0320](0320-a-description-keywords-rows-are-found-once-literal-first-and-named-by-row-id.md)
(a description read once a process),
[ADR-0322](0322-a-category-spans-the-index-and-an-agent-filters-by-age-experience-and-employer.md)
(agent-only filters, family tables),
[ADR-0277](0277-an-agent-reads-a-posting-by-id-and-finds-jobs-like-one.md) (`/job`),
[ADR-0324](0324-an-agent-reads-what-a-roles-postings-ask-for-counted-over-a-sample.md) (the
requirements view), [ADR-0253](0253-an-agent-reads-the-spaces-read-routes-through-a-read-scoped-token.md)
(the agent contract, now 14) · no served-table schema change, no stored-data change

## Context

The round-3 critique of the hosted MCP server (2026-09-29, 7.4/10) found its one P0 in the
question a new graduate or someone moving country asks first: "which jobs sponsor visas, which
offer relocation". The only tool for it was a description keyword, and a description that says
"sponsorship" says it mostly to refuse it. `search_jobs` with `keyword "sponsorship"` reported
"4,806 jobs match"; of its top five, two offered sponsorship and three refused it, and of 40 more
read through `/job`, about 36 refused. Nothing in the tool warned of it, and eval t13 passed while
checking only that a description search ran.

The critique's fix was a derived column extracted at ingest: a served-table schema change and a
`DERIVATIONS_VERSION` bump, which need the owner. The brief asked for the answer without one.

## Decision

**1. The rules** (`headstart.jobs.work_authorization`). `stances(description)` names what a
description states among three **work-authorisation stances**: `offers_sponsorship`,
`refuses_sponsorship`, `offers_relocation`. It reads only the sentences around a topic word
(`PREFILTER`: sponsor, visa, relocation, citizen, immigration, work authorisation or permit, H-1B,
"U.S. person"), and each mention in a window cut to its own clause: at most 70 characters either
side, no further than the nearest field label ("Relocation: Domestic VISA Sponsorship: No"), a
field's own label kept with its answer ("Is Sponsorship Available? No"), a full stop after an
initial ("U.S. citizen") ending nothing. Then:

- **refuses_sponsorship**: a negation in the window ("not", "without", "unable", "no", "n't"), or
  "now or in the future", or a citizenship requirement ("Must be a U.S. citizen", "U.S. citizenship
  is required", "U.S. persons only") outside an equal-opportunity sentence;
- **offers_sponsorship**: an offering word ("available", "we sponsor", "will consider sponsoring",
  "support", "cover") with no negation, **and** the window naming what it sponsors (a visa,
  immigration, H-1B, work authorisation, "a new applicant") or a field answering yes. "Sponsorship"
  alone is often a sales or mentoring word ("executive sponsorship", "horizontal sponsorship"), and
  an offer read wrongly is the costly error. "Sponsorship for this role is not guaranteed" offers
  it to some. A description that both offers and refuses holds only the refusal;
- **offers_relocation**: relocation assistance, a package, support, a stipend, "Relocation: Yes",
  with no negation and no "at your own cost"; a move asked of the candidate ("willing to
  relocate") or a thing moved ("water main relocation") is none.

Words that are not about a visa are left out whatever is near them: company-sponsored benefits,
executive and project sponsors, athletic or certification sponsorship, a sponsor for a security
clearance, "Visa" in title case after help words (the card network's staff).

**2. A query-time filter, read once a process** (`headstart.serving.work_authorization_rows`).
`WorkAuthorizationRows.start()`, called from `JobSearch.warm()` at boot, reads in a thread of its
own every description `PREFILTER` matches (LanceDB's regex picks them), in batches of 8,192, and
keeps each stance's Job ids. `SearchFilters.work_authorization` names a stance;
`IndexCapabilities.work_authorization_clause` compiles it to `id IN (…)` over that stance's ids,
so `build_filter` reads it like any other clause: on the served table, on a description keyword's
rows read into memory (ADR-0308), on a family table (ADR-0322). An unknown stance is a 400
whatever `strict` says (the page never sends one); a stance asked before the read finishes waits
up to 10 s, then is a 503 saying to try again shortly; a failed read is a 503 too, never an empty
answer. It is in `facets.AGENT_ONLY`, so it can be the Blocking filter.

**3. The answers say it** (agent contract 14). `/job` carries each Job's `work_authorization`:
its stances and up to five `mentions`, the sentences about sponsorship, visas, work
authorisation, citizenship or relocation (equal-opportunity sentences left out), read from the
whole description, not the 12,000-character cut. `/requirements` counts each stance over the
sampled Jobs with a description. In the MCP server: `search_jobs` takes `work_authorization`,
says it in the scope line as read by rules, and its description says to use it, never `keyword`,
for visas; a `keyword` naming sponsorship, visas, citizenship, clearance, work authorisation or
relocation adds a line with the hand-read rate (80 of 100 descriptions mentioning sponsorship
refused it; 14 of 63 mentioning relocation said none is offered). `get_job` prints the stances
and a `Mentions:` line of quoted sentences before the description, charged to the descriptions'
budget. `role_requirements` gives each stance's share.

**4. `search_jobs`' description is cut to what an agent must know before choosing arguments**
(round-3 P2-10: 2,038 of 2,048 characters). How `keyword` matches, how the salary bounds and
`max_years` treat a range and a job stating no experience, how `company` beside `category`
resolves and how `sort salary` orders moved into those properties' own descriptions.

## Measurement

**Labelled sample: 660 descriptions read by hand**, by subagents blind to the rules' output, from
the served table pulled 2026-09-29 (500,167 rows). The labels, with the text the rules read, are
`tests/fixtures/work_authorization_labelled.jsonl`; three labels were corrected after reading the
text ("Available for Work Visa Sponsorship? No" labelled none twice; "This position offers
relocation" labelled none once).

| Sample | Rows | How drawn |
| --- | --- | --- |
| random | 200 | any description matching a topic word; half tuned on, half held out |
| random-sponsor-visa | 100 | descriptions naming sponsor or visa; tuned on |
| predicted-offers-v0 | 120 | offers of either kind predicted by an early version; tuned on |
| predicted-v1-frozen | 120 | 60 sponsorship offers, 30 refusals, 30 relocation offers, drawn after freezing v1 |
| predicted-offers-v2-frozen | 120 | 70 sponsorship offers predicted by the final rules, 50 likely offers they did not predict, drawn after the last change |

On the last sample, drawn and read after the rules stopped changing: **offers_sponsorship
precision 68 of 70 (0.97)**, and of the 73 offers the whole sample holds, the rules found 68
(recall 0.93). The two wrong ones: an offer limited to Principal-level roles on a Senior role,
and "work permit" in a construction job. v1 had measured 0.885 (54 of 61) on its own frozen
sample, wrong mostly on "executive" or "customer sponsorship"; requiring the offer to name a visa
took that sample to 54 of 55 with no offer lost.

Over all 660, the final rules:

| Stance | Precision | Recall |
| --- | --- | --- |
| offers_sponsorship | 0.98 (208 of 212) | 0.96 (208 of 217) |
| refuses_sponsorship | 0.97 (251 of 258) | 0.97 (251 of 259) |
| offers_relocation | 0.99 (177 of 178) | 0.99 (177 of 179) |

On the untuned random half alone (100 rows): offers 4 of 4 (recall 4 of 5), refusals 41 of 42
(recall 41 of 41), relocation 17 of 17. `tests/test_jobs_work_authorization.py` holds the rules to
these rates on the fixture.

**Cost.** On the served table: 134,327 descriptions match `PREFILTER`; read and classified in 29–36
s on a laptop (71 s once while other work ran), 0.25 ms a description after finding topic words
by `str.find` (a case-insensitive alternation over each whole description had cost three times
every rule after it). 65,111 refuse sponsorship, 4,253 offer it (4,017 with the final rules),
17,372 offer relocation. Named by id, 65,106 refusals counted in 0.11 s and ranked a page in
0.12 s, and 4,447 offers in 0.02 s (LanceDB 0.36, two threads).

## Considered

- **A column derived at ingest** (the critique's fix): precise and free at query time, but a
  schema change, a `DERIVATIONS_VERSION` bump and a re-derivation of every row, which need the
  owner. The rules are the same module either way; moving them to ingest later changes where
  `stances()` runs, not what it says.
- **SQL regexes in the where-clause**: no boot read, but Rust's regex has no clause-level
  negation, and a window regex per mention over 500,000 descriptions costs seconds per count.
- **`_rowid IN (…)`** as ADR-0320 names rows: faster, but a row id names another row in a
  keyword's in-memory rows and in a family table; `id IN` measured fast enough.
- **An LLM reading each description**: better on the hard cases (offers for other locations,
  hedged company-wide boilerplate) but a cost per row and a router dependency at boot.

## Deferred

- Offers limited to other locations or levels ("sponsorship only within the Netherlands",
  "Principal-level and above") read as offers; a location-aware rule would need the Job's own
  place. A requirement to hold a local work visa ("valid Japanese work visa required") is not read
  as a refusal. A clearance that implies citizenship without saying so is none.
- The search page has no control for the stance; the website is unchanged.

## Consequences

- A "which jobs sponsor visas" question is answered by what the descriptions say, not by the word
  appearing in them, and every answer names the reading as text-derived and fallible.
- Every boot spends about a minute of one of the Space's two CPUs reading descriptions in the
  background; a stance asked in that minute is refused as not ready.
- Eval t36 judges the answer's truth: no job the answer names may be one whose description the
  Space reads as refusing sponsorship.
