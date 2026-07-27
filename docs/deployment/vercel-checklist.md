# Deploying HeadStart on Vercel — Checklist

A step-by-step checklist to get the HeadStart dashboard live on Vercel. Read the mental
model first — it explains *why* the steps are short.

## Mental model: what actually deploys

HeadStart's web surface is a **static site**, not a running program. There is nothing for
Vercel to execute:

- `docs/index.html` — a ~165-line dashboard (vanilla HTML/JS, no framework, no build step).
- `docs/jobs.json` — the job feed (a single committed JSON; ~5.2 MB / ~11,690 jobs today).
  The dashboard loads it with `fetch("./jobs.json")`.

The **scraper** (`python -m headstart`) and the **Telegram bot** run on **GitHub Actions**,
not Vercel. `.github/workflows/scrape.yml` rebuilds `docs/jobs.json` every 6 h and commits it;
`.github/workflows/bot.yml` polls Telegram. Vercel's *only* job is to serve the `docs/` folder
over HTTPS (replacing GitHub Pages, which is off because the repo is private).

```
GitHub Actions (every 2h)          GitHub repo (main)            Vercel
  python -m headstart  ──commits──►  docs/jobs.json  ──push──►  redeploy ──► serves docs/ as the site
                                     docs/index.html
```

So: **no Python runtime, no serverless functions, no database, no env vars** are needed for the
MVP. Keep that in mind — most of Vercel's complexity (functions, build config, storage) simply
doesn't apply here.

