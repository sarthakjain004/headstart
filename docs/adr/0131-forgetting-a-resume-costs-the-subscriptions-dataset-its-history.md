# ADR-0131: Forgetting a résumé costs the Subscriptions dataset its whole history

**Status:** accepted · **Date:** 2026-09-10 · **Amends ADR-0124**, which decided *where* a Résumé
document is stored and left four things open that building it forced. This ADR settles those four
and nothing else; ADR-0124's six decisions all stand.

## Context

ADR-0124 accepted an opt-in account copy of a **Résumé document** — one JSON file per document in
the private Subscriptions dataset — and was unusually specific about the record, the path, the
identifier and the write cadence. It also wrote one sentence that made the feature unbuildable
until this ADR: *"Until that scheduled job exists, syncing does not ship."* The job in question is
a scheduled `super_squash_history` that makes deletion real, and ADR-0124 assumed it without
weighing what it costs on **this** repo.

Four things were genuinely open. Each is settled below, with the option that was rejected.

## 1. The retention job squashes a repo full of irreplaceable records — and ships anyway

**Decision: build it.** `.github/workflows/squash-subscribers-history.yml`, weekly, green no-op
until the deployment's secrets are set.

The cost is not the one the sibling workflow pays. `squash-dataset-history` collapses the *index*
dataset, and its stated justification is that "everything in the dataset is derived state the
pipeline regenerates". **That reasoning does not transfer here and is not borrowed.** The
Subscriptions repo holds Profiles, Saved sets, Saved jobs and Subscriptions — user records with no
generator behind them. Squashing destroys every earlier revision of all of them. That is a real
loss of rollback for data nobody can recompute, and it happens on a schedule, weekly, forever.

It is accepted for three reasons, in ascending order of weight:

1. **Writes are per-record files** (the property `store.py`'s docstring calls a correctness
   decision rather than a scaling one). A bug can damage the record it was writing and no other,
   so the blast radius a rollback would be recovering from is one Account's one record.
2. **The recoverable records are the cheap ones.** A Saved set is a query and five filters; a star
   is a button press; a Profile is one paste and one AI read. Every one of them is minutes of a
   user's time to rebuild.
3. **The one irreplaceable record is the one whose working copy is not here.** A Résumé document
   is hours of writing — and ADR-0124 decision 3 puts the working copy in the Account's own
   browser and makes HF a sync target. The squash never touches the live tree (checked, before
   success is announced, against the file *map* rather than a byte total), so the account copy
   survives it; and the browser copy survives it regardless. The record whose history is worth
   most is the record that has a second copy.

Against that sits a promise the product cannot otherwise make. A résumé holds employment history,
education and a way to contact somebody. "Delete" that leaves the document readable forever, to
anyone holding the token, is not deletion, and ADR-0124 said so first. **Not squashing was the
alternative** — keep the history, and word the UI as "removed from your account; a copy may remain
in our backups indefinitely". It was rejected: nobody switches on a feature described that way,
and describing it any other way would be a lie.

**Weekly rather than monthly, and the reason is schedule reliability rather than urgency.** GitHub
drops scheduled events silently — measured in this repo twice — so a monthly cron aimed at a
30-day promise has no margin: one dropped fire and the sentence in the product is false. Weekly
leaves three consecutive misses of room, and the job costs two API calls.

**The concurrent-write race is named, not solved.** The Space and the alerts run write this repo
while it is being squashed. The window is one API call wide, it runs at 03:17 UTC on a Sunday, and
the before/after file-map check catches the detectable shapes of it. It deliberately takes its own
concurrency group rather than sharing `email-alerts`: only one run may be pending per group, so a
Digest fired by the pipeline would displace a queued squash and silently disable the retention the
promise rests on — the exact failure `squash-dataset-history`'s own header documents.

## 2. Two browsers, one document: refuse, and keep both

ADR-0124 decision 4 already chose the shape — a `rev` the client increments, a push refused unless
it is exactly one past what is stored, and the loser kept beside the winner as "… (this device)".
What was open is where `rev` lives and what happens at the edges.

**Decision: `rev` is a plain integer field on the document**, beside `sync`, both declared in
`resume_document.js`'s Builder. No Command touches either, so neither enters the undo stack and
neither moves `updatedAt`. Since the stored record is the client's export unchanged (ADR-0124
decision 1), putting the counter anywhere else would have meant a second file or a second schema —
the two things that decision exists to avoid.

Three edges the decision did not cover:

- **The revision is written back only after the server answers.** Incrementing optimistically
  leaves a failed push claiming a revision the server never saw, and every later push refused
  forever. The push sends `rev + 1`; the local document keeps its old `rev` until a 200 comes back.
