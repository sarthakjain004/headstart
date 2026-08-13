#!/usr/bin/env python3
"""Which hosts each ATS serves boards from, and how to read a tenant out of one of its URLs.

`wayback_pages.py` and `wayback_paginate.py` are two strategies for one job — walk an ATS's
archived CDX URLs and collect the tenants — so what a tenant *looks like* is decided here rather
than in each of them.

`PROVIDERS` is derived from the scrapers' own URL construction and cross-checked against
`data/validate/liveness/{ats}.csv`. A provider is rarely one host, and sweeping only the obvious
one loses tenants silently: Zoho spreads 8,197 known tenants over 8 TLDs, of which
`zohorecruit.com` holds 6,101.
"""

import argparse
import csv
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parent.parent.parent
WB = ROOT / "data" / "wayback-ats"

Style = Literal["sub", "path", "workday"]

# Subdomains and path segments that are the ATS's own furniture, not a tenant.
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


@dataclass(frozen=True)
class Provider:
    """The (host, style) pairs to sweep for one ATS.

    ``style`` is per host, because a provider can serve two shapes at once — Workable has
    15,238 ledger rows on `apply.workable.com/{tenant}` and 1,623 on `{tenant}.workable.com`:

    - ``sub``     — ``{tenant}.{host}``; the label left of the host. The host must be the FULL
                    board host (``jobs.personio.de``, not ``personio.de``), because a label
                    containing a dot is rejected as a deeper subdomain.
    - ``path``    — ``{host}/{tenant}``; the first path segment.
    - ``workday`` — ``{tenant}.{dc}.{host}``; the first host label, with the datacenter kept in
                    the reconstructed URL.
    """

    hosts: tuple[tuple[str, Style], ...]


def _at(style: Style, *domains: str) -> tuple[tuple[str, Style], ...]:
    return tuple((d, style) for d in domains)


# Row/live counts in the comments are from the liveness ledgers on 2026-08-13, and say what a
# sweep is worth — they are not targets.
#
# Callers dedupe a provider's hosts against one shared tenant set, which is safe because the
# label is allocated per provider rather than per host: across the ledgers, 0 of 8,197 Zoho
# tenants and 0 of 4,561 Personio tenants appear on more than one of their provider's hosts.
PROVIDERS: dict[str, Provider] = {
    "ashby": Provider(_at("path", "jobs.ashbyhq.com")),
    "darwinbox": Provider(_at("sub", "darwinbox.in", "darwinbox.com")),
    # Misses the vanity tail (careers.qualcomm.com and kin), which has no host namespace.
    "eightfold": Provider(_at("sub", "eightfold.ai")),
    "freshteam": Provider(_at("sub", "freshteam.com")),
    # `*.us.greenhouse.io` resolves but 301/302s to the unprefixed host and holds no ledger rows
    # of its own — an alias, so sweeping it would only re-find what `boards` already has. The EU
    # pods are a real split: 824 rows, 497 live.
    "greenhouse": Provider(
        _at(
            "path",
            "job-boards.greenhouse.io",
            "boards.greenhouse.io",
            "job-boards.eu.greenhouse.io",
            "boards.eu.greenhouse.io",
        )
    ),
    "keka": Provider(_at("sub", "keka.com")),
    "lever": Provider(
        _at("path", "jobs.lever.co", "jobs.eu.lever.co")
    ),  # EU: 154 rows, 92 live
    "personio": Provider(_at("sub", "jobs.personio.com", "jobs.personio.de")),
    "recruitee": Provider(_at("sub", "recruitee.com")),
    "ripplehire": Provider(_at("sub", "ripplehire.com")),
    "rippling": Provider(_at("path", "ats.rippling.com")),
    # Slugs are case-sensitive and mostly mixed-case (8,737 of 12,706 ledger tenants) — see
    # `extract`, which is why this provider cannot use a lowercasing extractor.
    "smartrecruiters": Provider(
        _at("path", "jobs.smartrecruiters.com", "careers.smartrecruiters.com")
    ),
    # Partial by nature: the slug IS the customer's vanity host (`careers.wipro.com`), spread
    # over hundreds of apexes, so most of the ~2,100 live boards are unreachable by any domain
    # sweep. These two hosts are the enumerable part — ~66 live boards. The rest needs the
    # careers-page scan, not a wider sweep.
    "successfactors": Provider(_at("sub", "jobs2web.com", "jobs.hr.cloud.sap")),
    "teamtailor": Provider(_at("sub", "teamtailor.com")),
    "trakstar": Provider(_at("sub", "hire.trakstar.com")),
    # Two shapes at once: 15,238 ledger rows are `apply.workable.com/{tenant}`, 1,623 are
    # `{tenant}.workable.com`. Sweeping only the first leaves those 1,623 unreachable.
    # The `sub` half has a dense apex that sorts ahead of the tenants in urlkey order, so page 1
    # is all `apply.`/`www.` — use wayback_paginate's --filter to skip it, as for eightfold.
    "workable": Provider(
        _at("path", "apply.workable.com") + _at("sub", "workable.com")
    ),
    "workday": Provider(_at("workday", "myworkdayjobs.com")),
    # 8 TLDs; `.com` alone is 6,101 of 8,197 known tenants.
    "zoho": Provider(
        _at(
            "sub",
            "zohorecruit.com",
            "zohorecruit.eu",
            "zohorecruit.in",
            "zohorecruit.com.au",
            "zohorecruit.ca",
            "zohorecruit.sa",
            "zohorecruit.com.cn",
            "zohorecruit.jp",
        )
    ),
}

