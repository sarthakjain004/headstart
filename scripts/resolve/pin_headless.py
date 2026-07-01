#!/usr/bin/env python3
"""Pin the ATS of JS-SPA careers pages by rendering them and capturing runtime network calls.

curl_cffi fetches HTML but doesn't run JS, so an SPA that fetches its board via XHR at load
(boards-api.greenhouse.io, api.lever.co, a Workday cxs endpoint, *.darwinbox.in, ...) shows no
ATS host in the static HTML. This drives real (headless) Chrome via pydoll, enables network
capture, navigates homepage + careers page, and greps the captured REQUEST URLs (and rendered
DOM) for any known ATS host — the board call is caught the instant the SPA makes it.

Usage:  python scripts/resolve/pin_headless.py   # edit TARGETS below
"""

import asyncio
import re

from pydoll.browser import Chrome
from pydoll.browser.options import ChromiumOptions

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SETTLE = 6  # seconds to let late XHRs fire after load

ATS = re.compile(
    r"[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com|boards(?:-api)?\.greenhouse\.io/[\w/?=-]*"
    r"|job-boards\.greenhouse\.io/[\w-]+|api\.lever\.co/v0/postings/[\w-]+|jobs\.lever\.co/[\w-]+"
    r"|api\.ashbyhq\.com/posting-api/job-board/[\w-]+|jobs\.ashbyhq\.com/[\w-]+"
    r"|[a-z0-9-]+\.darwinbox\.(?:in|com)|[a-z0-9-]+\.keka\.com|[a-z0-9-]+\.zohorecruit\.(?:com|in)"
    r"|apply\.workable\.com/[\w-]+|[a-z0-9-]+\.recruitee\.com|[a-z0-9-]+\.sensehq\.com"
    r"|[a-z0-9-]+\.hire\.trakstar\.com|[a-z0-9-]+\.skillate\.com"
    r"|smartrecruiters\.com/[\w-]+|[a-z0-9-]+\.eightfold\.ai"
    r"|careers-[a-z0-9-]+\.icims\.com|[a-z0-9-]+\.zwayam\.com|[a-z0-9-]+\.turbohire\.co",
    re.I,
)

TARGETS = [
    ("Hasura", "hasura.io", ["/careers", "/about/careers", "/jobs"]),
    ("Exotel", "exotel.com", ["/careers", "/about/careers", "/company/careers"]),
    (
        "Icertis",
        "icertis.com",
        ["/company/careers", "/careers", "/company/life-at-icertis"],
    ),
    ("SirionLabs", "sirion.ai", ["/careers", "/company/careers", "/about/careers"]),
    ("Vymo", "vymo.com", ["/careers", "/company/careers", "/about/careers"]),
    ("Facilio", "facilio.com", ["/careers", "/company/careers", "/about/careers"]),
]


def opts():
    o = ChromiumOptions()
    o.binary_location = CHROME
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
        except Exception:
            pass
    return out


async def scan(browser, name, domain, paths):
    tab = await browser.new_tab()
    try:
        await tab.enable_network_events()
    except Exception:
        pass
    found = set()
    for url in [f"https://{domain}/"] + [f"https://{domain}{p}" for p in paths]:
        try:
            await tab.go_to(url, timeout=30)
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
        blob = dom + "\n" + "\n".join(net_urls(logs))
        for m in ATS.finditer(blob):
            found.add(m.group(0).lower())
        if found:
            break
    try:
        await tab.close()
    except Exception:
        pass
    return name, domain, sorted(found)[:5]


async def main():
    # fresh browser per company: reusing one across many long renders made it unstable (tab
    # creation timed out after ~6), so isolate each render in its own short-lived browser.
    for name, domain, paths in TARGETS:
        try:
            async with Chrome(options=opts()) as browser:
                await browser.start()
                n, d, hits = await scan(browser, name, domain, paths)
        except Exception as e:
            n, d, hits = name, domain, []
            print(f"  {name:12} ({domain}): ERR {type(e).__name__}", flush=True)
            continue
        print(
            f"  {n:12} ({d}): " + (", ".join(hits) if hits else "- (no ATS host)"),
            flush=True,
        )


if __name__ == "__main__":
    asyncio.run(main())
