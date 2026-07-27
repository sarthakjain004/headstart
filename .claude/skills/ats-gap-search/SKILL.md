---
name: ats-gap-search
description: Spin up a resilient discovery agent that finds company Boards one ATS is missing, verifies each against the real API, and lands them in the liveness ledger. Use when asked to close discovery gaps, find more Boards or companies for an ATS, or grow coverage for greenhouse/workday/zoho/lever/ashby/eightfold/smartrecruiters or any other provider.
---

# ATS gap search

Launch one subagent per ATS to close its Board-discovery gap. Discovery is where coverage
actually grows: a mature ATS is limited by the candidate list, not by the liveness probe.

**One agent per ATS, never one agent for several.** They write to different ledgers and run for
tens of minutes; a single agent serialises what should be parallel and risks cross-ATS edits.

## Pick the target

Rank by **yield per Board**, not by corpus share. A newly-found Eightfold Board is worth ~290 tech
jobs; a median Workday Board is worth 5. Compute it from the two ledgers rather than assuming:

```bash
# live Boards per ATS
for f in data/validate/liveness/*.csv; do
  printf "%-16s %6s\n" "$(basename $f .csv)" "$(awk -F, '$4=="live"' $f | wc -l)"
done
# tech yield per Board — data/state/board_priority.csv from HF, grouped by ATS prefix
```

A small known set on a high-yield ATS is the strongest signal: it means discovery has barely been
attempted and the ceiling is far away.

## Brief the agent

Research these four before writing the brief. A brief that gets them wrong produces unusable output:

1. **Baseline** — rows and live count in `data/validate/liveness/{ats}.csv`. Measure it; state it.
2. **Slug shape** — read `CONTEXT.md`. It is ATS-specific: a bare label for most, a **host** for
   Zoho and SuccessFactors, a **full URL** for Workday. Say which, and say what the ledger's
   `tenant` and `url` columns must hold.
3. **Verification endpoint** — read `src/headstart/scrapers/{ats}.py` and quote its real request
   shape. This is how the agent proves a Board exists.
4. **Existing miner** — does `scripts/discover/mine_{ats}.py` exist, and what does its docstring say
   it does *not* cover? Those stated gaps are the highest-value targets.

Then compose the brief from [brief-template.md](brief-template.md), which carries the standing
process, and point the agent at [techniques.md](techniques.md) and [resilience.md](resilience.md).

## Supervise

The agent runs long and will be interrupted. Your job while it runs:

- **Watch for stalls.** A dropped connection reports as "API Error: Response stalled mid-stream" or
  "Connection closed mid-response" — usually the machine sleeping, not agent error. Resume with
  `SendMessage`; the transcript survives.
- **On resume, tell it to land first.** State what is on disk (ledger rows, staged candidates) so it
  resumes from fact rather than memory, and have it checkpoint before mining further.
- **Check progress from the filesystem**, never by reading the agent transcript: ledger row deltas,
  candidate-file mtimes, new scripts under `scripts/discover/`.
- **Audit job counts for constants.** A probe that emits the same number for many Boards is a
  fallthrough. See [resilience.md](resilience.md#fallthrough-detection).

## Land the results

Commit only ATSes whose agent has **finished**. Files still being written by a live agent belong in
a later commit.

Verify before committing:

- The ledger diff is **additions only** — a discovery run has no reason to alter or drop rows.
- Job counts vary across Boards.
- Row count equals unique-Slug count.

`data/validate/liveness/` is committed to git and is authoritative; `data/wayback-ats/` is gitignored
scratch. A miner worth keeping is promoted to `scripts/discover/` with a docstring saying what it
does and why it works.
