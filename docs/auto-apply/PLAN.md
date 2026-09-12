# Auto-apply — v1 design for review (revision 5)

**Status:** design confirmed 2026-09-03; revised four times after external review
rounds (§10–§13); nothing built yet; **one decision open** (§14). **Decisions:** ADR-0105 (browser
submits; state machine), ADR-0106 (one-shot plan; deterministic fill), ADR-0107 (PII in the
browser), ADR-0108 (trust boundaries), ADR-0109 (hot state store — *proposed*). **Vocabulary:**
`CONTEXT.md` §Applying. **Evidence:** `experiment/auto-apply/`.

Self-contained. Decision numbers 1–25 are stable from earlier revisions; amended ones say so;
26+ are new.

## 1. What this is

HeadStart is a job search engine that reads openings directly from company ATS boards (~426k tech
jobs across 22 ATSes), embeds them, and serves a semantic search behind Google sign-in. A signed-in
**Account** has a **Profile** (facts extracted once from a pasted résumé) and **Saved jobs**. A
private LLM router (LiteLLM, tunnel-only) serves résumé extraction through `ask(prompt) -> str`.

**Auto-apply** adds one button beside each job. **The promise is probabilistic and says so:** one
click starts the application; many finish unattended; whatever needs a person — a question nobody
has answered, an essay to glance at, a CAPTCHA — appears in one queue with its reason. It is not
"never click again".

## 2. What was measured before deciding

Probed live on 2026-09-02/03.

- **No unauthenticated, candidate-scoped submission API exists on any major ATS.** Greenhouse,
  Lever, Ashby (`applicationForm.submit`) and SmartRecruiters all *have* application endpoints;
  all authenticate as the employer.
- **Every public apply form probed is CAPTCHA-gated at submit** (reCAPTCHA Enterprise, hCaptcha,
  Turnstile, depending on ATS). Lever documents its confirmation email as *optional* — email is not
  a universal success signal.
- **Reading the form is free almost everywhere submitting is not.** Nine ATSes expose the schema
  unauthenticated; Workday's questionnaires *branch*.
- **Census of 3,849 jobs / 37,867 fields across nine ATSes:** 73% of question occurrences are
  exact repeats; after ~2,900 jobs, 80% of a fresh job's *questions* are already answered; only 24%
  of applications reuse a *form shape*; each ATS uses 5–13 widget types; 52% of jobs carry a
  free-text question; 117 Greenhouse fields across 439 jobs are conditional; 38–65% of inputs on
  the HTML ATSes are hidden.
- **Zero-touch is a different number from question reuse.** Simulating a bank from 75% of the
  harvest against the rest: only **38%** of jobs have *every required field* covered — 41% blocked
  by an unknown question, 41% by a per-Job glance, 13% by an essay — before CAPTCHA, upload or
  browser failure. This is the number the product is built against.
- **Job URLs are ATS-hosted**: Ashby and Lever 100%; Greenhouse 83%, with a canonical rewrite.
- **Storage primitives:** `huggingface_hub` 1.21 has `create_commit(parent_commit=…)` (CAS on repo
  HEAD) and `super_squash_history` (history can be made to forget). Neither makes a Git repo a
  transactional database.
- **A full-corpus schema harvest is ~1.6 GB.** Ashby enforces a rolling rate budget.

## 3. Decisions, each with what was rejected

1. **Submission happens in the user's own browser, via a Chrome extension.** *Rejected:* server-side
   POST; a bookmarklet. Chrome only.
2. **One click, never interrupted mid-flow.** State machine **Queued → Running → Ready →
   Submitting**, ending **Sent / Needs you / Stopped / Unknown**.
3. **The click claims the Application before anything is filled.** *(amended)* One per Account per
   Job, ever, as a unique constraint in a transactional store (ADR-0109); the HF
   `create_commit(parent_commit=HEAD)` CAS is the documented fallback. *Rejected:* check-then-create.
