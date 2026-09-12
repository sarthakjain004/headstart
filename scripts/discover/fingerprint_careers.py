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
   (LinkedIn/Naukri/Wellfound/workatastartup — an apply link, not a board we could scrape) and
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
    python scripts/discover/fingerprint_careers.py verify OUT.csv               # confirm the hits

`scan` streams one line per company and appends to OUT.csv as it goes, so it resumes: a re-run
skips domains already recorded and a run killed mid-sweep costs only the company in flight.

`verify` exists because a host string in a JS bundle is a claim, not a board. It re-derives the
board URL from each hit and fetches it — the clean-JSON APIs by their real endpoint (shapes taken
from `src/headstart/scrapers/*.py`), everything else by a plain GET reported with status and size.
Believe a row after `verify` says `jobs=N`, not before.

Needs dnspython for the CNAME stage (not a base dependency; CI installs base deps only). Without
it that stage is skipped and every other signal still runs.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from curl_cffi import requests as _requests

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
from headstart.scrapers import registry

try:
    import dns.resolver

    _DNS = dns.resolver.Resolver()
    _DNS.lifetime = 4.0
    _DNS.timeout = 2.0
except Exception:  # noqa: BLE001
    _DNS = None

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
            + r"([a-z0-9-]+)\.(wd\d+)\.myworkdaysite\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?([a-zA-Z0-9_-]+)",
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
            HOST + r"([a-z0-9-]+)\.fa\.(?:ocs|em\d|us\d|ca\d|eu\d)\.oraclecloud\.com",
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
    # ---------- NOT supported: global ----------
    "icims": ("ats", [SUB + r"icims\.com"]),
    "taleo": ("ats", [SUB + r"taleo\.net"]),
    "jobvite": ("ats", [r"jobs\.jobvite\.com/([a-zA-Z0-9_-]+)", SUB + r"jobvite\.com"]),
    "bamboohr": ("ats", [SUB + r"bamboohr\.(?:com|co\.uk)"]),
    "breezy": ("ats", [SUB + r"breezy\.hr"]),
    "jazzhr": ("ats", [SUB + r"applytojob\.com"]),
    "phenom": ("ats", [SUB + r"phenompeople\.com"]),
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
    "clearcompany": ("ats", [SUB + r"clearcompany\.com"]),
    "adp": ("ats", [r"workforcenow\.adp\.com", r"recruiting\.adp\.com"]),
    "ukg": ("ats", [SUB + r"ultipro\.com"]),
    "occupop": ("ats", [SUB + r"occupop\.com"]),
    "hrcloud": ("ats", [SUB + r"hrcloud\.com"]),
    # ---------- NOT supported: India-origin HRMS / ATS ----------
    "peoplestrong": (
        "ats",
        [SUB + r"peoplestrong\.com", SUB + r"altone\.io", SUB + r"peoplestrong\.in"],
    ),
    "turbohire": ("ats", [SUB + r"turbohire\.co"]),
    "pyjamahr": ("ats", [SUB + r"pyjamahr\.com", r"api\.pyjamahr\.com"]),
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
    "wellfound": (
        "jobboard",
        [r"(?:wellfound|angel)\.co/(?:company|l)/([a-zA-Z0-9_.-]+)/jobs"],
    ),
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
CASE_SENSITIVE = {"smartrecruiters"}

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
    "wellfound": {"wellfound.com"},
    "apna": {"apna.co"},
    "hirebuddy": {"hirebuddy.net"},
}

