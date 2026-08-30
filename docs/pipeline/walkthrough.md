# Pipeline walkthrough

A plain-language explanation of how data moves through the pipeline, written for someone learning
the system rather than someone who already knows it.

**This document grows.** It is question-driven: each section answers something that was actually
confusing, and new sections get appended as new questions come up. It is deliberately *undated*,
unlike its neighbours in this folder — those are one-off analyses of a moment in time, this one is
meant to stay current. If you change behaviour a section describes, update the section.

Two companions worth knowing: `CONTEXT.md` at the repo root is the glossary and mental model, and
`docs/adr/` records *why* each decision was made. This file is the connective tissue between them —
what actually happens, in order, in ordinary words.

---

## The map: five jobs, many stages

A GitHub Actions **job** and a pipeline **stage** are not the same thing, and confusing them is the
single most common way to go looking in the wrong log. One job runs many stages.

| job | stages inside it, in order |
| --- | --- |
| `scrape-plan` | `scrape_plan` |
| `scrape` (×N, ≤15) | `scrape_run` |
| `join` | `scrape_join` → `filter_tech` → `update_descriptions` → `update_ledgers` ×4 → `embed_plan` |
| `embed` (×N, ≤15) | `embed_run` |
| `merge` | `embed_merge` → `update_meta` → `index sync` → `index prune` → `role_trends` → HF uploads |

Everything runs from `src/headstart/ingest/`, invoked as `python -m headstart.ingest.<module>`.
The authoritative sequence is `.github/workflows/pipeline.yml` — if this table and that file ever
disagree, the workflow is right.

---

## Facts vs derivations, and `DERIVATIONS_VERSION`

### The problem

`embed_plan` **skips any job id it has already embedded.** That is a deliberate cost saving — you
do not want to re-embed 500k jobs every run. But it has a sharp edge: once a job is embedded,
nothing ever looks at it again.

So if you fix a bug in `salary.py` today, the fix reaches **new** jobs only. Every job already in
the index keeps serving the old, wrong answer forever. `update_meta.py` exists to break that.

### Two kinds of field, and only one updates itself

**Facts** are *observed* from the ATS: `title`, `company`, `location`, `remote`,
`employment_type`, `department`, `url`, `posted_at`, and the raw `salary` / `experience` strings.

These are **automatic**. Every run, for any job in that run's scrape that the store already holds,
the freshly scraped values overwrite the stored ones. No version, no bookkeeping — if a company
edits a posting, you pick it up the next time you scrape that board.

**Derivations** are *computed by our code* from those facts. There are seven:

```text
min_years  max_years  experience_source                                  <- experience.py
min_salary_annual  max_salary_annual  salary_currency  salary_source     <- salary.py
```

These are **not automatic**, because they are `f(code, facts)`. The facts may not have changed at
all — the *code* did, and nothing in a normal run would notice that.

### So how does a derivation ever get updated?

Three ways:

1. **You bump `DERIVATIONS_VERSION`** — a single integer in `doc_prep.py` (currently `7`).
   `update_meta` compares it against a watermark in `data/state/derivations.json`. If the code is
   newer, it sweeps every eligible row back through the full extraction cascade. Nothing bumps
   this for you.
2. **The re-derive queue** (ADR-0062). When `update_descriptions` finally learns a description for
   a job that did not have one, it appends that id to `data/state/pending_rederive.txt`, and
   `update_meta` re-runs the cascade for exactly those rows *at an unchanged version*.
3. **The row's own inputs moved** — the fact a value was derived from changed.

One subtlety: even during a full sweep, rows whose description the store does **not hold** are
deliberately left alone. Recomputing without the text a value originally came from could only
downgrade it. That is roughly 127,501 rows.

Never rewritten at all: `vector`, `id`, `ats`, and `has_description` where a row already has one.

### The trap

If you fix `salary.py` so it returns a *different answer for input that has already been scraped*,
and you do not bump the version, then your code is correct, your tests pass, and production keeps
serving the old wrong number indefinitely. Nothing errors.

This repo has shipped that exact mistake **twice**. The rule of thumb is: *could this change the
answer for a job already in the index?* If yes, bump.

