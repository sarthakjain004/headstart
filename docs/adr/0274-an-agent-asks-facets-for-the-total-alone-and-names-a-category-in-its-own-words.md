# ADR-0274: An agent asks `/facets` for the total alone, and names a category in its own words

**Status:** accepted · **Date:** 2026-09-29 · **Relates to:**
[ADR-0253](0253-an-agent-reads-the-spaces-read-routes-through-a-read-scoped-token.md) (the agent
contract and its version), [ADR-0267](0267-the-space-hosts-the-mcp-server-at-a-url-anyone-can-add.md)
(the hosted `/mcp`), [ADR-0220](0220-a-trained-title-classifier-decides-a-role-family.md) (the role
families and their retired ids), [ADR-0104](0104-a-keyword-filter-with-a-scope-map-and-a-stored-description-column.md)
(the description keyword and its coverage)

## Context

An independent critique of the Space MCP server (2026-09-29, round 1) found two faults this
decision closes.

**No category list on the hosted server (P0-2).** `space_mcp/role_families.py` looked for
`config/role_families.json` three directories above the package. That is the repository on an
editable install, but in the Space's image the package is `/app/headstart` and the config
`/app/config`, two levels up, so the hosted `tools/list` offered `category` as a free string with
no enum. The `uvx` install has no `config/` at all. A model could not discover the ids, and the
label every answer prints ("AI, ML & Data Science") was refused as input with no list of what
would be accepted. `search_jobs` read the retired `ai-ml` as 0 jobs, while `read_trends` quietly
read `python-development` as Software Engineering.

**A description keyword took 100 to 125 s (P0-4).** `search_jobs` asks `/search` and `/facets`
at once. Measured live on 2026-09-29 with `kw_in=description`, `/search` took 10.6 s and `/facets`
98.7 s: the facet strip counts about 46 options, and each one re-scans every row the other
filters match for the keyword. Through the hosted `/mcp`, the critique's two such calls took
102.4 s and 124.8 s. Re-measured on the same day, "relocation" in titles or descriptions took
113.0 s, and "kubernetes" in the descriptions of remote jobs 22.9 s: the cost grows with the rows
the other filters leave to scan. A concise answer prints none of those option counts; it prints
the total, the Blocking filter when nothing matched, and now the description coverage.

## Decision

**1. The list is found on every install.** `role_families.FILE` is the first of: a copy beside the
module, which the wheel carries (`pyproject.toml` force-includes `config/role_families.json` as
`headstart/space_mcp/role_families.json`, which is what `uvx` builds from GitHub's archive); then
`config/role_families.json` in each ancestor directory, nearest first, which finds the repository
on an editable install and `/app/config` in the Space's image. The ancestors are walked, never
indexed, the rule `search_filters.fx` already follows for `fx_rates.json`. Without the file
anywhere, `category` is still a free string that the Space refuses on its own. Tests import the
package in a fresh interpreter from a copied `/app/headstart` + `/app/config` layout and from a
wheel's layout, and a wheel built with `uv build` was installed and read 25 families.

**2. A caller's words for a category are read as one current family.** `role_families.resolve`
reads an id, a label, a close name or a retired id, case-blind, by words: an exact match of every
word of an id or label wins ("ai-ml", "AI/ML", "AI, ML & Data Science"). Otherwise every family
whose id or label holds all the words asked is a candidate ("machine learning" is a word set of
the retired "AI / Machine Learning"). A retired family is followed to the current one that took
it over. A name that fits several families is refused naming them, and one that fits none is
refused listing every current id with its label. The enum and the refusals list only current
families.

The server checks every argument against its schema before a tool runs, so a label would be
refused by the enum before the tool could read it. A tool therefore names a reader per argument,
`SpaceTool.argument_readers`, and `server.call` runs it before the check. `search_jobs` and
`read_trends` both read `category` through `role_families.resolve`, and a contract test holds every
tool with a `category` to it. A reader hands back a value it cannot read unchanged, so a wrong type
is still the schema check's to refuse. The answer states the category it searched as
`category ai-ml-data-science (AI, ML & Data Science)`.

**3. `/facets` answers the total alone when asked.** `counts=total` makes
`JobSearch.facets` call `facets.counts(..., only_total=True)`. That counts no option, so `facets`
is `{}`. It still counts the total, `blocking` (only when the total is zero, as before) and
`description_coverage` (only under a description keyword, as before). It is cached in the same
LRU as the full strip, keyed by the mode too. `counts=all` or no `counts` is the full strip, and
any other value is a 400. The page never sends it. This is new agent contract, so the app's
`_AGENT_API_VERSION` and the server's `AGENT_API` rise to 2 together, and a server that needs it
refuses an older Space instead of receiving a full strip it did not ask for. A concise
`search_jobs` sends `counts=total`, and `detail: "full"` still asks for, and prints, every option.

## Options rejected

- **Serve the family list from the Space** (a `/families` route, or a field on `/trends`), so
  every install reads it live. `tools/list` is answered before any Space read, and on stdio before
  the Space is known to be awake. The list only changes with a deploy, which rebuilds the wheel
  and the image anyway. It would also be new contract for no gain over a file the package
  already carries.
- **An environment variable naming the config** that the Space sets. The walk needs nothing set,
  and a variable the image forgets to set is the same bug again.
- **Loosen the schema so labels pass the check** (no enum, or `anyOf` an enum and a string). The
  enum is how a model discovers the ids, and `anyOf` is outside the keywords the contract tests
  allow.
- **Skip `/facets` in a concise answer.** That loses the total the answer opens with, and the
  Blocking filter an empty answer needs.
- **Put a total on `/search`.** Its response is a bare array its clients already read, the reason
  `/facets` was made its own route (issue #275).
- **A full-text index on `description`.** This is the real fix for the scan itself, and it would
  speed up `/search` too. It is backend work with a storage cost on an index that is already at
  its budget, so it is deferred. This decision removes the other 88 s.

## Risks, stated plainly

- **A reader runs before the schema check,** so a reader that raised on a wrong type would hide
  the check's sentence. `resolve` passes a non-string through, and a test pins that.
- **Word matching can be too generous.** "security" reads as Security, and so does its retired
  "Security Engineering". A one-word name shared by two families is refused, not guessed. A
  family whose words are a subset of another's is read as the exact one first.
- **A description keyword still pays one scan.** `/search` and the count-only total each scan
  once, in parallel, so a concise description-keyword answer takes about as long as `/search`
  alone (10.6 s in the measurement above), not zero. `detail: "full"` still pays the whole strip.
- **The wheel's copy can go stale only with the wheel.** It is built from the same commit's
  `config/`, and `uvx` rebuilds when `main` moves.

## Consequences

- The hosted and `uvx` servers list the 25 current families as `category`'s enum. A label or close
  name is read as its id, and a refusal lists the ids with their labels.
- A concise `search_jobs` under a description keyword no longer waits on the facet strip.
- `tests/test_space_mcp_role_families.py` covers the layouts and the reading;
  `tests/test_serving_facets.py`, `tests/test_serving_job_search.py` and
  `tests/test_space_mcp_against_space_app.py` cover `counts=total` through the real app;
  `tests/test_space_mcp_tools.py` holds every tool's readers to its schema.
- `docs/agents/space-mcp-server.md` describes both, and the iteration tasks gain t13 (a
  description keyword) and t14 (a category named by its label).