- **A push whose round trip outlived the user's typing must not restore the pre-push words.** The
  answered revision is stamped on the document *as it stands now* (the open one, or storage's),
  never on the stale payload that was sent. This is the same trap `Store.adopt` documents one
  level down, and it costs a copy of the résumé if got wrong.
- **No stored record accepts any revision.** A strict `rev == 1` would turn "the other device
  deleted it" into a conflict against an empty slot that no amount of retrying resolves. There is
  nothing to lose when nothing is stored — the counter guards stored content, and there is none.

**The check is read-then-write, not a transaction.** Two pushes inside the same round trip can
both read the same `rev` and both be accepted. That window is what ADR-0124 weighed a transactional
store for and declined; it is stated in the route's docstring rather than hidden, and both devices
still hold their own copy in the browser.

## 3. Signed out, or no account at all, degrades to exactly ADR-0123

**Decision: two mechanisms, because there are two different absences.**

- **The deployment cannot store anything** — no sign-in wall, or no Subscriptions dataset (the
  local dev server, a fork, a Space with no secrets). The switch is **not rendered at all**:
  `resume_sync_on` is a template flag, like `sets_on` and `profile_on` before it, and an undefined
  Jinja name renders falsy. A control offering to store something nothing can store is worse than
  no control.
- **This session is signed out** — the wall is on, the session expired mid-edit. The switch renders
  **disabled and says why** ("Sign in to use this"), a push in flight answers 401 and reports it
  stickily, and the browser copy is untouched. The one state this must never sit in is a switch
  reading "on" while nothing is being stored.

Everything else — an offline machine, a Hub outage, a refused write — leaves the dirty document
marked dirty and tries again at the next coarse event. **Nothing on the Résumé tab blocks on the
network**, which is ADR-0124 decision 3 restated as an implementation rule.

## 4. Sync is a layer beside the Repository, not a second implementation of it

ADR-0124's own header predicted the account sync would be "a second implementation of six methods
rather than a rewrite" against `resume_repository.js`'s interface. **That prediction is withdrawn,
by ADR-0124's own decision 3.** The Repository is what the editor reads and writes on every
keystroke; an implementation of it that spoke to the network would put a Git commit behind a
keypress, which is the exact thing decision 1 of that ADR forbids.

So `resume_sync.js` is a separate module with its own vocabulary — `note`, `flush`, `setEnabled`,
`pull`, `forget` — and the cadence lives in it:

| event | what fires it | gate |
|---|---|---|
| explicit save | the switch going on, or "Save now" | — |
| tab going away | `visibilitychange` → hidden | dirty |
| heartbeat | at most one push per **3 minutes** while editing | dirty |

Three minutes is the floor **between** pushes, not a delay on the first: after a quiet spell the
first completed local write goes up promptly and everything behind it waits the floor out, so no
three-minute window ever holds two pushes. Being prompt costs one commit per editing session; the
alternative leaves the opening minutes of writing on one machine while the switch says otherwise.
A document nobody edited is never pushed at all, so opening one costs nothing.

All three are gated on the document having actually changed, so alt-tabbing ten times costs
nothing. **The trigger is a completed local write, not a keystroke and not a repaint** — a new
`onSaved` hook on the client Store, rather than its `subscribe`, because `subscribe` also fires on
`adopt` and a sync driven by it would push a document nobody had edited every time one was opened.
`beforeunload` is deliberately *not* a trigger: a push is a round trip a closing page will not
finish, and the browser copy is already safe by then.

## Consequences

One thing ADR-0124 did not mention had to be built for the feature to be worth having: **the
restore path**. Sync that cannot be read back solves nothing — the problem being solved is "one
cleared cache from gone". So `GET /resumes` lists what an Account holds, `GET /resumes/{id}`
returns one, and the Résumés popover shows account-only documents as a second group of rows that
open a copy here. This is the only place the account copy is ever *read*, which keeps it a sync
target rather than a source.

Two bounds exist and are abuse bounds, not product promises: **10 documents** per Account, and
**512 KB** per document (the worked example serialises to about 8 KB). The size bound is
whole-record because the record is opaque here by design — there are no fields to bound
individually.

Three sentences shipped in the product were true when written and became false the moment this
merged, and are rewritten in the same change: `resume_repository.js`'s header ("no endpoint, no
upload… every word stays on the machine it was typed on"), `resume.html`'s ("no résumé text ever
reaches the server"), and the Résumés popover's own storage line (which said account saving was
"planned"). The Data tab's *What is stored about you* list gains a bullet naming the account copy,
what it holds, and the 30-day deletion window — which is a claim the workflow in §1 is what makes
true.
