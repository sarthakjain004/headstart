#!/usr/bin/env python3
"""Sample real boards to measure how one ATS shows salary (docs/salary-extraction/).

For up to N live boards of ``<ats>`` (``config.load_active_companies`` — the same liveness-ledger
source and dedup every other production consumer uses), fetches the listing response through the
*real* registered scraper (``registry.get_scraper``) and its real ``parse()``, then measures, per
Job: whether ``salary`` came back populated (a structured-field hit), and whether the description
text *looks* like it mentions a figure (``_SALARY_HINT_RE`` — a loose detector for this coarse
measurement pass only; it is not the extractor. ``headstart.salary`` is what actually parses a
figure out, built from what this script finds).

Listing-only ATSes (``has_detail_pass = False``) get one cheap request per board — the whole
sample. A detail-pass ATS needs its own bounded adapter in ``_DETAIL_ADAPTERS`` (at most
``_DETAIL_FETCH_CAP`` per-job detail fetches per board, calling the scraper's own endpoint methods
directly rather than its ``fetch_raw()``, which several detail-pass scrapers use for a full
per-board fan-out) built during that ATS's own research pass — see docs/salary-extraction/
README.md. An ATS with neither shape exits with a clear message rather than guessing.

**Spare egress is automatic, never hand-rolled.** Every fetch goes through the scraper's own
``_get()``/``_post()``/``_job_detail()``-style methods, which already carry
``**self._egress()`` — so an ATS with ``egress_fallback_on`` set (workday: ``{429}``) transparently
routes through `headstart.spare_egress`'s WARP fallback the same way the real pipeline does,
reactively, the first time this process meets a wall. No adapter here should ever call
``http.fetch`` directly; that would silently skip it. One real local limitation, verified
2026-08-21: rotation (`systemctl restart warp-svc`) needs systemd and doesn't exist on macOS, so a
local run gets one alternate route per process, not full adaptive rotation — CI still gets that.

Raw captures land in ``experiment/salary-extraction/<ats>/artifacts/<slug>.json`` (the full parsed
Job list, for reading the real API shape) plus one ``coverage_summary_<n>_seed<seed>.json`` per
run (named by board count *and* seed, so neither a small live-verification run nor a same-size
re-verify with a different seed can clobber another run's summary in the same directory). Progress
prints one line per board as it completes (a bounded thread pool via ``as_completed``, never a
blocking map).

``--workers`` (default :data:`_DEFAULT_WORKERS`) bounds board-level concurrency. Higher than a
single ATS's own production per-*tenant* concurrency (e.g. workday's own ``detail_workers = 6``)
is safe here specifically because boards are sampled across many different companies/instances —
workday's own metering is per (source IP, instance host), and ``workday.py`` documents only 18
known ``wdN`` instances (not "hundreds" — corrected 2026-08-21 after code review), each shared by
many companies. 32 workers spread across 18 instances is under 2 concurrent requests per instance
*on average* — a calculated estimate from that documented count, not a measured per-instance load,
since board sampling order isn't guaranteed to spread evenly across instances.

    .venv/bin/python scripts/enrich/salary_sample.py workable
    .venv/bin/python scripts/enrich/salary_sample.py workday --n 500 --workers 48
    .venv/bin/python scripts/enrich/salary_sample.py workable --misses
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from headstart.config import CompanyRef, load_active_companies
from headstart.models import Job
from headstart.scrapers import registry
from headstart.scrapers.base import BaseScraper

ROOT = Path(__file__).resolve().parents[2]
LEDGER_DIR = ROOT / "data" / "validate" / "liveness"
ARTIFACTS_ROOT = ROOT / "experiment" / "salary-extraction"

#: Default board-level concurrency. Deliberately well above any single ATS's own production
#: per-tenant bound (see the module docstring's concurrency note for why that's safe here — 32
#: workers over workday's documented 18 ``wdN`` instances is under 2 concurrent/instance on
#: average, a calculated estimate, not a measured one) — 3000 boards at the old default of 8 took
#: long enough to make a full pass impractical. Override with --workers for a specific ATS if its
#: own rate-limiting turns out to need something gentler.
_DEFAULT_WORKERS = 32

# A coarse "does this text look like it mentions a salary" detector for the measurement pass only
# — currency symbols/codes near digits, magnitude shorthand, and the region-specific phrasings
# this repo's India-strong-segment scope makes common (LPA, CTC, "per annum"). Deliberately loose:
# false positives get sorted out by reading the --misses/hit sample by hand, not by tightening this
# regex, since headstart.salary (not this script) is where real precision belongs.
_SALARY_HINT_RE = re.compile(
    r"""
    (?:[$€£₹]\s?\d[\d,]*\s?[kK]?)                       # $120,000  €50k  ₹8
    | (?:\d[\d,]*\s?[-–to]{1,3}\s?\d[\d,]*\s?[kK]?\s?(?:USD|EUR|GBP|INR|CAD|AUD))
    | (?:\bLPA\b)                                        # Lakhs Per Annum
    | (?:\bCTC\b)                                        # Cost To Company
    | (?:salary|compensation|remuneration)\s+(?:range|of|is|:|between)
    | (?:per\s+annum|annual\s+salary|base\s+salary)
    """,
    re.IGNORECASE | re.VERBOSE,
)


@dataclass
class BoardResult:
    ats: str
    slug: str
    company: str
    jobs: int
    jobs_with_salary_field: int
    jobs_with_desc_hint: int
    jobs_with_either: int
    error: str | None = None


def _sample_boards(ats: str, n: int, seed: int) -> list[CompanyRef]:
    all_companies = [c for c in load_active_companies(LEDGER_DIR) if c.ats == ats]
    if len(all_companies) <= n:
        return all_companies
    return random.Random(seed).sample(all_companies, n)


#: How many per-job detail fetches a detail-pass adapter may spend on one board — the ~3/board
#: cap the sampling design commits to (docs/salary-extraction/README.md). Only the detailed jobs
#: are counted toward coverage stats: an undetailed posting has no description to mine, and
#: lumping it in as a "no signal" job would dilute the measurement with jobs never actually read,
#: not jobs genuinely found to say nothing.
_DETAIL_FETCH_CAP = 3


def _fetch_workday(scraper: BaseScraper) -> list[Job]:
    """Bounded adapter for workday: one listing page via the scraper's own single-page primitive
    (``_post``, offset 0 — never ``fetch_raw()``, which recursively subdivides and paginates the
    *whole* board plus fans out detail fetches over *every* posting found). Detail-fetches only
    the first :data:`_DETAIL_FETCH_CAP` postings, via the scraper's own ``_job_detail``, then
    parses just those — so the returned Jobs are exactly the ones with a real description."""
    scraper._resolve_instance()
    page = scraper._post({}, offset=0)
    postings = (page or {}).get("jobPostings") or []
    sample = postings[:_DETAIL_FETCH_CAP]
    for item in sample:
        detail = scraper._job_detail(item.get("externalPath"))
        item["_detail"] = detail or {}
    return scraper.parse(sample, datetime.now(UTC).isoformat())


def _fetch_smartrecruiters(scraper: BaseScraper) -> list[Job]:
    """Bounded adapter for smartrecruiters: one listing page via the scraper's own single-page
    primitive (``_get()``, no offset — never ``fetch_raw()``, which pages the *whole* board plus
    fans out detail fetches over *every* posting found). Detail-fetches only the first
    :data:`_DETAIL_FETCH_CAP` postings via the scraper's own ``_job_description``, then parses
    just those — so the returned Jobs are exactly the ones with a real description."""
    page = json.loads(scraper._get())
    postings = (page or {}).get("content") or []
    sample = postings[:_DETAIL_FETCH_CAP]
    for item in sample:
        item["_description"] = scraper._job_description(item.get("id"))
    return scraper.parse({**page, "content": sample}, datetime.now(UTC).isoformat())


def _fetch_zoho(scraper: BaseScraper) -> list[Job]:
    """Bounded adapter for zoho: one listing page via the scraper's own single-page primitive
    (``_get()`` — never ``fetch_raw()``). Zoho's shape differs from workday/smartrecruiters:
    the listing page already carries ``Job_Description`` for most tenants (some tenants configure
    their careers site without that column — 28/71 in the scraper's own docstring — and need a
    per-job detail fetch instead). So this adapter detail-fetches up to :data:`_DETAIL_FETCH_CAP`
    of whichever records are actually missing a description (mirroring ``fetch_raw()``'s own
    ``empty`` selection, just capped) — for the common case (description already in the listing)
    this makes zero detail requests at all, cheaper than every other detail-pass ATS sampled so
    far. A record that still has no description afterward (missing inline *and* past the detail
    cap, or a detail fetch that came back empty) is dropped before returning: it was never given a
    chance to show a signal, and keeping it would dilute the coverage measurement with a job
    nobody actually read (the same principle :data:`_DETAIL_FETCH_CAP`'s docstring states, and the
    same shape ``_fetch_workday``/``_fetch_smartrecruiters`` get for free by slicing the postings
    list before ``parse()`` — zoho can't slice its raw HTML page the same way, so this adapter
    filters ``parse()``'s output by job id instead, found via ``code-review`` on PR #238)."""
    page = scraper._get()
    records = scraper._records(page)
    eligible = [
        r
        for r in records
        if r.get("id") and not r.get("Is_Locked") and r.get("Publish", True)
    ]
    missing_desc = [r for r in eligible if not r.get("Job_Description")]
    details = {}
    for r in missing_desc[:_DETAIL_FETCH_CAP]:
        detail = scraper._detail_description(r["id"])
        if detail:
            details[r["id"]] = detail
    keep_ids = {
        r["id"] for r in eligible if r.get("Job_Description") or r["id"] in details
    }
    jobs = scraper.parse(
        {"page": page, "details": details}, datetime.now(UTC).isoformat()
    )
    return [j for j in jobs if j.id.split(":", 2)[2] in keep_ids]


#: ATS -> bounded detail-pass adapter, built per-ATS as that ATS is reached (never assumed for one
#: not yet researched — see the module docstring). Each returns `list[Job]` for at most
#: `_DETAIL_FETCH_CAP` real, detail-fetched jobs.
_DETAIL_ADAPTERS = {
    "workday": _fetch_workday,
    "smartrecruiters": _fetch_smartrecruiters,
    "zoho": _fetch_zoho,
}


def _fetch_one(company: CompanyRef, artifacts_dir: Path) -> BoardResult:
    scraper = registry.get_scraper(company.ats, company.slug, company.name)
    adapter = _DETAIL_ADAPTERS.get(company.ats)
    if scraper.has_detail_pass and adapter is None:
        return BoardResult(
            ats=company.ats,
            slug=company.slug,
            company=company.name or company.slug,
            jobs=0,
            jobs_with_salary_field=0,
            jobs_with_desc_hint=0,
            jobs_with_either=0,
            error="detail-pass ATS: no bounded adapter built yet for this ATS — see module docstring",
        )
    try:
        jobs = adapter(scraper) if adapter else scraper.fetch()
    except Exception as exc:  # noqa: BLE001 - one board's failure must not sink the sample
        return BoardResult(
            ats=company.ats,
            slug=company.slug,
            company=company.name or company.slug,
            jobs=0,
            jobs_with_salary_field=0,
            jobs_with_desc_hint=0,
            jobs_with_either=0,
            error=f"{type(exc).__name__}: {exc}",
        )
    with_field = sum(1 for j in jobs if (j.salary or "").strip())
    with_hint = sum(
        1
        for j in jobs
        if not (j.salary or "").strip() and _SALARY_HINT_RE.search(j.description or "")
    )
    either = sum(
        1
        for j in jobs
        if (j.salary or "").strip() or _SALARY_HINT_RE.search(j.description or "")
    )
    safe_slug = company.slug.replace("/", "_")
    (artifacts_dir / f"{safe_slug}.json").write_text(
        json.dumps([asdict(j) for j in jobs], indent=2, default=str)
    )
    return BoardResult(
        ats=company.ats,
        slug=company.slug,
        company=company.name or company.slug,
        jobs=len(jobs),
        jobs_with_salary_field=with_field,
        jobs_with_desc_hint=with_hint,
        jobs_with_either=either,
    )


def _run_sample(ats: str, n: int, seed: int, workers: int) -> list[BoardResult]:
    boards = _sample_boards(ats, n, seed)
    print(
        f"sampling {len(boards)} live board(s) for {ats} ({workers} workers)",
        flush=True,
    )
    artifacts_dir = ARTIFACTS_ROOT / ats / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    results: list[BoardResult] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_fetch_one, company, artifacts_dir): company
            for company in boards
        }
        for i, future in enumerate(as_completed(futures), 1):
            r = future.result()
            results.append(r)
            status = (
                r.error
                or f"{r.jobs} jobs, {r.jobs_with_salary_field} field, {r.jobs_with_desc_hint} desc-hint"
            )
            print(f"  [{i}/{len(boards)}] {r.slug}: {status}", flush=True)
    return results


