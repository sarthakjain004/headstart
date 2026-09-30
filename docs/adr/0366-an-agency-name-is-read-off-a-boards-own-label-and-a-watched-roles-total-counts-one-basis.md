# ADR-0366: An agency name is read off a Board's own label, and a watched-roles total counts one basis

**Status:** accepted · **Date:** 2026-09-30 · **Amends:**
[ADR-0335](0335-an-agent-leaves-out-staffing-firms-and-job-boards-and-an-unchecked-agency-name-is-flagged.md)
(which text its agency-name rule reads) and
[ADR-0233](0233-trends-serves-reconciled-line-readings-and-the-page-only-formats.md) (what the tracked-roles drill's first
row adds up) · agent contract 22 · no served-table schema change, no stored-data change

## Context

The fifth critique of the Space MCP server (2026-09-30, 8.1/10) found four gaps this ADR settles.

- **R5-P1-3.** `hiring_now` flagged Lockheed Martin "operator unverified" and listed it after every
  unflagged row. `board_operator.unverified` matched `_AGENCY_NAME`, whose words include `hr`,
  against the whole tenant `lockheed.jobs.hr.cloud.sap`, and `hr` is SAP's host label. Every
  Board on that host carried the flag.
- **R5-P1-4.** `read_trends` with `breakdown: role` on Software Engineering since 2026-09-25 gave
  the watched roles "added together: listed 22,691 → 34,735 (+12,044)". Java and Python were
  counted only for the window's last 4.8 of 5.5 days, so the start omitted them and the end held
  their whole stock. The Space's `/trends` reading computes that first row
  (`line_reading._Reader._first_row` sums the lines run by run), so the fault was the Space's, not
  only the tool's.
- **R5-P2-3.** `hiring_now` lists flagged rows after the unflagged ones and then cuts at `limit`.
  At `limit` 10 on Expansion the answer began at "site #17", and nothing said 16 flagged rows the
  site ranks above it were not shown.
- **R5-P2-9.** Task t32 failed 0 of 3 because its Eversource pair
  (`radancy:jobs.eversource.com:101283120016`, `workday:eversource/externalsite:R-031045`) had
  closed. `/job` listed both as `missing` on 2026-09-30. No task covered R5-P1-3 or R5-P1-4.

## Decision

**1. The agency-name rule reads a Board's own label.** `board_operator._own_label` gives:

- a slug whole;
- the labels before a vendor's host suffix (`jobs.hr.cloud.sap`, `zohorecruit.*`, `icims.com`,
  `oraclecloud.com`, `openings.co`, `taleo.net`, `eightfold.ai`, `jobs2web.com`,
  `myworkdayjobs.com`, `successfactors.com`/`.eu`);
- otherwise, for a company's own host, its registrable label. A country registry's second level
  (`co`, `com`, `gov`, `gc`, …) is skipped, as in `isuzu.co.jp` → `isuzu`.

The vendor list is every host suffix that at least 27 Scrapable Boards' tenants share on
2026-09-30, measured with `scrapable_boards.load` over all 51 ledgers. The company's name is
still read whole.

The registrable-label rule goes one step past what the critique asked for, and on purpose. It
clears `recruit.lg.com`, `recruiting.claas.com`, `talent.tii.ae` and
`recruitment-recrutement.nrc-cnrc.gc.ca`. Each of these is a real employer's own career
subdomain, and the old rule made the same false statement about each as it did about Lockheed.
`hr-path.com`, `jobs.hr-com.de` and `3m-consultancy.zohorecruit.com` stay flagged, because their
own label does say it.

Measured on 2026-09-30, comparing the old rule with the new:

- **The company directory** (40,449 companies, fresh from HF): 43 companies clear, 0 newly
  flagged, and 1,296 stay flagged. Every one of the 43 was read by name, and all are employers:
  Lockheed Martin, Bechtel, HEINEKEN, W. L. Gore, NetJets, LG, Southern Glazer's, National
  Research Council Canada, Technology Innovation Institute, Trianz, Home Credit and the rest.