> Eligibility flag up front: Vercel's **Hobby (free) plan is non-commercial / personal use
> only** ([fair-use guidelines](https://vercel.com/docs/limits/fair-use-guidelines)). A
> personal/portfolio job board is fine. If HeadStart becomes a product (revenue, a company, or
> heavy public traffic), you must move to **Pro ($20/mo)**.

---

## Pre-flight

- [ ] You can sign in to Vercel with the GitHub account that owns `sarthakjain004/headstart`
      (or has admin on it). Vercel's GitHub App needs access to the **private** repo.
- [ ] `main` is green and contains `docs/index.html` + `docs/jobs.json` (it does — verified).
- [ ] Decide the access model: **public** dashboard, or gated behind Vercel login
      (Vercel Authentication is available on Hobby — see Optional below).
- [ ] Confirm this is a personal/non-commercial deployment (Hobby) — otherwise plan for Pro.

---

## Step 1 — Create the project

- [ ] Go to <https://vercel.com/new> → **Import Git Repository**.
- [ ] If the repo isn't listed, click **Adjust GitHub App Permissions** and grant Vercel access
      to `headstart` (it's private, so it won't appear until you do).
- [ ] Select `sarthakjain004/headstart` and click **Import**.

## Step 2 — Configure build & output (the only settings that matter)

Because the site is static and lives in a subfolder, point Vercel at `docs/` and tell it not to
build:

- [ ] **Framework Preset** → `Other`.
- [ ] **Root Directory** → `docs`  *(this is the key setting — Vercel will treat `docs/` as the
      site root, so `index.html` serves at `/` and `jobs.json` at `/jobs.json`)*.
- [ ] **Build Command** → leave empty / toggle **Override** on and clear it (skip build).
- [ ] **Output Directory** → leave default (with Root Directory = `docs` and no `public/`
      subfolder, Vercel serves the root of `docs/` directly).
- [ ] **Install Command** → leave empty.
- [ ] No **Environment Variables** are needed for the MVP.

> Why Root Directory and not a build: per Vercel's
> [build configuration docs](https://vercel.com/docs/builds/configure-a-build), a static site
> with only HTML/CSS/JS should use Framework = `Other` with an empty Build Command, and
> "Other" serves `public/` if present else the (root) directory. Setting **Root Directory =
> `docs`** makes that root be your `docs/` folder. Nothing outside `docs/` (src/, data/,
> scripts/, experiment/) is served — good, that's what you want.

## Step 3 — First deploy & verify

- [ ] Click **Deploy**. The build log should show *no build step* and just upload static files
      (a few seconds).
- [ ] Open the generated `*.vercel.app` URL — the dashboard should render.
- [ ] Confirm the feed loads: open the browser devtools Network tab and check
      `GET /jobs.json` returns `200` (and is served gzip/Brotli-compressed — the 5.2 MB JSON
      transfers far smaller).
- [ ] If the page shows "Could not load jobs.json", the relative path or Root Directory is
      wrong — re-check Step 2 (Root Directory must be `docs`).

---

## Step 4 — Keep it fresh (the redeploy mechanism)

The feed updates when `docs/jobs.json` changes on `main`. Two ways Vercel can pick that up:

**Option A — Auto-deploy on push (default, try this first).**

- [ ] In **Project → Settings → Git**, confirm the **Production Branch** is `main` and that
      auto-deploy on push is on (it is by default).
- [ ] After the next scheduled `scrape.yml` run commits a feed refresh, confirm a new Vercel
      deployment appears automatically.

> Known gotcha: Vercel occasionally does **not** auto-deploy pushes made by bots/Actions
> (`github-actions[bot]`), depending on Git integration settings. If feed-refresh commits land
> but no deploy fires, use Option B.

**Option B — Deploy Hook (reliable trigger from the Action).**

- [ ] In **Project → Settings → Git → Deploy Hooks**, create a hook for branch `main`; copy the
      URL.
- [ ] In the GitHub repo: **Settings → Secrets and variables → Actions → New repository
      secret**, name `VERCEL_DEPLOY_HOOK`, paste the URL.
- [ ] Add a final step to `.github/workflows/scrape.yml` so a refreshed feed pings Vercel:

      ```yaml
      - name: Trigger Vercel redeploy
        if: success()
        run: curl -fsS -X POST "${{ secrets.VERCEL_DEPLOY_HOOK }}"
      ```

  (Place it after the commit/push step. The hook needs no auth or payload —
  [Vercel Deploy Hooks](https://vercel.com/docs/deploy-hooks).)

---

## Optional — niceties

- [ ] **Custom domain**: Project → Settings → Domains → add your domain and follow the DNS
      records. Hobby allows up to 50 domains/project.
- [ ] **Gate access**: Project → Settings → **Deployment Protection → Vercel Authentication**
      (available on Hobby) requires a Vercel login to view the site. Note: *Password
      Protection* is Pro-only.
- [ ] **Web Analytics**: enable in the project (Hobby includes 50,000 events/month) to see
      dashboard traffic.
- [ ] **Cache control** (optional): the feed sits at a stable `/jobs.json`. Each redeploy
      invalidates Vercel's CDN cache, so a refresh goes live on deploy. If you want to bound
      staleness independently, add a `vercel.json` (see reference below) with a short
      `Cache-Control` on `jobs.json`.

---

## Limits & gotchas (Hobby plan, verified 2026-06)

- **Non-commercial only.** Personal/portfolio use is fine; a real product needs Pro. This is
  the main eligibility risk.
- **100 deployments/day.** HeadStart deploys ~4×/day (every 6 h) — far under the cap, even with
  manual deploys and the bot.
- **~100 GB fast data transfer/month.** With compression the feed is ~1 MB on the wire, so this
  is generous for personal traffic. Watch it only if the dashboard gets popular.
- **Feed size growth is the real scaling limit.** 5.2 MB / ~11,690 jobs today, but the active
  board set is ~23k *currently-hiring* boards — the feed can grow to tens of MB. A single
  static JSON that the browser downloads in full on every load gets slow well before any Vercel
  cap is hit. See "When to outgrow static" below.
- **Git history bloat.** Committing a multi-MB `docs/jobs.json` every 6 h grows repo history
  (~1,460 commits/yr). Tolerable for now; the scaling path below removes it.
- **Vercel clones, doesn't run, your Python.** Don't expect the scraper to run on Vercel. The
  3rd-party scrapers also rely on `curl_cffi`/WARP egress and long runtimes that Vercel's
  serverless model can't provide — keep scraping on GitHub Actions.

---

## When to outgrow the static feed

Stay on the committed-JSON model until the feed feels heavy (slow first paint, or it crosses
~10–20 MB uncompressed). Then, in rough order of effort:

1. **Split + compress the feed.** Paginate `jobs.json` (e.g. by ATS, or `jobs-1.json`,
   `jobs-2.json`, plus a small index the dashboard reads first). Cheapest fix, no new infra.
2. **Move the feed off git into object storage.** Have `scrape.yml` upload the JSON to
   **Vercel Blob** (available on Hobby) or any bucket, and have the dashboard fetch from there.
   Stops the git bloat; site still static.
3. **Real datastore + read API.** Push jobs into **Postgres (Neon/Supabase)** from Actions, and
   add a small Vercel Function to serve filtered/paginated queries. This is the point where a
   tiny bit of Vercel "backend" earns its keep — only adopt it when filtering server-side
   actually matters. Schema evolution stays additive: new columns are nullable (the `Job`
   model already does this for `description`/`experience`/`employment_type`/`salary`), or store
   the row as a JSONB blob.

Don't pre-build any of this. The static path is correct for the MVP.

---

## `vercel.json` reference (optional)

You don't need a `vercel.json` — Root Directory = `docs` in the dashboard is enough. But if you
prefer config-in-repo, add this at the **repo root** and leave Root Directory at default:

```json
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "framework": null,
  "buildCommand": null,
  "outputDirectory": "docs",
  "headers": [
    {
      "source": "/jobs.json",
      "headers": [
        { "key": "Cache-Control", "value": "public, max-age=300, must-revalidate" }
      ]
    }
  ]
}
```

`outputDirectory: "docs"` serves the folder; `framework: null` + `buildCommand: null` skip the
build; the `headers` block bounds feed staleness to 5 minutes (optional). If you set this, do
**not** also set Root Directory = `docs` (pick one mechanism).

---

## Sources

- [Vercel — Configuring a Build](https://vercel.com/docs/builds/configure-a-build) (skip build, Root/Output Directory)
- [Vercel — Hobby Plan](https://vercel.com/docs/plans/hobby) and [Fair-use guidelines](https://vercel.com/docs/limits/fair-use-guidelines) (limits, non-commercial use)
- [Vercel — Deploy Hooks](https://vercel.com/docs/deploy-hooks)
- [Vercel — vercel.json configuration](https://vercel.com/docs/project-configuration)
