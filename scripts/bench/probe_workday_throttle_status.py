"""Which status does Workday's per-tenant throttle settle as, from *this* vantage?

``docs/workday/400-is-a-throttle-not-a-bad-request.md`` reproduced the throttle from a laptop and
falsified every malformed-request reading of the 400. One thing it could not settle from there:
**production settles 400 where the laptop settles 429** (run 33590621111 recorded 36,550 x 400
against ~600 x 429, both statuses on the same Boards). The untested variable is the egress itself,
so this probe is built to run from inside GitHub Actions — the vantage production actually uses —
and its whole job is to name the status each route settles on.

Five arms, each printed as it finishes:

``where``    the vantage: exit IP, Cloudflare colo and ``warp=on/off``, direct and via the spare
             egress, so a status difference can be attributed to a route rather than guessed at.
``control``  the walk over a Board production loses *zero* details on, run first while this
             address is still clean. The discriminator between "this vantage is penalised" and
             "this tenant is configured differently": a 400-free control on a runner that 400s
             elsewhere rules the vantage out.
``direct``   the same sustained walk over the 400-heavy Board — the arm that reproduces the
             throttle.
``browser``  a real Chrome from the *same* address, straight after ``direct`` tripped the throttle.
             Passing where curl_cffi is refused would make the penalty client-shaped, not IP-shaped.
``warp``     the same walk through the spare egress (ADR-0063) — the route production's traffic
             takes once a Board has walled, and the one ADR-0102 newly opens for a 400.

Every arm keeps the first response of each distinct non-200 status **in full** — headers and body —
because that is the evidence naming who answered. A Cloudflare HTML block page and a Workday JSON
error carry the same status code and are completely different findings.

The walks deliberately bypass the retry ladder (``retry_on=frozenset()``): the question is what the
origin says first, and a ladder would report what it says third.

Run:
  .venv/bin/python -u scripts/bench/probe_workday_throttle_status.py --out probe.json
  # or, for the runner vantage: .github/workflows/probe-workday-400.yml (workflow_dispatch)
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from headstart import http, spare_egress
from headstart.scrapers.base import USER_AGENT
from headstart.scrapers.workday import WorkdayScraper

#: 95% of this Board's details settled 400 in production, run after run.
SUSPECT = "https://ghr.wd1.myworkdayjobs.com/us-emplsv"
#: 0% — larger, same ATS, same runs, same shard pool. The control the suspect is read against.
CONTROL = "https://aah.wd5.myworkdayjobs.com/External"

WIDTH = 25  # `workday._DETAIL_STREAMS` — production's own detail fan-out
_BLOCK = 100  # the status mix is reported per this many settled requests
_HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}
_BODY_SAMPLE = 800  # chars of a non-200 body kept as evidence


def _proxies(proxy: str | None) -> dict:
    return {"proxies": {"http": proxy, "https": proxy}} if proxy else {}


def _trace(proxy: str | None) -> dict[str, str]:
    """What this route looks like from outside: exit IP, Cloudflare colo, ``warp=on/off``.

    ``warp`` is the one that matters for the spare-egress arm — it is Cloudflare's own answer to
    "is this request really riding WARP", which no amount of reading ``warp-cli status`` proves.
    """
    out: dict[str, str] = {}
    try:
        response = http.fetch(
            "GET",
            "https://www.cloudflare.com/cdn-cgi/trace",
            timeout=20,
            retry_on=frozenset(),
            headers=_HEADERS,
            **_proxies(proxy),
        )
        fields = dict(
            line.split("=", 1) for line in response.text.splitlines() if "=" in line
        )
        out = {
            k: fields[k] for k in ("ip", "loc", "colo", "warp", "http") if k in fields
        }
    except Exception as exc:  # noqa: BLE001 - a vantage we cannot read is a result, not a crash
        return {"error": f"{type(exc).__name__}: {exc}"}
    try:  # best effort: the ASN is what says "Azure" or "Cloudflare" out loud
        info = http.fetch(
            "GET",
            f"https://ipinfo.io/{out.get('ip', '')}/json",
            timeout=15,
            retry_on=frozenset(),
            **_proxies(proxy),
        ).json()
        out["org"] = info.get("org", "?")
    except Exception:  # noqa: BLE001, S110 - the IP alone still identifies the network
        pass
    return out


def harvest(scraper: WorkdayScraper, want: int) -> list[str]:
    """``externalPath`` for up to ``want`` postings, off the plain unfaceted listing."""
    paths: list[str] = []
    offset = 0
    while len(paths) < want:
        page = scraper._post({}, offset=offset)
        postings = (page or {}).get("jobPostings") or []
        if not postings:
            break
        paths += [p["externalPath"] for p in postings if p.get("externalPath")]
        offset += len(postings)
    return paths[:want]


def walk(
    label: str, scraper: WorkdayScraper, paths: list[str], proxy: str | None
) -> dict:
    """Walk every path at production width and report what the origin settled on.

    Returns the status histogram, the request index of the first non-200, the mix per block of
    ``_BLOCK`` (a throttle trips partway, so the shape over time is the finding, not the total),
    and one full response per distinct non-200 status.
    """
    statuses: collections.Counter[str] = collections.Counter()
    blocks: dict[int, collections.Counter[str]] = collections.defaultdict(
        collections.Counter
    )
    samples: dict[str, dict] = {}
    first_bad: dict | None = None
    seq = 0
    lock = asyncio.Lock()

    async def run() -> None:
        nonlocal first_bad, seq
        from curl_cffi.requests import (
            AsyncSession,
        )  # what BaseScraper._gather_async uses

        gate = asyncio.Semaphore(WIDTH)
        async with AsyncSession(impersonate="chrome") as session:

            async def one(path: str) -> None:
                nonlocal first_bad, seq
                async with gate:
                    try:
                        response = await http.fetch_async(
                            session,
                            "GET",
                            scraper._detail_url(path),
                            timeout=30,
                            retry_on=frozenset(),  # the origin's FIRST answer, not its third
                            headers=_HEADERS,
                            **_proxies(proxy),
                        )
                        code = str(response.status_code)
                        headers, body = dict(response.headers), response.text
                    except Exception as exc:  # noqa: BLE001 - classifying it is the point
                        code, headers, body = type(exc).__name__, {}, str(exc)
                    async with lock:
                        seq += 1
                        index = seq
                        statuses[code] += 1
                        blocks[index // _BLOCK][code] += 1
                        if code == "200" or code in samples:
                            return
                        samples[code] = {
                            "at_request": index,
                            "headers": headers,
                            "body": body[:_BODY_SAMPLE],
                        }
                        if first_bad is None:
                            first_bad = {"at_request": index, "status": code}
                        print(
                            f"    first {code} at request #{index}\n"
                            f"      server={headers.get('server')!r} "
                            f"cf-ray={headers.get('cf-ray')!r} "
                            f"cf-mitigated={headers.get('cf-mitigated')!r} "
                            f"retry-after={headers.get('retry-after')!r}\n"
                            f"      body: {body[:200]!r}",
                            flush=True,
                        )

            await asyncio.gather(*(one(p) for p in paths))

    print(f"  {label}: walking {len(paths)} details at width {WIDTH}", flush=True)
    started = time.monotonic()
    asyncio.run(run())
    elapsed = time.monotonic() - started
    print(
        f"  {label}: {dict(statuses)} in {elapsed:.0f}s "
        f"({len(paths) / max(elapsed, 1e-9):.1f} req/s)",
        flush=True,
    )
    for block in sorted(blocks):
        print(f"      {block * _BLOCK:>5}+ {dict(blocks[block])}", flush=True)
    return {
        "statuses": dict(statuses),
        "first_bad": first_bad,
        "blocks": {str(b): dict(c) for b, c in sorted(blocks.items())},
        "samples": samples,
        "seconds": round(elapsed, 1),
    }


def browser_walk(scraper: WorkdayScraper, paths: list[str]) -> dict:
    """The same detail URLs through a real Chrome on this machine's direct route.

    Sequential and short on purpose: this arm is a pass/fail on whether the origin's refusal
    survives a genuine browser, not a second rate measurement.
    """
    from headstart import browser_http

    company, instance, site = scraper._parts()
    statuses: collections.Counter[str] = collections.Counter()
    samples: dict[str, dict] = {}
    started = time.monotonic()
    with browser_http.origin(
        f"https://{company}.{instance}.myworkdayjobs.com/{site}"
    ) as page:
        for index, path in enumerate(paths, 1):
            try:
                page.get_json(f"/wday/cxs/{company}/{site}{path}")
                statuses["200"] += 1
            except browser_http.BrowserHTTPError as exc:
                code = str(exc.status_code)
                statuses[code] += 1
                samples.setdefault(
                    code, {"at_request": index, "body": exc.body[:_BODY_SAMPLE]}
                )
            except Exception as exc:  # noqa: BLE001 - classifying it is the point
                statuses[type(exc).__name__] += 1
                samples.setdefault(
                    type(exc).__name__,
                    {"at_request": index, "body": str(exc)[:_BODY_SAMPLE]},
                )
            if index % 10 == 0:
                print(f"    ...{index}/{len(paths)} {dict(statuses)}", flush=True)
    elapsed = time.monotonic() - started
    print(f"  browser: {dict(statuses)} in {elapsed:.0f}s", flush=True)
    return {
        "statuses": dict(statuses),
        "samples": samples,
        "seconds": round(elapsed, 1),
    }


def board(slug: str) -> WorkdayScraper:
    scraper = WorkdayScraper(slug, "probe")
    scraper._resolve_instance()
    return scraper


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suspect", default=SUSPECT, help="the 400-heavy Board")
    parser.add_argument(
        "--control", default=CONTROL, help="a Board production never 400s"
    )
    parser.add_argument(
        "--n", type=int, default=800, help="details per walk (default 800)"
    )
    parser.add_argument(
        "--browser-n", type=int, default=40, help="details through Chrome"
    )
    parser.add_argument(
        "--arms",
        default="where,control,direct,browser,warp",
        help="comma-separated subset, run in this fixed order",
    )
    parser.add_argument("--out", type=Path, help="write the full result JSON here")
    args = parser.parse_args()
    arms = {a.strip() for a in args.arms.split(",") if a.strip()}

    result: dict = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "suspect": args.suspect,
        "control": args.control,
        "n": args.n,
        "width": WIDTH,
        "arms": {},
    }

    def save() -> None:
        if args.out:
            args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    if "where" in arms:
        print("== where ==", flush=True)
        direct = _trace(None)
        print(f"  direct: {direct}", flush=True)
        proxy = spare_egress.proxy_url()
        via_warp = _trace(proxy) if proxy else {"error": "no spare egress on this host"}
        print(f"  warp  : {via_warp}  (proxy={proxy})", flush=True)
        result["arms"]["where"] = {"direct": direct, "warp": via_warp, "proxy": proxy}
        save()

    # Before the suspect, deliberately: a control walked *after* 1,600 refused requests from
    # this address would inherit whatever reputation those earned, which is the confound it
    # exists to remove.
    if "control" in arms:
        control = board(args.control)
        print(f"\n== {control.board_key()} (control) ==", flush=True)
        control_paths = harvest(control, args.n)
        print(f"  harvested {len(control_paths)} externalPaths", flush=True)
        if control_paths:
            result["arms"]["control"] = walk("control", control, control_paths, None)
        save()

    scraper = board(args.suspect)
    paths: list[str] = []
    if arms & {"direct", "warp", "browser"}:
        print(f"\n== {scraper.board_key()} ==", flush=True)
        paths = harvest(scraper, args.n)
        print(f"  harvested {len(paths)} externalPaths", flush=True)
        if not paths:
            print(
                "  no paths — the listing itself is refused; nothing to walk",
                flush=True,
            )

    if "direct" in arms and paths:
        result["arms"]["direct"] = walk("direct", scraper, paths, None)
        save()

    # Straight after `direct`, so it asks its question of an origin that is already refusing us.
    if "browser" in arms and paths:
        print("\n== browser (same address, real Chrome) ==", flush=True)
        try:
            result["arms"]["browser"] = browser_walk(scraper, paths[: args.browser_n])
        except Exception as exc:  # noqa: BLE001 - a browser that won't start is a result
            print(f"  browser unavailable: {type(exc).__name__}: {exc}", flush=True)
            result["arms"]["browser"] = {"error": f"{type(exc).__name__}: {exc}"}
        save()

    if "warp" in arms and paths:
        print("\n== via the spare egress ==", flush=True)
        proxy = spare_egress.proxy_url()
        if proxy is None:
            print("  no spare egress on this host — skipped", flush=True)
            result["arms"]["warp"] = {"error": "no spare egress"}
        else:
            result["arms"]["warp"] = walk("warp", scraper, paths, proxy)
        save()

    if args.out:
        print(f"\nwrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
