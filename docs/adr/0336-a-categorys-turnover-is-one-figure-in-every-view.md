# ADR-0336: A category's turnover is one figure in every view

**Status:** accepted · **Date:** 2026-09-29 · **Amends:**
[ADR-0227](0227-trends-record-each-boards-opened-and-closed-jobs-not-only-its-net.md) (which runs
turnover leaves out) · **Relates to:**
[ADR-0270](0270-the-index-view-takes-a-counting-change-out-too.md) (the index view takes a
counting change out), [ADR-0272](0272-an-agent-reads-hiring-as-postings-opened-and-closed.md) (an agent
reads hiring as opened and closed), [ADR-0330](0330-trends-are-recomputed-from-recorded-job-facts-whenever-a-rule-changes.md)
(Trends restatement)

## Context

The round-3 critique of the MCP server (P1-3) asked `read_trends` for AI, ML & Data Science two
ways over the same 3.6 days of turnover. As a line of the whole index it read 1,654 opened and
1,821 closed, with 8 runs left out. As its own category view it read 1,254 opened and 1,504 closed,
with 15 runs left out. The raw `/trends` payload carried both figures, so the difference was the
Space's, not the MCP's. A journalist asking both ways got two hiring figures for one category.

The cause was one argument. The Space leaves a counting change's runs out of turnover Board by
Board (`netting.left_out_runs`, ADR-0227). A category's own view is a Level breakdown, and the
Space passed it `bands=True`, so an extraction change (`derivations_version`, "experience
reading") counted as moving every line. Its run and the run after it came out of every level line,
and so out of the first row, which sums them. The index view passes no levels, so it kept those
runs. ADR-0227 had named this difference and bounded it ("only that ordinary hiring differs"), but
never stated that the first row was left inconsistent with its own net: the first row's net keeps
an extraction change's jump (`bands_only` notes never reach a total), while its turnover left the
run out.

An extraction change never opens or closes a job. `job_turnover` books a Job whose band changed
as Recounted out of its old key and into its new one, so an extraction run's opened and closed are
ordinary hiring on the category. Only a level line takes the run's whole jump out of its net, so
only a level line must leave that run's turnover out to keep its parts adding up.

## Decision

**A line's turnover leaves out exactly the runs its own net takes out whole.**

- The Space's Board-by-Board rule (`left_out_runs`) no longer depends on the breakdown. A category's
  turnover, and `turnover_left_out`, are the same whether the index or the category's own view reads
  it.
- On a Level breakdown with no pick, a level line also leaves out each run a change that re-sorts
  levels lands on, and its settling run (`_hiring_turnover`, keyed on the note's new
  `re_sorts_levels`, which also covers a change that removes duplicates and re-reads experience at
  once). The first row does not. A pick's lines already worked this way, through their jumps.
- `read_trends` keeps the residual difference visible: when a Level breakdown's lines add up to
  other turnover than its first row, it says so in one line, with both figures ("The levels add up
  to 1,299 opened and 1,559 closed, not the first row's 1,699 and 1,876: each level also leaves out
  the runs an experience-reading change re-sorted levels on, which the first row keeps.").
- The agent contract moves to 12: `/trends` answers a category view's first row differently.

## What the Trends tab shows, before and after

Measured on the history refreshed from HF at the 2026-09-29 10:30 tick, window from 2026-09-22,
over every one of the 25 categories:

- **Before:** a category drill's first row leaves out 31 runs and the index table leaves out 20.
  All 25 categories differ. AI, ML & Data Science reads 1,299 opened and 1,559 closed in its drill
  and 1,699 and 1,876 as the index table's row.
- **After:** both leave out the same 20 runs, and all 25 categories read the same opened and closed
  both ways (AI/ML: 1,699 and 1,876). The level rows are unchanged (AI/ML's add up to 1,299 and
  1,559, as before), so in a drill the level rows' Opened and Closed now add up to less than the
  first row's. Before, both were short by the same runs. Every reading still reconciles.

A company's views are unchanged: a pick never went through `left_out_runs`.

## Consequences

- `tests/test_space_app.py` pins the parity on the fixture: for every category, `/trends?family=f`'s
  first row equals `/trends`' line for f, with an extraction change alone and combined with
  duplicate removal. It fails on the old rule. The real-history check above is a local script, not
  a test, since `data/state/` is not in the repository.
- ADR-0330's restatement recomputes the counts the tab reads. It does not touch this rule, which
  applies to whatever counts it reads.
