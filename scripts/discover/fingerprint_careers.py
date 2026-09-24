#!/usr/bin/env python3
"""Careers-page ATS fingerprinter — the multi-signal pass over companies a page scan called opaque.

`scripts/resolve/fingerprint.py` resolves a company by fetching `/` and `/careers` and regexing the
HTML for a *supported* ATS host. On a 396-company India seed that left **316 (80%) opaque** — and
opaque is not "no ATS", it is "this one signal saw nothing". Three structural reasons, each fixed
here by a different signal rather than by fetching the same two pages harder:

1. **The board is on a careers *subdomain*, not a path.** `careers.{domain}` frequently CNAMEs
   straight at the ATS (`*.myworkdayjobs.com`, `*.freshteam.com`, `*.darwinbox.in`, `*.zwayam.com`)
   or 301s to it. A fetch of `{domain}/careers` never sees either — the path often 404s or, worse,
   200s with a marketing page. So the *first* pass here is a **CNAME sweep** over ten careers-ish
   labels: one UDP query, no HTTP, and a CNAME into a provider zone is proof, not inference.
2. **The careers page is an SPA.** The ATS host lives in a JS bundle, not the served HTML, so a
   regex over the document finds nothing while the board is one XHR away. Unresolved companies get
   a bounded **bundle scan** (<=3 same-origin scripts off the careers page) as a last rung.
3. **The ATS isn't one we support, so nothing was looking for it.** The old table carries 21
   shapes, all but two of them supported ATSes. Coverage questions are exactly the ones you cannot
   answer with a table of what you already have — the point is to find what is *missing*. The
   table here carries 70+ providers, marked `supported` / `unsupported` from
   `headstart.scrapers.registry`, plus two deliberately-separate non-ATS classes: `jobboard`
   (LinkedIn/Naukri/workatastartup — an apply link, not a board we could scrape) and
   `diy` (Notion/Typeform/Google Forms/`mailto:`), which is what much of the genuine "no ATS"
   tail turns out to be.

**A failure is never a negative result.** Every company settles into exactly one of five states,
and they are kept apart in the CSV because collapsing them is how a headline number goes wrong:
`resolved` (an ATS matched), `jobboard`/`diy` (a real apply route, not an ATS), `none` (at least
one page fetched cleanly and nothing matched — a settled negative), and `unreachable` (every
probe DNS-failed, timed out, or was walled — says nothing about the company, worth re-probing).
`pages_ok`/`pages_err` are written per row so that split is auditable after the fact.

Signals, cheapest first, and each recorded as the `signal` column so the writeup can rank them:
  cname     — a careers-ish subdomain CNAMEs into a provider zone (decisive, ~1 UDP query)
  redirect  — a careers host/path lands on an ATS URL after following redirects
  homepage  — the ATS host appears in the apex page's HTML (link, iframe, script, inline JS)
  page      — same, on a careers page (harvested link, or /careers, /jobs, ...)
  robots    — robots.txt names the ATS host
  sitemap   — sitemap.xml names it
  jsbundle  — only the SPA's JS bundle names it
  slugprobe — nothing on the site named a board, but a shared-namespace ATS answers a slug
              derived from the company with jobs>0 (inferred, not observed — ranked last, and
              kept separable in the CSV so it can be discounted)

Run:
    python scripts/discover/fingerprint_careers.py scan   SEED.csv OUT.csv [--workers 8]
    python scripts/discover/fingerprint_careers.py indeed INDEED.jsonl OUT.csv [--workers 8]
    python scripts/discover/fingerprint_careers.py verify OUT.csv               # confirm the hits

`scan` streams one line per company and appends to OUT.csv as it goes, so it resumes: a re-run
skips domains already recorded and a run killed mid-sweep costs only the company in flight.

`indeed` is a narrow input adapter for an Indeed harvest's full apply URLs. Its host is evidence,
not a Board: it records a detected ATS separately from a scrapable candidate and preserves a
short-link's path where that path identifies the tenant. It never writes a liveness ledger.

`verify` derives canonical `board_key()` identities and calls the read-only ATS probes from
`scripts/validate/check_liveness.py`, once per distinct Board. It writes live/dead/unknown to a
separate CSV. It never writes ledgers; aliases and canonical landing still go through the normal
liveness workflow. Providers lacking a liveness probe remain explicitly unverified.

Needs dnspython for the CNAME stage (not a base dependency; CI installs base deps only). Without
it that stage is skipped and every other signal still runs.
"""

from __future__ import annotations

import argparse
import csv
import html
import importlib.util
import json
import re
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

import certifi
from curl_cffi import requests as _requests

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts/discover"))
from fingerprint_deep import (
    api_signatures,
    browser_page,
    certificate_career_hosts,
    certificate_names,
    public_domain,
)
from fingerprint_job_evidence import check_jobs
from wayback_feeder import ADP_HOST, ADP_PAGE_PATH, extract

from headstart import scrapable_boards
from headstart.board_identity import board_key, lower_key
from headstart.config import CompanyRef
from headstart.scrapers import registry

try:
    import dns.resolver

    _DNS_EMPTY_ERRORS = (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN)
    _DNS = dns.resolver.Resolver()
    _DNS.lifetime = 4.0
    _DNS.timeout = 2.0
except Exception:  # noqa: BLE001
    _DNS = None
    _DNS_EMPTY_ERRORS = ()

UA = "headstart/0.1"
PAGE_CAP = 900_000
BUNDLE_CAP = 1_500_000
TIMEOUT = 12

# The ATSes with a scraper. Read from the registry rather than restated, so this script can never
# claim an ATS is unsupported after someone wires one up. DISABLED_ATS (join) still counts as
# supported: the scraper exists, it is only held out of the active scrape list.
SUPPORTED: frozenset[str] = frozenset(registry.SCRAPERS)

# --- pattern table ------------------------------------------------------------------------
# Every subdomain-tier pattern MUST carry this lookbehind. Without it the engine starts a match
# attempt at every offset inside a long run of hostname-legal characters — minified JS and base64
# blobs are full of them — which is quadratic on a 900KB page (fingerprint.py measured >120s on
# one 520KB blob). The lookbehind rejects a mid-run offset in a single step.
HOST = r"(?<![a-z0-9.-])"
SUB = HOST + r"([a-z0-9][a-z0-9-]{1,60})\."

