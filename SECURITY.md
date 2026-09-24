# Security policy

## Reporting a vulnerability

Please report security issues privately through GitHub's form:
**[Security → Report a vulnerability](https://github.com/sarthakjain004/headstart/security/advisories/new)**.
Only the maintainer can see the report. If you can't use the form, email
[sarthakjain004@gmail.com](mailto:sarthakjain004@gmail.com). Please don't open a public issue or PR for a
vulnerability.

A useful report says what you found, how to reproduce it, and what an attacker could do with it.
You'll get an acknowledgement within a week.

## Scope

In scope:

- The search Space (`imposeidon-headstart-search.hf.space`) and its code in `deploy/hf-space/`,
  particularly sign-in, sessions, and anything that could expose or change another user's
  Account data (saved searches, saved jobs, résumés, subscriptions).
- The alerts (email and Telegram) and the unsubscribe flow.
- The GitHub Actions workflows in this repo, including anything that could leak their secrets.

Out of scope:

- Vulnerabilities in the third-party ATS sites HeadStart reads. Report those to the vendor.
- Findings that need a compromised maintainer account or machine.

## Testing rules

- Use only accounts you own. Don't access, change or delete another user's data.
- Don't load-test or flood the Space or the alert bot. It runs on free tiers, and an outage hits
  every user.
- Stop and report as soon as you've shown the issue.

## Secrets in this repository

GitHub secret scanning and push protection are on. Test fixtures under `tests/fixtures/` are
captures of vendors' public career pages, with vendor browser keys replaced by `REDACTED`. A string
there that looks like a token is either a placeholder or one of those vendors' public client keys,
not a credential for this project.
