# ADR-0324: An agent reads what a role's postings ask for, counted over a sample

**Status:** accepted, amended by [ADR-0332](0332-a-requirements-sample-counts-each-requisition-once-under-its-directory-name.md) · **Date:** 2026-09-29 · **Relates to:**
[ADR-0253](0253-an-agent-reads-the-spaces-read-routes-through-a-read-scoped-token.md) (the agent
contract), [ADR-0258](0258-the-spaces-read-routes-answer-anyone.md) (the public read routes),
[ADR-0267](0267-the-space-hosts-the-mcp-server-at-a-url-anyone-can-add.md) (the hosted server),
[ADR-0273](0273-a-country-filter-matches-every-way-a-location-names-a-country.md) (the country
gazetteer), [ADR-0274](0274-an-agent-asks-facets-for-the-total-alone-and-names-a-category-in-its-own-words.md)
(package data a `uvx` install carries), [ADR-0277](0277-an-agent-reads-a-posting-by-id-and-finds-jobs-like-one.md)
(descriptions as untrusted text)

## Context

The second critique of the Space MCP server (2026-09-29, 6.0/10) found that a career switcher
gets no requirements view (its P1-10). "What does a data engineer typically need" could only be
answered by reading five postings through `get_job`, about 18 kB. "Which skills are in demand" had
no tool at all. A description-keyword count per skill took 20–45 s a skill and matched substrings,
so "rust" counted "Trust".

## Decision

**One new tool, `role_requirements`, over one new public read route, `GET /requirements`.** No
LLM and no pipeline stage: the Space counts a sample of served postings at request time.

- **The sample.** `q=` (a role, as in search) and/or `family=` (a role family) pick the postings,
  narrowed by every search filter and `board=`. With `q`, the sample is the `n` postings closest
  to it (ANN search, as `/search` ranks). With `family` alone, it is the family's `n` newest to
  HeadStart, from one filtered scan of `id` and `first_seen` whose length is also the family's
  count. With both, it is the family's postings among the 2,000 closest to `q` (the window a sorted
  search re-orders). `n` defaults to 300, from 50 to 500; the tool always asks for 300. At 300, a
  share near 50% is known to about ±6 points (95%), which is what a "most asked for" list needs.
- **Descriptions are read by id for the sample alone** (one `id IN (…)` read, as `/job` reads),
  never as a column scan, and never leave the Space. The answer carries counts, the vocabulary's
  own skill names, and the company names search already shows, quoted.
