# Privacy policy

*Last updated: 2026-09-24*

This policy covers the HeadStart search site (`imposeidon-headstart-search.hf.space`) and its
email and Telegram job alerts. HeadStart is a personal open-source project run by Sarthak Jain.
Its code is public at [github.com/sarthakjain004/headstart](https://github.com/sarthakjain004/headstart),
so you can check everything below against the code.

## What HeadStart collects

**When you sign in.** You sign in with Google. HeadStart receives your email address from Google,
accepts it only if Google says it's verified, and keeps it in a signed session cookie. It keeps
nothing else from your Google Account.

**What you choose to save.** While you're signed in, the site stores what you create:

- saved searches: a name, the search text and the filters;
- starred jobs: a copy of each job's title, company, location, salary and link, so the star
  survives the posting closing;
- companies you follow or hide;
- your Profile: a role description, job title, years of experience, skills, roles, education and
  location. You can edit all of these.

These records are filed under a short SHA-256 hash of your email address instead of the address.
That keeps the address out of file names, but it isn't anonymisation: anyone who already knows
an address can compute its hash, and if you have email alerts on, the alert record under the
same hash contains the address itself.

**Résumé text you paste into Profile.** It is sent once to an AI model to fill in your Profile, and
only the extracted fields above are kept. The site doesn't store or log the pasted text. On its
way to the model it passes through HeadStart's own model gateway, a private server on Oracle
Cloud, which forwards it to one of several third-party AI providers. HeadStart also counts how many
times your Account has used this feature, because each Account has a lifetime limit.

**Résumés you write in the Résumé builder.** These stay in your browser (its local storage) by
default. HeadStart's servers get a copy only if you turn on Account sync for that résumé. Sync is
off by default and set per résumé, with a limit of 10 synced résumés per Account.

**Email alerts.** Email alerts are invite-only. The maintainer keeps a list of invited email
addresses, and can attach a search (with filters) to an invitation or a Telegram chat id. If an
invitation has a search attached, or the list sets a default search, alerts for it start without
the invited person signing in.
For each active alert, HeadStart stores the email address, the search and filters, when the alert
was created, a marker of the last alert sent, and a private unsubscribe token. The emails are sent
through [Resend](https://resend.com).

**Telegram alerts.** When you message the HeadStart Telegram bot to ask for access, it records your
Telegram chat id, @username, display name and when you asked, so the maintainer can approve or
decline. If the request is declined, the chat id is kept on a declined list so the same request
isn't announced again. If it's approved, your chat gets an alert record with a search and a marker
of the last alert sent.

**Server logs.** The site's server logs each request it receives, including the address of the
page. For a search, that address contains the search terms; for an unsubscribe link, it contains
the link's token. Hugging Face, which hosts the site, keeps these logs.

## What HeadStart doesn't do

- No analytics, advertising or tracking scripts. The only third-party script on the site is
  Google's sign-in script, loaded from `accounts.google.com`, which uses Google's own cookies.
- No selling or sharing of your data, and no use of it beyond running the features above.

## Where your data lives

- **Hugging Face** hosts the site and stores Account and alert records in a private dataset.
- **GitHub Actions** runs the jobs that send alerts and operate the Telegram bot. Those jobs read
  alert records, including email addresses and chat ids.
- **Google** handles sign-in.
- **Resend** delivers email alerts, and **Telegram** delivers Telegram alerts.
- **Oracle Cloud** hosts HeadStart's model gateway, and **third-party AI providers** read pasted
  résumé text, as described above.

Each of these runs under its own privacy policy.

## Cookies and browser storage

- **HeadStart sets one cookie:** the session cookie that keeps you signed in. It lasts up to 30
  days and is marked `Secure`, `HttpOnly` and `SameSite=Lax`.
- **Browser local storage** holds display preferences, the jobs you dismissed, and your Résumé
  builder drafts. This never leaves your browser unless you turn on résumé sync.

## Deleting your data

- **On the site** you can delete saved searches, starred jobs and synced résumés, clear your
  Profile, and remove company follows or hides. Clearing your Profile keeps only the usage count
  for the résumé-reading limit.
- **Stop email alerts** with the unsubscribe link in any alert email. **Stop Telegram alerts** by
  sending `/stop` to the bot. Stopping deletes the alert record but keeps a small opt-out marker,
  so the alert isn't recreated, and leaves any invitation in place.
- **To delete everything tied to your Account,** including the opt-out marker and any invitation,
  email [sarthakjain004@gmail.com](mailto:sarthakjain004@gmail.com) from the address you sign in
  with.

Deleted records are gone from your Account immediately, and are removed from the dataset's commit
history within 30 days.

## Contact

Questions or requests about your data: [sarthakjain004@gmail.com](mailto:sarthakjain004@gmail.com).

## Changes

If this policy changes, the new version is posted here with a new date. The history of every
change is public in the repository.
