#!/usr/bin/env python3
"""The Wayback feeder's shared half: which hosts each ATS serves boards from, and how to read a
Company's slug out of one archived URL.

`wayback_pages.py` and `wayback_paginate.py` are two strategies for one job — walk an ATS's
archived CDX URLs and collect the slugs — so what a slug *looks like* is decided here rather than
in each of them.

`ATS_HOSTS` is the set of hosts that serve **archivable board pages carrying a slug**. That is
deliberately not the same set as the hosts the scrapers fetch from: five scrapers talk to an
`api.`/`boards-api.` host whose paths begin with a version segment, and measuring Common Crawl
found zero boards behind any of them that the board hosts did not already have.

An ATS is rarely one host, and sweeping only the obvious one loses boards silently: Zoho spreads
8,197 known slugs over 8 TLDs, of which `zohorecruit.com` holds 6,101 — and, found by the
2026-09-08 audit, Trakstar's pre-rename `recruiterbox.com` holds *twice* the archive of the
`hire.trakstar.com` this table used to list alone. The liveness ledger cannot be used to check
this table: it was populated by this feeder, so it only ever contains hosts already swept. See
`docs/discovery/2026-09-08_wayback-host-coverage-audit.md`.

(The output column is still called `tenant`, matching every other discovery feeder's CSV.
CONTEXT.md retires the term but parks the code/data rename as a separate change.)
"""

import argparse
import csv
import errno
import os
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, get_args

ROOT = Path(__file__).resolve().parent.parent.parent
WB = ROOT / "data" / "wayback-ats"
socket.setdefaulttimeout(120)

Style = Literal["sub", "host", "path", "workday", "workdaysite"]

# An ATS that serves the same board from two hostnames. The value is the spelling the scraper
# wants; `extract` emits it in place of the host it actually saw, which is what makes the two
# collapse — `dedupe_key` keys every non-`path` style on the URL, so rewriting the URL here is
# the whole of the fix and `dedupe_key` needs no special case. Do NOT put a regional pod in here:
# Zoho's TLDs and Greenhouse's EU/ANZ hosts serve *different* board sets, and collapsing those
# would drop a real second board (see `dedupe_key`).
_CANONICAL_HOST = {
    # Trakstar Hire is Recruiterbox renamed. `{slug}.recruiterbox.com` 301s to
    # `{slug}.hire.trakstar.com` with a 1:1 label mapping, verified live on 1lattice, 1stopasia
    # and recruiterbox itself. The scraper never learned the new name either — `trakstar.py:104`
    # still carries `_FEED_NS = {"job": "https://recruiterbox.com/rss/job/"}`.
    "recruiterbox.com": "hire.trakstar.com",
    # `{slug}.kekahire.com/careers` 302s to `{slug}.keka.com/careers`, also 1:1. Tenants get a
    # provisioned pod record (`10decoders` -> `cin01.career.kekahire.com`), not the wildcard.
    "kekahire.com": "keka.com",
}
# `en`, `en-US`, `pt-BR` — a Workday board archived under a locale prefix.
_LOCALE = re.compile(r"[a-z]{2}(-[A-Za-z]{2})?")
# `www2`, `www4` — the vendor's own numbered web front-ends. INFRA holds bare `www`, which does
# not catch these, and iCIMS is the first ATS whose archive surfaces them: Wayback's SURT
# canonicalization strips `www.`, so `www.icims.com` collapses onto the bare `com,icims)` key and
# sorts first, while `wwwN.icims.com` keeps its own label and reaches `extract` as a candidate
# tenant. Rejected for *subdomain labels only*: `careers.smartrecruiters.com/www4` is a real live
# Board whose slug is the path segment `www4`, so widening INFRA instead would have dropped it.
_NUMBERED_WWW = re.compile(r"www\d+")
# The datacenter label of a *production* Workday host, per `WorkdayScraper._URL_PATTERN`.
_WD_INSTANCE = re.compile(r"wd\d+")
# Workday's own routes under a board host. Unlike `INFRA` these are not plausible board names —
# a real site is never called `refreshFacet` — so rejecting them costs nothing and they were 26
# of one sampled page's 96 harvested rows.
_WD_NON_SITE = {
    "assets",
    "refreshfacet",
    ".well-known",
    "static",
    "images",
    "favicon.ico",
}
STYLES: tuple[Style, ...] = get_args(Style)

# Subdomains and path segments that are the ATS's own furniture, not a Company.
INFRA = {
    "www",
    "app",
    "apps",
    "apply",
    "blog",
    "support",
    "help",
    "api",
    "status",
    "smtp",
    "mail",
    "email",
    "cdn",
    "assets",
    "static",
    "go",
    "info",
    "docs",
    "careers",
    "jobs",
    "admin",
    "portal",
    "test",
    "staging",
    "dev",
    "demo",
    "about",
    "home",
    "login",
    "secure",
    "my",
}

