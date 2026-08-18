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

Writes the same name,domain,hits,status schema as fingerprint.py, resumable by domain, so a
crashed browser pass costs only its in-flight companies and the output can be fed straight to
scripts/merge/merge_fingerprint_into_tenants.py --src.

Usage:  python scripts/resolve/fingerprint_headless.py [n] [--seed CSV] [--out CSV]
        python scripts/resolve/fingerprint_headless.py --seed config/seed_global.csv
Set HEADSTART_CHROME if Chrome isn't in a standard location for your platform.
"""

import argparse
import asyncio
import os
import re
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fingerprint import SEED, detect, load_seed, open_results
from pydoll.browser import Chrome
from pydoll.browser.options import ChromiumOptions

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUT = ROOT / "data" / "resolve" / "fingerprint_headless_results.csv"
CONCURRENCY = 10
NAV_TIMEOUT = 22
SETTLE = 4  # seconds to let late XHRs fire after load
# Where Chrome lives, per platform. This was a single hardcoded Windows path, which made the
# whole headless pass unrunnable anywhere else — and the headless pass is the half that matters,
# since ~79% of fingerprinted hosts are opaque to no-JS curl. Override with HEADSTART_CHROME.
CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)


def chrome_binary() -> str:
    env = os.environ.get("HEADSTART_CHROME")
    if env:
        return env
    for p in CHROME_CANDIDATES:
        if Path(p).exists():
            return p
    raise SystemExit(
        "no Chrome/Chromium found in the usual places; set HEADSTART_CHROME to its path"
    )


def opts():
    o = ChromiumOptions()
    o.binary_location = chrome_binary()
    o.headless = True
    for a in (
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
        "--window-size=1280,900",
    ):
        o.add_argument(a)
    return o


def net_urls(logs):
    out = []
    for e in logs:
        try:
            out.append(e["params"]["request"]["url"])
        except Exception:  # noqa: BLE001, S110
            pass
    return out


def careers_links(dom, base):
    seen, out = set(), []
    for h in re.findall(r'href=["\']([^"\']+)["\']', dom, re.IGNORECASE):
        if re.search(
            r'career|/jobs?(?:[/"\'?]|$)|join-us|work-with|life-at|hiring',
            h,
            re.IGNORECASE,
        ):
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
    except Exception:  # noqa: BLE001, S110
        pass
    await asyncio.sleep(SETTLE)
    try:
        dom = await tab.page_source
    except Exception:  # noqa: BLE001
        dom = ""
    try:
        logs = await tab.get_network_logs()
    except Exception:  # noqa: BLE001
        logs = []
    return detect(dom) | detect("\n".join(net_urls(logs))), dom


async def fingerprint_company(browser, sem, row):
    name, domain = row["name"], row["domain"]
    async with sem:
        tab = await browser.new_tab()
        try:
            await tab.enable_network_events()
        except Exception:  # noqa: BLE001, S110
            pass
        try:
            base = f"https://{domain}/"
            hits, home_dom, tried = set(), "", set()
            seen_dom = False
            for i, u in enumerate(
                (base, f"https://{domain}/careers", f"https://{domain}/jobs")
            ):
                if u in tried:
                    continue
                tried.add(u)
                h, dom = await scan_page(tab, u)
                seen_dom = seen_dom or bool(dom)
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
            return name, domain, hits, "ok" if seen_dom else "unreachable"
        finally:
            try:
                await tab.close()
            except Exception:  # noqa: BLE001, S110
                pass


async def main():
    ap = argparse.ArgumentParser(
        description="Headless (JS-rendered) ATS fingerprinter."
    )
    ap.add_argument(
        "n", nargs="?", type=int, help="only the first n seed rows (default: all)"
    )
    ap.add_argument(
        "--seed", type=Path, default=SEED, help="seed CSV with name,domain columns"
    )
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="results CSV")
    ap.add_argument(
        "--restart", action="store_true", help="truncate the output instead of resuming"
    )
    args = ap.parse_args()

    rows = load_seed(args.seed, args.n)
    cw, cf, already = open_results(args.out, args.restart)
    pending = [r for r in rows if r["domain"] not in already]
    print(
        f"{len(rows)} seed rows | {len(already)} already done | {len(pending)} to scan",
        flush=True,
    )
    if not pending:
        cf.close()
        return

    sem = asyncio.Semaphore(CONCURRENCY)
    hit = done = 0
    async with Chrome(options=opts()) as browser:
        await browser.start()
        # as_completed + flush per company: a browser pass is slow and crash-prone, so results
        # must land on disk as they happen rather than in one gather at the end.
        for fut in asyncio.as_completed(
            [fingerprint_company(browser, sem, r) for r in pending]
        ):
            name, domain, hits, status = await fut
            done += 1
            cw.writerow(
                [name, domain, ";".join(f"{a}:{t}" for a, t in sorted(hits)), status]
            )
            cf.flush()
            if hits:
                hit += 1
            shown = ", ".join(f"{a}:{t}" for a, t in sorted(hits)) if hits else "-"
            print(f"  [{done}/{len(pending)}] {name} ({domain}): {shown}", flush=True)
    cf.close()
    print(
        f"\n{hit}/{len(pending)} companies fingerprinted (headless) -> {args.out}",
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
