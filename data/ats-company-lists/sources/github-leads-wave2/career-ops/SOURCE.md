# career-ops (santifer)

- **URL:** https://github.com/santifer/career-ops
- **Author / repo:** santifer/career-ops
- **License:** MIT
- **Fetched:** 2026-06-23 (via `gh api .../contents/templates/portals.example.yml`)
- **One-line:** `providers/*.mjs` are pure per-ATS host logic (no company data); the
  real bonus is the populated `portals.example.yml → tracked_companies` block.

## What was extracted
The brief pointed at `providers/*.mjs` + `portals.yml`. Findings:
- `providers/*.mjs` (ashby, greenhouse, lever, workday, smartrecruiters, workable,
  recruitee, ibm, glints, jobstreet, …) hardcode only ATS **hosts/parsers**, no slugs.
- `portals.yml` itself is gitignored; the committed `templates/portals.example.yml`
  (1333 lines) ships a *populated* `tracked_companies:` block (lines 462–1258) with
  real `careers_url` + `api` endpoints. Extracted that.

Files:
- `portals.example.yml` — full template, verbatim.
- `career-ops_tracked_companies.json` — **111 companies** parsed from it.

## ATS breakdown (111 tracked companies)
| ATS | count |
|---|---|
| greenhouse | 37 |
| ashby | 34 |
| lever | 10 |
| workable | 2 |
| UNKNOWN (first-party careers pages, e.g. openai.com/careers) | 28 |

- **82 / 111 have ats + slug.** AI/voice-AI heavy (Anthropic, PolyAI, Parloa, Hume,
  ElevenLabs, Deepgram, Vapi, Bland). Slugs derived from the embedded `api`/`careers_url`.
