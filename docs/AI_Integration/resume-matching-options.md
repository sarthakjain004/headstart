# Resume matching — options and costs

**Status:** decision brief, nothing built. Once a route is chosen, record it as an ADR under
[`../adr/`](../adr/) and this file becomes background.
**Date:** 2026-07-26

A user pastes their resume into a separate box ("add your resume"), and gets back the Jobs that
best suit them, each showing how well it matches. This document lays out the realistic ways to
build that, what each costs, and what each can honestly promise.

---

## Already settled — don't reopen

These came out of the design session and are inputs, not open questions.

**The resume is transient, not stored.** The user pastes it, we use it, we forget it. No new
person-entity, no persistence, no PII at rest. **Subscriber** (the Telegram alert concept) is
untouched — a stored resume would have collided with it, since a Subscriber is already "a person
with match criteria who gets told about new Jobs."

**Years of experience stays a manual filter.** The user types it into the existing control. It is
never inferred from the resume. This keeps CLAUDE.md's filters/query split intact.

**An AI layer distils the resume before embedding.** It reduces the raw resume to role signal —
skills, technologies, what they built, problem domains. This is *not* the LLM query-parser CLAUDE.md
defers: that one infers **filters** from free text, this one produces **embedding input**. The
distinction is load-bearing, and it holds only if the distilled text contains no structured
constraints (no years, no location, no salary). Distillation also fixes three problems at once — it
cuts embed time from ~2 s to ~0.2 s, sharpens a vector that would otherwise average several careers
together, and keeps name/phone/email out of the encoder.

**Retrieval is two-stage.** Cosine retrieves the top N, then N get scored properly. N is
configurable, default 30.

**Calibration/validation is deferred.** Noted as a risk below, not a blocker.

---

## The constraints that actually bind

Discovered while grilling the design. These are why the obvious answer doesn't work.

**The index stores no description text.** `index._schema()` holds id, ats, company, title, location,
remote, employment_type, experience, min_years, max_years, experience_source, salary, department,
url, posted_at, and the vector. That is deliberate (ADR-0005/0006): a **Doc** is a transient string
assembled at embed time, encoded, and dropped. `title` is the only surviving text.

**A cross-encoder cannot run on stored vectors.** A **bi-encoder** (what search uses today) embeds
two texts *separately* and compares the finished vectors — which is exactly what makes precomputing
possible. A **cross-encoder** feeds *both raw texts into one model together* so every resume token
attends to every description token; that joint pass is the accuracy gain, and it is why nothing can
be precomputed. Generalised: **anything computed from the stored vectors alone cannot know more than
cosine already knows.** More signal means going back to the text.

**The corpus is large and the Space re-downloads it.** Measured on HF today: `data/lancedb` is
**747 MB**, `data/embeddings` is 1,057 MB, and the embedding store holds **263,769** vectors. The
Space `snapshot_download`s the LanceDB directory *at every boot* (`app.py:28`), and free-tier Spaces
sleep when idle, so this is paid on every cold start — not once.

**Serve-time compute is 2 vCPU.** Measured yesterday (ADR-0029 thread sweep): a 137M-parameter model
sustains **558 tok/s** on 2 physical cores. Everything below extrapolates from that.

**Cosine scores are not percentages.** Recorded in [../learnings.md](../learnings.md). Range never
approaches 0, magnitude was never calibrated against outcomes, and the scale shifts with query
length. A cross-encoder's sigmoid output does **not** fix this — it is differently scaled, still
uncalibrated for "does this person fit this job."

### Per-Job storage, measured and derived

| item | per Job | × ~250k Jobs |
|---|---:|---:|
| vector (768 × float32) | 3.0 KB | ~750 MB (matches the observed 747 MB) |
| cleaned description (~1,040 tok × ~4 chars) | ~4.2 KB | **~1.04 GB** |
| skills list (~20 × ~15 chars) | ~300 B | **~75 MB** |