---

## What `update_descriptions` actually does

The simplest framing: **it is a permanent memory for job descriptions, so a failed fetch cannot
lose text you already had.**

### The problem it solves

Before it existed, a description was read exactly **once** — at embed time — and then discarded.
Combine that with `embed_plan` skipping already-embedded ids and you get a trap: if the scrape that
embedded a job happened to fail its detail fetch, that job became a **title-only vector that no
future run could ever repair.** About 16,771 jobs are stuck in exactly that state.

### Two directions, every run

**Corpus → store (save).** Every description this run actually fetched is written to disk
permanently, so it survives a later run whose fetch fails.

**Store → corpus (restore).** Where this run's scrape came back empty for a job, the stored text is
written *back into* `data/jobs/tech/{ats}.jsonl`.

The second direction is the clever part. Nothing downstream had to change — `embed_plan`,
`doc_prep` and `experience.extract` all go on reading the corpus file exactly as before, and the
corpus is simply **correct again** by the time they see it.

Note that "restoring" is **not** a network fetch. The store is a local directory of gzipped files
(`data/descriptions/{ats}/*.jsonl.gz`, ~406 MB on HF as of 2026-08-26 and growing every run),
pulled once at the start of
the join job. A restore is a local dict lookup. The expensive thing is what the store *avoids* —
going back to the ATS for one detail page *per job*, on the nine ATSes that have a detail pass at
all. That is what the skip-list it publishes (417,773 ids) exists to spare, though today only
eightfold reads it — see the open question at the end of this file.

### The two states

The store maps `id -> text`, and membership means exactly one thing:

| state in the store | meaning | next run |
| --- | --- | --- |
| **text entry** | we hold this description | skipped, not re-fetched |
| **absent entirely** | we do not hold it | fetched again |

