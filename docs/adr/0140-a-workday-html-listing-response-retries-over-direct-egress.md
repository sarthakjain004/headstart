# ADR-0140: A Workday HTML listing response retries once over direct egress

- **Status:** Accepted
- **Date:** 2026-09-12
- **Amends:** [ADR-0063](0063-spare-egress-for-a-spent-origin-budget.md) for this one response class

## Context

Five pipeline executions recorded 3,912 `JSONDecodeError` failures across 3,057 canonical Workday
Boards. The executed scraper called `raise_for_status()` and then `response.json()`, so the terminal
decode failures were 2xx responses whose bodies were not JSON. The logs discarded the status,
content type, final URL, body length, fingerprint and body, so they cannot establish what those
historical bodies were or which upstream component produced them.

The failed call was a listing page, not a Job detail. When it raised, `fetch_raw()` returned no Job
list for that Board; even Jobs accumulated before a later-page failure were discarded. There is
therefore no honest historical list of “exact affected Jobs.” The Board list is exact; current Job
identities from later probes describe later availability only.

Read-only verification from this machine tested all 3,057 affected Boards once. It found 3,056 JSON
responses and one Workday-branded HTTP 520 HTML page; the identical request then returned JSON ten
times. That reproduces a transient unexpected body, but not the historical terminal 2xx status.

GitHub-run-hosted runners then tested every affected Board over three routes (run `34686380092`):

| Route | First response was JSON | HTML challenge | Other raised response |
| --- | ---: | ---: | ---: |
| Direct | 3,057 / 3,057 | 0 | 0 |
| One fixed WARP exit | 2,399 / 3,057 | 658 | 0 |
| WARP, rotate after a classified challenge | 2,570 / 3,057 | 470 | 17 |

Rotation recovered 266 of the 470 challenged requests, leaving 204 challenges; including the 17
transport failures, the rotating path settled 2,836/3,057 successfully. Rotation helps a bad WARP
route, but direct egress was materially better in this sample.

Two more four-runner replays used the shared HTTP client's real retry and Workday 429-wall policy.
Starting direct (`34686623380`) and starting already Workday-walled (`34686794085`) each produced
3,047 JSON responses and ten structured HTTP failures. The walled run used 16 observed WARP IPs and
12 successful rotations. Neither produced a terminal decode failure, so the historical upstream
cause remains unresolved.

## Decision

An unexpected Workday listing body raises `UnexpectedListingResponse`, never an empty Board. Its
bounded message records classification, Workday instance, UTC observation time, status, content
type, query-stripped final URL, byte length, SHA-256, and a sanitized 320-byte body prefix. The
ordinary failed-Board path therefore preserves the evidence without a separate unbounded capture.

Only positively recognized transient bodies earn one extra fetch: a Cloudflare challenge, an
explicit maintenance page, or the measured Workday branded error-page shape. That fetch is made
over direct egress. This applies equally to the synchronous first page/slices and asynchronous
pagination. A second bad response still raises, and the existing first-page-versus-mid-crawl 404
contract remains intact.

Unknown malformed bodies are not retried. A JSON parser defect, permanent template change, or an
unrecognized response must stay visible rather than being multiplied across the origin. TLS
verification remains enabled. No unexpected response is converted to an empty Board or a gone
verdict, so it cannot quarantine the Board or authorize eviction.

## Rejected alternatives

- **Retry every JSON error.** This hides parser defects and permanent response changes, and the
  evidence only supports transient handling for recognized shapes.
- **Keep retrying or rotating WARP.** Rotation improved WARP but remained worse than direct across
  the complete affected population.
- **Treat non-JSON as an empty Board.** That turns an observation failure into false delisting
  evidence.
- **Disable TLS verification.** No evidence connects certificate validation to this incident.
- **Name the historical upstream cause.** The missing historical bodies make that unknowable; the
  new diagnostics exist so a recurrence can settle it.

## Consequences

The next recurrence will be attributable by response class, instance and time without retaining an
unbounded body. A recognized transient can recover once outside the WARP route shown to generate
challenges. Coverage summaries still report a Board as failed if that retry does not recover, and
the Board remains outside authoritative eviction scope.

The direct retry is a narrow exception to ADR-0063, not a global retreat from spare egress. Workday
429 handling and every other ATS keep their existing routing policy.