# kind: "ats" (a real board we could scrape), "jobboard" (an aggregator's apply page — a real
# route, but not a per-company board), "diy" (a form/doc, i.e. genuinely no ATS). Only "ats"
# counts toward the resolution headline; the other two are reported separately on purpose.
PATTERNS: dict[str, tuple[str, list[str]]] = {
    "radancy": (
        "ats",
        [r"(?:[a-z0-9.-]+\.)?talentbrew\.com\b", r"(?:[a-z0-9.-]+\.)?radancy\.com\b"],
    ),
    # ---------- supported ----------
    "greenhouse": (
        "ats",
        [
            r'(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/embed/job_board[^"\'\s]{0,200}?[?&]for=([a-zA-Z0-9_-]+)',
            r"boards-api(?:-eu)?\.greenhouse\.io/v1/boards/([a-zA-Z0-9_-]+)",
            r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/([a-zA-Z0-9_-]+)",
        ],
    ),
    "lever": (
        "ats",
        [
            r"api(?:\.eu)?\.lever\.co/v0/postings/([a-zA-Z0-9_-]+)",
            r"jobs(?:\.eu)?\.lever\.co/([a-zA-Z0-9_-]+)",
        ],
    ),
    "ashby": (
        "ats",
        [
            r"api\.ashbyhq\.com/posting-api/job-board/([a-zA-Z0-9_-]+)",
            r"jobs\.ashbyhq\.com/(?:embed\?[^\"'\s]{0,80}?board=)?([a-zA-Z0-9_-]+)",
        ],
    ),
    "zoho": (
        "ats",
        [
            # The full host, not the leading label: zoho.py's slug IS the careers host
            # ("pnbcsl.zohorecruit.in" — the data centre varies .in/.com/.eu).
            HOST
            + r"([a-z0-9][a-z0-9-]{1,60}\.zohorecruit\.(?:com|eu|in|ca|com\.au|jp))",
            r"recruit\.zoho\.(?:com|in|eu)/recruit/[A-Za-z]{0,30}\?[^\"'\s]{0,120}?digest=([a-zA-Z0-9_-]+)",
            # Zoho Recruit on a **vanity domain** — `careers.yellow.ai`, with no zohorecruit.com
            # host anywhere on the page. Invisible to every host-shaped pattern, and measured on
            # this seed it is not a corner case. The tell is Zoho's own career-site front-end
            # script, which zoho.py's docstring names by hand: it is the file that reads the
            # `<input type="hidden" ... id="jobs">` blob the scraper parses. Confirmed on
            # careers.yellow.ai (1.7MB page, no `zohorecruit` string anywhere, `#jobs` blob
            # present). Matched on the asset *paths* rather than the script filename because on
            # that page the `<script>` tag sits at byte 1,045,676, past PAGE_CAP, while the
            # stylesheet links land at byte 4,484.
            r"(?:css|javascript)/career-website-(?:common|customize|detail)",
        ],
    ),
    "workday": (
        "ats",
        [
            HOST
            + r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?([a-zA-Z0-9_-]+)",
            HOST
            + r"(wd\d+)\.myworkdaysite\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?(?:recruiting|wday/cxs)/([a-z0-9_-]+)/([a-zA-Z0-9_-]+)",
            HOST
            + r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/wday/cxs/[^/\s]+/([a-zA-Z0-9_-]+)",
        ],
    ),
    "workable": (
        "ats",
        [
            r"apply\.workable\.com/(?:api/v1/widget/accounts/)?([a-zA-Z0-9_-]+)",
            SUB + r"workable\.com",
        ],
    ),
    "smartrecruiters": (
        "ats",
        [
            r"api\.smartrecruiters\.com/v1/companies/([a-zA-Z0-9_-]+)",
            r"(?:careers|jobs)\.smartrecruiters\.com/([a-zA-Z0-9_-]+)",
        ],
    ),
    "recruitee": ("ats", [SUB + r"recruitee\.com", SUB + r"ainterviews\.com"]),
    "oracle": (
        "ats",
        [
            HOST + r"([a-z0-9-]+\.fa\.(?:ocs|em\d|us\d|ca\d|eu\d)\.oraclecloud\.com)",
            r"oraclecloud\.com/hcmUI/CandidateExperience",
        ],
    ),
    "sensehq": ("ats", [SUB + r"sensehq\.com"]),
    "keka": ("ats", [SUB + r"keka\.com"]),
    "trakstar": ("ats", [SUB + r"hire\.trakstar\.com", SUB + r"recruiterbox\.com"]),
    "ripplehire": ("ats", [SUB + r"ripplehire\.com"]),
    "darwinbox": ("ats", [SUB + r"darwinbox\.(?:in|com|co|us|eu|sa|id)"]),
    "teamtailor": ("ats", [SUB + r"teamtailor\.com"]),
    "personio": (
        "ats",
        [HOST + r"([a-z0-9][a-z0-9-]{1,60}\.jobs\.personio\.(?:de|com))"],
    ),
    "join": ("ats", [r"join\.com/companies/([a-zA-Z0-9_-]+)"]),
    "rippling": ("ats", [r"ats\.rippling\.com/([a-zA-Z0-9_-]+)"]),
    "freshteam": ("ats", [SUB + r"freshteam\.com"]),
    "eightfold": ("ats", [HOST + r"([a-z0-9][a-z0-9-]{1,60}\.eightfold\.ai)"]),
    "successfactors": (
        "ats",
        [
            SUB + r"(?:successfactors|sapsf)\.(?:com|eu)",
            r"rmkcdn\.successfactors\.com",
            r"(?:jobs|career|careers)\d{0,2}\.sap\.com",
            # jobs2web.com is SAP's own RMK infrastructure (SuccessFactors absorbed Jobs2Web), so
            # a careers host CNAMEd at `{n}.jobs2web.com` is a SuccessFactors board, not a
            # separate provider. Chargebee is the live example in this seed.
            HOST + r"(\d{3,8})\.jobs2web\.com",
        ],
    ),
    "zwayam": (
        "ats",
        [SUB + r"zwayam\.com", SUB + r"openings\.co", r"public\.zwayam\.com"],
    ),
    # A Jibe Board is its client id, the single label of `{client}.jibeapply.com`. SUB's
    # lookbehind keeps a vanity CNAME target (`careers.rm.com.jibeapply.com`) from yielding
    # its last label: no match can start mid-host, and `careers.` is not followed by the zone.
    "jibe": ("ats", [SUB + r"jibeapply\.com"]),
    # ---------- NOT supported: global ----------
    # Both scrapers are host-keyed.  Capturing only the first provider label would turn
    # `1-bp.icims.com` into `1-bp`, a string no scraper can use.
    "icims": ("ats", [HOST + r"([a-z0-9][a-z0-9-]{1,60}\.icims\.com)"]),
    # Host-only Taleo evidence proves the vendor but cannot name a Board. Concrete URLs are
    # split into supported taleo_be/taleo_enterprise identities by taleo_board_from_url().
    "taleo": ("ats", [SUB + r"taleo\.net"]),
    "jobvite": ("ats", [r"jobs\.jobvite\.com/([a-zA-Z0-9_-]+)", SUB + r"jobvite\.com"]),
    "bamboohr": ("ats", [SUB + r"bamboohr\.(?:com|co\.uk)"]),
    "breezy": ("ats", [SUB + r"breezy\.hr"]),
    "jazzhr": ("ats", [SUB + r"applytojob\.com"]),
    "phenom": (
        "ats",
        [HOST + r"([a-z0-9][a-z0-9.-]{1,60}\.phenompeople\.com)"],
    ),
    "avature": ("ats", [SUB + r"avature\.net"]),
    "cornerstone": ("ats", [SUB + r"csod\.com"]),
    "comeet": ("ats", [r"comeet\.com/jobs/([a-zA-Z0-9_.-]+)", SUB + r"comeet\.co"]),
    "pinpoint": ("ats", [SUB + r"pinpointhq\.com"]),
    "homerun": ("ats", [SUB + r"homerun\.co"]),
    "manatal": ("ats", [SUB + r"manatal\.com"]),
    "recruiterflow": ("ats", [SUB + r"recruiterflow\.com"]),
    "recruitcrm": ("ats", [SUB + r"recruitcrm\.io"]),
    "loxo": ("ats", [SUB + r"loxo\.co"]),
    "jobscore": ("ats", [r"careers\.jobscore\.com/careers/([a-zA-Z0-9_-]+)"]),
    "dover": ("ats", [r"app\.dover\.io/([a-zA-Z0-9_-]+)", r"app\.dover\.com/jobs"]),
    "getro": ("ats", [SUB + r"getro\.com", r"jobs\.getro\.com"]),
    "gem": ("ats", [r"jobs\.gem\.com/([a-zA-Z0-9_-]+)"]),
    "hireology": ("ats", [SUB + r"hireology\.com"]),
    # The public Board is `{slug}.hrmdirect.com` (HRM Direct, ClearCompany's career site); the
    # same label keys the tenant's `{slug}.clearcompany.com` app (docs/clearcompany/).
    "clearcompany": ("ats", [SUB + r"hrmdirect\.com", SUB + r"clearcompany\.com"]),
    # ADP Workforce Now: the Board is `cid` + `ccId` in the career-center page's query (adp.py);
    # `scan` reads them out of the captured query. ADP Recruiting Management is a different
    # platform (its own host, path slug and API), so it is its own key.
    "adp": (
        "ats",
        [
            re.escape(f"{ADP_HOST}/{ADP_PAGE_PATH}") + r"\?([^\s\"'<>\\]+)",
            # Any other mention still detects the ATS, with no Board to name.
            re.escape(ADP_HOST) + f"(?!/{re.escape(ADP_PAGE_PATH)}\\?)",
        ],
    ),
    # A Board is the path word of `myjobs.adp.com/{slug}/cx` (adp_recruiting.py). A legacy
    # `recruiting.adp.com` link names the client number, not a site, so it detects the ATS only.
    "adp_recruiting": (
        "ats",
        [
            r"myjobs\.adp\.com/(?!public/)([a-zA-Z0-9][a-zA-Z0-9_.-]*[a-zA-Z0-9_-])",
            r"recruiting\.adp\.com",
        ],
    ),
    "ukg": ("ats", [SUB + r"ultipro\.com"]),
    "occupop": ("ats", [SUB + r"occupop\.com"]),
    "hrcloud": ("ats", [SUB + r"hrcloud\.com"]),
    # ---------- NOT supported: India-origin HRMS / ATS ----------
    "peoplestrong": (
        "ats",
        [SUB + r"peoplestrong\.com", SUB + r"altone\.io", SUB + r"peoplestrong\.in"],
    ),
    "turbohire": ("ats", [SUB + r"turbohire\.co"]),
    "pyjamahr": (
        "ats",
        [
            r"jobs\.pyjamahr\.com/([a-zA-Z0-9_-]+)",
            SUB + r"pyjamahr\.com",
            r"api\.pyjamahr\.com",
        ],
    ),
    "skillate": ("ats", [SUB + r"skillate\.com"]),
    "kula": ("ats", [r"careers\.kula\.ai/([a-zA-Z0-9_-]+)"]),
    "param": ("ats", [HOST + r"([a-z0-9][a-z0-9-]{1,60}\.app\.param\.ai)"]),
    "jobsoid": ("ats", [SUB + r"jobsoid\.com"]),
    "hrone": ("ats", [SUB + r"hrone\.cloud"]),
    "zinghr": ("ats", [SUB + r"zinghr\.com"]),
    "adrenalin": ("ats", [SUB + r"myadrenalin\.com"]),
    "qandle": ("ats", [SUB + r"qandle\.com"]),
    "greythr": ("ats", [SUB + r"greythr\.com"]),
    "factohr": ("ats", [SUB + r"factohr\.com"]),
    "pockethrms": ("ats", [SUB + r"pockethrms\.com"]),
    "beehive": ("ats", [SUB + r"beehivehcm\.com"]),
    "empxtrack": ("ats", [SUB + r"empxtrack\.com"]),
    "ceipal": ("ats", [SUB + r"ceipal\.com"]),
    "recooty": ("ats", [SUB + r"recooty\.com"]),
    "ismartrecruit": ("ats", [SUB + r"ismartrecruit\.com"]),
    "pitchnhire": ("ats", [SUB + r"pitchnhire\.com"]),
    "springrecruit": ("ats", [SUB + r"springrecruit\.com"]),
    "expertia": ("ats", [SUB + r"expertia\.ai"]),
    "jobma": ("ats", [SUB + r"jobma\.com"]),
    "talview": ("ats", [SUB + r"talview\.com"]),
    "hirepro": ("ats", [SUB + r"hirepro\.in"]),
    "zappyhire": ("ats", [SUB + r"zappyhire\.com"]),
    "talentrecruit": ("ats", [SUB + r"talentrecruit\.com"]),
    "hiringhood": ("ats", [SUB + r"hiringhood\.com"]),
    "sutrahr": ("ats", [SUB + r"sutrahr\.com"]),
    "hirebuddy": ("ats", [SUB + r"hirebuddy\.net"]),
    # ---------- aggregators: an apply route, not a scrapable per-company board ----------
    "workatastartup": (
        "jobboard",
        [r"(?:www\.)?workatastartup\.com/companies/([a-zA-Z0-9_-]+)"],
    ),
    "talent500": ("jobboard", [r"talent500\.co/(?:jobs|companies)"]),
    "unstop": ("jobboard", [r"unstop\.com/(?:jobs|companies)/([a-zA-Z0-9_-]+)"]),
    "linkedin": (
        "jobboard",
        [
            r"linkedin\.com/jobs/(?:view|search)",
            r"linkedin\.com/company/[a-zA-Z0-9_.-]{2,60}/jobs",
        ],
    ),
    "naukri": (
        "jobboard",
        [r"naukri\.com/[a-z0-9-]{0,60}-jobs", r"naukri\.com/(?:job-listings|jobs)"],
    ),
    "indeed": ("jobboard", [r"indeed\.com/(?:cmp|viewjob|jobs)"]),
    "instahyre": ("jobboard", [r"instahyre\.com/(?:jobs|c)/"]),
    "cutshort": ("jobboard", [r"cutshort\.io/(?:company|jobs)/"]),
    "glassdoor": ("jobboard", [r"glassdoor\.[a-z.]{2,6}/(?:Jobs|job-listing)"]),
    "foundit": ("jobboard", [r"foundit\.in/(?:search|job)"]),
    "apna": ("jobboard", [r"apna\.co/jobs"]),
    "ycombinator": (
        "jobboard",
        [r"(?:www\.)?ycombinator\.com/companies/[a-z0-9-]{2,60}/jobs"],
    ),
    # ---------- DIY: a form or a doc, i.e. genuinely no ATS ----------
    "google_forms": ("diy", [r"docs\.google\.com/forms/", r"forms\.gle/"]),
    "typeform": ("diy", [SUB + r"typeform\.com"]),
    "notion": (
        "diy",
        [r"(?:www\.)?notion\.(?:so|site)/[A-Za-z0-9-]{8,}", SUB + r"notion\.site"],
    ),
    "airtable": ("diy", [r"airtable\.com/(?:shr|app)[A-Za-z0-9]{6,}"]),
    "tally": ("diy", [r"tally\.so/r/[A-Za-z0-9]{4,}"]),
    "fillout": ("diy", [r"forms\.fillout\.com/t/[A-Za-z0-9]{4,}"]),
    "zohoforms": ("diy", [r"forms\.zohopublic\.(?:com|in)/"]),
    "mailto": (
        "diy",
        [
            r"mailto:(?:careers|jobs|hr|hiring|recruit|talent|apply|work)@[a-z0-9.-]{3,60}"
        ],
    ),
}

