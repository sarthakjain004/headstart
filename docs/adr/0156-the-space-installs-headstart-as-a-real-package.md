# ADR-0156: The Space installs `headstart` as a real package

**Status:** accepted · **Date:** 2026-09-15 · **Relates to:** ADR-0020 (free-tier deployment), ADR-0042 (`search.JobSearch` as the one serving path)

## Context

The slice of `headstart` the deployed Space serves was declared in three places that had to
independently agree, and it broke silently twice: `.github/workflows/deploy-space.yml` held a
hardcoded `cp` list of files/dirs (`facets.py`, `fx.py`, `geo.py`, `llm_router.py`,
`profile_extract.py`, `search.py`, `alerts/**`, `ui/**`, plus three `config/*.json` files),
`deploy/hf-space/Dockerfile` held a separate, independently-maintained `COPY` line naming an
overlapping-but-not-identical set, and six modules carried a
`try: from headstart import X / except ImportError: import X` (or, for logging, a
`logging.getLogger` stand-in for `headstart.log.get`) shim — because the flat-copy layout meant
`headstart` was never a real installed package inside the Space image. #115 and #128 both shipped
a green CI run, a booting Space, and one feature that quietly did nothing: `role_families.json`
and `role_watchlist.json` respectively were synced by the workflow but never `COPY`'d by the
Dockerfile, so the trends panel silently lost its labels and its by-role drill.

Separately, the same three-way split had already let `deploy/hf-space/app.py` (the Space) and
`scripts/ui/serve.py` (local dev) drift: both build a near-identical `render_template("base.html",
...)` context by hand, and `app.py:has_min_salary=_searcher.has_min_salary_annual` had no
counterpart in `serve.py` — Jinja's `Undefined` is falsy, so the "Highest salary" sort option
silently vanished from local dev only. Auditing the two context blocks for this PR found a second,
independent instance of the identical bug: `serve.py`'s `cfg` dict (which becomes `window.CFG`,
read by `app.js` client-side) was missing `keyword_scopes` and `keyword_default_scope`, so the
Keyword filter's scope `<select>` silently lost its "needs description" disabling and its
default-scope comparison in local dev only.

## Decision

**Make the Space install `headstart` as a real Python package**, collapsing the three-way
declaration to one. `deploy-space.yml`'s staging step no longer curates a file list: it does two
whole-directory copies, `cp -r src/headstart deploy/hf-space/headstart` and
`cp -r config deploy/hf-space/config`, into the Space repo it is about to push. The Dockerfile
mirrors that with two whole-directory `COPY`s, `COPY headstart ./headstart` and
`COPY config ./config`. Because Docker's build context for this image *is* the Space's own repo
(the workflow's `upload_folder` pushes exactly the `deploy/hf-space/` tree it just staged, not the
whole monorepo), there is no way to `pip install` from the real `pyproject.toml` without either
shipping it into the Space repo too or fabricating one there — direct package-shaped directory
copies are the simpler mechanism that gets the same result, detailed in Options below.

At runtime, `WORKDIR /app` puts the container's cwd — and so `sys.path[0]` — at `/app`, and
`/app/headstart/__init__.py` makes `headstart` a genuine importable package from there, exactly as
it already is via `pyproject.toml`'s `packages = ["src/headstart"]` in the repo and via pytest's
`pythonpath = ["src"]` under test. `deploy/hf-space/app.py` now imports it precisely the way
`scripts/ui/serve.py` already did — `from headstart import facets, fx, geo, llm_router,
profile_extract, search` and `from headstart.alerts import access, identity` — so the six
dual-import/logging shims (`search.py` ×2, `facets.py` ×1, plus the `logging.getLogger` stand-ins
in `search.py`, `fx.py`, `headstart/alerts/store.py`) are deleted outright; nothing conditional
remains to keep in sync. `fx.py`'s existing `config/fx_rates.json` ancestor-walk needed no code
change at all — `/app` is still an ancestor of `/app/headstart/fx.py`, so it finds
`/app/config/fx_rates.json` unmodified (verified by staging the exact Docker layout locally and
importing it — see the PR). `app.py`'s two remaining flat-file reads (`role_families.json`,
`role_watchlist.json`) move from `Path(__file__).with_name(...)` to `Path(__file__).parent /
"config" / ...`, matching where the wholesale `config/` copy actually puts them. Templates/static,
previously their own synced/`COPY`'d pair, now ride inside the package at `headstart/ui/` —
`app.py` locates them via `Path(headstart.__file__).parent / "ui"`, the same expression
`serve.py` now uses, so both entry points resolve the UI's location identically rather than one
hand-rolling a fallback for "the synced copy isn't here in a repo checkout."

**Fix both `has_min_salary`-shaped drifts as a direct, in-scope consequence.** `serve.py` gains
`has_min_salary=_searcher.has_min_salary_annual` and the two missing `cfg` keys
(`keyword_scopes`, `keyword_default_scope`), computed from the same `scopes =
keyword_scope_options()` call `app.py` already makes. This is not a separate, deferred patch:
the packaging fix and the drift fix share one root cause (two hand-written context blocks with
nothing checking they agree), so both land together. `tests/test_space_deploy_sync.py` gains an
AST-based check that `app.py` and `serve.py` pass the same top-level kwargs *and* the same `cfg`
keys to `base.html` — the check that would have caught both bugs before they shipped, replacing
the file-list/shim checks the old declaration needed.

## Options considered

