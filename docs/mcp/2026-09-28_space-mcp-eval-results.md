# Space MCP server: first live evaluation (2026-09-28)

This is the dated summary that plan §9 (`2026-09-28_space-mcp-server-plan.md`) asks for. It records
the first live run of the `headstart-space` MCP server's evaluation against the deployed Space, the
tool-description changes the transcripts led to, and the one run of the sealed held-out set.

## What was measured, and how

Each task is one natural-language request sent to Claude Code (`claude -p`, version 2.1.212; every
run reported the model `claude-opus-4-8[1m]`). The run registers only this checkout's
`python -m headstart.space_mcp`, removes every built-in tool (`--tools ""`, so there is no web
search or file access to fall back on), allows only the server's three registered tools, and runs
in an empty directory, so no project instructions or memory reach the agent. The runner
(`scripts/eval/space_mcp_eval.py`) saves each stream-json transcript and judges it with the task's
verifier:

- **Argument checks** (`search_args` / `tool_args`): at least one successful call of the named
  tool whose arguments meet every rule, such as `salary_min` 3000000 with `salary_currency` INR,
  and whose `query` holds none of the constraints that belong in filters. The Space is asked with
  `strict=1`, which makes it refuse any filter it would otherwise drop. So a call with the right
  arguments returns rows that satisfy them, and the rows are not re-read.
- **`trend_sign`**: the verifier reads `/trends` itself and compares the sign of the netted hiring
  with the direction the answer states.
- **`hot_top`**: the verifier reads `/hot` itself, drops the Operators the site hides by default,
  and needs N−1 of the top N companies named in the answer.
- **`blocking_named`**: a `search_jobs` result named a Blocking filter, and the answer names it.
- **`mentions`**: required wording in the answer and in the tool results.

The runner records the tool calls, the characters of tool output, errors, refusals and wall time
for every task. It checks each run against §9's bar: at most one wrong (11 of 12, 3 of 4), a median
of at most three tool calls, no tool result over 30,000 characters (about 10,000 tokens), and every
`strict` refusal corrected by the next call.

There were 12 iteration tasks, which descriptions were tuned against. Four held-out tasks were
written by a separate agent before the first run and sealed by sha256 in the iteration file. They
were read only by the runner's `--heldout` mode, once, after tuning had stopped. Each task ran once
per round. There were no repeats, so a single run's pass or fail includes the model's own
run-to-run variation.

## Iteration results

Three full runs were made. Round 0 used the descriptions as merged in #793. Round 1 followed the
first description change, and round 2 the second. A round costs about $0.85–1.00 of Claude usage
and takes 4–5 minutes when the Space is steady.

| Task | What it asks | Round 0 | Round 1 | Round 2 |
|---|---|---|---|---|
| t01 | Remote backend in Bengaluru, at least ₹30 lakh | pass, 1 call | pass, 1 | pass, 1 |
| t02 | New ML engineer jobs, last 24 hours | pass, **3 calls** | pass, 1 | pass, 1 |
| t03 | Is Stripe hiring more or less than two weeks ago | pass, 1 | pass, 1 | pass, 1 |
| t04 | Who is expanding fastest this week | pass, 1 | pass, 1 | pass, 1 |
| t05 | Google vs Microsoft this month | pass, 1 | pass, 1 | pass, 1 |
| t06 | AI/ML across the index | pass, 1 | pass, 1 | pass, 1 |
| t07 | Entry-level frontend at Citi, then Citi's trend | **fail**, 3 (1 refusal) | **fail**, 2 | pass, 2 |
| t08 | Remote contract, "Rust" in the title | pass, 1 | pass, 1 | pass, 1 |
| t09 | Data-engineering seniority levels | pass, 1 | pass, 1 | pass, 1 |
| t10 | Highest-paying staff roles anywhere, USD | pass, 1 | pass, 1 | pass, 1 |
| t11 | "3+ years senior backend in Pune" | **fail**, 1 | pass, 1 | pass, 1 |
| t12 | Haskell in Indore at ₹1 crore | pass, 3 | **fail**, 7 (Space restart) | pass, 4 (3 timeouts) |
| **Correct** | | **10 of 12** | **10 of 12** | **12 of 12** |
| Median calls | | 1 | 1 | 1 |
| Largest result | | 6,579 chars | 5,817 | 4,641 |
| Refusals corrected | | 1 of 1 | 0 of 0 | 0 of 0 |

