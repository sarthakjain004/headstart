# ADR-0035: Email job alerts — invite-only, Google-verified, one Digest per pipeline run

- Status: Accepted; [ADR-0042](0042-signed-in-ui-saved-sets.md) generalizes the Subscription to
  "the one Saved set per Account with email on", and [ADR-0043](0043-saved-sets-subscription-projection.md)
  implements it as a projection — adding the sets endpoints as a third Subscription writer
  beside subscribe/unsubscribe and the alerts run (delivery, Watermark, and invite gate stand);
  [ADR-0069](0069-sets-own-their-projection-against-the-allowlist.md) then resolves those writers,
  making this ADR's "an entry's own Query is authoritative" conditional on the Account keeping no
  Saved sets
- Date: 2026-08-05
- Adds a **Subscription** to the Alerts language (CONTEXT.md §Alerts), deliberately *beside*
  Telegram's Subscriber/Filter rather than reusing those terms.
- Consumes [ADR-0031](0031-first-seen-index-stamp.md)'s `first_seen` stamp as the "appeared in this
  run" primitive, and the Space's `/search` as the ranking engine. Follows
  [ADR-0032](0032-llm-access-via-router-behind-a-password.md)'s precedent of a gated endpoint on the
  Space, and its injected-callable seam (`query_for(text, ask=…)`).
- Adds a second private HF dataset alongside [ADR-0020](0020-free-tier-deployment.md)'s
  `headstart-index` — the first data the Space writes rather than reads.

## Context

HeadStart already has an alert path: `headstart/bot.py` registers Telegram Subscribers, stores a
Filter each, diffs new Jobs against a seen-set, and caps at 10 Jobs per Subscriber per run. It
cannot be extended into what is wanted here, for two measured reasons. It matches **keyword
substrings** (`headstart/filters.py::matches`) with no embedding anywhere in the path, so it has no
score to report; and it reads `docs/jobs.json`, the ~2,000-Job Feed, not the 274,707-row Search
index. It has also **never run**: it needs `TELEGRAM_BOT_TOKEN`, `STATE_GIST_ID` and
`STATE_GIST_TOKEN`, and `gh secret list` returns exactly one secret, `HF_TOKEN`. Its Gist state
store is unproven infrastructure, not a paved road.

What is wanted: a person signs in, states the role they want as a **Query** plus a set of **Search
filters**, and after each pipeline run receives an email of the Jobs that *appeared in that run*,
ranked against their Query, each with its semantic score and apply link. Access is **invite-only** —
the owner decides which addresses may enable it at all.

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

**The Space installs `google-auth[requests]`, and the extra is load-bearing.** `google-auth`
declares `requests` as an extra rather than a core dependency, and `google.auth.transport.requests`
answers its absence with a bare `ImportError` — not `ModuleNotFoundError` — so plain `google-auth`
resolves, installs and imports cleanly right up until a real credential arrives, then turns every
sign-in into a 401 reading `sign-in could not be verified: ImportError`. Nothing else in the image
supplies `requests`: `huggingface_hub` moved to httpx. This shipped that way and made the feature
unusable while looking configured — the panel rendered, the client id was live, and the only
symptom was a 401 nobody was watching. It is invisible to every seam this ADR chose: the `verifier`
argument stands in for Google in `tests/test_alerts_identity.py`, `tests/test_space_app.py` stubs
`sys.modules` to import `app.py` at all, and CI's quality job installs no extras. That is the cost
of the lazy import below, taken deliberately — so the requirements line itself is now the assertion,
in `test_space_requirements_ask_for_the_google_auth_requests_extra`.

**The allowlist drives the run, and an entry may carry the Query itself.** *(Amended
2026-08-06.)* As first built, a Subscription existed only once its owner had signed in with
Google, and the allowlist merely gated who was permitted to — which meant enrolling somebody was
a two-party operation: the owner edits a file, then that person signs in. For an invite list of
people who are simply told "you're on it", that second step is the whole cost of the feature. So
an allowlist entry may now be an object carrying `query` and `filters` rather than a bare
address, and `run` iterates *invites* rather than stored Subscriptions, minting the Subscription
on first sight. Bare strings still mean self-serve — invited, Query supplied at sign-in — so the
Google path is unchanged for anyone who wants to choose their own.

Three consequences are load-bearing rather than incidental.

**An address struck off the list is never reached at all**, so removal stops mail without hunting
down the record. That is stronger than the previous re-check for stopping mail, and weaker for
cleanup: the orphaned `subscriptions/{id}.json` is never deleted, so re-adding that address later
resumes from its old Watermark and mails the whole intervening backlog. Reconciling orphans is
deferred, not solved.

**A Subscription is stored before any send**, on both creation and revision. Its Watermark starts
at now and `send_one` persists only after a Digest is accepted, so a first run matching nothing
would otherwise leave the record unwritten, re-mint it with a fresh Watermark next run, and
restart the window forever — nobody would ever be mailed.

