# Portfolio strategy — making this a standout AI/ML project

Target: **AI / ML Engineer** roles (new grad). This doc is about *positioning* — what to build
and emphasize so the project reads like senior work, not a class assignment.

## The honest framing

**"I built a RAG chatbot" is commodity now.** Every bootcamp grad has one. What makes an AI
project stand out is not the model — it's **judgment, scale, and measurement.** The good news is
HeadStart already supplies the rare cards.

## The rare cards HeadStart already holds

Most student projects fake these; yours are real. Lead with them.

- **Real scale** — ~3.3M Job postings across 16 **ATS** platforms, global.
- **A production pipeline** — incremental, id-keyed indexing; liveness validation; dedup.
- **Documented decisions** — `CONTEXT.md` (domain glossary) and `docs/design-choices.md` (the
  reasoning behind non-obvious calls). Great for the writeup *and* for answering "why did you do
  it that way?" in an interview.
- **A live demo surface** — the Telegram bot is a working product people can actually use.

The scraping/pipeline half is already a strong *data-engineering* project on its own; the AI
layer broadens it to *AI/ML systems*. That's two projects' worth of signal in one repo.

## The components that signal depth (not tutorial-following)

- **LLM query understanding** (structured outputs) — shows you know LLMs are for *parsing*, not
  just chatting.
- **Hybrid retrieval** (structured filter + semantic rank) — the judgment piece. Being able to
  explain *"why I don't just embed everything — embeddings can't do `salary > 150k`"* shows
  information-retrieval knowledge, not cargo-culting.
- **LLM re-ranking** — depth beyond vanilla vector search.
- **Evaluation harness — the centerpiece.** Almost no portfolio RAG project has one, and it is
  exactly what AI-engineering roles screen for. Build a small labeled set, measure recall@k /
  nDCG@10 / MRR, run an **LLM-as-judge** to score relevance at scale, and **ablate**: "hybrid
  beat pure-vector by X; re-rank added Y." (See [glossary.md](glossary.md) for every term.)
- **LLM enrichment pass** — normalizing the messy `experience`/`salary` fields turns a
  data-quality liability into an AI feature you can talk about, and it's what makes the structured
  filters actually work.

## Why evaluation is *the* differentiator

Most candidates can say "I used embeddings." Almost none can say "I measured nDCG@10, ran
ablations, and used an LLM-judge I validated against human labels." Evaluation is what turns
"I built a thing" into "I built a thing, proved it works, and can reason about why" — the exact
skill AI/ML roles are hiring for. It also gives you the killer interview answer *and* a concrete
number for the resume bullet.

## Example resume bullet

> Built a hybrid semantic + structured Job-search engine over 3.3M postings scraped from 16 ATS
> platforms — LLM query-parser (structured outputs) extracts hard filters + intent, FAISS/LanceDB
> vector retrieval with LLM re-ranking, evaluated at **nDCG@10 = 0.XX** via an LLM-as-judge
> harness; incremental indexing keeps the corpus fresh at &lt;$Y/scrape.

## What to deliberately avoid (resume-driven over-engineering)

Experienced reviewers see through these:

- **Fine-tuning an LLM from scratch** — expensive, usually worse than off-the-shelf, and signals
  you don't know when *not* to train.
- **Building your own vector database** — use FAISS/LanceDB; reinventing it isn't the interesting
  part.
- **Kubernetes / microservices for a solo project** — a clean, well-documented monolith with a
  real evaluation beats distributed-systems theater. Depth comes from the eval and the hybrid
  design, not infrastructure.

## Emphasis for AI/ML Engineer specifically

Lean hardest into the **evaluation rigor** (metrics, ablations, validated LLM-judge), the
**re-ranking** (bi-encoder → cross-encoder, with measured lift), and the **enrichment** pass.
Optionally, a learned re-ranker if you want extra ML depth. Metrics are the headline; the scale
and pipeline are the credible foundation underneath them.
