# One string cost 102 Boards their entire detail pass — 2026-09-07

`headstart/0.1 (job-board reader)` was on a SuccessFactors edge policy's denylist as an **exact
literal**. Every job page those Boards fetched came back 403, `_job_fields` mapped that to `None`
like any other failure, and the pipeline stayed green while ingesting nothing from them for at
least five consecutive runs.

## What it cost

From `docs/pipeline/2026-09-07_five-run-log-review.md` §1, measured across runs `34074802564`
through `34088295600`:

| | |
|---|---|
| Boards returning **0 jobs in all five runs** | **102**, every one SuccessFactors |
| Wasted per run | **631 board-minutes** — 7.3% of the run's 8,646 |
| Postings listed and not ingested (run `34088295600`) | **56,120** across 127 Boards |
| Share of all straggler time spent on zero-yield Boards | **46–49%** |

And it owned the wall clock. `careers.te.com` fetched 2,127 pages in 1,614 s — **97% of its
shard**, and `scrape_plan`'s predicted makespan equalled its single-board floor in every run, so
the scrape stage (roughly half the pipeline) was paced by a Board that produced nothing.

## Why no log said so

`SuccessFactorsScraper._job_fields` collapses two unrelated outcomes:

```python
if response.status_code != 200:
    return None
return _titled_fields(response.text, url)
```

A refusal and a body-that-will-not-parse are indistinguishable downstream, so
`report_detail_gaps` could only say `2127/2127 detail fields missing`. That reads as a parser
problem, and it sent the first pass of this investigation at the markup.

## The false trail, recorded on purpose

Fetching a live `careers.te.com` job page from a laptop with plain `curl` returned **200, 93,038
bytes**. The page carried no JSON-LD and no `<meta itemprop>` microdata, only `joblayouttoken`
spans — and *German* ones, since that tenant is localised. That is a tidy story: the scraper reads
English label spans, the tenant serves German, the fields come back empty.

It was wrong. Running the repo's own `successfactors._titled_fields` over the saved page returns a
usable dict — title and description — and `jobs.l3harris.com` returns location and date as well.
The parser was never the problem.

The second theory was that the Actions runner egress was walled, since the pages plainly worked
from a laptop and the vantage was the only variable left. A probe was written to test that from
inside Actions. **That was also wrong**, and expensively so: the probe reproduced the bug on its
first local run, in about thirty seconds, because it did what `curl` had not — sent the request
through `headstart.http.fetch` with the repo's own `USER_AGENT`.

> Two elaborate explanations died to one harness that ran the real code path. Build the loop
> first.

## The measurement

`scripts/bench/probe_successfactors_detail.py`, and then a bisection of the string itself. Same
URL, same session, one variable at a time:

| Request | Result |
|---|---|
| `impersonate="chrome"` + `headstart/0.1 (job-board reader)` | **403**, 111 B |
| `impersonate="chrome"` + a Chrome UA | 200, 92,808 B |
| `impersonate="chrome"` + no UA header | 200, 92,808 B |
| **no** impersonation + `headstart/0.1 (job-board reader)` | **403**, 111 B |
| `impersonate="chrome"` + repo UA + `Accept`/`Accept-Language` | **403**, 111 B |

So it is neither the TLS fingerprint (row 4 has no mismatch and still fails) nor a missing header
(row 5 adds them and still fails). The body names the mechanism:

```json
{"error":{"status-code":"403","message":"Policy ID: tZKgGCUW","request-id":"2a676dfa…"}}
```

Then the string itself, which is where it gets specific:

| User-Agent | Result |
|---|---|
| `headstart/0.1 (job-board reader)` | **403** |
| **`headstart/0.1`** — the string now shipped | **200** |
| `headstart/0.1 (job-board)` | 200 |
| `headstart/0.1 (reader)` | 200 |
| `headstart/0.1 (job board reader)` — space, not hyphen | 200 |
| `headstart/0.1 (job-board viewer)` | 200 |
| `somethingelse/1.0 (job-board reader)` | 200 |
| `curl/8.7.1` · `python-requests/2.32.3` · `Googlebot` · none | 200 |

