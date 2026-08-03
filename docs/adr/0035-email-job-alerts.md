# ADR-0035: Email job alerts — invite-only, Google-verified, one Digest per pipeline run

- Status: Accepted
- Date: 2026-08-03
- Adds a second delivery channel to the **Alerts** language (CONTEXT.md §Alerts), whose
  Subscriber/Filter/Notification terms are today Telegram-shaped.
- Consumes [ADR-0031](0031-first-seen-index-stamp.md)'s `first_seen` stamp (via the Space's
  `seen_within` filter) as the "appeared in this run" primitive, and the Space's `/search` as the
  ranking engine. Stores state in the private HF dataset of
  [ADR-0020](0020-free-tier-deployment.md). Follows [ADR-0032](0032-llm-access-via-router-behind-a-password.md)'s
  precedent of a gated write endpoint on the Space.

## Context

HeadStart already has an alert path: `headstart/bot.py` registers Telegram Subscribers, stores a
Filter each, diffs new Jobs against a seen-set, and caps at 10 Jobs per Subscriber per run. It
cannot be extended into what is wanted here, for two measured reasons. It matches **keyword
substrings** (`headstart/filters.py::matches`) with no embedding anywhere in the path, so it has no
score to report; and it reads `docs/jobs.json`, the ~2,000-Job curated Feed, not the 274,707-row
Search index. It has also **never run**: it needs `TELEGRAM_BOT_TOKEN`, `STATE_GIST_ID` and
`STATE_GIST_TOKEN`, and `gh secret list` returns exactly one secret, `HF_TOKEN`. Its Gist state
store is therefore unproven infrastructure, not a paved road.

What is wanted: a person signs in, states the role they want in natural language plus structured
Filters, and after each pipeline run receives an email of the Jobs that *appeared in that run*,
ranked against their own query, each with its semantic score and apply link. Access is
**invite-only** — the owner decides which addresses may enable it at all.

The relevant scale: the 2026-08-02T19:11Z run added 3,846 rows and evicted 4,673. The schedule is
every 2h (12/day), but the real cadence is irregular — the last twelve runs took 33–124 minutes and
two of five scheduled runs that day failed on a transient HF 429. The Space caps a page at
`_MAX_K = 100` and returns `score = 1 - cosine_distance` per row.

## Decision

**Google sign-in for identity only; delivery from our own sender.** The feature needs one fact
about the person — a verified address — so it uses the `email`/`profile` scopes, which are
non-sensitive: a client ID, no client secret, and no consent-screen verification. The alternative,
sending *through* the user's mailbox with `gmail.send`, is a restricted scope requiring Google's
CASA security assessment to exceed 100 users, and it would have the account mailing itself.

**Access is deny-by-default and re-checked at send time.** An `ALERTS_ALLOWLIST` secret carries
comma-separated addresses, compared lowercased and trimmed against the **Google-verified** email
from the ID token, never against a client-supplied field. It gates `/subscribe`, and it is checked
again for each Subscriber before a Digest goes out, so removing an address stops mail already
flowing rather than only blocking new signups. The Space reads it at startup, and the pipeline
already restarts the Space every run, so an edit takes effect within one cycle without a deploy.

**Subscriber state lives in the existing private HF dataset, at a top-level `subscribers/` path.**
No new dataset and no new credential: `HF_TOKEN` is the repo's only secret, the Space already
authenticates to that dataset at startup, and the alerts workflow can use the same token. The path
is deliberately *outside* `data/`, so the pipeline's `state_fetch 'data/…'` globs can never pull
personal data onto a runner. `subscribers/` gets an explicit `.gitignore` entry, because the
repo's data ignores are per-subdirectory allowlists — a new path under a public repo is otherwise
committable, which is exactly how a private file nearly shipped once already.

**Two state files, split by writer.** The Space owns `subscribers.json` (records, created on
subscribe, deleted on unsubscribe); the alerts workflow owns `watermarks.json`
(`last_notified_at` per Subscriber). Both stores are whole-file read-modify-write, so a single
shared file would let a watermark write land on a stale read and silently resurrect an
unsubscribed person. Disjoint files remove the class; the workflow's `concurrency` group prevents
two alert runs racing each other. If the Subscriber count ever makes whole-file writes contentious,
the upgrade is one file per record, not a lock.

**"Appeared in this run" is a per-Subscriber watermark, cut exactly.** A fixed two-hour window
would both double-send and drop Jobs given a 33–124 minute spread and skipped runs. Instead the
gap since that Subscriber's own `last_notified_at` is rounded **up** to whole hours (the unit
`seen_within` takes) and the deliberate over-selection is then cut exactly in memory on the
`first_seen` each row already carries. To stop the over-selected window crowding out real matches,
the request asks for `k=100` — the Space's ceiling — and the shortlist is capped at **30** after
the cut. A new Subscriber's watermark is set at signup, so their first Digest covers only what
appeared after they joined; nobody is ever blasted with backlog.