# A path slug may contain a dot (a Company using its domain as its slug), which makes a file
# served from the board root — `ads.txt`, `manifest.json` — indistinguishable by shape. Reject on
# the trailing extension instead of by filename, because the filenames are an open set.
# None of these is a TLD, so no real domain-shaped slug collides.
FILE_SUFFIXES = (
    ".txt",
    ".xml",
    ".html",
    ".htm",
    ".json",
    ".js",
    ".css",
    ".ico",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".pdf",
    ".php",
    ".aspx",
    ".map",
    ".webmanifest",
)


TIMEOUT = 120
UA = "HeadStart-wayback/0.1 (ATS tenant discovery)"
_CTX = ssl._create_unverified_context()
# The Archive throttles rather than refuses: these come back mid-sweep and clear on their own.
_RETRYABLE = {408, 429, 500, 502, 503, 504}


class FetchError(Exception):
    """A CDX request that could not be completed, carrying why it was given up on."""


# The Archive does not answer overload with 429 — it stops accepting TCP connections, which
# surfaces as ECONNREFUSED/ECONNRESET. That is a whole-host condition, not a per-page one, so a
# refusal parks *every* worker until this timestamp rather than letting each retry into the same
# closed door. Guarded by its own lock because the harvesters call `fetch` from a thread pool.
_REFUSED = {errno.ECONNREFUSED, errno.ECONNRESET, errno.EPIPE, errno.ETIMEDOUT}
_COOLDOWN_LOCK = threading.Lock()
_cooldown_until = 0.0


def _park(seconds: float) -> None:
    """Hold every worker off the host for ``seconds`` from now."""
    global _cooldown_until
    with _COOLDOWN_LOCK:
        _cooldown_until = max(_cooldown_until, time.monotonic() + seconds)


def _wait_out_cooldown() -> None:
    while True:
        with _COOLDOWN_LOCK:
            remaining = _cooldown_until - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 5))


def fetch(url: str, attempts: int = 6) -> str:
    """GET ``url`` on a fresh connection, retrying what is worth retrying.

    Every attempt builds its own opener and sends ``Connection: close``, so a refused or
    half-dead socket is never reused — a retry is a genuinely new connection, which is the only
    thing that helps once the Archive has stopped accepting the old ones.

    Three failure classes, handled differently. A **connection refusal** is the host saying "too
    many"; it parks all workers and backs off hardest. A **429/5xx** is throttling, backed off
    exponentially and honouring ``Retry-After``. Anything else — 400, 403, 404 — is the server's
    final answer, raised at once rather than retried five more times.

    Never returns None. The first version swallowed every exception and returned None, which
    cost a harvest run: 18 ATSes reported "could not get page count: None" and the reason had
    been discarded at the one point it was known.
    """
    last = ""
    for attempt in range(1, attempts + 1):
        _wait_out_cooldown()
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": UA, "Connection": "close"}
            )
            opener = urllib.request.build_opener(
                urllib.request.HTTPSHandler(context=_CTX)
            )
            with opener.open(request, timeout=TIMEOUT) as response:
                return response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as err:
            if err.code not in _RETRYABLE:
                raise FetchError(f"HTTP {err.code} {err.reason}") from err
            last = f"HTTP {err.code} {err.reason}"
            delay = _retry_after(err) or 2**attempt
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            last = f"{type(err).__name__}: {err}"
            if _is_refusal(err):
                delay = min(15 * attempt, 90)
                _park(
                    delay
                )  # the host is closing the door on everyone, not just this page
            else:
                delay = 2**attempt
        if attempt == attempts:
            break
        time.sleep(min(delay, 90))
    raise FetchError(f"{last} (gave up after {attempts} attempts)")


def _is_refusal(err: Exception) -> bool:
    """Whether the host refused the connection itself, rather than answering it."""
    inner = getattr(err, "reason", err)
    code = getattr(inner, "errno", None)
    return code in _REFUSED


def _retry_after(err: urllib.error.HTTPError) -> float | None:
    raw = err.headers.get("Retry-After") if err.headers else None
    try:
        return float(raw) if raw else None
    except ValueError:
        return None  # an HTTP-date; the exponential backoff is a fine substitute


def _with_style(style: Style, *hosts: str) -> tuple[tuple[str, Style], ...]:
    """Pair each host with the style that reads a slug out of it."""
    return tuple((h, style) for h in hosts)


