---
name: add-ats-scraper
description: Add a scraper for a new ATS end to end — live measurement, scraper and tests, liveness probe, tenant discovery, committed ledger, docs, reviewed PR. Use when asked to add, build or support a new ATS, job-board platform or careers provider, including when the answer may be a dead end or a disabled-on-arrival scraper.
---

# Add an ATS scraper

A scraper here ships as one PR carrying seven things that must agree: the measurement, the
scraper, its tests, a liveness probe, a tenant pool, the committed ledger the pipeline scrapes
from, and the docs that quote the ledger's numbers. Reference builds: `pyjamahr` (#560),
`bamboohr` (#505), `gem` (#504), `phenom` (#501) — `git show --stat` shows each footprint.

The leading word is **measured**. Every claim that reaches code, a docstring or a doc carries a
number and a sample size, taken from the live host in this session. Upstream code, vendor docs,
earlier notes and this repo's own TODO entries are **hypotheses** — each one earlier builds
adopted unmeasured was wrong somewhere (phenom's "CSRF required", bamboohr's "`locationType 2` is
remote", oracle's `CX_1`, icims' `lastmod` dates).

A **checkpoint** is where you stop and report to whoever invoked you, because the decision there
is a design fork (CLAUDE.md §1, §6). When your brief already settles that decision, record it and
continue.

## 0. Set up

- Work in your own worktree off `origin/main` (`git worktree add … -b add-{ats}-scraper
  origin/main`). Prefix every hand-run `python` with `PYTHONPATH=src`: a bare `python` imports
  the main checkout's code.
- **Use the ATS key the repo already uses.** Grep `data/ats-tenants-merged/`,
  `scripts/discover/fingerprint_careers.py` and `scripts/merge/consolidate_harvested_lists.py`
  for the provider; the pool file's stem, the liveness prober and the fingerprinter's
  `SUPPORTED` set all key on it (Cornerstone is `cornerstone`, not `csod`).
- **Gitignored inputs live only in the main checkout.** Copy
  `<main checkout>/data/ats-tenants-merged/{ats}.csv` (and
  `data/wayback-ats/{ats}.csv`) into your worktree when they exist — every script resolves paths
  against the worktree it runs in. When no pool exists yet, step 3 creates it.
- Keep scratch files under a directory named for the ATS; sibling agents share the scratchpad.
- Claim an ADR number: one past the highest in `git ls-tree origin/main docs/adr/` **and** every
  open PR's `docs/adr/` diff. Parallel builds take the number their caller assigns.
- `grep -rn {ats} tests/` — a test that uses your ATS name as its "no scraper" example must
  change with you (#505 edited `test_relocate_dead_boards.py` for exactly this).

**Done when** the worktree is on a fresh branch, holds any existing pool, and you hold the ATS key
and an ADR number no one else holds.

## 1. Gather the hypotheses

Write every existing claim about this ATS into `experiment/{ats}-{surface}/LOG.md`, one line
each, to be confirmed or killed in step 2:

- `CLAUDE.md`'s build list and `experiment/ats-scraper-candidates/LOG.md` (tech %, pool size).
- Upstream implementations: `kalil0321/ats-scrapers` (`src/ats_scrapers/scrapers/{ats}.py`,
  seed list `ats-companies/{ats}.csv`, MIT) first, then `gh search code` / `gh search repos`.
  Record each endpoint, parameter and hardcoded constant.
- The ATS's public developer or help docs.

**Done when** the LOG lists every candidate surface (listing, detail, discovery) and every
upstream constant or assumption as an open hypothesis.

## 2. Measure the live API

Answer every question in [measurement.md](measurement.md) against real tenants, sampling **both
sides** of each discriminator (live and dead, empty and full, small and the largest Board you can
find). Raw captures go in `experiment/{ats}-{surface}/artifacts/`; the write-up is
`docs/{ats}/{YYYY-MM-DD}_{surface}-measurement.md`. The experiment folder is the build's local
notebook and stays out of git (the user's call: in the repo it is noise), so the write-up stands
alone — every number a reader needs is inline, and the scripts and captures are named as kept
locally.

**Done when** every question in measurement.md has a number and a sample size, or an explicit
"not measurable, because …".

**Checkpoint.** Report the slug identity, the listing and detail surfaces chosen (and why the
others lost), the dead-versus-empty rule, the tech-gate verdict, the rate-limit knee and the
discovery plan — or, if the ATS is unbuildable (login wall, per-tenant auth, no public surface),
the evidence for a **dead end**. A dead end ships as a docs-only PR: the measurement doc plus a
dead-end line in `CLAUDE.md`'s build list.

## 3. Build the tenant pool

Union every reachable source into `data/ats-tenants-merged/{ats}.csv` (`ats,tenant,url,source`):
the existing pool, the upstream seed list, any vendor-published roster, a Wayback sweep and a
Common Crawl sweep — commands and feeder entries in [wiring.md](wiring.md#discovery). Wayback's
CDX queues concurrent requests from one IP, so when sibling builders are running, sweep only in
the slot your caller gives you, and build the scraper while it runs.

**Done when** every row's `tenant` is in the slug shape the scraper keys on, you can state how
many tenants each source contributed and how many only it found, and the final pool is copied
back to the main checkout's `data/ats-tenants-merged/{ats}.csv`.

## 4. Build the scraper, test-first

Invoke the `tdd` skill. Record real responses (trimmed, never invented) as fixtures in
`tests/fixtures/`, write `tests/test_{ats}.py` against them, then
`src/headstart/scrapers/{ats}.py`. The contract, class attributes and every file that changes
alongside the module are in [wiring.md](wiring.md#scraper).

The module docstring is where the measurement lives in code: each non-obvious choice states the
number that forced it, as `pyjamahr.py` and `icims.py` do. A choice with no number behind it goes
back to step 2.

**Done when** `pytest -q`, `ruff check` and `ruff format --check` are green on the whole repo;
`PYTHONPATH=src python scripts/validate/verify_scraper.py {ats} 20` scrapes real Boards without
error; and a script over `get_scraper("{ats}", slug).fetch()` on the largest Board prints each
`Job` field's non-null share, each matching step 2's table.

## 5. Probe liveness and commit the ledger

Add `p_{ats}` to `scripts/validate/check_liveness.py` with tests, run it over the pool, and commit
`data/validate/liveness/{ats}.csv`. Details: [wiring.md](wiring.md#liveness).

**Done when** the ledger is committed, its job counts vary across Boards (a constant is a
fallthrough), every `dead` verdict rests on a response you have seen a real dead tenant give, a
spot check of 5 `live` and 5 `dead` rows against the host agrees, and the three duplicate-Board
mechanisms in CLAUDE.md §"Checking a liveness ledger for duplicate boards" are each checked.

## 6. Decide whether it scrapes

Estimate the pool's storage cost per tech Job: Hiring Boards × postings × bytes per posting
fetched, divided by the tech Jobs it yields (`headstart.jobs.tech_filter.is_tech(title, department)`
over a real sample; `scripts/validate/ats_tech_yield.py` is title-only and handles four ATSes).
The accepted bar is ADR-0158's jazzhr: ~10.7 GB for ~5,100 tech Jobs, about **2 MB per tech
Job**. At or under it the ATS lands active; over it, it lands in `DISABLED_ATS` with the
arithmetic in the comment, as jazzhr and jobvite first did.

**Done when** the decision and its arithmetic are in the ADR. **Checkpoint** if the ATS lands
disabled, or its cost is within 2x of the bar either way.

## 7. Write it down

Per [wiring.md](wiring.md#docs): one ADR carrying every choice a reader would otherwise
re-litigate (slug identity, surface, dead-versus-empty rule, tech gate, enable decision); the
ATS's line removed from `CLAUDE.md`'s build list; `README.md`'s scraper count and list; every Board
figure in `README.md` and `CONTEXT.md` recomputed; CONTEXT.md's Detail-pass entry. Then invoke
`verify-search-filters` in this PR (CLAUDE.md requires it): `URL_SHAPES` is generated from
`url_shape` (ADR-0157), so its coverage gate passes now. Its live-row checks can only see rows the
Space serves, which a new ATS has none of until the pipeline runs. Without a fresh session
cookie (`~/.headstart_session`, a Google sign-in only the user can do) the harness exits 2
before any check, coverage gate included, so ask the user to refresh it; if they defer instead,
name the whole harness as a post-pipeline follow-up in the PR and at the pre-merge
checkpoint.

**Done when** `pytest tests/test_board_counts.py` is green and every number you wrote traces to a
command you ran.

## 8. Ship

Commit, push, open the PR. Invoke the `code-review` skill against the merge-base, apply or
explicitly defer each finding, then invoke it **again** — the second round reviews the first
round's fixes. Pipeline data moves only through the pipeline's own schedule: leave
`pipeline.yml`, `deploy-space.yml` and `bench-tech-gate.yml` undispatched.

Merge **alone**. Immediately before `gh pr merge --squash`, fetch `origin/main`; if it moved,
rebase, renumber a colliding ADR file by file, recompute every Board figure from the merged tree,
and re-run the full suite. Other sessions land ledger rows on main too, so the figures move
between your checks. Confirm the PR's CI actually ran — GitHub silently drops `pull_request`
events, runs nothing on a conflicting PR, and zero checks is not a pass.

**Done when** the PR is merged and CI on `main` is green. **Checkpoint** before merging when your
caller asked to serialize merges: stop at "review round two applied, rebased, green" and report.