# Taleo's hostname identifies the platform, not the Board. Enterprise needs its Career Section;
# Business Edition needs shard/instance plus org/cws. A host-only signal remains generic `taleo`
# below, while a concrete URL is promoted to one of the two supported scraper identities.
TALEO_URL = re.compile(r"https?://[a-z0-9.-]+\.taleo\.net/[^\s\"'<>]+", re.IGNORECASE)
TALEO_ENTERPRISE_SECTION = re.compile(r"^/careersection/([^/?#]+)/?", re.IGNORECASE)
TALEO_BE_ROUTE = re.compile(
    r"^(?P<base>/[^/?#]+/ats/careers/v2)/(?:viewRequisition|searchResults)/?$",
    re.IGNORECASE,
)


def taleo_board_from_url(url: str) -> tuple[str, str] | None:
    """Supported Taleo ATS and canonical Board slug from a concrete job/board URL."""
    parsed = urlsplit(html.unescape(url).rstrip(".,;)"))
    host = (parsed.hostname or "").lower()
    if not host.endswith(".taleo.net"):
        return None
    if host.endswith(".tbe.taleo.net"):
        route = TALEO_BE_ROUTE.match(parsed.path)
        query = parse_qs(parsed.query)
        if not route or not query.get("org") or not query.get("cws"):
            return None
        board = urlunsplit(
            (
                "https",
                host,
                route.group("base") + "/searchResults",
                urlencode((("org", query["org"][0]), ("cws", query["cws"][0]))),
                "",
            )
        )
        return "taleo_be", board
    section = TALEO_ENTERPRISE_SECTION.match(parsed.path)
    if not section or section.group(1).lower().endswith(".ftl"):
        return None
    return (
        "taleo_enterprise",
        urlunsplit(("https", host, f"/careersection/{section.group(1)}", "", "")),
    )


# Tokens that are never a real tenant slug — provider infra, generic path words, minifier debris.
# Reused from scripts/resolve/fingerprint.py, which learned most of these the hard way.
BLOCK = {
    "embed",
    "job_board",
    "js",
    "jobs",
    "job",
    "board",
    "boards",
    "api",
    "v0",
    "v1",
    "postings",
    "posting-api",
    "www",
    "careers",
    "career",
    "en",
    "content",
    "static",
    "assets",
    "for",
    "apply",
    "widget",
    "client",
    "public",
    "search",
    "css",
    "images",
    "img",
    "app",
    "help",
    "blog",
    "support",
    "docs",
    "status",
    "mail",
    "portal",
    "secure",
    "login",
    "auth",
    "home",
    "info",
    "go",
    "signup",
    "academy",
    "dbx",
    "explore",
    "newsroom",
    "hr",
    "c",
    "partners",
    "developers",
    "community",
    "events",
    "demo",
    "resources",
    "pricing",
    "about",
    "contact",
    "product",
    "products",
    "news",
    "get",
    "try",
    "marketing",
    "sales",
    "cdn",
    "media",
    "files",
    "download",
    "index",
    "main",
    "account",
    "accounts",
    "settings",
    "admin",
    "dashboard",
    "test",
    "staging",
    "dev",
    "preview",
    "email",
    "link",
    "links",
    "share",
    "track",
    "click",
    "http",
    "https",
}
# SmartRecruiters board ids keep their capitals (8,736 of 12,644 ledger tenants carry them), so
# lower-casing would mint a second, non-matching row for a board we already hold.
CASE_SENSITIVE = {"smartrecruiters", "pyjamahr"}

# Each provider's own registrable domains, so scanning the provider's own site (or a company that
# IS the provider — zoho.com is in this very seed) doesn't self-match its infra as a tenant board.
PROVIDER_DOMAINS = {
    "greenhouse": {"greenhouse.io"},
    "lever": {"lever.co"},
    "ashby": {"ashbyhq.com"},
    "zoho": {"zoho.com", "zohorecruit.com", "zohorecruit.in", "zohocorp.com"},
    "recruitee": {"recruitee.com"},
    "workable": {"workable.com"},
    "darwinbox": {"darwinbox.in", "darwinbox.com"},
    "keka": {"keka.com"},
    "qandle": {"qandle.com"},
    "ripplehire": {"ripplehire.com"},
    "turbohire": {"turbohire.co"},
    "smartrecruiters": {"smartrecruiters.com"},
    "teamtailor": {"teamtailor.com"},
    "freshteam": {"freshteam.com", "freshworks.com"},
    "trakstar": {"trakstar.com"},
    "sensehq": {"sensehq.com"},
    "rippling": {"rippling.com"},
    "personio": {"personio.de", "personio.com"},
    "eightfold": {"eightfold.ai"},
    "zwayam": {"zwayam.com", "openings.co", "naukri.com"},
    "peoplestrong": {"peoplestrong.com"},
    "pyjamahr": {"pyjamahr.com"},
    "icims": {"icims.com"},
    "phenom": {"phenom.com", "phenompeople.com"},
    "greythr": {"greythr.com"},
    "hrone": {"hrone.cloud"},
    "zinghr": {"zinghr.com"},
    "talview": {"talview.com"},
    "expertia": {"expertia.ai"},
    "unstop": {"unstop.com"},
    "linkedin": {"linkedin.com"},
    "naukri": {"naukri.com"},
    "instahyre": {"instahyre.com"},
    "cutshort": {"cutshort.io"},
    "notion": {"notion.so"},
    "airtable": {"airtable.com"},
    "typeform": {"typeform.com"},
    "apna": {"apna.co"},
    "hirebuddy": {"hirebuddy.net"},
}

# The CNAME sweep's zones. A careers-ish label CNAMEing into one of these is decisive — the
# company has pointed DNS at that provider — and costs one UDP query, no HTTP.
CNAME_ZONES = {
    "talentbrew.com": "radancy",
    "radancy.com": "radancy",
    "jibeapply.com": "icims",
    "phenompeople.net": "phenom",
    "myworkdayjobs.com": "workday",
    "myworkdaysite.com": "workday",
    "myworkdaycdn.com": "workday",
    "freshteam.com": "freshteam",
    "darwinbox.in": "darwinbox",
    "darwinbox.com": "darwinbox",
    "keka.com": "keka",
    "zwayam.com": "zwayam",
    "openings.co": "zwayam",
    "greenhouse.io": "greenhouse",
    "lever.co": "lever",
    "ashbyhq.com": "ashby",
    "zohorecruit.com": "zoho",
    "zohorecruit.in": "zoho",
    "recruit.zoho.com": "zoho",
    "recruitee.com": "recruitee",
    "workable.com": "workable",
    "smartrecruiters.com": "smartrecruiters",
    "teamtailor.com": "teamtailor",
    "personio.de": "personio",
    "personio.com": "personio",
    "eightfold.ai": "eightfold",
    "successfactors.com": "successfactors",
    "sapsf.com": "successfactors",
    "sapsf.eu": "successfactors",
    "jobs2web.com": "successfactors",
    "oraclecloud.com": "oracle",
    "taleo.net": "taleo",
    "icims.com": "icims",
    "jobvite.com": "jobvite",
    "phenompeople.com": "phenom",
    "bamboohr.com": "bamboohr",
    "breezy.hr": "breezy",
    "hrmdirect.com": "clearcompany",
    "clearcompany.com": "clearcompany",
    "applytojob.com": "jazzhr",
    "avature.net": "avature",
    "csod.com": "cornerstone",
    "peoplestrong.com": "peoplestrong",
    "turbohire.co": "turbohire",
    "pyjamahr.com": "pyjamahr",
    "skillate.com": "skillate",
    "jobsoid.com": "jobsoid",
    "hrone.cloud": "hrone",
    "zinghr.com": "zinghr",
    "greythr.com": "greythr",
    "hire.trakstar.com": "trakstar",
    "recruiterbox.com": "trakstar",
    "sensehq.com": "sensehq",
    "ripplehire.com": "ripplehire",
    "ats.rippling.com": "rippling",
    "kula.ai": "kula",
    "param.ai": "param",
    "pinpointhq.com": "pinpoint",
    "homerun.co": "homerun",
    "getro.com": "getro",
    "comeet.co": "comeet",
    "recruiterflow.com": "recruiterflow",
    "manatal.com": "manatal",
    "zappyhire.com": "zappyhire",
    "talentrecruit.com": "talentrecruit",
    "hirepro.in": "hirepro",
    "ultipro.com": "ukg",
    "jobscore.com": "jobscore",
}
# Zones whose *single-label* hosts are a different ATS's Board than the zone's CNAME targets.
# `{client}.jibeapply.com` is a Jibe client's own host, while a vanity host CNAMEs to a dotted
# `careers.rm.com.jibeapply.com`, which stays with `CNAME_ZONES` above. `zone_of` checks here first.
DIRECT_LABEL_ZONES = {"jibeapply.com": "jibe"}
CNAME_LABELS = (
    "careers",
    "jobs",
    "career",
    "job",
    "hiring",
    "apply",
    "work",
    "join",
    "talent",
    "recruit",
)