# Host counts in the comments are liveness-ledger rows on 2026-08-13; they say what a sweep is
# worth, and are not targets.
#
# An ATS's hosts share one dedupe set, keyed by `dedupe_key` rather than by the slug — see there
# for why the label alone is the wrong identity outside `path` styles.
ATS_HOSTS: dict[str, tuple[tuple[str, Style], ...]] = {
    "ashby": _with_style("path", "jobs.ashbyhq.com"),
    "darwinbox": _with_style("sub", "darwinbox.in", "darwinbox.com"),
    # `host` style: this ATS's slug is the whole board host, not the label — `eightfold.py`
    # builds `https://{slug}/careers`, and every one of the ledger's 109 live boards is stored
    # that way (all 138 bare-label rows are dead). Misses the vanity tail (careers.qualcomm.com
    # and kin), which has no host namespace to enumerate.
    "eightfold": _with_style("host", "eightfold.ai"),
    "freshteam": _with_style("sub", "freshteam.com"),
    # `*.us.greenhouse.io` resolves but 301/302s to the unprefixed host and holds no ledger rows
    # of its own — an alias, so sweeping it would only re-find what `boards` already has. The EU
    # pods are a real split: 824 rows, 497 live.
    "greenhouse": _with_style(
        "path",
        "job-boards.greenhouse.io",
        "boards.greenhouse.io",
        "job-boards.eu.greenhouse.io",
        "boards.eu.greenhouse.io",
        # ANZ is Greenhouse's third production region (CT: prod-apse2-0/prod-apse4-0), not an
        # alias: `boards.anz.` 301s region-preservingly, and 12/12 live EU slugs 404 here.
        "job-boards.anz.greenhouse.io",
        "boards.anz.greenhouse.io",
    ),
    # `host` style: `icims.py`'s slug IS the board host (`career-celanese.icims.com`).
    #
    # Unlike `cc_miner.py`'s entry, this cannot apply the measured discriminator — 1,499 of 1,499
    # live iCIMS boards have a hyphen in the label, and the vendor's ~120 infrastructure hosts are
    # mostly single words — because `extract` takes (url, host, style) and no ATS, and widening
    # that signature for one provider is not worth it. Two existing rules do most of the work:
    # `valid()` rejects the INFRA words (`www`, `api`, `login`, `dev`, `staging`, `careers`,
    # `jobs`…) and the `"." in label` guard rejects every deeper subdomain (`admin.social`,
    # `agents.dev`, `staging.social`, and the one real `www.`-archived tenant, which is reachable
    # without the prefix anyway). Roughly 70 single-label infra hosts still get harvested; each
    # costs one `probe_icims.py` request and lands as a dead ledger row, so the error is bounded
    # and self-correcting rather than silent.
    "icims": _with_style("host", "icims.com"),
    "keka": _with_style(
        "sub", "keka.com", "kekahire.com"
    ),  # alias, see _CANONICAL_HOST
    "lever": _with_style(
        "path", "jobs.lever.co", "jobs.eu.lever.co"
    ),  # EU: 154 rows, 92 live
    # `host` style: `oracle.py` builds `https://{slug}/hcmRestApi/...`, so the slug IS the whole
    # board host (`airborneo-iacatj.fa.ocs.oraclecloud.com`), as for iCIMS and Eightfold.
    #
    # Every pod is listed separately, and the apex is NOT usable: with host `oraclecloud.com`,
    # `extract` sees the label `airborneo-iacatj.fa.ocs`, whose dots trip the `"." in label` guard
    # and it returns None. Measured both ways 2026-09-08 — the apex harvests exactly nothing, so a
    # one-line `("oraclecloud.com", "host")` would look right and silently find zero boards.
    #
    # 15 pods come from `data/validate/liveness/oracle.csv` (ocs 552 rows, us2 211, em2 106, us6
    # 85, em3 54, ap1 39, ca2 11, us8 10, em8 10, us1 7, ap2 7, ca3 6, em5 4, la1 2, em4 2). The
    # ledger alone would be circular — it only knows pods already discovered — so a CDX existence
    # probe swept the rest of the grid (us3-us12, em1/em6/em7/em9-em11, ap3/ap5-ap7, ca1/ca4,
    # la2/la3, uk1-uk3, me1/me2, in1, jp1, au1, sa1/sa2, ocs1-ocs3). It found **ap4**, which the
    # ledger has zero rows for and which archives 5 real tenant boards (ccjs, eftw, efyq, egda,
    # hdip) — so it is included. Everything else in that grid is empty; `em1` archives only the
    # bare pod host with no tenant under it, so it is deliberately left out.
    "oracle": _with_style(
        "host",
        "fa.ocs.oraclecloud.com",
        "fa.us2.oraclecloud.com",
        "fa.em2.oraclecloud.com",
        "fa.us6.oraclecloud.com",
        "fa.em3.oraclecloud.com",
        "fa.ap1.oraclecloud.com",
        "fa.ca2.oraclecloud.com",
        "fa.us8.oraclecloud.com",
        "fa.em8.oraclecloud.com",
        "fa.us1.oraclecloud.com",
        "fa.ap2.oraclecloud.com",
        "fa.ca3.oraclecloud.com",
        "fa.em5.oraclecloud.com",
        "fa.la1.oraclecloud.com",
        "fa.em4.oraclecloud.com",
        "fa.ap4.oraclecloud.com",
        # The pod-less tier: `jpmc.fa.oraclecloud.com` is live with 7,323 jobs and matches none
        # of the 16 pods above. Not a duplicate — it CNAMEs to `eino.fa.ocs…`, which has zero
        # ledger rows. `cc_miner` misses it too: its regex requires a pod segment.
        "fa.oraclecloud.com",
    ),
    "personio": _with_style("sub", "jobs.personio.com", "jobs.personio.de"),
    "recruitee": _with_style("sub", "recruitee.com"),
    "ripplehire": _with_style("sub", "ripplehire.com"),
    "rippling": _with_style("path", "ats.rippling.com"),
    # Slugs are case-sensitive and mostly mixed-case (8,737 of 12,706 ledger slugs) — see
    # `extract`, which is why this ATS cannot use a lowercasing extractor.
    "smartrecruiters": _with_style(
        "path", "jobs.smartrecruiters.com", "careers.smartrecruiters.com"
    ),
    # `host` style, and here the reason is in the scraper's own docstring: the slug IS the
    # customer's vanity host, which is also why this sweep is
    # partial by nature — `careers.wipro.com` and its kin are spread
    # over hundreds of apexes, so most of the ~2,100 live boards are unreachable by any domain
    # sweep. These two hosts are the enumerable part — ~66 live boards. The rest needs the
    # careers-page scan, not a wider sweep.
    "successfactors": _with_style("host", "jobs2web.com", "jobs.hr.cloud.sap"),
    "teamtailor": _with_style("sub", "teamtailor.com"),
    # `recruiterbox.com` is the pre-rename namespace and holds 40 CDX pages against
    # `hire.trakstar.com`'s 20 — the larger half of this provider's archive. Alias, so
    # `_CANONICAL_HOST` rewrites it and the two spellings collapse.
    "trakstar": _with_style("sub", "hire.trakstar.com", "recruiterbox.com"),
    # Two shapes at once: 15,238 ledger rows are `apply.workable.com/{slug}`, 1,623 are
    # `{slug}.workable.com`. Sweeping only the first leaves those 1,623 unreachable. The `sub`
    # half has a dense apex that sorts ahead of the slugs in urlkey order, so its page 1 is all
    # `apply.`/`www.`; skip it with `--domain workable.com --filter …`, which scopes the filter
    # to that host so the `apply.` path walk is left alone.
    "workable": _with_style("path", "apply.workable.com")
    + _with_style("sub", "workable.com"),
    "workday": (
        ("myworkdayjobs.com", "workday"),
        ("myworkdaysite.com", "workdaysite"),
    ),
    # 8 TLDs; `.com` alone is 6,101 of 8,197 known slugs.
    "zoho": _with_style(
        "sub",
        "zohorecruit.com",
        "zohorecruit.eu",
        "zohorecruit.in",
        "zohorecruit.com.au",
        "zohorecruit.ca",
        "zohorecruit.sa",
        "zohorecruit.com.cn",
        "zohorecruit.jp",
    ),
}