4. **Unknown is the uncertainty state; settling it Not submitted is the only door to a second
   Attempt.** *(amended)* The invariant is *at most once automatically*, not exactly-once. An
   Unknown is settled by evidence into **Sent**, or into **Not submitted** — *verified* (evidence
   it did not go through) or, because many ATSes send no confirmation email, *asserted* (the
   Account's explicit choice, warned it may apply twice). Only Not submitted opens Attempt 2, so
   the record reads "attempt 1 Unknown → Not submitted, attempt 2 Sent". *Rejected:* overwriting
   the Unknown; pretending exactly-once survives; forbidding the assertion (a graveyard); a second
   door into Attempt 2 that bypasses the settling. *Renamed:* **Cleared** → **Not submitted** — the
   old name read as "all fine" when it means the opposite of Sent.
5. **Sent requires that ATS's three-valued predicate, biased toward Unresolved.** *(amended)*
   *Confirmed* wants the strongest evidence the ATS exposes — a durable application identifier
   where one exists (per ATS, from a real submission), otherwise marker ∧ form gone ∧ no error,
   knowing a frontend can hide the form before its backend persists. *Failed* = validation error,
   form present → Needs you. Else *Unresolved* → Unknown. *Rejected:* 2xx; any single signal;
   "three conditions true" as sufficient.
6. **Essays and LLM-proposed answers wait for a glance in v1.**
7. **Answers are standing or per-Job.** *(amended)* Standing = a fact reused without asking —
   **but not forever**: each carries `confirmed_at` and is re-glanced once after a review window
   (180 days; 90 for kinds that drift: location, employment status, current employer, visa).
   Per-Job = always a glance with the last value proposed. Promotion to standing is a separate,
   plainly worded act, never a side-effect of confirming once. Kind proposed on first sight,
   confirmed at first glance; per-Job when unsure. *Rejected:* immutable standing Answers.
8. **A Question's identity is its canonical wording plus its option *set*.** *(amended)*
   Canonicalisation defined now: lowercase, entities decoded, Unicode punctuation folded,
   whitespace collapsed, parentheticals dropped; options as an order-insensitive set — Yes/No
   equals No/Yes, Yes/No/Prefer-not-to-say does not. Employer not in the key.
9. **Every Answer carries provenance; every Sent Attempt keeps a submission snapshot.**
10. **Contact details and the résumé live only in the browser.** *(amended)* Contact in
    `chrome.storage.local`; the résumé as a blob in IndexedDB (not `storage.local`'s 10 MB).
    Neither encrypted at rest — the same posture as a Downloads folder, stated plainly. "One click"
    presupposes a *provisioned* browser.
11. **The LLM plans content once per page through `ask()` with a ~15 s budget.**
12. **Adapters read the application's fields, never every input.**
13. **Fill is verified per box:** set → settle → read back → compare → re-scan.
14. **The fill is a bounded loop** (3 transitions / 10 s / recurring mutation → Stopped).
15. **Form mechanics are deterministic and fail loudly**; Stopped carries a category *(amended)*:
    *adapter* / *external* — now including a page going stale **in place** (session expired, form
    version changed, application disabled, CSRF rotated) / *browser*. Only *adapter* is the repair
    queue.
16. **The form is read at the click; no harvest stage.**
17. **v1 supports Greenhouse, Ashby and Lever.**
18. **The extension never holds the Google credential.** *(amended; see 30 for the pairing spec.)*
19. **No pacing of sends.**
20. **No fit gate.**
21. **State machine with a lease and a wall clock.** *(amended)* Lease 3 min, heartbeat 10 s with
    `run_id`; phase deadlines (read 20 s / plan 15 s / fill 30 s incl. settle and upload / observe
    20 s) under a hard **`T_total` = 120 s**. Lease expiry *before submit was initiated* → Needs you
    — worded that way, not "the ATS received nothing" (Greenhouse uploads the résumé to S3 before
    submit; forms autosave). Expiry during Submitting → Unknown. Heartbeats are never durable writes.
22. **Serial per Account** — one execution slot, **granted atomically by the backend**, never by the
    extension's local queue, which cannot serialise across devices. Queue order FIFO by claim; a
    Queued Application can be cancelled. *(owner-confirmed)*
23. **Validation happens twice; only the second may submit** — and *Ready* records a fingerprint.
24. **The ATS page is untrusted input to the plan.** *(amended)* The security property is *the
    model cannot see facts the Question does not need* — not "a person reads the answer", which is
    mitigation. A written **disclosure policy** maps question category → the exact facts sent;
    essays get a named subset by essay category (never the whole Profile); the directive filter is
    defence in depth, not the boundary.
25. **The extension operates only on a bound origin and the claimed Job.** *(amended)* Manifest
    `host_permissions` = *capability* (an ATS origin hosts thousands of companies); the claim =
    *authorisation*. The **service worker** enforces it from Chrome-supplied `sender.tab.id` /
    `frameId` / `tab.url`, never from what the content script says; client-supplied origin/URL/job
    id are assertions checked for consistency; the backend derives Account from the token and Job
    from its own claim record.
26. **Application vs Attempt.** *(amended)* An **Application** is one Account–Job pair and holds
    one or more **Attempts**; an **Attempt** is the concrete browser run that tries to submit it.
    State, `run_id`, lease and the submission snapshot belong to the Attempt; the pair and the
    history belong to the Application. *Rejected:* a single record pretending to be exactly-once;
    "a state machine over Attempts" as the explanation — it hid which object owns what.
27. **A fence, not just a lease.** *(amended)* Two mechanisms, two problems: the **lease** is one
    Attempt's temporary ownership, renewed by heartbeat, telling the server who *should* own the
    work; the **fence** refuses a stale Attempt that acts anyway, because an expired lease cannot
    stop a tab that is still executing. Nothing irreversible happens without `submit_intent(application, run_id)` granted by
    the backend (run current, lease valid, state Ready) **and** a fresh schema+value fingerprint
    matching the one taken at *Ready*, immediately before the click. Either failing → back to
    Running. The service worker drops any content-script message whose `run_id`/`tab_id` is not the
    one it is executing; a content script can *request* submit, never authorise it. *Rejected:*
    lease-as-ownership; validated-a-moment-ago as valid-now.
28. **Active execution context, not "background tab".** *(new)* The adapter needs a tab it owns;
    background is the initial default and foreground the measured fallback; neither is a
    state-machine invariant. *Rejected:* baking background execution into the design before the
    experiment.
29. **The extension is durable and resumable.** *(new)* MV3 service workers are killed after idle;
    an *Execution* record (application id, `run_id`, `tab_id`, phase, deadline, last fingerprint)
    lives in extension storage; on restart the worker finds the tab, reconciles with the server, and
    continues or stands down. *Rejected:* an always-alive coordinator.
30. **Pairing is specified.** *(new)* ≥128-bit one-time code, 5-minute expiry, Account-bound,
    consumed atomically, rate-limited, redeemable only via the extension's pairing endpoint; yields
    a scoped, short-lived, refreshable, revocable token. *Rejected:* a six-digit code.
31. **File inputs have their own contract.** *(new)* `DataTransfer` → `input.files` → events → wait
    for the ATS's own upload → verify the *accepted* state. A local file is not a filled field.
32. **Frames.** *(new)* Application fields must be in the top frame in v1; CAPTCHA iframes are
    recognised as challenges, never filled; frame identity is part of field identity.
33. **Hot state lives in a transactional store; the HF repo is not a database.** *(new; ADR-0109,
    proposed)* Claim, slot grant and every transition are transactions; heartbeats never durable
    writes; retention is a written policy with real deletion. **Which store is the open decision
    (§13).** *Rejected:* every transition as a Git commit on a global HEAD; snapshots living
    forever in repo history.
34. **Retention is stated.** *(new)* Answers, Attempts and snapshots are the Account's to delete;
    deletion is real within a stated window; a snapshot retention period is shown in the product.
35. **The success metric is zero-touch completion and confirmed acceptance**, measured — not
    question reuse. *(new)* The 38% simulation is the baseline to beat.
36. **Ready is not authorisation to submit; the fence is.** *(new; restates 27 from the state
    machine's side so nobody reads Ready as the last gate.)*

## 4. The click, end to end

1. Click *auto-apply*, signed in as an Account.
2. **Claim** (transaction): Application for (Account, Job) as **Queued**, Attempt 1, canonical
   ATS-hosted URL bound. Exists already → show its state.
3. **Slot**: the backend grants this Account's single execution slot to the oldest Queued
   Application → **Running**, lease + `run_id`; the extension writes its Execution record and
   heartbeats.
4. The extension opens the bound URL in a tab it owns. The **service worker** checks Chrome's
   `sender.tab.url` and origin against the claim before the content script is allowed to do
   anything. Fail → Stopped (*external*).
5. Content script reads the application's fields by the adapter's conventions, top frame only
   (read deadline 20 s). Hidden, unclassified and framed inputs are ignored.
6. Form sent to the backend under the scoped token; the backend derives Account and Job from its
   own records and checks the asserted URL for consistency.
7. Standing Answers applied by canonical wording + option set — no LLM. Stale standing Answers
   (past review window) become a glance.
8. Directive-shaped questions set aside. Residue → **one** `ask()` (15 s), page text as data,
   facts per the disclosure policy. On timeout, residue proceeds without the model.
9. Backend validates what it can see → plan *valid*.
10. Plan returns; extension merges contact data and the résumé blob locally.
11. **Fill–verify loop** (30 s incl. settle and upload): set → settle → read back → compare →
    re-scan; file inputs by their own contract; bound 3 / 10 s / recurrence → Stopped.
12. **Final browser-side validation** → **Ready** with a schema+value fingerprint — only if every
    field is verified from a standing Answer or local data and nothing is proposed, drafted,
    per-Job, suspicious, stale, or missing. Else → **Needs you**, tab parked.
13. **Fence**: `submit_intent(application, run_id)` → granted only if run current, lease valid,
    state Ready; extension re-fingerprints; match → **Submitting**, press. Mismatch → Running.
14. Predicate (20 s): *Confirmed* → **Sent** with snapshot; *Failed* → **Needs you**; *Unresolved* →
    **Unknown**, tab parked. Lease expiry here → Unknown.
15. Retry from the queue is attended, same Attempt, from the live page. An Unknown is settled to
    **Sent** (evidence) or **Not submitted** (verified, or asserted by the warned Account); only
    Not submitted opens Attempt 2 as a fresh Queued run.

## 5. Vocabulary

- **Application** — one Account's standing with one Job; claimed by a click; one per Account per
  Job, ever; run one at a time per Account in click order; submitted through Attempts.
- **Attempt** — the concrete browser run that tries to submit an Application, kept in full;
  normally one; two once the first is settled Not submitted, both on record.
- **Sent / Needs you / Stopped / Unknown / Not submitted** — as in §3 (2, 4, 5, 15). **Lease** =
  an Attempt's temporary ownership; **fence** = refusal of a stale Attempt's action. Different
  mechanisms; both needed.
- **Question** — canonical wording + option set; not per-Account.
- **Answer** — standing (reused; reviewed after a window) or per-Job (always a glance); provenance;
  superseded, not edited.
- **Profile**, **Résumé** — as before; a résumé copy lives in the browser only.

## 6. Mechanics assumed (flag if any looks wrong)

Hot state (Application, Attempt, `run_id`, lease, slot, queue) in the transactional store of
ADR-0109; Answers move there too (read on every plan); Profile stays on HF. The extension's
Execution record is in extension storage. Each adapter ships: field identification, per-widget
interaction contract, file-upload contract, frame policy, three-valued predicate, Stopped
categoriser. The disclosure policy and the directive list are small reviewed tables in code.

## 7. Deliberately not in v1

Workday and every other ATS. Company-domain embeds. Framed application fields. Any harvest stage.
Any LLM role beyond the one-shot plan. Auto-sending LLM text. Send pacing. A fit gate. Multi-device
sync of local data. Embedding-based question clustering. Automated reconciliation of Unknown.
Encryption at rest for local data.

## 8. Risks and open questions a reviewer should push on

- **The zero-touch ceiling is 38% before CAPTCHA and upload.** Per-Job glances and unknown
  questions each block ~41% of jobs early. The bank grows and per-Job promotion helps; the number
  is reported, not hidden.
- **Confirmed-acceptance rate is unmeasured**; background vs foreground both measured.
- **Profile facts still reach the router's provider**, now per a disclosure policy; essays receive
  a named subset. The residual risk is a legitimate essay that over-discloses within that subset.
- **Standing-Answer review windows** are guesses; measure how often a re-glance changes the value.
- **Asserting Not submitted** (owner-confirmed) relaxes exactly-once per Job, per user, warned, on
  record — it is the escape hatch for ATSes that send no confirmation email.
- **Serial per Account** (owner-confirmed) is not pacing; **no pacing** stands.
- **Single-device, unencrypted local data.**
- **The store decision (§14) gates the crash and two-device experiments.**
- **Loop bounds, phase deadlines, `T_total`, lease, review windows are initial values.**

## 9. Build order and the Greenhouse experiment

1. **Greenhouse alone, real board, owned tab.** Happy path; conditional field; settle; résumé
   upload (its own contract); CAPTCHA; predicate; **adversarial cases:** page changes after read /
   during fill / between Ready and submit; old run continues after lease expiry; second tab submits
   with a stale `run_id`; service worker killed mid-run; upload succeeds but submit fails;
   success-looking UI with no email; instruction-shaped question; a benign essay that over-
   discloses; reordered options; a stale standing Answer; two devices claim at once; network cut
   right after submit. Pairing, the transactional claim, the slot, the lease and the fence are part
   of this step.
2. Backend: claim/slot/fence endpoints on the store; plan endpoint with the disclosure policy.
3. Answers (standing/per-Job, provenance, review), snapshots, the Needs-you tab, deletion.
4. Measure: zero-touch completion; confirmed acceptance; Sent/Needs-you/Stopped(by category)/
   Unknown; transitions; phase durations; re-glance change rate.
5. Ashby, then Lever.
6. Then SmartRecruiters and the Workday account-wall design.

## 10. Review round 1 — findings and resolutions

| # | finding | verdict | resolution |
|---|---|---|---|
| 1–12 | lost-response duplicates; permanent Answers; form mutation; 2xx; widget contract; Google credential; hidden inputs; Question identity; CAPTCHA metric; deadline; atomic uniqueness; provenance | all valid (8 partly) | decisions 3–14, 18 |

## 11. Review round 2 — findings and resolutions

| # | finding | verdict | resolution |
|---|---|---|---|
| 1–14, — | untrusted ATS text; zombie Running; validation before PII; semantic mutation; retry machine; token+URL boundary; context-dependent Questions; brittle predicate; budget ≠ deadline; Unknown graveyard; arbitrary bound; provenance; concurrency valve; CAS concreteness; Stopped fault | all valid (4, 7 partly) | decisions 21–25, 4, 5, 9, 11, 13–15 |

## 12. Review round 3 — findings and resolutions

Two facts checked: `super_squash_history` exists (HF history can be made to forget); the
zero-touch simulation gives **38%**, confirming the evidence challenge.

| # | finding | verdict | resolution |
|---|---|---|---|
| 1 | override breaks "one application ever" | valid, critical | **Attempt** modelled; invariant restated as at-most-once-automatically — 4, 26 |
| 2 | lease ≠ tab ownership | valid, critical | fence: `submit_intent` + pre-click fingerprint; worker drops stale `run_id`/`tab_id` — 27, 36 |
| 3 | HF repo as hot-state DB | valid, architectural | hot/durable split; transactional store; heartbeats never durable; retention policy — 33, 34, ADR-0109 (**owner decides the store**, §13) |
| 4 | LLM boundary: glance is mitigation, not containment | valid, critical | disclosure policy per question category; essays get a named subset; directive filter demoted to defence-in-depth — 24 |
| 5 | Ready→submit race | valid, critical | fingerprint at Ready, re-fingerprint before click — 27 |
| 6 | predicate can still be false-Sent | valid, critical | biased to Unresolved; durable id preferred — 5 |
| 7 | background tab as invariant | valid, high | active execution context — 28 |
| 8 | MV3 worker termination | valid, high | durable Execution record + reconcile — 29 |
| 9 | host_permissions capability vs authorisation | valid, high | separated; optional permissions preferred — 25 |
| 10 | client-supplied origin trusted | valid, high | assertions checked; identity from token + claim; worker uses Chrome's `sender` — 25 |
| 11 | pairing underspecified | valid, high | spec — 30 |
| 12 | local storage: provisioning, 10 MB, unencrypted | valid, high | IndexedDB blob; stated posture; provisioned-browser wording — 10 |
| 13 | content-script messages untrusted | valid, high | worker validates sender; content script requests, never authorises — 25, 27 |
| 14 | iframes undefined | valid, high | top-frame only; CAPTCHA frames recognised — 32 |
| 15 | file upload ≠ field fill | valid, high | upload contract — 31 |
| 16 | directive filter as security control | valid, high | demoted — 24 |
| 17 | Question normalisation undefined | valid, medium | canonicalisation + option set — 8 |
| 18 | standing forever too strong | valid, medium | review windows — 7 |
| 19 | promotion is a policy change | valid, medium | separate explicit act — 7 |
| 20 | queue order undefined | valid, medium | FIFO + cancel — 22 |
| 21 | scheduler ownership | valid, medium | backend slot grant, atomic — 22 |
| 22 | timing not summed | valid, medium | `T_total` 120 s < lease — 21 |
| 23 | "ATS received nothing" too strong | valid, medium | reworded: submit never initiated — 21 |
| 24 | page stale in place | valid, medium | *context invalid* under external — 15 |
| 25 | product language | valid, medium | probabilistic promise — §1 |
| 26 | snapshot retention | valid | retention policy; real deletion — 34 |
| — | 80% reuse ≠ zero-touch | valid | measured: 38%; metric changed — 35 |
| — | Ashby/Lever API wording | valid | corrected — §2 |

## 13. Review round 4 — naming and model clarity

Two wording findings, both accepted; no mechanism changed, but the model got sharper.

| # | finding | verdict | resolution |
|---|---|---|---|
| 1 | **Cleared** is ambiguous — it reads "everything is fine" when it means the opposite of Sent | valid | renamed **Not submitted**; and it became the *single* door to Attempt 2, absorbing what had been a separate override path — 4 |
| 2 | "a state machine over Attempts" is hard to parse; which object owns what? | valid | Application = one Account–Job pair holding Attempts; Attempt = the concrete browser run, owning state / `run_id` / lease / snapshot — 26 |
| — | lease vs fence should be stated as two mechanisms for two problems | agreed | stated that way in CONTEXT.md **Attempt**, ADR-0105 §3 and decision 27 |

The model that came out of it:

```
Application (Account, Job)
├── Attempt 1 : Submitting → Unknown → Not submitted   (verified | asserted)
└── Attempt 2 : Submitting → Sent

lease = this Attempt owns the work, until it goes quiet
fence = an Attempt whose lease lapsed is refused, even if its tab is still alive
```

## 14. The one open decision — where hot state lives (ADR-0109)

| | store | for | against |
|---|---|---|---|
| **A (recommended)** | SQLite/Postgres on the existing private box, behind the router's tunnel | no new vendor; private; transactional; real deletes; matches the router's posture | tunnel is a SPOF the product already tolerates: store down → "auto-apply temporarily off", search unaffected |
| B | managed free-tier store (Turso / Neon / Supabase) | no tunnel; purpose-built | new vendor holding employment data; free tier may not last |
| C | keep HF: one doc per Application, transitions only, nightly squash | nothing new to run | global HEAD contention stays; Space restarts lose in-memory leases; deletion is a nightly squash |

Everything else in this document holds under any of the three.