# Paths worth trying on the apex when link-harvesting from the homepage found nothing. The sweep
# stops at the first page that resolves, so ordering is a cost knob, not a correctness one.
CAREERS_PATHS = (
    "/careers",
    "/careers/",
    "/jobs",
    "/join-us",
    "/company/careers",
    "/about/careers",
    "/work-with-us",
    "/career",
    "/life",
    "/careers/jobs",
    "/en/careers",
)
# href text/target hints that mark a link as careers-ish, for the homepage harvest.
LINKISH = re.compile(
    r"career|job|join[-_ ]?us|hiring|we[-_ ]?are[-_ ]?hiring|work[-_ ]?with[-_ ]?us|"
    r"life[-_ ]?at|vacanc|openings|opportunit",
    re.IGNORECASE,
)
SOCIAL_DOMAINS = frozenset(
    {
        "instagram.com",
        "facebook.com",
        "twitter.com",
        "x.com",
        "youtube.com",
        "linkedin.com",
    }
)
HREF = re.compile(
    r"""(?:href|src|data-href|content)\s*=\s*["']([^"'>\s]{1,400})["']""", re.IGNORECASE
)
SCRIPT_SRC = re.compile(
    r"""<script[^>]{0,200}?\ssrc\s*=\s*["']([^"'>\s]{1,400})["']""", re.IGNORECASE
)
# Real Workday URLs carry a locale segment ("/en-US/AcmeCareers"). Without re-checking the site
# capture against this, every localed board resolves to ".../en-US" instead of its real site. The
# bare form is an explicit language list, NOT any two letters: real site names are two letters too
# (Knight Frank's board is ".../KF"), and rejecting those would drop live boards.
LOCALE = re.compile(
    r"^(?:[a-z]{2}-[a-z]{2}|en|es|fr|de|pt|it|ja|zh|ko|nl|ru|ar|pl|tr|sv|da|fi|cs|hu|th|vi|id)$",
    re.IGNORECASE,
)

# How many careers-ish links found *on a careers page* may be queued for a second hop. A careers
# landing page is often marketing with the board one click further in; an unbounded harvest would
# instead walk the whole site off a footer.
SECOND_HOP = 4

# Legal-entity suffixes a company name carries but a board slug never does. Stripped to make an
# EXTRA candidate slug, never to replace the full one.
LEGAL_SUFFIXES = {
    "inc",
    "llc",
    "ltd",
    "limited",
    "pvt",
    "private",
    "plc",
    "corp",
    "corporation",
    "gmbh",
    "ag",
    "se",
    "sa",
    "nv",
    "bv",
    "ab",
    "oy",
    "as",
    "srl",
    "spa",
    "kk",
    "pte",
    "holdings",
    "company",
    "technologies",
    "technology",
    "labs",
    "software",
    "solutions",
    "systems",
    "india",
}

# Stage 6's boards. **Only the ATSes that publish the board owner's name**, because a slug probe
# infers rather than observes and a shared namespace is full of namesakes. Measured 2026-09-07 on
# this seed: probing the identity-less boards too (ashby, lever, freshteam, rippling) produced
# three extra hits — ashby `tilt`, `pulse` and `scribe` — and **all three were other companies**
# (a London bike retailer, a San Francisco startup, a US SaaS firm), against zero true positives.
# There is no non-circular way to confirm them: the obvious "does the board mention the company"
# text check matches the slug that is already embedded in every one of the board's own job URLs,
# and the next-obvious "does it name the company's domain" is too weak in the other direction
# (measured False for the Postman, Groww and Swiggy boards, which are all genuine). So they are
# out, and their absence is a precision choice, not an oversight.
# Endpoints and count functions come from each scraper's own url() in src/headstart/scrapers/.
SLUG_PROBES = {
    "greenhouse": (
        "https://boards-api.greenhouse.io/v1/boards/{s}/jobs",
        lambda d: len(d.get("jobs", [])),
    ),
    "smartrecruiters": (
        "https://api.smartrecruiters.com/v1/companies/{s}/postings",
        lambda d: d.get("totalFound", 0) if isinstance(d, dict) else 0,
    ),
    "workable": (
        "https://apply.workable.com/api/v1/widget/accounts/{s}?details=true",
        lambda d: len(d.get("jobs", [])) if isinstance(d, dict) else 0,
    ),
    "recruitee": (
        "https://{s}.recruitee.com/api/offers/",
        lambda d: len(d.get("offers", [])) if isinstance(d, dict) else 0,
    ),
}

# ATSes whose scraper slug is the **company's own** careers hostname, not a label in the
# provider's namespace.  A CNAME target such as `15544.jobs2web.com` proves SuccessFactors but
# cannot be scraped; the queried host can.  The same contract is explicit in the Phenom and
# iCIMS scrapers, which each call their slug the board host.  This is deliberately a per-scraper
# shape table rather than an inference from provider DNS.
QUERY_HOST_ATS = frozenset({"successfactors", "zwayam", "phenom", "icims"})
# zoho's slug is a full host as well, but a matched `*.zohorecruit.*` host is already correct —
# only the vanity-domain fingerprint (which captures nothing) needs the evidence host instead.
HOST_SLUG_ATS = frozenset({"zoho"})
# ATSes whose slug is a full host inside the provider's own zone (oracle.py: "the slug is the
# careers host"; eightfold and personio the same), so the CNAME target *is* the right answer.
PROVIDER_HOST_ATS = frozenset({"oracle", "eightfold", "personio"})

# A provider can be conclusively detected while still not yielding a tenant the scraper can use.
# Workday needs the path from a careers URL, and `ext.teamtailor.com` is Teamtailor infrastructure
# rather than a company label.  These detections must never be presented as candidate Boards.
TEAMTAILOR_INFRA = frozenset({"ext", "www", "jobs", "careers"})
# These providers use a provider-subdomain slug and no path. A CNAME target is usable only after
# its provider suffix is stripped; every other CNAME zone is detection-only until URL/page
# evidence supplies the shape its scraper expects.
CNAME_LABEL_ATS = frozenset(
    {
        "bamboohr",
        "breezy",
        "clearcompany",
        "darwinbox",
        "freshteam",
        "jibe",
        "keka",
        "recruitee",
        "sensehq",
        "teamtailor",
        "trakstar",
        "workable",
    }
)

# Bump when a probe gains a materially new signal.  Resume skips only a row from this exact
# channel set, and never suppresses an unreachable result.
CHANNELS = "apply-url+cname-chain+http+robots+sitemap+jsbundle+slugprobe:v5"
if _DNS is None:
    CHANNELS += ":no-dns"
CHANNELS += ":psl-v1"


def channels_for(deep: bool) -> str:
    return CHANNELS + (":cert+api+browser-v2" if deep else "")


FIELDS = [
    "job_evidence",
    "matched_apply_urls",
    "certificate_hosts",
    "company",
    "domain",
    "input_kind",
    "input_id",
    "apply_url",
    "employer_key",
    "employer_keys",
    "sample_apply_urls",
    "jobs",
    "channels",
    "status",
    "ats",
    "kind",
    "supported",
    "tenant",
    "board_key",
    "candidate",
    "signal",
    "evidence_url",
    "other_hits",
    "pages_ok",
    "pages_err",
    "note",
    "verification",
    "verify_url",
    "verify_detail",
]


@dataclass(frozen=True, slots=True)
class HostSeed:
    """One deduplicated, evidence-preserving apply-host input."""

    company: str
    host: str
    input_id: str
    apply_url: str
    employer_key: str
    jobs: int
    employer_keys: tuple[str, ...] = ()
    sample_apply_urls: tuple[str, ...] = ()


_local = threading.local()
_print_lock = threading.Lock()
_post_banned: set[str] = set()


@lru_cache(maxsize=8192)
def _post_host_gate(host: str):
    return threading.BoundedSemaphore(2)


def session():
    """This thread's pooled curl_cffi session.

    Chrome impersonation gets us past TLS-fingerprint bot walls (Cloudflare/Akamai 403 a plain
    stack outright, regardless of headers), while the User-Agent stays honest about who we are.
    """
    s = getattr(_local, "s", None)
    if s is None:
        s = _requests.Session(impersonate="chrome")
        s.headers.update({"User-Agent": UA})
        _local.s = s
    return s


def reg_domain(host: str) -> str:
    """Registrable domain from the packaged PSL, including private hosting suffixes."""
    return public_domain(host)


def scan(
    text: str, self_domain: str, *, allow_provider_host: bool = False
) -> list[tuple[str, str, str, int]]:
    """Every ATS/jobboard/diy reference in `text`, as (ats, kind, tenant, hits).

    Self-referential matches are dropped: a company that IS a provider (zoho.com sits in this very
    seed) otherwise matches its own infra subdomains as if they were a tenant board.
    """
    rd = reg_domain(self_domain)
    hits: list[tuple[str, str, str]] = []
    counts: Counter[tuple[str, str, str]] = Counter()
    for match in TALEO_URL.finditer(text):
        resolved = taleo_board_from_url(match.group(0))
        if not resolved:
            continue
        ats, tenant = resolved
        key = (ats, "ats", tenant)
        if key not in counts:
            hits.append(key)
        counts[key] += 1
    for ats, (kind, pats) in PATTERNS.items():
        if not allow_provider_host and rd in PROVIDER_DOMAINS.get(ats, set()):
            continue
        for p in pats:
            for m in re.finditer(
                HOST + "(?:" + p + r")(?![a-z0-9_.-])", text, re.IGNORECASE
            ):
                if ats == "workday" and m.lastindex and m.lastindex >= 3:
                    co, pod, site = m.group(1).lower(), m.group(2).lower(), m.group(3)
                    if ".myworkdaysite.com/" in m.group(0).lower():
                        co, pod = pod, co
                    if site.lower() in {
                        "job",
                        "jobs",
                        "wday",
                        "recruiting",
                    } or LOCALE.match(site):
                        continue
                    # The Workday scraper's slug IS the full board URL (workday.py slug_from), so
                    # emitting "{co}/{site}" would drop the pod and be unusable downstream.
                    tok = f"https://{co}.{pod}.myworkdayjobs.com/{site}"
                elif ats == "adp":
                    # The whole query is captured; only `cid` + `ccId` name the Board, the same
                    # reading every other ADP discovery source makes (`wayback_feeder.extract`).
                    got = (
                        extract(
                            f"https://{ADP_HOST}/{ADP_PAGE_PATH}?"
                            + html.unescape(m.group(1)),
                            ADP_HOST,
                            "adp",
                        )
                        if m.lastindex
                        else None
                    )
                    tok = got[0] if got else ""
                else:
                    raw = (m.group(1) if m.lastindex else "") or ""
                    tok = raw if ats in CASE_SENSITIVE else raw.lower()
                    if tok:
                        lo = tok.lower()
                        # 3-60 chars: a 1-2 char token is almost always minified-JS debris (a
                        # stray `apply.workable.com/j` -> "j"), never a board slug. The split()
                        # check screens host-shaped tokens, where the blocked word leads.
                        if (
                            not (3 <= len(tok) <= 60)
                            or lo in BLOCK
                            or ("." not in tok and lo.split(".")[0] in BLOCK)
                        ):
                            continue
                key = (ats, kind, tok)
                if key not in counts:
                    hits.append((ats, kind, tok))
                counts[key] += 1
    return [(ats, kind, tok, counts[(ats, kind, tok)]) for ats, kind, tok in hits]


