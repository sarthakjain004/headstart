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

The Space sits behind the sign-in wall (ADR-0042), so ``/search`` 401s an anonymous caller
and every check would report the same meaningless error. The harness therefore runs as a
signed-in user, presenting the Flask session cookie copied out of a browser — see
:func:`_session_cookie`.

Run:  python scripts/eval/verify_filters.py [--base https://... ] [--no-http]
Exit: 0 clean, 1 when any check recorded violations, 2 when it could not sign in.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_BASE = "https://imposeidon-headstart-search.hf.space"
_REPORT_DIR = _ROOT / "data" / "eval" / "filter_checks"

sys.path.insert(0, str(_ROOT / "src"))
from headstart import geo  # noqa: E402 - after the sys.path insert above

# Deliberately OUTSIDE the repo: this is a live credential for a real account, and a file
# in the tree is one `git add` away from being published.
_SESSION_FILE = Path.home() / ".headstart_session"
# The page-size ceiling the serving path enforces (search.JobSearch's `max_k`), so a crafted
# `k` can't dump the table. Asserted here, which means changing it there fails this run —
# correct: a page-size change should be deliberate.
_MAX_K = 100
# Statuses that mean the posting is GONE, as opposed to a bot wall (403/429) or a transport
# error — only these fail the run; see the summary's url_dead_links.
_DEAD_STATUSES = frozenset({404, 410})
# The signed-in session, set once by main(). Read ONLY by the calls that talk to our own
# base; the per-ATS probes in run_url_checks build their own Request precisely so a live
# session credential is never sent to a third-party job board.
_COOKIE = ""

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
    # Two legitimate shapes. The second is the EMBED form: a tenant may configure its own
    # board page, and then the API's `absolute_url` — and greenhouse's own canonical link —
    # both point there with the job in `?gh_jid=`. Verified live 2026-08-12:
    # job-boards.greenhouse.io/codeblack/jobs/4012421004 302s to
    # codeblack.netlify.app/?gh_jid=4012421004, and the embed's job endpoint returns that
    # posting. The page renders client-side, so `title_on_page` reads false on it — a limit
    # of an HTTP probe, not a broken link.
    "greenhouse": r"https://(?:(?:job-boards|boards)\.greenhouse\.io/.+/jobs/\d+|.+[?&]gh_jid=\d+)",
    "lever": r"https://jobs(\.eu)?\.lever\.co/[^/]+/[0-9a-f-]{36}",
    "ashby": r"https://jobs\.ashbyhq\.com/[^/]+/[0-9a-f-]{36}",
    # Was host-agnostic (`https://.+/o/[^/]+`) because tenants serve on custom careers
    # domains — which is exactly how it passed 458 rows whose custom host was dead. Both the
    # scraper and the serve-time rewrite now put every link on the tenant's own host, so the
    # shape can assert that host and this check finally bites. Both of them keep a fallback
    # for an offer with no slug to build from, and a row that took it would fail here — which
    # is the point: it has never happened (0 of 772 served rows, 0 of 612 offers inspected),
    # so if it ever does, that is news and not something to wave through.
    "recruitee": r"https://[\w-]+\.recruitee\.com/o/[^/]+",
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
    # scraper: f"https://{slug}{path}" where the SLUG IS THE BOARD HOST — five live ledger rows
    # sit on custom domains (careers.micron.com, jobs.vodafone.com, portal.careers.hsbc.com…),
    # so anchoring on .eightfold.ai flagged real rows. Host-agnostic like recruitee/darwinbox;
    # verified live 2026-08-02 on both host kinds (amdocs-sandbox.eightfold.ai, careers.micron.com)
    "eightfold": r"https://[^/]+/careers/job/\d+",
    # scraper passes through the API's own url field; tenants live on {slug}.freshteam.com
    "freshteam": r"https://[\w-]+\.freshteam\.com/jobs/[\w-]+",
    # scraper passes through RMK sitemap URLs: /job/{slug}/{id}/ on per-tenant vanity hosts
    # (jobs.bt.com, careers.capgemini.com, jobs.turbo.co.th — no common host to anchor on)
    "successfactors": r"https://[^/]+/job/.+/\d+/?",
    # from the scraper's construction (oracle.py: /hcmUI/CandidateExperience/en/sites/{site}/job/{id});
    # ZERO indexed rows today (single-company unlock, Icertis) so no live sample to verify against —
    # the shape is source-derived only, and the first indexed row will exercise it.
    "oracle": r"https://[^/]+/hcmUI/CandidateExperience/.+/job/\d+",
    # from the scraper's construction (sensehq.py: {slug}.sensehq.com/careers/jobs/{id});
    # ZERO indexed rows today — source-derived only, same caveat as oracle.
    "sensehq": r"https://[\w-]+\.sensehq\.com/careers/jobs/\d+",
    # run_wellfound*.py build f"https://wellfound.com/jobs/{id}-{slug}".rstrip("-"), so the
    # slug is optional when a listing has none. Verified against all 6,462 rows of
    # data/jobs/wellfound.csv (zero non-matching). Wellfound was served with NO shape entry
    # until 2026-08-05 — the same class of gap the coverage gate above was added to catch.
    "wellfound": r"https://wellfound\.com/jobs/\d+(-[\w-]+)?",
}