---

## The options

### Option 1 — Cosine only, no number

Distil the resume, embed, cosine search, show the ranked list. Either no score at all, or a coarse
band ("strong / possible") derived from rank position rather than raw distance.

**Cost:** essentially nothing. A POST endpoint, a textarea, and the existing `_search()` path.
Storage unchanged, serve time ~0.2 s.

**Gives you:** a working feature quickly, and an honest one — you promise ordering and deliver
ordering.

**Doesn't give you:** the match number you asked for.

*Pick this if* you want it shipped and measured before investing, and can live without a number
for now.

### Option 2 — Cosine ranks, coverage supplies the number

Extract a `skills` list per Job **at ingest time on GitHub Actions** — the same shape the pipeline
already uses twice, in `experience.py` (ADR-0018) and `tech_filter` (ADR-0017). Extract skills from
the resume in the distillation pass you're already doing. The displayed number is set overlap:
*"you meet 7 of 9 listed requirements."*

**Cost:** +~75 MB index (+10% on the boot download), a new ingest step plus a backfill over the
corpus, and a canonical skill vocabulary. Serve time is a set intersection — microseconds.

**Gives you:** a real number that is *defensible without calibration*, because it counts concrete
things rather than predicting an outcome. It self-explains — you show which skills matched and which
didn't — which is also the cheapest partial answer to the deferred validation problem, since a user
can verify a list themselves in a way they can never verify "78%".

**Doesn't give you:** better *ordering* than Option 1 — ranking is still bi-encoder cosine. And it
only sees the skills axis: "experience leading a team," "thrives in ambiguity," and domain fit fall
straight through. A job listing 3 must-haves and 15 nice-to-haves will score a perfect candidate at
20% unless must-have and nice-to-have are separated.

*Pick this if* an honest, explainable number matters more than squeezing out ranking accuracy.

### Option 3 — Cosine retrieves, cross-encoder reranks

Store descriptions, ship a reranker in the Space image, and rescore the top N as text pairs.

**Cost, and this is the problem:** descriptions add **~1.04 GB**, taking the Space's cold-boot
download from 747 MB to **~1.8 GB — 2.4× what it is now**. Plus per-search latency:

| reranker | params | est. rate | N=30, full text | N=30, truncated to 256 tok |
|---|---:|---:|---:|---:|
| MiniLM-L6 cross-encoder | 22M | ~2,750 tok/s | ~12 s | **~4 s** |
| bge-reranker-base | 278M | ~275 tok/s | ~124 s | ~39 s |

A pair is ~100 resume tokens + a ~1,040-token description ≈ 1,140 tokens; N=30 is ~34k tokens per
search. Note the tension in the right-hand column: **truncating to 256 tokens shows the reranker a
quarter of the job description** — you are cutting away the text the cross-encoder was adopted to
read. The cheap column and the accurate column pull against each other.

RAM is *not* a constraint — free-tier Spaces have 16 GB, and even a 278M reranker adds ~1.1 GB.

**Gives you:** genuinely better ordering within the top N, because cross-attention sees both texts
together.

**Doesn't give you:** a trustworthy percentage. Its score is uncalibrated for this task in exactly
the way cosine is.

*Pick this if* ranking accuracy within the shortlist is the thing you care about most, and the
cold-start cost is acceptable.

### Option 4 — Cross-encoder reranks, coverage supplies the number

Options 2 and 3 together: best ordering, honest number.

**Cost:** the sum — ~1.11 GB added, 4–124 s per search, both build efforts.

**Gives you:** the best version of the feature on both axes.

**Doesn't give you:** anything that justifies the cost until Options 2 and 3 have each been measured
separately. Building both at once means never learning which one earned its keep.

*Pick this if* Options 2 and 3 have both shipped and both proved out.

### Option 5 — Cosine retrieves, an LLM scores the top N

