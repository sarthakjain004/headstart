#!/usr/bin/env python3
"""Compare unsupported ATSes by what they would actually contribute: live boards, tech jobs, India.

`data/ats-tenants-merged/` holds ~51k candidate tenants on ATSes HeadStart has no scraper for, and
the row counts alone are a bad way to rank them — measured on bamboohr, 45% of a 15,602-row pool
had no DNS record at all, and 0 of 294 jobs were in India. A pool count is an upper bound, never a
board count.

So this probes a random sample of each ATS's pool and reports the four numbers that decide whether
a scraper is worth building:

  live rate      what fraction of the pool still resolves and answers
  jobs/board     how much a live board actually carries (most SMB boards carry nothing)
  tech share     `headstart.tech_filter.is_tech` over the title
  India share    the location, where the surface exposes one

**Titles come from the job URL where possible.** jazzhr and softgarden both put a title slug in the
job link, which is the cheapest reliable title in HTML that carries no JSON-LD (none of these six
do). Where the surface is JS-rendered and exposes no server-side list, this reports `js-only` and
declines to guess a number rather than reporting a plausible-looking zero.

Recall the tech gate is deliberately recall-biased (CLAUDE.md): it passed "Building Services
Engineer" and "CNC Machining Process Engineer" on the bamboohr sample, so `tech` here is an upper
bound on *software* roles — read it as such and eyeball the sample titles it prints.

Usage:  python -u scripts/validate/ats_tech_yield.py [--sample 60] [ats ...]
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import random
import re
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from headstart.tech_filter import is_tech

POOL = pathlib.Path("data/ats-tenants-merged")
UA = "headstart/0.1"
INDIA = re.compile(
    r"\b(india|bangalore|bengaluru|hyderabad|pune|mumbai|chennai|gurgaon|gurugram|"
    r"noida|delhi|kolkata|ahmedabad|kochi|jaipur|indore|coimbatore|trivandrum)\b",
    re.IGNORECASE,
)


def _get(url: str, timeout: int = 25) -> tuple[str, str, int]:
    """(body, code, curl_rc). curl_rc 6 means DNS did not resolve — a real dead board."""
    p = subprocess.run(
        [
            "curl",
            "-sSL",
            "--max-time",
            str(timeout),
            "-A",
            UA,
            "-w",
            "\n%{http_code}",
            url,
        ],
        capture_output=True,
        text=True,
        check=False,  # a non-zero curl is a verdict here (rc 6 = dead), not an error
    )
    if p.returncode != 0:
        return "", "", p.returncode
    out = p.stdout
    nl = out.rfind("\n")
    return out[:nl], out[nl + 1 :].strip(), 0


def _slug_titles(body: str, pattern: re.Pattern) -> list[str]:
    """Titles read out of job-link slugs: `.../apply/{id}/Senior-Backend-Engineer`."""
    seen = {}
    for m in pattern.finditer(body):
        raw = m.group("title")
        seen[m.group(0)] = re.sub(r"[-_+]+", " ", raw).strip()
    return list(seen.values())


_JAZZ = re.compile(r"applytojob\.com/apply/[A-Za-z0-9]+/(?P<title>[A-Za-z0-9\-]{4,80})")
_SOFT = re.compile(r"/job/\d+/(?P<title>[A-Za-z0-9%\-]{4,90})")
_JOBVITE = re.compile(
    r'href="/[^"/]+/job/[A-Za-z0-9]+"[^>]*>(?P<title>[^<]{4,90})<', re.IGNORECASE
)


def probe(ats: str, row: dict) -> dict:
    """One board: its state, its job titles, and how many of those look India-located.

    Each branch returns (titles, india_count) or a non-live state; the scoring happens once at the
    bottom so the four ATSes cannot drift apart in how they count.
    """
    tenant, url = row["tenant"], (row.get("url") or "").strip()
    out = {
        "ats": ats,
        "tenant": tenant,
        "state": "?",
        "jobs": 0,
        "tech": 0,
        "india": 0,
        "titles": [],
    }

    target = {
        "breezy": f"https://{url or tenant + '.breezy.hr'}/json",
        "jazzhr": f"https://{url or tenant + '.applytojob.com'}/apply/",
        "softgarden": f"https://{tenant}.softgarden.io/",
        "jobvite": f"https://jobs.jobvite.com/{tenant}/search",
    }.get(ats)
    if target is None:
        out["state"] = "js-only"
        return out

    body, code, rc = _get(target)
    if rc == 6:  # DNS does not resolve: a real dead board, not a blip
        out["state"] = "dead-dns"
        return out
    if rc:
        out["state"] = f"curl-{rc}"  # timeout / reset: unknown, NOT dead
        return out
    if code != "200":
        out["state"] = f"http-{code}"
        return out

    titles: list[str] = []
    india = 0
    if ats == "breezy":
        try:
            jobs = json.loads(body)
        except Exception:  # noqa: BLE001 — an unparsable body is a reported state
            out["state"] = "unparsable"
            return out
        if not isinstance(jobs, list):
            jobs = []
        titles = [str(j.get("name", "")) for j in jobs if isinstance(j, dict)]
        for j in jobs:
            loc = j.get("location") or {} if isinstance(j, dict) else {}
            blob = json.dumps(loc) if isinstance(loc, dict) else str(loc)
            india += bool(INDIA.search(blob))
    elif ats in ("jazzhr", "softgarden"):
        titles = _slug_titles(body, _JAZZ if ats == "jazzhr" else _SOFT)
        india = len([t for t in titles if INDIA.search(t)])
    else:  # jobvite
        titles = [m.group("title").strip() for m in _JOBVITE.finditer(body)]
        india = len([t for t in titles if INDIA.search(t)])

    titles = [t for t in titles if t]
    out.update(
        state="live",
        jobs=len(titles),
        tech=sum(1 for t in titles if is_tech(t, "")),
        india=india,
        titles=[t for t in titles if is_tech(t, "")][:3],
    )
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sample", type=int, default=60)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument(
        "ats", nargs="*", default=["breezy", "jazzhr", "softgarden", "jobvite"]
    )
    args = ap.parse_args()
    random.seed(13)

    for ats in args.ats:
        f = POOL / f"{ats}.csv"
        if not f.exists():
            print(f"{ats}: no pool file", flush=True)
            continue
        rows = list(csv.DictReader(f.open(encoding="utf-8")))
        pick = random.sample(rows, min(args.sample, len(rows)))
        states, jobs, tech, india, ex = Counter(), 0, 0, 0, []
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for fut in as_completed([pool.submit(probe, ats, r) for r in pick]):
                r = fut.result()
                states[r["state"]] += 1
                jobs += r["jobs"]
                tech += r["tech"]
                india += r["india"]
                ex += r["titles"]
        live = states.get("live", 0)
        print(
            f"\n=== {ats}: {len(pick)} sampled of {len(rows)} pool rows ===\n"
            f"  outcomes     : {dict(states.most_common())}\n"
            f"  live boards  : {live} ({100 * live / len(pick):.0f}%)\n"
            f"  jobs         : {jobs}  ({jobs / live:.1f}/live board)"
            if live
            else f"  jobs: {jobs}",
            flush=True,
        )
        if jobs:
            print(
                f"  tech (upper) : {tech}  ({100 * tech / jobs:.0f}% of jobs)",
                flush=True,
            )
            print(f"  boards w/ India text: {india}", flush=True)
            print(f"  sample tech titles: {ex[:6]}", flush=True)


if __name__ == "__main__":
    main()
