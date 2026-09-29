# The Space MCP server — search, trends and hiring now for an agent

An MCP server that lets an agent read HeadStart the way the website does: find open tech jobs,
read a posting in full, see how the number of openings is changing, see which companies are
hiring hardest this week, and see what a role's postings ask for.
The Space hosts it at a URL anyone can add to Claude, and it also runs on your own machine as a
subprocess of your agent client. Either way it answers from the deployed Space's own read routes,
so every number is the one the website shows. It calls only postings opened and closed hiring,
though: the website's Trends table still calls a category's change in openings "Hiring", re-counting
included (ADR-0272, ADR-0321). The decision and its alternatives are ADR-0253,
ADR-0258, ADR-0267, `docs/mcp/2026-09-28_space-mcp-server-plan.md` and
`docs/mcp/2026-09-28_hosted-mcp-endpoint-options.md`; this file is the how-to.

It is **read-only**. It cannot save, follow, hide or subscribe to anything, and no account applies
to it — so a company you hid on the website is **not** hidden from an agent's search.

## Use it without installing

The Space serves the same tools over Streamable HTTP (ADR-0267). The connector URL is:

```text
https://imposeidon-headstart-search.hf.space/mcp
```

It needs no account, token or sign-in.

- **claude.ai** (web, desktop and mobile apps): **Customize → Connectors → Add custom connector**,
  name it HeadStart, paste the URL, and choose no sign-in. A Free plan may add one custom
  connector. A connector added on the web is also in the mobile and desktop apps.
- **Claude Code**:

  ```bash
  claude mcp add --transport http headstart https://imposeidon-headstart-search.hf.space/mcp
  ```

- **Any other client** that speaks Streamable HTTP, in the 2025-11-25 handshake or the 2026-07-28
  stateless revision. To check it without a client:

  ```bash
  npx @modelcontextprotocol/inspector@2.8.0 --cli \
    https://imposeidon-headstart-search.hf.space/mcp --transport http --method tools/list
  ```

**Limits** (ADR-0267, ADR-0276, ADR-0325).

- **How often.** 30 requests a minute from one address, and 300 a minute shared by everyone arriving
  from Anthropic's published range (`160.79.104.0/21`, which is every claude.ai user). Past it the
  answer is a 429.
- **How many at once.** At most 4 requests are answered at once across all callers, and at most 2
  of them from one caller. Anthropic's range counts as one caller here too, because nothing in a
  claude.ai request identifies the person. A request waits up to 10 s for a place. If its caller
  already holds 2, it then gets a 429. If every place is held, it gets a 503.
- **One description search at a time.** A `search_jobs` call with `keyword_in` set to
  `description` or `both` reads descriptions. Before ADR-0320 it scanned every description the
  filters left and took 16–18 s alone, about twice that beside another (measured 2026-09-29), so
  it runs on a place of its own,
  one for all callers. It never takes one of the 4 places above, so fast calls never wait behind
  it. When another scan is running, it waits up to 10 s and then gets a 503 asking it to retry in
  about 20 s, or to match the keyword in titles instead. The Space finds a description keyword's
  rows once and keeps them (ADR-0320): the page, its total and the next page share one finding,
  so a repeat or a second page reads no description. A first search looks for the keyword's
  literal first and reads the exact word rule only on those rows. On the hosted Space (14 calls,
  one each, 2026-09-29, ADR-0320) a repeat or a next page took 0.7–0.9 s where the one page 2
  measured before had taken 12.8 s, and a first search 1.2–21.9 s, none near the 45 s deadline.
- **How refusals look.** Every refusal is a JSON-RPC error carrying the request's `id`, with the
  HTTP status as its `code` and a sentence as its `message`, plus `Retry-After`. Claude Code shows
  it to the model as `Streamable HTTP error: Error POSTing to endpoint: {…}` and does not retry.