| | | for | against |
|---|---|---|---|
| **A (taken)** | Whole-directory copy: `headstart/` and `config/` land in the image as real, unmodified directories (no `pip`, no wheel build) | zero new build-time machinery — no `pyproject.toml`/`README.md` needed in the Space repo, no hatchling/PEP-517 step, nothing that can fail on a build-context git-detection quirk; `headstart` is importable purely because `WORKDIR /app` + `__init__.py` make it one; verified end-to-end by staging the literal `/app` layout locally and running real imports against it | ships the whole `src/headstart` tree (`scrapers/`, `ingest/`, `resume_mcp/` included) into the image, unused; not literally `pip`-installed, so no package metadata (`importlib.metadata.version`, `pip list`) exists for it |
| B | `pip install --no-deps .` — Dockerfile builds and installs the real wheel via `pyproject.toml`'s `packages = ["src/headstart"]` | genuinely "the repo's own package," with real metadata; the packaging *tooling* enforces what ships rather than a directory copy | needs `pyproject.toml` + `README.md` staged into the Space repo too (the `readme` field points at it); hatchling's default file-selection leans on VCS tracking, which the Space's build context — `deploy/hf-space/` alone, no `.git` — may not satisfy predictably; a new install-time dependency (hatchling, PEP-517 build isolation) with unmeasured build-time cost, for a live production image, against a rule this task states explicitly: be conservative and don't make the build worse |
| C | Generate the workflow's `cp` list and the Dockerfile's `COPY` line from one shared YAML/TOML manifest, checked by a test | keeps today's curated, minimal file set (nothing unused ships) | the manifest *is* a fourth artifact and a third format to keep current — it only prevents the two derived lists from disagreeing with *it*, not the underlying problem that someone must still remember to add a new module to *something*; does nothing for the six import shims, which was the concrete, already-measured pain (#115/#128 were exactly this drift, twice) |

Option A is the pick: it is the only one that both eliminates the shims (the stated, concrete goal)
and adds no new build machinery to a live production image, at a size cost that is unmeasurable
against the baked-in encoder (`src/headstart` is 2.8 MB; the model + torch layer is hundreds of MB
uncompressed). Option B is the "more correct" answer in the abstract — a real `pip`-installed
package — but its risk (an untested build-context assumption, a new tool in the critical path) is
concrete and unbounded while its benefit (package metadata nothing in this codebase reads) is
theoretical. Option C treats the symptom the task named as secondary (a curated list existing at
all) rather than the mechanism that let it drift twice unnoticed (no single thing forced the two
lists, or the six shims, to agree).

## Consequences

- **The deploy trigger widens along with the copy.** `deploy-space.yml`'s `on.push.paths` moved
  from a curated list of the exact files copied to `src/headstart/**` and `config/**`, matching
  the now-unconditional whole-tree copy. A change to a module the Space never imports (say,
  `scrapers/oracle.py`) now triggers a Space rebuild/redeploy that changes nothing user-visible —
  wasted but cheap, since the Dockerfile bakes the expensive encoder layer *before* the
  `COPY headstart`/`COPY config` lines, so only the late, fast layers rebuild. The alternative
  (leave the trigger list curated while the copy goes unconditional) reintroduces a smaller version
  of the same drift risk this ADR removes: a newly-Space-relevant module added under
  `src/headstart/` without a matching trigger-path edit would ship correctly on the *next*
  triggering change, but not immediately on its own — a staleness bug, not a breakage, and
  strictly milder than #115/#128, but still avoidable at a cost measured in idle CI minutes rather
  than incident time.
- **`tests/test_space_deploy_sync.py`'s checks changed shape, not just content.** The old
  per-file-sync and per-shim-fallback checks no longer have anything to compare (there is no
  curated list, and the shims are gone by construction). The new checks assert the two
  whole-directory copies stay paired (workflow stages what the Dockerfile installs) and that
  `app.py`/`serve.py` resolve the same `base.html` context, top-level and inside `cfg` — a
  narrower, more direct test of "does the served slice still match" than counting files.
- **A deeper drift-elimination — one shared context-building function instead of two hand-rolled
  ones — is deliberately not done here.** `app.py`'s `index()` (sign-in wall, trends, alerts,
  résumé sync) and `serve.py`'s (none of those, real search state instead) diverge on enough real
  behavior that merging them risks the wall/trends logic more than the packaging change
  justifies; the new test is the guard against future drift, not a refactor that removes the
  possibility of writing one. Left as a candidate for a future, narrower PR.
- **`start.sh` is untouched and unaffected.** It boots the llm-router tunnel and then always
  `exec python app.py` (ADR-0032's degrade-don't-die contract) — nothing about how `headstart`
  gets into the image changes what it execs or when, so the tunnel/secrets logic needed no
  review beyond confirming this diff carries no changes to it (it doesn't).
- **Verified two ways, without a Docker registry push.** First, the exact `/app` layout (a
  `headstart/` package dir + `config/` dir + `app.py`/`start.sh`, staged the same way the
  workflow would stage them) was built locally and `import headstart; from headstart import fx;
  fx.table()` confirmed the ancestor-walk finds `config/fx_rates.json` unmodified. Second, a full
  `docker build` was run against that identical context end to end (Dockerfile, requirements
  install, encoder bake, the two whole-directory `COPY`s) and completed successfully; the built
  image was also used to confirm `import headstart` and the UI/config paths resolve exactly as
  this ADR describes.
