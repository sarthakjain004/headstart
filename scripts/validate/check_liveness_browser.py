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

* **Read a stated total, never the rendered card count.** Pagination makes the visible cards a
  floor — Workday renders 20 at a time, so counting them reported 20 for a 282-job board. It
  states the real figure on page one ("282 JOBS FOUND", "1 - 20 of 282 jobs"), and reading that
  is one lookup, exact, and costs no extra requests against a host that rate-limits. Verified
  against the JSON API's own ``total``: 17/282/25, exact on all three. Where an ATS states no
  total the card count is used and *is* a floor — a Board that renders jobs is still Live, just
  under-counted.
* **Personio's "board missing" and "throttled" look alike.** A tenant with no public board
  redirects to Personio's marketing site — but so, apparently, can a request that would have been
  429'd, and the sampled rows 429 over HTTP right now. Since the two are indistinguishable from
  the rendered page, personio deliberately settles UNKNOWN rather than DEAD there: a false DEAD
  carries a 90-day TTL and silently removes a live Board from the Active list, which is far more
  expensive than asking again in three days. recruitee's marketing page *is* mapped to DEAD,
  because its rows do not throttle and the page is positively identified by title.

Safety: this **never writes the ledger unless asked**. `data/validate/liveness/` is committed to
git and is the authoritative Active list, so a browser run — slower, smaller-sampled, easier to
get wrong — defaults to a dry run that prints what it *would* change. Pass ``--apply`` to write.

Two rules bound what ``--apply`` may do, and both exist because this checker is the *less* trusted
of the two:

* **A probe that couldn't tell never overwrites a row that had a verdict.** Every browser failure
  — timeout, crash, unrecognised page — settles UNKNOWN, and Chrome hanging on 50 live boards
  would otherwise rewrite them all ``unknown, jobs=None``. The Active list keeps only ``live`` with
  ``jobs >= 1``, so that is a silent mass deactivation from one bad run. UNKNOWN is written only
  over an already-UNKNOWN row, where it destroys nothing and lets a large backlog advance.
* **Rows this run did not probe are carried through byte-for-byte**, so a partial run is a partial
  update, never a truncation.

Two costs those rules buy. Both are deliberate, and neither is fixed here:

* **A consistently-failing board is re-selected every run** under ``--status live`` / ``--status
  dead``. Its verdict is never overwritten, so ``checked_at`` never moves and the tenant-ordered
  head of the ledger keeps picking the same rows. Grinding through a backlog of *failing* rows
  would need a per-run cursor, which this does not have.
* **``--status unknown`` spends the primary checker's TTL budget.** Bumping ``checked_at`` on an
  UNKNOWN→UNKNOWN row defers `check_liveness.py`'s cheaper, more authoritative HTTP probe of that
  row by ``UNKNOWN_TTL_DAYS`` (3). That is the wrong trade for precisely the rows the evidence
  above says want a paced HTTP re-probe rather than a browser — so keep ``--limit`` small there.

Run:  python scripts/validate/check_liveness_browser.py workable --status unknown --limit 50
      python scripts/validate/check_liveness_browser.py personio --status unknown --apply
      python scripts/validate/check_liveness_browser.py recruitee --status dead --limit 200
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from headstart import liveness

LIVE, DEAD, UNKNOWN = "live", "dead", "unknown"