def get(url: str, cap: int = PAGE_CAP) -> tuple[str, str, str]:
    """Fetch `url` following redirects. Returns (body, final_url, error).

    The error is *returned*, not raised, so the caller can keep the two buckets apart: a page that
    failed to load is NOT evidence that a company has no ATS.
    """
    try:
        r = session().get(url, timeout=TIMEOUT, allow_redirects=True, verify=False)
    except Exception as exc:  # noqa: BLE001
        return "", url, type(exc).__name__
    final = str(getattr(r, "url", url))
    body = (r.content or b"")[:cap].decode("utf-8", "replace")
    if r.status_code >= 400:
        # A 4xx body is still worth scanning — a custom 404 on careers.acme.com sometimes carries
        # the ATS widget — but the status rides along so it never reads as a clean negative.
        return body, final, f"http{r.status_code}"
    ct = r.headers.get("content-type", "")
    if ct and not any(x in ct for x in ("html", "text", "javascript", "json", "xml")):
        return "", final, "content-type"
    return body, final, ""


def post_json(url: str, headers: dict, body) -> tuple[dict | None, str]:
    """One bounded public listing POST; stop shared-API probing after a throttle response."""
    host = urlsplit(url).hostname or ""
    with _post_host_gate(host):
        if host in _post_banned:
            return None, "throttled"
        try:
            kwargs = {"json": body} if isinstance(body, dict) else {"data": body}
            response = session().post(
                url, headers=headers, timeout=7, verify=certifi.where(), **kwargs
            )
            if response.status_code == 429 or (
                host == "public.zwayam.com" and response.status_code == 403
            ):
                _post_banned.add(host)
            if response.status_code != 200:
                return None, f"http{response.status_code}"
            data = response.json()
            return (data, "") if isinstance(data, dict) else (None, "not-object")
        except Exception as exc:  # noqa: BLE001
            return None, type(exc).__name__


def cname_chain(host: str, depth: int = 4) -> list[str]:
    """Follow CNAMEs from `host`, with bounded loop-safe traversal.

    A resolver lookup returns only the current hop on several DNS stacks.  A provider zone may be
    reached only after an intermediate CDN name, so returning one answer is not a chain walk.
    """
    if _DNS is None:
        return []
    found: list[str] = []
    pending = [(host.lower().rstrip("."), 0)]
    seen = {pending[0][0]}
    while pending:
        current, level = pending.pop(0)
        if level >= depth:
            continue
        try:
            targets = [
                str(r.target).rstrip(".").lower()
                for r in _DNS.resolve(current, "CNAME")
            ]
        except _DNS_EMPTY_ERRORS:
            targets = []
        except Exception:  # noqa: BLE001
            _local.dns_errors = getattr(_local, "dns_errors", 0) + 1
            targets = []
        for target in targets:
            if target in seen:
                continue
            seen.add(target)
            found.append(target)
            pending.append((target, level + 1))
    return found


def candidate_slugs(name: str, domain: str) -> list[str]:
    """Slugs worth asking a shared-namespace board about, for one company.

    Kept deliberately narrow — the domain label and the squashed name (with and without a legal
    suffix). Wider guessing buys namesake false positives, not coverage. Minimum length 3: a
    two-char slug is too generic and collides with unrelated boards.
    """
    out: list[str] = []
    label = domain.split("/")[0].split(".")[0].lower()
    if label and label != "www":
        out.append(label)
    words = re.findall(r"[a-z0-9]+", name.lower())
    if words:
        out.append("".join(words))
        trimmed = list(words)
        while len(trimmed) > 1 and trimmed[-1] in LEGAL_SUFFIXES:
            trimmed.pop()
        if trimmed != words:
            out.append("".join(trimmed))
    seen: set[str] = set()
    return [
        s
        for s in out
        if 3 <= len(s) <= 60 and s not in BLOCK and not (s in seen or seen.add(s))
    ]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def declared_name(ats: str, slug: str, payload) -> str | None:
    """The name the board itself claims. Every ATS in :data:`SLUG_PROBES` publishes one.

    Measured 2026-09-07 against the live APIs: greenhouse needs a second call to `/v1/boards/{s}`;
    workable returns `name` in the widget payload, smartrecruiters `content[].company.name`,
    recruitee `offers[].company_name`.
    """
    try:
        if ats == "greenhouse":
            body, _f, _e = get(f"https://boards-api.greenhouse.io/v1/boards/{slug}")
            return json.loads(body).get("name")
        if ats == "workable":
            return payload.get("name")
        if ats == "smartrecruiters":
            return payload["content"][0]["company"]["name"]
        if ats == "recruitee":
            return payload["offers"][0].get("company_name")
    except Exception:  # noqa: BLE001
        return None
    return None


def slug_confirms(ats: str, slug: str, payload, company: str) -> tuple[bool, str]:
    """Is the board this slug found actually *this* company's? -> (accepted, what the board said).

    A shared namespace hands back namesakes, and they are not rare: on this seed
    `boards-api.greenhouse.io/v1/boards/tcs/jobs` returns 108 live jobs belonging to **Thornbury
    Community Services**, a UK healthcare provider, and `/boards/pine/jobs` returns a Canadian
    mortgage brokerage, not Pine Labs.

    So the bar is the board's own declared name matching the seed company's **exactly** once both
    are reduced to letters and digits. Deliberately strict, in both directions:

    * No suffix stripping. "Pine Labs" vs a board named "Pine" would pass any tolerant rule —
      `labs` is a legal-suffix word — and it is wrong.
    * No substring containment, for the same reason.

    The cost is real: a board named "Zetwerk" for a company seeded as "Zetwerk Manufacturing"
    would be refused. That is the right trade for a *coverage measurement*, where a false
    positive corrupts the headline and a false negative only understates it — and every refusal
    is written to the row's `note` with the name the board gave, so a human can overturn it.
    """
    name = declared_name(ats, slug, payload)
    if not name:
        return False, "no declared name"
    return _norm(name) == _norm(company), f"name={name}"


def normalise_tenant(ats: str, tenant: str, evidence: str) -> str:
    """Return the slug the named scraper can use, or ``""`` if evidence lacks one.

    Detection and candidate construction are intentionally separate: a provider CNAME can be
    conclusive while still lacking Workday's site path or a Teamtailor company label.  Returning an
    empty string makes that state explicit instead of minting a plausible-but-broken Board.
    """
    if not evidence:
        return tenant
    if " API " in evidence:
        return tenant
    source_host = evidence.split(" CNAME ")[0].lower() if " CNAME " in evidence else ""
    if ats in QUERY_HOST_ATS and source_host:
        return source_host
    if ats in QUERY_HOST_ATS:
        # An explicit board link on a corporate homepage retains its provider host. Vendor
        # assets (e.g. cdn.phenompeople.com) only fingerprint the page serving the careers UI.
        if "." in tenant and tenant.split(".")[0] not in {
            "cdn",
            "assets",
            "static",
            "api",
            "www",
            "rmkcdn",
            "public",
        }:
            return tenant
        return urlsplit(evidence).hostname or ""
    if ats in CNAME_LABEL_ATS and (" CNAME " in evidence or "." in tenant):
        matched = _zone_match(tenant)
        if not matched:
            return ""
        zone, _provider = matched
        label = tenant.removesuffix("." + zone)
        if "." in label or label in BLOCK or label in TEAMTAILOR_INFRA:
            return ""
        return label
    if " CNAME " in evidence:
        return tenant if ats in PROVIDER_HOST_ATS | HOST_SLUG_ATS else ""
    if ats in HOST_SLUG_ATS:
        return (
            tenant if "." in tenant else (urlsplit(evidence).netloc.lower() or tenant)
        )
    if ats == "workday":
        return tenant if tenant.startswith("http") else ""
    if ats == "pyjamahr":
        return tenant if "." not in tenant and not tenant.startswith("http") else ""
    if ats == "teamtailor" and tenant in TEAMTAILOR_INFRA:
        return ""
    return tenant


def candidate_identity(ats: str, tenant: str, company: str) -> tuple[str, str]:
    """Build a candidate's real Board key without claiming it is live.

    This crosses the same `board_key()` seam as the pipeline.  A malformed slug or an unsupported
    ATS is therefore visible in the artifact rather than becoming a hand-written ledger row.
    """
    if not ats:
        return "", ""
    if ats not in SUPPORTED:
        return "", "unsupported"
    if not tenant:
        return "", "detected-needs-url-evidence"
    try:
        return board_key(CompanyRef(ats, tenant, company or None)), "unverified"
    except Exception as exc:  # noqa: BLE001
        return "", f"board-key-error:{type(exc).__name__}"


def _zone_match(host: str) -> tuple[str, str] | None:
    h = host.lower().rstrip(".")
    matches = [
        (len(zone), zone, ats)
        for zone, ats in CNAME_ZONES.items()
        if h == zone or h.endswith("." + zone)
    ]
    _length, zone, ats = max(matches, default=(0, "", None))
    return (zone, ats) if ats else None


def zone_of(host: str) -> str | None:
    h = host.lower().rstrip(".")
    for zone, ats in DIRECT_LABEL_ZONES.items():
        label = h.removesuffix("." + zone)
        if label != h and "." not in label:
            return ats
    matched = _zone_match(host)
    return matched[1] if matched else None


