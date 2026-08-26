# Personio's missing descriptions are language-scoped, not absent (2026-08-26)

## Symptom

`update_descriptions` reported personio leaving ~27% of its tech Jobs with no description and no
stored answer either way, run after run, with `learned` ≈ 0 and `settled 0 as having none`:

```
[update_descriptions] personio: filled 4 from the store, learned 1, settled 0 as having none,
                      queued 0 to re-derive, 339 still unrecorded      # run 32942748996
```

339 of 1,247 tech Jobs = **27.2%**, and 335/1,273 = 26.3% the run before. That is ~5x the next
worst ATS (darwinbox 5.8%) and put every one of those Jobs outside Tier-2 experience/salary
extraction, with a title-only embedding vector.

## Root cause

**Personio's `/xml` feed returns each description only in the language the request asks for, and
the bare feed asks for the tenant's configured default.** A posting authored in any other language
comes back as a present-but-childless `<jobDescriptions />`. The text exists; it is simply not the
translation being served.

Live, `gridx.jobs.personio.com` position `2747735` ("Senior Backend Engineer — Energy Management
Platform"):

```xml
<position>
    <id>2747735</id>
    <name>Senior Backend Engineer - Energy Management Platform (all genders)</name>
    <jobDescriptions />          <!-- bare feed -->
    ...
</position>
```

The same position on `?language=en` carries 25,418 characters across 3 `<jobDescription>` sections.

## What the measurement ruled out

The prior suspicion was a third silent field bug in `_description()` — this module has already had
two (`_salary`'s structured element whose `.text` is always empty, `_location`'s unread
`additionalOffices` sibling). **That framing did not survive contact with the data.**

Over **296 live boards / 2,029 positions** (2026-08-26, random seed 20260826), every one of the
191 empty positions had:

- a `<jobDescriptions>` element **present** with **zero** `<jobDescription>` children — so there
  was no section for the parser to mis-read;
- `findtext("value")` and a full `itertext()` walk of the `<value>` subtree agreeing exactly, so
  the `_salary`-shaped "text hidden below a child element" bug was **not** occurring;
- **no unread sibling element** anywhere in the `<position>` carrying text, so the `_location`
  -shaped bug was not occurring either.

`_description()` was reading the feed correctly. The feed was answering a different question than
the one being asked.

## Why `settled 0 as having none` is structural, not a second bug

`reconcile()` only settles a Job as authoritatively description-less when `job["detail_fetched"]`
is truthy (`update_descriptions.py`). `Job.detail_fetched` is set by **eightfold alone**, and
personio does not declare `has_detail_pass` — so personio can *never* settle anything, by
construction. Its empty Jobs fall to the `unrecorded` branch and are re-counted as a fresh backlog
every run forever.

That is worth knowing, but it was the **wrong thing to fix**. Settling these Jobs as "has none"
would have recorded a falsehood — the descriptions exist — and permanently suppressed the signal
that they were missing. The backlog was the symptom; the language scoping was the disease.

## The fix

`PersonioScraper.fetch_raw` now fetches the bare feed and, only if positions are left
description-less, re-asks the same board per language and fills the gaps
(`_DESCRIPTION_LANGUAGES = ("en", "es", "nl", "fr")`).

**Filling only, never replacing**, is the safety property that makes this sound. Measured over
249 boards, switching wholesale to `?language=en` would **recover 133 descriptions and destroy
1,159** (101 tech) — most tenants are German, and asking for English empties them. The scraper's
existing `url()` comment already claimed this and the measurement confirmed it exactly. So a
language variant may only supply a block the bare feed left childless.

**`?language=` scopes the descriptions, not the position list.** Merging a variant back by
position id would be unsafe if the parameter also filtered *which* positions came back — a short
variant read as the Board's list would look like a truncation and could drive an eviction. It does
not: over **138 Boards / 938 positions** (seed 31337 — 140 sampled, 138 fetched; see the holdout
below) no variant ever added or dropped a position relative to the bare feed. An independent
re-check at review time agreed: 148 variant-vs-bare comparisons over 37 further Boards, zero
mismatches. The design does not rely on that holding —
the Board's position list is always the bare feed's, an unknown id in a variant is ignored, and an
absent one simply stays unfilled — but it is why the merge is a pure description fill.

### Why those four language codes

Swept over all 58 boards holding the 191 empty positions, with `en, de, es, fr, nl, it, pt, pl,
sv, da`:

| code | positions recovered |
|---|---|
| `en` | 153 |
| `es` | 17 |
| `nl` | 13 |
| `fr` | 6 |
| `de`, `it`, `pt`, `pl`, `sv`, `da` | **0** |

`de` recovering nothing is not a surprise once the mechanism is understood: a tenant whose
postings are German is already being served German by the bare feed. The six zero-yield codes are
deliberately excluded rather than added for symmetry — the same speculative-generality trap a
review already caught in `_salary`'s period mapping.

**187 of 191 (97.9%)** empty positions are recoverable this way. The remaining 4 are empty in
every feed variant and carry their description only in the HTML job page's JSON-LD `JobPosting`
node — **none of the 4 was a tech role**, so a per-Job detail pass was not added for them.

## Holdout verification

Run through the real `PersonioScraper` on a **different random sample** (seed 777) from the one the
language list was chosen on — 344 boards, 2,442 positions, 311 tech:

| | before | after |
|---|---|---|
| empty descriptions | 212 (8.68%) | **10 (0.41%)** |
| empty **tech** descriptions | 44 (14.15% of tech) | **0 (0.00%)** |
| requests per board | 1.00 | 1.26 |

Every tech empty in the holdout was recovered. Cost is bounded and mostly zero: 288 of 344 Boards
pay no extra request at all, 40 pay one, and no Board pays more than four.

Re-measured independently at code-review time on a third sample (seed 31337, 140 Boards drawn and
138 fetched — the other 2 are departed tenants whose feed 307s to `personio.com`, the #313 case —
for 938 positions): empty descriptions **78 → 1**, **1.21 requests per Board**, 116 of 138
Boards paying nothing extra, and a worst case of exactly four extra —
`albaberlin.jobs.personio.com`, whose one empty position is empty in all four variants too. The
same run re-measured the counterfactual: a blanket `?language=en` would have recovered 66
descriptions and destroyed **238**.

## Consequence: the English gate moves, net favourably

The recovered text is in the posting's own language, so it can change `doc_prep.is_english`'s
verdict for Jobs that previously reached the gate with a title and nothing else. Measured over the
204 positions the fix filled in the holdout (43 tech):

- detected language: `en` 135, `de` 31, `nl` 27, `es` 1
- **tech** Jobs newly passing the gate (title-only said no, title+description says yes): **+9**
- **tech** Jobs newly failing it: **−2**

Net **+7 tech Jobs** become eligible for the English index. The 2 losses are the language gate
working as designed (CLAUDE.md: the corpus is English-only for now) — they are genuinely
non-English postings that were only passing because they had no body text to detect on.

## Reach into already-indexed data

- **Derived metadata is repaired.** A Job whose description settles this run is appended to the
  ADR-0062 pending-rederive queue, and `update_meta` re-runs the `experience`/`salary` cascade for
  exactly those ids **at an unchanged `DERIVATIONS_VERSION`** — that queue exists for this case.
  `index sync`'s `_refresh_metadata` then pushes the changed columns into the served table.
- **No `DERIVATIONS_VERSION` bump is required or wanted.** The change is confined to
  `scrapers/personio.py`; `experience.extract()` and `salary.extract()` are untouched and return
  the same values for the same input. A bump would additionally force `update_meta` to load the
  entire ~1 GB description store and re-derive every ATS, and CLAUDE.md requires the version
  comment to cite an `experience.py`/`salary.py` commit range that does not exist here.
- **Vectors are repaired only where the meta row carries `has_description: false`.** A personio
  meta row written before that flag shipped (2026-08-13) carries no flag, is not read as degraded
  (`embed_plan` tests `has_description is False` and nothing else since #166/ADR-0062), and is
  since backfilled to `True` — so it can never be re-embedded by any current path. Those Jobs get
  their text and their numbers back, but keep a title-only vector. Sizing that population needs
  `data/embeddings/jobs/meta.jsonl` from HF, counted by `ats`.

## Reproducing

Harnesses live in `experiment/personio-description-coverage/`, which — like every `experiment/`
capture in this repo — is **gitignored** (`.gitignore:34`), so they are not fetched with a clone:

| script | what it answers |
|---|---|
| `probe_feeds.py N SEED` | are empty positions a parse bug or an absent block? |
| `probe_variants.py HOST` | is an empty block recoverable from a feed variant at all, rather than a per-job fetch? |
| `probe_language_merge.py N SEED --langs ...` | per language: what does it recover, what does it destroy? |
| `probe_residue.py HOSTS_FILE` | wide language sweep + JSON-LD job-page fallback |
| `probe_multilang.py HOST ID` | does the feed accept more than one language per request? (no) |
| `verify_fix_live.py N SEED` | holdout: coverage and request cost through the real scraper |
| `probe_english_gate.py N SEED` | how the recovered text moves `is_english` |

The two cases worth keeping to hand, both re-verified 2026-08-26, are enough to re-derive the
whole finding without any harness:

```bash
# recovery: bare feed empty, ?language=en full
curl -s https://gridx.jobs.personio.com/xml           # position 2747735 -> <jobDescriptions />
curl -s 'https://gridx.jobs.personio.com/xml?language=en'   # same position -> 25,418 chars

# destruction: bare feed full, ?language=en empty (why this is fill-only)
curl -s https://interlead.jobs.personio.de/xml               # 7 of 7 positions described
curl -s 'https://interlead.jobs.personio.de/xml?language=en' # 1 of 7
```