# Per-ATS: how to reach a tenant's board, and how to read a verdict off the rendered page.
#
# `url` builds the board address from the ledger's tenant/url. `count_js` runs in page context
# after render and must return an integer job count, or -1 for "this is not a board" (which is
# what settles DEAD). Selectors are deliberately broad — these are marketing-grade pages that
# change often, and a missed selector must read as "couldn't tell" (UNKNOWN), never as zero jobs.
#
# Each DEAD tell is a *positive* identification of a not-a-board page — a redirect target or a
# title — never a phrase found anywhere in the body, because a live board quoting that phrase in
# one posting would settle DEAD for 90 days. personio has no DEAD tell at all (see below), so it
# can only ever return a count, 0, or null; that asymmetry is deliberate, not an omission.
_PAGE_PROBES: dict[str, dict] = {
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
            // A tenant with no board 302s to apply.workable.com/oops, titled just "Workable" —
            // no text match catches that, so the redirect target is the tell and it is the only
            // thing that settles DEAD here. Verified 2026-08-15 against a nonexistent tenant.
            if (location.pathname.replace(/\\/$/, "") === "/oops") return -1;
            // Match the title, not the whole body: a live board carrying one expired posting
            // whose blurb says "no longer accepting" would otherwise settle DEAD for 90 days.
            if (/(page not found|doesn.t exist)/i.test(title)) return -1;
            // A real board with nothing open renders the company blurb and no cards, titled
            // "{Company} - Current Openings". But the title ships in the initial HTML, so it
            // proves this IS a board and *not* that the board finished rendering — trusting it
            // alone reports a late-painting board as live-but-empty, and min_jobs=1 then drops
            // it from the Active list. Require the rendered shell before believing a zero.
            const shell = document.querySelector("[data-ui='overview']");
            if (shell && (/current openings/i.test(title) || /^careers at /i.test(t.trim()))) return 0;
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
            // No DEAD sentinel here, deliberately. A missing board and a throttled request both
            // land on Personio's marketing site and are indistinguishable from the rendered
            // page, so this settles UNKNOWN (3-day TTL) rather than risk a false DEAD (90-day
            // TTL) that silently drops a live Board from the Active list.
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
            // The stated total FIRST, never the rendered card count. Workday paginates at 20,
            // so counting cards reports 20 for a 282-job board — and it says the real number
            // right there: "282 JOBS FOUND" / "1 - 20 of 282 jobs". Reading it is one lookup and
            // exact, where walking the pages would be a dozen round trips against a host that
            // already rate-limits us.
            const found = document.querySelector("[data-automation-id='jobFoundText']");
            if (found) {
                const m = (found.innerText || "").match(/([\\d,]+)\\s*JOBS?\\s*FOUND/i);
                if (m) return parseInt(m[1].replace(/,/g, ""), 10);
            }
            const outOf = document.querySelector("[data-automation-id='jobOutOfText']");
            if (outOf) {
                const m = (outOf.innerText || "").match(/of\\s+([\\d,]+)/i);
                if (m) return parseInt(m[1].replace(/,/g, ""), 10);
            }
            // Scoped to the results region, not the whole body: a live board whose posting text
            // happens to contain "0 jobs" would otherwise return 0, and min_jobs=1 drops a
            // zero-count board from the Active list just as surely as a false DEAD would.
            const results = document.querySelector(
                "[data-automation-id='jobResults'], [data-automation-id='searchResults']");
            if (results && /(no jobs found|0 jobs)/i.test(results.innerText || "")) return 0;
            // Title, not body: a live board listing one expired posting ("no longer available")
            // would otherwise settle DEAD and carry a 90-day TTL.
            if (/(page not found|no longer available)/i.test(document.title || "")) return -1;
            // Only now the rendered cards, and only as a floor: a board that renders jobs but
            // states no total is still Live, just under-counted.
            const items = document.querySelectorAll("[data-automation-id='jobTitle']");
            if (items.length) return items.length;
            return null;
        """,
    },
}

# These hosts rate-limit per IP — apply.workable.com hard-429s with a ~20h retry-after, which is
# what created the backlog this script is aimed at. A browser is slow enough that modest
# concurrency is already gentle, but the cap is explicit so it can never be raised by accident.
_MAX_TABS = 4
# Not the effective ceiling: pydoll applies its own 60s command timeout to PageMethod.NAVIGATE and
# ignores this value, so an unreachable host blocks ~60s, not 25 (observed in the smoke test).
# Budget a run at 60s/board worst case, and treat this as the floor it can return early on.
_NAV_TIMEOUT_S = 25
_RENDER_SETTLE_MS = 1200


def _nav_url(built: str) -> str | None:
    """A built board address as something ``go_to`` can navigate to, or None if there is nothing
    navigable in the row.

    The ledger does not normalise scheme, so a row can hold a bare host
    (``foo.jobs.personio.com``). ``go_to`` raises on those, ``_probe`` maps the raise to UNKNOWN,
    and a whole run then reads as a wall rather than as an address that was never valid — the
    failure mode this module warns about twice elsewhere. Measured 2026-08-15: of a 300-board
    sample, all 103 errors were bare hosts and all 197 rows carrying a scheme returned a verdict.

    Counted through the per-ATS builders rather than off the raw column, because only three of the
    four pass the stored URL through: **personio 3,292, recruitee 225, workday 5 — 3,522 rows**.
    workable is unaffected at any count, since its builder derives the address from the tenant and
    ignores the stored URL entirely. Of the 3,522, only 30 are ``unknown``; the rest are ``live``
    (2,507) or ``dead`` (985), so this mostly unblocks *re-verifying* boards, not the backlog.

    Returns None for a row with no usable address — workday's builder yields ``"/"`` when the
    ledger stores no URL, and a workday tenant has no host to derive one from.
    """
    url = built.strip()
    if url == "/":
        return None
    return url if url.startswith(("http://", "https://")) else f"https://{url}"


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
    target = _nav_url(spec["url"](tenant, url))
    if target is None:
        return UNKNOWN, None  # nothing navigable in the ledger row
    try:
        await tab.go_to(target, timeout=_NAV_TIMEOUT_S)
        try:
            await tab.find(css_selector=spec["ready"], timeout=8, raise_exc=False)
        except Exception:  # noqa: BLE001, S110 - a missing anchor is not a verdict
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

    spec = _PAGE_PROBES[ats]
    options = ChromiumOptions()
    for flag in (
        "--headless=new",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
    ):
        options.add_argument(flag)

    probed: dict[str, tuple[str, int | None]] = {}
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
            probed[row.tenant] = (status, jobs)
            done += 1
            # Per item, flushed: a long batch that reports only at the end hides a probe that
            # started failing on row 3, and one slow board must not stall all visibility.
            print(
                f"  [{done}/{len(rows)}] {row.tenant[:34]:34} {status:<7} "
                f"{'' if jobs is None else f'{jobs} jobs'}",
                flush=True,
            )

        await asyncio.gather(*(one(r) for r in rows))
    return probed


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("ats", choices=sorted(_PAGE_PROBES), help="which ATS to re-probe")
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

    if not 1 <= args.tabs <= _MAX_TABS:
        print(
            f"refusing --tabs {args.tabs}: these hosts rate-limit per IP and a burst is what "
            f"created this backlog (allowed 1-{_MAX_TABS})",
            flush=True,
        )
        return 2

    path = liveness.path_for(args.root, args.ats)
    ledger = liveness.load(path)
    if not ledger:
        print(f"no ledger at {path}", flush=True)
        return 1

    # Past its TTL, not merely matching the status: without this the run re-probes the same
    # head-of-ledger rows every time and a 21k backlog never advances.
    today = datetime.now(UTC).date()
    rows = [
        v
        for v in ledger.values()
        if v.status == args.status and liveness.needs_probe(v, today)
    ][: args.limit]
    if not rows:
        print(
            f"{args.ats}: no rows with status={args.status} are due a re-probe",
            flush=True,
        )
        return 0
    print(
        f"{args.ats}: {len(rows)} board(s) with status={args.status}, {args.tabs} tabs"
        f"{'' if args.apply else '  [DRY RUN — pass --apply to write]'}",
        flush=True,
    )

    probed = asyncio.run(_run(args.ats, rows, args.tabs))

    # A probe that couldn't tell must never overwrite a row that had a verdict. Chrome hanging
    # on 50 live boards would otherwise rewrite them all `unknown, jobs=None`, and the Active
    # list keeps only `live` with `jobs >= 1` — a silent mass deactivation from one bad run.
    # Writing UNKNOWN over an already-UNKNOWN row destroys nothing and lets the backlog advance.
    settled = {
        tenant: (status, jobs)
        for tenant, (status, jobs) in probed.items()
        if status != UNKNOWN or ledger[tenant].status == UNKNOWN
    }
    counts = Counter(status for status, _ in probed.values())
    # Against the stored verdict, not against --status: a board going live,7 -> live,21 is a real
    # write, and counting only status flips made the dry run under-report what --apply would do.
    changed = sum(
        1
        for tenant, verdict in settled.items()
        if verdict != (ledger[tenant].status, ledger[tenant].jobs)
    )
    print(
        f"\n{args.ats}: {len(probed)} probed -> "
        + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        + f"; {changed} row(s) would change"
        + f", {len(probed) - len(settled)} left untouched (couldn't tell)",
        flush=True,
    )

    if not args.apply:
        print("dry run — ledger untouched", flush=True)
        return 0

    today_iso = today.isoformat()
    for tenant, (status, jobs) in settled.items():
        old = ledger[tenant]
        ledger[tenant] = liveness.Verdict(
            old.ats, old.tenant, old.url, status, jobs, today_iso
        )
    liveness.write(path, ledger.values())
    print(f"wrote {path} ({len(ledger)} rows, {len(settled)} re-probed)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