**Ranking is the Space's `/search`, not a re-implementation.** The workflow sends the Subscriber's
query and Filters over HTTP and reads back the same score the UI shows. Recomputing embeddings in
the workflow would duplicate the ranking rules that `headstart/search.py` exists to hold once, and
would let a Digest's scores drift from the same search run in the browser. The cost is a hard
dependency on the Space being awake: the merge job restarts it at the end of every run, so the
alerts workflow always arrives at a cold Space and needs a wake-and-retry budget in the shape
[ADR-0033](0033-state-fetch-retry-budget.md) established. A Subscriber with no query cannot be
served — `/search` returns `[]` for an empty `q` and there would be no score — so the query is
required at signup.

**Sending is a separate workflow gated on pipeline success**, triggered by `workflow_run:
completed` and exiting unless the conclusion was `success`. Email delivery must not share a
failure domain with ingest in either direction: a 429-killed run sends nothing rather than stale
results, and a mail bug can never fail the index.

**The code is a package, `src/headstart/alerts/`**, with `store`, `identity`, `search`,
`shortlist`, `digest`, `mail` and `run` as members. Two properties are load-bearing rather than
cosmetic. `__init__.py` stays **empty**, so the Space importing `alerts.store` executes nothing
else and never drags the spreadsheet or mail dependencies into its image. Members import each
other **relatively** (`from .store import …`), which makes the package position-independent — the
same directory works as `headstart.alerts` in the repo and as a top-level `alerts` once
`deploy-space.yml` copies it in, the way `geo.py` and `llm_router.py` are copied today.
`shortlist` and `digest` are pure functions over data, matching the discipline `bot.py` already
uses so the matching rules stay unit-testable without a network.

**A Digest carries both a linked list in the body and an `.xlsx` attachment** of company, title,
apply link and score. The body is the primary surface because a link is one tap on a phone where
an attachment is download-open-scroll; the spreadsheet exists for working the list at a desk. It
is built with `xlsxwriter` (pure Python, no compiled dependencies) imported inside the render
function. Zero matches sends nothing at all.

**Mail leaves through Resend**, behind the `mail` seam so the provider is one adapter rather than
a fact spread across the sender. Two of its limits are design inputs, not fine print. It needs a
**verified sending domain**: the shared `onboarding@resend.dev` address only delivers to the
Resend account's own mailbox, so reaching invited recipients requires a domain with Resend's
SPF/DKIM records published — the one prerequisite this feature has outside the repo. And its free
tier is 3,000 messages a month **capped at 100 a day**, where the daily cap binds first: twelve
runs a day puts the ceiling near eight always-matching Subscribers, which the invite list is
expected to stay well inside. Exceeding either limit is a paid-tier decision, and the seam means
switching provider is a new adapter, not a change to how a Digest is built.

## Alternatives considered

- **A GitHub Gist as the store**, as `headstart/state.py` does for the bot. A "secret" Gist is
  *unlisted*, not access-controlled — anyone with the URL reads it without logging in. Tolerable
  for Telegram chat ids, wrong for a list of email addresses, and the Gist path has never actually
  run (its two secrets do not exist).
- **A second private HF dataset** for Subscribers. Cleaner separation, but rejected by the owner:
  another repo and another rotation surface for a handful of invited users. The `subscribers/`
  path plus the `data/` glob discipline buys most of the isolation for none of the cost.
- **A hashed allowlist committed to the public repo.** Email addresses are a small, highly
  guessable space — known local-part conventions against known domains — so a published hash list
  is reversible by brute force, not a redaction.
- **Extending the Telegram bot's `matches()`**: no score is derivable from substring matching, and
  the corpus is the 2,000-Job Feed rather than the Search index.
- **A fixed two-hour lookback** instead of per-Subscriber watermarks — see the cadence numbers
  above.
- **Other senders.** Amazon SES is materially cheaper per message and would matter at scale, but
  carries the same domain requirement plus a sandbox-exit request, and its ceiling is irrelevant
  while the invite list is the binding constraint. Gmail SMTP with an app password is the only
  option needing no domain (~500/day), and was rejected because it sends from a personal mailbox
  and stakes that account's reputation on automated mail.

## Consequences

Three new secrets join `HF_TOKEN`: `GOOGLE_CLIENT_ID`, `ALERTS_ALLOWLIST` and `RESEND_API_KEY`.
Personal data enters the system for the first time, which is why the private-dataset
path, the `.gitignore` line, and a rule that addresses are never printed to workflow logs are part
of this decision rather than follow-up hygiene. Each record carries a random unsubscribe token, so
the unsubscribe link needs no session and no signing key.

The Telegram bot is left untouched and still inert. Two alert paths with different matching
semantics is a real duplication, and the intended resolution — should email prove out — is to move
Telegram onto the same `shortlist`, not to grow a second ranking rule.

Nothing ships until a sending domain is verified with Resend; until then the feature is inert in
the same deliberate way the Telegram workflow is inert without its secrets. Volume is bounded by
the invite list by design, and the send-nothing-on-zero rule keeps real usage under the nominal
ceiling — the day Resend's 100/day cap is reached is the signal that this outgrew its assumptions,
not a failure to design for scale.