def _summarize(ats: str, results: list[BoardResult], seed: int) -> None:
    ok = [r for r in results if r.error is None]
    errored = [r for r in results if r.error is not None]
    total_jobs = sum(r.jobs for r in ok)
    total_field = sum(r.jobs_with_salary_field for r in ok)
    total_hint = sum(r.jobs_with_desc_hint for r in ok)
    total_either = sum(r.jobs_with_either for r in ok)
    boards_with_any = sum(1 for r in ok if r.jobs_with_either > 0)

    print(f"\n===== {ats} coverage summary =====")
    print(f"boards sampled: {len(results)}  ({len(errored)} errored, {len(ok)} ok)")
    if not ok:
        print("no successful boards — nothing to summarize")
        return
    print(f"jobs seen: {total_jobs}")
    if total_jobs:
        print(
            f"  jobs with a structured salary field: {total_field} ({100 * total_field / total_jobs:.1f}%)"
        )
        print(
            f"  jobs with only a description hint:   {total_hint} ({100 * total_hint / total_jobs:.1f}%)"
        )
        print(
            f"  jobs with either:                     {total_either} ({100 * total_either / total_jobs:.1f}%)"
        )
    print(
        f"boards with >=1 job showing either: {boards_with_any}/{len(ok)} ({100 * boards_with_any / len(ok):.1f}%)"
    )

    # Named per run size AND seed (not a fixed "coverage_summary.json") so a small live-
    # verification sample can't silently clobber a large main run's summary, or another
    # differently-seeded verification run of the same size, in the same artifacts directory —
    # all are real, worth keeping (code review finding, PR #235; size-only collided on a same-N
    # re-verify before this fix, caught live while applying it).
    summary_path = (
        ARTIFACTS_ROOT
        / ats
        / "artifacts"
        / f"coverage_summary_{len(results)}_seed{seed}.json"
    )
    summary_path.write_text(
        json.dumps(
            {
                "ats": ats,
                "boards_sampled": len(results),
                "boards_ok": len(ok),
                "boards_errored": len(errored),
                "jobs_seen": total_jobs,
                "jobs_with_salary_field": total_field,
                "jobs_with_desc_hint_only": total_hint,
                "jobs_with_either": total_either,
                "boards_with_any_salary_signal": boards_with_any,
                "results": [asdict(r) for r in results],
            },
            indent=2,
        )
    )
    print(f"\nwrote {summary_path.relative_to(ROOT)}")