Round 2 meets every part of §9's bar. Round 1's t12 failure was not a description problem. The
Space redeployed at 12:38 UTC while the task ran: a merge to `main` triggers `deploy-space`. The
agent's call timed out, then got "The HeadStart Space is starting" five times, and the run hit the
runner's 600-second limit. That round's other results stand, because t12 was its last task.

## What each description change fixed

Every change is to a tool's `description`, an argument's `description`, or `when_to_use`. No tool
behaviour changed. They are in `src/headstart/space_mcp/tools/search_jobs.py` and `read_trends.py`.
Sizes after tuning: `search_jobs` 1,374 characters, `read_trends` 667, `hiring_now` 442 (unchanged).
The longest `when_to_use` is 160 and the server instructions are 727, all within the 2,048 / 200 /
2,048 limits the contract tests hold.

**`query` ranks but never narrows; `keyword` requires a word** (search_jobs, round 1). In round 0,
t02 first searched `query: "machine learning engineer"` with a 24-hour window. It got 13,144
matches, and the top row was a "MODULE LEAD - Java/Selenium" job. It then needed two more calls
with a `keyword` to reach ML jobs. t12 and t07 also read a large total as matching jobs. The tool
already said, in its result, that the query "orders the matches but does not narrow them", but the
agent only learned it after the call. The description now says so up front and says to use
`keyword` to require a word. In rounds 1 and 2, t02 sent `keyword: "machine learning"` in its
first call: one call, not three.

**`max_years` is the user's own years** (search_jobs, round 1). In round 0, t11 ("3+ years senior
backend in Pune") left the years out entirely. The agent put Pune in `india_place` and then sorted
11,125 rows into "your band" and "too senior" by hand in its answer. The description and the
argument's own description now say "'3+ years' is 3". In rounds 1 and 2, t11 sent `max_years: 3`
(3,834 rows). Its answer explained that the filter keeps postings asking for at most 3 years, and
offered to widen it if the user has more.

**Say when `company` matched as text** (search_jobs, round 1). In round 0, t07's answer never told
the user that "citi" had been matched as a substring of company names, which can take in other
employers. The tool result said so, but the answer did not pass it on. After the change, the round
1 and 2 answers say the company box "matches any employer whose name contains 'Citi'".