**Only the complete literal is blocked.** Not a bot heuristic we tripped — a denylist entry for
this crawler by name.

## Is working around it legitimate?

Checked before deciding, because a named block deserves the question. All three hosts — the two
suspects and the control — serve the **byte-identical** stock SuccessFactors robots.txt, and it
disallows only `/applybutton/`, `/talentcommunity/`, `/emailsubscribe/`, `/services/`,
`/preapply/`, `/unsubscribe/` and friends. The `/job/` path this scraper fetches is **explicitly
allowed**, and there is no `User-agent: headstart` stanza anywhere.

So the one place these tenants express crawler policy permits exactly what we do, the control host
with identical policy does not block us, and the block lives only in an edge bot-management rule.
That reads as automated classification rather than a considered exclusion. Changing the string is
not evading a stated wish; it also does not change what we fetch, how fast, or whether robots.txt
is honoured.

## The fix, and why it is bare

`base.USER_AGENT` is now `headstart/0.1`. The obvious improvement — adding a contact URL, so a host
that dislikes our traffic can reach a human — **cannot be made**, and that is a measurement too.

zwayam's edge rejects any User-Agent carrying a domain or an email with `curl (92) HTTP/2 stream
error`, 2 of 2 attempts on each of four candidates:

| User-Agent | zwayam |
|---|---|
| `headstart/0.1 (+https://github.com/…)` | **curl (92)** |
| `headstart/0.1 (+github.com/…)` | **curl (92)** |
| `headstart/0.1 (github.com/…)` | **curl (92)** |
| `headstart/0.1 (contact …@…)` | **curl (92)** |
| `headstart/0.1 (a/b)` | ok |
| `headstart/0.1 (contact: maintainer)` | ok |
| a long domainless phrase | ok |
| `headstart/0.1` | ok |

And zwayam **blackholes** stock agents — `curl`'s and `python-requests`' own defaults time out
rather than refusing (`zwayam.py` module docstring, measured 2026-08-27), so a retry ladder reads
that as transient and repeats forever. Three constraints, one narrow intersection:

- not the SuccessFactors literal,
- no domain and no email,
- not a stock tool default.

`headstart/0.1` sits in it. `tests/test_user_agent.py` pins all three as properties rather than as
a fixed string, so the next edit that violates one fails there instead of in another silent
five-run data loss.

## Does this make the makespan floor worse?

A fair challenge: a 111-byte 403 sounds cheaper than a 93 KB 200, so unblocking `careers.te.com`
might make its 2,127-page pass *slower* and grow the very floor the fix was meant to remove.

Measured on 8 of that Board's real pages, same session, one string each:

| | mean per page | projected 2,127 pages at 6-wide |
|---|---|---|
| old UA — 403 | **5.57 s** | ~32.9 min |
| new UA — 200 | **1.35 s** | ~8.0 min |

**4.1x faster on this sample.** The intuition misses the retry ladder: 403 is in `http.TRANSIENT`,
so every refusal cost several attempts with backoff, while a served page is fetched once.
(`_DETAIL_WORKERS = 6`, and `successfactors` sets no `detail_streams`, so 6 is the width on both
the sync and async paths.)

**Read this as a direction, not a number.** It is n=8, one Board, one session, **from a laptop** —
and per-page latency is exactly the quantity that varies by vantage, which is the whole reason the
in-Actions probe below is still worth running. Only the *old* arm can be reconciled against
production at all, and it overshoots: 32.9 min projected against the 1,614 s (26.9 min) the run
logged, 22% high. The 1.35 s served-page figure has no production counterpart yet, by definition —
no run has ever fetched these pages successfully. What the sample supports is the **sign**: the
floor shrinks rather than grows, so item 1 of the review's ranked scope is not undermined. The
magnitude wants the next real run to confirm it.

## Pending: the Actions vantage

**This is not settled, and nothing above should be read as settling it.** Every measurement in this
document — the bisection, the 16/16 recovery, the per-ATS sweep, the floor sample — was taken from
one laptop and one egress address. Production runs from GitHub Actions.

That matters more here than it usually would, because the *first* theory of this bug was an egress
wall, and it was discarded on the strength of laptop evidence alone. If the SuccessFactors policy
scores address and User-Agent together rather than either alone, the new string could clear a
laptop and still be refused on a runner — and the symptom would be exactly what it was before: 102
Boards at 0 jobs, silently, with a green run.

`.github/workflows/probe-successfactors-ua.yml` exists to answer that, with two replicas because
one runner is one address:

```
gh workflow run probe-successfactors-ua.yml
```

**It has not been run.** `workflow_dispatch` requires the workflow file on the default branch, so it
cannot be dispatched until this change merges. Dispatch it immediately afterwards and record the
result here; until then this document's claims are laptop-vantage claims.

## Verification

**The bug's own loop, before and after** —
`python -u scripts/bench/probe_successfactors_detail.py --n 3 --arms direct`:

| Board | before | after |
|---|---|---|
| `careers.te.com` | parsed 0/3, `{'403': 3}` | parsed 3/3, `{'200': 3}` |
| `jobs.l3harris.com` | parsed 0/3, `{'403': 3}` | parsed 3/3, `{'200': 3}` |
| `careers.bureauveritas.com` (control) | parsed 3/3 | parsed 3/3 |

**Recovery across the zero-yield Boards.** Of the 20 worst by seconds burned, 16 listed
successfully from a laptop; **16 of 16 went 403 → 200 and parsed a real job title**, and 0 of 20
worked under the old string. Four — `careers-inc.nttdata.com`, `corningjobs.corning.com`, `jobs.scotiabank.com`,
`bechtel.jobs.hr.cloud.sap` — returned no listing **from this laptop**, so they could not be
sampled. Calling that "a separate listing-side fault", as an earlier draft of this doc did, is
contradicted by the review's own §1 table: in CI those same Boards listed 1,399 / 1,212 / 1,232
pages. Their listing works in production and the laptop is the odd vantage, so the likeliest
reading is that the fix reaches them too and this **understates** recovery rather than deferring a
known break. Not asserted either way — the in-Actions probe below is what would settle it.

**No regression anywhere else.** Two live Boards per ATS across the **20 ATSes that have a
liveness ledger** — the registry holds 22, of which `join` is in `DISABLED_ATS` and `oracle` and
`sensehq` have no ledger and therefore no Board to sample — real `fetch_raw()` + `parse()` under
each string, reproducible from a clone:

```
python -u scripts/validate/user_agent_sweep.py \
    --old "headstart/0.1 (job-board reader)" --new "headstart/0.1"
```
 **38 SAME, 2 BETTER (successfactors 0 → 1), 0 WORSE** — zwayam
included, twice. The `DNSError` (personio, zoho) and `ValueError` (workday) rows never reached a
host under **either** string, so the sweep reports them as `UNREACHED` rather than `SAME` — they
are pre-existing and unrelated, and counting them as evidence that the change is safe would be
wrong.

## What would have prevented it

Nothing here needed a new abstraction — it needed the failure to be *visible*. Two gaps let a
total, permanent loss on 102 Boards sit green for five runs:

1. **`_job_fields` throws away the status.** A 403 and an unreadable 200 arrive as the same `None`,
   so no log line downstream could name the cause. Worth widening: `report_detail_gaps` could
   carry a status histogram the way workday's `failed mid-crawl` line already does.
2. **Nothing alerts on a Board that yields zero after real work.** A Board burning 1,614 s for 0
   jobs, every run, is a strong signal and it was only found by reading five runs by hand. The
   ADR-0064 value gate should have caught it and could not — for its own separate reason, written
   up as item 2 of the review's ranked scope.