# The CNAME sweep's zones. A careers-ish label CNAMEing into one of these is decisive — the
# company has pointed DNS at that provider — and costs one UDP query, no HTTP.
CNAME_ZONES = {
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
# provider's namespace (successfactors.py: "Slug = the vanity host"; zwayam.py slug_from() is
# `host_of(url)`). For these the tenant is the host we asked about, never the CNAME target — a
# target like `15544.jobs2web.com` or `blackbuck.cluster3.openings.co` is provider infrastructure
# and is not a slug any scraper here can use.
VANITY_HOST_ATS = frozenset({"successfactors", "zwayam"})
# zoho's slug is a full host as well, but a matched `*.zohorecruit.*` host is already correct —
# only the vanity-domain fingerprint (which captures nothing) needs the evidence host instead.
HOST_SLUG_ATS = frozenset({"zoho"})
# ATSes whose slug is a full host inside the provider's own zone (oracle.py: "the slug is the
# careers host"; eightfold and personio the same), so the CNAME target *is* the right answer.
PROVIDER_HOST_ATS = frozenset({"oracle", "eightfold", "personio"})

FIELDS = [
    "company",
    "domain",
    "status",
    "ats",
    "kind",
    "supported",
    "tenant",
    "signal",
    "evidence_url",
    "other_hits",
    "pages_ok",
    "pages_err",
    "note",
]

_local = threading.local()
_print_lock = threading.Lock()


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
    """Crude registered domain, with the common two-part-TLD case (`foo.co.in`) handled."""
    parts = host.lower().split("//")[-1].split("/")[0].split(":")[0].split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "com", "net", "org", "gov", "ac"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host.lower()


def scan(text: str, self_domain: str) -> list[tuple[str, str, str]]:
    """Every ATS/jobboard/diy reference in `text`, as (ats, kind, tenant).

    Self-referential matches are dropped: a company that IS a provider (zoho.com sits in this very
    seed) otherwise matches its own infra subdomains as if they were a tenant board.
    """
    rd = reg_domain(self_domain)
    hits: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for ats, (kind, pats) in PATTERNS.items():
        if rd in PROVIDER_DOMAINS.get(ats, set()):
            continue
        for p in pats:
            for m in re.finditer(p, text, re.IGNORECASE):
                if ats == "workday" and m.lastindex and m.lastindex >= 3:
                    co, pod, site = m.group(1).lower(), m.group(2).lower(), m.group(3)
                    if site.lower() in BLOCK or LOCALE.match(site):
                        continue
                    # The Workday scraper's slug IS the full board URL (workday.py slug_from), so
                    # emitting "{co}/{site}" would drop the pod and be unusable downstream.
                    tok = f"https://{co}.{pod}.myworkdayjobs.com/{site}"
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
                            or lo.split(".")[0] in BLOCK
                        ):
                            continue
                key = (ats, tok)
                if key not in seen:
                    seen.add(key)
                    hits.append((ats, kind, tok))
    return hits


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


def cname_chain(host: str) -> list[str]:
    """The CNAME targets for `host` ([] if none, or if dnspython isn't installed)."""
    if _DNS is None:
        return []
    try:
        return [str(r.target).rstrip(".").lower() for r in _DNS.resolve(host, "CNAME")]
    except Exception:  # noqa: BLE001
        return []


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
    """Rewrite a matched token into the string that ATS's *scraper* actually keys on.

    Two shapes of hit produce a token no scraper could use, and both were live on this seed:

    * A CNAME into a **vanity-host ATS**. `jobs.chargebee.com CNAME 15544.jobs2web.com` proves
      SuccessFactors, but successfactors.py's slug is the vanity host, so the answer is
      `jobs.chargebee.com` — `15544.jobs2web.com` is SAP's shared RMK infrastructure. Same for
      zwayam, whose slug_from() is `host_of(url)`: BlackBuck's board is `careers.blackbuck.com`,
      not the `blackbuck.cluster3.openings.co` its DNS points at.
    * A **page** match on the same class of ATS, where the regex captured a provider-internal
      label rather than the board. Practo, Coforge and Tiger Analytics each matched an
      `impl.zwayam.com` reference inside their own Zwayam-served careers page; the board is the
      page's own host (`careers.practo.com`, which CLAUDE.md independently confirms).

    Everything else is returned untouched — including :data:`PROVIDER_HOST_ATS`, where the full
    provider host *is* the slug (oracle.py: "the slug is the careers host").
    """
    if not evidence:
        return tenant
    if ats in HOST_SLUG_ATS:
        return (
            tenant if "." in tenant else (urlsplit(evidence).netloc.lower() or tenant)
        )
    if ats not in VANITY_HOST_ATS:
        return tenant
    # A CNAME evidence string is "{queried host} CNAME {target}"; anything else is a URL.
    host = (
        evidence.split(" CNAME ")[0]
        if " CNAME " in evidence
        else urlsplit(evidence).netloc
    )
    return host.lower() or tenant


