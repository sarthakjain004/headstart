#!/usr/bin/env python3
"""Fill in full job descriptions for the Wellfound company-page roster.

run_wellfound_company_jobs.py gets every job of every company, but the company page carries only
a ~300-char `descriptionSnippet` (measured: 198-424 chars) against a board median of ~3,500. The
per-job page `/jobs/{id}-{slug}` carries the whole thing — as a JSON-LD `JobPosting`, not
`__NEXT_DATA__` (measured 7,592-9,663 chars on Deepgram listings).

That shape difference is a trap worth stating plainly: `_is_blocked()` reads a page as blocked
when `__NEXT_DATA__` is absent, and detail pages never have it, so the default predicate marks
every one of them blocked and burns `_load_page`'s full 20x2s retry budget before returning html
that was fine from the start. Measured 41s/page that way, 0.2s with `is_challenged` below. Only a
real challenge is worth waiting out — a resolved page with no JobPosting is a *dead listing*, and
waiting for a posting that will never arrive cost another 62s on the first closed job we hit.

`desc_source` on every written row records where its description came from:
  * `board`   — the role-board sweep already had the full text for a job it saw (free)
  * `detail`  — fetched from /jobs/{id}-{slug}: JSON-LD `JobPosting`, or the rendered
                `#job-description` block for the live listings that serve no JSON-LD
  * `snippet` — the tech gate rejected the title, so no fetch was spent (not indexed anyway)
  * `gone`    — listing closed/404: the page resolved with neither source on it

Rows that were challenged past the solve budget, or whose page returned a *different* job's
posting, are deliberately left unwritten so the next `--append` retries them — better a missing
row than a frozen snippet or one job's description on another job's row.

The tech gate is `headstart.tech_filter.is_tech`, the same recall-biased classifier the pipeline
uses (ADR-0017) — here it decides only *which pages are worth fetching*, a cost reducer like the
source query, never the authoritative filter. `filter_tech` still decides what ships. Note the
company page has no `primaryRoleTitle`, so `department` is only available for jobs the board also
saw; for the rest the gate runs on title alone.

Standing rule: only ever request Wellfound through Cloudflare WARP.

Resume: the output CSV is its own checkpoint — every finished row is flushed, and --append seeds
the done-set from it, so a hard block resumes from the last fetched job rather than restarting.

`--all-titles` is the escape hatch for rows that still lack a full description: it fetches every
job regardless of title, and re-attempts anything a previous run left at `snippet` or `gone`.
Those are deliberately excluded from the done-set, and because --append only ever appends, such
a row is written a SECOND time with its full description. The file's contract is therefore:
**for a duplicated id, the LAST row wins** — it is the one with the better description. A plain
--append run (no --all-titles) never duplicates.

Run:  python scripts/scrape/run_wellfound_job_details.py
          [--headless] [--append] [--limit N] [--delay S] [--jitter S]
          [--all-titles] [--audio-first] [--proxy socks5://host:port]
"""

import asyncio
import csv
import html as htmllib
import json
import random
import re
import sys

import datadome_slider
from datadome_slider import audio_ready
from pydoll.browser import Chrome
from run_wellfound import (
    EXP,
    ROOT,
    HardBlocked,
    _captcha_frame_html,
    _flag,
    _lf,
    _load_page,
    _options,
    is_hard_block,
)
from run_wellfound import (
    OUT as BOARD_CSV,
)
from run_wellfound_company_jobs import COLS, warp_on_via
from run_wellfound_company_jobs import OUT as ROSTER_CSV
from run_wellfound_sweep import warp_on

sys.path.insert(0, str(ROOT / "src"))
from headstart.tech_filter import is_tech

OUT = ROOT / "data" / "jobs" / "wellfound_full.csv"

