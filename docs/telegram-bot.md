# Telegram alerts (v2) — setup

Users subscribe to new-job alerts: they message the bot, set filters, and get pinged when
matching roles appear. It runs entirely on GitHub Actions (no server):
`.github/workflows/bot.yml` polls Telegram every ~15 min, handles commands, and sends
alerts for jobs added by the latest scrape. Subscriber state lives in a private Gist.

## One-time setup

1. **Create the bot.** Message @BotFather, send `/newbot`, follow the prompts, copy the
   **bot token**.
2. **Create a state Gist.** Make a *secret* gist at gist.github.com with one file named
   `headstart-state.json` containing `{}`. Copy its id from the URL
   (`gist.github.com/<user>/<THIS_ID>`).
3. **Create a PAT** (Settings → Developer settings → Personal access tokens) with **only the
   `gist` scope**. Copy it.
4. **Add three repo secrets** (repo → Settings → Secrets and variables → Actions):
   `TELEGRAM_BOT_TOKEN`, `STATE_GIST_ID`, `STATE_GIST_TOKEN`.
5. Run it once (Actions → telegram-bot → Run workflow), then message your bot `/start`.

Until the secrets exist the workflow is a deliberate green no-op.

## Commands

`/start` · `/q <keywords>` · `/location <text>` · `/remote` · `/company <text>` ·
`/ats <greenhouse|lever|ashby>` · `/status` · `/clear` · `/stop`

## Notes

- **Latency:** GitHub schedules cron loosely, so replies/alerts can lag 15–40 min. For
  instant replies, command-handling could move to a webhook (e.g. a free Cloudflare Worker);
  notifications are scrape-paced (every 6h) either way.
- **Privacy:** chat ids and filters live only in the private Gist, never the repo.
- **First run** seeds the "seen jobs" set without alerting, so subscribers only get roles
  added after they subscribe.