# Deliberately absent, so nobody re-derives them from scratch:
#   join    — `join.com/companies/{slug}` is a two-segment path, so `path` would harvest
#             "companies" for every row. Also in `registry.DISABLED_ATS` (~99.99% non-tech).
#   sensehq — has a scraper (`registry.SCRAPERS`) and an enumerable `{slug}.sensehq.com`, but no
#             liveness ledger, so there is nothing to check a sweep against yet. Add it here once
#             `data/validate/liveness/sensehq.csv` exists.
#   oracle, phenom, pyjamahr, zwayam — no enumerable host namespace (per-tenant pods, UUIDs, or
#             boards that live on customer domains).
#   greythr, qandle, beehive, taleo, HirePro, iSmartRecruit, Recruit CRM, Ceipal — verified dead
#             ends (CLAUDE.md's ATS-expansion TODO); the retired PowerShell feeder still swept
#             qandle and beehive.
# An ATS with no scraper yet (turbohire, peoplestrong, jobsoid, …) can still be swept ad hoc:
# `--domain HOST --style sub` bypasses this table.


def valid(label: str, path_slug: bool = False) -> bool:
    """Whether ``label`` looks like a Company's slug rather than ATS furniture.

    Case-insensitive, because a mixed-case slug is still a slug; ASCII-only, because CDX serves
    raw UTF-8 paths and an IDN or a stray `²` is noise rather than a slug.

    ``path_slug`` widens the character set to dots and underscores, both legal in a path segment
    and neither legal in a hostname label — Ashby and Lever let a Company use its domain as its
    slug (`jobs.ashbyhq.com/adept.ai`), and Greenhouse and Rippling have underscored ones
    (`boards.greenhouse.io/edged_infrastructure`). Rejecting them cost 1,703 ledger rows, 199 of
    them live boards (dots 362/179, underscores 1,341/20). In a *subdomain* label a dot means a
    deeper subdomain and an underscore cannot occur, so the caller passes False there.
    """
    lowered = label.lower()
    if lowered in INFRA or not 1 < len(lowered) < 64:
        return False
    if not path_slug and _NUMBERED_WWW.fullmatch(lowered):
        return False
    if path_slug and lowered.endswith(FILE_SUFFIXES):
        return False  # a file served from the board root, not a slug
    if not (lowered[0].isascii() and lowered[0].isalnum()):
        return False
    extra = "-._" if path_slug else "-"
    return all(c.isascii() and (c.isalnum() or c in extra) for c in lowered)