LD_RE = re.compile(
    r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.DOTALL
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\n{3,}")


def ld_jobposting(page_html: str) -> dict | None:
    """The JSON-LD `JobPosting` block, or None. A detail page carries several ld+json blocks
    (BreadcrumbList and friends); only the JobPosting one has the description."""
    for m in LD_RE.finditer(page_html):
        try:
            d = json.loads(m.group(1))
        except Exception:  # noqa: BLE001, S112
            continue
        if isinstance(d, dict) and d.get("@type") == "JobPosting":
            return d
    return None


def is_challenged(page_html: str) -> bool:
    """True only while DataDome/Cloudflare is in the way — the one thing worth waiting out.

    Deliberately NOT keyed on __NEXT_DATA__ (detail pages have none) and NOT on the JobPosting
    either: a resolved page with no JobPosting is a *dead listing*, not a block. Waiting for a
    posting that will never appear cost 62s on the first closed job we hit
    (4486544-principal-customer-success-engineer, a 404 with no captcha markers at all).
    """
    return "captcha-delivery.com" in page_html or "Just a moment" in page_html


def posting_matches(ld: dict, job_id: str) -> bool:
    """Guard against attributing one job's description to another.

    `identifier.value` is `{id}-{slug}`. A stale read (page not yet swapped after go_to) would
    otherwise silently write the previous job's description onto this row — a corruption that
    would be invisible in a 30k-row run.
    """
    ident = ld.get("identifier")
    value = ident.get("value") if isinstance(ident, dict) else None
    return bool(value) and str(value).split("-", 1)[0] == str(job_id)


def description_html(page_html: str) -> str | None:
    """Inner html of the page's `#job-description` block, or None.

    The fallback for live listings that serve no JSON-LD — which do exist: Checkfront's
    `4476061-staff-software-engineer` renders fine, says "Actively Hiring", and has no
    JobPosting block at all. Treating those as dead would silently drop real descriptions.
    Verified byte-identical to the JSON-LD text on a page carrying both (8,711 chars each).
    """
    m = re.search(r'<(\w+)[^>]*\bid="job-description"[^>]*>', page_html)
    if not m:
        return None
    tag, start, depth = m.group(1), m.end(), 1
    token = re.compile(rf"</?{re.escape(tag)}\b[^>]*>", re.IGNORECASE)
    i = start
    while depth:
        t = token.search(page_html, i)
        if not t:
            return None
        depth += -1 if t.group(0).startswith("</") else 1
        i = t.end()
        if depth == 0:
            return page_html[start : t.start()]
    return None


def html_to_text(raw: str | None) -> str | None:
    """JSON-LD descriptions are HTML; the board's are markdown. Flatten the HTML to plain text so
    both land in the same column as readable prose (block tags -> newlines, then strip)."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    t = re.sub(r"(?i)<br\s*/?>", "\n", raw)
    t = re.sub(r"(?i)</(p|div|li|ul|ol|h[1-6]|tr)>", "\n", t)
    t = re.sub(r"(?i)<li[^>]*>", "- ", t)
    t = _TAG_RE.sub("", t)
    t = htmllib.unescape(t)
    t = "\n".join(line.rstrip() for line in t.splitlines())
    return _WS_RE.sub("\n\n", t).strip() or None


async def load_detail(tab, url: str, browser) -> str:
    """Navigate to a detail page, waiting out / solving only an actual challenge.

    This is `_load_page` with the detail-page ready predicate — the whole reason it grew a
    `blocked=` hook. It returns as soon as the page is not challenged, whether or not a
    JobPosting is on it, so a closed listing costs one load instead of the full retry budget.
    """
    return await _load_page(tab, url, browser, blocked=is_challenged)


def board_descriptions() -> dict[str, tuple[str, str | None]]:
    """job id -> (full description, department) from the role-board sweep, when it has one."""
    if not BOARD_CSV.exists():
        return {}
    csv.field_size_limit(sys.maxsize)
    out: dict[str, tuple[str, str | None]] = {}
    with BOARD_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            desc = (row.get("description") or "").strip()
            if desc:
                out[row["id"]] = (desc, row.get("department") or None)
    return out


def read_roster() -> list[dict]:
    if not ROSTER_CSV.exists():
        return []
    csv.field_size_limit(sys.maxsize)
    with ROSTER_CSV.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def needs_fetch(row: dict, board: dict, all_titles: bool) -> bool:
    """Whether this row is worth spending a live page load on: no free board description, and
    the tech gate says the title is worth it (ADR-0017's `is_tech` as a *cost reducer* only)."""
    if row["id"] in board:
        return False  # already have the full text for free
    return all_titles or is_tech(row.get("title"), row.get("department") or None)


async def resolve_row(
    tab,
    browser,
    row: dict,
    board: dict,
    stats: dict,
    *,
    all_titles: bool,
    delay: float,
    jitter: float,
) -> dict | None:
    """The finished row to write, or None when it must be left for the next ``--append``.

    Everything one job needs, in one place, because two drivers now run it: this module's
    sequential loop and ``run_wellfound_job_details_parallel.py``'s tab fleet. Extracted rather
    than copied — the decisions here are subtle enough (a posting must be proven to belong to
    *this* job; a challenged page must not freeze a snippet) that a second copy would drift.

    Raises :class:`HardBlocked` so the caller decides what a burnt IP means — the sequential
    driver stops, the parallel one stops the whole fleet.
    """
    out = dict(row)
    if row["id"] in board:
        out["description"] = board[row["id"]][0]
        out["desc_source"] = "board"
        out["department"] = out.get("department") or board[row["id"]][1]
        stats["board"] += 1
        return out
    if not needs_fetch(row, board, all_titles):
        out["desc_source"] = "snippet"  # non-tech title: snippet is enough
        stats["snippet"] += 1
        return out

    try:
        page_html = await load_detail(tab, row["url"], browser)
    except Exception as e:  # noqa: BLE001 - one job's fetch must not sink the run
        print(f"  {row['url']} fetch error: {type(e).__name__}", flush=True)
        page_html = ""
    if is_challenged(page_html) and is_hard_block(
        await _captcha_frame_html(tab, browser)
    ):
        raise HardBlocked(f"hard block on {row['url']}")
    if is_challenged(page_html):
        # Still challenged after the solve budget: leave the row unwritten so the next --append
        # retries it, rather than freezing a snippet in place.
        stats["challenged"] += 1
        print(f"  still challenged, will retry: {row['url']}", flush=True)
        await asyncio.sleep(delay + random.random() * jitter)
        return None

    ld = ld_jobposting(page_html)
    job_id = row["id"].rsplit(":", 1)[-1]
    # Either source must demonstrably belong to THIS job before it is written.
    mismatch = (
        not posting_matches(ld, job_id) if ld is not None else job_id not in page_html
    )
    if mismatch:
        # Wrong job's page (stale read): skip rather than write a mismatched description.
        stats["mismatch"] += 1
        print(f"  posting id mismatch, will retry: {row['url']}", flush=True)
        await asyncio.sleep(delay + random.random() * jitter)
        return None

    text = html_to_text(ld.get("description")) if ld else None
    if not text:
        # Live listing with no JSON-LD — read the rendered block instead.
        text = html_to_text(description_html(page_html))
    if text:
        out["description"] = _lf(text)
        out["desc_source"] = "detail"
        stats["detail"] += 1
    else:
        # Resolved page with no posting = the listing is closed/404. Keep the row (it is real
        # history) but mark it so it is never mistaken for a fetched one.
        out["desc_source"] = "gone"
        stats["gone"] += 1
        print(f"  listing gone (no JobPosting): {row['url']}", flush=True)
    await asyncio.sleep(delay + random.random() * jitter)
    return out


async def main() -> int:
    proxy = (
        _flag("--proxy", "") or None
    )  # e.g. socks5://127.0.0.1:40000 (WARP proxy mode)
    if not (warp_on_via(proxy) if proxy else warp_on()):
        where = f"through {proxy}" if proxy else "on the default route"
        print(
            f"ABORT: WARP is not on {where}. Standing rule: never scrape Wellfound on the "
            "residential IP. Connect WARP and retry — or pass --proxy for WARP proxy mode.",
            flush=True,
        )
        return 2
    ok, status = audio_ready()
    print(f"audio fallback: {'OK' if ok else 'MISSING'} — {status}", flush=True)

    headless = "--headless" in sys.argv
    datadome_slider.AUDIO_FIRST = "--audio-first" in sys.argv
    append = "--append" in sys.argv
    all_titles = (
        "--all-titles" in sys.argv
    )  # fetch every job, not just tech-titled ones
    limit = _flag("--limit", 0)
    delay = _flag("--delay", 4.0)
    jitter = _flag("--jitter", 2.0)

    roster = read_roster()
    if not roster:
        print(
            f"ABORT: no roster — run run_wellfound_company_jobs.py first ({ROSTER_CSV}).",
            flush=True,
        )
        return 2
    board = board_descriptions()
    print(
        f"roster: {len(roster)} jobs | board already has {len(board)} full descriptions",
        flush=True,
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    EXP.mkdir(parents=True, exist_ok=True)

    done: set[str] = set()
    if append and OUT.exists():
        csv.field_size_limit(sys.maxsize)
        widened = 0
        with OUT.open(encoding="utf-8") as fr:
            for row in csv.DictReader(fr):
                # --all-titles means "re-attempt everything that still lacks a full description":
                # `snippet` (the title gate refused to spend a fetch) and `gone` (the page had
                # neither JSON-LD nor a #job-description block — which is how a listing that is
                # merely a page shape we haven't met yet would look). Leaving either in the
                # done-set makes widening a no-op and freezes a ~300-char snippet forever, which
                # full-descriptions-are-non-negotiable forbids.
                if all_titles and row.get("desc_source") in ("snippet", "gone"):
                    widened += 1
                    continue
                done.add(row["id"])
        print(f"resume: {len(done)} rows already written", flush=True)
        if widened:
            print(
                f"  --all-titles: re-fetching {widened} rows left at snippet",
                flush=True,
            )

    write_header = not (append and OUT.exists())
    f = OUT.open("a" if append else "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(f, fieldnames=COLS, lineterminator="\n")
    if write_header:
        writer.writeheader()

    todo = [r for r in roster if r["id"] not in done]
    if limit:
        todo = todo[:limit]

    n_fetch = sum(1 for r in todo if needs_fetch(r, board, all_titles))
    print(
        f"to write {len(todo)} rows; {n_fetch} need a detail fetch "
        f"({'all titles' if all_titles else 'tech-titled only'}) -> {OUT}",
        flush=True,
    )

    stats = {
        "board": 0,  # description reused from the role-board sweep
        "detail": 0,  # fetched from the job page's JSON-LD
        "snippet": 0,  # non-tech title: not worth a fetch
        "gone": 0,  # listing closed/404 — no posting to fetch
        "challenged": 0,  # unwritten, retried by the next --append
        "mismatch": 0,  # unwritten, retried by the next --append
    }
    async with Chrome(options=_options(headless, proxy)) as browser:
        tab = await browser.start()
        try:
            await tab.enable_auto_solve_cloudflare_captcha()
        except Exception:  # noqa: BLE001, S110
            pass
        for i, row in enumerate(todo):
            try:
                out = await resolve_row(
                    tab,
                    browser,
                    row,
                    board,
                    stats,
                    all_titles=all_titles,
                    delay=delay,
                    jitter=jitter,
                )
            except HardBlocked as exc:
                f.close()
                print(
                    f"\n!! HARD BLOCK on {exc}\n"
                    f"   Stopping. Let it lapse, then resume from this job with:\n"
                    f"     python scripts/scrape/run_wellfound_job_details.py --append",
                    flush=True,
                )
                raise SystemExit(1) from exc
            if out is None:
                continue  # challenged or mismatched: left for the next --append
            writer.writerow(out)
            f.flush()
            if (i + 1) % 25 == 0 or i + 1 == len(todo):
                print(f"  [{i + 1}/{len(todo)}] {stats}", flush=True)
    f.close()
    print(f"\nDONE: {len(todo)} rows -> {OUT}  {stats}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
