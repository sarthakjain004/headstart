# Separate recorded activity from first-counted coverage

This is the explicitly limited first UI/backend slice approved on 2026-10-02.
The existing historical Board deltas establish first counting, not first authoritative
reading or a complete population of sites eligible at the start. A successful zero-job
Board can leave no stock delta. Fixed membership is useful but does not mean consistently
measured. These limits are visible, not silently translated into 100% quality.

## Three complementary readings

- **Sites tracked at start:** Boards first stock-counted by the selected comparable
  baseline. Membership remains fixed even when a Board stops contributing served stock.
  The count is every such Board in the Company/source scope, not surviving employers or
  only Boards with the selected category. Date changes can select a different population.
- **Sites added since start:** each additional Board's first-counted tech backlog is
  coverage, including another Board belonging to an existing Company. Subsequent recorded
  openings and closures/removals appear separately, over their shorter observation period.
  They are not hidden merely because the main chart selects the baseline cohort.
- **All known jobs:** served tech stock over all first-counted Boards in scope, including
  additions. This inventory is not a whole-market growth estimate.

The UI defaults to the existing `coverage=comparable`, preserving explicit All and shared
links that select All. The HTTP API's default and existing fields retain their meanings.
The summary is additive and does not replace the chart's reconciled/netted line readings.
Company shares and reference captions qualify comparable/source-filtered scopes as the
Company's tracked sites. The three compact cards keep dates, site counts, stock and activity
visible; freshness unknown and the backlog/read-gap caveat stay inline. Native
**What these numbers mean** disclosure holds the longer quality, policy and exposure limits.
On mobile the full summary follows the chart, KPI and controls. A compact starting-site
count/freshness line stays above the chart; desktop retains the three-column summary.

## Additive response contract

`coverage_summary` is null for an empty window or when its first plotted tick predates
Board count history. No population is reconstructed from the aggregate-only archive.
Otherwise it contains:

| Key | Meaning |
| --- | --- |
| `baseline` | Effective cohort baseline; the chart's `base` under comparable, otherwise the first plotted tick |
| `membership_basis` | `first_stock_count`; never first authoritative read |
| `from`, `to` | First and last plotted tick; activity excludes events on `from` |
| `scope`, `family` | All tech categories, or selected family; independent of chart metric, level or watched-role split |
| `cohort.boards` | Fixed original first-counted Board denominator in Company/source scope |
| `cohort.stock_start`, `cohort.stock_latest` | Tech inventory at the window endpoints for those same Boards |
| `entrants.boards` | Boards first stock-counted after baseline and by the last tick |
| `entrants.first_counted_backlog` | Tech stock at each entrant Board's own first count, summed once |
| `entrants.stock_start`, `entrants.stock_latest` | Entrants' endpoint tech stock, including entrants first counted before a later explicit window start |
| `all_known.stock_start`, `all_known.stock_latest` | Endpoint tech stock over both groups |
| `cohort/entrants.observed_opened` | Recorded Opened after both window start and each Board's own first count; null where no subsequent turnover exposure exists |
| `cohort/entrants.recorded_closed` | Recorded Closed over the same interval, labelled **recorded closures/removals** in the UI |
| `cohort/entrants.net_recounted` | Signed Recounted in less out, labelled net counting adjustments; not a count of events or a second count of first-counted backlog |
| `cohort/entrants.observed_since` | Earliest possible recorded turnover exposure for the group, bounded by window start, first count, and turnover-ledger start; individual sites can start later |
| `cohort/entrants.closures_unseen` | Distinct group Boards with a recorded unscoped marker after window start and by window end |
| `quality` | `start_eligibility`, `endpoint_freshness`, `successful_zero_boards`, `event_causes` are explicitly `unknown` |

Stock excludes non-tech and watched-role duplicate counts. Category drill summaries include
every band of that category; a watched-role drill still labels its summary as the category's.
An unknown category (including a literal `other`, which is not the chart's folded Other row)
gets an explicitly all-tech summary rather than a misleading zero category count.
The New-this-week chart does not change the summary's stock into a flow.

Recorded events are observations under the existing ledger's rules, not proof of employer
intent or a cause-pure hiring series. Closed can include Dormant policy evictions; rule and
scraper changes can affect observations. Recounting and netted chart movement may differ
from these raw recorded events. Zero recorded events do not prove flat hiring. Even zero
`closures_unseen` proves neither complete read coverage nor endpoint freshness: failed,
partial and unscheduled reads are not fully reconstructible from count history. No net
or quality percentage is derived from incomplete closure evidence.

## Follow-up: quality and successful zero-Board history

Use ADR-0330's stored `data/facts/board_reads/{stamp}.parquet`, not the current liveness
ledger or posting dates. Existing writer `job_facts.board_reads` includes successful empty
reads as well as errors/truncation, with canonical Board identity, outcome, `in_scope`,
line count and stated total. Before claiming quality:

1. Pin a fresh facts revision; verify the read files' coverage/publication continuity and
   align scrape-run stamps with served Trends ticks. Missing files remain unknown.
2. Build first-authoritative read membership including zero-line sites and keep it fixed.
   Distinguish all-empty listings from all-non-tech/English-gated served zeroes.
3. For each endpoint disclose authoritative read age and failed/partial/not-scheduled
   exposure. A stale carried-forward count is not a new successful observation.
4. Add event-cause evidence for policy versus employer removals before naming confirmed
   employer closures. Validate independent per-id/per-Board history; reconciliation alone
   cannot establish cause purity or complete scraper coverage.
5. Publish capture coverage dates and test zero baseline then opening, missing read file,
   unscoped read, unscheduled tick, off-Board departure and restored Board identity.

This slice adds no pipeline writes, deployment action, historical backfill or facts download.
Parent's independent memory/restatement verification and visual review remain separate gates.

## Regression scenarios

Real tick fixtures cover backlog 100 then Opened 3/Closed 1; a new Board of an old Company;
departed baseline member retained at zero stock; unscoped closure markers; absent read
quality; changed baseline membership; explicit earlier baseline with a later activity
window; source/category scope; and stock meaning under either chart metric. UI tests
exercise default comparable, explicit All sharing, visible unknowns, backlog/activity
separation and changed-population announcements.

## Local rendered QA

The actual Space template, CSS, JavaScript and `/trends` response were rendered with
Playwright Chromium against synthetic tick fixtures, with model/Hub imports stubbed and
every browser request intercepted locally. No production state was read or changed.
Checked desktop 1440×1100, mobile 390×844 and small mobile 320×800: document scroll width
equalled viewport width at each size, no page errors, three labelled cards visible,
unknown freshness readable without color, disclosure initially closed and keyboard Enter
opens it, and radio ArrowLeft moves selection/focus with correct ARIA and roving tabindex.
Trends radio controls measured 44px high, including after screenshot/repaint.
The final mobile order was checked from rendered bounding boxes: full summary below
controls, compact cohort/freshness line above the plot. Keyboard focus has an inset ring
using the existing accent token, and radio focus/selection survive keyboard navigation.
Closed/open summaries, full Trends panels and focused radio screenshots are retained
locally under `experiment/trends-coverage-panels-2026-10-02/artifacts/`; they are not
committed. This report stands alone; screenshots are synthetic examples, not corpus evidence.
Parent visual review and independent restatement verification remain required before merge.
