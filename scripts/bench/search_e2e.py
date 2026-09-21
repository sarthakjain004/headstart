#!/usr/bin/env python3
"""Playwright timing from Search click through network phases and visible settled UI."""

from __future__ import annotations

import argparse
import getpass
import json
import statistics
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

CASES = (
    ("browse", "", {}),
    (
        "browse_combined_cold",
        "",
        {"remote": True, "ats": "greenhouse", "maxyears": "5", "seen": "168"},
    ),
    (
        "browse_combined_warm",
        "",
        {"remote": True, "ats": "greenhouse", "maxyears": "5", "seen": "168"},
    ),
    ("semantic", "backend engineer", {}),
    (
        "combined_cold",
        "backend engineer",
        {"remote": True, "ats": "greenhouse", "maxyears": "5", "seen": "168"},
    ),
    (
        "combined_warm_1",
        "frontend engineer",
        {"remote": True, "ats": "greenhouse", "maxyears": "5", "seen": "168"},
    ),
    (
        "combined_warm_2",
        "machine learning infrastructure",
        {"remote": True, "ats": "greenhouse", "maxyears": "5", "seen": "168"},
    ),
    (
        "combined_warm_3",
        "developer tools engineer",
        {"remote": True, "ats": "greenhouse", "maxyears": "5", "seen": "168"},
    ),
)


_OBSERVER = """() => {
  const state = { start: null, sawSkeleton: false, sawSearching: false,
                  rowsMs: null, fullMs: null };
  window.__headstartBench = state;
  document.querySelector('button.go').addEventListener('click', () => {
    performance.clearResourceTimings();
    state.start = performance.now();
  }, { capture:true, once:true });
  const check = () => {
    if (state.start === null) return;
    const results = document.getElementById('results');
    const count = document.getElementById('n');
    state.sawSkeleton ||= !!results.querySelector('.skel');
    state.sawSearching ||= /searching|loading/.test(count.textContent);
    if (state.rowsMs === null && state.sawSkeleton &&
        !results.querySelector('.skel') && results.querySelector('.card, .empty')) {
      state.rowsMs = performance.now() - state.start;
    }
    if (state.fullMs === null && state.sawSearching &&
        (/of .* matching your filters/.test(count.textContent) || count.textContent === '0 results')) {
      state.fullMs = performance.now() - state.start;
    }
  };
  const observer = new MutationObserver(check);
  observer.observe(document.getElementById('results'), { childList: true, subtree: true });
  observer.observe(document.getElementById('n'), { childList: true, subtree: true, characterData: true });
  state.observer = observer;
}"""


def _resource(entry: dict | None, start: float) -> dict | None:
    if not entry:
        return None
    return {
        "dispatch_ms": round(entry["fetchStart"] - start, 2),
        "ttfb_ms": round(entry["responseStart"] - entry["requestStart"], 2),
        "download_ms": round(entry["responseEnd"] - entry["responseStart"], 2),
        "end_to_end_ms": round(entry["responseEnd"] - entry["fetchStart"], 2),
        "response_end_from_action_ms": round(entry["responseEnd"] - start, 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", required=True)
    ap.add_argument("--authenticated", action="store_true")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        if args.authenticated:
            cookie = getpass.getpass("Session cookie: ")
            host = urlsplit(args.base).hostname
            context.add_cookies(
                [
                    {
                        "name": "session",
                        "value": cookie,
                        "domain": host,
                        "path": "/",
                        "secure": args.base.startswith("https://"),
                    }
                ]
            )
        page = context.new_page()
        page.goto(args.base, wait_until="domcontentloaded", timeout=120_000)
        page.wait_for_function(
            "!document.querySelector('#results .skel')", timeout=120_000
        )

        rows = []
        with dest.open("w", encoding="utf-8") as out:
            for name, query, filters in CASES:
                page.locator("#q").fill(query)
                page.evaluate(
                    """filters => {
                      document.getElementById('remote').checked = !!filters.remote;
                      document.getElementById('ats').value = filters.ats || '';
                      document.getElementById('maxyears').value = filters.maxyears || '';
                      const seen = document.getElementById('seen');
                      if (seen) seen.value = filters.seen || '';
                    }""",
                    filters,
                )
                page.evaluate(_OBSERVER)
                page.locator("button.go").first.click()
                page.wait_for_function(
                    "window.__headstartBench.rowsMs !== null", timeout=120_000
                )
                page.wait_for_function(
                    "window.__headstartBench.fullMs !== null", timeout=120_000
                )
                measured = page.evaluate(
                    """() => {
                      const state = window.__headstartBench;
                      state.observer.disconnect();
                      const entries = performance.getEntriesByType('resource');
                      const latest = suffix => [...entries].reverse().find(e => new URL(e.name).pathname === suffix);
                      const pick = e => e && ({ fetchStart:e.fetchStart, requestStart:e.requestStart,
                                               responseStart:e.responseStart, responseEnd:e.responseEnd });
                      return { start:state.start, rowsMs:state.rowsMs, fullMs:state.fullMs,
                               search:pick(latest('/search')), facets:pick(latest('/facets')),
                               cards:document.querySelectorAll('#results .card').length };
                    }"""
                )
                row = {
                    "case": name,
                    "action_to_rows_ms": round(measured["rowsMs"], 2),
                    "action_to_settled_ms": round(measured["fullMs"], 2),
                    "cards": measured["cards"],
                    "search": _resource(measured["search"], measured["start"]),
                    "facets": _resource(measured["facets"], measured["start"]),
                }
                rows.append(row)
                out.write(json.dumps(row, sort_keys=True) + "\n")
                out.flush()
                print(json.dumps(row, sort_keys=True), flush=True)
        context.close()
        browser.close()

    warm = [r["action_to_settled_ms"] for r in rows if "warm" in r["case"]]
    if warm:
        print(f"warm settled median={statistics.median(warm):.2f} ms", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
