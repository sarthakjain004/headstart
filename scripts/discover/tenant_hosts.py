"""The ledger row a tenant hostname names, for the ATSes that give every customer its own subdomain.

`mine_common_crawl_host_graph.py` and `mine_crux_origin_list.py` both read lists of *hostnames*
(a web graph's vertices, a browser-telemetry origin list) and need the same answer for each:
is this `{label}.{vendor}.com` a tenant, and if so what is its `(ats, tenant, url)` in the ledger's
own spelling. `wayback_feeder.ATS_HOSTS` cannot answer it for these lists: it lacks JazzHR
(`applytojob.com`), and its `extract` drops a dotted label, which is how Teamtailor's regional pod
`{slug}.na.teamtailor.com` (a Board of its own, `teamtailor:{slug}.na`) was never mined.

Spelling, as `data/validate/liveness/{ats}.csv` holds each family (2026-09-29): the tenant is the
bare lowercase label and the url `https://{host}`; Teamtailor's `.na` pod is tenant `{slug}.na`
(6 rows landed 2026-09-28); Personio keeps the TLD it was seen on (`.jobs.personio.de` or `.com`,
one Board either way, `PersonioScraper.board_key`).
"""

from __future__ import annotations

import re

#: `(ats, apex, prefix labels between the tenant and the apex)`. The tenant is the one label in
#: front of `prefix.apex`; a host with any more labels is vendor infrastructure or a vanity name.
FAMILIES = (
    ("bamboohr", "bamboohr.com", ""),
    ("teamtailor", "teamtailor.com", ""),
    ("teamtailor", "teamtailor.com", "na"),
    ("personio", "personio.com", "jobs"),
    ("personio", "personio.de", "jobs"),
    ("jazzhr", "applytojob.com", ""),
    ("breezy", "breezy.hr", ""),
    ("recruitee", "recruitee.com", ""),
    ("pinpoint", "pinpointhq.com", ""),
    ("clearcompany", "hrmdirect.com", ""),
    ("freshteam", "freshteam.com", ""),
    ("trakstar", "trakstar.com", "hire"),
)

#: A first label that is the vendor's own site, never a customer.
VENDOR_LABELS = frozenset(
    {
        "www", "app", "login", "api", "id", "auth", "cdn", "apply", "jobs", "careers", "my",
        "join", "onboarding", "resources", "interviews", "community", "support", "help",
        "status", "mail", "partners", "docs", "blog", "hire", "assets", "apps", "mobile", "s",
        "data-warehouse-docs",
    }
)  # fmt: skip

_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def reversed_prefixes() -> dict[str, tuple[str, str, str]]:
    """`{reversed host prefix: family}`, e.g. `com.teamtailor.na.` for the `.na` pod, for scanning a
    list sorted by reversed host (a web graph's vertices)."""
    out = {}
    for family in FAMILIES:
        _, apex, middle = family
        labels = [*reversed(apex.split(".")), *([middle] if middle else [])]
        out[".".join(labels) + "."] = family
    return out


def row_for_host(host: str) -> tuple[str, str, str] | None:
    """`(ats, tenant, url)` for a tenant host of one of the FAMILIES, else None."""
    host = host.strip().lower().rstrip(".")
    for ats, apex, middle in FAMILIES:
        suffix = f".{middle}.{apex}" if middle else f".{apex}"
        if not host.endswith(suffix):
            continue
        label = host[: -len(suffix)]
        if "." in label or not _LABEL.match(label) or label in VENDOR_LABELS:
            continue
        if middle == "na":
            return ats, f"{label}.na", f"https://{host}"
        return ats, label, f"https://{host}"
    return None