# Deliberately absent, so nobody re-derives them from scratch:
#   join   — `join.com/companies/{slug}` is a two-segment path, so `path` would harvest
#            "companies" for every row. It is also in `registry.DISABLED_ATS` (~99.99% non-tech).
#   oracle, phenom, pyjamahr, zwayam — no enumerable host namespace (per-tenant pods, UUIDs, or
#            boards that live on customer domains).
#   greythr, qandle, beehive, taleo, HirePro, iSmartRecruit, Recruit CRM, Ceipal — verified dead
#            ends (CLAUDE.md's provider TODO); the retired PowerShell feeder still swept qandle
#            and beehive.
# A provider with no scraper yet (turbohire, peoplestrong, jobsoid, …) can still be swept ad hoc:
# `--domain HOST --style sub` bypasses this table.


# Well-known files served from a board host's root. They look exactly like a dotted path slug,
# so they are only distinguishable by name.
WELL_KNOWN = {"ads.txt", "robots.txt", "sitemap.xml", "favicon.ico", "security.txt"}


def valid(label: str, allow_dot: bool = False) -> bool:
    """Whether ``label`` looks like a tenant rather than ATS furniture.

    Case-insensitive, because a mixed-case slug is still a slug; ASCII-only, because CDX serves
    raw UTF-8 paths and an IDN or a stray `²` is noise rather than a tenant.

    ``allow_dot`` is for path slugs, where a dot is legal — Ashby and Lever let a company use its
    domain as its slug (`jobs.ashbyhq.com/adept.ai`). Rejecting those cost 372 ledger rows, 180
    of them live boards. In a *subdomain* label a dot means a deeper subdomain, never a tenant,
    so the caller passes False there.
    """
    lowered = label.lower()
    if lowered in INFRA or lowered in WELL_KNOWN or not 1 < len(lowered) < 64:
        return False
    if not (lowered[0].isascii() and lowered[0].isalnum()):
        return False
    extra = "-." if allow_dot else "-"
    return all(c.isascii() and (c.isalnum() or c in extra) for c in lowered)