It briefly had a third — see [the section below](#the-store-used-to-hold-verdicts-too--and-why-that-was-removed-adr-0089), which is worth reading, because the reasoning that put it there is more
persuasive than the measurement that took it away.

This maps onto the log line the stage prints:

```text
personio: filled 4 from the store, learned 1, queued 0 to re-derive,
          339 still unrecorded
```

`filled` = restored into the corpus · `learned` = new or changed text saved ·
`queued to re-derive` = ids handed to `update_meta` · `still unrecorded` = no text this run and
none stored, so the next run starts here again.

### Why the storage looks odd

Writes are **append-only**. Each run writes one small `{seq}.jsonl.gz` fragment per ATS holding
only what changed; the big `base.jsonl.gz` is rewritten only by `--compact`. Readers load
base-then-fragments in order, last write winning.

That is not over-engineering. Rewriting the whole store every run would mint a fresh copy of every
`base.jsonl.gz` — ~362 MB of that total — *per run*, and HF keeps every blob forever — the
exact mistake that filled the 100 GB quota in ~45 runs on `data/lancedb`.

---

## Why the text, and not just a `has_description` flag

Because downstream code needs the actual words to do its job, not the knowledge that words exist
somewhere:

- `embed_run` has to feed the text into the model to produce a vector
- `experience.extract()` reads the description to find "3+ years"
- `salary.extract()` reads it to find "₹10-12 LPA"
- `is_english()` reads `title + description[:500]` to decide language

A boolean can't be embedded and can't be regex'd. You'd know the job has a description and still be
unable to use it.

And here's the thing — `has_description` **already exists** (`doc_prep.py:192`). It just answers a
different question. It's a flag *about the vector*: `embed_plan:92` reads
`if row.get("has_description") is False` to find vectors that were built from a bare title and are
candidates for an upgrade re-embed. So:

- `has_description` → *"is this vector degraded?"* — a planner decision
- the store's text → *"what do I actually embed and extract from?"* — the work itself

They are complementary, not alternatives.

---

## The store used to hold verdicts too — and why that was removed (ADR-0089)

Until 2026-08-26 the store had a **third** state. A `null` entry meant "the detail pass *answered*
and this posting genuinely has no description" — an authoritative absence, recorded so the Job
would stop being re-fetched every run. It was gated on a `Job.detail_fetched` flag the scraper set.

It was removed, and the reasoning is worth keeping because it is a good example of a design that
looks right and measures wrong.

**The state is real, but it is tiny — and the flag feeding it is wrong on live data.** The
reproducible measurement is the store's own: of **417,773** entries it had accumulated, only **8**
were `null`, all eightfold. A wider live sample across six detail-pass ATSes did turn up a handful
of postings that genuinely have no description — three still verifiable on their live pages, none
of them a tech role — but that board draw was random and unseeded, so ADR-0089 deliberately does
not quote its per-ATS counts and neither does this file. Where empties came in *large* blocks they
were bad fetches: Zoho job pages serving an error body under HTTP 200 is the clearest case.

The decisive measurement is on eightfold itself, the only scraper that ever set the flag. On
`telekom-growthhub`, its `position_details` API answers **HTTP 200 with no description** for 5 of 5
postings whose public pages carry full text. That maps to `""`, which is exactly what set
`detail_fetched = True` — so all five would have been recorded as "genuinely has none",
permanently, and one of them passes the tech gate and would have reached the store. That one
already *is* in the store, recorded as having no description while its page serves 5,677
characters: a falsehood the flag wrote into production data. And it is not alone — every one of
the 8 `null` entries was checked against its live page, and **5 of the 6 that could be checked are
wrong**. Exactly one is right, and it is the only *tech* posting ever confirmed to be in the
category the state existed to record. A completed fetch is not an authoritative answer.
ADR-0089 has the per-entry table and the repro; read it there rather than trusting this
retelling.

**The flag was inert almost everywhere.** Nine scrapers declare `has_detail_pass` — eight
scrapable, since `join` is disabled — but only eightfold ever set `detail_fetched`, and, not
coincidentally, `needs_detail` (the skip-list's only consumer) is also called by eightfold alone.
So for the other seven the flag changed nothing: they re-fetch every detail every run whether a
Job is settled or not.

**So what are the 1,974–2,165 "still unrecorded" Jobs each run?** Not postings that lack a
description — **fetch failures**. Measured over five consecutive runs on 2026-08-26, the counter is
successfactors 844–872, zoho 390–409, workday 111–615, smartrecruiters 119–132, and personio
anywhere from 1 to 343 — and eightfold, the scraper this whole section is about, 0 or 1. Every
one of them is correctly *not* settled, which is why removing the settle branch does not change
the count: those same five runs each logged `settled 0 as having none`.

Today the store is two-state: `id -> text`, and membership means we hold the words. A legacy `null`
reads as unheld, and `compact` drops those 8 on its next pass.

**One thing this leaves open**, and it is the more interesting question: the skip-list publishes
an id for every description the store holds — 417,765 on 2026-08-26 — and only eightfold reads
it. Making
the other seven scrapers consult `have_details` would save a lot of fetching — but Workday's detail
carries `startDate`, the only `posted_at` source, so it is a real design problem, not a switch.
ADR-0089 deliberately does not foreclose it. (It used to carry a second blocker — `_posting_key`
read the detail's `jobReqId`, so skipping a detail renamed the posting. ADR-0097 removed that one:
identity now comes from the listing alone.)

## Files to read, in order

1. **`src/headstart/ingest/doc_prep.py`** — start here. `META_FIELDS` (line 42), `to_meta()` (line
   173, the function that computes everything), and `DERIVATIONS_VERSION` (line 170). Read the
   comment block *above* the constant: it is a changelog of every bump and why, and it is the best
   documentation in the file.
2. **`src/headstart/ingest/update_meta.py`** — read the module docstring. ~45 lines, and it
   explains the facts-vs-derivations split in the codebase's own words.
3. **`src/headstart/ingest/update_descriptions.py`** — the module docstring covers the store's two
   directions and why membership means text and nothing else; `reconcile()` is where the logic
   lives, and `_entries()` is the one place the "held" rule is written down.
4. **`src/headstart/experience.py`** and **`src/headstart/salary.py`** — the extraction cascades
   themselves, once you want to see what actually does the extracting.
5. **`.github/workflows/pipeline.yml`** — the authoritative stage order, with a comment on most
   steps explaining why it sits exactly there.