**An entry's own Query is authoritative; the file's `default_query` is only a seed.** The two are
carried separately rather than folded together at parse. An entry's Query is a statement about
that person, so it overrides what they last chose and is applied through `revised`, keeping the
Watermark and the unsubscribe token. A default is a statement about nobody in particular, so it
may seed an address with no record yet and is ignored for anyone who has one. Folding them
together — the first cut of this change — meant a default silently overwrote a signed-in person's
own Query on every run, forever, which would have made the Google path worse than useless for
anyone it was left switched on for.

**Subscriptions live in their own private dataset, `imPoseidon/headstart-subscribers`, and the
Space holds a write token scoped to it alone.** This is the security posture of the feature, not a
filing preference. The Space's existing `HF_TOKEN` is a fine-grained **read** token scoped to the
index dataset (`docs/agents/deployment.md`), so it cannot write Subscriptions at all; the only
alternative to a second repo is widening *that* token to write, which hands a public,
unauthenticated web app the ability to overwrite `data/lancedb` and the 400k-vector embedding
store. HF tokens are scoped per repository, so there is no path-level middle ground. A separate
repo makes the worst case "lose the Subscriptions" instead of "lose the index", and keeps the
index unwritable from the web.

**One file per Subscription — `subscriptions/{id}.json` — so there is no read-modify-write.** A
single JSON blob would lose a record whenever two people signed up inside one request window, and
Flask serves requests concurrently; that is a correctness bug at two users, not a scale concern.
Per-record files make writes disjoint by construction: subscribe is an upload, unsubscribe is a
delete, and the Watermark rides inside the record the workflow already rewrites. This also
dissolves the Space-writes-here / workflow-writes-there split an earlier draft needed.

**Access is one stored policy, checked twice.** `subscriptions/allowlist.json` in the same repo is
the single list of permitted addresses, read by the Space at subscribe time and by the workflow
before each send — so striking an address off stops mail already flowing, and there is no second
copy in a second secret to drift out of sync. It is compared against the **Google-verified** email
from the ID token, never a client-supplied field. Allow-listing is HeadStart policy, not a Google
fact, so it lives in its own module; `identity` verifies a token and returns an address, and
nothing else.

**"Appeared in this run" is an exact cutoff, not an hour-granular window.** The Space's filter
builder gains `first_seen_after=<ISO instant>` beside the existing `seen_within` hours. Without it
the workflow would have to round the gap since the last Digest *up* to whole hours and cut the
surplus in memory — and that surplus competes for the same 100-row ceiling, so a Digest could come
back short with no signal that it had. `first_seen` is written by us and already compared as an
ISO string, so the addition is a `>` on a column that exists. Because
`deploy/hf-space/app.py::_build_filter` is the reference implementation and currently has **no test
coverage anywhere** (`deploy/` sits outside `testpaths`), the parameter ships with tests for the
clause it builds. *(Since [ADR-0042](0042-signed-in-ui-saved-sets.md) the reference builder is
`headstart.search.build_filter`, tested directly in `tests/test_search.py`.)*

**Each Subscription carries its own Watermark, and delivery is at-least-once.** A fixed two-hour
lookback would both double-send and drop Jobs given a 33–124 minute spread and skipped runs. The
Watermark advances **only after Resend accepts the Digest** — the deliberate consequence being that
a crash between send and write re-sends at most one capped Digest, where the opposite order would
silently swallow a window and tell nobody. `bot.py` already has the losing version of this choice
(it saves state after sending, so a send crash loses its offset); this ADR does not inherit it. A
new Subscription's Watermark is set at signup, so its first Digest covers only what appeared after
it joined.

**Ranking is the Space's `/search`, not a re-implementation.** The workflow sends the Query and
Search filters over HTTP and reads back the same score the UI shows, capped at the best **30**.
Recomputing embeddings in the workflow would duplicate the ranking rules `headstart/search.py`
exists to hold once, and would let a Digest's scores drift from the same search in the browser. The
cost is a dependency on the Space being awake: the merge job restarts it at the end of every run,
so the workflow always arrives at a cold Space and needs a wake-and-retry budget in the shape
[ADR-0033](0033-state-fetch-retry-budget.md) established. A Subscription with no Query cannot be
served — `/search` returns `[]` for an empty `q` and there would be no score — so the Query is
required at signup.

**Sending is a separate workflow gated on pipeline success**, triggered by `workflow_run:
completed` and exiting unless the conclusion was `success`. Email delivery must not share a failure
domain with ingest in either direction: a 429-killed run sends nothing rather than stale results,
and a mail bug can never fail the index.

**The code is a package, `src/headstart/alerts/`**, with `store`, `identity`, `access`,
`space_query`, `shortlist`, `digest`, `mail` and `run` as members. (The Space client is
`space_query`, not `search`: `headstart/search.py` already holds the ranking conventions, and
Rule 3 rejects near-homographs that are hard to grep and easy to misread in a traceback.) Two properties are load-bearing rather than
cosmetic. `__init__.py` stays **empty**, so the Space importing `alerts.store` executes nothing else
and never drags the spreadsheet or mail dependencies into its image. Members import each other
**relatively** (`from .store import …`), which makes the package position-independent — the same
directory works as `headstart.alerts` in the repo and as a top-level `alerts` once
`deploy-space.yml` copies it in, the way `geo.py` and `llm_router.py` are copied today.

