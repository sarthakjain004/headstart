# ADR-0105: Applications are submitted from the Account's own browser

**Status:** accepted · **Date:** 2026-09-03 · **First decision of the auto-apply feature; introduces CONTEXT.md §Applying (Application, Question, Answer). Sibling of ADR-0106 (how the fill is planned), ADR-0107 (where the applicant's PII lives) and ADR-0108 (the two trust boundaries)**

## Context

HeadStart indexes ~426k tech Jobs across 22 ATSes and wants a one-click *auto-apply*: the user
clicks beside a Job and the application is submitted with no further clicks or typing. The obvious
build is server-side — HeadStart already holds the Job, so build the payload and POST it to the ATS.

Measured live on 2026-09-02, that path does not exist for a candidate:

- **There is no unauthenticated, candidate-scoped submission API on any major ATS.** Greenhouse's,
  Lever's, Ashby's (`applicationForm.submit`) and SmartRecruiters' application endpoints all exist
  and all authenticate as the *employer's* API key. The public forms are what a candidate gets.
- **Every public form probed is CAPTCHA-gated at submit.** Greenhouse's embed POST needs a
  `csrfToken`, a page `fingerprint` and a reCAPTCHA Enterprise token bound to the same browser
  session ("cannot be replayed by curl"). Lever's and Recruitee's forms carry `h-captcha-response`;
  Ashby's page bootstrap ships a `recaptchaPublicSiteKey`; Freshteam, Trakstar, Join and Eightfold
  run reCAPTCHA; Darwinbox and Rippling run Cloudflare Turnstile.
- **Reading the form is free everywhere submitting is not.** Nine ATSes hand over the complete
  application schema unauthenticated (Greenhouse `?questions=true`, Ashby's `non-user-graphql`,
  Recruitee's `/api/offers/`, Workday's `/wday/cxs/{tenant}/questionnaire/{id}`, Rippling's job
  detail, and the Lever/Freshteam/Teamtailor/Trakstar apply pages). The asymmetry is the whole
  design: prepare anywhere, submit only where a person is.
- Server-side submission would also mean datacenter IPs and machine-scale velocity — the two
  signals employer-side fraud tooling flags — and *bypassing* a CAPTCHA is where "a tool applying
  with my real credentials" turns into CFAA exposure. Workday adds a per-tenant candidate account
  with email verification before the form even opens.

## Decision

1. **A Chrome extension is the deliverable.** The click on HeadStart hands the Job's URL to the
   extension, which opens the ATS page in a background tab in the Account's own browser. A content
   script reads the live form, the Space plans the fill (ADR-0106), the extension fills and clicks
   submit. HeadStart's servers never POST to an ATS.
2. **The extension never bypasses a CAPTCHA.** An invisible one is allowed to score the session; a
   challenge ends the **Application** as **Needs you** ("clear the challenge"). Retrying from the
   queue is attended, so the challenge is solved live then.
3. **An Application is one Account–Job pair holding one or more Attempts; an Attempt is the
   concrete browser run that tries to submit it.** The Attempt is what carries state, `run_id`,
   lease and submission snapshot; the Application carries the pair and the history. States
   **Queued → Running → Ready → Submitting**, ending **Sent**, **Needs you**, **Stopped**, or
   **Unknown** — the uncertainty state, which is settled only by evidence, into **Sent** or **Not
   submitted**. The invariant is *at most once automatically*: one Application per Account per Job,
   ever; normally one Attempt; a second **only** once the first is settled Not submitted — either
   *verified* (independent evidence: the ATS's confirmation email absent and an "already applied"
   state absent where the ATS exposes one) or *asserted* (the Account's explicit choice, warned it
   may apply twice). Settling does not overwrite the Attempt: it resolves that Attempt and opens a
   new one beside it, so the history reads "attempt 1 Unknown → Not submitted, attempt 2 Sent".
   **Lease and fence are different mechanisms.** The lease is temporary ownership — renewed by
   heartbeat, expiring when the browser goes quiet — and tells the server who *should* own the
   work. It cannot stop a tab that is still executing, so the fence does that: every privileged
   operation carries its `run_id`, and one that is no longer current is refused. Lease without
   fence is a hotel key that still opens the door after checkout. The transitions:

   | from | to | when |
   |---|---|---|
   | (click) | Queued | claimed atomically; FIFO by claim time; a Queued Application may be cancelled |
   | Queued | Running | the backend grants this Account's single execution slot — atomically, never the extension's local queue, which cannot serialise across devices |
   | Running | Needs you | unanswered / glance / per-Job / challenge; plan budget expired with residue; lease expired **before submit was ever initiated** |
   | Running | Stopped | unrecognised widget, page, or mutation bound; or the page went stale under us (reason: adapter / external / browser / *context invalid*) |
   | Running | Ready | browser-side validation passed with nothing pending (ADR-0106 §11) and a schema+value fingerprint taken |
   | Ready | Submitting | **fenced**: the extension asks the backend for `submit_intent(application, run_id)`; granted only if that `run_id` is current, the lease valid, the state Ready — *and* the extension re-fingerprints the form immediately before the click and it matches. Either check failing → back to Running |
   | Submitting | Sent | that ATS's predicate returns *Confirmed* (ADR-0106 §9) |
   | Submitting | Needs you | *Failed*: a validation error with the form still present |
   | Submitting | Unknown | *Unresolved* within the observation deadline; or lease expired in this state |
   | Needs you, Stopped | Running | attended retry, same Attempt, always from the live page |
   | Unknown | Sent | independent evidence it went through (the ATS's confirmation email; an "already applied" state) |
   | Unknown | Not submitted | settled *verified* (evidence it did not go through) or *asserted* (the Account's warned choice) |
   | Not submitted | Queued (new Attempt) | the only door to a second Attempt |

   **Lease, fence, execution context.** Running/Ready/Submitting hold a lease (3 min) renewed by
   a 10 s heartbeat carrying a `run_id`; every server transition is compare-and-swap on it. The
   lease alone is a *timeout*, not ownership — an expired tab can still press a button — so
   ownership is the fence: nothing irreversible happens without `submit_intent` granted for the
   current `run_id`, and the service worker discards any content-script message whose
   `run_id`/`tab_id` is not the one it is executing. The Application needs an *active execution
   context*; whether that is a background or a foreground tab is an adapter/measurement choice
   (ADR-0105 §8), not a state-machine invariant. **Wall clock:** per-phase deadlines (read 20 s,
   plan 15 s, fill 30 s incl. settle and upload, observe 20 s) sit under a hard `T_total` of
   120 s, and the 3 min lease sits above that. A run that ends before submit was *initiated* ends
   Needs you — not because the ATS "received nothing" (Greenhouse uploads the résumé to S3 before
   submit; some forms autosave) but because no *application* was submitted.
   **Serial per Account** (owner-confirmed 2026-09-03): one execution slot per Account, granted by
   the backend; the rest Queued in click order. Not pacing — nothing delays a send at its turn.
   **Unknown** (owner-confirmed): the uncertainty state, never retried on its own. Because some
   ATSes send no confirmation email, evidence is often unavailable — so the Account may *assert*
   Not submitted rather than verify it. That is a per-Job, warned, recorded act, and it produces a
   second Attempt beside the first, never a silent duplicate; a permanently un-appliable Job is
   the worse failure.
   **Stopped carries a category** — *adapter* (the repair queue), *external* (posting closed,
   maintenance, login wall, redirect off origin, *or the page going stale in place*: session
   expired, form version changed, application disabled), *browser* (crash or phase timeout).
   **Where this state lives** is ADR-0109's question; this ADR only requires that claim, slot
   grant, and every transition be atomic and that heartbeats never be durable writes.

4. **v1 supports Greenhouse, Ashby and Lever** — 20% of tech Jobs (board-priority ledger,
   2026-09-03), single-page forms, no account wall, 5/13/7 widget types respectively, full harvested
   evidence. The button is hidden on every other ATS; no half-working states. Workday (32%) is
   deferred deliberately: its per-tenant account makes first contact with every company a Needs-you
   by construction, and its form is a five-page wizard — the biggest prize and the worst place to
   learn.
5. **No pacing of sends** — chosen 2026-09-03 with the velocity risk on the table. The click is the
   user's decision. Revisit if deliverability measurably suffers; adding a delay is cheap.
6. **No fit gate.** The user chose the Job; knockout questions are ordinary Questions answered
   truthfully, and the employer's own knockout does what it was built to do.

7. **The extension never holds the Google credential.** Sign-in stays on the website; the account
   page issues a one-time pairing code; the extension exchanges it at the Space for a short-lived
   token scoped to the Applications endpoints, revocable from the account page. The extension has
   broad rights over ATS pages, which is exactly why its credential must be narrow.
8. **The metric is zero-touch completion and confirmed acceptance, not question reuse.** The
   harvest's 80% question-reuse figure is a ceiling on one input; simulated against the same data,
   only **38%** of jobs have *every required field* covered by a bank built from the prior 75% —
   41% are blocked by an unknown question, 41% by a per-Job glance, 13% by an essay — before
   CAPTCHA, upload or browser failure. The product promise is therefore probabilistic and says so:
   *one click starts it; many finish unattended; whatever needs a person appears in Needs you.*
   The first Greenhouse experiment measures the real split with the ATS's confirmation email as
   ground truth, in both a background and a foreground tab, before either is fixed.

## Consequences

A second codebase (the extension), Chrome-only for v1, and a store listing and permission model
users must accept. The per-ATS **Stopped** count becomes the maintenance signal — it is how the day
Greenhouse changes its combobox markup is noticed, without an LLM papering over it (ADR-0106).