def _session_cookie(path: Path) -> str:
    """The Flask session cookie copied out of a signed-in browser, or "".

    The wall verifies Google once and then trusts this cookie for weeks (ADR-0042), so
    presenting it is exactly what a real user's browser does — no bypass is added to the
    app for the harness's benefit.
    """
    raw = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    # tolerate a whole `session=<value>` pasted from the devtools Cookie header
    return raw.split("session=", 1)[-1].strip().strip('";')


def _request(url: str) -> urllib.request.Request:
    req = urllib.request.Request(url)
    if _COOKIE:
        req.add_header("Cookie", f"session={_COOKIE}")
    return req


def _read(url: str) -> list[dict]:
    with urllib.request.urlopen(_request(url), timeout=120) as resp:
        return json.load(resp)


def _get(base: str, params: dict) -> list[dict]:
    """One ``/search`` call as the signed-in user.

    Retries once on a transport failure — the 2026-08-03 run logged two (a timeout and a
    connection reset) as check errors, which read as findings when they were only the
    network. A genuinely unreachable endpoint fails both attempts and still reports.
    """
    url = f"{base}/search?" + urllib.parse.urlencode(params)
    try:
        return _read(url)
    except urllib.error.HTTPError:
        raise  # an HTTP status is never retried: that IS the finding
    except OSError:
        time.sleep(3)
        return _read(url)


def _probe(base: str, path: str, params: dict) -> tuple[int, object]:
    """``(status, decoded body)`` — for the checks whose expectation IS a status code.

    ``HTTPError`` is itself a response, so a 400 is data here rather than an exception.
    """
    url = f"{base}{path}?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(_request(url), timeout=120) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as err:
        try:
            return err.status, json.load(err)
        except Exception:  # noqa: BLE001 - a non-JSON error body is still a status
            return err.status, None


def _iso_within(posted_at: str | None, days: int) -> bool:
    if not posted_at or not re.match(r"^\d{4}-\d{2}-\d{2}", posted_at):
        return False
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    return posted_at >= cutoff


def _seen_within(first_seen: str | None, hours: int) -> bool:
    """``first_seen`` is written by ``index sync``, not an ATS, so it is always full ISO-8601 UTC —
    no shape guard needed, unlike ``_iso_within``. A null predates the column (ADR-0031) and must
    never appear inside a window."""
    if not first_seen:
        return False
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(
        timespec="seconds"
    )
    return first_seen >= cutoff


def _day_between(value: str | None, start: str | None, end: str | None) -> bool:
    """The row's ISO date part inside ``[start, end]``, BOTH ends inclusive.

    Inclusive at the top end because that is what ``build_filter`` promises: a
    ``posted_before`` compares strictly below the NEXT day, so a job posted on the bound
    itself belongs in the results.
    """
    if not value or not re.match(r"^\d{4}-\d{2}-\d{2}", value):
        return False
    day = value[:10]
    return (not start or day >= start) and (not end or day <= end)


def _india_ok(place: str, location: str | None) -> bool:
    """A place-filtered row must really name that place.

    Checked against the gazetteer's alias DATA (``geo.CITIES``/``REGIONS``/``STATES``)
    rather than by re-reading ``geo.where``'s SQL — a check built from the clause it is
    checking would rubber-stamp a broken clause. Also asserts the gazetteer's own
    documented traps stay out: Surat must not return Surat Thani, and country-level India
    must not return Indiana.
    """
    loc = (location or "").lower()
    if place == "india":
        if (
            "indiana" in loc
        ):  # the carve-out geo.where spells out; the trap the filter must not fall into
            return False
        pools = [("india",), *geo.CITIES.values(), geo.STATES]
    elif place in geo.REGIONS:
        pools = [geo.CITIES[c] for c in geo.REGIONS[place]]
    else:
        pools = [geo.CITIES.get(place, (place,))]
    if not any(alias in loc for pool in pools for alias in pool):
        return False
    return not any(bad in loc for bad in geo.EXCLUDE.get(place, ()))