**I/O is injected as plain callables, not adapter classes.** `shortlist` and `digest` are pure
functions over data; everything that touches the network is passed in, exactly as
`resume_query.query_for(text, ask=llm_router.ask)` already does in the Space's own request path, and
faked in tests with `monkeypatch.setattr` as `tests/test_http.py` does. The repo has no `conftest.py`
and no mocking library, so formal adapters for four seams would introduce an idiom nothing else here
uses — and `identity` and `mail` have exactly one real implementation each, which is a hypothetical
seam, not a real one.

**A Digest carries both a linked list in the body and an `.xlsx` attachment** of company, title,
apply link and score. The body is the primary surface because a link is one tap on a phone where an
attachment is download-open-scroll; the spreadsheet exists for working the list at a desk. It is
built with `xlsxwriter` (pure Python, no compiled dependencies) imported inside the render function.
Zero matches sends nothing at all.

**Mail leaves through Resend**, behind an injected send callable so the provider is one substitution
rather than a fact spread through the sender. Two of its limits are design inputs. It needs a
**verified sending domain**: the shared `onboarding@resend.dev` address only delivers to the Resend
account's own mailbox, so reaching invited recipients requires a domain with Resend's SPF/DKIM
records published — the one prerequisite this feature has outside the repo. And its free tier is
3,000 messages a month **capped at 100 a day**, where the daily cap binds first: twelve runs a day
puts the ceiling near eight always-matching Subscriptions, which the invite list is expected to stay
well inside.

## Alternatives considered

- **Keeping Subscriptions in the index dataset** (no second repo). Rejected on token scope: the
  Space would need write access to the repo holding `data/lancedb` and `data/embeddings`, so a
  compromise of a public web app becomes a compromise of the Search index. The second repo costs one
  more token to rotate and buys a blast radius that stops at the Subscriptions.
- **A GitHub Gist as the store**, as `headstart/state.py` does for the bot. A "secret" Gist is
  *unlisted*, not access-controlled — anyone with the URL reads it without logging in. Tolerable for
  Telegram chat ids, wrong for email addresses, and a `gist`-scoped token reaches every Gist the
  account owns. The path has also never actually run.
- **A hashed allowlist committed to the public repo.** Email addresses are a small, highly guessable
  space — known local-part conventions against known domains — so a published hash list is reversible
  by brute force, not a redaction.
- **Reusing `Subscriber` and `Filter` for the email path.** CONTEXT.md already warns that `Filter`
  and **Search filter** "share a word and nothing else"; a Subscription is matched by *Search
  filters* and ranked by a *Query*, so reusing either term would make three referents for one word.
- **Extending the Telegram bot's `matches()`**: no score is derivable from substring matching, and
  the corpus is the 2,000-Job Feed rather than the Search index.
- **A fixed two-hour lookback**, or an hour-granular `seen_within` with an in-memory cut — see the
  cadence numbers and the truncation hazard above.
- **Other senders.** Amazon SES is materially cheaper per message and would matter at scale, but
  carries the same domain requirement plus a sandbox-exit request. Gmail SMTP with an app password
  is the only option needing no domain (~500/day), and was rejected because it sends from a personal
  mailbox and stakes that account's reputation on automated mail.

## Consequences

Four new secrets join `HF_TOKEN`: `GOOGLE_CLIENT_ID`, `RESEND_API_KEY`, and a write-scoped
`SUBSCRIBERS_TOKEN` in both the Space and Actions. *(A fifth, `ALERTS_TOKEN`, was added
2026-08-13: once ADR-0042 walled the Space, this run's `/search` call needed a credential of
its own. See that ADR's amendment — without it every Digest 401s.)* The allowlist is deliberately *not* a secret —
it is data in the Subscriptions repo, so it has one home and one edit path. Personal data enters the
system for the first time, which is why the private repo, a `subscriptions/` `.gitignore` entry, and
a rule that addresses are never printed to workflow logs are part of this decision rather than
follow-up hygiene. Each record carries a random unsubscribe token, so the unsubscribe link needs no
session and no signing key.

The Space performs its first durable write. Everything it holds in memory still dies on restart
(~12×/day), which is why no part of a Subscription is cached there.

The Telegram bot is left untouched and still inert. Two alert paths with different matching
semantics is a real duplication, and the intended resolution — should email prove out — is to move
Telegram onto the same `shortlist`, not to grow a second ranking rule. **Done in ADR-0038**, which
also revisits this ADR's rejection of adapter classes: Telegram made that seam real.

Nothing ships until a sending domain is verified with Resend and the Subscriptions repo exists;
until then the feature is inert in the same deliberate way the Telegram workflow is inert without
its secrets. Volume is bounded by the invite list by design, and the send-nothing-on-zero rule keeps
real usage under the nominal ceiling — the day Resend's 100/day cap is reached is the signal that
this outgrew its assumptions, not a failure to design for scale.
