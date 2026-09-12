# Full src/headstart review — 2026-09-12

Reviewed **102 files / 33,167 added lines** at commit
`129ca66615a3ec62b81cd363a3eb314a056b4fb6`, against Git's empty tree.
The agreed current side was `HEAD`; uncommitted work is outside this review.
Code, documentation and tests were exported from the pinned commit into an isolated
snapshot. No source fixes, production changes, or external messages were made.

```bash
git diff 4b825dc642cb6eb9a060e54bf8d69288fbee4904 129ca66615a3ec62b81cd363a3eb314a056b4fb6 -- src/headstart
git log --oneline 129ca66615a3ec62b81cd363a3eb314a056b4fb6 -- src/headstart
```

Both objects were validated and the diff was nonempty. An empty tree has no commit
ancestry or merge-base, so the review uses a two-tree diff instead of three-dot.
The relevant history contains 244 commits. Standards and Spec were reviewed by
independent parallel agents, following the `code-review` skill. Findings remain
separate; they have not been combined or ranked across axes.

Sources: the pinned `CLAUDE.md`, `CONTEXT.md`, `README.md`, `docs/agents/domain.md`,
accepted ADRs under `docs/adr/`, and relevant feature documentation. Referenced
ticket #382 was fetched during spec discovery; other historical tickets were not
bulk-fetched. Superseded decisions and explicit deferrals were respected.
All source line numbers and source links below refer to the pinned commit.

## Standards

