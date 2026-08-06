# Email job alerts — setup

Invite-only email alerts (ADR-0035). A person signs in with Google on the Space, gives a
**Query** and **Search filters**, and after each successful pipeline run gets one **Digest**:
the best 30 Jobs first seen since their last email, with scores, links and an `.xlsx`
attachment. No matches, no email.

Nothing sends until every item below exists. The Space hides the panel and
`.github/workflows/alerts.yml` is a green no-op in the meantime — the same
dark-until-configured shape as the Telegram bot and the résumé feature.

## One-time setup

1. **Create the Subscriptions dataset.** A **private** HF dataset,
   `imPoseidon/headstart-subscribers`. It holds `subscriptions/{id}.json` per person plus
   `subscriptions/allowlist.json`. Deliberately *not* the index dataset: the Space has to
   write here, and a token that can write the index would let a public web app destroy it.

2. **Seed the allowlist.** Upload `subscriptions/allowlist.json`:

   ```json
   { "allowed": ["you@example.com"] }
   ```

   An absent, unreadable or empty allowlist denies everyone — that is the intended failure
   direction, so a fetch blip can never open the feature up.

3. **Create a fine-grained HF token** with **write** access to *that dataset only*. This is
   `SUBSCRIBERS_TOKEN`. Never reuse the index `HF_TOKEN`, which is read-scoped on purpose.

4. **Create a Google OAuth client id.** Google Cloud console → APIs & Services →
   Credentials → OAuth client ID → *Web application*. Add the Space origin
   (`https://imposeidon-headstart-search.hf.space`) to **Authorized JavaScript origins**.
   Only the `email`/`profile` scopes are used — non-sensitive, so no consent-screen
   verification and no client secret.

5. **Verify a sending domain with Resend.** Publish the SPF/DKIM records it gives you. Until
   a domain is verified, Resend's shared `onboarding@resend.dev` sender only delivers to your
   own account address, so invited recipients would get nothing. Then create an API key.

6. **Set the secrets and variables.**

   | Where | Name | Kind | Value |
   | --- | --- | --- | --- |
   | Space | `GOOGLE_CLIENT_ID` | secret | the OAuth client id |
   | Space | `SUBSCRIBERS_REPO` | secret | `imPoseidon/headstart-subscribers` |
   | Space | `SUBSCRIBERS_TOKEN` | secret | the write-scoped token |
   | Actions | `SUBSCRIBERS_TOKEN` | secret | the same token |
   | Actions | `RESEND_API_KEY` | secret | the Resend key |
   | Actions | `SUBSCRIBERS_REPO` | variable | `imPoseidon/headstart-subscribers` |
   | Actions | `ALERTS_SENDER` | variable | e.g. `alerts@yourdomain.com` |
   | Actions | `SPACE_URL` | variable | the Space base URL (optional; defaults to it) |

   Adding a Space secret auto-restarts the Space, which is what makes the panel appear.

## Adding and removing people

Edit `subscriptions/allowlist.json` in the dataset. It is the only edit path, and it is what the
alerts run iterates — so removing an address stops mail that is already flowing, with no need to
find and delete their record. Deleting `subscriptions/{id}.json` also works and is what the
unsubscribe link does.

An entry is either a **bare address** — invited, with their query coming either from their own
sign-in on the Space or from `default_query` below — or an **object carrying the query**, which
needs no sign-in at all: the run creates the subscription on first sight and mails from the next
pipeline run onward. A bare address with no `default_query` set receives nothing until they sign
in, and the run logs it as `invited but no query yet`.

```json
{
  "default_query": "software engineer",
  "allowed": [
    "self-serve@example.com",
    { "email": "ada@example.com",
      "query": "backend engineer at a climate startup",
      "filters": { "remote": "true", "max_years": "5" } }
  ]
}
```

`filters` accepts the `/search` parameters in `store.ALLOWED_SEARCH_FILTERS`; anything else is
dropped silently. Where an entry names a query it wins over whatever is stored, so editing this
file re-aims someone's alerts on the next run — their watermark and unsubscribe link survive it.
Setting `query` without `filters` clears any filters they had chosen; that is the entry speaking
for them, so say both or neither.

`default_query` is **only a seed**, deliberately weaker than an entry's own query: it starts
anyone who has no record yet, and is ignored for anyone who does. That is what keeps it from
overwriting a query somebody picked for themselves at sign-in every run. So with a default set, a
bare address does receive alerts without ever signing in — and if they later sign in and choose
their own query, that choice sticks.

**Recipients other than the Resend account owner need a verified sending domain.** Until then
Resend refuses them and the run logs `FAILED` for that id every time, without advancing the
watermark — loud, retried forever, never delivered. See step 5 above.

## How it runs

`alerts.yml` triggers on `workflow_run` for **nightly-pipeline** and exits unless the run
succeeded — a failed run published nothing, so there is nothing to describe. For each
allowlisted Subscription it asks the Space for `first_seen_after=<that Subscription's
Watermark>`, keeps the best 30, mails them, and only then advances the Watermark. That order
is deliberate: a crash after sending re-sends at most one capped Digest, where the reverse
would drop a window silently.

## Notes

- **Cadence** is per successful pipeline run (up to ~12/day), not a clock.
- **Resend's free tier** is 3,000/month but **100/day**, and the daily cap binds first —
  roughly eight always-matching Subscriptions. Hitting it is the signal to move to a paid tier.
- **The Space is always cold** when the alerts run starts, because the merge job restarts it at
  the end of every pipeline run; `alerts.space_query` retries at 15s/30s/60s for that reason.
- **Privacy:** addresses live only in the private dataset, never in this public repo
  (`subscriptions/` is gitignored) and never in workflow logs — records are logged by id.
