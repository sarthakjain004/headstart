---
title: HeadStart Semantic Job Search
emoji: "\U0001F50D"
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# HeadStart — semantic job search

Semantic search over HeadStart's tech-job corpus (scraped directly from company ATS boards):
nomic embeddings → LanceDB filter-then-rank. The index is refreshed nightly by the repo's
`nightly-pipeline` GitHub Actions workflow, which uploads the LanceDB table to a private HF
dataset and restarts this Space.

Source: <https://github.com/sarthakjain004/headstart> (deployment design: ADR-0020).
