# Wellfound — target roles to scrape

The role/location slices HeadStart should pull from Wellfound, via the SSR scraper
(`scripts/scrape/run_wellfound.py`). Two URL shapes are in play:

- **Location-scoped:** `https://wellfound.com/role/l/{role}/{location}`
- **Remote (location-agnostic):** `https://wellfound.com/role/r/{role}`

Both render jobs into `__NEXT_DATA__` and paginate with `?page=N`, so the same parser handles
both. The role slug is already the engineering filter (filtering at the source, per project
scope).

## Roles (slugs) — 11, each scraped in india + remote
- `backend-engineer`
- `full-stack-engineer`
- `software-engineer`
- `software-architect`
- `artificial-intelligence-engineer`
- `frontend-engineer`
- `devops-engineer`
- `data-scientist`
- `machine-learning-engineer`
- `mobile-engineer`
- `product-designer`  _(design, not strictly engineering — included per explicit request)_

## Target URLs (22 = 11 roles × {india, remote})

### India (location = `india`) — `/role/l/{role}/india`
- https://wellfound.com/role/l/backend-engineer/india
- https://wellfound.com/role/l/full-stack-engineer/india
- https://wellfound.com/role/l/software-engineer/india
- https://wellfound.com/role/l/software-architect/india
- https://wellfound.com/role/l/artificial-intelligence-engineer/india
- https://wellfound.com/role/l/frontend-engineer/india
- https://wellfound.com/role/l/devops-engineer/india
- https://wellfound.com/role/l/data-scientist/india
- https://wellfound.com/role/l/machine-learning-engineer/india
- https://wellfound.com/role/l/mobile-engineer/india
- https://wellfound.com/role/l/product-designer/india

### Remote — `/role/r/{role}`
- https://wellfound.com/role/r/backend-engineer
- https://wellfound.com/role/r/full-stack-engineer
- https://wellfound.com/role/r/software-engineer
- https://wellfound.com/role/r/software-architect
- https://wellfound.com/role/r/artificial-intelligence-engineer
- https://wellfound.com/role/r/frontend-engineer
- https://wellfound.com/role/r/devops-engineer
- https://wellfound.com/role/r/data-scientist
- https://wellfound.com/role/r/machine-learning-engineer
- https://wellfound.com/role/r/mobile-engineer
- https://wellfound.com/role/r/product-designer

## How to scrape these

**Single board** — `run_wellfound.py [role] [location]`. The special location `remote`
switches to the `/role/r/{role}` board; any other location uses `/role/l/{role}/{location}`:
- `python scripts/scrape/run_wellfound.py software-engineer india`
- `python scripts/scrape/run_wellfound.py software-architect remote`

**All 22 boards at once** — `run_wellfound_sweep.py` walks every role x {india, remote} in one
browser session, dedupes by job id across boards, and writes one CSV (`data/jobs/wellfound.csv`,
16-col schema incl. full `description`, `compensation`, `currency`, `ats_source`; LF line
terminators). WARP-guarded (aborts if WARP is off). On a challenge it escalates audio → dynamic
slider → manual-wait. The `ROLES` list there is the source of truth — keep it in sync with this doc.
- `python scripts/scrape/run_wellfound_sweep.py` (all pages, all boards)
- flags: `--max-pages N` (cap per board), `--delay S`, `--headless`, `--no-warmup`,
  `--append` (don't clobber; seed dedup from the existing CSV), `--start-board N`/`--start-page N`
  (resume after a crash), `--roles a,b,c` (scrape only these role slugs — india+remote).

**Scrape only some roles (e.g. newly-added ones) onto the existing CSV:**
`python scripts/scrape/run_wellfound_sweep.py --append --roles frontend-engineer,devops-engineer,data-scientist,machine-learning-engineer,mobile-engineer,product-designer`

Scale note: a full sweep is large — board page-counts run ~7–111 (AI-engineer/remote alone
~111 pages), so budget tens of minutes and expect heavy IP exposure. Use `--max-pages` to bound
a run, or `--roles` to scope it.