- **The counts** (`serving/requirement_counts.py`): how many postings the filters and family admit
  (a query ranks but does not narrow, so this is not the query's size); the skills the descriptions
  mention, as a share of the sampled postings that carry a description, with how many distinct
  employers mention each; minimum years in bands (0–1, 2–4, 5–7, 8+) kept apart by source (stated
  by the posting, estimated from the title's seniority, not stated); salary quartiles per currency
  over each stated range's midpoint, a year; the remote share; the companies with the most sampled
  postings; the countries their locations name (ADR-0273); and, with a family lookup, each sampled
  posting's category (the tool names none that ADR-0306 hides: those count with "other or no
  tech category"). The query path also returns the sample's similarity range.
- **The answer states its sample**: "counted over 300 postings, of 514,163 that the filters
  admit", how they were picked, and the similarity range.
- **Skills come from a curated vocabulary**, `config/tech_skills.json`: 376 skills in 17 kinds
  (languages, web, backend, mobile, cloud, DevOps, data, databases, AI/ML, security, networking,
  IT, enterprise platforms, testing, embedded, practices, clearance), each with its terms.
  `serving/tech_skills.py` matches them:
  - A description is cut into tokens: a word with dots joining it to more letters (`Node.js`,
    `ASP.NET`, `.NET`) and trailing `+` or `#` (`C++`, `C#`, `Security+`), with `/`, `-` and `&`
    tokens of their own. A term matches whole tokens only, so "trust" is not Rust, "HTML" is not
    ML, and "JavaScript" is not Java.
  - At each place the longest term wins: "React Native" is not also React.
  - A term may be `cased` ("Go", "Spark", "React"); `listed`, counted only beside another counted
    skill in a list, optionally of named kinds ("Python, R and SQL" names R; "SAP/Oracle" does not
    name the Oracle database); or carry `not_before` and `not_after` text ("Spark Capital",
    "West Java").
  - In a description written in Title Case, a cased capitalised word counts only in a list.
  - A skill whose term is in the employer's company name is not counted for its postings.
- **The vocabulary ships as package data** the way the role families do (ADR-0274):
  `pyproject.toml` force-includes it beside `serving/tech_skills.py`, which reads that copy first
  and otherwise `config/` in the nearest ancestor (the checkout, or `/app` in the Space's image).
  `.gitignore` re-includes it.
- **Cached for the boot**, 64 answers: the table does not change until the next boot.
- **A new agent contract, 6**: `/requirements` is new contract, so the app's `_AGENT_API_VERSION`
  and the server's `AGENT_API` rise together (ADR-0253's rule). The route is in `_READ_ROUTES`,
  so it is public and rate-limited like the others.

## Measurement

**Precision, by reading.** The corpus was 5,000 served descriptions, 200 from each of the 25 role
families, drawn with a fixed seed from a local copy of the served table (514,163 rows, 2026-09-29)
joined to the role assignments of the 04:04 tick. About 1,435 (posting, skill) hits were read and
labelled by hand, in all 25 families: 6–12 hits for each of about 90 risky skills (a language named
like an English word, a symbol in a name, an acronym with other meanings), 3 hits for each of about
100 broader or product-named skills, re-reads after every fix, and two uniform samples of all hits
(80, then 200 on the shipped vocabulary). A hit was judged correct only when the term names that
technology *and* is about the job's work, stack or requirements; a hit in an investor, customer or
partner list, a perk, or the hiring process was judged wrong.

- **On the shipped vocabulary, a uniform sample of 200 hits was 196 correct (98.0%).** The four
  misses were two employers describing themselves, "Message Queuing" inside MQTT's name, and a
  GitHub coding task in the hiring process.
- **Terms that fell below 90% were constrained or dropped:**

| Skill | What it also matched | Before | Change | After |
|---|---|---|---|---|
| SAS | the SAS storage interface ("SAS/SATA") | 6/8 | bare "SAS" only in a list of languages or data tools; "SAS programming", "SAS Viya" and other phrases | 8/8 |
| Express | "PCI Express" | 4/6 | only in a list of backend, web, language or database skills; not after "PCI" | 6/6 |
| Spark | "Spark Capital" | 5/6 | not before "Capital" | — |
| Master data management | "MDM", mostly mobile device management | 2/6 | "MDM" dropped | — |
| Oracle Database | bare "Oracle" as an ERP or a vendor | 4/6 | bare "Oracle" only in a list of databases or data tools | 10/10 |
| OpenAI APIs | investor and customer lists | 3/6 | API phrases; bare "OpenAI" only in a list of AI skills | 6/6 |
| Security operations (SOC) | system-on-chip, "SOC 3" reports | 4/6 | phrases only ("SOC analyst", "security operations center") | 6/6 |
| PCI DSS | the PCI bus, "PCI-Express" | 5/6 | compliance phrases; bare "PCI" only in a list of security skills | 10/10 |
| Digital forensics | forensic science | 5/6 | qualified phrases only | — |
| 5G and LTE | employers' boilerplate | 3/6 | only in a list of networking or embedded skills | 5/5 |
| ARM | Azure Resource Manager templates, NetSuite ARM | 1/6 | qualified phrases ("ARM Cortex"), bare "ARM" only in an embedded list; "ARM templates" and Bicep count as infrastructure as code | 6/6 |
| PLC | the "plc" company suffix | 5/6 | cased | — |
| Signal processing | "DSP/SSP" (a demand-side platform) | 5/6 | "DSP" cased, not before "/SSP" | — |
| Unity | Databricks Unity Catalog, Cisco Unity | 1/6 | not before "Catalog", "Connection", "Express", not after "Cisco"; "Unity Catalog" counts as Databricks | 3/3 |
| Data structures and algorithms | "data structures" in the data-modelling sense | 1/6 | the paired phrase only | 6/6 |
| Flutter | Flutter Entertainment | 1/3 | not before "Entertainment" | 9/10 |
| iOS | Cisco IOS | 9/10 | cased; "IOS" not after "Cisco" | — |
| Swift, and every capitalised word | a description in Title Case | 1 miss of 80 | the Title Case rule | 12/12 |

- **Kept at the bar:** Snowflake 29 of 32 (91%: customer lists). **Kept below it on purpose:**
  "computer vision" was 4 of 10 by the strict rule, 6 of the 10 being one employer's boilerplate
  repeated across its postings; the term itself was right every time. No vocabulary can tell an
  employer's self-description from a requirement, so the answer gives each skill's distinct
  employers beside its share: a large share from few employers reads as repetition, not demand.

**Latency, on the same local copy** (lancedb 0.33 on a laptop; the Space runs 0.36 on 2 vCPU), the
first call of each: "data engineer" 1.0 s; the security category 0.5 s; "frontend developer",
remote, 0.4 s; "data engineer" in data engineering 0.6 s; software engineering, the largest
category, 0.4 s; a sample of 500, 0.5 s; "machine learning engineer" in Germany 3.9 s, where the
country's place pattern (15,335 characters of where-clause) prefilters the vector search. Matching
costs about 1 ms a description. A repeat is answered from the cache.

## Options rejected

- **A skill-demand ledger per category, built by the pipeline** (the critique's first suggestion).
  A new stage, new state on HF (whose storage is the binding cost, ADR-0168), and an answer only per
  category, with no query and no filters. The vocabulary module could back one later.
- **Counting over every matching posting.** A category runs to 69,000 postings, about 350 MB of
  descriptions a request on a 2 vCPU Space. A sample of 300 answers the question to a few points.
- **The most frequent requirement phrases of N postings** (the critique's interim). Frequent phrases
  are scraped text, which would put the descriptions' own words into the answer, and they are
  dominated by "strong communication skills".
- **A description keyword per skill through `search_jobs`.** 20–45 s a skill, substring matching,
  and one skill a call.
- **An LLM reading the sample.** The server answers only from what the Space serves, the router is
  private, and a count from a model cannot be checked the way a named term can.
- **The newest postings for a query.** A query ranks but does not narrow, so "the newest matching
  postings" would be the newest in the index. The closest ones are what the question means.

## Risks, stated plainly

- **A mention is not a requirement.** A skill named in "nice to have", in the team's stack or in an
  employer's description of itself counts the same. The answer says "mention" and gives employers.
- **The list is closed.** A skill not in `config/tech_skills.json` is invisible, so the answer is
  the listed skills in demand, not every skill. A new skill enters by the same test: read a sample
  of its hits first.
- **The sample is the postings most like the query**, and a few employers can hold much of it
  (Booz Allen held 19 of the 300 closest to "data engineer"). The answer lists the companies.
- **Descriptions are English-only and not all stored.** 496,365 of 498,460 served rows carried one
  on 2026-09-29; the shares are of the sampled postings that do.
- **A category alone scans two columns of every admitted row** (0.4–0.6 s locally), once per boot
  per request shape.

## Consequences

- The tool is documented in `docs/agents/space-mcp-server.md` §"The tools". The evaluation has three
  iteration tasks for it (t24 to t26); the sealed held-out tasks are untouched.
- Tests: `tests/test_serving_tech_skills.py` (the hard terms and every matching rule),
  `tests/test_serving_requirement_counts.py` (the counts), `tests/test_serving_job_search.py` (the
  three sampling paths on a real LanceDB table, the refusals and the cache),
  `tests/test_space_app.py` (the route, the public-path set, the contract version),
  `tests/test_space_mcp_server.py` and `tests/test_space_mcp_against_space_app.py` (the tool,
  against a fake Space and the real app).