Send each (resume, job) pair to an LLM and have it return a structured verdict: matched
requirements, missing ones, a short rationale.

**Cost:** money per search (N calls), an API key in the Space, and a hard dependency on an external
service in the serving path — which today has none; `anthropic` is an `eval`-only extra used by
`scripts/eval/judge_pool.py`. Latency ~2–4 s if the N calls run in parallel.

**Gives you:** the highest quality and the best explanations by a distance, and you already own
similar machinery in the eval harness.

**Doesn't give you:** free operation. Cost scales with traffic, which is the one thing a portfolio
project can't bound.

*Pick this if* quality of explanation matters more than running cost.

---

## Side by side

| | 1 · cosine only | 2 · coverage | 3 · cross-encoder | 4 · both | 5 · LLM |
|---|---|---|---|---|---|
| index growth | none | +75 MB | +1.04 GB | +1.11 GB | none |
| cold-boot download | 747 MB | 822 MB | **~1.8 GB** | ~1.9 GB | 747 MB |
| serve latency | ~0.2 s | ~0.2 s | 4–124 s | 4–124 s | ~2–4 s |
| marginal money | none | none | none | none | **per search** |
| ranking quality | baseline | baseline | **best local** | **best local** | best |
| number shown | none/band | **honest, explainable** | uncalibrated | **honest** | **honest** |
| explains itself | no | **yes** | no | yes | **yes** |
| build effort | small | medium | medium-large | large | medium |
| reversible | trivially | easily (drop a column) | painfully (1 GB backfill) | painfully | easily |

---

## Recommendation

**Option 2, with Option 3 as a later upgrade gated on measurement.**

The reasoning. You asked for a *real match number*, and Option 2 is the only route that delivers one
you can defend without calibration data you don't have — a count of concrete things beats a
prediction dressed as a percentage. It costs +10% on the boot download against Option 3's +140%,
and it keeps serve time interactive, which 4–124 s plainly is not. It also puts the heavy work at
ingest on GitHub Actions, which is where this repo already puts heavy work, and follows a pattern
the pipeline has used twice.

The honest counter-argument: Option 2 does nothing for ranking. Its number is only as good as skill
extraction, and if descriptions turn out to be prose-heavy without recoverable skill lists, the
whole thing degrades. **That is an empirical question and worth a spike before committing** — pull
200 real descriptions, extract skills, read the output, and see whether the lists are any good. That
read-then-decide loop is how ADR-0018's patterns were found.

Option 3 is not wrong, it is *expensive in a way that lands on the user*: a 1.8 GB cold start and a
multi-second search, in exchange for better ordering of 30 results the user will mostly skim. If
ranking quality turns out to be the real complaint after Option 2 ships, that is the moment to buy
it — and measure it against the eval harness rather than assuming.

---

## Open regardless of choice

- **`/search` takes `q` as a GET parameter** (`app.py:159`). A pasted resume will exceed practical
  URL limits — this needs a POST endpoint whichever option wins.
- **Skill vocabulary normalisation** (Options 2, 4): `k8s` / `Kubernetes` / `container
  orchestration` must collapse to one canonical term or coverage undercounts badly.
- **Must-have vs nice-to-have** (Options 2, 4, 5): weighting them equally makes strong candidates
  look weak.
- **No resume→Job eval set.** ADR-0011's harness grades *query*→Job, with the judge validated by
  Cohen's kappa. Resume→Job has no labelled data, so no option here can currently be measured
  against ground truth. Deferred by decision, but it is the thing that would tell you whether any
  of this works.
- **Glossary terms pending.** `CONTEXT.md` has no entry for **Query** at all, and **Doc** is defined
  as strictly per-Job — so the pasted resume and its distilled form are unnamed. Proposed:
  **Resume** (what the user pastes), **Profile text** (the distilled role signal that gets
  embedded), and **Query** promoted to a real entry covering both it and the typed search string.