def extract(url: str, domain: str, style: Style) -> tuple[str, str] | None:
    """Read the tenant and its board URL out of one archived URL, or None if it isn't one.

    Returns both, because reconstructing the board URL from the tenant alone is lossy for
    Workday, whose host carries a datacenter (`acme.wd1.myworkdayjobs.com`) that no amount of
    `{tenant}.{domain}` gets back.

    The tenant is returned **as written**. Hosts are case-insensitive so the host half is
    lowercased, but a path slug belongs to the ATS: 8,737 of SmartRecruiters' 12,706 ledger
    tenants are mixed-case, and lowercasing them yields slugs that resolve to nothing.
    """
    scheme, _, rest = url.partition("://")
    if scheme not in ("http", "https") or not rest:
        return None
    host, _, path = rest.partition("/")
    path = path.split("?")[0].split("#")[0]
    host = host.split(":")[0].lower()

    if style == "path":
        if host != domain or not path:
            return None
        seg = path.split("/")[0]
        # `embed` is a Greenhouse board-widget route, not a tenant. Only path boards have it.
        if seg.lower() == "embed" or not valid(seg, allow_dot=True):
            return None
        return seg, f"https://{domain}/{seg}"

    if not host.endswith("." + domain):
        return None
    label = host[: -len("." + domain)] if style == "sub" else host.split(".")[0]
    if style == "sub" and "." in label:
        return None  # a deeper subdomain, not a tenant
    # Keep the whole host: for workday the datacenter is not derivable from the tenant.
    return (label, f"https://{host}") if valid(label) else None


def targets(
    ats: str, domain: str | None = None, style: Style | None = None
) -> list[tuple[str, Style]]:
    """The (host, style) pairs to sweep.

    With no ``domain``, every host the ATS serves from. With ``domain``, just that one — and
    with ``style`` too, without consulting the table at all, so an ATS that has no scraper yet
    can still be swept by hand.
    """
    if domain and style:
        return [(domain, style)]
    if ats not in PROVIDERS:
        known = ", ".join(sorted(PROVIDERS))
        raise SystemExit(
            f"unknown ats {ats!r}. known: {known}\n"
            f"for an ATS not in the table, pass --domain HOST --style sub|path|workday"
        )
    hosts = PROVIDERS[ats].hosts
    if not domain:
        return list(hosts)
    for host, host_style in hosts:
        if host == domain:
            return [(host, host_style)]
    raise SystemExit(
        f"{ats} does not serve from {domain!r}. known hosts: "
        f"{', '.join(h for h, _ in hosts)} (or pass --style to sweep it anyway)"
    )


def host_picker(doc: str) -> "argparse.ArgumentParser":
    """The CLI both harvesters share: which ATS, and optionally which single host."""
    ap = argparse.ArgumentParser(
        description=doc, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("ats", help=f"one of: {', '.join(sorted(PROVIDERS))}")
    ap.add_argument("--domain", help="sweep only this host instead of all of the ATS's")
    ap.add_argument(
        "--style",
        choices=("sub", "path", "workday"),
        help="with --domain, sweep a host that is not in the table at all",
    )
    return ap


def picked_hosts(args) -> list[tuple[str, Style]]:
    if args.style and not args.domain:
        raise SystemExit("--style only means anything with --domain")
    return targets(args.ats, args.domain, args.style)


@contextmanager
def tenant_sink(ats: str):
    """Open `data/wayback-ats/{ats}.csv` for appending, yielding (seen, writer).

    ``seen`` is preloaded with the tenants already on disk, lowercased, so a re-run dedupes
    against earlier harvests instead of duplicating them. Rows are flushed by the caller as
    each page lands — a harvest that only wrote at the end would lose everything on a stall.
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
        yield seen, _Sink(ats, seen, csv.writer(f), f)
    print(
        f"DONE: {len(seen)} unique {ats} tenants in data/wayback-ats/{ats}.csv",
        flush=True,
    )


class _Sink:
    """Writes a tenant the first time it is seen, and reports whether it was new."""

    def __init__(self, ats, seen, writer, handle):
        self._ats, self._seen, self._writer, self._handle = ats, seen, writer, handle

    def add(self, found: tuple[str, str]) -> bool:
        tenant, url = found
        if tenant.lower() in self._seen:
            return False
        self._seen.add(tenant.lower())
        self._writer.writerow([self._ats, tenant, url])
        return True

    def flush(self) -> None:
        self._handle.flush()
