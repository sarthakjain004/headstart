#!/usr/bin/env python3
"""Shared vocabulary for the Wayback CDX tenant harvesters.

`wayback_pages.py` and `wayback_paginate.py` are two strategies for one job — walk an ATS's
archived URLs and read the tenant out of each — so the part that decides *what a tenant looks
like* lives here rather than in each of them.

`PROVIDERS` is the list of board hosts worth sweeping. It is derived from the scrapers' own URL
construction and cross-checked against the row counts in `data/validate/liveness/{ats}.csv`, so
a provider that serves boards from several hosts carries all of them: sweeping only
`zohorecruit.com` reaches 6,107 of Zoho's 8,197 known tenants and silently misses the rest.
"""

from dataclasses import dataclass

# Subdomains and path segments that are the ATS's own furniture, not a tenant.
INFRA = {
    "www",
    "app",
    "apps",
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
    "embed",
}


@dataclass(frozen=True)
class Provider:
    """Where an ATS's boards live, and how to read a tenant out of one of its URLs.

    ``style`` is one of:

    - ``sub``     — ``{tenant}.{domain}``; the label left of the domain. Pass the FULL board
                    host as the domain (``jobs.personio.de``, not ``personio.de``), because a
                    label containing a dot is rejected as a deeper subdomain.
    - ``path``    — ``{domain}/{tenant}``; the first path segment.
    - ``workday`` — ``{tenant}.{dc}.{domain}``; the first host label, with the datacenter kept
                    in the reconstructed URL.
    """

    style: str
    domains: tuple[str, ...]
    note: str = ""


# Ordered by style, then by name. Counts in the notes are live rows in the liveness ledger at
# the time of writing (2026-08-13), and are there to say what a sweep is worth — not as a target.
#
# Callers dedupe a provider's hosts against one shared tenant set, which is safe because the
# label is allocated per provider rather than per host: across the ledgers, 0 of 8,197 Zoho
# tenants and 0 of 4,561 Personio tenants appear on more than one of their provider's hosts.
PROVIDERS: dict[str, Provider] = {
    # ── path: {host}/{tenant} ───────────────────────────────────────────────────────────────
    "ashby": Provider("path", ("jobs.ashbyhq.com",)),
    "greenhouse": Provider(
        "path",
        (
            "job-boards.greenhouse.io",
            "boards.greenhouse.io",
            "job-boards.eu.greenhouse.io",
            "boards.eu.greenhouse.io",
        ),
        # `*.us.greenhouse.io` resolves but 301/302s to the unprefixed host and holds no ledger
        # rows of its own — an alias, so sweeping it would only re-find what `boards` already has.
        note="4 pods; EU is a real split (824 live rows), US is an alias",
    ),
    "lever": Provider("path", ("jobs.lever.co", "jobs.eu.lever.co")),
    "rippling": Provider("path", ("ats.rippling.com",)),
    "smartrecruiters": Provider(
        "path",
        ("jobs.smartrecruiters.com", "careers.smartrecruiters.com"),
        note="slugs are case-sensitive and mostly mixed-case — see `extract`",
    ),
    "workable": Provider("path", ("apply.workable.com",)),
    # ── sub: {tenant}.{host} ────────────────────────────────────────────────────────────────
    "darwinbox": Provider("sub", ("darwinbox.in", "darwinbox.com")),
    "eightfold": Provider(
        "sub",
        ("eightfold.ai",),
        note="misses the vanity tail (careers.qualcomm.com and kin), which has no namespace",
    ),
    "freshteam": Provider("sub", ("freshteam.com",)),
    "keka": Provider("sub", ("keka.com",)),
    "personio": Provider("sub", ("jobs.personio.com", "jobs.personio.de")),
    "recruitee": Provider("sub", ("recruitee.com",)),
    "ripplehire": Provider("sub", ("ripplehire.com",)),
    "successfactors": Provider(
        "sub",
        ("jobs2web.com", "jobs.hr.cloud.sap"),
        # The slug IS the customer's vanity host (`careers.wipro.com`), spread over hundreds of
        # apexes, so most of the ~2,100 live boards are unreachable by any domain sweep. These
        # two hosts are the part that is enumerable; the rest needs the careers-page scan.
        note="partial by nature — ~66 of ~2,100 live boards",
    ),
    "teamtailor": Provider("sub", ("teamtailor.com",)),
    "trakstar": Provider("sub", ("hire.trakstar.com",)),
    "zoho": Provider(
        "sub",
        (
            "zohorecruit.com",
            "zohorecruit.eu",
            "zohorecruit.in",
            "zohorecruit.com.au",
            "zohorecruit.ca",
            "zohorecruit.sa",
            "zohorecruit.jp",
            "zohorecruit.com.cn",
        ),
        note="8 TLDs; .com alone is 6,107 of 8,197 known tenants",
    ),
    # ── workday: {tenant}.{dc}.{host} ───────────────────────────────────────────────────────
    "workday": Provider("workday", ("myworkdayjobs.com",)),
}

