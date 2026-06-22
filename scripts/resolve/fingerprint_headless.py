#!/usr/bin/env python3
"""Headless fingerprinter — catches ATS boards that only appear after JS runs.

`scripts/resolve/fingerprint.py` reads static HTML + same-origin JS bundles. This one drives real
(headless) Chrome via pydoll — CDP-direct, no WebDriver, so it's lighter/faster than Playwright
and uses the system Chrome instead of a bundled browser — and fingerprints the *rendered DOM*
plus the page's runtime network requests. That catches boards a single-page app injects into
the DOM or fetches at load time, which the static pass can't see.

Reuses the detect() signature registry from fingerprint.py. One browser; tabs run concurrently;
each tab early-exits on the first ATS hit. The rendered DOM is the primary signal (an injected
embed shows up there); the network-request URLs are a secondary signal (a direct runtime XHR to
an ATS host). Pages that contact no known ATS host are genuinely in-house — not a render gap.

Usage:  python scripts/resolve/fingerprint_headless.py [n]   # first n companies from seed_india.csv
"""
import asyncio
import csv
import re
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fingerprint import detect, SEED  # noqa: E402

from pydoll.browser import Chrome  # noqa: E402
from pydoll.browser.options import ChromiumOptions  # noqa: E402

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
CONCURRENCY = 4
NAV_TIMEOUT = 22
SETTLE = 4  # seconds to let late XHRs fire after load


def opts():
    o = ChromiumOptions()
    o.binary_location = CHROME
    o.headless = True
    for a in ("--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
              "--disable-blink-features=AutomationControlled", "--window-size=1280,900"):
        o.add_argument(a)
    return o


def net_urls(logs):
    out = []
    for e in logs:
        try:
            out.append(e["params"]["request"]["url"])
        except Exception:
            pass
    return out


def careers_links(dom, base):
    seen, out = set(), []
    for h in re.findall(r'href=["\']([^"\']+)["\']', dom, re.I):
        if re.search(r'career|/jobs?(?:[/"\'?]|$)|join-us|work-with|life-at|hiring', h, re.I):
            u = urllib.parse.urljoin(base, h)
            if u not in seen:
                seen.add(u)
                out.append(u)
            if len(out) >= 2:
                break
    return out


async def scan_page(tab, url):
    try:
        await tab.go_to(url, timeout=NAV_TIMEOUT)
    except Exception:
        pass
    await asyncio.sleep(SETTLE)
    try:
        dom = await tab.page_source
    except Exception:
        dom = ""
    try:
        logs = await tab.get_network_logs()
    except Exception:
        logs = []
    return detect(dom) | detect("\n".join(net_urls(logs))), dom


async def fingerprint_company(browser, sem, row):
    name, domain = row["name"], row["domain"]
    async with sem:
        tab = await browser.new_tab()
        try:
            await tab.enable_network_events()
        except Exception:
            pass
        try:
            base = f"https://{domain}/"
            hits, home_dom, tried = set(), "", set()
            for i, u in enumerate((base, f"https://{domain}/careers", f"https://{domain}/jobs")):
                if u in tried:
                    continue
                tried.add(u)
                h, dom = await scan_page(tab, u)
                if i == 0:
                    home_dom = dom
                hits |= h
                if hits:
                    break
            if not hits and home_dom:
                for u in careers_links(home_dom, base):
                    if u in tried:
                        continue
                    tried.add(u)
                    h, _ = await scan_page(tab, u)
                    hits |= h
                    if hits:
                        break
            return name, domain, hits
        finally:
            try:
                await tab.close()
            except Exception:
                pass


async def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 18
    rows = list(csv.DictReader(SEED.open(encoding="utf-8")))[:n]
    sem = asyncio.Semaphore(CONCURRENCY)
    async with Chrome(options=opts()) as browser:
        await browser.start()
        results = await asyncio.gather(
            *[fingerprint_company(browser, sem, r) for r in rows])
    hit = 0
    for name, domain, hits in results:
        if hits:
            hit += 1
            print(f"  {name} ({domain}): "
                  + ", ".join(f"{a}:{t}" for a, t in sorted(hits)), flush=True)
        else:
            print(f"  {name} ({domain}): -", flush=True)
    print(f"\n{hit}/{len(results)} companies fingerprinted (headless)", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
