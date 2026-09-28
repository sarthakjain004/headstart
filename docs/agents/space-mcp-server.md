# The Space MCP server — search, trends and hiring now for an agent

A local MCP server that lets an agent read HeadStart the way the website does: find open tech jobs,
see how the number of openings is changing, and see which companies are hiring hardest this week.
It runs on your own machine as a subprocess of your agent client and answers from the deployed
Space's own read routes, so every number is the one the website shows. The decision and its
alternatives are ADR-0253 and `docs/mcp/2026-09-28_space-mcp-server-plan.md`; this file is the
how-to.

It is **read-only**. It cannot save, follow, hide or subscribe to anything, and no account applies
to it — so a company you hid on the website is **not** hidden from an agent's search.

## Install it

1. **Get the token.** The Space admits this server with its `AGENT_TOKEN` secret, a read-scoped
   token separate from `ALERTS_TOKEN`. If it is not set yet, generate one and add it to the Space
   (this restarts the Space, which takes about four minutes to boot):

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   .venv/bin/python -c "
   from huggingface_hub import HfApi
   HfApi().add_space_secret('imPoseidon/headstart-search', 'AGENT_TOKEN', '<the token>')"
   ```

   It must differ from `ALERTS_TOKEN`: if the two are equal, the Space ignores `AGENT_TOKEN`.

2. **Add the server** from a checkout installed with `pip install -e .` (the base install is
   enough — no torch, no index):

   ```bash
   claude mcp add headstart-space --scope user --transport stdio \
     --env HEADSTART_AGENT_TOKEN=<the token> \
     -- /absolute/path/to/HeadStart/.venv/bin/python -m headstart.space_mcp
   ```

   The name comes first: the CLI reads a name placed right after `--env` as another pair and
   rejects it. Use the checkout's own interpreter by absolute path, because a user-scoped server
   starts from every project's directory. `claude mcp add --env` writes the token in plain text
   into `~/.claude.json`; to keep it out of that file, export `HEADSTART_AGENT_TOKEN` in your shell
   and use a project `.mcp.json` whose `env` says `"HEADSTART_AGENT_TOKEN": "${HEADSTART_AGENT_TOKEN}"`.

3. **Check it.** `/mcp` in Claude Code lists `headstart-space` with three tools. Without a client:

   ```bash
   npx @modelcontextprotocol/inspector@2.8.0 --cli \
     /absolute/path/to/HeadStart/.venv/bin/python -m headstart.space_mcp --method tools/list
   ```

`HEADSTART_SPACE_URL` points it at another deployment; the default is
`https://imposeidon-headstart-search.hf.space`.

### When the token is absent

The server still starts and lists its tools, and every call answers with the variable to set —
not a client that reports "failed to connect" and says nothing.

## The tools

**`search_jobs`** — open tech jobs. Put **only the role** in `query` ("backend engineer at a
climate startup"); years, pay, place, company, employment type and dates each have their own
field. Omit `query` to list the newest jobs that match the filters. The answer gives the total,
one page of jobs with their links and ids, how the page is ordered, and — when nothing matches —
the filter costing the most.

- `company` is the site's company box: any company name *containing* the text. A Board key from an
  earlier answer (`lever:razorpay`, or the start of a result id) means every Board of that company.
- `category` narrows one company's jobs to a job category, so it needs a directory company: a key,
  or an exact name, which is then read as the directory's largest company of that name — the
  answer says so.
- A salary bound needs `salary_currency` (30 lakh is `salary_min: 3000000`, `salary_currency: INR`).
- **With a `query`, `sort` orders only the 2,000 closest matches.** For the highest salary or the
  newest anywhere, omit `query` and narrow with `keyword` and the filters.
- `detail: "full"` adds how many jobs each filter option would give.

**`read_trends`** — how the number of open tech jobs changed over a window, with the changes that
are not hiring (counting changes, newly found boards, duplicate removals) separated out. Whole
index by default; or one `category`; or up to 10 `companies` (keys or exact names). `breakdown`
lists the lines by category, seniority `level`, watched `role` or `company`. A company's counts
begin 2026-09-13, when per-Board counting began, and the answer says so when you ask for more.

**`hiring_now`** — companies ranked over the trailing week on one Lens: `expansion` (net growth),
`volume` (jobs opened) or `rate` (jobs opened as a share of openings). Staffing firms and job
boards are left out unless you ask for them, as on the site. Each row carries a key the other two
tools accept.

## What it cannot tell you, and why

- **No job descriptions.** No Space route serves one; titles, companies and locations are what an
  answer carries, each quoted as scraped text.
- **No per-category Hiring now.** Ranking every company within one category costs about 11 ms a
  company at the Space; `read_trends` with a `category` and named `companies` answers it for the
  companies you name.
- **A cold Space takes minutes.** The Space restarts after every pipeline run and sleeps when idle;
  a boot measured 4 min 13 s on 2026-09-28. A call waits at most 90 s, then says the Space is
  starting — ask again in a few minutes.
- **An older Space is refused, not trusted.** Every reply from the app states the agent contract it
  serves; a Space older than this server is reported as needing a deploy, because it would ignore
  the strictness that keeps a mistyped filter from quietly widening a search.

## Adding a tool

The server is built to grow: a tool is **one module and one registry line**, and nothing else in the
server changes.

1. Write `src/headstart/space_mcp/tools/<tool_name>.py` (the module's name is the tool's name). It
   holds the answer function and ends with `TOOL = SpaceTool(...)`: title, description (the rule
   that matters most first; at most 2,048 characters), a closed input schema in the portable JSON
   Schema keywords, the one `when_to_use` sentence the server's instructions will carry, and
   `max_chars`, the answer's size at the tool's largest input.
2. Add `<tool_name>.TOOL` to `REGISTRY` in `src/headstart/space_mcp/tools/__init__.py`. The server
   lists it, puts its `when_to_use` in its instructions, checks its arguments against its schema,
   fills its defaults, and cuts any answer past its `max_chars`.
3. A Space route no tool read before also needs: a `SpaceRoute` member; `AGENT_TOKEN` admitted on
   it in the Space's token map (`deploy/hf-space/app.py`, ADR-0253); and, when it is new contract,
   the Space's agent contract version and this server's `AGENT_API` raised together, so an older
   Space is refused rather than half-understood.
4. `tests/test_space_mcp_tools.py` holds every registered tool to the rules above without being
   edited. Add what the tool does to `tests/test_space_mcp_server.py` (against a fake Space) and,
   where it depends on the real app's answer, `tests/test_space_mcp_against_space_app.py`.
5. Describe it here, and give the evaluation (`scripts/eval/`) a task for it.

A tool that **writes** or reads **one Account's records** is a decision, not an addition: every tool
today is read-only and Account-free, the token opens read routes only, and the contract tests pin
both. It needs its own credential first (per-Account tokens are the deferred design).

## Where it lives

`src/headstart/space_mcp/` — `tools/` (one module per tool, and `REGISTRY`), `space_tool.py` (what a
tool is), `server.py` (serves the registry), `space_client.py` (the one way it reaches the Space),
`company_scope.py`, `role_families.py` and `scraped_text.py` — on the shared loop in
`src/headstart/mcp_protocol/`. Tests: `tests/test_space_mcp_*.py`.