# Deliberately absent, so nobody re-derives them from scratch:
#   join   — `join.com/companies/{slug}` is a two-segment path, so `path` would harvest
#            "companies" for every row. It is also in `registry.DISABLED_ATS` (~99.99% non-tech).
#   oracle, phenom, pyjamahr, zwayam — no enumerable host namespace (per-tenant pods, UUIDs, or
#            boards that live on customer domains).
#   greythr, qandle, beehive, taleo, HirePro, iSmartRecruit, Recruit CRM, Ceipal — verified
#            dead ends (CLAUDE.md's provider TODO); the old PowerShell feeder still swept
#            qandle and beehive.


def valid(label: str) -> bool:
    """Whether ``label`` looks like a tenant rather than ATS furniture.

    Case-insensitive: a mixed-case slug is still a slug (SmartRecruiters writes most of them
    that way), so the check lowercases and the caller keeps the original.
    """
    lowered = label.lower()
    if lowered in INFRA:
        return False
    return 1 < len(lowered) < 64 and lowered[0].isalnum() and _is_label(lowered)


def _is_label(lowered: str) -> bool:
    return all(c.isalnum() or c == "-" for c in lowered)


def extract(url: str, domain: str, style: str) -> tuple[str, str] | None:
    """Read the tenant and its board URL out of one archived URL, or None if it isn't one.

    Returns both because reconstructing the board URL from the tenant alone is lossy for
    Workday, whose host carries a datacenter (`acme.wd1.myworkdayjobs.com`) that no amount of
    `{tenant}.{domain}` gets back.

    The tenant is returned **as written**. Hosts are case-insensitive so the host half is
    lowercased, but a path slug belongs to the ATS: SmartRecruiters has 8,737 mixed-case
    tenants of 12,706, and lowercasing them yields slugs that resolve to nothing.
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
        return (seg, f"https://{domain}/{seg}") if valid(seg) else None

    if not host.endswith("." + domain):
        return None
    if style == "sub":
        label = host[: -len("." + domain)]
        return (label, f"https://{host}") if "." not in label and valid(label) else None
    if style == "workday":
        label = host.split(".")[0]
        # Keep the whole host: the datacenter is not derivable from the tenant.
        return (label, f"https://{host}") if valid(label) else None
    raise ValueError(f"unknown style {style!r}")


def targets(ats: str, domain: str | None = None) -> list[tuple[str, str]]:
    """The (domain, style) pairs to sweep for ``ats`` — all of them, or just ``domain``."""
    if ats not in PROVIDERS:
        known = ", ".join(sorted(PROVIDERS))
        raise SystemExit(f"unknown ats {ats!r}. known: {known}")
    provider = PROVIDERS[ats]
    if domain:
        return [(domain, provider.style)]
    return [(d, provider.style) for d in provider.domains]
