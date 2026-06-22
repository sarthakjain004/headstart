# HeadStart

Find software-engineering openings straight from companies' ATS (Applicant Tracking
System) career boards — earlier and more completely than relying on LinkedIn.

## Why

LinkedIn is not a comprehensive mirror of the job market. Employers have to *opt in* to
push roles there (via ATS integrations / "job wrapping"), and that path is gated and often
skipped. So two kinds of roles slip through: ones LinkedIn never gets because the employer
never syndicated them, and ones it gets late or buries below paid listings. Reading the ATS
directly catches both. The target is companies **worldwide**, focused on **software-engineering
/ tech roles**; the long tail of smaller employers (India among them) is just where the
LinkedIn gap is widest.

The coverage is whatever set of companies you point it at — there is no master "all
Greenhouse jobs" feed, so the curated company list in `config/companies.toml` *is* the
product (grown from the full slug universe in `data/ats-companies/`).

## How it works

```
GitHub Actions (cron)
  → scrape each company in config/companies.toml  (Greenhouse / Lever / Ashby JSON APIs)
  → normalize to one Job schema, dedupe
  → write docs/jobs.json
GitHub Pages serves docs/  → static dashboard filters jobs.json client-side
```

No server: scheduled Actions + a static Pages site. A second workflow runs the Telegram
alert bot (v2) — `/start`, set filters, get pinged when new matching roles appear
(setup in `docs/telegram-bot.md`).

## Scope

Locked for v1: portfolio-quality build; sources are Greenhouse + Lever + Ashby (Zoho
Recruit / India-specific ATS come next); deterministic rule-based filtering (no LLM yet);
output is a GitHub Pages dashboard. v2 (implemented): Telegram bot — `/start`, set filters,
get notified of new matching roles.

## Layout

- `src/headstart/` — the package: `models.py` (Job + normalization), `scrapers/` (per-ATS),
  `config.py`, `pipeline.py`, `__main__.py`; plus the v2 bot: `filters.py`, `bot.py`,
  `telegram.py`, `state.py`.
- `config/companies.toml` — the curated active company list (coverage).
- `data/ats-companies/` — the full slug universe to grow that list from.
- `docs/` — `index.html` dashboard + generated `jobs.json` (served by Pages).
- `.github/workflows/` — `scrape.yml` (refresh feed) and `bot.yml` (Telegram alerts).

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
