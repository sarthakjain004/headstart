# ADR-0106: The LLM plans content once per page; the fill is deterministic per ATS and Stops rather than guesses

**Status:** accepted · **Date:** 2026-09-03 · **Uses `llm_router.ask()` with one addition, a per-call timeout; rejects a tool-calling browser agent and, separately, an LLM "repair crew" for unknown widgets. Sibling of ADR-0105/0107**

## Context

The alternative on the table was the AIHawk/OpenClaw shape: an LLM given browser controls — read
the page, click, type, screenshot — driving each application turn by turn. Its appeal is that it
looks unbreakable: forms differ, an intelligent agent adapts. Four measurements, all from the
2026-09-02 harvest of 3,849 jobs / 37,867 fields across nine ATSes (`experiment/auto-apply/`),
decided against it:

- **73% of question occurrences are exact repeats** of a question already seen, and it compounds:
  once ~2,900 jobs have been seen, **80%** of what a fresh job asks is already answered (Greenhouse
  82%, Lever 83%, Ashby 81%, Workday 77%). An agent that reasons out every field on every
  application redoes solved work four times in five.
- **Only 24% of applications reuse a form shape already seen on that board.** Forms differ
  job-to-job *within one company*, so whole-form plans barely cache either. **The unit that caches
  is the question, not the form.**
- **Each ATS uses 5–13 widget types across all its boards** — Greenhouse 5 across 6,373 fields,
  Workday 6, Lever 7, Ashby 13. Content varies per job; the *mechanics* of operating a form are
  per-ATS and stable. Breakage is a months-scale, per-ATS event that one fix heals for thousands of
  boards — the same maintenance model as the scrapers this repo already runs.
- The browserless Greenhouse agent skill — itself an LLM driving a browser — still hand-codes the
  ARIA-combobox trick, the geocoded location field and the S3 presigned upload. Mechanics knowledge
  lives in a prompt or in code either way; in a prompt it is non-deterministic (the same page
  passes Monday and fails Tuesday with no code change to point at), slow (1–3 min/application),
  untestable, and "intelligent" next to a button that cannot be un-pressed.

`llm_router.ask(prompt) -> str` is single-turn, no tools, no retries — all deliberate (its
docstring). A tool-calling loop is a real change to a module kept thin on purpose.

## Decision

1. **At most one router call per page, and only for the residue.** The Space first applies every
   **Answer** whose exact wording matches — no LLM. Only the leftover Questions go to `ask()`, with
   their options, the **Profile**'s facts, and the Answers relevant to them; the reply is a JSON
   fill plan: proposed Answers for new-wording Questions, essay drafts, and the Questions it cannot
   answer. If nothing is left over, the LLM is not called.
2. **The plan is validated against the live form before any box is touched** — every required box
   has a value, every select value is one of that form's own options. Anything that fails drops out
   of "known" and becomes **Needs you**.
3. **Proposed Answers and essay drafts are Needs you in v1** — pre-filled, so confirming is a
   glance, but never sent unread. **Answers are of two kinds** (CONTEXT.md **Answer**): a *standing*
   Answer is a fact that does not change by Job — work authorisation, clearance, degree, prior
   employment — and a confirmed wording binds to it for good; a *per-Job* Answer is a choice that
   legitimately differs by Job — expected salary, start date, notice period, relocation, preferred
   location — and is never assumed: the last value is proposed for a glance every time, and the
   Account may promote one to standing — a separate, explicitly worded act, never a side-effect
   of confirming a value once. **Standing is a default, not forever**: each standing Answer
   carries when it was last confirmed and is re-glanced once after a review window (initially
   180 days; 90 for kinds that drift — location, employment status, current employer, visa
   status). Simulated on the harvest, per-Job glances alone block 41% of jobs from zero-touch;
   that is the price of never sending a ₹25 LPA answer to a Staff role, and it is reported, not
   hidden. A
   **Question's identity is its wording plus its options**; an Answer that does not pick exactly
   one of a form's options is asked once at the finer grain and kept. **Which kind a Question is**
   — standing or per-Job — is proposed by the model on first sight and confirmed by the Account at
   the first glance, defaulting to per-Job when unsure; wording that points at *this* job ("here",
   "this country", "this role") is per-Job however factual it reads. No "employer policy" kind: if
   an employer reads "authorised" differently, the applicant's truthful answer is still the answer.
   Every Answer carries when it was confirmed, in which Application, its *source* (confirmed,
   promoted, profile-derived) and its use count; a change supersedes rather than edits — and every
   Sent Application keeps a submission snapshot: each field's question text, options, and the
   value actually sent, so an audit six months later does not depend on the Question still
   existing in the same form.