def _row_ok(row: dict) -> bool:
    """Every served row must carry the fields the UI cannot render without.

    ``id`` is the newest of them and the load-bearing one: it is the star identity
    (``{ats}:{slug}:{native_id}``, models.py), so a null or malformed id silently breaks
    saving a job rather than erroring anywhere visible.
    """
    job_id, ats = row.get("id") or "", row.get("ats") or ""
    if not (job_id and ats and job_id.startswith(f"{ats}:") and job_id.count(":") >= 2):
        return False
    return bool(row.get("title") and row.get("company") and row.get("url"))


def _etype_ok(value: str | None, canonical: str) -> bool:
    v = (value or "").lower()
    return {
        "full-time": ("full" in v or "permanent" in v),
        "part-time": "part" in v,
        "contract": ("contract" in v or "freelance" in v),
        "internship": "intern" in v,
    }[canonical]


def _preflight(base: str) -> tuple[bool, str | None]:
    """``(wall is on, who we are)`` — from ``/me``, which answers from the caller's own
    cookie and is public precisely so it can tell you that."""
    status, body = _probe(base, "/me", {})
    if status != 200 or not isinstance(body, dict):
        return True, None  # can't confirm an identity; treat as not signed in
    return bool(body.get("auth")), body.get("email")


def run_checks(base: str, atses: list[str]) -> list[dict]:
    """Every (query × filter) case with a per-row validator; returns check records."""
    cases: list[tuple[str, dict, callable, str]] = []
    for q in QUERIES:
        cases.append((f"row integrity [{q}]", {"q": q, "k": 30}, _row_ok, q))
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
    # Hours, not days — the window is meant to be shorter than one pipeline cycle. Every row
    # predating the migration is null, so a run before the column has propagated returns nothing,
    # which is a pass (the predicate is only applied to rows that came back).
    for hours in (2, 24, 168):
        cases.append(
            (
                f"seen_within={hours}h",
                {"q": "software engineer", "seen_within": hours, "k": 40},
                lambda r, h=hours: _seen_within(r.get("first_seen"), h),
                "",
            )
        )
    # Custom date ranges (ADR-0031's neighbours: both ends optional, both inclusive). These
    # are the Matches tab's own recency controls and had no coverage at all — the window
    # filters above exercise a different code path (a computed cutoff, not user-typed text).
    today = datetime.now(timezone.utc).date()
    d7 = (today - timedelta(days=7)).isoformat()
    d30 = (today - timedelta(days=30)).isoformat()
    d90 = (today - timedelta(days=90)).isoformat()
    for name, params, start, end in (
        ("posted_after", {"posted_after": d30}, d30, None),
        ("posted_before", {"posted_before": d30}, None, d30),
        ("posted range", {"posted_after": d90, "posted_before": d30}, d90, d30),
    ):
        cases.append(
            (
                f"{name} {params}",
                {"q": "software engineer", **params, "k": 40},
                lambda r, s=start, e=end: _day_between(r.get("posted_at"), s, e),
                "",
            )
        )
    for name, params, start, end in (
        ("seen_after", {"seen_after": d7}, d7, None),
        ("seen_before", {"seen_before": today.isoformat()}, None, today.isoformat()),
        (
            "seen range",
            {"seen_after": d30, "seen_before": today.isoformat()},
            d30,
            today.isoformat(),
        ),
    ):
        cases.append(
            (
                f"{name} {params}",
                {"q": "software engineer", **params, "k": 40},
                lambda r, s=start, e=end: _day_between(r.get("first_seen"), s, e),
                "",
            )
        )
    # The alerts Watermark cutoff (ADR-0035), and the only recency filter that is STRICTLY
    # greater — a Digest that re-selected the row its Watermark came from would mail a
    # duplicate, so equality here is a real defect, not an off-by-one nicety.
    moment = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat(
        timespec="seconds"
    )
    cases.append(
        (
            f"first_seen_after={moment}",
            {"q": "software engineer", "first_seen_after": moment, "k": 40},
            lambda r, m=moment: bool(r.get("first_seen")) and r["first_seen"] > m,
            "",
        )
    )
    # ...and in combination, because that is how a Digest actually queries: the Watermark
    # cutoff never travels alone, it rides with the Subscription's own saved filters.
    cases.append(
        (
            f"combo first_seen_after={moment}+remote+max_years=5",
            {
                "q": "software engineer",
                "first_seen_after": moment,
                "remote": "true",
                "max_years": 5,
                "k": 40,
            },
            lambda r, m=moment: (
                bool(r.get("first_seen"))
                and r["first_seen"] > m
                and r.get("remote") is True
                and (r.get("min_years") is None or r["min_years"] <= 5)
            ),
            "",
        )
    )
    # The India gazetteer (ADR-0024) — a country, a region that expands to member cities,
    # a plain city, and the city whose alias needs an exclusion to stay honest.
    for place in ("india", "delhi ncr", "bengaluru", "surat"):
        cases.append(
            (
                f"india={place}",
                {"q": "software engineer", "india": place, "k": 30},
                lambda r, p=place: _india_ok(p, r.get("location")),
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
    # 500 is over the serving cap: it must come back clamped, not as the whole table. The
    # page-size assertion itself lives with the violations below.
    for k in (5, 50, 100, 500):
        cases.append((f"k={k}", {"q": "engineer", "k": k}, _row_ok, ""))
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
    # The new filters in combination — a date range narrows the same query a place filter
    # does, and the two clauses are ANDed into one where, so a broken join shows here and
    # nowhere else.
    cases.append(
        (
            f"combo india=bengaluru+posted_after={d90}+remote",
            {
                "q": "backend engineer",
                "india": "bengaluru",
                "posted_after": d90,
                "remote": "true",
                "k": 30,
            },
            lambda r: (
                _india_ok("bengaluru", r.get("location"))
                and _day_between(r.get("posted_at"), d90, None)
                and r.get("remote") is True
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
        # A page never exceeds what was asked for, nor the serving cap.
        cap = min(int(params.get("k", 20)), _MAX_K)
        if len(rows) > cap:
            violations.append({"_k_overflow": len(rows), "_cap": cap})
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


def run_input_checks(base: str) -> list[dict]:
    """Garbage in a filter must be REFUSED, not ignored.

    Each custom date is re-serialized through ``date.fromisoformat`` on its way into the
    where-clause, so an unparseable one raises and the route answers 400. The failure this
    guards against is the quiet one: a filter the server drops on the floor returns
    unfiltered results that the UI still labels as filtered.
    """
    bad = (
        ("posted_after", "not-a-date"),
        ("posted_before", "2026-13-45"),
        ("seen_after", "yesterday"),
        ("seen_before", "2026-99"),
        ("first_seen_after", "soon"),
        ("max_years", "three"),
        ("k", "lots"),
    )
    out = []
    for param, value in bad:
        name = f"rejects {param}={value!r}"
        params = {"q": "engineer", param: value}
        status, body = _probe(base, "/search", params)
        violations = [] if status == 400 else [{"expected": 400, "got": status}]
        out.append(
            {
                "name": name,
                "params": params,
                "n_results": len(body) if isinstance(body, list) else 0,
                "n_violations": len(violations),
                "violations": violations,
            }
        )
        print(f"[input] {name}: HTTP {status}", file=sys.stderr, flush=True)
    return out


def _gh_jid_matches_row(row: dict, url: str) -> bool:
    """A greenhouse embed link's ``gh_jid`` names the job THIS row is for — vacuously true
    of every other link, which identifies its job in the path.

    The embed is the one form whose host can't be anchored: a tenant's board page may live
    anywhere, which is exactly how recruitee's host-agnostic pattern waved through 458 dead
    links. What the embed does carry is the job id, and so does the row
    (``{ats}:{slug}:{native_id}``), so pin one to the other. Note this proves the link
    *names* the job, never that its host serves it — only the HTTP probe can say that.
    """
    if "gh_jid=" not in url:
        return True
    jid = re.split(r"[&#]", url.split("gh_jid=", 1)[1])[0]
    return jid == (row.get("id") or "").rsplit(":", 1)[-1]


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
                # kept OUT of shape_ok: a wrong-job link and a wrong-shaped link are
                # different diagnoses, and step 3 of the skill has the reader walk each
                # shape_ok=false to a root cause. Both fail the run; they just say why.
                "points_at_job": _gh_jid_matches_row(r, url),
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
                except urllib.error.HTTPError as exc:
                    # A 4xx/5xx RAISES here, so without this branch the status never
                    # reaches the record and every dead link looks like a transport error.
                    # That is not hypothetical: it is why the 2026-08-12T11:36 run reported
                    # url_dead_links=0 while probing two 404s.
                    rec["http_status"] = exc.status
                    rec["http_error"] = str(exc)[:120]
                except Exception as exc:  # noqa: BLE001
                    rec["http_error"] = str(exc)[:120]
                time.sleep(0.5)  # politeness between cross-ATS probes
            out.append(rec)
            print(
                f"[url] {ats}: shape_ok={rec['shape_ok']} "
                f"right_job={rec['points_at_job']} "
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
    ap.add_argument(
        "--cookie-file",
        default=str(_SESSION_FILE),
        help="file holding the browser `session` cookie (see _session_cookie)",
    )
    args = ap.parse_args()

    global _COOKIE
    _COOKIE = _session_cookie(Path(args.cookie_file))
    # Establish the identity BEFORE any check runs: behind the wall an anonymous run turns
    # every single check into the same 401, which reads like a broken deployment instead of
    # a missing cookie. One actionable line beats eighty misleading findings.
    auth_on, who = _preflight(args.base)
    if auth_on and not who:
        print(
            "not signed in — the wall (ADR-0042) will 401 every check.\n"
            f"  Sign in at {args.base}, copy the `session` cookie from devtools\n"
            f"  (Application → Cookies), and save its value to {args.cookie_file}.",
            file=sys.stderr,
            flush=True,
        )
        return 2
    print(
        f"[setup] signed in as {who}"
        if who
        else "[setup] wall off — running anonymous",
        file=sys.stderr,
        flush=True,
    )

    # the ATSes actually present, straight from an unfiltered query sweep
    seen: set[str] = set()
    for q in QUERIES[:4]:
        try:
            seen |= {r.get("ats") for r in _get(args.base, {"q": q, "k": 100})}
        except Exception:
            pass
    atses = sorted(a for a in seen if a)
    print(f"[setup] ATSes present in results: {atses}", file=sys.stderr, flush=True)

    # The coverage GATE spans sampled ∪ registry — a low-ranking ATS (join, personio, trakstar
    # measured absent from the 4-query sweep) must not evade the shape requirement by ranking
    # low. Per-ATS check/url cases still run only over the sampled set: an ATS with zero indexed
    # rows would produce meaningless cases (app.py's whitelist silently ignores unknown ats).
    from headstart.scrapers.registry import DISABLED_ATS, SCRAPERS

    gate_atses = sorted(set(atses) | (set(SCRAPERS) - DISABLED_ATS))

    checks = run_checks(args.base, atses) + run_input_checks(args.base)
    url_checks = run_url_checks(args.base, atses, http=not args.no_http)

    report = {
        "base": args.base,
        "ran_at": datetime.now(timezone.utc).isoformat(),
        # who, deliberately not recorded: the report is a shareable artifact and the
        # address behind the session is not part of what was verified
        "signed_in": bool(who),
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
            # Every non-2xx, INCLUDING the ones that are somebody else's bot wall — advisory,
            # because a 403 on a URL a browser opens fine is TLS fingerprinting and chasing
            # it teaches the reader to ignore red runs.
            "url_http_failures": sum(
                1
                for u in url_checks
                if u.get("http_error") or (u.get("http_status") or 200) >= 400
            ),
            # ...but a link that is GONE is the whole point of this harness, and until now it
            # counted for nothing: the 2026-08-12 run's two recruitee 404s left the exit code
            # untouched — it was red only because a greenhouse shape failed. A dead link now
            # fails the run on its own.
            "url_dead_links": sum(
                1 for u in url_checks if (u.get("http_status") or 0) in _DEAD_STATUSES
            ),
            "url_wrong_job": sum(1 for u in url_checks if not u["points_at_job"]),
            # A served ATS with no URL_SHAPES entry used to print shape_ok=False but count
            # nothing — three ATSes shipped unchecked that way (eightfold/freshteam/
            # successfactors, caught 2026-08-02 by a user-visible sandbox row). Coverage is
            # now a failure, not a footnote.
            "atses_without_shape": sorted(a for a in gate_atses if a not in URL_SHAPES),
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
        + report["summary"]["url_dead_links"]
        + report["summary"]["url_wrong_job"]
        + len(report["summary"]["atses_without_shape"])
    )
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