def extract(url: str, host: str, style: Style) -> tuple[str, str] | None:
    """Read the slug and its board URL out of one archived URL, or None if it isn't one.

    Returns both, because reconstructing the board URL from the slug alone is lossy for Workday,
    whose host carries a datacenter (`acme.wd1.myworkdayjobs.com`) that no amount of
    `{slug}.{host}` gets back.

    The slug is returned **as written**. Hosts are case-insensitive so the host half is
    lowercased, but a path slug belongs to the ATS: 8,737 of SmartRecruiters' 12,706 ledger slugs
    are mixed-case, and lowercasing them yields slugs that resolve to nothing.
    """
    scheme, _, rest = url.partition("://")
    if scheme not in ("http", "https") or not rest:
        return None
    seen_host, _, path = rest.partition("/")
    path, _, query = path.partition("?")
    path = path.split("#")[0]
    seen_host = seen_host.split(":")[0].lower()

    if style == "path":
        if seen_host != host or not path:
            return None
        seg = path.split("/")[0]
        if seg.lower() == "embed":
            # Greenhouse's board-widget route carries the real slug in `?for=`, so the archived
            # widget URL names a Company as surely as a board URL does. Dropping the whole route
            # cost 43 ledger rows, 10 of them live boards that appear under no other shape.
            # A widget URL with no `for=` names nobody, and falls out below as an empty segment.
            # The rule is written for Greenhouse but applied to every path ATS, which trades a
            # hypothetical Company slugged exactly "embed" for not threading the ATS in here.
            seg = urllib.parse.parse_qs(query).get("for", [""])[0]
        if not valid(seg, path_slug=True):
            return None
        return seg, f"https://{host}/{seg}"

    if not seen_host.endswith("." + host):
        return None

    if style == "workday":
        # A Workday board is a *site* on a host, and `WorkdayScraper.slug_from` keeps the whole
        # careers URL because `url()` rebuilds `/wday/cxs/{company}/{site}/jobs` from it. Emitting
        # the bare host would hand the scraper a slug its own `_URL_PATTERN` rejects, so the site
        # segment has to survive — past any locale Wayback archived the board under.
        company, _, rest_host = seen_host.partition(".")
        instance = rest_host.split(".")[0]
        # Mirror `WorkdayScraper._URL_PATTERN`'s `wd\d+` exactly. Wayback has archived plenty of
        # `impl-wdN` hosts — Workday's implementation/preview tenants — and harvesting them hands
        # the scraper a slug it raises on, which a live round-trip caught and no replay could.
        if not _WD_INSTANCE.fullmatch(instance) or not valid(company):
            return None
        site = _workday_site(path)
        if not site:
            return None
        return f"{company}/{site}", f"https://{seen_host}/{site}"

    if style == "workdaysite":
        # Workday's *second* career-site domain, and the tenant is in the PATH rather than the
        # host: `https://wd{N}.myworkdaysite.com/[{locale}/]recruiting/{tenant}/{site}`. The
        # `workday` style above cannot read it — it takes the first host label as the company,
        # and here that label IS the `wd\d+` instance. Measured over one Common Crawl snapshot:
        # 108 Boards, 31 of them in no ledger, 12 of 14 sampled live (2,389 jobs).
        #
        # The emitted URL is the `myworkdayjobs.com` spelling, which is both what
        # `WorkdayScraper._URL_PATTERN` accepts unchanged and what collapses this against the
        # same board harvested from the other domain.
        instance = seen_host.split(".")[0]
        if not _WD_INSTANCE.fullmatch(instance):
            return None  # impl-/perf-/wcpdev- are non-production tenants
        segments = [seg for seg in path.split("/") if seg]
        if segments and _LOCALE.fullmatch(segments[0]) and len(segments) > 1:
            segments = segments[1:]
        if len(segments) < 3 or segments[0].lower() != "recruiting":
            return None
        company = segments[1]
        if not valid(company):
            return None
        site = _workday_site("/".join(segments[2:]))
        if not site:
            return None
        return (
            f"{company}/{site}",
            f"https://{company}.{instance}.myworkdayjobs.com/{site}",
        )

    label = seen_host[: -len("." + host)]
    if label == "www" and style == "sub":
        # Teamtailor's "powered by" backlink, on every career site it hosts:
        # `https://www.teamtailor.com/?utm_campaign=poweredby&utm_content={slug}.teamtailor.com`.
        # The slug is in the query, like Greenhouse's `embed?for=` above, and `www` is INFRA so
        # the row was dropped. Measured: 183 ledger rows carry this shape (174 live) and 53 of
        # those tenants — 51 live — appear in no Teamtailor harvest. Guarded on `utm_content`
        # naming a host under this same ATS, so it cannot fire on another vendor's backlink.
        content = urllib.parse.parse_qs(query).get("utm_content", [""])[0].lower()
        content = content.split("/")[0].split(":")[0]
        if not content.endswith("." + host):
            return None
        label = content[: -len("." + host)]
        if "." in label or not valid(label):
            return None
        return label, f"https://{content}"
    if "." in label or not valid(label):
        return None  # a deeper subdomain, or furniture — not a slug
    # `host` ATSes are keyed by the whole board host, because that is what their scraper is
    # handed as a slug; a `sub` ATS is keyed by the label, and its scraper recovers the host
    # from the URL emitted alongside it.
    canonical = _CANONICAL_HOST.get(host)
    if canonical:
        seen_host = f"{label}.{canonical}"
    return (seen_host if style == "host" else label), f"https://{seen_host}"