4. **Form mechanics are deterministic, per ATS.** An unrecognised widget or an unexpected page ends
   the Application **Stopped** — the fill halts rather than guesses. There is no second LLM role
   that inspects the DOM and works out how to operate an unknown box; that "repair crew" was
   proposed and rejected on 2026-09-03 for simplicity: fail loudly, fix the adapter, and let the
   per-ATS Stopped count be the repair queue.
5. **The form is read at the click; there is no schema-harvest pipeline stage in v1.** A
   full-corpus harvest is ~1.6 GB (2.6 KB/job × 624k vectors in the store) — near the size of the
   whole HF dataset the pipeline already strains to rewrite. A harvest over the Saved universe was
   considered and deferred: it buys a readiness badge and a faster click at the price of a stage,
   storage and a second source of truth that can drift; the live form must be authoritative anyway.
   The nine adapters in `experiment/auto-apply/harvest_all_ats.py` stay as evidence and are the
   starting point if that stage is ever wanted.
6. **An adapter reads the application's fields, never "every input."** On the HTML-rendered ATSes
   38–65% of inputs are hidden — `accountId`, `origin`, `referer`, `lead_id`, `indeed_apply_success`
   — and some are honeypots whose filling marks the browser as automated. Each adapter identifies
   fields by the ATS's own conventions (Greenhouse `question_*` and its standard names inside the
   application form, Ashby `_systemfield_*` and UUID paths, Lever `cards[…]` and its named fields)
   and never reads a hidden, off-screen, or unclassified input into the plan, nor fills one.
7. **Fill is verified per box, by read-back.** A widget type is not an interaction contract:
   setting `.value` on a React-controlled input leaves the app's state empty, and a `role=combobox`
   is driven by click-flyout-click-option, not assignment. Each adapter specifies, per widget:
   locate → set through the ATS's real interaction (native setter plus dispatched events, or the
   click sequence) → **settle** → read back → compare → **re-scan**. *Settle* means the adapter
   waits for the page to go quiet (no DOM mutation for ~300 ms and no in-flight validation it knows
   about, bounded at 3 s), because a select can trigger asynchronous validation or rewrite another
   field's options after the value is visibly set. *Re-scan* means the schema is read again and a
   filled value that is no longer a legal option is treated as unfilled. A box whose read-back does
   not match is not filled; the Application is **Stopped**.
8. **Forms mutate while being filled, so the fill is a bounded loop, not a pass.** Workday's
   questionnaire carries branching follow-ups; Greenhouse has 117 "if yes, …" dependent fields
   across 439 harvested jobs. After each fill pass the adapter re-reads the form; newly revealed
   boxes are filled *deterministically from standing Answers only* — no second LLM call — and
   anything revealed that is not known becomes Needs you; the plan is re-validated. The loop
   stops — Stopped, category by cause — on any of: three form-state transitions, ten seconds in
   the loop, or the same mutation recurring (a field that reappears after being filled). Three is
   a tuning constant, not a safety property; the time and recurrence bounds are what make it one.
   Measure the transition distribution on Greenhouse before fixing any of the three. This keeps
   the LLM one-shot while dropping the false assumption that the form at read time is the form at
   submit time.
