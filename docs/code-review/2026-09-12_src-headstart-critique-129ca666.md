# src/headstart engineering critique — 2026-09-12

**Overall: 7.3/10.** HeadStart has a coherent domain model, useful shared modules, substantial tests,
and unusually explicit reasoning about constrained infrastructure. Its weakest point is the correctness
of state transitions between otherwise sensible modules: empty scrape results, unsubscribe intent,
failed reads, and competing browser requests. These affect the product's central promises.

This is an independent engineering judgement for a growing, free-tier product, not a commercial-platform
checklist. The assessed source is **102 files / 33,167 lines** at
[`129ca66615a3ec62b81cd363a3eb314a056b4fb6`](https://github.com/sarthakjain004/headstart/tree/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart).
Uncommitted changes are excluded. All source/spec/test reads used the exported pinned snapshot.
The completed [Standards/Spec review](2026-09-12_src-headstart-empty-tree-129ca666.md) supplies independent
review coverage, two P1 reproductions, and suite results; this critique adds targeted inspection and
two new synthetic reproductions. Neither report proves every control path correct.

## Scoring

Each category is scored out of ten; its contribution is `weight × score / 100`.

| Category | Weight | Score | Contribution | Reason |
| --- | ---: | ---: | ---: | --- |
| Correctness and reliability | 30% | 6.0 | 1.80 | Good local policies; four confirmed functional defects cross important interfaces. |
| Architecture and maintainability | 20% | 8.0 | 1.60 | Strong domain vocabulary and useful seams; some contract knowledge remains distributed. |
| Tests and verification | 15% | 8.0 | 1.20 | Broad, fast suites and real-source UI tests; lifecycle, dependency and request-order gaps remain. |
| Operations and observability | 10% | 7.0 | 0.70 | Resumability, progress and run reports; incomplete visibility into permanently withheld freshness. |
| Performance and resource discipline | 10% | 8.5 | 0.85 | Cost-aware scheduling, detail reuse and incremental storage suit free-tier limits. |
| Security and data handling | 10% | 8.0 | 0.80 | Verified identity, constrained record paths and private-data separation; full deployment security unassessed. |
| Product and UI completeness | 5% | 7.0 | 0.35 | Search, Matches, Saved jobs, Profiles and Trends exist; async correctness needs attention. |
| **Total** | **100%** | | **7.30 → 7.3/10** | |

The score is not lower because substantial complexity already lives behind interfaces that earn their
keep. It is not higher because passing local tests currently coexists with broken end-to-end state
transitions. Fixing those transitions is more valuable than changing frameworks or splitting files by size.

## What is strong

- **Search has a real shared interface.** `JobSearch.run(args)` serves both UIs; `build_filter` also
  supplies Facets. Model/prefix conventions, input escaping and filter semantics have locality rather
  than separate implementations per caller ([search.py:1–23][search], [facets.py:85–103][facets]).
- **Pure policy is separated from effects.** `index_plan.plan_sync` can exercise grace-period and
  partial-scrape policy without LanceDB. `refresh_row` can exercise metadata reconciliation without a
  store. Those seams enabled useful reproductions here ([index_plan.py:122–234][plan],
  [update_meta.py:235–325][refresh]). This is module depth, not merely small functions.
- **Scraping complexity is shared where it actually varies.** `BaseScraper` provides detail fan-out,
  held-description checks and truncation reporting; ATS-specific extraction stays in Scrapers.
  Shared HTTP handles pooled sessions and retry policy ([base.py:151–237][base],
  [http.py:1–15][http]). Provider-specific complexity alone does not justify a rewrite.
- **The pipeline accounts for failure and cost.** `JobWriter` flushes Jobs before journaling completion;
  state fetching distinguishes missing prior state from a first run; cost-based packing and retained
  descriptions avoid unnecessary work ([harvest.py:64–146][harvest], [state_fetch.py:402][fetch],
  [state_witness.py:79][witness], [binpack.py:29][binpack], [update_descriptions.py:1][descriptions]).
- **Identity and sensitive input have sensible seams.** Google verification delegates signature checks;
  Profile extraction accepts an injected LLM callable, limits input and returns selected fields;
  record identifiers are checked before reads. UI rendering escapes scraped text and restricts link
  schemes ([identity.py:36–61][identity], [profile_extract.py:134–163][profile],
  [store.py:569][store-get], [app.js:5–8][ui-top]). This credits visible controls, not an audited deployment.

## Findings and gaps

Priorities: **P1** repair promptly; **P2** next reliability work; **P3** scoped improvement or revisit.
For accepted deferrals, priority means a review order, not an instruction to implement them.
Confidence below concerns the stated code behaviour; no current production incidence is claimed.

### HS-C01 — P1 · Confirmed defect · Empty Boards never reach eviction

**Fact:** `_scraped_boards` infers scope from emitted Job ids. A successfully scraped Board with zero
postings emits no id; `live_keep_set(min_jobs=0)` keeps it in prune's keep-set. Consequently repeated
empty scrapes never mark its previously indexed Jobs Unconfirmed or evict them
([index.py:242–245][empty-scope], [index_plan.py:289–325][keep]). This contradicts
[ADR-0023:38–40][adr23]. **Impact:** closed Jobs can remain searchable indefinitely.
**Confidence: high**, actual JobWriter and three empty scrapes reproduced; explicit-scope control evicts
on the second absence. **Next:** carry completed authoritative Board identities through the join,
independent of Job rows, and test empty/full/truncated/failed outcomes through sync. Keep the two-scrape
grace period. Full reproduction is in the previous review.

### HS-C02 — P1 · Confirmed defect · Unsubscribe does not preserve intent

**Fact:** for an Invite with `query` or `default_query` and no Saved sets, deleting its Subscription
allows the next `subscription_for` call to create it again ([run.py:171–180][seed]).
The unsubscribe contract says deletion stops Digests ([email-alerts.md:83–84][email-doc]);
[ADR-0069:43–49][ownership] preserves this no-Saved-sets population.
**Impact:** matching Jobs can resume delivery after unsubscribe.
**Confidence: high**, both seed forms reproduced; unseeded control stays absent.
**Next:** persist opt-out intent independently of record absence and make enrollment honour it;
test unsubscribe → later run → explicit re-enable. Previous review contains the reproduction.

### HS-C03 — P2 · Confirmed defect · A failed Subscription read can overwrite valid state

**Fact:** `Store.get` logs a read error but returns the same `None` as an absent record
([store.py:569–608][store-get]). The seeded-Invite path then writes a fresh Subscription at that path
([run.py:171–180][seed]), replacing its Watermark and unsubscribe token. This defeats the preservation
contract of `Subscription.revised` ([store.py:186–195][subscription]).
**Trigger/impact:** a read fails while the subsequent write succeeds; matching Jobs since the old
Watermark can be skipped and old unsubscribe links invalidated.
**Confidence: high**, synthetic TimeoutError reproduced with the actual Store and a healthy-read control.
**Next:** distinguish confirmed absence from unreadable state at the Store interface; skip that Account
on uncertain reads. Logging alone does not make replacement safe. This is separate from accepted races.

### HS-C04 — P2 · Confirmed defect · Older responses overwrite newer UI intent

**Fact:** `fetchPage` applies each Search and Facets completion without checking which request is current
([app.js:462–508][ui-search]). Start A, then B; resolve B, then A: the query box still says B while
results show A. Enter and filter changes can start overlapping requests. `runSet` and `loadTrends`
also commit unchecked completions ([app.js:839–855][ui-set], [app.js:1382–1415][ui-trends]).
**Impact:** rows/counts or a selected set/chart can describe outdated controls.
**Confidence: high for Search**, dynamically reproduced against shipped JS; sibling flows inspected only.
**Next:** one current-request identity per view, checked before every success/error/facet render; capture
page/filter state per request. Add out-of-order completion tests. Cancellation is an optional resource saving.

### HS-C05 — P3 · Confirmed documented-standard defect · Silent preparation work

**Fact:** language detection and metadata preparation finish their corpus loop before reporting progress
([embed_plan.py:190–218][embed-plan], [embed_run.py:472–486][embed-run]);
[CLAUDE.md:345–348][streaming] requires incremental output. The previous review interrupted after
1,000 prepared synthetic Jobs and observed only the initial prior-store message.
**Impact:** a slow or killed preparation phase provides no completed-work count.
**Confidence: high. Next:** periodic scanned/accepted/rejected counts with elapsed time; retain existing
tokenization progress. This finding does not claim production data loss.

### HS-C06 — P2 · Architectural risk, explicitly accepted · Concurrent record writers

**Fact:** `send_one` sends, then writes the whole Subscription ([run.py:102–113][send]); Space edits
write that record too. Store uploads have no conditional version check ([store.py:518–527][store-write]).
[ADR-0043:64–73][projection] accepts same-record lost writes and multi-file toggle crash windows.
**Judgement:** per-record files correctly isolate Accounts but do not serialize changes within one Account.
An edit or unsubscribe overlapping delivery can be overwritten. At-least-once delivery itself is deliberate.
**Confidence: high in the mechanism; frequency unmeasured. Next:** first test interleavings and record
the chosen conflict policy. Consider conditional writes or a single owner only if the measured cost warrants
reopening the ADR; an immediate database migration is not justified.

### HS-C07 — P2 · Missing measurement · Withheld freshness has no age/row budget

**Fact:** Unauthoritative Boards leave eviction scope ([index.py:458–474][excluded]); the exclusion
record contains reasons, not a cumulative Job-age policy ([index_plan.py:365–400][unauthoritative]).
Repeatedly short scrapes can therefore retain closed rows indefinitely. This is an accepted tradeoff in
protecting live Jobs, distinct from HS-C01; [CLAUDE.md:472–477][freshness-rule] calls out the visibility gap.
**Judgement:** Board counts alone cannot express the user-visible freshness debt.
**Confidence: high in policy, current extent unknown. Next:** measure retained row counts, age since last
authoritative scrape, and persistent exclusions per Board/ATS. Choose a handling policy from that evidence;
do not add a blind age-based deletion rule that breaks recall.

### HS-C08 — P2 · Missing tests/assurance · Local passes do not cover lifecycle and runtime seams

**Evidence:** the pinned run excluded two Oracle tests because listing mocks left the real detail path
reachable (see previous report). README/schema checks explicitly skip without pyarrow
([test_readme_schema.py:8–9,33–44][schema-test]); quality CI installs `[dev]`
([ci.yml:31–37][ci]). UI tests execute real JS against a stub DOM, without a browser
([app_search.test.js:1–5,17–85][ui-test]). HS-C01–04 show missing interaction cases, not inadequate test volume.
**Judgement/confidence: high** that these leave assurance gaps; no claim the whole deployment is untested.
**Next:** close both Oracle network seams; add the four lifecycle/order regressions; make the schema gate
run on schema changes with its required dependencies. Add a small browser smoke for navigation, keyboard
controls and slow requests if UI work continues. Avoid wholesale test rewrites or inflated unit-test counts.

### HS-C09 — P3 · Architectural risk · Cross-stage contract edits still require several coordinated changes

**Fact:** Job fields, embedding metadata, served schema and result projection are distinct declarations
([models.py:13–38][models], [doc_prep.py:43–63][metadata], [index.py:153][schema],
[search.py:158–184][result-columns]); persistent Saved-set filters and UI controls have their own mappings
([store.py:64–112][filter-store], [app.js:283][controls]). ATS registration and verification URL shapes
are also separate registries ([registry.py:34][registry], [verify_filters.py:71][url-harness]).
**Judgement:** these projections are legitimately different, but their relationships require distributed
knowledge; adding one field/ATS can omit a consumer. No new mismatch is alleged.
**Confidence: high. Next:** document required mappings and extend focused contract checks when each
surface changes. Generate only truly identical declarations; do not force every projection into one schema.

### HS-C10 — P3 · Optional improvement · Duplicate delivery HTTP helpers

**Fact:** mail and Telegram duplicate POST/error-body handling ([mail.py:42–54][mail],
[telegram.py:44–56][telegram]); the prior Standards review demonstrated matching bodies.
**Judgement:** small lost-locality cost when diagnostic policy changes, with no current divergence shown.
**Confidence: high in duplication, low urgency. Next:** share the small common implementation when touching
either helper, preserving transport-specific errors. Keep the transport seam: two real delivery choices justify it.

### HS-C11 — P3 · Deliberate deferral · Stable-id text changes do not generally refresh vectors

**Fact:** sync preserves re-seen ids; full content-hash change detection is deferred
([index_plan.py:130–134][plan], [ADR-0021:23–39][content-drift]). Title/field metadata refresh and repair
of formerly missing descriptions solve narrower cases. Holding a description also skips repeated detail
fetches ([base.py:223–234][base]), so a hash alone would not observe every organic edit.
**Impact:** a still-open Job edited in place can keep an old semantic representation.
**Confidence: high in limitation; organic churn unmeasured here. Next:** measure edit churn with a bounded
freshness sample, then price periodic detail refresh and re-embedding together. This is an accepted budget
decision, not an undisclosed specification defect.

### HS-C12 — P3 · Deliberate deferrals · Product coverage has intentional limits

**Fact:** companies are global, but the semantic corpus is English-only; the recall-biased post-hoc Tech
filter is authoritative; constraints come from explicit Search filters. Multilingual retrieval and an LLM
query parser are deferred ([CLAUDE.md:5–36][scope]). Saved sets/Subscriptions omit keyword and salary
brackets by explicit decision ([ADR-0104:157–160][saved-deferrals]); Digests are capped and delivery is
at-least-once ([shortlist.py:23][digest-cap], [ADR-0035:130–142][alerts-adr]).
**Judgement/confidence: high:** these are real limits, not reasons to demand a larger platform.
**Next:** keep limits understandable at the point of use; prioritize expansion only from demonstrated
user need and budget. Saving every Search filter requires an explicit product decision.

## Practical improvement sequence

1. **Repair user intent and eviction:** HS-C01/02, with end-to-end state-transition tests and existing
   partial-scrape controls. Then HS-C03 so a storage outage cannot mint replacement state.
2. **Make view state match current controls:** HS-C04, including stale failures and Facets, and extend the
   same request-ownership rule to Matches/Trends after reproducing those flows.
3. **Strengthen assurance and diagnosis:** HS-C08 and HS-C05; measure HS-C07 before choosing a freshness
   policy. This provides evidence for decisions currently hidden behind successful runs.
4. **Revisit accepted risks only with evidence:** quantify same-Account contention and text churn
   (HS-C06/11). Address HS-C09/10 opportunistically in scoped changes. HS-C12 is a product decision.

What would raise the score: verified fixes for HS-C01–04 would materially improve correctness and UI
confidence; enforced runtime/schema checks and measured freshness would make an approximately **8/10**
assessment credible. A higher score would require demonstrated operational results, not just these code
changes. No source fixes or production operations were performed for this document.

## Coverage and limits

| Subsystem | Files | Inspection emphasis | Remaining uncertainty |
| --- | ---: | --- | --- |
| Root/core | 29 | Inventory; shared identities, harvesting and dependency placement | Not every normalization or network branch re-executed |
| Scrapers | 28 | Inventory; shared HTTP/BaseScraper contracts; prior scraper-contract scans | No live ATS campaign or renewed pagination/rate-limit measurement |
| Ingest/state/index | 22 | Scope/grace, preparation, refresh and state ownership; reused failure controls | No production snapshot, restart drill or resource benchmark |
| Metadata/extraction/search | Included above | Shared filters, projections, language gate and derivation interface | No fresh extraction-accuracy or retrieval-quality census |
| Alerts/accounts | 13 | Enrollment, unsubscribe, read failure, Watermarks and record ownership | No real messages, concurrent HF writes or full auth penetration test |
| UI | 10 | Request ordering, data rendering and existing real-source tests | No new browser layout/accessibility review |
| **Total** | **102** | **33,167 pinned lines inventoried** | **Targeted audit, not exhaustive semantic proof** |

Reused verification: **2,415 Python passed, 1 skipped, 2 deselected, 1 xfailed** in 15.87 seconds;
**65 UI tests passed**. Exclusions: `test_oracle_pages_past_the_first_200` and
`test_oracle_stops_on_a_short_page_when_no_total_is_given`. This critique did not rerun those suites.
It executed two additional synthetic probes below. The parent independently reran both documented
reproductions and confirmed their outcomes. Source outside `src/headstart` was inspected only
where needed to interpret tests/specifications; the Flask host, workflows and infrastructure are not a
complete security or deployment audit. No HF/local pipeline data was used for production figures.

### Compact inventory

Paths below are relative to `src/headstart`. **D** means a targeted control-path walkthrough in this
critique, not every line in that file. **S** means structural scan (module purpose/declarations), supported
by the previous all-directory review; it does not claim independent deep inspection of every implementation.

| Group | D: targeted walkthrough | S: structural scan |
| --- | --- | --- |
| Root (29) | `facets.py`, `harvest.py`, `llm_router.py`, `models.py`, `profile_extract.py`, `search.py` | `__init__.py`, `__main__.py`, `board_aliases.py`, `board_cost.py`, `board_description_gap.py`, `board_priority.py`, `browser_http.py`, `company_name.py`, `config.py`, `corpus.py`, `experience.py`, `fanout_stats.py`, `fx.py`, `geo.py`, `http.py`, `liveness.py`, `log.py`, `remote.py`, `roles.py`, `salary.py`, `spare_egress.py`, `tech_filter.py`, `telegram_bot_api.py` |
| Ingest (22) | `ingest/doc_prep.py`, `ingest/index.py`, `ingest/index_plan.py`, `ingest/update_meta.py` | `ingest/__init__.py`, `ingest/binpack.py`, `ingest/board_failures.py`, `ingest/embed_merge.py`, `ingest/embed_plan.py`, `ingest/embed_run.py`, `ingest/filter_tech.py`, `ingest/observability.py`, `ingest/role_assignments.py`, `ingest/role_trends.py`, `ingest/scrape_join.py`, `ingest/scrape_plan.py`, `ingest/scrape_run.py`, `ingest/shard_speedup.py`, `ingest/state_fetch.py`, `ingest/state_witness.py`, `ingest/update_descriptions.py`, `ingest/update_ledgers.py` |
| Alerts (13) | `alerts/identity.py`, `alerts/run.py`, `alerts/store.py` | `alerts/__init__.py`, `alerts/access.py`, `alerts/bot.py`, `alerts/digest.py`, `alerts/mail.py`, `alerts/registry.py`, `alerts/shortlist.py`, `alerts/space_query.py`, `alerts/telegram.py`, `alerts/transports.py` |
| Scrapers (28) | — | `scrapers/__init__.py`, `scrapers/base.py`, `scrapers/registry.py`, `scrapers/ashby.py`, `scrapers/darwinbox.py`, `scrapers/eightfold.py`, `scrapers/freshteam.py`, `scrapers/greenhouse.py`, `scrapers/icims.py`, `scrapers/jazzhr.py`, `scrapers/jobvite.py`, `scrapers/join.py`, `scrapers/keka.py`, `scrapers/lever.py`, `scrapers/oracle.py`, `scrapers/personio.py`, `scrapers/recruitee.py`, `scrapers/ripplehire.py`, `scrapers/rippling.py`, `scrapers/sensehq.py`, `scrapers/smartrecruiters.py`, `scrapers/successfactors.py`, `scrapers/teamtailor.py`, `scrapers/trakstar.py`, `scrapers/workable.py`, `scrapers/workday.py`, `scrapers/zoho.py`, `scrapers/zwayam.py` |
| UI (10) | `ui/static/app.js` | `ui/static/style.css`, `ui/templates/base.html`, `ui/templates/data.html`, `ui/templates/matches.html`, `ui/templates/profile.html`, `ui/templates/saved.html`, `ui/templates/search.html`, `ui/templates/signin.html`, `ui/templates/trends.html` |

### Reproducing the additional findings

Run from the pinned snapshot. Both probes use synthetic data and make no network calls or durable writes.
For HS-C04, this reuses the existing test harness and executes the actual `app.js`:

```bash
node <<'NODE'
const fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');
const dir = path.resolve('tests/js');
const prelude = fs.readFileSync(path.join(dir, 'app_search.test.js'), 'utf8').split('\ntest(')[0];
const h = {require, __dirname: dir, console, URLSearchParams};
vm.runInNewContext(prelude + '\nglobalThis.h = {loadApp, job, set};', h);
const {loadApp, job, set} = h.h;
(async () => {
  const pending = {};
  const a = loadApp(url => {
    if (url.startsWith('/facets?')) return {total: 1};
    if (!url.startsWith('/search?')) return [];
    const q = new URL(url, 'http://test').searchParams.get('q');
    return q ? new Promise(resolve => pending[q] = resolve) : [];
  });
  const tick = () => new Promise(resolve => setTimeout(resolve, 0));
  await tick();
  set(a.nodes, 'q', 'old'); const old = a.t.go();
  set(a.nodes, 'q', 'new'); const newer = a.t.go();
  await tick();
  pending.new([job('new', {title: 'NEW_RESULT'})]); await newer;
  console.log(a.nodes.results.innerHTML.includes('NEW_RESULT')); // true: control
  pending.old([job('old', {title: 'OLD_RESULT'})]); await old;
  console.log(a.nodes.q.value, a.nodes.results.innerHTML.includes('OLD_RESULT')); // new true
})();
NODE
```

HS-C03 uses the actual Store interface and `subscription_for`; `_read`/`_write` alone are replaced:

```python
import json
from unittest.mock import patch
from headstart.alerts import store as st
from headstart.alerts.run import subscription_for

original = st.Subscription.create('review@example.invalid', 'engineer', {},
                                  when='2026-09-01T00:00:00+00:00')
held = {original.path(): json.dumps(original.to_dict()).encode()}
store = st.Store('synthetic', 'unused')
invite = st.Invite(original.email, query='engineer')
def write(repo, path, data, token):
    held[path] = data
with patch.object(st, '_read', side_effect=lambda r, p, t: held[p]), \
     patch.object(st, '_write', side_effect=write):
    control = subscription_for(invite, store, frozenset())
assert control.unsubscribe_token == original.unsubscribe_token
with patch.object(st, '_read', side_effect=TimeoutError('synthetic read failure')), \
     patch.object(st, '_write', side_effect=write):
    fresh = subscription_for(invite, store, frozenset())
assert fresh.path() == original.path()
assert fresh.watermark != original.watermark
assert fresh.unsubscribe_token != original.unsubscribe_token
```

All four assertions passed. The logged error does not prevent the final overwrite. The original P1 and
preparation-progress reproductions remain in the [previous report](2026-09-12_src-headstart-empty-tree-129ca666.md).

<!-- Every evidence link is pinned to the assessed commit. -->
[search]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/search.py#L1-L23
[facets]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/facets.py#L85-L103
[plan]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/ingest/index_plan.py#L122-L234
[refresh]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/ingest/update_meta.py#L235-L325
[base]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/scrapers/base.py#L151-L237
[http]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/http.py#L1-L15
[harvest]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/harvest.py#L64-L146
[fetch]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/ingest/state_fetch.py#L402
[witness]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/ingest/state_witness.py#L79
[binpack]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/ingest/binpack.py#L29
[descriptions]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/ingest/update_descriptions.py#L1
[identity]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/alerts/identity.py#L36-L61
[profile]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/profile_extract.py#L134-L163
[store-get]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/alerts/store.py#L569-L608
[ui-top]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/ui/static/app.js#L5-L8
[empty-scope]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/ingest/index.py#L242-L245
[keep]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/ingest/index_plan.py#L289-L325
[adr23]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/docs/adr/0023-prune-stale-and-duplicate-index-rows.md#L38-L40
[seed]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/alerts/run.py#L171-L180
[email-doc]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/docs/email-alerts.md#L83-L84
[ownership]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/docs/adr/0069-sets-own-their-projection-against-the-allowlist.md#L43-L49
[subscription]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/alerts/store.py#L186-L195
[ui-search]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/ui/static/app.js#L462-L508
[ui-set]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/ui/static/app.js#L839-L855
[ui-trends]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/ui/static/app.js#L1382-L1415
[embed-plan]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/ingest/embed_plan.py#L190-L218
[embed-run]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/ingest/embed_run.py#L472-L486
[streaming]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/CLAUDE.md#L345-L348
[send]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/alerts/run.py#L102-L113
[store-write]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/alerts/store.py#L518-L527
[projection]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/docs/adr/0043-saved-sets-subscription-projection.md#L64-L73
[excluded]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/ingest/index.py#L458-L474
[unauthoritative]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/ingest/index_plan.py#L365-L400
[freshness-rule]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/CLAUDE.md#L472-L477
[schema-test]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/tests/test_readme_schema.py#L8-L44
[ci]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/.github/workflows/ci.yml#L31-L37
[ui-test]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/tests/js/app_search.test.js#L1-L85
[models]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/models.py#L13-L38
[metadata]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/ingest/doc_prep.py#L43-L63
[schema]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/ingest/index.py#L153
[result-columns]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/search.py#L158-L184
[filter-store]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/alerts/store.py#L64-L112
[controls]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/ui/static/app.js#L283
[registry]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/scrapers/registry.py#L34
[url-harness]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/scripts/eval/verify_filters.py#L71
[mail]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/alerts/mail.py#L42-L54
[telegram]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/alerts/telegram.py#L44-L56
[content-drift]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/docs/adr/0021-re-embed-on-content-change.md#L23-L39
[scope]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/CLAUDE.md#L5-L36
[saved-deferrals]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/docs/adr/0104-a-keyword-filter-with-a-scope-map-and-a-stored-description-column.md#L157-L160
[digest-cap]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/src/headstart/alerts/shortlist.py#L23
[alerts-adr]: https://github.com/sarthakjain004/headstart/blob/129ca66615a3ec62b81cd363a3eb314a056b4fb6/docs/adr/0035-email-job-alerts.md#L130-L142