def _workday_site(path: str) -> str | None:
    """The board's site segment, skipping any locale Wayback archived it under.

    Deliberately not `valid()`: that rejects `INFRA` words, and a Workday site is very often
    called exactly `Careers` or `External_Careers`. Here the segment is a board's real name, not
    a label competing with the ATS's own furniture, so only shape and file-ness are checked.
    """
    segments = [s for s in path.split("/") if s]
    if segments and segments[0].lower() == "wday":
        # `/wday/` is Workday's own namespace, and exactly one route in it names a board: the CXS
        # jobs endpoint, `/wday/cxs/{company}/{site}/jobs`. Reading that recovers real boards
        # Wayback archived only in API form. Everything else under `/wday/` is machinery, and
        # taking its first segment invented boards called `wday` and `videoLabels`.
        lowered = [s.lower() for s in segments]
        if lowered[:2] != ["wday", "cxs"] or len(segments) < 5 or lowered[4] != "jobs":
            return None
        segments = segments[3:4]
    for i, seg in enumerate(segments):
        if seg.lower() in _WD_NON_SITE:
            return None
        # A locale prefix only counts as one when something follows it to be the site. Workday
        # sites are routinely two letters themselves (`ac`, `au`, `hc`, `da` — 83 live boards),
        # so a trailing `xx` is the board, not a language.
        if _LOCALE.fullmatch(seg) and i + 1 < len(segments):
            continue
        # Loose on purpose: real sites include `1`, `G`, `_penn-careers`, and a 70-character
        # `ccd-denver-denvergov-…`. Hostname-label rules reject all of those, and did — 21 live
        # boards' worth. A site is a path segment, so only shape and file-ness are checked.
        if seg.lower().endswith(FILE_SUFFIXES) or len(seg) > 128:
            return None
        ok = all(c.isascii() and (c.isalnum() or c in "-._") for c in seg)
        return seg if ok else None
    return None


def dedupe_key(slug: str, url: str, style: Style) -> str:
    """What makes two harvested rows the same board.

    Not always the slug. A `path` ATS serves one Company from aliased hosts — Greenhouse's
    `boards.` and `job-boards.` are the same board — so the slug is the identity and the host is
    noise. Every other style is the reverse: Zoho's TLDs are *regional pods*, not aliases, and 62
    labels exist on two of them (14 with both sides live), so keying on the label alone would
    drop a real second board and leave no trace it had been seen.
    """
    return slug.lower() if style == "path" else url.lower()


