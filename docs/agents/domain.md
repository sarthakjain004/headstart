# Domain Docs

How the engineering skills should consume HeadStart's domain documentation when exploring the
codebase. HeadStart is **single-context**: one `CONTEXT.md` at the repo root and ADRs under
`docs/adr/`.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root — the domain glossary (ATS, Company, Board, Job, Slug, Scraper;
  Discovery, Feeder, Resolve, Liveness, Active list, Feed; Subscription, Invite, Transport, Master, Watermark, Digest).
- **`docs/adr/`** — read the ADRs that touch the area you're about to work in (`README.md` indexes
  them). Each `NNNN-*.md` is one decision: the option picked, the ones rejected, and why.

If any of these don't exist yet, **proceed silently** — don't flag their absence or suggest creating
them upfront. `/domain-modeling` (reached via `/grill-with-docs` and `/improve-codebase-architecture`)
creates them lazily when terms or decisions actually get resolved.

## File structure

```
/
├── CONTEXT.md
├── docs/adr/
│   ├── README.md          ← index
│   ├── 0001-per-ats-slug-derivation.md
│   └── …
└── src/headstart/
```

## Use the glossary's vocabulary

When your output names a domain concept (an issue title, a refactor proposal, a hypothesis, a test
name), use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary explicitly
avoids — say **ATS**, not "provider"; **Company** / **Slug**, not "tenant"; **Board**, not "careers
page"; **Job**, not "posting".

If the concept you need isn't in the glossary yet, that's a signal — either you're inventing language
the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0002 (pooled curl_cffi HTTP) — but worth reopening because…_
