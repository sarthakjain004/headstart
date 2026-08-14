#!/usr/bin/env python3
"""Liveness checker that probes through a real browser, for boards HTTP probing can't settle.

`check_liveness.py` is the primary checker and stays so: it probes each ATS's JSON endpoint with
``curl_cffi``, which is orders of magnitude cheaper than a browser and settles the overwhelming
majority of boards. This is the escalation *below* it — a real Chrome driven over CDP (pydoll),
for tenants whose verdict the cheap probe cannot reach.

**Read this before running it, because the evidence says it will recover less than you expect.**
Sampled 2026-08-15 against the four ATSes it supports, the stuck rows break down as:

  workable   21,428 UNKNOWN (95%) — the widget API answers 200 *right now* for every sampled
             tenant. These are stale: one ``--force`` run tripped apply.workable.com's per-IP
             Cloudflare 429 (~20h retry-after) and blanked ~16k boards in a day. They need a
             paced re-probe, not a browser; a browser is the same IP making more requests.
  personio    4,555 UNKNOWN (48%) — every sampled tenant returns 429. A browser gets no
             different quota. Pacing is the fix.
  workday     1,602 UNKNOWN (5%)  — the ``/wday/cxs/{co}/{site}/jobs`` JSON API answers 200 for
             the sampled unknowns. The HTML is a JS shell, but the probe never reads the HTML.
  recruitee       8 UNKNOWN (0%)  — nothing to recover. Its 4,281 DEAD rows return 200 with
             Recruitee's generic "Customizable ATS & hiring software" marketing page, i.e. the
             tenant has no public board. Those verdicts are correct; a browser renders the same
             marketing page.

So the browser earns its cost only where a *client-shaped* wall is real — a host that serves a
genuine Chrome and refuses ``curl_cffi`` from the same IP, which is what darwinbox does
(`docs/darwinbox/cloudflare-wall.md`). None of the four above demonstrably do. Kept and supported
because that can change per host at any time, and because proving a wall is *not* client-shaped is
worth doing once with a real browser rather than assuming.

Two limits of reading a rendered page rather than an API, both load-bearing:

* **Job counts are a floor, not a total.** The browser counts what the first page rendered, and
  Workday paginates at 20 — a 500-job board reports 20. The HTTP probe reads the API's own
  ``total`` and is authoritative for counts; prefer it whenever it can settle the row at all.
  What the browser is good for is the *status*, not the number.
* **Personio's "board missing" and "throttled" look alike.** A tenant with no public board
  redirects to Personio's marketing site — but so, apparently, can a request that would have been
  429'd, and the sampled rows 429 over HTTP right now. Since the two are indistinguishable from
  the rendered page, personio deliberately settles UNKNOWN rather than DEAD there: a false DEAD
  carries a 90-day TTL and silently removes a live Board from the Active list, which is far more
  expensive than asking again in three days. recruitee's marketing page *is* mapped to DEAD,
  because its rows do not throttle and the page is positively identified by title.

Safety: this **never writes the ledger unless asked**. `data/validate/liveness/` is committed to
git and is the authoritative Active list, so a browser run — slower, smaller-sampled, easier to
get wrong — defaults to a dry run that prints what it *would* change. Pass ``--apply`` to write,
and only rows this run actually settled are touched; every other row is carried through untouched.

Run:  python scripts/validate/check_liveness_browser.py workable --status unknown --limit 50
      python scripts/validate/check_liveness_browser.py personio --status unknown --apply
      python scripts/validate/check_liveness_browser.py recruitee --status dead --limit 200
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from headstart import liveness  # noqa: E402

LIVE, DEAD, UNKNOWN = "live", "dead", "unknown"

# Per-ATS: how to reach a tenant's board, and how to read a verdict off the rendered page.
#
# `url` builds the board address from the ledger's tenant/url. `count_js` runs in page context
# after render and must return an integer job count, or -1 for "this is not a board" (which is
# what settles DEAD). Selectors are deliberately broad — these are marketing-grade pages that
# change often, and a missed selector must read as "couldn't tell" (UNKNOWN), never as zero jobs.
_ATS: dict[str, dict] = {
    "workable": {
        "url": lambda t, u: f"https://apply.workable.com/{t}/",
        "ready": "[data-ui='job'], [data-ui='overview'], main",
        "count_js": """
            const cards = document.querySelectorAll("[data-ui='job'], li[data-ui='job']");
            if (cards.length) return cards.length;
            const anchors = document.querySelectorAll("a[href*='/j/']");
            if (anchors.length) return anchors.length;
            const title = document.title || "";
            const t = document.body.innerText || "";
            if (/(page not found|doesn.t exist|no longer)/i.test(t)) return -1;
            // A real board with nothing open renders the company blurb and no cards, titled
            // "{Company} - Current Openings". Confirm it IS a board before returning 0, or a
            // failed render would be recorded as a live-but-empty board — a false LIVE, which
            // is the one verdict this checker must never invent.
            if (/current openings/i.test(title) || /^careers at /i.test(t.trim())) return 0;
            if (/no (open )?(jobs|positions)/i.test(t)) return 0;
            return null;
        """,
    },
    "personio": {
        "url": lambda t, u: (u or f"https://{t}.jobs.personio.de").rstrip("/") + "/",
        "ready": "[data-testid='job-list'], .job-list, main, body",
        "count_js": """
            const cards = document.querySelectorAll(
                "[data-testid='job-item'], a[href*='/job/'], .job-box, .jobs-list li");
            if (cards.length) return cards.length;
            const t = document.body.innerText || "";
            if (/(keine offenen|no open positions|no vacancies)/i.test(t)) return 0;
            if (/(nicht gefunden|not found|404)/i.test(t)) return -1;
            return null;
        """,
    },
    "recruitee": {
        "url": lambda t, u: (u or f"https://{t}.recruitee.com").rstrip("/") + "/",
        "ready": "main, body",
        # Recruitee serves its own marketing site for a tenant with no public board — same 200,
        # same shell. Its title is the tell, and it is what makes those rows DEAD rather than
        # unknown. Checked before counting, or the marketing page's nav links look like jobs.
        "count_js": """
            const title = (document.title || "").toLowerCase();
            if (title.includes("customizable ats") || title.includes("hiring software")) return -1;
            const cards = document.querySelectorAll(
                "a[href*='/o/'], [data-testid='offer'], .offer, .job-card");
            if (cards.length) return cards.length;
            const t = document.body.innerText || "";
            if (/no (open )?(jobs|positions|vacancies)/i.test(t)) return 0;
            return null;
        """,
    },
    "workday": {
        "url": lambda t, u: (u or "").rstrip("/") + "/",
        "ready": "[data-automation-id='jobResults'], [data-automation-id='searchResults'], body",
        "count_js": """
            const items = document.querySelectorAll("[data-automation-id='jobTitle']");
            if (items.length) return items.length;
            const total = document.querySelector("[data-automation-id='jobFoundText']");
            if (total) {
                const m = (total.innerText || "").match(/([\\d,]+)/);
                if (m) return parseInt(m[1].replace(/,/g, ""), 10);
            }
            const t = document.body.innerText || "";
            if (/(no jobs found|0 jobs)/i.test(t)) return 0;
            if (/(page not found|no longer available)/i.test(t)) return -1;
            return null;
        """,
    },
}

# These hosts rate-limit per IP — apply.workable.com hard-429s with a ~20h retry-after, which is
# what created the backlog this script is aimed at. A browser is slow enough that modest
# concurrency is already gentle, but the cap is explicit so it can never be raised by accident.
_MAX_TABS = 4
_NAV_TIMEOUT_S = 25
_RENDER_SETTLE_MS = 1200


def _script_value(response):
    """The JS return value out of CDP's ``Runtime.evaluate`` envelope.

    pydoll hands back the raw protocol reply, which nests twice —
    ``{"id": N, "result": {"result": {"type": "number", "value": 3}}}``. Unwrapping only one
    level silently yields ``None``, which this script reads as "couldn't tell" — so every board
    settles UNKNOWN and the run looks like a wall rather than a parsing mistake. Tolerates a bare
    value too, in case a pydoll version returns one.
    """
    if not isinstance(response, dict):
        return response
    inner = response.get("result", response)
    if isinstance(inner, dict) and "result" in inner:
        inner = inner["result"]
    return inner.get("value") if isinstance(inner, dict) else inner


async def _probe(tab, spec: dict, tenant: str, url: str) -> tuple[str, int | None]:
    """One board through an already-open tab. Returns a ``(status, jobs)`` verdict.

    Never raises: a browser failure is exactly the "couldn't tell" case the three-state model
    exists for, so it settles UNKNOWN and the row is re-probed on its TTL like any other.
    """
    try:
        await tab.go_to(spec["url"](tenant, url), timeout=_NAV_TIMEOUT_S)
        try:
            await tab.find(css_selector=spec["ready"], timeout=8, raise_exc=False)
        except Exception:  # noqa: BLE001 - a missing anchor is not a verdict
            pass
        await asyncio.sleep(_RENDER_SETTLE_MS / 1000)
        count = _script_value(
            await tab.execute_script(f"(() => {{{spec['count_js']}}})()")
        )
        if count is None:
            return UNKNOWN, None  # rendered, but nothing we recognise — do not guess
        count = int(count)
        return (DEAD, None) if count < 0 else (LIVE, count)
    except Exception:  # noqa: BLE001 - transport/timeout/crash all mean "couldn't tell"
        return UNKNOWN, None


async def _run(
    ats: str, rows: list[liveness.Verdict], tabs: int
) -> dict[str, tuple[str, int | None]]:
    """Probe every row through one browser, ``tabs`` at a time. Results keyed by tenant."""
    from pydoll.browser.chromium import Chrome
    from pydoll.browser.options import ChromiumOptions

    spec = _ATS[ats]
    options = ChromiumOptions()
    for flag in (
        "--headless=new",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
    ):
        options.add_argument(flag)

    settled: dict[str, tuple[str, int | None]] = {}
    done = 0
    async with Chrome(options=options) as browser:
        await browser.start()
        gate = asyncio.Semaphore(tabs)

        async def one(row: liveness.Verdict) -> None:
            nonlocal done
            async with gate:
                tab = await browser.new_tab()
                try:
                    status, jobs = await _probe(tab, spec, row.tenant, row.url)
                finally:
                    await tab.close()
            settled[row.tenant] = (status, jobs)
            done += 1
            # Per item, flushed: a long batch that reports only at the end hides a probe that
            # started failing on row 3, and one slow board must not stall all visibility.
            print(
                f"  [{done}/{len(rows)}] {row.tenant[:34]:34} {status:<7} "
                f"{'' if jobs is None else f'{jobs} jobs'}",
                flush=True,
            )

        await asyncio.gather(*(one(r) for r in rows))
    return settled


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("ats", choices=sorted(_ATS), help="which ATS to re-probe")
    ap.add_argument(
        "--status",
        default=UNKNOWN,
        choices=[LIVE, DEAD, UNKNOWN],
        help="which existing verdict to re-probe (default: unknown)",
    )
    ap.add_argument(
        "--limit", type=int, default=50, help="max boards this run (default: 50)"
    )
    ap.add_argument(
        "--tabs",
        type=int,
        default=_MAX_TABS,
        help=f"concurrent tabs (default: {_MAX_TABS})",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="write the ledger; without it this is a dry run that only reports",
    )
    ap.add_argument(
        "--root", default=".", help="repo root holding data/validate/liveness"
    )
    args = ap.parse_args()

    if args.tabs > _MAX_TABS:
        print(
            f"refusing --tabs {args.tabs}: these hosts rate-limit per IP and a burst is what "
            f"created this backlog (cap {_MAX_TABS})",
            flush=True,
        )
        return 2

    path = liveness.path_for(args.root, args.ats)
    ledger = liveness.load(path)
    if not ledger:
        print(f"no ledger at {path}", flush=True)
        return 1

    rows = [v for v in ledger.values() if v.status == args.status][: args.limit]
    if not rows:
        print(f"{args.ats}: no rows with status={args.status}", flush=True)
        return 0
    print(
        f"{args.ats}: {len(rows)} board(s) with status={args.status}, {args.tabs} tabs"
        f"{'' if args.apply else '  [DRY RUN — pass --apply to write]'}",
        flush=True,
    )

    settled = asyncio.run(_run(args.ats, rows, args.tabs))

    changed = {t: v for t, (s, _) in settled.items() if (v := s) != args.status}
    counts: dict[str, int] = {}
    for status, _ in settled.values():
        counts[status] = counts.get(status, 0) + 1
    print(
        f"\n{args.ats}: {len(settled)} probed -> "
        + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        + f"; {len(changed)} would change from {args.status}",
        flush=True,
    )

    if not args.apply:
        print("dry run — ledger untouched", flush=True)
        return 0

    today = date.today().isoformat()
    for tenant, (status, jobs) in settled.items():
        old = ledger[tenant]
        # Only rows this run actually settled are rewritten; everything else is carried through
        # exactly as it was, so a partial run can never blank the ledger.
        ledger[tenant] = liveness.Verdict(
            old.ats, old.tenant, old.url, status, jobs, today
        )
    liveness.write(path, ledger.values())
    print(f"wrote {path} ({len(ledger)} rows, {len(settled)} re-probed)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