def cli(doc: str) -> argparse.ArgumentParser:
    """The CLI both harvesters share: which ATS, and optionally which single host."""
    ap = argparse.ArgumentParser(
        description=doc, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("ats", help=f"one of: {', '.join(sorted(ATS_HOSTS))}")
    ap.add_argument("--domain", help="sweep only this host instead of all of the ATS's")
    ap.add_argument(
        "--style",
        choices=STYLES,
        help="with --domain, sweep a host that is not in the table at all",
    )
    return ap


def hosts_for(
    ats: str, domain: str | None = None, style: Style | None = None
) -> list[tuple[str, Style]]:
    """The (host, style) pairs to sweep.

    With no ``domain``, every host the ATS serves from. With ``domain``, just that one — and with
    ``style`` too, without consulting the table at all, so an ATS that has no scraper yet can
    still be swept by hand.
    """
    if style and not domain:
        raise SystemExit("--style only means anything with --domain")
    if domain and style:
        return [(domain, style)]
    if ats not in ATS_HOSTS:
        raise SystemExit(
            f"unknown ats {ats!r}. known: {', '.join(sorted(ATS_HOSTS))}\n"
            f"for an ATS not in the table, pass --domain HOST --style {'|'.join(STYLES)}"
        )
    hosts = ATS_HOSTS[ats]
    if not domain:
        return list(hosts)
    for known, known_style in hosts:
        if known == domain:
            return [(known, known_style)]
    raise SystemExit(
        f"{ats} does not serve from {domain!r}. known hosts: "
        f"{', '.join(h for h, _ in hosts)} (or pass --style to sweep it anyway)"
    )


def adopt_legacy_state(ats: str, suffix: str) -> None:
    """Carry a pre-per-host cursor over to its new name, or say why it can't be.

    State files gained the host in their name when a sweep became multi-host, so an old
    `.{ats}_pages_done` is no longer found and the sweep silently restarts. Where the ATS serves
    from exactly one host the old file can only have come from that host, so adopt it. Where it
    serves from several there is no way to tell which, so say so rather than guess — re-walking
    is merely slow, but crediting the wrong host would skip pages that were never read.
    """
    legacy = WB / f".{ats}_{suffix}"
    if not legacy.exists():
        return
    hosts = ATS_HOSTS.get(ats, ())
    if len(hosts) == 1:
        adopted = WB / f".{ats}_{hosts[0][0]}_{suffix}"
        if adopted.exists():
            print(
                f"note: {legacy.name} superseded by {adopted.name}; ignoring it",
                flush=True,
            )
            return
        legacy.rename(adopted)
        print(f"note: adopted {legacy.name} as {adopted.name}", flush=True)
        return
    candidates = ", ".join(h for h, _ in hosts) or "the host it was swept from"
    print(
        f"note: {legacy.name} predates per-host state and names no host, so it is ignored and "
        f"this sweep restarts. Rename it to .{ats}_<host>_{suffix} for whichever of "
        f"{candidates} it was swept from to keep the progress.",
        flush=True,
    )


def _style_of(url: str, hosts: tuple[tuple[str, Style], ...]) -> Style:
    """Which of the ATS's hosts a stored row came from, and so how to key it.

    Matched by suffix as well as equality, because only a `path` row's host *is* the table host:
    a `sub`, `host` or `workday` row sits underneath it (`acme.wd1.myworkdayjobs.com` under
    `myworkdayjobs.com`). Comparing by equality alone silently mis-keyed every Workday row on
    reload, so a resumed sweep re-wrote rows it already had.
    """
    seen_host = url.split("://")[-1].split("/")[0].lower()
    for (
        known,
        style,
    ) in hosts:  # exact first: `apply.workable.com` before `workable.com`
        if seen_host == known:
            return style
    for known, style in hosts:
        if seen_host.endswith("." + known):
            return style
    return "path" if "/" in url.split("://")[-1] else "sub"


# `%2F` — the encoding of `/` — glued onto the front of a real hostname. A redirect-wrapped
# link (`...?url=https%3A%2F%2Fcareers-aei.icims.com`) archived and canonicalised loses its `%`,
# so CDX serves `http://2fcareers-aei.icims.com/` and `extract` sees a well-formed host with a
# dotless label that no rule rejects. The board is then stored twice: once real, once mangled.
_ENCODED_SLASH = re.compile(r"^(252f|2f)(?=.)")


def _leading_label(tenant: str, style: Style) -> str:
    """The token a mangled prefix would be glued to, in this style's own identity.

    Style-aware because a `path` slug may legally contain dots — Ashby and Lever let a Company use
    its domain (`adept.ai`), 1,528 such slugs on Ashby alone — so splitting one at the first dot
    would put a stub into the corroboration set and could strip a real Company down to a label
    `valid()` would never have admitted. Only a hostname's leading label ends at a dot.
    """
    head = tenant.split("/")[0]  # workday keys as `{company}/{site}`
    return (head if style == "path" else head.split(".")[0]).lower()


def prune_encoded_slashes(ats: str, out: Path) -> int:
    """Drop rows whose slug is `%2F` + a host we independently know, and say how many.

    Runs after the harvest, not inside :func:`extract`, because the test is *relational*: it needs
    the rest of the corpus. `extract` sees one URL and cannot tell `2fcareers-aei` (mangled, since
    `careers-aei` exists) from `2flystudiosde` (real — "2Fly Studios", and `lystudiosde` is
    nothing). Stripping every `2f` unconditionally would delete that live board, so the stripped
    form must be corroborated before a row is dropped.

    Runs on every `slug_sink` exit and rewrites the whole file, not just this run's rows — so a
    `--domain` sweep of one host still prunes the rest. That is deliberate: corroboration grows as
    the corpus does, so an artifact left behind by an earlier partial sweep is cleaned up by the
    next one that happens to find its real host.

    Measured 2026-09-08 over all harvests: 142 rows carry the prefix and 125 (88%) decode to a
    host already in the same file or the ledger. The other 17 stay — an artifact whose real host
    this sweep has not reached yet is indistinguishable from a genuine `2f…` company, and a
    later sweep that finds the real host will prune it then.
    """
    if not out.exists():
        return 0
    hosts = ATS_HOSTS.get(ats, ())

    def label(tenant: str, url: str) -> str:
        return _leading_label(tenant, _style_of(url, hosts))

    with out.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    known = {label(r["tenant"], r["url"]) for r in rows}
    ledger = ROOT / "data" / "validate" / "liveness" / f"{ats}.csv"
    if ledger.exists():
        with ledger.open(encoding="utf-8") as fh:
            known |= {
                label(r["tenant"], r["url"])
                for r in csv.DictReader(fh)
                if r.get("tenant") and r.get("url")
            }

    def mangled(row: dict) -> bool:
        head = label(row["tenant"], row["url"])
        m = _ENCODED_SLASH.match(head)
        return bool(m) and head[m.end() :] in known

    keep = [r for r in rows if not mangled(r)]
    dropped = len(rows) - len(keep)
    if dropped:
        # Written aside and renamed, never truncated in place: `slug_sink` promises a harvest is
        # never lost to a stall, and a crash midway through rewriting 20k rows would break exactly
        # that. `os.replace` is atomic on the same filesystem.
        tmp = out.with_suffix(".csv.tmp")
        with tmp.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["ats", "tenant", "url"])
            for r in keep:
                w.writerow([r["ats"], r["tenant"], r["url"]])
        os.replace(tmp, out)
    return dropped


