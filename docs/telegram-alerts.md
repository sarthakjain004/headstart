# Telegram job alerts — setup

Job alerts delivered as Telegram DMs (ADR-0038). Same ranking as the email digests — the
semantic search you set, best new roles since your last message — just a different transport.
Unlike email it needs no sending domain, so it reaches anyone.

Two workflows, deliberately separate. `.github/workflows/bot.yml` polls every ~15 min and
only handles **enrolment**: `/start`, the master's approvals, and `/q`. Delivery is
`alerts.yml`, on the pipeline's `workflow_run` trigger, so digests arrive per successful
pipeline run rather than on a clock.

## One-time setup

1. **Create the bot.** Message @BotFather, send `/newbot`, follow the prompts, copy the
   **bot token**.

2. **Add the secrets** (repo → Settings → Secrets and variables → Actions):

   | Name | Kind | Value |
   | --- | --- | --- |
   | `TELEGRAM_BOT_TOKEN` | secret | the token from BotFather |
   | `SUBSCRIBERS_TOKEN` | secret | the write-scoped token the alerts run already uses |
   | `SUBSCRIBERS_REPO` | variable | `imPoseidon/headstart-subscribers` |

   The last two already exist if email alerts are set up. `bot.yml` needs them because
   approved people are stored as subscriptions, not in a separate list.

3. **Claim the master seat.** Run `bot.yml` once (Actions → telegram-bot → Run workflow),
   then message your bot `/start`. **The first chat to `/start` becomes the master** — do
   this before sharing the bot with anyone.

Until the secrets exist both workflows are a deliberate green no-op. The `STATE_GIST_*`
secrets the old keyword bot used can be deleted; nothing reads them.

## Using it

Anyone else who sends `/start` is announced to the master, who answers:

```text
Ada Lovelace (@ada_l) — id 2000 wants job alerts.
/allow 2000   or   /deny 2000
```

Once approved they set what they're looking for, and can change it whenever:

| Command | Who | What |
| --- | --- | --- |
| `/q <search>` | anyone approved | set the search, e.g. `/q backend engineer at a climate startup` |
| `/status` | anyone approved | show the current search |
| `/stop` | anyone approved | stop alerts and delete the record |
| `/allow <id>` · `/deny <id>` | master | answer a pending request |
| `/pending` | master | who is waiting |
| `/revoke <id>` | master | stop someone already approved |

A person with no search set yet receives nothing — the run logs them as `no query set yet`.
Write the query the way the search box wants it: **the role you want, not structured
constraints**. "backend engineer at a climate startup" works; "3+ years, remote" does not,
because years and remote are filters, not part of the semantic query (see `CLAUDE.md`).

## Notes

- **A digest arrives as several messages plus a spreadsheet.** Telegram caps a message at
  4096 characters, so roles go 10 per message, and the full set is attached as `.xlsx`.
- **Enrolment latency:** GitHub schedules cron loosely, so `/start` and approvals can lag
  15–40 min. Delivery is unaffected — that runs off the pipeline, not the clock.
- **Privacy:** chat ids live only in the private Subscriptions dataset, never in this repo
  and never in workflow logs — records are logged by id.
- **Blocking the bot** stops messages immediately, but Telegram then refuses delivery and
  the run logs a `FAILED` for that record each time. `/stop` or the master's `/revoke` is
  the clean exit.
