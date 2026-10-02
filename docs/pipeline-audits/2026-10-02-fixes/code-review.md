# Independent Standards and Spec review

Initial review: `48ff7c14...40fa6d44`; follow-up: `48ff7c14...87dd6dca`. The branch was then
rebased onto `ab2d51ed` and the explicit-proxy correction reviewed at `844a392c`.
ADR numbers moved to 0374–0376 because upstream allocated 0373 while this work was in progress.

## Standards

Initial findings, both reproduced independently:

- **P2: browser shutdown could be skipped.** Unfinished-cost bookkeeping preceded the shutdown
  guard. Injecting disk-full during cancellation produced `shutdown_called=False`, contradicting
  the harvest-finally lifecycle contract. Fixed by enclosing all teardown in the shutdown finalizer;
  the new harvest regression exercises that path.
- **P2: an abandoned context released a new lifetime's semaphore.** An old yielded origin exited
  after shutdown/reopen and increased the new gate from 4 to 5. Fixed by capturing browser, loop,
  semaphore and generation; stale page requests are rejected and old cleanup never touches new
  resources. Threaded and real Chrome shutdown/reopen checks passed.

Follow-up finding:

- **P2: singular explicit `proxy=` bypassed pacing validation.** Curl supports both `proxy=` and
  `proxies=`, but the new managed-route guard initially rejected only the latter. Reproduction
  charged the direct clock while sending to an explicit proxy. Both spellings are now refused
  before sending in sync and async drivers; four regression cases and independent zero-send
  checks passed.

Final Standards outcome: **no unresolved actionable findings**. Fifteen dual-egress tests passed
in the independent verification. The adapter's existing private Pydoll cleanup access and necessary
sync/async driver differences were not reported as automatic code smells.

## Spec

Initial finding:

- **P2: existing browser contexts could enter a replacement lifetime.** With one active new tab,
  closing the old origin increased its gate from 3 to 4; old Page requests also used the new loop.
  This contradicted worker lifetime isolation while permitting reopen. The reviewer repeated the
  corrected case: the gate remains 3, and the old page raises `browser page belongs to a closed
  lifetime`.

Final Spec outcome: **no remaining actionable findings**. Recovery ordering and interruption
tests preserve positional correspondence; the actual workflow shell stops upload on invalid
recovery; optional Avature pacing, retry/collapse behavior and default-off wiring match the scope.
The reports distinguish local measurements from runner evidence and missing historical HTTP data
from confirmed flapping paths. Eighty-eight targeted tests passed in that independent review.

Total findings: Standards **3**, Spec **1**; worst severity on each axis **P2**.
All are resolved; the repeated browser finding is retained under each axis rather than merged.

Main subsequently allocated 0374 for public browsing. The docs-only conflict resolution preserves
that decision and renumbers this work to 0375–0377. Final source is rebased onto `96665e49`;
the reviewed store/browser/network implementation is unchanged.
