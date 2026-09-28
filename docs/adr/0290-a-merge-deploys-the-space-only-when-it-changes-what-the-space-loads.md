# ADR-0290: A merge deploys the Space only when it changes what the Space loads

**Status:** accepted · **Date:** 2026-09-29 · **Amends:**
[ADR-0156](0156-the-space-installs-headstart-as-a-real-package.md) (its deploy trigger, all of
`src/headstart/**`, and its cost estimate for a deploy that changes nothing, "wasted but cheap"),
[ADR-0267](0267-the-space-hosts-the-mcp-server-at-a-url-anyone-can-add.md) (its deploy-trigger
paragraph, including the `resume_mcp` negation it kept from
[ADR-0253](0253-an-agent-reads-the-spaces-read-routes-through-a-read-scoped-token.md)) ·
**Issue:** #836

## Context

`deploy-space.yml` ran on every push to main that touched `src/headstart/**`, `config/**` or
`deploy/hf-space/**`, less `resume_mcp`. ADR-0156 accepted that on purpose: a change the Space
never imports would deploy it for nothing, "wasted but cheap", and in return nothing the Space
does import could miss a deploy.

**How often.** There were 62 deploy-space runs on 2026-09-28 (UTC). Each push's own diff shows
that 34 of them changed no file the Space loads or reads. They were a scraper, an ingest stage, a
ledger module or the alerts sender.

**What the Space loads.** Loading `deploy/hf-space/app.py` and reading `sys.modules` gives 51
`headstart` modules. They are four top-level modules (`__init__`, `embedding_conventions`,
`llm_router`, `log`), plus every module of `mcp_protocol`, `search_filters`, `serving` and
`trends`, and every module of `space_mcp` except `__main__`. The Space also loads 4 of the 15
modules in `alerts`, 4 of the 13 in `boards` and 2 of the 6 in `jobs`. It loads nothing from
`scrapers`, `network` or `ingest`. One function in `boards.board_identity` imports
`scrapers.registry` when called, but the Space calls only `ats_of`, `lower_key` and `tenant` from
that module. The Space also reads `src/headstart/ui/` and `config/` as files.

**What a deploy costs.** Every deploy boots a new container, and a boot is long. #842's image
built in 17 s from cached layers. Its container logged its startup at 22:20:17 UTC on 2026-09-28
and answered its first request at 22:26:37. Pulling the index and loading the encoder took about
five minutes of that, and the Hot ranking took 51 s. Whether HF keeps sending traffic to the old
container during that boot is not settled yet. The evidence so far:

- **A pipeline `restart_space` rolls.** Polling `POST /mcp` every 5 s through the restart at
  21:54:10 UTC gave 307 polls. The old boot answered throughout, apart from 2 single-poll HF edge
  502s.
- **A deploy seemed not to roll during the MCP eval.** The Space rebuilt at 12:03, 12:38, 12:56
  and 13:13 UTC, and the eval's stdio client reported the Space as "starting" during each boot
  (`docs/mcp/2026-09-28_space-mcp-eval-results.md`). That is a report from a client, not a poll.
- **The poll through #839's and #842's deploys was confounded.** From 22:13:45 until #842's boot
  answered at 22:26:37, only 21 of 125 polls reached the app. But the errors went on after the new
  boot was up: the container logged a 200 for every request that reached it, while most polls got
  502s with no `x-proxied-host`. Four popular Spaces also answered 502 or 503 from here at
  22:41, and four of five outside probes (check-host.net) got a 502 from ours. That was HF's edge
  failing across Spaces, so this window says nothing about deploys.

The no-op deploys are worth removing either way. Each one starts a boot of more than six
minutes on a 2 vCPU Space, and each one loses what the boot computed: the Hot ranking, the Trends
answers and the per-caller rate-limit windows.

## Decision

**`on.push.paths` lists what the Space loads, and nothing else.** A package is named by a `**` glob
when the Space loads all of it. A package the Space loads only part of has each loaded module
listed by file. The list also covers `src/headstart/ui/**`, `config/**` and
`deploy/hf-space/**`. `space_mcp/__main__.py` is negated, since it is the local stdio entry point.
`resume_mcp` needs no negation any more, because no pattern includes it. The staging step is
unchanged: the whole package still ships (ADR-0156), so a change outside the list reaches the
Space with the next deploy.

**A test pins the list in both directions.** `tests/test_space_deploy_trigger.py` loads the app
in a fresh interpreter. The model, the index and the Hub download are stubbed, as
`tests/test_space_app.py` stubs them. The test then reads the files of every loaded `headstart`
module. It fails if a loaded module is outside `paths`, which is the staleness ADR-0156 feared:
a change that would reach the Space only when some later change deployed it. It also fails if
`paths` covers a module the Space never loads, which is the restart for nothing that this ADR
removes. Files under `ui/`, `config/` and `deploy/hf-space/` are checked by path, since
`sys.modules` cannot see them.

**A concurrency group, with `cancel-in-progress`.** A deploy that starts while an earlier one is
still uploading cancels the earlier one. The newer checkout holds every earlier change, and
`upload_folder` makes one commit, so a cancelled run leaves the Space on either the old commit or
the new one, never a mix. This buys little, and the gain is stated here so that nobody counts on
it. A run takes a median 17 s, and only 3 of the 62 runs on 2026-09-28 started while an earlier
one was still running. The minutes of errors come from HF booting each commit, and the group
cannot merge commits that land minutes apart.

## Options rejected

- **Negate only `scrapers`, `network` and `ingest`.** This is simpler, and it fails safe: a new
  package would deploy by default. But 40 of the 62 runs on 2026-09-28 would still have deployed,
  against 28 with this list. The ledgers under `boards`, the alerts sender and the tech filter,
  salary and experience extractors under `jobs` would each still restart the Space.
- **Debounce: wait N minutes inside the concurrency group before uploading.** A merge landing
  during the wait would cancel the waiting run, so a burst would deploy once. On 2026-09-28 this
  would have cut the 28 deploys to 21 with a 5-minute wait, or to 16 with a 10-minute wait. The
  cost is that every change reaches the Space N minutes later. This is left to the owner.
- **Keep `src/headstart/**`** (ADR-0156). ADR-0156's reason was the staleness risk of a curated
  list. The test now catches that risk before merge. And a wasted deploy costs a six-minute boot
  and everything that boot had computed, not only idle CI minutes.

## Consequences

- A PR that makes the Space import a new module fails the test until `paths` names that module.
  A PR that adds a module the Space never loads to a package listed by a `**` glob fails the test
  until the module gets a `!` negation.
- On 2026-09-28, 34 of the 62 deploys would not have happened.
- Narrowing the trigger makes deploys rarer. It does not change what one deploy does to the app.
  That still needs a clean measurement. ADR-0267's §Risks is where it is recorded (#837).