- **Scrapable Boards** (176,865, each read with its directory name where one exists): 104 clear
  and 0 are newly flagged. Of those 104, 96 are SuccessFactors, 4 WP Job Openings and 4 Zwayam.
- **Live `/hot`**: Lockheed Martin (#5 on Expansion, #18 on Volume) was the only flagged row the
  change clears. Zorba Consulting India stays flagged.

`/hot` computes `operator_unverified` Space-side (`hot_ranking`), so the contract moves to 22.

**2. The tracked-roles first row adds up one set of roles.** In the tracked-roles drill (`family`
with `split_by: family`), the first row sums only the lines counted from the earliest run any line
is counted at. Its start and latest therefore count the same roles.

A breakdown needs no such rule. Its rows start at the first row's run, at 0 before they are
counted, and a counting change sizes whatever sorts into them (ADR-0270).

`read_trends` names the roles the total leaves out. These are the lines whose `span_days` is
shorter than the total's. The answer says "Java and Python are left out of that total: counted
only from partway through the window, their start is no like-for-like base".

The other ways to fix it:

- **Sum each role's own change** (−3,427 on p5e). This compares starts taken at different times,
  and the first row would stop being a line that can be drawn run by run.
- **Keep the mixed sum and state the joined part.** This leaves a total that is not a change, and
  the website's reading would still carry it.

No other `read_trends` total has parts carrying a "counted for its last" note while the total
itself lacks one: breakdown rows are aligned to the first row, and the Company breakdown has no
first row. The website does not draw this first row: its roles view prints a fixed sentence and
the "Openings in tracked roles" tile, which reads `openings`. So no page change follows.

**3. `hiring_now` counts the flagged rows its limit hid.** The count covers the flagged rows cut
by `limit` that the site ranks above a row shown. The answer adds: "N flagged rows the site ranks
above rows shown here are not shown (limit L); raise limit to see them."

**4. An eval task can require a live fact, and a task whose fact is gone is retired, not
failed.** A task's `requires` names the facts its premise rests on:

- `jobs`: every id is served by `/job`;
- `hot_row`: a company is in a Lens's first N rows shown on `/hot`;
- `roles_joined_partway`: some watched role joined the window partway.

`run_task` checks them before it starts `claude`. If one is gone, it records the verdict
`retired` and makes no run. `summary` names retired tasks on a line of their own that holds no
bar, and both `summary` and `tally` leave them out of the judged count. A fixture that cannot be
read is an `error` (unjudged), as a verifier's unread Space is.

t32 now requires its Eversource pair. There are two new tasks:

- **t40** (`employer_unflagged`): it fails a tool result line or an answer line that calls Lockheed
  Martin unverified, a staffing firm, an agency or a recruiter, and an answer that leaves Lockheed
  out. It requires Lockheed in Volume's first 20 rows.
- **t41** (`watched_roles_total`): it reads the Space's role lines itself. It passes an answer
  stating either like-for-like total (the roles counted from the start, or each role's own change
  summed) and fails one stating the mixed total. It requires a role that joined partway.

Each new verifier and each requirement kind has self-tests. t40 and t41 are not in the recorded
replay: until this change deploys, the live Space serves the old `/hot` flag and the old first
row, so recording now would pin the gap.

## Consequences

- An employer on a vendor's host, or on its own recruiting subdomain, is no longer called a
  possible agency by its host alone. An agency whose only agency-like word sits in a subdomain
  (`hr.nippon24.jp`) now passes unflagged unless its name says so. The rule was only ever a lead
  (ADR-0335), and the name is still read.
- The vendor list is hand-kept. A new vendor whose host has a label such as `hr` or `talent` will
  flag its tenants until it is added, and the measurement above is the way to find one.
- A watched-roles total now changes value when a role joins or leaves the watch list mid-window.
  It says so by naming the left-out roles, rather than folding their stock into the change.
- The eval can shrink silently as its data-dependent tasks retire. The summary names each retired
  task on its own line, so replacing them stays visible work.
