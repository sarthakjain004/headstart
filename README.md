# HeadStart

Find software-engineering openings straight from companies' ATS (Applicant Tracking
System) career boards — earlier and more completely than relying on LinkedIn.

## Why

LinkedIn is not a comprehensive mirror of the job market. Employers have to *opt in* to
push roles there (via ATS integrations / "job wrapping"), and that path is gated and often
skipped. So two kinds of roles slip through: ones LinkedIn never gets because the employer
never syndicated them, and ones it gets late or buries below paid listings. Reading the ATS
directly catches both. The gap is widest in the India market and the long tail of smaller
employers, which is the segment this project targets.

The coverage is whatever set of companies you point it at — there is no master "all
Greenhouse jobs" feed, so the curated company list in `config/companies.toml` *is* the
product.

## How it works

```
GitHub Actions (cron)
  → scrape each company in config/companies.toml  (Greenhouse / Lever / Ashby JSON APIs)
  → normalize to one Job schema, dedupe
  → write docs/jobs.json
GitHub Pages serves docs/  → static dashboard filters jobs.json client-side
```

No server: scheduled Actions + a static Pages site. (v2 adds a Telegram bot for
new-job notifications, also driven by Actions.)

## Scope

Locked for v1: portfolio-quality build; sources are Greenhouse + Lever + Ashby (Zoho
Recruit / India-specific ATS come next); deterministic rule-based filtering (no LLM yet);
output is a GitHub Pages dashboard. v2: Telegram bot — `/start`, set filters, get notified
of new matching roles.

## Layout

- `src/headstart/` — the package: `models.py` (Job + normalization), `scrapers/`
  (per-ATS), `config.py`, `pipeline.py`, `__main__.py`.
- `config/companies.toml` — the curated company list (coverage).
- `docs/` — `index.html` dashboard + generated `jobs.json` (served by Pages).
- `.github/workflows/scrape.yml` — scheduled refresh.

## Development

Requires Python 3.12+.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest                           # run tests (network-free, uses fixtures)
python -m headstart              # live scrape -> docs/jobs.json
python -m http.server -d docs    # preview the dashboard at http://localhost:8000
```
