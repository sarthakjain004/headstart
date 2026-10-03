# Comeet employer overlap validation — 2026-10-03

Three candidate pairs have **partial overlap**, not proof that one whole Board is a
duplicate of the other. Employer-owned careers pages identify the current endorsed
route: Greenhouse for ScaleOps, Comeet for SPAR Solutions and BioCatch. A migration is
plausible, but neither its date nor retirement of every posting on the other Board was
established. No alias, parked Board, liveness verdict or served row was changed by this
check.

## Complete current posting sets

These are fresh reads of both public Boards, not served-index counts. All six reads
completed without truncation. “Shared titles” lowercases and collapses punctuation;
it is a candidate signal, not a shared requisition identity.

| Employer | Comeet Board | Other Board | Jobs, Comeet / other | Distinct titles, Comeet / other | Shared titles |
| --- | --- | --- | ---: | ---: | ---: |
| ScaleOps | `comeet:99.003` | `greenhouse:scaleops` | 53 / 57 | 53 / 52 | 19 |
| SPAR Solutions | `comeet:89.00a` | `bamboohr:sparsolutions` | 8 / 6 | 8 / 6 | 4 |
| BioCatch | `comeet:03.00e` | `lever:biocatch` | 27 / 13 | 25 / 13 | 3 |

Source reads: the [ScaleOps Comeet Board](https://www.comeet.com/jobs/scaleops/99.003),
[SPAR Comeet Board](https://www.comeet.com/jobs/sparsolutions/89.00a),
[BioCatch Comeet Board](https://www.comeet.com/jobs/biocatch/03.00e),
[ScaleOps public Greenhouse API](https://boards-api.greenhouse.io/v1/boards/scaleops/jobs?content=true),
[SPAR BambooHR Board](https://sparsolutions.bamboohr.com/careers) and its public listing/details,
and [BioCatch public Lever API](https://api.lever.co/v0/postings/biocatch?mode=json).
Descriptions were also compared across every pair, including different titles, to avoid
mistaking renaming for new work. Word-set Jaccard scores below summarize that comparison;
they do not implement a duplicate threshold.

## ScaleOps: current Greenhouse endorsement, partial overlap

The [employer careers page](https://scaleops.com/careers/) embeds `greenhouseJobs` with
board token `scaleops` and explicitly links its [Greenhouse Board](https://job-boards.eu.greenhouse.io/scaleops)
as the fallback. Its captured page contains no Comeet reference. The 19 shared normalized
titles leave 34 Comeet-only and 33 Greenhouse-only title strings; some are renamed roles.
For example, Comeet `4F.E63` “Director of AI at CEO Office” and Greenhouse `4941455101`
“VP AI” have 0.936 description similarity and compatible Israel/Tel Aviv locations.

[AI Engineer on Comeet](https://www.comeet.com/jobs/scaleops/99.003/ai-engineer/DF.362)
and [AI Engineer on Greenhouse](https://job-boards.eu.greenhouse.io/scaleops/jobs/4913574101)
are another likely overlap (0.696, Israel/Tel Aviv), but the systems expose different
native ids. Greenhouse also carries Bookkeeper and IT Manager, whereas Comeet has its
own unmatched titles. The evidence supports an apparent move toward Greenhouse, not a
complete posting equivalence or a proven closure of Comeet-only work.

## SPAR Solutions: current Comeet endorsement, substantial partial overlap

The [employer careers page](https://www.sparsolutions.com/career) has seven distinct
Comeet job links, including Technical Project Manager `30.94D` and Associate Project
Manager `12.C6B`; no BambooHR link appears in the captured page. The current ATS sets
still differ. Strong same-role evidence includes these native pairs:

| Comeet id | BambooHR id | Role | Description similarity | Location evidence |
| --- | ---: | --- | ---: | --- |
| `97.368` | `123` | Salesforce Business Analyst | 0.995 | Both Pune, India |
| `87.E68` | `127` | Senior AI Engineer | 0.998 | Both Pune, India |
| `12.C6B` | `125` | Associate Project Manager | 0.993 | Both Pune, India |
| `30.94D` | `113` | Technical Project Manager | 0.882 | BambooHR Pune; Comeet unstated |

The Associate Project Manager pair is missed by exact title normalization because
BambooHR prefixes the title with “Role:”. The same-title Salesforce Solutions Consultant
pair has only 0.306 similarity and different location specificity (United States / Remote).
BambooHR's “Salesforce Solutions Consultant - IND” is not present under that title on
Comeet; Comeet likewise has titles absent from BambooHR. Current endorsement favors
Comeet, but a whole-Board subset or migration-completion claim is unsupported.

## BioCatch: current Comeet endorsement, partial overlap across distinct geographies

The [employer careers page](https://www.biocatch.com/cybersecurity-careers) loads its
[first-party jobs JavaScript](https://www.biocatch.com/hubfs/hub_generated/template_assets/1/41194499331/1790016896075/template_mjtw_main.min.js).
The invoked jobs-listing function requests Comeet company `03.00e` positions. The captured
page and bundle contain no Lever route. The embed token was neither used nor copied into
this report; the comparison reads public hosted Comeet HTML.

Comeet `1A.C6C` and Lever `e7f8e860-c0ae-4ea4-ae98-5763fe9deb28`, Senior DevOps Infra
Engineer, have 0.955 description similarity and compatible Tel Aviv locations. Sales
Account Director Saudi Arabia (`B7.168` / `6d72bf8a-9c64-488a-9956-e5b9fa69e3ff`)
has 0.804 similarity and compatible Riyadh/Saudi Arabia locations. By contrast, the
[Comeet Senior Backend Developer](https://www.comeet.com/jobs/biocatch/03.00E/senior-backend-developer/EF.07B)
is in Israel and the [same-title Lever posting](https://jobs.lever.co/biocatch/00a05a98-4068-46ed-b218-cf9f69801f4d)
is in Spain (0.596 similarity). That pair must not be collapsed from its title.
Other high text similarities cross regions too: Benelux versus DACH, APAC versus NAM.
No whole-Board alias follows from reusable role descriptions.

## Reproduce and interpret

The ignored notebook `experiment/three-ats-overlap-validation/` retains `capture.py`,
`compare.py`, `compare_descriptions.py`, logs, employer HTML/JavaScript, current API bodies,
parsed jobs, native ids, locations, `artifacts/live-comparison.json` and
`artifacts/all-description-pairs.json` (3,021 ScaleOps, 48 SPAR and 351 BioCatch pairs).
Run with `PYTHONPATH=src`; the comparison
uses each registered Scraper's public fetch/parse seam. The coordinating census's native
evidence and frozen served comparison live separately in
`experiment/cross-provider-ats-overlap-2026-10-03/` in the primary checkout.

Prefer the employer-endorsed route when making a deliberate source-selection decision.
Parking the other route would be an endorsement-based coverage trade, not a proved alias:
its currently unmatched postings would disappear. Keeping both preserves those postings
but retains the measured partial duplicates. This report supplies the evidence for that
decision and does not silently make it.
