# Tesla batched in-page `Promise.all(fetch(...))` — feasibility experiment (issue #553)

## Question

`tesla.py` ships every `Job.description = None` (~8,105 postings, the largest description gap in
the corpus) because a per-job detail costs a full browser navigation — Akamai blocks any
*explicit* HTTP request to `/cua-api/careers/job/{id}` with a `429 {"cpr_chlge":"true"}`, proven
even from inside an already-warmed, cookie-bearing pydoll/Chrome session issuing the request via
`tab.request` (a single page-context fetch). Issue #553 asked whether upstream's own working
mechanism — a **batched** `Promise.all(ids.map(id => fetch(...)))` executed in-page — is the one
variant our own prior 429 measurement never covered, since it used a single explicit request, not
a batch, and not via raw `Runtime.evaluate`/`execute_script`.

**Hard constraint respected**: no new dependency. Everything below runs on this repo's own
pydoll + stock headful Chrome, no `cloakbrowser` fork, no residential proxy.

## Method

Ad hoc scripts (not committed — read-only probes against the real site, kept out of `src/`),
driving pydoll directly: `browser.new_tab()`, `tab.go_to(...)`, and `tab.execute_script(js,
return_by_value=True, await_promise=True)` to run an async IIFE that does
`Promise.all(ids.map(id => fetch(\`/cua-api/careers/job/${id}\`, {credentials: 'include'})))`
inside the page's own JS context — the CDP `Runtime.evaluate` path, not `tab.request`. Full raw
output: `artifacts/2026-09-22_batched-fetch-results.txt`.

## Result: it works, and the trigger is a job-page navigation, not just elapsed time

| # | setup | result |
|---|---|---|
| 1 | Batched fetch (10 ids) issued from the **search** page, right after its own state call | **404 on all 10** |
| 2 | (sanity) Real navigation to one job page — the page's *own* natural request | 200, full `jobDescription` |
| 3 | Batched fetch (8 **different** ids) issued from a tab that had just navigated to **one** job page | **200 on all 8**, every one carrying a real `jobDescription` |
| 4 | Control: **wait 10s on the search page alone**, no job-page nav, same 8 ids as #3 | **404 on all 8** — ruling out "just needed more time" |
| 5 | **Three separate, sequential** `execute_script` calls (not one big `Promise.all`) from a tab that navigated directly to one job page | All three: 200 on every id (including re-fetching the same 4 ids a second time), 0.6–1.6s each |

Attempt 4 is the control that matters: it isolates *what* unlocks the batched fetch. It is not
elapsed time or having made the state call — it is specifically having navigated to a **job
detail page** (`/careers/search/job/{slug}-{id}`). Once that has happened once, the tab can fetch
**arbitrarily many other job ids** — not just the one it navigated to — and can do so across
**multiple separate calls**, not only inside the one `Promise.all` that happened to run right
after the navigation (attempt 5).

Reading: Akamai's sensor challenge is almost certainly tied to that page route running its own
bundled JS (which generates the `_abck`/sensor payload the job-detail page's own script performs
as a side effect of loading), not to the specific request or even the specific job id. Once one
real navigation has satisfied it, the tab's fetches are trusted for a window this experiment did
not fully characterize (at least the ~15s spanned by attempt 5's three calls).

## What this changes about the "thousands of navigations" premise

`tesla.py`'s current `has_detail_pass = False` is reasoned from "a full browser navigation **per
job**" being too expensive for 8,105 postings. That premise is wrong for the mechanism actually
proven live: **one navigation, then N batched in-page fetches** — the same shape
`browser_http`'s darwinbox precedent already uses for an unrelated wall (ADR-0056), just with a
`Promise.all` batch instead of one fetch per warmed tab. At the batch sizes tested here (4–8 ids,
well under any observed limit), each call landed in 0.6–2.6s.

## What is NOT yet known, and why this stays an experiment, not a shipped fix

- **No batch-size ceiling was found**, but none was cleanly tested either — a `40`-id batch
  attempt failed on a harness bug (the *state* endpoint's response body became unretrievable via
  CDP after several rapid repeated calls in the same run; a fallback list of guessed-sequential
  ids all 404'd, which is a bad-id artifact, not a wall — visible in the raw log). Real listing
  ids are not dense/sequential, so a real batch-size/pacing characterization needs a fresh,
  correctly-sourced id list, not a guessed range.
- **How long the post-navigation trust window lasts** was bounded below (~15s, attempt 5) but not
  found to expire — a full-board run (8,105 postings) would need to know whether it holds for the
  whole crawl or needs periodic re-navigation.
- **Integration cost**: wiring this into `tesla.py` as a real `has_detail_pass` path is a
  non-trivial scraper change — batch size/pacing at full scale, error handling for a batch that
  partially fails, and how it fits the existing single-navigation browser lifecycle
  (`_ensure_started`/`_fetch_state_json`) all need their own design pass, not a patch.

## Recommendation

Build it as a scoped follow-up, not in this pass: the feasibility question issue #553 asked is
answered **yes**, cleanly and reproducibly (5 attempts, the key one — attempt 4 — a real negative
control). But turning it into a shipped detail pass needs its own batch-size/pacing measurement at
real scale and a design pass for how it fits `tesla.py`'s current single-navigation shape — exactly
the kind of "genuine design decision" CLAUDE.md's own rule reserves for a deliberate scoped
change, not a live-measurement pass. Posted as a comment on #553 with this finding; issue left
open rather than forcing a partial implementation.