def cname_hits(
    host: str, self_domain: str, *, allow_provider_host: bool = False
) -> list[tuple[str, str, str, str, str, int]]:
    """Classify `host` itself and every bounded CNAME target.

    A host inside a provider zone (`anaqua.bamboohr.com`) is already a positive signal even when
    it has no CNAME record.  The raw host is also retained as the source in a CNAME evidence
    string, letting :func:`normalise_tenant` distinguish host-keyed scrapers from provider hosts.
    """
    host = host.lower().rstrip(".")
    targets = cname_chain(host)
    chain = " -> ".join(targets)
    evidence = f"{host} CNAME {chain}" if chain else f"https://{host}/"
    out: list[tuple[str, str, str, str, str, int]] = []
    for candidate in [host, *targets]:
        ats = zone_of(candidate)
        if not ats or (
            not allow_provider_host
            and reg_domain(self_domain) in PROVIDER_DOMAINS.get(ats, set())
        ):
            continue
        kind = PATTERNS.get(ats, ("ats", []))[0]
        out.append((ats, kind, candidate, "cname", evidence, 1))
    return out


def probe(
    company: str,
    domain: str,
    *,
    cname_hosts: tuple[str, ...] = (),
    initial_urls: tuple[str, ...] = (),
    generated_career_hosts: bool = True,
    deep: bool = False,
) -> dict:
    """Resolve one company from a company domain or supplied careers host.

    ``scan`` supplies the former and synthesises careers labels.  ``indeed`` supplies the latter:
    its apply URL is scanned first and the concrete host, not `careers.<host>`, is DNS-probed.
    Both routes share every later evidence rung and one candidate-construction seam.
    """
    domain = re.sub(r"^https?://", "", domain.strip().lower()).strip("/")
    hits: list[
        tuple[str, str, str, str, str, int]
    ] = []  # (ats, kind, tenant, signal, evidence, hit_count)
    ok = err = 0
    notes: list[str] = []
    _local.dns_errors = 0
    cert_hosts: list[str] = []
    observed_pages: list[tuple[str, str]] = []
    render_target: str | None = None

    def scan_here(text):
        return scan(text, domain, allow_provider_host=not generated_career_hosts)

    def record(found, signal, evidence):
        for ats, kind, tok, count in found:
            hits.append((ats, kind, tok, signal, evidence, count))

    def has_candidate() -> bool:
        for ats, kind, token, _signal, evidence, _count in hits:
            if kind != "ats":
                continue
            _key, state = candidate_identity(
                ats, normalise_tenant(ats, token, evidence), company
            )
            if state == "unverified":
                return True
        return False

    def fetch_and_record(
        url: str, signal: str, cap: int = PAGE_CAP
    ) -> tuple[str, str, str]:
        """Keep failure evidence scannable without letting it settle a negative result."""
        nonlocal ok, err, render_target
        body, final, error = get(url, cap=cap)
        if reg_domain(urlsplit(final).hostname or "") in SOCIAL_DOMAINS:
            # Social pages contain platform-wide third-party URLs unrelated to this employer.
            return "", final, "social-page"
        if body or final != url:
            record(scan_here(final + "\n" + body), signal, final)
            if len(observed_pages) < 4:
                observed_pages.append((body, final))
        if error:
            err += 1
        elif body:
            ok += 1
            if signal == "apply-url" and render_target is None:
                render_target = final
        else:
            err += 1
        return body, final, error

    # --- 1. Scan supplied URLs without fetching; provider paths often carry the whole identity.
    for url in initial_urls:
        record(scan_here(url), "apply-url", url)

    # --- 2. CNAME sweep: test the supplied host itself, then bounded targets.
    rd = reg_domain(domain)
    hosts = cname_hosts or tuple(f"{label}.{domain}" for label in CNAME_LABELS)
    for host in hosts:
        if has_candidate():
            break
        hits.extend(
            cname_hits(host, rd, allow_provider_host=not generated_career_hosts)
        )
        if has_candidate():
            break

    if _local.dns_errors:
        err += _local.dns_errors
        notes.append(f"dns-failures:{_local.dns_errors}")

    # Fetch only while identity is missing. This also follows Greenhouse short links.
    for url in initial_urls:
        if has_candidate():
            break
        fetch_and_record(url, "apply-url")

    # --- 3. apex page: scan it, and harvest careers-ish links off it.
    body, final, e = ("", f"https://{domain}/", "")
    if not has_candidate():
        body, final, e = fetch_and_record(f"https://{domain}/", "homepage")
    if e:
        notes.append(f"root:{e}")
    links = [
        urljoin(final or f"https://{domain}/", m.group(1))
        for m in HREF.finditer(body or "")
        if LINKISH.search(m.group(1))
        and reg_domain(urlsplit(urljoin(final, m.group(1))).hostname or "")
        not in SOCIAL_DOMAINS
    ]
    # dedupe, keep order: a nav bar repeats the same careers link a dozen times
    seen_l: set[str] = set()
    ordered: list[str] = []
    for u in links:
        k = u.split("#")[0].rstrip("/")
        if k not in seen_l and u.startswith("http"):
            seen_l.add(k)
            ordered.append(u)
    # A harvested link that IS an ATS host resolves the company without another fetch.
    if ordered:
        for link in ordered:
            record(scan_here(link), "homepage", link)

    # --- 4. careers hosts + harvested links + the standard paths, until something matches.
    # The queue grows as it drains: a careers *landing* page is very often marketing, with the
    # actual board one hop further in ("/company/careers/" -> "/company/careers/open-positions/",
    # which is where Postman's Greenhouse board lives). So careers-ish links found on a careers
    # page are appended too — bounded to SECOND_HOP so a link-farm footer can't run away with it.
    candidates = (
        [f"https://careers.{domain}/", f"https://jobs.{domain}/"]
        if generated_career_hosts
        else []
    )
    candidates += ordered[:4]
    candidates += [f"https://{domain}{p}" for p in CAREERS_PATHS[:6]]
    tried: set[str] = {f"https://{domain}"}
    best_careers_page: tuple[str, str] | None = None
    second_hop = 0
    while candidates:
        if has_candidate():
            break
        url = candidates.pop(0)
        key = url.split("#")[0].rstrip("/")
        if key in tried:
            continue
        tried.add(key)
        body, final, _e = fetch_and_record(url, "page")
        if not body:
            continue
        # The final URL matters as much as the body: careers.acme.com often 301s onto the ATS.
        offsite = reg_domain(urlsplit(final).netloc) != reg_domain(domain)
        # Re-record redirect evidence with its stronger signal; page evidence is already retained.
        if offsite:
            record(scan_here(final), "redirect", final)
        if best_careers_page is None and not _e:
            best_careers_page = (body, final)
        if second_hop < SECOND_HOP and re.search(
            r"career|job|hiring|opening", final, re.IGNORECASE
        ):
            for m in HREF.finditer(body):
                nxt = urljoin(final, m.group(1))
                if not LINKISH.search(m.group(1)) or not nxt.startswith("http"):
                    continue
                if reg_domain(urlsplit(nxt).hostname or "") in SOCIAL_DOMAINS:
                    continue
                if nxt.split("#")[0].rstrip("/") in tried or nxt in candidates:
                    continue
                candidates.append(nxt)
                second_hop += 1
                if second_hop >= SECOND_HOP:
                    break

    # --- 5. robots.txt / sitemap.xml — these routinely name the ATS host outright.
    for path, signal, cap in (
        ("/robots.txt", "robots", 200_000),
        ("/sitemap.xml", "sitemap", 400_000),
    ):
        if has_candidate():
            break
        b, _f, _e = fetch_and_record(f"https://{domain}{path}", signal, cap=cap)
        if not b:
            continue

    # --- 6. last rung: the SPA's own JS bundle. Only for companies still unresolved, capped at 3
    # same-origin scripts, because this is the expensive signal (bundles run to megabytes).
    if not has_candidate() and best_careers_page:
        body, final = best_careers_page
        srcs = [urljoin(final, m.group(1)) for m in SCRIPT_SRC.finditer(body)]
        srcs = [u for u in srcs if reg_domain(urlsplit(u).netloc) == reg_domain(domain)]
        srcs.sort(
            key=lambda u: (
                0 if re.search(r"main|app|index|bundle|chunk", u, re.IGNORECASE) else 1
            )
        )
        for u in srcs[:3]:
            js, _jf, _e = fetch_and_record(u, "jsbundle", cap=BUNDLE_CAP)
            if not js:
                continue
            if has_candidate():
                break

    # Opt-in deep pass: each channel has its own fixed network/resource bound. Certificate
    # siblings from other companies are retained as seeds, never attributed to this employer.
    if deep and not has_candidate():
        cert_hosts, cert_error = certificate_names(domain)
        if cert_error:
            err += 1
            notes.append(f"certificate:{cert_error}")
        for host in (
            certificate_career_hosts(domain, cert_hosts) if not zone_of(domain) else []
        ):
            hits.extend(cname_hits(host, rd, allow_provider_host=True))
            if has_candidate():
                break
            fetch_and_record(f"https://{host}/", "certificate")
            if has_candidate():
                break

    if deep and not has_candidate():
        target = render_target or (best_careers_page or ("", f"https://{domain}/"))[1]
        api_host = urlsplit(target).hostname or domain
        api_hits, errors = api_signatures(
            api_host, "\n".join(b for b, _u in observed_pages), get, post_json
        )
        for ats_name, token, endpoint in api_hits:
            hits.append(
                (ats_name, "ats", token, "api", f"{api_host} API {endpoint}", 1)
            )
        err += sum(e not in {"http404", "http410"} for e in errors)
        if errors:
            notes.append("api-errors:" + ",".join(sorted(set(errors))))

    if deep and not has_candidate():
        target = render_target or (best_careers_page or ("", f"https://{domain}/"))[1]
        rendered, final, urls, browser_error = browser_page(target)
        if reg_domain(urlsplit(final).hostname or "") not in SOCIAL_DOMAINS:
            record(scan_here(rendered + "\n" + "\n".join(urls)), "browser", final)
        if browser_error:
            err += 1
            notes.append(f"browser:{browser_error}")
        elif rendered:
            ok += 1

    # --- 7. slug probe: ask the clean-JSON boards directly whether this company has one.
    # This is the stage whose absence made the first pass' number wrong, not just incomplete —
    # `boards-api.greenhouse.io/v1/boards/postman/jobs` returns 63 live jobs, and Postman was
    # still recorded opaque. No amount of page-scanning finds a board a company never links to
    # (Postman's own careers page names no ATS host anywhere, in HTML or bundle).
    # It is the LAST stage on purpose: it infers a slug rather than observing a link, so a
    # namesake board on a shared ATS would be a false positive. `signal=slugprobe` keeps those
    # rows separable from the observed ones, and `jobs>0` is required.
    if not has_candidate():
        for ats, (tmpl, count) in SLUG_PROBES.items():
            for slug in candidate_slugs(company, domain):
                body, final, e = get(tmpl.format(s=slug), cap=2_000_000)
                if not body or e:
                    err += 1
                    continue
                ok += 1
                try:
                    payload = json.loads(body)
                    n = count(payload)
                except Exception:  # noqa: BLE001, S112 - a non-JSON body is just "no board"
                    continue
                if n <= 0:
                    continue
                accepted, said = slug_confirms(ats, slug, payload, company)
                if not accepted:
                    notes.append(f"rejected {ats}:{slug} ({said})")
                    continue
                notes.append(said)
                hits.append((ats, "ats", slug, "slugprobe", final, 1))
            if has_candidate():
                break

    # --- settle. Signal precedence picks the primary hit; kind picks the bucket.
    order = {
        "apply-url": 0,
        "cname": 1,
        "redirect": 2,
        "page": 3,
        "homepage": 4,
        "robots": 5,
        "sitemap": 6,
        "jsbundle": 7,
        "slugprobe": 8,
        "certificate": 5,
        "api": 2,
        "browser": 7,
    }
    ats, kind, tok, signal, ev = "", "", "", "", ""
    for want, state in (("ats", "resolved"), ("jobboard", "jobboard"), ("diy", "diy")):
        pool = [h for h in hits if h[1] == want]
        if pool:
            if want == "ats":
                usable = [
                    hit
                    for hit in pool
                    if candidate_identity(
                        hit[0], normalise_tenant(hit[0], hit[2], hit[4]), company
                    )[1]
                    == "unverified"
                ]
                pool = usable or pool
            ats, kind, tok, signal, ev, _count = min(
                pool, key=lambda h: (order.get(h[3], 9), -h[5])
            )
            status = state
            break
    else:
        # No hit at all. `none` is a settled negative (something loaded and was clean);
        # `unreachable` says only that we never saw the site, and must never be counted as one.
        status = "none" if ok else "unreachable"
    tok = normalise_tenant(ats, tok, ev)
    key, candidate = candidate_identity(ats, tok, company)
    if kind != "ats":
        key, candidate = "", ""
    elif signal == "slugprobe" and key:
        # A provider's self-declared company name is not proof of corporate ownership. This
        # caught a public Recruitee board calling itself Google during the unknown-host sweep.
        candidate = "inferred-needs-affiliation"
    others = sorted(
        {f"{a}:{t}" for a, _k, t, _s, _e, _n in hits}
        - ({f"{ats}:{tok}"} if ats else set())
    )
    return {
        "certificate_hosts": "|".join(cert_hosts),
        "company": company,
        "domain": domain,
        "input_kind": "domain",
        "input_id": domain,
        "apply_url": "",
        "employer_key": "",
        "employer_keys": "",
        "sample_apply_urls": "",
        "jobs": "",
        "channels": channels_for(deep),
        "status": status,
        "ats": ats,
        "kind": kind,
        "supported": ("yes" if ats in SUPPORTED else "no") if kind == "ats" else "",
        "tenant": tok,
        "board_key": key,
        "candidate": candidate,
        "signal": signal,
        "evidence_url": ev,
        "other_hits": " ".join(others[:8]),
        "pages_ok": ok,
        "pages_err": err,
        "note": ";".join(notes[:8]),
        "verification": "",
        "verify_url": "",
        "verify_detail": "",
    }


