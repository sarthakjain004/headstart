# ADR-0038: Telegram alerts — one Digest, pluggable transports, enrolment by approval

**Status:** accepted · **Date:** 2026-08-06 · **Supersedes parts of:** ADR-0035

## Context

ADR-0035 shipped email job alerts and left an explicit debt: *"Two alert paths with
different matching semantics is a real duplication, and the intended resolution — should
email prove out — is to move Telegram onto the same `shortlist`, not to grow a second
ranking rule."* Until now `headstart/bot.py` matched keyword `Filter`s against the **Feed**
and kept a seen-Job set, while `headstart/alerts/` ranked semantically against the **Search
index** with a Watermark. Two rankings, two notions of "new", two stores.

What forced the issue was delivery, not architecture. Email cannot reach anybody but the
Resend account owner until a sending domain is verified, and verifying one means buying a
domain. Telegram has no such gate: no DNS, no sender reputation, no per-recipient cost, and
none of Resend's 100/day cap. For a personal invite-list product that is the difference
between working and not.

Two constraints shape the rest. A bot may not message someone who has never messaged it, so
Telegram needs a chat id that only exists once that person speaks — the allowlist cannot
simply be hand-written the way an address can. And Telegram caps one message at 4096
characters, well under a 30-role Digest.

## Decision

**One ranking, two transports.** `space_query` → `shortlist` → `Digest` is unchanged and
shared; only delivery differs. The keyword path is retired outright: `headstart/bot.py`,
`headstart/filters.py` and `headstart/state.py` are deleted along with the Gist that backed
them. This is ADR-0035's stated resolution, executed.

**Transports are a plug-in seam, and that revisits ADR-0035 deliberately.** That ADR
rejected formal adapters because *"`identity` and `mail` have exactly one real implementation
each, which is a hypothetical seam, not a real one"* — correct then, and still the reason
everything else here injects plain callables. Telegram makes the seam real, so the premise is
gone rather than the rule being wrong. `alerts/transports.py` holds a `Transport` record —
name, a predicate selecting the Subscriptions it serves, the environment variables it needs,
and a send function — and `TRANSPORTS` is the ordered tuple `run` walks. Adding Slack or a
webhook is one literal and one tuple entry; `run` never learns a channel exists. The *idiom*
is kept: a Transport is a record of functions, not a class hierarchy, so it still fakes with
`monkeypatch.setattr` and needs no mocking library.

**Exactly one transport per Subscription**, chosen by whether it carries a chat id. This is
what keeps a single Watermark correct: two channels sharing one would let a healthy channel
advance it past a window the other never delivered. Email is last in `TRANSPORTS` and its
predicate is unconditional, so nothing can fall off the end.

**A transport with no secrets is skipped, not failed.** `TransportUnset` is separate from a
delivery failure because being dark until configured is the shape the whole feature was built
in — a repo running only Telegram would otherwise report red on every run, forever.

**Enrolment is master-approves-newcomer.** The first chat to `/start` claims the master seat;
everyone after is announced to the master, with their Telegram display name and `@username`
so the question is about a person rather than an opaque number, and answered with `/allow` or
`/deny`. Trust-on-first-use is the simplification: the bot token is a secret only the owner
holds, so the first `/start` is theirs unless the token leaked before setup. Reassigning
`master` means editing the registry by hand, which is the right amount of friction for a
thing that should happen once.

The alternative shapes were worse. A **fully open bot** ("anyone who /starts gets alerts")
was rejected: every subscriber costs Space searches on every run, and a leaked bot link
becomes an open service. **Allowlisting chat ids by hand** keeps ADR-0035's posture exactly
but reintroduces the friction that made email unusable — the owner would have to relay ids
into a file for every person. Approval-in-chat keeps the owner in control while costing the
newcomer one tap.

**An approved person is a Subscription, not a second kind of record.** `subscriptions/{id}.json`
is the one answer to "who receives alerts", so revoking somebody is deleting the file the
unsubscribe link already deletes, and `alerts.run` delivers to them without knowing Telegram
exists. Their id is `sha256("telegram:{chat_id}")` — namespaced before hashing so it can
never collide with an address-derived id, which in one flat directory would silently hand one
person another's Watermark and unsubscribe token. `email` stays empty rather than holding a
placeholder, and that emptiness is what distinguishes a bot record from an allowlisted person
who was also given a chat id by hand: the latter is already covered by their Invite, and
selecting on the chat id alone would deliver to them twice per run.

**The registry holds only what is not a Subscription** — master, pending requests, polling
offset — at `telegram/registry.json` in the same private dataset, so the bot and the alerts
run share one store rather than two. Its offset advances even for updates that produce no
reply, or an unanswerable update would be re-fetched forever; it is saved *after* sending, so
a crash re-sends a message rather than silently losing it.

**A Digest is chunked at 10 roles per message, with the full set as an `.xlsx`.** Ten keeps
each message comfortably inside the 4096-character cap with room for long titles, and matches
the batch size the old bot already used. The spreadsheet goes via `sendDocument` — the one
Telegram call that cannot be JSON, so `multipart/form-data` is hand-rolled rather than pulling
in `requests` for a single upload. Every interpolated value is HTML-escaped: a job title
containing a stray `<` would otherwise make Telegram reject the whole message.

**A separate sender rather than reusing `headstart/telegram.py`.** That client swallows a
failed send so one blocked chat cannot abort the polling loop — right for a bot answering
commands, and exactly wrong here, where a swallowed failure would advance a Watermark past a
Digest that never arrived. Same API, opposite failure contract. `alerts/telegram.py` also
treats an HTTP 200 carrying `"ok": false` as a refusal, which is how Telegram reports a
blocked bot or an unknown chat.

## Consequences

Telegram needs one secret, `TELEGRAM_BOT_TOKEN`, in Actions; `bot.yml` additionally needs the
Subscriptions repo and token now that it writes records instead of Gist state, and the
`STATE_GIST_*` secrets can be deleted. Email is unaffected and still works for the Resend
account owner; the sending-domain gate is untouched by this ADR and remains the reason email
reaches nobody else.

Two enrolment paths now feed one run — the hand-edited allowlist and the bot — and `run`
iterates both. That is a genuine widening of ADR-0035's "the allowlist is the one edit path",
and it is the cost of removing the friction: the allowlist remains the only path for *email*,
while Telegram's equivalent is the master's approval.

The bot polls every 15 minutes on a schedule, so an approval can sit that long before it
takes effect, and a newcomer's `/start` can take that long to reach the master. Acceptable
for enrolment; it would not be for delivery, which is why delivery stays on the pipeline's
`workflow_run` trigger.

Known gap, deliberately not solved: revoking somebody the *allowlist* named still leaves an
orphaned `subscriptions/{id}.json`, as ADR-0035 records. Telegram revocation does not have
this problem — `/revoke` deletes the record — but the two paths now differ in that respect.