**A `breakdown` of company needs two companies** (read_trends, round 1). In round 0, t07 sent
`breakdown: "company"` for Citi alone. It was refused ("breakdown company compares companies; name
two or more") and corrected on the next call. The argument's description now says a company
breakdown needs two or more companies, and that one company's lines come by category. t07 made no
refused call in rounds 1 and 2.

**A salary sort reads the low end of each range** (search_jobs, round 1). The verifier cannot see
this one, because t10 checks arguments. In round 0, t10's answer told the user the ranking was
"sorted by the maximum of the stated range", which is wrong: the Space sorts on
`min_salary_annual`. The description now says a salary sort orders by the low end. The round 1 and
2 answers say so, and point out that a lower-floor role (Anthropic, $405k–$625k) has the highest
ceiling.

**Name the directory company a trend read** (read_trends, round 2). In round 1, t07 still failed.
Its answer reported Citi's trend but never said which directory company the name had been read as.
The tool result names it: "Citi" (`workday:citi/2`, 1 Board), which is narrower than the search's
substring match over 93 jobs on two Boards. A round-1 sentence in the description was not enough.
Round 2 moved the instruction into `when_to_use`, which is part of the server instructions every
session loads, and asks for the key and Board count. The round 2 answer opens its trend section
with "Read as the directory company **Citi** (`workday:citi/2`, 1 Board, 1,213 tech openings)".

Tuning stopped after round 2. It met the bar with every task passing, and the one remaining
trouble, t12's timeouts, is the Space and not the descriptions.

## Held-out result

The sealed file's hash matched, and it ran once, with the round 2 descriptions, unchanged since.

| Task | Tool calls | Verdict |
|---|---|---|
| h1: a German backend search with a monthly salary, experience, recency and employment type | `search_jobs` ×1 | pass |
| h2: which way hiring for chip-design roles is going over three weeks | `read_trends` ×2 (hardware-engineering, embedded-firmware) | pass |
| h3: a company's trend since January | `read_trends` ×1 | pass |
| h4: the top five companies by rate | `hiring_now` ×1 | pass |

It was **4 of 4 correct**, with a median of 1 tool call, a largest result of 12,695 characters, and
no refusals. What the transcripts show beyond the verdicts:

- h1 turned "€5,500 a month" into `salary_min` 66000 EUR, and put the years, place, employment type
  and date window in their own fields. It used a `query` with a salary sort, and its answer said the
  query ranks but does not filter.
- h3 asked for 365 days. The answer said that per-company counts begin on 2026-09-13, so no
  comparison with January is possible. It then named the directory company it had read
  (`workday:jj`, 4 Boards).

## Findings for the owner, not fixed here

- **A description-scope keyword search can time out on `/facets`.** t12's agent chose
  `keyword_in: "both"` for "Haskell" in both tuned rounds, and every such call timed out. Measured
  directly, once each, on the running Space: `/facets` with `kw=Haskell&india=indore` answered in
  13.2 s; the same with `kw_in=both` gave no answer within 120 s; a plain `q=backend` answered in
  1.9 s. Search itself answered in 27 s. This is one query, not a population. It points at the
  Space's facet counting over descriptions, which recounts once per dropped filter, not at the
  tool. The descriptions do not steer away from `keyword_in: "both"`: that would hide a Space
  problem behind wording.
- **Every merge to `main` restarts the Space.** Over this hour-long run, the Space rebuilt at
  12:03, 12:38, 12:56 and 13:13 UTC. Each boot takes about 4 minutes, during which every tool call
  answers "starting". Round 1's t12 was lost to one of these restarts. An agent using the server
  while the repo is busy will meet it too.
- **h2's trend reading looks wrong, though the verdict passed.** Over the same 21-day window,
  Embedded & Firmware reported hiring −11,410 (−69.9%) with only 71 opened and 95 closed.
  Hardware & Silicon reported +1,018 and withheld its percentage for "a window under 3 days". A
  category reassignment reported as hiring, and a short-window note on a three-week window, both
  suggest the taxonomy change between those categories is not netted as a Marked change. This was
  not verified further.
- **The `rate` Lens rewards churn.** h4's top company, New York Life, shows a rate of 1,665%: 433
  opened against 26 open now, with a net of −26. The agent flagged it itself. It is how the Lens
  is defined, not a tool error.

## What 12 + 4 tasks can and cannot show

- **They can show** that each of the §9 behaviours works at least once against live data: a
  constraint lands in its own field rather than in `query`, a currency is attached to a salary
  bound, the Lens and breakdown are chosen correctly, a Blocking filter is named, and the two
  company readings are told apart. They can also show that the descriptions are what moved t02,
  t07 and t11. The round 0 and round 2 transcripts differ in exactly the argument or sentence the
  change named.
- **They cannot show a reliability rate.** Each task ran once per round. Even a perfect score on
  these samples bounds the true success rate only loosely: the 95% lower bound for 12 of 12 is
  about 74%, for 4 of 4 about 40%, and for all 16 together about 79%. The 12 iteration tasks were
  also the tuning set, so 12 of 12 is biased upward. The held-out 4 of 4 is the unbiased number,
  and it is small.
- **They test one client.** Every run was Claude Code with one model. Another MCP client, or a
  model that does not read tool descriptions as closely, may behave differently.
- **Most verifiers check arguments and wording, not rows.** They trust `strict=1` to make the
  Space apply what it is sent. `trend_sign` and `hot_top` re-read the Space, but no verifier
  judges whether an answer's prose is faithful beyond the checked terms. For example, t10's round
  0 answer misdescribed the salary sort and still passed.

Raw transcripts, stderr and per-round results files are kept locally under
`experiment/space-mcp-eval/artifacts/` (not committed): `2026-09-28T1218Z_iteration_*` (round 0),
`T1225Z` (round 1), `T1250Z` (round 2) and `T1320Z_heldout_*`.