def probe_host(seed: HostSeed, *, deep: bool = False) -> dict:
    """Fingerprint a supplied careers host while preserving its full apply URL evidence."""
    row = probe(
        seed.company,
        seed.host,
        cname_hosts=(seed.host,),
        initial_urls=seed.sample_apply_urls
        or ((seed.apply_url,) if seed.apply_url else ()),
        generated_career_hosts=False,
        deep=deep,
    )
    row |= {
        "input_kind": "indeed",
        "input_id": seed.input_id,
        "apply_url": seed.apply_url,
        "employer_key": seed.employer_key,
        "employer_keys": "|".join(seed.employer_keys),
        "sample_apply_urls": "|".join(seed.sample_apply_urls),
        "jobs": seed.jobs,
    }
    if len(seed.employer_keys) > 1 and seed.input_id == seed.host and row["board_key"]:
        # Old host-only snapshots can mix unrelated employers behind redirect wrappers.
        # Preserve detection evidence, but never assign all their postings to the first board.
        row["candidate"] = "ambiguous-employers"
    return row


# A shared URL host has no tenant identity, but these paths do.  Keeping each path lets a Greenhouse
# short-link follow reveal its target and lets `jobs.pyjamahr.com/{slug}` retain the company slug.
PATH_IDENTITY_HOSTS = frozenset(
    {
        "api.ashbyhq.com",
        "jobs.ashbyhq.com",
        "boards.greenhouse.io",
        "job-boards.greenhouse.io",
        "boards.eu.greenhouse.io",
        "job-boards.eu.greenhouse.io",
        "jobs.lever.co",
        "jobs.eu.lever.co",
        "apply.workable.com",
        "grnh.se",
        "jobs.pyjamahr.com",
        "jobs.smartrecruiters.com",
        "careers.smartrecruiters.com",
    }
)


def indeed_seeds(path: Path, include_resolved: bool = False) -> list[HostSeed]:
    """Group an Indeed JSONL harvest into stable, evidence-carrying careers-host probes."""
    grouped: dict[str, dict] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                job = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                not include_resolved
                and job.get("_ats") not in {"vanity", "taleo"}
                and (job.get("_ats_slug") or job.get("_apply_host") != "grnh.se")
            ):
                continue
            recruit = job.get("recruit") if isinstance(job.get("recruit"), dict) else {}
            apply_url = str(recruit.get("viewJobUrl") or "").strip()
            supplied_host = (
                str(job.get("_apply_host") or "").strip().lower().rstrip(".")
            )
            host = (urlsplit(apply_url).hostname or supplied_host).lower().rstrip(".")
            if not host:
                continue
            employer = (
                job.get("employer") if isinstance(job.get("employer"), dict) else {}
            )
            source = job.get("source") if isinstance(job.get("source"), dict) else {}
            company = str(employer.get("name") or source.get("name") or host).strip()
            employer_key = str(employer.get("key") or "").strip()
            input_id = apply_url if host in PATH_IDENTITY_HOSTS and apply_url else host
            if employer_key and host not in PATH_IDENTITY_HOSTS:
                input_id = f"{host}#employer={employer_key}"
            if apply_url and (
                host in PATH_IDENTITY_HOSTS
                or host.endswith(
                    (".myworkdayjobs.com", ".myworkdaysite.com", ".taleo.net")
                )
            ):
                # Shared providers group by Board, not by job URL or provider host. Short links
                # remain URL-scoped until the redirect reveals their identity.
                for ats, kind, token, _count in scan(
                    apply_url, host, allow_provider_host=True
                ):
                    if kind != "ats":
                        continue
                    key, state = candidate_identity(
                        ats, normalise_tenant(ats, token, apply_url), company
                    )
                    if state == "unverified":
                        input_id = key
                        break
            item = grouped.setdefault(
                input_id,
                {
                    "company": company,
                    "host": host,
                    "input_id": input_id,
                    "apply_url": apply_url,
                    "employer_key": employer_key,
                    "jobs": 0,
                    "employer_keys": set(),
                    "sample_apply_urls": [],
                },
            )
            item["jobs"] += 1
            if employer_key:
                item["employer_keys"].add(employer_key)
            if (
                apply_url
                and len(item["sample_apply_urls"]) < 8
                and apply_url not in item["sample_apply_urls"]
            ):
                item["sample_apply_urls"].append(apply_url)
    return [
        HostSeed(
            company=item["company"],
            host=item["host"],
            input_id=item["input_id"],
            apply_url=item["apply_url"],
            employer_key=item["employer_key"],
            jobs=item["jobs"],
            employer_keys=tuple(sorted(item["employer_keys"])),
            sample_apply_urls=tuple(item["sample_apply_urls"]),
        )
        for item in sorted(grouped.values(), key=lambda s: s["input_id"])
    ]


def _old_rows(out: Path) -> dict[str, dict]:
    if not out.exists():
        return {}
    with out.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != FIELDS:
            raise ValueError(
                f"{out} has an older fingerprint schema; choose a new output path rather than "
                "appending incompatible rows"
            )
        return {r["input_id"]: r for r in reader if r.get("input_id")}


def _error_row(seed: HostSeed, input_kind: str, exc: Exception) -> dict:
    row = dict.fromkeys(FIELDS, "")
    row |= {
        "company": seed.company,
        "domain": seed.host,
        "input_kind": input_kind,
        "input_id": seed.input_id,
        "apply_url": seed.apply_url,
        "employer_key": seed.employer_key,
        "employer_keys": "|".join(seed.employer_keys),
        "sample_apply_urls": "|".join(seed.sample_apply_urls),
        "jobs": seed.jobs or "",
        "channels": CHANNELS,
        "status": "unreachable",
        "pages_ok": 0,
        "pages_err": 1,
        "note": f"probe:{type(exc).__name__}",
    }
    return row


