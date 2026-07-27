# Agent brief template

Fill the four researched facts from [SKILL.md](SKILL.md#brief-the-agent); the rest is standing
process. Keep the whole brief in the spawn prompt — a subagent cannot ask follow-up questions before
it starts.

---

You are doing **Board discovery for {ATS}** in the HeadStart repo at `{repo path}`. Find company
Boards the project doesn't have, verify them, and land them in the liveness ledger. Be relentless:
when an approach fails, diagnose why and try another.

**Scour the internet freely** — web search, careers pages, aggregators, corporate and startup
directories, certificate-transparency logs, GitHub, press coverage. Follow leads opportunistically.

## Vocabulary

`CONTEXT.md` is the authority. **Board** = one Company's openings hosted by its ATS. **Slug** = the
identifier locating a Company within its ATS. **Company** = the employer. Write those words; the
ledger's column is named `tenant` for historical reasons — leave the column alone.

## Read first

`CLAUDE.md`, `CONTEXT.md`, `docs/discovery/overview.md`, `docs/learnings.md`,
`src/headstart/scrapers/{ats}.py`{, `scripts/discover/mine_{ats}.py`}.

## Baseline

`data/validate/liveness/{ats}.csv`: **{N} rows, {M} live.** That set yields **{J} tech jobs — {Y}
per Board**. {One sentence on what the ratio implies: a small set on a high-yield ATS means
discovery has barely been attempted and the ceiling is far away.}

## Slug format

{Bare label / full host / full URL}, appearing as `{example}`. The `tenant` column holds {…} and
`url` holds {…}. {If host or URL: dedupe on the full value — a bare label collapses distinct
Companies.}

## Verification

{Exact endpoint and request shape, quoted from the scraper.} Match the scraper's real behaviour
rather than inventing a request. Never record a `live` status with a job count you did not observe.

## Where to look

{Two or three ATS-specific angles — who buys this ATS, and which namespace is enumerable.} Consult
[techniques.md](techniques.md) for the full ranked set and the measured duds; it will save you from
re-running techniques already proven exhausted.

## Standing process

1. **Establish the real baseline** yourself — known Slugs, live/dead/unknown split.
2. **Mine from several independent angles.** Each is blind to what the others surface.
3. **Dedupe hard** against everything already in the ledger.
4. **Verify every candidate** against the real endpoint and record the count you observed.
5. **Land it.** Write candidates to `data/wayback-ats/{ats}.csv`, then run
   `python scripts/validate/check_liveness.py` and let *it* write the ledger. Never hand-edit the
   ledger.

**Checkpoint every batch, not once at the end.** `data/wayback-ats/` is gitignored scratch; only the
ledger is durable. Runs get interrupted, and an agent that staged 1,568 candidates without landing
them lost its entire session while the ones that checkpointed lost nothing.

**Do all work inline** — a sub-agent inherits the same failure modes and takes its work down with it.

Read [resilience.md](resilience.md) before you hit your first block. It covers block triage
(challenge vs rate limit vs IP block — three problems needing opposite responses), WARP IP rotation
in proxy mode, and fallthrough detection.

## Constraints

- **{ATS} files only.** Sibling agents are live in this tree. Leave `scripts/merge/merge_tenants.py`
  and shared files like `cc_miner.py` alone — flag what needs changing instead of editing it.
- No commits, pushes, or PRs.
- Stream output as work completes (`print(..., flush=True)`, `as_completed` over a blocking `map`).
- Politeness binds (ADR-0026): cap concurrency, back off on 429/5xx.
- Captures under `experiment/ats-gap-{ats}/artifacts/` with a `LOG.md` of what you tried and what
  each attempt yielded.
- A miner worth keeping goes to `scripts/discover/mine_{ats}.py`, ruff-clean, with a docstring saying
  what it does and why it works.

## Report back

Baseline, NEW Boards found, how many verified live, jobs they represent, yield per technique, and
what failed and why. Only numbers you measured.