9. **Sent requires that ATS's own success predicate.** A 2xx is not acceptance — a 200 can carry
   `{success: false}`, a 202 means "queued", and some flows decide client-side after the response.
   Each adapter's predicate is **three-valued and biased toward Unresolved**, because a false Sent
   is dangerous and a false Unknown is merely annoying. *Confirmed* requires the strongest evidence
   that ATS exposes to a candidate — a durable application identifier in the response or
   confirmation URL where one exists (to be established per ATS from a real submission in the
   prototype), and otherwise a positive marker **and** the form gone **and** no error banner, with
   the caveat that a frontend can hide the form before its backend has persisted anything;
   *Failed* is a validation error shown with the form still present — evidence the ATS did *not*
   take it, so Needs you and safely retryable; anything else within the observation deadline is
   *Unresolved* → **Unknown** (ADR-0105 §3), never Sent.
10. **The plan call has a hard end-to-end deadline.** The router's 120 s timeout and no-retry rule
    stay, but this endpoint gives it a budget of about 15 s — `ask()` gains a per-call `timeout`,
    the one change to `llm_router` — and on expiry proceeds with the deterministic residue alone;
    everything the model would have answered becomes Needs you. This is the *plan* budget; the
    Application's own bound is the lease and the per-phase deadlines in ADR-0105 §3 — the two are
    different clocks and neither is "the timeout". "A click never blocks" is thereby a property of
    the design, not a hope about the provider.
11. **Validation happens twice, and only the second may submit.** The backend can validate what it
    can see — Questions, standing Answers, proposals — and declares the plan *valid*. It cannot see
    the name, email, phone or résumé, which exist only in the browser (ADR-0107). So the extension
    validates again after merging them and after every read-back: every application field is
    verified from an approved deterministic source — a standing Answer, or locally-held contact and
    résumé data — every required box has a value, every select value is legal. Only that second
    verdict, *ready*, moves the Application to **Ready** (ADR-0105 §3). A résumé that is required
    and missing locally is caught here, not at the ATS. *Ready* also records a fingerprint of the
    form's schema and values; the fence in ADR-0105 §3 re-fingerprints immediately before the
    click and refuses to submit a form that changed in the gap — the browser is concurrent, and
    "validated a moment ago" is not "valid now".
12. **The form's text is untrusted input to the plan call** — ADR-0108 governs what reaches the
    model and how its reply is contained.
13. **File inputs have their own contract; a local file is not a filled field.** Upload is: build
    the `File`, assign via `DataTransfer` to `input.files`, dispatch the events the ATS listens
    for, wait for the ATS's own upload to complete (Greenhouse goes to S3 via presigned fields
    before submit), then verify the ATS's *accepted* state — filename shown, type and size not
    rejected. Only that last state counts as verified.
14. **Frames.** v1 requires the application's fields in the top frame (`all_frames: false`);
    CAPTCHA widgets live in iframes and are recognised as challenges, never filled; a form that
    puts application fields in a frame is Stopped (*adapter*) until the adapter learns it. Frame
    identity is part of field identity.
15. **Question canonicalisation is defined now, not later.** Wording: lowercase, HTML entities
    decoded, Unicode punctuation folded, whitespace collapsed, parentheticals dropped. Options: a
    *set* of the same normalisation — order-insensitive, so Yes/No equals No/Yes, while
    Yes/No/Prefer-not-to-say is a different Question. This affects coverage, not safety.

## Consequences

The hot path has no model in the trust chain: the LLM proposes, the schema validates, the
read-back verifies, a person confirms anything the model originated. Latency is seconds per page
and bounded. The cost that remains is per-ATS adapter maintenance — now explicitly a per-widget
interaction contract plus a success predicate, not a selector list — which the Stopped count makes
visible. Per-Job Answers lower the zero-touch rate by design; promotion to standing hands that
choice to the Account.
