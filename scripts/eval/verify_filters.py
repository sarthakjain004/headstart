#!/usr/bin/env python3
"""Verify the deployed Space's filters and result integrity against live data.

Fires a battery of semantic queries at the Space's ``/search``, exercising every filter
alone and in combinations, and validates each returned row against the filter's semantics
(a ``remote=true`` result must be remote, a ``posted_within=7`` result must carry an
ISO date inside the window, …). Also generalizes the darwinbox lesson: per-ATS URL-shape
checks (a job link must look like that ATS's job-detail URL, not a board/home page) and
live HTTP spot-checks on a sample of links.

Streams per-check progress and writes a JSON report for analysis:
  data/eval/filter_checks/{UTC timestamp}.json

Run:  python scripts/eval/verify_filters.py [--base https://... ] [--no-http]
Exit: 0 clean, 1 when any check recorded violations.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_BASE = "https://imposeidon-headstart-search.hf.space"
_REPORT_DIR = _ROOT / "data" / "eval" / "filter_checks"

QUERIES = (
    "backend engineer",
    "machine learning engineer",
    "senior frontend react developer",
    "devops kubernetes",
    "data engineer",
    "mobile app developer",
    "security engineer",
    "qa automation engineer",
)

# What each ATS's job-detail URL must look like. A link that matches the ATS but not the
# shape is the darwinbox failure class: it resolves somewhere, just not at the job.
URL_SHAPES = {
    "greenhouse": r"https://(job-boards|boards)\.greenhouse\.io/.+/jobs/\d+",
    "lever": r"https://jobs(\.eu)?\.lever\.co/[^/]+/[0-9a-f-]{36}",
    "ashby": r"https://jobs\.ashbyhq\.com/[^/]+/[0-9a-f-]{36}",
    "recruitee": r"https://.+/o/[^/]+",  # tenants use custom careers domains (careers_url)
    "workable": r"https://apply\.workable\.com/(j/[A-Z0-9]+|[^/]+/j/[A-Z0-9]+)",
    "smartrecruiters": r"https://jobs\.smartrecruiters\.com/[^/]+/\d+",
    "zoho": r"https://[^/]+/jobs/Careers/\d+/.+",
    "darwinbox": r"https://[^/]+/ms/candidatev2/[^/]+/careers/jobDetails/[0-9a-f]+$",
    "keka": r"https://[^.]+\.keka\.com/careers/jobdetails/\d+",
    "teamtailor": r"https://.+/jobs/\d+.*",
    "personio": r"https://.+\.jobs?\.personio\.(com|de)/job/\d+.*",
    "ripplehire": r"https://[^.]+\.ripplehire\.com/candidate/careers/?$",  # board-level: known gap
    "join": r"https://join\.com/companies/[^/]+/.+",
    "rippling": r"https://ats\.rippling\.com/[^/]+/jobs/[0-9a-f-]+",
    "trakstar": r"https://[^.]+\.hire\.trakstar\.com/jobs/[0-9a-z]+/?",
    "workday": r"https://[^.]+\.wd\d+\.myworkdayjobs\.com/.+/job/.+",
}


def _get(base: str, params: dict) -> list[dict]:
    url = f"{base}/search?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=120) as resp:
        return json.load(resp)


def _iso_within(posted_at: str | None, days: int) -> bool:
    if not posted_at or not re.match(r"^\d{4}-\d{2}-\d{2}", posted_at):
        return False
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    return posted_at >= cutoff


def _etype_ok(value: str | None, canonical: str) -> bool:
    v = (value or "").lower()
    return {
        "full-time": ("full" in v or "permanent" in v),
        "part-time": "part" in v,
        "contract": ("contract" in v or "freelance" in v),
        "internship": "intern" in v,
    }[canonical]


def run_checks(base: str, atses: list[str]) -> list[dict]:
    """Every (query × filter) case with a per-row validator; returns check records."""
    cases: list[tuple[str, dict, callable, str]] = []
    for q in QUERIES:
        cases.append((f"baseline [{q}]", {"q": q, "k": 30}, lambda r: True, q))
        cases.append(
            (
                f"remote [{q}]",
                {"q": q, "remote": "true", "k": 30},
                lambda r: r.get("remote") is True,
                q,
            )
        )
        cases.append(
            (
                f"has_salary [{q}]",
                {"q": q, "has_salary": "true", "k": 30},
                lambda r: r.get("salary"),
                q,
            )
        )
    for years in (0, 2, 5):
        cases.append(
            (
                f"max_years={years}",
                {"q": "software engineer", "max_years": years, "k": 40},
                lambda r, y=years: r.get("min_years") is None or r["min_years"] <= y,
                "",
            )
        )
    for days in (1, 7, 30, 90):
        cases.append(
            (
                f"posted_within={days}",
                {"q": "software engineer", "posted_within": days, "k": 40},
                lambda r, d=days: _iso_within(r.get("posted_at"), d),
                "",
            )
        )
    for ats in atses:
        cases.append(
            (
                f"ats={ats}",
                {"q": "software engineer", "ats": ats, "k": 25},
                lambda r, a=ats: r.get("ats") == a,
                "",
            )
        )
    for etype in ("full-time", "part-time", "contract", "internship"):
        cases.append(
            (
                f"etype={etype}",
                {"q": "software engineer", "etype": etype, "k": 30},
                lambda r, e=etype: _etype_ok(r.get("employment_type"), e),
                "",
            )
        )
    for loc in ("berlin", "india", "london", "san francisco"):
        cases.append(
            (
                f"location={loc}",
                {"q": "backend developer", "location": loc, "k": 25},
                lambda r, L=loc: L in (r.get("location") or "").lower(),
                "",
            )
        )
    for co in ("tech", "labs"):
        cases.append(
            (
                f"company~{co}",
                {"q": "software engineer", "company": co, "k": 25},
                lambda r, c=co: c in (r.get("company") or "").lower(),
                "",
            )
        )
    for k in (5, 50, 100):
        cases.append((f"k={k}", {"q": "engineer", "k": k}, lambda r: True, ""))
    # combos: several filters at once must all hold
    cases.append(
        (
            "combo remote+max_years=3+posted_within=30",
            {
                "q": "backend engineer",
                "remote": "true",
                "max_years": 3,
                "posted_within": 30,
                "k": 30,
            },
            lambda r: (
                r.get("remote") is True
                and (r.get("min_years") is None or r["min_years"] <= 3)
                and _iso_within(r.get("posted_at"), 30)
            ),
            "",
        )
    )
    cases.append(
        (
            "combo has_salary+etype=full-time",
            {
                "q": "senior developer",
                "has_salary": "true",
                "etype": "full-time",
                "k": 30,
            },
            lambda r: (
                r.get("salary") and _etype_ok(r.get("employment_type"), "full-time")
            ),
            "",
        )
    )

    checks = []
    for name, params, validator, _q in cases:
        try:
            rows = _get(base, params)
        except Exception as exc:  # noqa: BLE001 - a dead endpoint IS the finding
            checks.append({"name": name, "params": params, "error": str(exc)[:200]})
            print(f"[check] {name}: ERROR {exc}", file=sys.stderr, flush=True)
            continue
        if not isinstance(rows, list):
            checks.append(
                {"name": name, "params": params, "error": f"non-list: {rows}"}
            )
            continue
        violations = [r for r in rows if not validator(r)]
        k = int(params.get("k", 20))
        if len(rows) > k:
            violations.append({"_k_overflow": len(rows)})
        checks.append(
            {
                "name": name,
                "params": params,
                "n_results": len(rows),
                "n_violations": len(violations),
                "violations": violations[:5],
            }
        )
        print(
            f"[check] {name}: {len(rows)} rows, {len(violations)} violations",
            file=sys.stderr,
            flush=True,
        )
    return checks


def run_url_checks(base: str, atses: list[str], http: bool) -> list[dict]:
    """Per-ATS URL-shape validation over sampled results + optional live HTTP probes."""
    samples: dict[str, list[dict]] = {}
    for ats in atses:
        try:
            rows = _get(base, {"q": "software engineer", "ats": ats, "k": 5})
        except Exception:
            rows = []
        samples[ats] = rows

    out = []
    for ats, rows in samples.items():
        pattern = URL_SHAPES.get(ats)
        for r in rows[:3]:
            url = r.get("url") or ""
            rec = {
                "ats": ats,
                "url": url,
                "title": r.get("title"),
                "shape_ok": bool(pattern and re.match(pattern, url)),
                "shape_known": pattern is not None,
            }
            if http and url.startswith("https://"):
                try:
                    req = urllib.request.Request(
                        url, headers={"User-Agent": "Mozilla/5.0"}
                    )
                    with urllib.request.urlopen(req, timeout=25) as resp:
                        body = resp.read(400_000).decode("utf-8", "replace")
                    rec["http_status"] = resp.status
                    title_words = [
                        w for w in re.split(r"\W+", r.get("title") or "") if len(w) > 3
                    ]
                    rec["title_on_page"] = any(
                        w.lower() in body.lower() for w in title_words[:3]
                    )
                except Exception as exc:  # noqa: BLE001
                    rec["http_error"] = str(exc)[:120]
                time.sleep(0.5)  # politeness between cross-ATS probes
            out.append(rec)
            print(
                f"[url] {ats}: shape_ok={rec['shape_ok']} "
                f"status={rec.get('http_status', '-')} "
                f"title_on_page={rec.get('title_on_page', '-')} {url[:70]}",
                file=sys.stderr,
                flush=True,
            )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default=_DEFAULT_BASE)
    ap.add_argument("--no-http", action="store_true", help="skip live URL probes")
    args = ap.parse_args()

    # the ATSes actually present, straight from an unfiltered query sweep
    seen: set[str] = set()
    for q in QUERIES[:4]:
        try:
            seen |= {r.get("ats") for r in _get(args.base, {"q": q, "k": 100})}
        except Exception:
            pass
    atses = sorted(a for a in seen if a)
    print(f"[setup] ATSes present in results: {atses}", file=sys.stderr, flush=True)

    checks = run_checks(args.base, atses)
    url_checks = run_url_checks(args.base, atses, http=not args.no_http)

    report = {
        "base": args.base,
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "atses": atses,
        "checks": checks,
        "url_checks": url_checks,
        "summary": {
            "checks": len(checks),
            "checks_with_violations": sum(1 for c in checks if c.get("n_violations")),
            "check_errors": sum(1 for c in checks if c.get("error")),
            "urls_probed": len(url_checks),
            "url_shape_failures": sum(
                1 for u in url_checks if u["shape_known"] and not u["shape_ok"]
            ),
            "url_http_failures": sum(
                1
                for u in url_checks
                if u.get("http_error") or (u.get("http_status") or 200) >= 400
            ),
        },
    }
    _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = _REPORT_DIR / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps(report, indent=1))
    print(f"report -> {out}", file=sys.stderr, flush=True)
    print(json.dumps(report["summary"], indent=1))
    bad = (
        report["summary"]["checks_with_violations"]
        + report["summary"]["check_errors"]
        + report["summary"]["url_shape_failures"]
    )
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