def zone_of(host: str) -> str | None:
    h = host.lower().rstrip(".")
    for zone, ats in CNAME_ZONES.items():
        if h == zone or h.endswith("." + zone):
            return ats
    return None


def probe(company: str, domain: str) -> dict:
    """Resolve one company. Signals run cheapest-first and stop at the first that names an ATS."""
    domain = re.sub(r"^https?://", "", domain.strip().lower()).strip("/")
    hits: list[
        tuple[str, str, str, str, str]
    ] = []  # (ats, kind, tenant, signal, evidence)
    ok = err = 0
    notes: list[str] = []

    def record(found, signal, evidence):
        for ats, kind, tok in found:
            hits.append((ats, kind, tok, signal, evidence))

    def has_ats() -> bool:
        return any(h[1] == "ats" for h in hits)

    # --- 1. CNAME sweep: one UDP query per careers-ish label, no HTTP, decisive when it fires.
    rd = reg_domain(domain)
    for label in CNAME_LABELS:
        host = f"{label}.{domain}"
        for target in cname_chain(host):
            ats = zone_of(target)
            # A provider probing itself is not a tenant: keka.com is in this very seed, and
            # `careers.keka.com` CNAMEs to `cin02.hr.keka.com` — which mine_keka.py documents as
            # the *wildcard* pod every unknown label lands on, i.e. the opposite of a board.
            if not ats or rd in PROVIDER_DOMAINS.get(ats, set()):
                continue
            kind = PATTERNS.get(ats, ("ats", []))[0]
            hits.append((ats, kind, target, "cname", f"{host} CNAME {target}"))
        if has_ats():
            break

    # --- 2. apex page: scan it, and harvest careers-ish links off it.
    body, final, e = get(f"https://{domain}/")
    if e:
        notes.append(f"root:{e}")
    if body:
        ok += 1
        record(scan(final + "\n" + body, domain), "homepage", final)
    else:
        err += 1
    links = [
        urljoin(final or f"https://{domain}/", m.group(1))
        for m in HREF.finditer(body or "")
        if LINKISH.search(m.group(1))
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
        record(scan("\n".join(ordered), domain), "homepage", ordered[0])

    # --- 3. careers hosts + harvested links + the standard paths, until something matches.
    # The queue grows as it drains: a careers *landing* page is very often marketing, with the
    # actual board one hop further in ("/company/careers/" -> "/company/careers/open-positions/",
    # which is where Postman's Greenhouse board lives). So careers-ish links found on a careers
    # page are appended too — bounded to SECOND_HOP so a link-farm footer can't run away with it.
    candidates = [f"https://careers.{domain}/", f"https://jobs.{domain}/"]
    candidates += ordered[:4]
    candidates += [f"https://{domain}{p}" for p in CAREERS_PATHS[:6]]
    tried: set[str] = {f"https://{domain}"}
    best_careers_page: tuple[str, str] | None = None
    second_hop = 0
    while candidates:
        if has_ats():
            break
        url = candidates.pop(0)
        key = url.split("#")[0].rstrip("/")
        if key in tried:
            continue
        tried.add(key)
        body, final, e = get(url)
        if not body:
            err += 1
            continue
        ok += 1
        # The final URL matters as much as the body: careers.acme.com often 301s onto the ATS.
        offsite = reg_domain(urlsplit(final).netloc) != reg_domain(domain)
        record(scan(final, domain), "redirect" if offsite else "page", final)
        record(scan(body, domain), "page", final)
        if best_careers_page is None:
            best_careers_page = (body, final)
        if second_hop < SECOND_HOP and re.search(
            r"career|job|hiring|opening", final, re.IGNORECASE
        ):
            for m in HREF.finditer(body):
                nxt = urljoin(final, m.group(1))
                if not LINKISH.search(m.group(1)) or not nxt.startswith("http"):
                    continue
                if nxt.split("#")[0].rstrip("/") in tried or nxt in candidates:
                    continue
                candidates.append(nxt)
                second_hop += 1
                if second_hop >= SECOND_HOP:
                    break

    # --- 4. robots.txt / sitemap.xml — these routinely name the ATS host outright.
    for path, signal, cap in (
        ("/robots.txt", "robots", 200_000),
        ("/sitemap.xml", "sitemap", 400_000),
    ):
        if has_ats():
            break
        b, f, e = get(f"https://{domain}{path}", cap=cap)
        if not b:
            err += 1
            continue
        ok += 1
        record(scan(b, domain), signal, f)

    # --- 5. last rung: the SPA's own JS bundle. Only for companies still unresolved, capped at 3
    # same-origin scripts, because this is the expensive signal (bundles run to megabytes).
    if not has_ats() and best_careers_page:
        body, final = best_careers_page
        srcs = [urljoin(final, m.group(1)) for m in SCRIPT_SRC.finditer(body)]
        srcs = [u for u in srcs if reg_domain(urlsplit(u).netloc) == reg_domain(domain)]
        srcs.sort(
            key=lambda u: (
                0 if re.search(r"main|app|index|bundle|chunk", u, re.IGNORECASE) else 1
            )
        )
        for u in srcs[:3]:
            js, jf, e = get(u, cap=BUNDLE_CAP)
            if not js:
                err += 1
                continue
            ok += 1
            record(scan(js, domain), "jsbundle", jf)
            if has_ats():
                break

    # --- 6. slug probe: ask the clean-JSON boards directly whether this company has one.
    # This is the stage whose absence made the first pass' number wrong, not just incomplete —
    # `boards-api.greenhouse.io/v1/boards/postman/jobs` returns 63 live jobs, and Postman was
    # still recorded opaque. No amount of page-scanning finds a board a company never links to
    # (Postman's own careers page names no ATS host anywhere, in HTML or bundle).
    # It is the LAST stage on purpose: it infers a slug rather than observing a link, so a
    # namesake board on a shared ATS would be a false positive. `signal=slugprobe` keeps those
    # rows separable from the observed ones, and `jobs>0` is required.
    if not has_ats():
        for ats, (tmpl, count) in SLUG_PROBES.items():
            for slug in candidate_slugs(company, domain):
                body, final, e = get(tmpl.format(s=slug), cap=2_000_000)
                if not body:
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
                hits.append((ats, "ats", slug, "slugprobe", final))
            if has_ats():
                break

    # --- settle. Signal precedence picks the primary hit; kind picks the bucket.
    order = {
        "cname": 0,
        "redirect": 1,
        "page": 2,
        "homepage": 3,
        "robots": 4,
        "sitemap": 5,
        "jsbundle": 6,
        "slugprobe": 7,
    }
    ats, kind, tok, signal, ev = "", "", "", "", ""
    for want, state in (("ats", "resolved"), ("jobboard", "jobboard"), ("diy", "diy")):
        pool = [h for h in hits if h[1] == want]
        if pool:
            ats, kind, tok, signal, ev = min(pool, key=lambda h: order.get(h[3], 9))
            status = state
            break
    else:
        # No hit at all. `none` is a settled negative (something loaded and was clean);
        # `unreachable` says only that we never saw the site, and must never be counted as one.
        status = "none" if ok else "unreachable"
    tok = normalise_tenant(ats, tok, ev)
    others = sorted(
        {f"{a}:{t}" for a, _k, t, _s, _e in hits} - ({f"{ats}:{tok}"} if ats else set())
    )
    return {
        "company": company,
        "domain": domain,
        "status": status,
        "ats": ats,
        "kind": kind,
        "supported": ("yes" if ats in SUPPORTED else "no") if kind == "ats" else "",
        "tenant": tok,
        "signal": signal,
        "evidence_url": ev,
        "other_hits": " ".join(others[:8]),
        "pages_ok": ok,
        "pages_err": err,
        "note": ";".join(notes[:3]),
    }


def cmd_scan(args) -> None:
    with Path(args.seed).open(encoding="utf-8") as fh:
        rows = [r for r in csv.reader(fh) if len(r) >= 2]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()
    if out.exists():
        with out.open(encoding="utf-8") as fh:
            done = {r["domain"] for r in csv.DictReader(fh)}
    todo = [
        (c, d) for c, d in ((r[0].strip(), r[1].strip()) for r in rows) if d not in done
    ]
    print(
        f"{len(rows)} seeded, {len(done)} already done, {len(todo)} to probe",
        flush=True,
    )
    fresh = not out.exists()
    with out.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if fresh:
            w.writeheader()
            fh.flush()
        n = 0
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futs = {pool.submit(probe, c, d): (c, d) for c, d in todo}
            for fut in as_completed(futs):
                c, d = futs[fut]
                try:
                    row = fut.result()
                except Exception as exc:  # noqa: BLE001
                    row = dict.fromkeys(FIELDS, "")
                    row |= {
                        "company": c,
                        "domain": d,
                        "status": "unreachable",
                        "pages_ok": 0,
                        "pages_err": 1,
                        "note": f"probe:{type(exc).__name__}",
                    }
                n += 1
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


# --- verify -------------------------------------------------------------------------------
# A host string in a JS bundle is a claim, not a board. These are the real endpoints, taken from
# each scraper's own url() construction; anything without one falls back to a plain GET whose
# status and size are reported so a human can judge it.
# `verify` is deliberately WIDER than SLUG_PROBES: probing needs a board that publishes its
# owner's name (or a namesake sails through), but confirming a board we already have *observed*
# evidence for only needs the job feed. So ashby/lever/freshteam/rippling belong here even though
# they are barred from stage 6.
VERIFY = {
    "greenhouse": (
        "https://boards-api.greenhouse.io/v1/boards/{s}/jobs",
        lambda d: len(d.get("jobs", [])),
    ),
    "lever": (
        "https://api.lever.co/v0/postings/{s}?mode=json",
        lambda d: len(d) if isinstance(d, list) else 0,
    ),
    "ashby": (
        "https://api.ashbyhq.com/posting-api/job-board/{s}",
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
    "freshteam": (
        "https://{s}.freshteam.com/hire/widgets/jobs.json",
        lambda d: len(d) if isinstance(d, list) else 0,
    ),
    "rippling": (
        "https://api.rippling.com/platform/api/ats/v1/board/{s}/jobs",
        lambda d: len(d) if isinstance(d, list) else 0,
    ),
    "teamtailor": (
        "https://{s}.teamtailor.com/jobs.json",
        lambda d: len(d) if isinstance(d, list) else len(d.get("jobs", [])),
    ),
    "sensehq": (
        "https://{s}.sensehq.com/careers/api/jobs",
        lambda d: len(d) if isinstance(d, list) else len(d.get("jobs", [])),
    ),
    "pyjamahr": (
        "https://api.pyjamahr.com/api/career/jobs/?company_uuid={s}",
        lambda d: len(d.get("results", [])),
    ),
}
PAGE_VERIFY = {
    "workday": "{t}",  # the Workday tenant IS the board URL
    "darwinbox": "https://{t}.darwinbox.in/ms/candidate/careers",
    "keka": "https://{t}.keka.com/careers/",
    "zwayam": "https://{t}/",
    "peoplestrong": "https://{t}.peoplestrong.com/",
    "icims": "https://{t}.icims.com/jobs/search",
    "phenom": "https://{t}.phenompeople.com/",
    "eightfold": "https://{t}/careers",
    "trakstar": "https://{t}.hire.trakstar.com/",
    "ripplehire": "https://{t}.ripplehire.com/candidate/careers",
    "turbohire": "https://{t}.turbohire.co/",
    "jobvite": "https://jobs.jobvite.com/{t}",
    "bamboohr": "https://{t}.bamboohr.com/careers",
    "breezy": "https://{t}.breezy.hr/",
    "jazzhr": "https://{t}.applytojob.com/apply",
    "zoho": "https://{t}.zohorecruit.com/jobs/Careers",
    "taleo": "https://{t}.taleo.net/careersection/",
    "kula": "https://careers.kula.ai/{t}",
    "gem": "https://jobs.gem.com/{t}",
    "dover": "https://app.dover.io/{t}",
    "adrenalin": "https://{t}.myadrenalin.com/",
    "oracle": "https://{t}.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience",
    "successfactors": "https://{t}.successfactors.com/",
    "hrone": "https://{t}.hrone.cloud/",
    "zinghr": "https://{t}.zinghr.com/",
    "skillate": "https://{t}.skillate.com/",
    "jobsoid": "https://{t}.jobsoid.com/",
    "recruiterflow": "https://{t}.recruiterflow.com/",
}


def _verify_darwinbox(tenant: str) -> str:
    """Darwinbox's board is a POST, not a page. The careers URL only serves an SPA shell (~1.2KB,
    zero job words), so a GET reports nothing about whether the board exists — this hits the same
    `alljobs` endpoint DarwinboxScraper does."""
    host = tenant if "." in tenant else f"{tenant}.darwinbox.in"
    body = {"companyId": "main", "page": 1, "sort_option": "new", "limit": 50}
    try:
        r = session().post(
            f"https://{host}/ms/candidateapi/job/alljobs?companyId=main",
            json=body,
            timeout=TIMEOUT,
            verify=False,
            headers={"User-Agent": UA, "Accept": "application/json"},
        )
        return f"jobs={len(r.json().get('data') or [])}"
    except Exception as exc:  # noqa: BLE001
        return f"ERR {type(exc).__name__}"


def cmd_verify(args) -> None:
    with Path(args.results).open(encoding="utf-8") as fh:
        rows = [
            r for r in csv.DictReader(fh) if r["status"] == "resolved" and r["tenant"]
        ]
    if args.ats:
        keep = set(args.ats.split(","))
        rows = [r for r in rows if r["ats"] in keep]
    print(f"verifying {len(rows)} resolved rows", flush=True)

    def one(r):
        ats, t = r["ats"], r["tenant"]
        if ats == "darwinbox":
            return r, t, _verify_darwinbox(t)
        if ats in VERIFY and "." not in t:
            url, count = VERIFY[ats]
            body, final, e = get(url.format(s=t), cap=3_000_000)
            if not body:
                return r, url.format(s=t), f"ERR {e}"
            try:
                return (
                    r,
                    final,
                    f"jobs={count(json.loads(body))}" + (f" [{e}]" if e else ""),
                )
            except Exception:  # noqa: BLE001
                return r, final, f"unparsable ({e or 'ok'}, {len(body)}B)"
        # A tenant that is already a host or a URL is fetched as-is. Formatting it into a
        # template appends the provider domain a second time (`15544.jobs2web.com.
        # successfactors.com`) and every such check DNS-fails, which reads as a dead board.
        if t.startswith("http") or "." in t:
            url = t if t.startswith("http") else f"https://{t}/"
        elif ats in PAGE_VERIFY:
            url = PAGE_VERIFY[ats].format(t=t)
        else:
            return r, "", "no verifier"
        body, final, e = get(url)
        if not body:
            return r, url, f"ERR {e}"
        hint = len(re.findall(r"(?i)\b(job|position|opening|vacanc)", body))
        return r, final, f"{e or 'http200'} {len(body)}B jobwords={hint}"

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for fut in as_completed([pool.submit(one, r) for r in rows]):
            r, url, verdict = fut.result()
            print(
                f"  {r['company'][:24]:24} {r['ats']:16} {r['tenant'][:42]:42} "
                f"{verdict:30} {url[:64]}",
                flush=True,
            )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="fingerprint a company,domain CSV")
    s.add_argument("seed")
    s.add_argument("out")
    s.add_argument("--workers", type=int, default=8)
    s.set_defaults(fn=cmd_scan)
    v = sub.add_parser("verify", help="confirm resolved rows against the real board")
    v.add_argument("results")
    v.add_argument("--ats", default="", help="comma-separated ATS filter")
    v.add_argument("--workers", type=int, default=8)
    v.set_defaults(fn=cmd_verify)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