@contextmanager
def slug_sink(ats: str):
    """Open `data/wayback-ats/{ats}.csv` for appending and yield the sink that writes to it.

    The sink is preloaded with the slugs already on disk, lowercased, so a re-run dedupes against
    earlier harvests instead of duplicating them. Rows are flushed by the caller as each page
    lands — a harvest that only wrote at the end would lose everything on a stall.
    """
    WB.mkdir(parents=True, exist_ok=True)
    out = WB / f"{ats}.csv"
    # Preload with the same identity the sink will compute, so a re-run dedupes against earlier
    # harvests. The style comes from the row's own host, because one CSV can hold two of them —
    # Workable's `apply.` path rows and its `{slug}.` sub rows.
    seen: set[str] = set()
    if out.exists():
        with out.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                slug, url = row["tenant"], row["url"]
                seen.add(dedupe_key(slug, url, _style_of(url, ATS_HOSTS.get(ats, ()))))
    else:
        out.write_text("ats,tenant,url\n", encoding="utf-8")
    with out.open("a", newline="", encoding="utf-8") as f:
        sink = _Sink(ats, seen, csv.writer(f), f)
        yield sink
    dropped = prune_encoded_slashes(ats, out)
    if dropped:
        print(
            f"pruned {dropped} row(s) whose slug was %2F glued onto a host already known",
            flush=True,
        )
        # Counted off the file, not `len(seen) - dropped`: `seen` holds dedupe_keys, and for a
        # `path` ATS one key can stand for two rows, so subtracting rows from keys under-reports.
        seen = {r["tenant"] for r in csv.DictReader(out.open(encoding="utf-8"))}
    print(
        f"DONE: {len(seen)} unique {ats} slugs in data/wayback-ats/{ats}.csv",
        flush=True,
    )


class _Sink:
    """Writes a slug the first time it is seen, and reports whether it was new.

    `add` deliberately both mutates and answers: it is one decision, and splitting it into
    `contains` then `write` would invite a check-then-write race. It is **not** thread-safe —
    `wayback_pages` calls it from several page threads and holds its own lock to serialise them.
    """

    def __init__(self, ats, seen, writer, handle):
        self._ats, self._seen, self._writer, self._handle = ats, seen, writer, handle

    def add(self, found: tuple[str, str], style: Style) -> bool:
        slug, url = found
        key = dedupe_key(slug, url, style)
        if key in self._seen:
            return False
        self._seen.add(key)
        self._writer.writerow([self._ats, slug, url])
        return True

    def flush(self) -> None:
        self._handle.flush()
