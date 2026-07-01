#!/usr/bin/env python3
"""Recon for the jwc20 'authenticated GraphQL' Wellfound method.

jwc20/wellfound-scraper logs in with a real account, then runs an in-page XHR POST to
wellfound.com/graphql (operation JobSearchResultsX) which inherits the session cookies and
same-origin trust. Their persisted operationId + tag IDs are from March 2024 and almost
certainly stale now, so this script logs in (via pydoll, on WARP) and *intercepts the live
/graphql traffic* to capture the current operationName -> operationId + variable shape.

When the login page is challenged on WARP it runs the DataDome slider solver
(datadome_slider.py) before filling the form. Outputs land in
experiment/wellfound-datadome/artifacts/ (graphql-ops.json, login-page.html).
Credentials come from .env (gitignored): EMAIL, PASSWORD. Never printed, never committed.

Run:  python scripts/scrape/wellfound_recon.py
"""

import asyncio
import json
import urllib.request
from pathlib import Path

from pydoll.browser import Chrome
from pydoll.browser.options import ChromiumOptions

from datadome_slider import solve_slider  # same dir; sys.path[0] when run as a script

ROOT = Path(__file__).resolve().parent.parent.parent
EXP = ROOT / "experiment" / "wellfound-datadome" / "artifacts"
OPS_OUT = EXP / "graphql-ops.json"
HTML_OUT = EXP / "login-page.html"


def load_env() -> dict:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def warp_on() -> bool:
    """Standing rule: only ever request Wellfound through Cloudflare WARP."""
    try:
        with urllib.request.urlopen(
            "https://www.cloudflare.com/cdn-cgi/trace", timeout=10
        ) as r:
            return "warp=on" in r.read().decode("utf-8", "replace")
    except Exception:
        return False


def _blocked(src: str) -> bool:
    return "captcha-delivery.com" in src or "Just a moment" in src


async def login(tab, browser, email: str, password: str) -> tuple[bool, str]:
    await tab.go_to("https://wellfound.com/login")
    el = None
    solve_calls = 0
    for _ in range(20):  # ~40s for DataDome to clear + form to render
        await asyncio.sleep(2)
        src = await tab.page_source
        if _blocked(src):
            # The login page itself is DataDome-gated on WARP — try the slider solver.
            if "captcha-delivery.com" in src and solve_calls < 2:
                solve_calls += 1
                print(
                    f"login: DataDome slider detected — solve attempt {solve_calls}...",
                    flush=True,
                )
                await solve_slider(tab, browser, EXP)
            continue
        el = await tab.find(id="user_email", timeout=0, raise_exc=False)
        if el:
            break
    if not el:
        return False, "login form never appeared (challenge unsolved or markup changed)"
    pw = await tab.find(id="user_password", timeout=0, raise_exc=False)
    btn = await tab.find(name="commit", timeout=0, raise_exc=False)
    if not (pw and btn):
        return False, f"missing fields (pw={bool(pw)} btn={bool(btn)})"
    await el.insert_text(email)
    await pw.insert_text(password)
    await btn.click()
    await asyncio.sleep(7)
    return True, "submitted"


async def main() -> int:
    if not warp_on():
        print(
            "ABORT: WARP is not on. Standing rule: never hit Wellfound on the residential IP.",
            flush=True,
        )
        return 2
    print("WARP on — proceeding.", flush=True)
    EXP.mkdir(parents=True, exist_ok=True)
    env = load_env()
    email, password = env.get("EMAIL"), env.get("PASSWORD")
    if not (email and password):
        print("ABORT: EMAIL/PASSWORD missing from .env", flush=True)
        return 2

    captured = []

    async def on_req(event):
        try:
            p = event.get("params", {})
            req = p.get("request", {})
            url = req.get("url", "")
            if "/graphql" not in url:
                return
            body = req.get("postData", "") or ""
            op = opid = variables = None
            try:
                j = json.loads(body)
                op = j.get("operationName")
                opid = j.get("extensions", {}).get("operationId")
                variables = j.get("variables")
            except Exception:
                pass
            hdrs = {k.lower() for k in req.get("headers", {})}
            captured.append(
                {
                    "operationName": op,
                    "operationId": opid,
                    "has_apollo_signature": "x-apollo-signature" in hdrs,
                    "has_wf_cfp": "x-wf-cfp" in hdrs,
                    "variables": variables,
                    "url": url,
                }
            )
        except Exception:
            pass

    opts = ChromiumOptions()
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--window-size=1400,1000")
    async with Chrome(options=opts) as browser:
        tab = await browser.start()
        try:
            await tab.enable_auto_solve_cloudflare_captcha()
        except Exception:
            pass
        await tab.enable_network_events()
        await tab.on("Network.requestWillBeSent", on_req)

        ok, msg = await login(tab, browser, email, password)
        print(f"login: {ok} ({msg})", flush=True)

        # Verify + provoke the job-search GraphQL op.
        await tab.go_to("https://wellfound.com/jobs")
        await asyncio.sleep(6)
        jobs_src = await tab.page_source
        HTML_OUT.write_text(jobs_src, encoding="utf-8")
        logged_in = ("/login" not in (await tab.current_url)) and not _blocked(jobs_src)
        print(
            f"on /jobs: url-ok={logged_in} blocked={_blocked(jobs_src)} "
            f"JobSearchPage_marker={'JobSearchPage' in jobs_src}",
            flush=True,
        )

        await tab.go_to("https://wellfound.com/role/l/software-engineer/india")
        await asyncio.sleep(6)

        # De-dupe captured ops by (operationName, operationId).
        seen, uniq = set(), []
        for c in captured:
            key = (c["operationName"], c["operationId"])
            if key in seen:
                continue
            seen.add(key)
            uniq.append(c)
        OPS_OUT.write_text(json.dumps(uniq, indent=2), encoding="utf-8")

        print(
            f"\ncaptured {len(captured)} /graphql calls, {len(uniq)} unique ops:",
            flush=True,
        )
        for c in uniq:
            print(
                f"  {c['operationName']:<28} id={c['operationId']}  "
                f"sig={c['has_apollo_signature']} cfp={c['has_wf_cfp']}",
                flush=True,
            )
        print(f"\nfull detail -> {OPS_OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
