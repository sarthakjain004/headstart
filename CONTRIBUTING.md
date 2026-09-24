# Contributing

Thanks for looking. HeadStart is a small project with strong habits. Following them is most of
what a good change here takes.

## Set up

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Before you open a PR, run the same checks CI runs:

```bash
ruff check . && ruff format --check .
pytest                          # network-free, fixture-based
node --test tests/js/*.test.js  # the UI tests: Node 22+, no npm install
```

A handful of test modules need the embedding stack (`torch`) or the browser driver (`pydoll`).
They skip under `[dev]`, as they do in CI. Install `.[embed,scrape]` to run them too. The
résumé editor's browser tests also need Playwright's Chromium (`playwright install chromium`).

## What a good change looks like

- **One issue, one PR.** Keep the diff to what the issue asks for. Don't reformat or refactor
  neighbouring code on the way past.
- **Measure, don't assume.** If a change rests on how a real ATS endpoint behaves (pagination, rate
  limits, response shape), hit the endpoint, and say in the PR how many hosts you sampled. Many of
  the ADRs exist because a plausible assumption died on contact with a live host.
- **Tests come with the change.** A bug fix brings a test that fails without it. A scraper change
  brings or updates a fixture under `tests/fixtures/`.
- **Record non-obvious decisions.** If you chose between real alternatives, add a numbered ADR in
  [`docs/adr/`](./docs/adr/) and a row in its index (`tests/test_adr_index.py` checks the index).
- **Use the domain words.** [`CONTEXT.md`](./CONTEXT.md) defines Board, Slug, Job, Liveness and the
  rest. Use those terms in code and prose.
- **Keep commit messages short** (50 words at most).

## Adding an ATS scraper

A new scraper is a `BaseScraper` subclass in `src/headstart/scrapers/{ats}.py`, added to
`SCRAPERS` in `registry.py`. Before its jobs ship, in the same PR:

1. Set the class's `url_shape`, a regex every job link it builds must fully match, checked against
   the ATS's real routing. Add a test that each parsed job's `url` matches it (see
   `test_job_url_matches_url_shape` in `tests/test_bamboohr.py`).
   `scripts/eval/verify_filters.py` reads the shapes from there and checks served links against
   the live Space.
2. Add a liveness ledger at `data/validate/liveness/{ats}.csv`. `scrapable_boards.load` only reads
   ledgers, so without one the scrape plan never picks the ATS.
3. Document what you measured about the ATS under `docs/{ats}/`.

[`CLAUDE.md`](./CLAUDE.md) has the long form of these rules, including the traps each existing
ATS taught us, and [`.claude/skills/add-ats-scraper/`](./.claude/skills/add-ats-scraper/)
walks a coding agent through the whole process.

## Where things go

- The ingest pipeline is `src/headstart/ingest/`, one module per stage (ADR-0028).
- One-off and R&D scripts go in `scripts/{stage}/` (`discover/`, `validate/`, `eval/`, …), never
  loose in `scripts/`.
- Pipeline data lives in a Hugging Face dataset, not in git. See
  [`docs/agents/deployment.md`](./docs/agents/deployment.md).

## Reporting security issues

Please don't open a public issue. See [SECURITY.md](./SECURITY.md).

## License

By contributing you agree that your contribution is licensed under the
[GNU AGPL v3](./LICENSE), the project's license.