def _run_scan(args, seeds: list[HostSeed], input_kind: str, runner) -> None:
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    old = _old_rows(out)
    # A growing harvest changes a host's job count without changing its DNS or board identity.
    # `jobs` is provenance, never a reason to append a duplicate probe result.
    todo = [
        seed
        for seed in seeds
        if not (
            (prior := old.get(seed.input_id))
            and prior.get("input_kind") == input_kind
            and prior.get("channels") == channels_for(getattr(args, "deep", False))
            and prior.get("status") != "unreachable"
            and not (
                prior.get("candidate") != "unverified"
                and int(prior.get("pages_err") or 0)
            )
            and set(seed.sample_apply_urls).issubset(
                set(prior.get("sample_apply_urls", "").split("|"))
            )
        )
    ]
    print(
        f"{len(seeds)} seeded, {len(seeds) - len(todo)} current, {len(todo)} to probe",
        flush=True,
    )
    fresh = not out.exists()
    with out.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if fresh:
            w.writeheader()
            fh.flush()
        pending = {s.input_id for s in todo}
        for seed in seeds:
            if seed.input_id in pending:
                continue
            prior = old[seed.input_id]
            metadata = {
                "jobs": str(seed.jobs),
                "employer_keys": "|".join(seed.employer_keys),
            }
            if any(prior.get(k) != v for k, v in metadata.items()):
                w.writerow(prior | metadata)
        fh.flush()
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futs = {pool.submit(runner, seed): seed for seed in todo}
            for n, fut in enumerate(as_completed(futs), 1):
                seed = futs[fut]
                try:
                    row = fut.result()
                except Exception as exc:  # noqa: BLE001
                    row = _error_row(seed, input_kind, exc)
                w.writerow(row)
                fh.flush()
                with _print_lock:
                    sup = {"yes": "", "no": " [UNSUPPORTED]"}.get(row["supported"], "")
                    print(
                        f"  [{n}/{len(todo)}] {row['company'][:26]:26} {row['domain'][:30]:30} "
                        f"{row['status']:11} {row['ats'] or '-'}{sup} "
                        f"({row['signal']}) {row['tenant'][:46]}",
                        flush=True,
                    )


def cmd_scan(args) -> None:
    with Path(args.seed).open(encoding="utf-8") as fh:
        rows = [r for r in csv.reader(fh) if len(r) >= 2]
    seeds = [
        HostSeed(c.strip(), d.strip(), d.strip(), "", "", 0)
        for c, d in rows
        if c.strip() and d.strip()
    ]
    _run_scan(
        args,
        seeds,
        "domain",
        lambda seed: probe(seed.company, seed.host, deep=args.deep),
    )


def cmd_indeed(args) -> None:
    if _DNS is None:
        print(
            "WARNING: dnspython missing; DNS channel disabled. Install dnspython for full coverage.",
            file=sys.stderr,
        )
    seeds = indeed_seeds(Path(args.harvest), include_resolved=args.include_resolved)
    seeds.sort(key=lambda seed: (-seed.jobs, seed.input_id))
    _run_scan(args, seeds, "indeed", lambda seed: probe_host(seed, deep=args.deep))


def cmd_certificates(args) -> None:
    """Enumerate sibling-host seeds from known career boards, without assigning their ATS."""
    with Path(args.seed).open() as fh:
        hosts = sorted(
            {row[-1].strip() for row in csv.reader(fh) if row and row[-1].strip()}
        )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["source_host", "candidate_host", "evidence", "error"]
        )
        writer.writeheader()
        with ThreadPoolExecutor(max_workers=min(args.workers, 4)) as pool:
            futures = {pool.submit(certificate_names, host): host for host in hosts}
            for future in as_completed(futures):
                host = futures[future]
                names, error = future.result()
                for name in names or [""]:
                    writer.writerow(
                        {
                            "source_host": host,
                            "candidate_host": name,
                            "evidence": "tls-san-seed-only",
                            "error": error,
                        }
                    )
                fh.flush()


# --- verify -------------------------------------------------------------------------------
# The scraper itself is the URL authority.  `cmd_verify` below builds the scraper instead of
# duplicating per-provider endpoint templates that inevitably drift as a scraper evolves.


@lru_cache(maxsize=1)
def liveness_probes():
    """Reuse the provider-aware read-only probes; never call the ledger-writing runner."""
    spec = importlib.util.spec_from_file_location(
        "fingerprint_liveness", ROOT / "scripts/validate/check_liveness.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.PROBES


def cmd_verify(args) -> None:
    with Path(args.results).open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        extras = {"certificate_hosts", "job_evidence", "matched_apply_urls"}
        if not reader.fieldnames or [
            f for f in reader.fieldnames if f not in extras
        ] != [f for f in FIELDS if f not in extras]:
            raise ValueError("verify requires a current fingerprint CSV")
        rows = list(
            {row["input_id"]: row for row in reader if row.get("input_id")}.values()
        )
    if args.ats:
        keep = set(args.ats.split(","))
        rows = [r for r in rows if r["ats"] in keep]
    live = {
        lower_key(board_key(company))
        for company in scrapable_boards.load(
            ROOT / "data/validate/liveness", min_jobs=0
        )
    }
    print(f"verifying {len(rows)} fingerprint rows", flush=True)
    probes = liveness_probes()
    source_urls: dict[tuple[str, str], set[str]] = {}
    if getattr(args, "harvest", None):
        wanted = {row["domain"] for row in rows}
        with Path(args.harvest).open() as fh:
            for line in fh:
                try:
                    job = json.loads(line)
                except ValueError:
                    continue
                url = (job.get("recruit") or {}).get("viewJobUrl") or ""
                host = urlsplit(url).hostname
                if host in wanted:
                    employer = str((job.get("employer") or {}).get("key") or "")
                    source_urls.setdefault((host, employer), set()).add(url)

    def one(row: dict) -> dict:
        row = dict(row)
        row["job_evidence"] = "not-checked"
        row["matched_apply_urls"] = ""
        if (
            row.get("input_kind") == "indeed"
            and row.get("input_id") == row.get("domain")
            and len([k for k in row.get("employer_keys", "").split("|") if k]) > 1
        ):
            row["candidate"] = "ambiguous-employers"
        if row.get("candidate") != "unverified" or not row.get("board_key"):
            row["verification"] = "not-a-candidate"
            return row
        if lower_key(row["board_key"]) in live and not getattr(
            args, "recheck_known", False
        ):
            row["candidate"] = "known-live"
            row["verification"] = "known-live"
            return row
        try:
            scraper = registry.get_scraper(row["ats"], row["tenant"], row["company"])
            row["verify_url"] = (
                row["tenant"]
                if row["tenant"].startswith("https://")
                else scraper.board_page() or scraper.url()
            )
            if row["ats"] not in probes:
                row["verification"] = "no-liveness-probe"
                return row
            verdict, jobs = probes[row["ats"]](row["tenant"], row["verify_url"])
        except Exception as exc:  # noqa: BLE001
            row["verification"] = f"listing-unreachable:{type(exc).__name__}"
            return row
        row["verify_detail"] = f"jobs={jobs}" if jobs is not None else ""
        row["verification"] = verdict
        return row

    def eligible(row):
        mixed = (
            row.get("input_kind") == "indeed"
            and row.get("input_id") == row.get("domain")
            and len([k for k in row.get("employer_keys", "").split("|") if k]) > 1
        )
        return (
            row.get("candidate") == "unverified" and row.get("board_key") and not mixed
        )

    def one_group(group):
        # Liveness and listing payloads are shared per Board; job matches remain per input.
        first = next((r for r in group if eligible(r)), group[0])
        result = one(first)
        cache = {}

        def cached_get(url, cap=PAGE_CAP):
            key = ("GET", url, cap)
            if key not in cache:
                cache[key] = get(url, cap=cap)
            return cache[key]

        def cached_post(url, headers, body):
            key = (
                "POST",
                url,
                json.dumps(headers, sort_keys=True),
                json.dumps(body, sort_keys=True) if isinstance(body, dict) else body,
            )
            if key not in cache:
                cache[key] = post_json(url, headers, body)
            return cache[key]

        checked = []
        for original in group:
            if not eligible(original):
                checked.append(one(original))
                continue
            row = original | {
                k: result[k]
                for k in (
                    "verification",
                    "verify_url",
                    "verify_detail",
                    "candidate",
                    "job_evidence",
                    "matched_apply_urls",
                )
            }
            if row["verification"] in {"live", "known-live"}:
                urls = {u for u in row.get("sample_apply_urls", "").split("|") if u}
                if getattr(args, "harvest", None):
                    urls = source_urls.get(
                        (row["domain"], row.get("employer_key", "")), set()
                    )
                row["job_evidence"], matches = check_jobs(
                    row["ats"], row["tenant"], urls, cached_get, cached_post
                )
                row["matched_apply_urls"] = "|".join(matches)
            checked.append(row)
        return checked

    out = (
        Path(args.out) if args.out else Path(args.results).with_suffix(".verified.csv")
    )
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        grouped: dict[str, list[dict]] = {}
        for row in rows:
            grouped.setdefault(
                lower_key(row["board_key"]) or row["input_id"], []
            ).append(row)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(one_group, group): group for group in grouped.values()
            }
            for n, future in enumerate(as_completed(futures), 1):
                checked = future.result()
                for row in checked:
                    writer.writerow(row)
                fh.flush()
                print(
                    f"  [{n}/{len(rows)}] {row['company'][:24]:24} {row['ats']:16} "
                    f"{row['tenant'][:42]:42} {row['verification']}",
                    flush=True,
                )
    print(
        f"wrote {out}; live candidates still require canonical alias checks and ledger landing",
        flush=True,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="fingerprint a company,domain CSV")
    s.add_argument("seed")
    s.add_argument("out")
    s.add_argument("--workers", type=int, default=8)
    s.add_argument(
        "--deep",
        action="store_true",
        help="add bounded certificate, API and browser fallbacks",
    )
    s.set_defaults(fn=cmd_scan)
    i = sub.add_parser(
        "indeed", help="fingerprint raw apply hosts from an Indeed harvest JSONL"
    )
    i.add_argument("harvest")
    i.add_argument("out")
    i.add_argument("--include-resolved", action="store_true")
    i.add_argument("--workers", type=int, default=8)
    i.add_argument(
        "--deep",
        action="store_true",
        help="add bounded certificate, API and browser fallbacks",
    )
    i.set_defaults(fn=cmd_indeed)
    c = sub.add_parser(
        "certificates",
        help="read known career hosts' TLS SAN rosters as unclassified seeds",
    )
    c.add_argument("seed", help="host-per-line or company,host CSV")
    c.add_argument("out")
    c.add_argument("--workers", type=int, default=4)
    c.set_defaults(fn=cmd_certificates)
    v = sub.add_parser(
        "verify", help="persist Board-key and scraper-route verification"
    )
    v.add_argument("results")
    v.add_argument("--out", help="verification CSV (default: RESULTS.verified.csv)")
    v.add_argument("--ats", default="", help="comma-separated ATS filter")
    v.add_argument(
        "--harvest",
        help="reconcile same-job URLs from this Indeed JSONL against bounded API listings",
    )
    v.add_argument(
        "--recheck-known",
        action="store_true",
        help="also probe candidates already marked live in the ledger",
    )
    v.add_argument("--workers", type=int, default=8)
    v.set_defaults(fn=cmd_verify)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