- **P3 — documented-standard violation: corpus preparation hides completed work.**
  [embed_plan.py:190](https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/ingest/embed_plan.py#L190-L218)
  and [embed_run.py:472](https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/ingest/embed_run.py#L472-L486)
  run language detection and metadata derivation across the corpus before logging
  their counts. This conflicts with `CLAUDE.md:345–348`: “Output must stream
  incrementally” and “A long batch that prints only at the end is forbidden.”
  A reproduction ran 1,000 synthetic Jobs through the actual planner preparation
  functions, then interrupted before output generation: the only log was the
  initial prior-store summary. A failure during this phase leaves no progress
  count. Emit periodic scan/preparation counters, following the existing
  tokenization progress pattern. This is an observability issue, without a
  production data-loss claim.

- **P3 — judgement-only smell: duplicated HTTP helpers.**
  [mail.py:42](https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/alerts/mail.py#L42-L54)
  and [telegram.py:44](https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/alerts/telegram.py#L44-L56)
  duplicate the error-body formatter and POST implementation, including
  `body = exc.read().decode("utf-8", "replace").strip()[:200]`. This matches the
  review baseline's **Duplicated Code** smell: response-reading or diagnostic-policy
  changes require editing both copies. Consider one small shared implementation
  while retaining transport-specific failure contracts. No current functional
  divergence demonstrated.

No higher-severity standards breach substantiated. Saved sets omitting
keyword/salary filters are explicitly deferred. `facets.pool.map` was excluded:
its small, complete response showed no concrete streaming harm.

## Spec

- **P1 — Empty Boards retain closed Jobs indefinitely.**
  [index.py:242](https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/ingest/index.py#L242-L245)
  derives scrape scope only from emitted Job ids. A successful scrape returning
  zero Jobs therefore never records an absence. `live_keep_set()` explicitly
  retains empty Boards (`min_jobs=0`), so prune cannot remove those rows either.
  Spec: ADR-0023:38–40, “a Board scraped with no tech jobs still has its closed
  postings evicted.” Reproduced using the actual `JobWriter`: three successive
  empty scrapes produced no Unconfirmed ids or deletions; prune also returned no
  deletions. Positive control: explicitly supplying that Board as scope marks its
  Job Unconfirmed on the first absence and deletes it on the second.

- **P1 — Unsubscribing does not persist for query-seeded Invites without Saved sets.**
  [run.py:171](https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/alerts/run.py#L171-L180)
  recreates any missing Subscription from the Invite's `query` or `default_query`.
  `/unsubscribe` deletes that record, so the next alerts run silently enrolls the
  person again and subsequent matching Jobs resume delivery. Spec:
  `docs/email-alerts.md:83–84`, “Deleting `subscriptions/{id}.json` also works and
  is what the unsubscribe link does.” The current route promises “No more job
  digests.” ADR-0069:48–49 expressly preserves the no-Saved-sets path;
  ADR-0043's set flag protection does not cover it. In-memory reproduction
  confirmed recreation and token rotation for both seed types; an unseeded
  Invite remained absent.

No substantiated scope-creep finding. The documented Saved-set keyword/salary
omissions and historical Digest ceiling were excluded.

## Verification and limits

- Python: **2,415 passed, 1 skipped, 2 deselected, 1 xfailed**, in 15.87 seconds.
- UI: **65 passed** via `node --test tests/js/*.test.js`.
- The initial Python run was interrupted after 1,777 passes when Oracle listing
  tests reached unmocked detail HTTP calls. The completed run excluded
  `test_oracle_pages_past_the_first_200` and
  `test_oracle_stops_on_a_short_page_when_no_total_is_given`; their listing seam
  is mocked, but the detail pass is not. These exclusions are a verification
  limitation, not additional findings within `src/headstart`.
- Both P1 findings were independently reproduced by the parent agent, including
  the explicit-scope eviction control and unseeded-Invite control. No mail was sent.
- Inventoried all 102 files: 29 root, 22 ingest, 13 alerts, 28 scrapers, 10 UI.
  The Standards scan parsed all 92 Python files and checked imports, identities,
  concurrency, logging, routing, and duplicate function bodies. Spec analysis
  targeted shared filters, UI filter flow, alert enrollment/delivery, scrape scope,
  the language gate, metadata refresh, and listing/truncation contracts.
- This is a targeted full-directory audit, not exhaustive proof of every line,
  endpoint, or control path. Findings use pinned code and synthetic inputs;
  no current production incidence or endpoint-behavior claims are made.

Completed Python command, run from the pinned snapshot with the repository venv:

```bash
/Users/sarthakjain/Projects/HeadStart/.venv/bin/python -m pytest -q --disable-warnings --tb=short \
  -k 'not test_oracle_pages_past_the_first_200 and not test_oracle_stops_on_a_short_page_when_no_total_is_given'
```

## Reproduction: both P1 findings

Run with `PYTHONPATH=src` from the pinned checkout using the repository's Python
environment. `JobWriter` writes only inside a temporary directory; Subscription
records are held in memory. No HF access, ATS calls, or message delivery occurs.

```python
from tempfile import TemporaryDirectory
from unittest.mock import patch
from headstart.config import CompanyRef
from headstart.harvest import JobWriter
from headstart.ingest.index import _scraped_boards
from headstart.ingest import index_plan as ip
from headstart.alerts.run import subscription_for
from headstart.alerts.store import Invite

with patch.object(ip, "load_active_companies", return_value=[
    CompanyRef("greenhouse", "example", "Example")
]) as loader:
    keep = ip.live_keep_set("unused")
    assert loader.call_args.kwargs["min_jobs"] == 0
live = ip.boards_by_canon(keep)
jid = "greenhouse:example:closed"
with TemporaryDirectory(prefix="headstart-empty-board-review-") as path:
    writer = JobWriter(path, {"greenhouse"})
    writer.write([])
    writer.mark_done("greenhouse:example")
    writer.close()
    scope = _scraped_boards(path, set(), live)
    state = frozenset()
    for n in range(1, 4):
        plan = ip.plan_sync([jid], [], scope, live, state)
        state = plan.unconfirmed
        print(n, sorted(scope), sorted(plan.delete), sorted(state), flush=True)
    print("prune", ip.plan_prune([jid], keep), flush=True)
    first = ip.plan_sync([jid], [], keep, live, frozenset())
    second = ip.plan_sync([jid], [], keep, live, first.unconfirmed)
    print("control", sorted(first.unconfirmed), sorted(second.delete), flush=True)

class MemoryStore:
    def __init__(self):
        self.rows = {}
    def get(self, account):
        return self.rows.get(account)
    def put(self, sub):
        self.rows[sub.id] = sub
    def remove(self, account):
        self.rows.pop(account, None)

for label, invite in [
    ("query", Invite("review@example.invalid", query="engineer")),
    ("default_query", Invite("review@example.invalid", default_query="engineer")),
    ("bare_control", Invite("review@example.invalid")),
]:
    store = MemoryStore()
    first = subscription_for(invite, store, frozenset())
    if first:
        store.remove(first.id)
    second = subscription_for(invite, store, frozenset())
    print(label, first is not None, second is not None, flush=True)
```

Observed outcomes:

```text
1 [] [] []
2 [] [] []
3 [] [] []
prune ([], [])
control ['greenhouse:example:closed'] ['greenhouse:example:closed']
query True True
default_query True True
bare_control False False
```

## Reproduction: preparation progress

The Standards agent ran the following against pinned code. It preserves the real
`is_english`, `build_doc`, and `to_meta`; the corpus/state inputs, logging setup,
and interruption are substituted. It stops before output generation.

```python
import sys
from unittest.mock import patch
from headstart.ingest import embed_plan

class StopAfterScan(Exception):
    pass

job = {
    "ats": "greenhouse",
    "title": "Software Engineer",
    "description": (
        "We are looking for a software engineer to build reliable distributed systems. "
        "You will develop and maintain Python services, work with our engineering team, "
        "and improve our production infrastructure. At least 3 years of software "
        "engineering experience required."
    ),
}
def rows(_):
    for i in range(1000):
        yield dict(job, id=f"greenhouse:example:{i}")
    raise StopAfterScan

lines = []
with (
    patch.object(sys, "argv", ["embed_plan"]),
    patch.object(embed_plan, "_prior_rows", return_value=(set(), set())),
    patch.object(embed_plan, "load_scores", return_value={}),
    patch.object(embed_plan, "iter_jobs", side_effect=rows),
    patch.object(embed_plan.log, "setup"),
    patch.object(embed_plan.observability, "context"),
    patch.object(embed_plan._log, "info", side_effect=lambda msg: lines.append(str(msg))),
):
    try:
        embed_plan.main()
    except StopAfterScan:
        pass
print(lines, flush=True)
```

Observed: `['prior store: 0 embedded ids (0 without a description)']` after all
1,000 Jobs had been scanned (0.97 seconds in that reproduction). No preparation
progress was emitted before interruption.

Standards: **2 findings, both P3**, worst: invisible preparation progress.
Spec: **2 findings, both P1**, tied: stale Jobs and recreated subscriptions.
