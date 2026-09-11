# ADR-0133: The Résumé tab reads the Saved jobs the page already fetched

**Status:** accepted · **Date:** 2026-09-11 · **Extends ADR-0124.** Nothing about a **Tailoring**
changes shape: it has carried a `jobId` since it was written. This says where that id comes from,
and it is the first change that ever supplies one.

## Context

`Cmd.addTailoring(name, jobId)` (`resume_document.js`) has accepted a job id since ADR-0124, and
its only call site passed one argument. So every Tailoring ever made recorded `null`, while
CONTEXT.md's **Tailoring** entry already claimed it names "the **Job** it was written for where
one is known". The tab asked the visitor to *type out* a job the app was showing them two tabs
away.

Making that true needs the **Saved job** rows in the Résumé tab. There were two readings of what
that costs, and they disagreed by a server route:

1. `resume_sync.js`'s entire surface is `/resumes`, so the Résumé tab has no way to reach saved
   jobs — it needs a new endpoint, or at least a new fetch.
2. `app.js` already holds them, on the same page, in `mySaved`.

**Reading 2 is the true one, and it was settled by reading the code rather than by argument:**

- `base.html` loads `static/app.js` and `static/resume/*.js` into **one document**, and
  `{% include "resume.html" %}`s the panel into it. There is no separate résumé page. The editor
  already reads `window.localStorage` and `window.addEventListener` directly; `window` is not a
  boundary it has been keeping.
- `app.js` fetches `GET /saved` once on load wherever the Saved tab is rendered, and keeps
  `mySaved` current through every star and unstar (`toggleStar`, including its rollback paths).
- `deploy/hf-space/app.py`'s `/saved` already returns every field the picker needs —
  `job_id`, `title`, `company`, `url`, `location`, `salary`, `starred_at`, plus `open`.

## Decision

**The Résumé tab reads the Saved jobs through one seam on `window`, and never fetches them.**

```js
// app.js
window.savedJobs = () => (mySaved ? mySaved.slice() : null);
```

Four things are load-bearing:

1. **A getter, not a subscription.** The editor calls it when the "Tailor for a job" menu opens.
   `app.js` does not know the Résumé tab exists, and the Résumé tab holds no reference to
   `app.js` — the tab degrades to exactly what it was if the seam is absent, which is how it
   behaves on a deployment with no account store.
2. **Null is not `[]`.** Null means *there is no such list* — signed out, no account store, or a
   `/saved` that has not answered. `[]` means *signed in, nothing starred*. The picker is not
   rendered at all for the first and says so out loud for the second, and conflating them would
   tell an account with no stars that the feature does not exist.
3. **A copy.** `mySaved` is the Saved tab's own list; a reader that sorted it in place would
   reorder that tab from inside a menu. (The editor slices again before sorting — the seam's copy
   is `app.js` protecting its state, not a promise the editor is allowed to lean on.)
4. **No second fetch.** A duplicate `GET /saved` from `resume_sync.js` would cost a second round
   trip *and* be able to disagree with the Saved tab the moment a star is toggled — two lists,
   one truth, and no way to tell which one is stale.

**A Tailoring keeps the job's id and its own name, and nothing else of the posting.** The name
defaults to `Company · Title` and is the visitor's to change before the version is created —
which is the only chance, since nothing renames a Tailoring afterwards. That name is what still
reads once the star is gone: a **Saved job** is already a copy taken at star time (ADR-0042), so
a third copy inside the résumé document would be one more thing to keep in step and one more
thing to go stale.

## Consequences

- The picker is offered only where there is something to pick, so the free-text field remains the
  fallback and the empty state rather than becoming dead weight.
- **A known gap, accepted deliberately:** a visitor who opens the menu while `/saved` is still in
  flight sees no picker and no explanation. Reopening the menu fixes it. Closing it properly means
  either a third state in the contract or a notification when the fetch lands — a subscription,
  which is a dependency rather than a seam. Revisit only if it is actually hit.
- `jobId` is still read by nothing. It is now *recorded*, which is what a later feature — a
  Tailoring that can quote the posting it is for — would have to have, and what CONTEXT.md has
  claimed all along.
