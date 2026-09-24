# Privacy policy

*Last updated: 2026-09-24*

This policy covers the HeadStart search site (`imposeidon-headstart-search.hf.space`) and its
email and Telegram job alerts. HeadStart is a personal open-source project run by Sarthak Jain.
Its code is public at [github.com/sarthakjain004/headstart](https://github.com/sarthakjain004/headstart),
so you can check everything below against the code.

## What HeadStart collects

**When you sign in.** You sign in with Google. HeadStart receives your email address from Google,
accepts it only if Google says it's verified, and keeps it in a signed session cookie. It keeps
nothing else from your Google account.

**What you choose to save.** While you're signed in, the site stores what you create:

- saved searches: a name, the search text and the filters;
- starred jobs: a copy of each job's title, company, location, salary and link, so the star
  survives the posting closing;
- companies you follow or hide;
- your Profile: a role description, job title, years of experience, skills, roles, education and
  location. You can edit all of these.

These records are filed under a one-way hash of your email address, not the address itself.

**Résumé text you paste into Profile.** It is sent once to an AI model to fill in your Profile, and
only the extracted fields above are kept. HeadStart does not store or log the pasted text. The
model runs at a third-party AI provider, reached through HeadStart's own model gateway. HeadStart
also records how many times your account has used this feature, because each account has a
lifetime limit.

**Résumés you write in the Résumé builder.** These stay in your browser (its local storage) by
default. HeadStart's servers get a copy only if you turn on account sync for that résumé. Sync is
off by default and set per résumé, with a limit of 10 synced résumés per account.

**Email alerts.** Email alerts are invite-only. If you have them turned on, HeadStart stores your
email address, the search and filters it runs for you, a marker of the last alert sent, and a
private unsubscribe token. The emails are sent through [Resend](https://resend.com).

**Telegram alerts.** When you message the HeadStart Telegram bot to ask for access, it records your
Telegram chat id, @username and display name so the maintainer can approve or decline the request.
Approved chats receive alerts through Telegram.

**Server logs.** The site's server logs each request it receives, including the address of the
page. For a search, that address contains the search terms.

## What HeadStart doesn't do

- No analytics, advertising or tracking scripts. The only third-party script on the site is
  Google's sign-in script, loaded from `accounts.google.com`.
- No selling or sharing of your data, and no use of it beyond running the features above.

## Where your data lives

- **Hugging Face** hosts the site and stores your account records in a private dataset.
- **Google** handles sign-in.
- **Resend** delivers email alerts, and **Telegram** delivers Telegram alerts.
- **An AI model provider** reads pasted résumé text, as described above.

Each of these runs under its own privacy policy.

## Cookies and browser storage

- **One cookie:** the session cookie that keeps you signed in. It lasts up to 30 days and is
  marked `Secure`, `HttpOnly` and `SameSite=Lax`.
- **Browser local storage:** holds display preferences, the jobs you dismissed, and your Résumé
  builder drafts. This never leaves your browser unless you turn on résumé sync.

## Deleting your data

- **Delete individual items on the site:** saved searches, starred jobs, synced résumés, your
  Profile, and company follows or hides. Deleting your Profile keeps only the usage count for the
  résumé-reading limit.
- **Stop email alerts** with the unsubscribe link in any alert email. **Stop Telegram alerts** by
  sending `/stop` to the bot.
- **To delete everything tied to your account,** email
  [sarthakjain004@gmail.com](mailto:sarthakjain004@gmail.com) from the address you sign in with.

Deleted records are gone from your account immediately and are removed from the dataset's backup
history within 30 days.

## Contact

Questions or requests about your data: [sarthakjain004@gmail.com](mailto:sarthakjain004@gmail.com).

## Changes

If this policy changes, the new version is posted here with a new date. The history of every
change is public in the repository.
