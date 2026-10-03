"""Archive sweep coverage outside enumerable ATS namespaces.

Company-domain providers can be checked on known hosts; this is an archive-presence
audit, not discovery of unknown customers. The single-company scrapers have no
tenant roster to discover. Tests reconcile these sets with the scraper registry.
"""

import csv
from pathlib import Path
from urllib.parse import urlsplit

from headstart.scrapers.registry import SCRAPERS

ROOT = Path(__file__).resolve().parents[2]
SINGLE_SOURCE_ATS = frozenset(
    {"amazon", "apple", "bytedance", "google", "meta", "tesla", "tiktok", "uber"}
)
COMPANY_DOMAIN_ATS = frozenset(
    {"phenom", "radancy", "happydance", "spire2grow", "wp_job_openings"}
)


def known_hosts(ats):
    """Every readable Board host in the provider's ledger and discovery pool."""
    hosts = set()
    for directory in ("data/validate/liveness", "data/ats-tenants-merged"):
        path = ROOT / directory / f"{ats}.csv"
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as source:
            for row in csv.DictReader(source):
                try:
                    slug = SCRAPERS[ats].slug_from(row["tenant"], row.get("url", ""))
                    host = urlsplit(slug if "://" in slug else "//" + slug).hostname
                except ValueError:
                    continue
                if host:
                    hosts.add(host.lower())
    return sorted(hosts)
