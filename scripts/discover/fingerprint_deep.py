"""Bounded evidence channels for careers hosts that static fingerprinting could not resolve.

Certificate SANs are discovery seeds, never proof of an ATS or employer. Browser work is passive:
one fresh page, no clicks/login, GET/HEAD only, 60 requests, two concurrent browsers, 12s navigation.
API hits require provider-specific JSON structure, not merely HTTP 200.
"""

from __future__ import annotations

import json
import re
import socket
import ssl
import threading
from contextlib import suppress
from functools import lru_cache
from urllib.parse import quote, urlsplit

import certifi
import tldextract

_PSL = tldextract.TLDExtract(
    suffix_list_urls=(), cache_dir=None, include_psl_private_domains=True
)
_BROWSERS = threading.BoundedSemaphore(2)


@lru_cache(maxsize=8192)
def public_domain(value: str) -> str:
    """Offline packaged PSL, including private boundaries such as github.io."""
    host = (urlsplit(value if "://" in value else "//" + value).hostname or "").rstrip(
        "."
    )
    host = host.encode("idna").decode("ascii").lower()
    return _PSL(host).top_domain_under_public_suffix or host


def certificate_names(host: str) -> tuple[list[str], str]:
    """Read a live, verified TLS certificate; cap the roster and omit wildcard guesses."""
    try:
        with (
            socket.create_connection((host, 443), timeout=4) as sock,
            ssl.create_default_context(cafile=certifi.where()).wrap_socket(
                sock, server_hostname=host
            ) as tls,
        ):
            cert = tls.getpeercert()
        names = {
            name.lower().rstrip(".")
            for kind, name in cert.get("subjectAltName", ())
            if kind == "DNS"
            and "*" not in name
            and re.fullmatch(r"[a-zA-Z0-9.-]+", name)
        }
        return sorted(names)[:100], ""
    except (OSError, ValueError) as exc:
        return [], type(exc).__name__


def certificate_career_hosts(host: str, names: list[str]) -> list[str]:
    """Only same-company names may feed this company's result; other SANs remain seeds."""
    return [
        name
        for name in names
        if name != host
        and public_domain(name) == public_domain(host)
        and re.search(r"(?:^|\.)(?:careers?|jobs?|talent|recruitment)\.", name)
    ][:4]


def browser_page(url: str) -> tuple[str, str, list[str], str]:
    """Render one page in an isolated Chromium context with bounded request/time budgets."""
    from playwright.sync_api import sync_playwright

    seen: list[str] = []
    with _BROWSERS, sync_playwright() as pw:
        browser = None
        context = None
        try:
            try:
                browser = pw.chromium.launch(headless=True, timeout=10_000)
            except Exception:  # noqa: BLE001 - desktop Chrome is an explicit fallback runtime
                browser = pw.chromium.launch(
                    channel="chrome", headless=True, timeout=10_000
                )
            context = browser.new_context(
                service_workers="block", accept_downloads=False
            )
            context.set_default_timeout(2000)

            def route(request_route):
                request = request_route.request
                if len(seen) >= 60:
                    request_route.abort()
                    return
                seen.append(request.url)
                if request.method not in {"GET", "HEAD"} or request.resource_type in {
                    "image",
                    "media",
                    "font",
                }:
                    request_route.abort()
                else:
                    request_route.continue_()

            context.route("**/*", route)
            page = context.new_page()
            error = ""
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=12_000)
                if response and response.status >= 400:
                    error = f"http{response.status}"
                page.wait_for_timeout(1500)
            except Exception as exc:  # noqa: BLE001 - partially rendered DOM remains evidence
                error = type(exc).__name__
            if len(seen) >= 60:
                error = error or "request-budget"
            return page.content()[:900_000], page.url, seen, error
        except Exception as exc:  # noqa: BLE001 - a missing browser must be retryable
            return "", url, [], type(exc).__name__
        finally:
            if context:
                with suppress(Exception):
                    context.unroute_all(behavior="wait")
                    context.close()
            if browser:
                with suppress(Exception):
                    browser.close()


def api_signatures(
    host: str, page: str, get, post
) -> tuple[list[tuple[str, str, str]], list[str]]:
    """At most four read-only listing calls, stopping on a schema-confirmed provider.

    Returns (ats, scraper-tenant, endpoint) evidence. Phenom/Zwayam request payloads come from
    the actual scrapers, so the discovery code cannot silently drift to a different API dialect.
    """
    from headstart.scrapers.phenom import PhenomScraper
    from headstart.scrapers.zwayam import body_error_code, search_request

    errors = []
    endpoint = f"https://{host}/widgets"
    data, error = post(
        endpoint,
        {"Content-Type": "application/json"},
        PhenomScraper(host)._search_payload(0, 1),
    )
    if error:
        errors.append(error)
    refine = data.get("refineSearch") if isinstance(data, dict) else None
    if (
        isinstance(refine, dict)
        and isinstance(refine.get("totalHits"), int)
        and isinstance(refine.get("data"), dict)
        and isinstance(refine["data"].get("jobs"), list)
    ):
        return [("phenom", host, endpoint)], errors

    endpoint, headers, body = search_request(host)
    data, error = post(endpoint, headers, body)
    if error:
        errors.append(error)
    listing = data.get("data") if isinstance(data, dict) else None
    if (
        isinstance(data, dict)
        and body_error_code(data) is None
        and isinstance(listing, dict)
        and isinstance(listing.get("totalCount"), int)
        and isinstance(listing.get("data"), list)
    ):
        return [("zwayam", host, endpoint)], errors

    group = re.search(r'_EF_GROUP_ID\s*=\s*["\']([^"\']+)', page)
    domain = group.group(1) if group else public_domain(host)
    for path in ("/api/pcsx/search", "/api/apply/v2/jobs"):
        endpoint = f"https://{host}{path}?domain={quote(domain, safe='')}&start=0&num=1"
        raw, _final, error = get(endpoint)
        if error:
            errors.append(error)
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        listing = data.get("data", data) if isinstance(data, dict) else None
        if (
            isinstance(listing, dict)
            and isinstance(listing.get("positions"), list)
            and isinstance(listing.get("count"), int)
            and domain != "eightfold.ai"
        ):
            return [("eightfold", host, endpoint)], errors
    return [], errors
