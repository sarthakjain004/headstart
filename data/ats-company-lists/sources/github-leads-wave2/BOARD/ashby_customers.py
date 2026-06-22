"""
Source: Ashby Customer Stories Page
--------------------------------------
Scrapes the publicly listed customer logos and case-study links from
https://www.ashbyhq.com/customers to seed the pipeline with high-confidence
known slugs. These are Ashby's own customers with public case studies.
No API key required. Best used as a warm seed alongside CDX sources.
Typical yield: 30–80 company slugs.
"""

import re
import logging
from typing import Set
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

CUSTOMERS_URL = "https://www.ashbyhq.com/customers"

# Known slug overrides where the company name differs from their job board slug
# e.g. "EightSleep" → "eightsleep", "Monte Carlo" → "monte-carlo"
# These are resolved via the /customers/{slug} URL on ashby's site
_KNOWN_SEED_SLUGS: Set[str] = {
    "ramp", "notion", "linear", "cursor", "replit", "clay", "harvey",
    "vanta", "retool", "posthog", "deel", "shopify", "snowflake",
    "zapier", "reddit", "mercury", "ironclad", "lemonade", "lime",
    "gorgias", "uipath", "deliveroo", "alan", "altura", "amo",
    "aurora-solar", "boomi", "brightline", "coder", "convictional",
    "dave", "eightsleep", "flock-safety", "form-energy", "fullstory",
    "hackerone", "january", "marqeta", "monte-carlo", "multiverse",
    "netgear", "sequoia", "stytch", "vanta", "oyster", "hopper",
    "superhumanapp", "cohere", "supabase", "teal",
}


def _slugify_from_url(href: str) -> str | None:
    """
    Extract a company slug from a /customers/{slug} href.
    E.g. '/customers/aurora-solar' → 'aurora-solar'
    """
    try:
        path = urlparse(href).path.rstrip("/")
        parts = path.split("/")
        # /customers/{slug} has exactly 3 parts when split by /
        if len(parts) >= 3 and parts[1] == "customers":
            slug = parts[2].lower()
            if slug and len(slug) > 1:
                return slug
    except Exception:
        pass
    return None


def fetch() -> Set[str]:
    """
    Scrape the Ashby customers page and return all identifiable company slugs.
    Returns a set of unique company slugs.
    """
    slugs: Set[str] = set(_KNOWN_SEED_SLUGS)    # start with hard-coded seeds

    logger.info("[AshbyCustomers] Scraping ashbyhq.com/customers ...")

    try:
        resp = requests.get(
            CUSTOMERS_URL,
            timeout=20,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; job-discovery-pipeline/1.0)",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        # Find all links that look like /customers/{slug}
        for tag in soup.find_all("a", href=True):
            href: str = tag["href"]
            if "/customers/" in href:
                slug = _slugify_from_url(href)
                if slug:
                    slugs.add(slug)

        # Also look for img alt tags (logo images) to get company names
        for img in soup.find_all("img", alt=True):
            alt: str = img.get("alt", "").strip().lower()
            # Convert company name to slug format
            candidate = re.sub(r"[^a-z0-9]", "-", alt).strip("-")
            candidate = re.sub(r"-{2,}", "-", candidate)
            if candidate and len(candidate) > 1 and re.match(r"^[a-z0-9]", candidate):
                slugs.add(candidate)

    except Exception as exc:
        logger.warning(f"[AshbyCustomers] Scraping failed: {exc}. Using seed list only.")

    logger.info(f"[AshbyCustomers] Total slugs from customer page: {len(slugs)}")
    return slugs