def _misses(ats: str, n: int, seed: int) -> None:
    """Re-read already-captured artifacts (no network) and sample jobs with NO salary signal at
    all, for manual reading — the read-then-widen half of the loop, mirroring
    experience_coverage.py --misses."""
    artifacts_dir = ARTIFACTS_ROOT / ats / "artifacts"
    if not artifacts_dir.exists():
        sys.exit(f"no captures at {artifacts_dir} — run a sample first")
    misses: list[dict] = []
    for f in sorted(artifacts_dir.glob("*.json")):
        if f.name.startswith("coverage_summary"):
            continue
        for j in json.loads(f.read_text()):
            has_field = bool((j.get("salary") or "").strip())
            desc = j.get("description") or ""
            has_hint = bool(_SALARY_HINT_RE.search(desc))
            if not has_field and not has_hint and len(desc) >= 200:
                misses.append(j)
    if not misses:
        print("no substantial misses found in captured artifacts")
        return
    sample = random.Random(seed).sample(misses, min(n, len(misses)))
    print(f"{len(misses)} substantial misses — sampling {len(sample)}\n")
    for j in sample:
        print(f"--- {j.get('id')} — {j.get('title')} ---")
        print((j.get("description") or "")[:1200].replace("\n\n", "\n"))
        print()


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("ats")
    ap.add_argument("--n", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument(
        "--workers",
        type=int,
        default=_DEFAULT_WORKERS,
        help=f"concurrent board fetches (default {_DEFAULT_WORKERS})",
    )
    ap.add_argument(
        "--misses", action="store_true", help="read miss samples from prior captures"
    )
    args = ap.parse_args()

    if args.ats not in registry.SCRAPERS:
        sys.exit(f"unknown ats {args.ats!r}; known: {sorted(registry.SCRAPERS)}")

    if args.misses:
        _misses(args.ats, n=8, seed=args.seed)
        return 0

    results = _run_sample(args.ats, n=args.n, seed=args.seed, workers=args.workers)
    _summarize(args.ats, results, args.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
