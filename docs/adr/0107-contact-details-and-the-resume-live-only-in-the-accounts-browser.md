# ADR-0107: Contact details and the résumé live only in the Account's browser

**Status:** accepted · **Date:** 2026-09-03 · **Preserves ADR-0032/0041 (the Profile carries no contact details; the server never stores the Résumé). Sibling of ADR-0105/0106**

## Context

Every application form needs a name, an email, a phone number and a résumé upload. The glossary
says, deliberately, that the **Profile** never keeps contact details and that HeadStart's servers
never store the **Résumé** — it is read once by the extraction call and discarded. Auto-apply needs
all four at fill time, so one of two things had to give: store them server-side, next to the
Profile in the private `headstart-subscribers` dataset, or keep them out of the server entirely.

Server-side storage would make HeadStart a store of phone numbers and résumé binaries — consent,
deletion, a file passing through HF on every apply — and rewrite two rules that were chosen on
purpose. And the one-shot plan (ADR-0106) does not need any of it: it needs the form's Questions,
the Profile's *facts* (title, years, skills, past roles, education, location — which already came
from the résumé once), and the Account's **Answers**. Essay drafts can be written from those facts.

## Decision

1. **Contact details and the résumé file live in the extension's storage on the Account's machine**
   and nowhere else. The extension merges them into the form's standard boxes (`first_name`,
   `_systemfield_resume`, `candidate[email]`, …) locally, at fill time, after the plan comes back.
2. **The server holds the Profile and the Answers** — facts about the applicant, never contact
   details. That is what reaches the LLM.
3. **Demographic Questions** (gender, race, veteran status, disability) **default to "decline to
   self-identify"**, which every such form offers, and are kept as Answers only when the Account
   chooses to fill them.
4. **The one disclosure that does happen** is the Profile facts and the relevant Answers reaching
   whichever provider the router routes that call to. It is named here so it is a decision, not a
   surprise.

## Consequences

Single-device: a new machine means re-entering four fields and re-uploading a file. The privacy
story becomes one sentence — HeadStart's servers never hold your résumé or your phone number —
and both existing glossary rules survive untouched; the **Résumé** entry gains one clause about the
browser-side copy.