- **How long.** A tool call gets 45 s. Past that the call answers "HeadStart did not answer within
  this call's 45 s…" and asks for narrower filters or the concise detail: a description keyword
  with `detail: "full"` is the likeliest to meet it. A `search_jobs` call with a description
  keyword says instead that reading descriptions is the slow part, that the Space keeps what the
  read finds so the same call in a minute or two is usually quick, or to look in titles or add a
  company (ADR-0320). The work it started runs on to its end, so
  while two such reads are still running, a new call is told HeadStart is still finishing earlier
  searches. Claude Code and the MCP Inspector give up on any request at 60 s (measured
  2026-09-29), which is why the deadline sits under it.
- **Other sites.** A request from a web page on any other site is refused with a 403. That means an
  `Origin` other than claude.ai, claude.com or the Space's own.
- **Size, boot and sleep.** Answers are capped as the local server's are. A pipeline restart or a
  deploy does not interrupt the URL, because the old boot answers until the new one is up
  (measured 2026-09-28, ADR-0267). While the Space wakes from sleep, the URL answers with Hugging
  Face's own error instead of a sentence, so ask again in a few minutes.
- **Hugging Face's edge fails some calls.** About one hosted call in seven came back as Hugging
  Face's HTML error page (HTTP 502; the page itself says 500) on 2026-09-29, and an unrelated
  Space failed the same way, so the request never reached HeadStart. No MCP client retries a
  failed POST. The server's instructions tell the model that the page is a passing fault and that
  every tool only reads, so it may retry the same call up to twice (ADR-0325). The
  [installed server](#install-it) does not have this problem: it reaches the Space over HTTPS
  itself and retries an edge reply for up to the call's 45 s, so the model sees one only when the
  edge fails that whole time. Use it where you can run a command.

## Install it

Anyone can run it: the Space's read routes are public (ADR-0258), so it needs no
account, token or key.

### One command, no clone

With [uv](https://docs.astral.sh/uv/) installed, add it to Claude Code:

```bash
claude mcp add headstart-space --scope user -- \
  uvx --from https://github.com/sarthakjain004/headstart/archive/refs/heads/main.tar.gz headstart-space-mcp
```

`uvx` downloads GitHub's archive of `main` and builds the base package from it into a cached
environment of its own — two dependencies, `curl_cffi` and `requests`, no torch and no index —
and runs its `headstart-space-mcp` command. Any MCP client that runs stdio servers can use the
same command. In Claude Desktop, add it to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "headstart-space": {
      "command": "uvx",
      "args": [
        "--from",
        "https://github.com/sarthakjain004/headstart/archive/refs/heads/main.tar.gz",
        "headstart-space-mcp"
      ]
    }
  }
}
```

**Updates arrive on their own.** Every start asks GitHub whether the archive changed (uv
revalidates it against its `ETag`); when `main` has moved, uv downloads the new archive and
rebuilds before the server starts, so no `--refresh` is needed. To pin a version instead, name a
fixed archive: `https://github.com/sarthakjain004/headstart/archive/refs/tags/<tag>.tar.gz` or
`https://github.com/sarthakjain004/headstart/archive/<commit-sha>.tar.gz`. An archive URL takes
no `@<branch>` suffix; another branch is `archive/refs/heads/<branch>.tar.gz`.

**The first run downloads about 13 MB; later runs start in 1–2 s.** The archive holds only the
current tree, not the repository's history (about 106 MB, which a `git+https://…` install
clones). On 2026-09-28 a cold install took 4 s from the archive against 11 s from the `git+` URL
on a ~4.6 MB/s link, and 42 s against 207 s on a ~250 KB/s link. A start after `main` moves pays
the archive download again. Run the command once in a terminal first, so your client's first
connection is not left waiting on the download (it exits when its input ends):

```bash
uvx --from https://github.com/sarthakjain004/headstart/archive/refs/heads/main.tar.gz headstart-space-mcp < /dev/null
```

The wheel carries the list of role families (`pyproject.toml` force-includes
`config/role_families.json`, ADR-0274), so installed this way the tools' `category` lists every
family, as it does from a checkout and on the hosted server.

### From a checkout

1. **Install** the base package from a checkout, with Python 3.12 or newer:

   ```bash
   git clone https://github.com/sarthakjain004/headstart && cd headstart
   python -m venv .venv && .venv/bin/pip install -e .
   ```

