#!/usr/bin/env python3
"""The Wayback feeder's shared half: which hosts each ATS serves boards from, and how to read a
Company's slug out of one archived URL.

`wayback_pages.py` and `wayback_paginate.py` are two strategies for one job — walk an ATS's
archived CDX URLs and collect the slugs — so what a slug *looks like* is decided here rather than
in each of them.

`ATS_HOSTS` is derived from the scrapers' own URL construction and cross-checked against
`data/validate/liveness/{ats}.csv`. An ATS is rarely one host, and sweeping only the obvious one
loses boards silently: Zoho spreads 8,197 known slugs over 8 TLDs, of which `zohorecruit.com`
holds 6,101.

(The output column is still called `tenant`, matching every other discovery feeder's CSV.
CONTEXT.md retires the term but parks the code/data rename as a separate change.)
"""

import argparse
import csv
import urllib.parse
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parent.parent.parent
WB = ROOT / "data" / "wayback-ats"

Style = Literal["sub", "host", "path", "workday"]
STYLES: tuple[Style, ...] = ("sub", "host", "path", "workday")

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


def _with_style(style: Style, *hosts: str) -> tuple[tuple[str, Style], ...]:
    """Pair each host with the style that reads a slug out of it."""
    return tuple((h, style) for h in hosts)


# Host counts in the comments are liveness-ledger rows on 2026-08-13; they say what a sweep is
# worth, and are not targets.
#
# Callers dedupe an ATS's hosts against one shared slug set, which is safe because the label is
# allocated per ATS rather than per host: across the ledgers, 0 of 8,197 Zoho slugs and 0 of
# 4,561 Personio slugs appear on more than one of their ATS's hosts.
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
    ),
    "keka": _with_style("sub", "keka.com"),
    "lever": _with_style(
        "path", "jobs.lever.co", "jobs.eu.lever.co"
    ),  # EU: 154 rows, 92 live
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
    "trakstar": _with_style("sub", "hire.trakstar.com"),
    # Two shapes at once: 15,238 ledger rows are `apply.workable.com/{slug}`, 1,623 are
    # `{slug}.workable.com`. Sweeping only the first leaves those 1,623 unreachable. The `sub`
    # half has a dense apex that sorts ahead of the slugs in urlkey order, so its page 1 is all
    # `apply.`/`www.`; skip it with `--domain workable.com --filter …`, which scopes the filter
    # to that host so the `apply.` path walk is left alone.
    "workable": _with_style("path", "apply.workable.com")
    + _with_style("sub", "workable.com"),
    "workday": _with_style("workday", "myworkdayjobs.com"),
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
#             ends (CLAUDE.md's provider TODO); the retired PowerShell feeder still swept qandle
#             and beehive.
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
    label = (
        seen_host[: -len("." + host)] if style != "workday" else seen_host.split(".")[0]
    )
    if style != "workday" and "." in label:
        return None  # a deeper subdomain, not a slug
    if not valid(label):
        return None
    # `host` ATSes are keyed by the whole board host, because that is what their scraper is
    # handed as a slug; `sub` and `workday` are keyed by the label. Either way the URL is the
    # host as seen — for workday the datacenter is not derivable from the slug.
    slug = seen_host if style == "host" else label
    return slug, f"https://{seen_host}"


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
    print(
        f"note: {legacy.name} predates per-host state and names no host, so it is ignored and "
        f"this sweep restarts. Rename it to .{ats}_<host>_{suffix} for whichever of "
        f"{', '.join(h for h, _ in hosts)} it was swept from to keep the progress.",
        flush=True,
    )


@contextmanager
def slug_sink(ats: str):
    """Open `data/wayback-ats/{ats}.csv` for appending and yield the sink that writes to it.

    The sink is preloaded with the slugs already on disk, lowercased, so a re-run dedupes against
    earlier harvests instead of duplicating them. Rows are flushed by the caller as each page
    lands — a harvest that only wrote at the end would lose everything on a stall.
    """
    WB.mkdir(parents=True, exist_ok=True)
    out = WB / f"{ats}.csv"
    seen: set[str] = set()
    if out.exists():
        with out.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                seen.add(row["tenant"].lower())
    else:
        out.write_text("ats,tenant,url\n", encoding="utf-8")
    with out.open("a", newline="", encoding="utf-8") as f:
        sink = _Sink(ats, seen, csv.writer(f), f)
        yield sink
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

    def add(self, found: tuple[str, str]) -> bool:
        slug, url = found
        if slug.lower() in self._seen:
            return False
        self._seen.add(slug.lower())
        self._writer.writerow([self._ats, slug, url])
        return True

    def flush(self) -> None:
        self._handle.flush()