2. **Add the server** to Claude Code:

   ```bash
   claude mcp add headstart-space --scope user --transport stdio \
     -- "$PWD/.venv/bin/python" -m headstart.space_mcp
   ```

   Use the checkout's own interpreter by absolute path, because a user-scoped server starts from
   every project's directory.

### Check it

`/mcp` in Claude Code lists `headstart-space` and its tools. Without a client, save the Claude
Desktop snippet above as `headstart-space.json` and list the tools through the Inspector:

```bash
npx @modelcontextprotocol/inspector@2.8.0 --cli \
  --config headstart-space.json --server headstart-space --method tools/list
```

Use the config file, not a command after `--cli`: Inspector 2.8.0 answers `Connection closed`
when that command carries `--from` (measured 2026-09-28, with the archive and the `git+` URL
alike), while the same server lists its three tools from the config.

`HEADSTART_SPACE_URL` points it at another deployment; the default is
`https://imposeidon-headstart-search.hf.space`.

## The tools

**`search_jobs`** — open tech jobs. Put **only the role** in `query` ("backend engineer at a
climate startup"); years, pay, place, company, employment type and dates each have their own
field. Omit `query` to list the newest jobs that match the filters. The answer gives the total,
one page of jobs with their links and ids, how the page is ordered, and — when nothing matches —
the filter costing the most.

- `company` is the site's company box: any company name *containing* the text. A Board key from an
  earlier answer (`lever:razorpay`, or the start of a result id) means every Board of that company.
  When no company name contains the text ("Strpie"), the answer offers up to five directory
  companies it may mean, with their keys.
- `category` narrows to one job category: across the whole index on its own ("ML jobs in
  Germany" is `category` plus `country: DE`), or within one company's jobs beside `company`, which
  then needs a directory company — a key, or an exact name, read as the directory's largest
  company of that name, and the answer says so. The schema lists the current ids; a label ("AI, ML
  & Data Science"), a close name ("AI/ML", "machine learning") or a retired id
  (`python-development`) is read as the id, and a name that fits several categories or none is
  refused with the ids and labels to choose from (ADR-0274). A category is the pipeline's role
  assignment, the one Trends counts, so its totals are exact; the Space reads a whole-index
  category from an in-memory copy of that family's rows (ADR-0322). `read_trends` reads `category`
  the same way.
- A salary bound needs `salary_currency` (30 lakh is `salary_min: 3000000`, `salary_currency: INR`).
- **Place, three ways.** `country` is an ISO 3166-1 alpha-2 code (`US`, `GB`, `DE`, `IN`; the
  schema lists the 95 it knows). It matches every way a job's location names the country — its
  name, its states or provinces, its cities, its codes — so "Austin, TX" is in `US` and "München"
  in `DE`; `IN` is the same rule as `india_place: "india"`. `india_place` narrows to an Indian city
  or region. `location` is plain text the location contains, for a city outside India or a place
  the gazetteer does not know. A location naming several countries is in each of them. How it
  matches, its measured precision and recall, and its costs: ADR-0273. `country` also takes a
  country's English name or a common abbreviation ("UK", "USA", "UAE"), read as the code
  (ADR-0322).
- **With a `query`, `sort` orders only the 2,000 closest matches.** For the highest salary or the
  newest anywhere, omit `query` and narrow with `keyword` and the filters.
- `detail: "full"` adds how many jobs each filter option would give, each option written as the
  argument that selects it (`max_years=0: 2,334`, `first_seen_within_hours=24: 374`). A concise
  answer asks the Space for the total alone (`/facets?counts=total`, ADR-0274), since every option's
  count re-scans the matches: under a description keyword the full strip took 98.7 s against
  10.6 s for the page itself.
- **What a row says.** Each row gives the posting's age ("posted 2026-09-24 (5 days ago)") and
  flags one over a year old; its employment type as the employer wrote it, beside the
  `employment_type` values it counts as (`type "FULL_TIME" (full-time)`); and every scraped field,
  the id included, quoted.
- **Copies of one posting are listed once** (ADR-0323). A row repeating one above it on the page
  is listed under it as `also #N`, giving only what differs; every id and link stays, and paging is
  the Space's. A copy is the same company and title, brackets aside (one posting copied per
  country; rows naming no company only on one Board), or the same title, first city and countries
  under another spelling of the company, as one posting on two of its Boards: Eversource's Radancy
  front says "EVERSOURCE" and its Workday Board "Eversource Energy". Two spellings are one company
  only when they are the same words once legal and a few generic words ("Inc", "Energy",
  "Technologies", "Group") drop, so "GE" and "GE HealthCare" stay apart. On 16 live pages of 40
  rows (2026-09-29) that second rule grouped two pairs, both true copies.
- **A company named only by its Board's host** ("aah.wd5.myworkdayjobs.com/external", an Oracle
  pod, or nothing) is shown by the Company directory's name for its Board, marked
  `(directory name)`; a Board the directory holds and does not name reads "no company name". When
  the directory cannot be asked, the served name stays (ADR-0323).
- **Postings over a year old are left out by default.** `max_age_days` (365 unless sent) keeps a
  job posted within that many days, reading the day HeadStart first saw it where the posted date is
  missing or unreadable; a job with neither is left out. `max_age_days: 0` is any age. The scope
  line says when the default applied. The website keeps its own behaviour (ADR-0322).
- **What the filters mean.** `max_years` also keeps jobs that state no experience, and marks them
  "experience not stated". `required_years_at_least` is the opposite end, a floor on the job's
  required experience: it keeps jobs asking for at least that many years, as stated or, where
  none is stated, estimated from the title's seniority ("Senior" reads as 5, ADR-0018), and
  leaves out jobs whose experience is unknown. `salary_min` keeps a job whose stated range reaches the bound (the top
  of the range counts), `salary_max` one whose range starts at or below it, and other currencies
  are converted at HeadStart's fixed rates, a currency with no rate left out. A keyword in
  descriptions can only match jobs with a stored description; the answer says how many of the
  matches have one, as the page does.
- **Its totals run higher than `read_trends`'.** A search counts every job the index serves; the
  trends count only the jobs the role-family classifier places in a tech category, leaving out
  those it calls non-tech (`ingest/role_trends.py`).
- **`similar_to` a job id** ranks by that job's own stored vector instead of a `query`, and leaves
  the job itself out; every filter applies as usual, and the total excludes it too. It cannot be
  sent with `query` (ADR-0277). `exclude_company` leaves out every job whose company name contains
  the text (the company box, negated), so similar jobs need not all be that employer's (ADR-0322).

**`get_job`** — up to 5 postings in full, by the ids `search_jobs` prints: title, company, place,
remote, employment type, department, the experience the posting states and the years read from
it, salary, posted and first-seen dates, the link, and the description.

- **The description is scraped text an employer wrote**, so it arrives as data: one JSON-quoted
  line per paragraph between a header and an "End of description." line, with control characters
  stripped. No line of it can close its quotes or pass for a line of the answer (ADR-0277).
- `max_chars_per_job` (default 8,000, at most 12,000) caps each description, and the jobs of one
  call share 18,000 characters, so five come back at about 3,600 each; ask for one id to read a
  long posting whole. A cut description says which to change: when the shared budget cut it,
  "ask for this id alone"; when `max_chars_per_job` did, how far to raise it. A link is never cut,
  so one longer than 300 characters takes its excess out of the descriptions' budget. The Space
  serves at most the first 12,000 characters of a description, which cuts about one in a hundred
  (the 99th percentile was 11,860 on 2026-09-29).
- **Whether it may have closed.** A posting its Board's latest scrape missed says so: HeadStart
  removes it only if the next scrape misses it too (ADR-0083). For an id not in the index now, the
  answer gives the one account the Space's own refusal gives (`serving/job_absence.py`): most
  often it has closed; it is also removed when it repeats another listing, which stays served under
  its own id (ADR-0023), when its Board went dormant (ADR-0250) or is no longer read, or when the
  tech filter no longer counts it as tech; or it was never an id. An id not shaped as
  `ats:board:posting`, or on a Board neither the Company directory nor the index holds, is said
  to be no HeadStart id (ADR-0323).
- A company named only by its Board's host is shown by its directory name, as in `search_jobs`.

**`read_trends`** — how tech hiring changed over a window. **Hiring is postings opened and closed,
and their net; the change in openings listed is not hiring** (ADR-0272). Whole index by default;
or one `category`; or up to 10 `companies` (keys or exact names). `breakdown` lists the lines by
category, seniority `level`, watched `role` or `company`.

- **Hiring comes first.** An answer leads with "Hiring, as postings opened and closed: O opened,
  C closed, net N".
- **Then the change in openings listed, split three ways.** An answer then gives "Openings listed:
  A → B" and splits that change into three parts:
  - what turnover accounts for;
  - what the counting changes HeadStart sized account for;
  - **the unsized rest**, which is not a hiring figure. It holds re-counting (Boards found or
    dropped, duplicates removed, counting changes HeadStart did not size) and any hiring before
    turnover began.

  The whole index sizes its counting changes (ADR-0270) and, from the first per-Board count on
  2026-09-13, its Boards found (ADR-0304), but not its duplicate removals. Before ADR-0304, on
  2026-09-29, the 30-day window listed +111,929 openings, counting changes were sized at −22,693,
  postings opened and closed netted −923, and the unsized rest was +135,545.
- **A window turnover covers only part of leads with one plain sentence** (ADR-0321). Turnover
  began on 2026-09-25 18:16, so every view whose window starts earlier, a company breakdown
  included, first says "HeadStart can measure hiring, as postings opened and closed, only from
  2026-09-25 18:16: 3.4 of this window's 30.0 days. Over the whole window it cannot say whether
  hiring rose or fell". A window that ends before turnover began says it has no hiring figure at
  all.
- **Turnover's other gaps are said next, before any figure.** It leaves out the runs inside its
  span where a counting change landed. Some closures go uncounted, so closed can run low; the
  answer gives the Boards as "N of M". On 2026-09-29 all 12,407 of the index's Boards had such a
  run, so the answer says closed runs low.
- **Each counting change is named once**, numbered, by short tags ("[4] category list + duplicate
  check + category sorting"), and figures refer to it by number. One line glosses each tag once,
  and "growth rescaled by [n]": where taking change [n] out would have left a line below zero,
  HeadStart scaled the line's earlier growth down instead.
- **A retired category names its successor.** A window from before the category list changed on
  2026-09-25 reads the old categories: "Security Engineering (retired; now Security)". Its jobs
  were re-sorted, so it does not line up with the successor's figures in a later window.
- **The closing line is an arithmetic check.** It says each line's parts add up to its change.
  That checks sums, not that any figure is hiring.
- **The site's own "hiring" figure appears only with `detail: full`**, labelled as including the
  unsized change.
- **The window.** It is `days` back from now, or `since`/`until` dates; a date after today is
  refused. The answer says when the history starts later than asked. A company's counts begin
  2026-09-13, when per-Board counting began.
- **Coverage.** `coverage: comparable` counts only the Boards tracked at the window's start, as
  the site's toggle does. The default `all` is what the site lists.
- **Measure.** `measure: new` reads postings first seen in the trailing 7 days, the site's New
  this week. It is not a count of postings opened.
- **A role breakdown** is the watched roles within a category, not the category. The answer also
  gives the category's own figures, and says when a category has no watched roles.

**`hiring_now`** — companies ranked over the trailing week on one Lens. The default,
`opened_less_closed`, ranks postings opened less postings closed, only for companies whose
closures were counted on every Board, so its figure holds no re-counting (ADR-0321). The site's
other Lenses are `expansion` (the site's net change, less the counting steps it could size),
`volume` (postings opened) and `rate` (postings opened as a share of openings). Every row gives
the site's numbers and opened less closed.

- **The site's Lenses apply every check that questions what they rank by.** On `expansion`,
  `volume` and `rate` a row is flagged when:
  - its net is more than its postings opened and closed could make, sign by sign, even at their
    pace over the whole week. Such a net is mostly re-counting, so report the row's opened and
    closed;
  - its closures were not counted, on all or only some of its Boards;
  - it opened more postings than are open now;
  - on `rate`, it has under 50 openings (a small base).

  `opened_less_closed` flags nothing, because none of these questions its figure, and it keeps its
  own order.
- **Flagged rows go last on the site's Lenses.** Every row `/hot` serves on `expansion`, `volume`
  or `rate` is listed unflagged first, each group in the site's order, and then cut to `limit`.
  Each row gives its place on the page ("site #7"). On 2026-09-29 the page's Expansion list began
  with Bosch Group, +442 on 23 postings opened and 33 closed; the answer began with Capital One,
  site #7.
- **Operators.** `operator` is who posts the jobs:
  - `employer`: the company itself, and any company not on the curated list;
  - `services`: an IT services firm posting client work;
  - `staffing`: a staffing agency;
  - `aggregator`: a job board.

  Staffing firms and job boards are left out unless you ask for them, as on the site.
- **Keys.** Each row carries a key the other two tools accept.

**`find_company`** — look a company up by `name`, typos, prefixes and known aliases allowed. It
lists every Company directory entry the name may mean, best match first, each with its key, its
Boards, its tech openings and how it matched (exact name, alias, prefix, every word, one typo, or
spaces ignored). It picks none of them: one employer can be several entries (one per ATS, or a
regional arm), and anything but an exact name or alias is a guess to confirm with the user. As in
the site's picker, of the entries sharing one name only the largest is listed; a smaller one is
reached by its Board key, which `find_company` also looks up exactly.

**`company_profile`** — one company's hiring, for a recruiter or a researcher. `company` is a key
from `find_company` or an exact name, read as `read_trends` reads one; the answer names the
company, its Boards and any other directory company the name may mean. It gives:

- its tech openings now, and the jobs its Boards serve that the tech filter sets aside;
- the last 30 days: postings **opened and closed** first (counted since 2026-09-25, when turnover
  counting began), then the change in openings with its re-counting part named;
- its job categories now, largest first, each with the postings opened and closed in it;
- where its served jobs are, by country, up to eight countries, each with its three commonest
  cities: "Dublin" and "Dublin, Ireland" are one Dublin in Ireland. A place is read as
  `search_jobs`' `country` reads it, so a country's figure is what that filter would count; a job
  naming two countries counts in both, and the jobs whose place names no country ("N/A",
  "Remote") are counted apart (`/companies/locations`, ADR-0275, ADR-0323);
- its levels, in the Trends Level view's bands (internships, 0–1, 2–4, 5–7 and 8+ years, not
  stated), each job counted once (`/companies/levels`, ADR-0323);
- how many of its served jobs are remote, of each employment type, state a salary, were posted in
  the last day, week, month or quarter, and are new to HeadStart this day or week. A line whose
  every count is 0 is left out, a one-count line such as "remote" too: a Board that states no
  employment type would otherwise read as hiring no full-time staff.

To list the jobs behind any of these, pass the key to `search_jobs` as `company`.

**`role_requirements`** — what postings for a role or a job category ask for, counted over a sample
of them, for a career switcher's "what does a data engineer typically need" (ADR-0324). It reads
`/requirements`, one route, and returns counts only.

- **The sample.** `query` is the role, as in `search_jobs`; the sample is the 300 postings closest
  to it among those the filters admit. `category` alone samples the category's 300 newest postings
  across the whole index; with `query` too, the closest within the category among the 2,000
  closest to the query. The answer's first line says which, over how many, of how many: "counted
  over 300 postings, of 514,163 that the filters admit". A query does not narrow, so that total is
  every posting the filters admit; the answer gives the similarity range of the sample instead.
- **Filters.** `company` (a name matched as the company box matches, or a Board key), `country`,
  `india_place`, `location`, `remote` and `max_years`, each meaning what it means in
  `search_jobs`.
- **Skills.** The tech skills the sampled descriptions mention, from a fixed list of about 380
  (`config/tech_skills.json`, matched by `serving/tech_skills.py`), each as a share of the sampled
  postings that carry a description, with how many distinct employers mention it. A posting counts
  once per skill. A mention can be an employer describing itself ("committed to AI, computer vision
  and sensor fusion" in every posting of one company), which is why the employers figure is there:
  a large share from few employers is boilerplate, not demand. The skill an employer is named after
  (Salesforce at Salesforce) is not counted for its own postings.
- **The rest.** Minimum years in bands (0–1, 2–4, 5–7, 8+), kept apart by source: stated by the
  posting, estimated from the title's seniority, or not stated. Salary quartiles per currency, a
  year, over the middle of each stated range. The remote share, the companies with the most
  sampled postings (with a key), the countries their locations name (ADR-0273), and, for a query,
  the job categories of the sample.
- **No description text.** Descriptions are scraped and read only on the Space; the answer carries
  counts, the list's own skill names and quoted company names.
- **Cost.** The Space reads the sample's descriptions by id, never the whole column, and keeps each
  answer for the boot. Measured on a local copy of the served table (514,163 rows, 2026-09-29):
  0.4–1.0 s a sample of 300, 3.9 s under a `country` whose place pattern is long (Germany).

## What it cannot tell you, and why

- **No last-seen date.** The served table has no per-Job "last seen"; `get_job` says only whether
  its Board's latest scrape missed the posting (ADR-0277).
- **A description as stored, not as posted.** Most ATSes' descriptions are stored with their line
  breaks collapsed, so a posting often reads as one paragraph, and one that is longer than 12,000
  characters is cut there.
- **No per-category Hiring now.** Ranking every company within one category costs about 11 ms a
  company at the Space; `read_trends` with a `category` and named `companies` answers it for the
  companies you name.
- **A cold Space takes minutes.** The Space restarts after every pipeline run, but the old boot
  answers until the new one is up (measured 2026-09-28, ADR-0267). It also sleeps when idle, and
  waking it takes a boot, measured at 4 min 13 s on 2026-09-28. A call waits at most 45 s, then
  says the Space is starting — ask again in a few minutes.
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
   `max_chars`, the answer's size at the tool's largest input. An argument whose caller's words
   need reading before the schema check (a category's label) names its reader in
   `argument_readers`, as `category` does with `role_families.resolve`.
2. Add `<tool_name>.TOOL` to `REGISTRY` in `src/headstart/space_mcp/tools/__init__.py`. The server
   lists it, puts its `when_to_use` in its instructions, checks its arguments against its schema,
   fills its defaults, and cuts any answer past its `max_chars`.
3. A Space route no tool read before also needs: a `SpaceRoute` member; the route public on the
   Space (`_PUBLIC_PATHS` in `deploy/hf-space/app.py` — GET-only, Account-free routes only,
   ADR-0258); and, when it is new contract,
   the Space's agent contract version and this server's `AGENT_API` raised together, so an older
   Space is refused rather than half-understood.
4. `tests/test_space_mcp_tools.py` holds every registered tool to the rules above without being
   edited. Add what the tool does to `tests/test_space_mcp_server.py` (against a fake Space) and,
   where it depends on the real app's answer, `tests/test_space_mcp_against_space_app.py`.
5. Describe it here, and give the evaluation (`scripts/eval/`) a task for it.

The Space hosts the registry at `/mcp`, so merging a tool deploys the Space (ADR-0267).

A tool that **writes** or reads **one Account's records** is a decision, not an addition: every tool
today is read-only and Account-free, reading public routes with no credential, and the contract
tests pin both. It needs a credential design first (per-Account tokens are the deferred design),
since a public route can never carry one person's data.

## Where it lives

`src/headstart/space_mcp/` — `tools/` (one module per tool, and `REGISTRY`), `space_tool.py` (what a
tool is), `server.py` (serves the registry), `space_client.py` (the one way it reaches the Space),
`company_scope.py`, `shown_company.py`, `posting_copies.py`, `role_families.py` and
`scraped_text.py` — on the shared protocol module in `src/headstart/mcp_protocol/` (`messages.py`,
and the `stdio.py` and `streamable_http.py` transports). The hosted route is `/mcp` in
`deploy/hf-space/app.py`, and the three routes only the tools read are `/companies/locations` and
`/companies/levels`, answered by `src/headstart/serving/location_counts.py` and `level_counts.py`,
and `/requirements`, answered by `JobSearch.requirements` with `serving/requirement_counts.py` and
`serving/tech_skills.py`. Tests:
`tests/test_space_mcp_*.py` and `tests/test_mcp_protocol_*.py`.
