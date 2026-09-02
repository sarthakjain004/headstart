"""Which status does Workday's per-tenant throttle settle as, from *this* vantage?

``docs/workday/400-is-a-throttle-not-a-bad-request.md`` reproduced the throttle from a laptop and
falsified every malformed-request reading of the 400. One thing it could not settle from there:
**production settles 400 where the laptop settles 429** (run 33590621111 recorded 36,550 x 400
against ~600 x 429, both statuses on the same Boards). The untested variable is the egress itself,
so this probe is built to run from inside GitHub Actions — the vantage production actually uses —
and its whole job is to name the status each route settles on.

Four arms, each printed as it finishes:

``where``    the vantage: exit IP, Cloudflare colo and ``warp=on/off``, direct and via the spare
             egress, so a status difference can be attributed to a route rather than guessed at.
``control``  the walk over a Board production loses *zero* details on, run first while this
             address is still clean. The discriminator between "this vantage is penalised" and
             "this tenant is configured differently": a 400-free control on a runner that 400s
             elsewhere rules the vantage out.
``direct``   the same sustained walk over the 400-heavy Board — the arm that reproduces the
             throttle.
``browser``  each path fetched **twice back to back**, once by curl_cffi and once by a real Chrome
             on the same address. A browser 200 is evidence only if the API client is being
             refused at that same moment, and the doc warns that a short clean run proves nothing
             (thermofisher answered 700 requests before it tripped) — so the pair is its own
             control for timing, and a separate browser-only arm would not have been readable.
``warp``     the same walk through the spare egress (ADR-0063) — the route production's traffic
             takes once a Board has walled, and the one ADR-0102 newly opens for a 400.

Every arm keeps the first response of each distinct non-200 status **in full** — headers and body —
because that is the evidence naming who answered. A Cloudflare HTML block page and a Workday JSON
error carry the same status code and are completely different findings.

Two deliberate choices about what is measured:

- The walks bypass the retry ladder (``retry_on=frozenset()``): the question is what the origin
  says first, and a ladder would report what it says third.
- They also drive their own ``AsyncSession`` rather than :meth:`BaseScraper.fan_out_async`, which
  the neighbouring probes use. That is not an oversight. ``fan_out_async`` resolves its width
  through :func:`spare_egress.stream_width`, which narrows to 12 the moment anything marks
  workday walled — and the listing harvest below *can* mark it, since ``_post`` carries
  ``_egress()``. A width that silently halves partway through is precisely what would make this
  run incomparable to the laptop's, which is the entire point of the exercise, so the width is
  pinned here instead.

Reported figures, and what each is for:

- ``first_refusal`` — the request index where a **refusal** status first appears, comparable to the
  doc's "trips at request" column. Kept apart from ``first_non_200`` so one stray 404 (a posting
  closed between the listing and the detail) cannot be misread as the trip.
- ``after_trip`` — the status mix settled *after* that first refusal. This is the column the doc
  calls comparable across vantages; the whole-walk mix is not, since it dilutes with however long
  the walk ran clean.
- Indices are **completion order**, not launch order: at width 25 the two differ by up to a window.

Run:
  .venv/bin/python -u scripts/bench/probe_workday_throttle_status.py \
      --out experiment/workday-400-root-cause/artifacts/throttle-status-laptop.json
  # or, for the runner vantage: .github/workflows/probe-workday-400.yml (workflow_dispatch),
  # which uploads one JSON + log per replica as a run artifact.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from headstart import http, spare_egress
from headstart.scrapers.base import USER_AGENT
from headstart.scrapers.workday import WorkdayScraper

#: 95% of this Board's details settled 400 in production, run after run.
SUSPECT = "https://ghr.wd1.myworkdayjobs.com/us-emplsv"
#: 0% — larger, same ATS, same runs, same shard pool. The control the suspect is read against.
CONTROL = "https://aah.wd5.myworkdayjobs.com/External"

WIDTH = (
    25  # `workday._DETAIL_STREAMS` — production's own detail fan-out, and the laptop's
)
_BLOCK = 100  # the status mix is reported per this many settled requests
_HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}
_BODY_SAMPLE = 800  # chars of a non-200 body kept as evidence
#: Statuses that mean "this origin is refusing us" rather than "this posting is gone". Only these
#: mark the trip, so a stray 404 mid-walk cannot masquerade as the throttle engaging.
_REFUSALS = frozenset({"400", "403", "429"})


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


def collect_paths(scraper: WorkdayScraper, want: int) -> list[str]:
    """``externalPath`` for up to ``want`` postings, off the plain unfaceted listing.

    Named as its neighbour ``probe_workday_detail.py`` names it. Deliberately *not* ``harvest``:
    ``headstart.harvest`` is the curated-feed entry point, and a near-synonym for a different
    thing is the naming failure CLAUDE.md §3 calls out by name.
    """
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
    label: str,
    scraper: WorkdayScraper,
    paths: list[str],
    proxy: str | None,
    slot: dict,
    save: Callable[[], None],
) -> None:
    """Walk every path at production width and record what the origin settled on, into ``slot``.

    ``slot`` is updated and ``save`` called every :data:`_BLOCK` completions rather than once at
    the end, so a cancelled job (the workflow's 45-minute cap, or a runner reclaimed mid-arm)
    still leaves the measurement it had reached — CLAUDE.md forbids buffering output to the end,
    and an arm that prints nothing for ten minutes and then dies has produced nothing at all.
    """
    statuses: collections.Counter[str] = collections.Counter()
    after_trip: collections.Counter[str] = collections.Counter()
    blocks: dict[int, collections.Counter[str]] = collections.defaultdict(
        collections.Counter
    )
    samples: dict[str, dict] = {}
    first_refusal: dict | None = None
    first_non_200: dict | None = None
    seq = 0
    lock = asyncio.Lock()
    started = time.monotonic()

    def snapshot() -> None:
        slot.update(
            requested=len(paths),
            walked=seq,
            statuses=dict(statuses),
            first_refusal=first_refusal,
            first_non_200=first_non_200,
            after_trip=dict(after_trip),
            blocks={
                f"{b * _BLOCK + 1}-{(b + 1) * _BLOCK}": dict(c)
                for b, c in sorted(blocks.items())
            },
            samples=samples,
            seconds=round(time.monotonic() - started, 1),
        )

    async def run() -> None:
        nonlocal first_refusal, first_non_200, seq
        from curl_cffi.requests import (
            AsyncSession,
        )  # what BaseScraper._gather_async uses

        gate = asyncio.Semaphore(WIDTH)
        async with AsyncSession(impersonate="chrome") as session:

            async def one(path: str) -> None:
                nonlocal first_refusal, first_non_200, seq
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
                        blocks[(index - 1) // _BLOCK][code] += 1
                        if first_refusal is not None:
                            after_trip[code] += 1
                        if code != "200" and code not in samples:
                            samples[code] = {
                                "at_request": index,
                                "headers": headers,
                                "body": body[:_BODY_SAMPLE],
                            }
                            print(
                                f"    first {code} at request #{index}\n"
                                f"      server={headers.get('server')!r} "
                                f"cf-ray={headers.get('cf-ray')!r} "
                                f"cf-mitigated={headers.get('cf-mitigated')!r} "
                                f"retry-after={headers.get('retry-after')!r}\n"
                                f"      body: {body[:200]!r}",
                                flush=True,
                            )
                        if first_non_200 is None and code != "200":
                            first_non_200 = {"at_request": index, "status": code}
                        if first_refusal is None and code in _REFUSALS:
                            first_refusal = {"at_request": index, "status": code}
                        if index % _BLOCK == 0:
                            print(
                                f"    ...{index}/{len(paths)} {dict(statuses)}",
                                flush=True,
                            )
                            snapshot()
                            save()

            await asyncio.gather(*(one(p) for p in paths))

    print(f"  {label}: walking {len(paths)} details at width {WIDTH}", flush=True)
    asyncio.run(run())
    snapshot()
    save()
    elapsed = slot["seconds"]
    print(
        f"  {label}: {dict(statuses)} over {seq}/{len(paths)} in {elapsed:.0f}s "
        f"({seq / max(elapsed, 1e-9):.1f} req/s)",
        flush=True,
    )
    if first_refusal:
        refused = sum(c for k, c in after_trip.items() if k != "200")
        total = sum(after_trip.values())
        print(
            f"    first refusal at #{first_refusal['at_request']} "
            f"({first_refusal['status']}); after it {refused}/{total} refused "
            f"({100 * refused / max(total, 1):.0f}%)",
            flush=True,
        )
    for key, mix in slot["blocks"].items():
        print(f"      {key:>9} {mix}", flush=True)


def paired_browser_walk(
    scraper: WorkdayScraper, paths: list[str], slot: dict, save: Callable[[], None]
) -> None:
    """Each path fetched twice back to back: curl_cffi, then a real Chrome on the same address.

    Paired rather than run as its own arm because a throttle window can lapse between arms — a
    browser 200 means nothing unless the API client is refused *at that moment*. The pair is
    reported as one key (``api 400 -> browser 200``), which is the discriminator itself: that
    combination says the penalty is client-shaped, ``api 400 -> browser 400`` says IP-shaped.
    """
    from headstart import browser_http

    company, instance, site = scraper._parts()
    pairs: collections.Counter[str] = collections.Counter()
    samples: dict[str, dict] = {}
    started = time.monotonic()
    with browser_http.origin(
        f"https://{company}.{instance}.myworkdayjobs.com/{site}"
    ) as page:
        for index, path in enumerate(paths, 1):
            try:
                api = str(
                    http.fetch(
                        "GET",
                        scraper._detail_url(path),
                        timeout=30,
                        retry_on=frozenset(),
                        headers=_HEADERS,
                    ).status_code
                )
            except Exception as exc:  # noqa: BLE001 - classifying it is the point
                api = type(exc).__name__
            try:
                page.get_json(f"/wday/cxs/{company}/{site}{path}")
                browser = "200"
            except browser_http.BrowserHTTPError as exc:
                browser = str(exc.status_code)
                samples.setdefault(
                    browser, {"at_request": index, "body": exc.body[:_BODY_SAMPLE]}
                )
            except Exception as exc:  # noqa: BLE001 - classifying it is the point
                browser = type(exc).__name__
                samples.setdefault(
                    browser, {"at_request": index, "body": str(exc)[:_BODY_SAMPLE]}
                )
            pairs[f"api {api} -> browser {browser}"] += 1
            slot.update(
                requested=len(paths),
                walked=index,
                pairs=dict(pairs),
                samples=samples,
                seconds=round(time.monotonic() - started, 1),
            )
            save()
            if index % 10 == 0:
                print(f"    ...{index}/{len(paths)} {dict(pairs)}", flush=True)
    print(f"  browser: {dict(pairs)} in {slot['seconds']:.0f}s", flush=True)


def resolved_scraper(slug: str) -> WorkdayScraper:
    """A scraper for ``slug`` with its serving instance resolved (a migrated tenant's slug host
    500s; ``_resolve_instance`` finds the one that answers)."""
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
        "--browser-n",
        type=int,
        default=40,
        help="paired api/Chrome fetches (default 40)",
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
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    def slot(name: str) -> dict:
        return result["arms"].setdefault(name, {})

    if "where" in arms:
        print("== where ==", flush=True)
        direct = _trace(None)
        print(f"  direct: {direct}", flush=True)
        proxy = spare_egress.proxy_url()
        via_warp = _trace(proxy) if proxy else {"error": "no spare egress on this host"}
        print(f"  warp  : {via_warp}  (proxy={proxy})", flush=True)
        slot("where").update(direct=direct, warp=via_warp, proxy=proxy)
        save()

    # Before the suspect, deliberately: a control walked *after* several hundred refused requests
    # from this address would inherit whatever reputation those earned, which is the confound it
    # exists to remove.
    if "control" in arms:
        control = resolved_scraper(args.control)
        print(f"\n== {control.board_key()} (control) ==", flush=True)
        control_paths = collect_paths(control, args.n)
        print(f"  harvested {len(control_paths)} externalPaths", flush=True)
        if control_paths:
            walk("control", control, control_paths, None, slot("control"), save)
        else:
            slot("control").update(error="listing returned nothing — cannot walk")
        save()

    scraper = resolved_scraper(args.suspect)
    paths: list[str] = []
    if arms & {"direct", "warp", "browser"}:
        print(f"\n== {scraper.board_key()} ==", flush=True)
        paths = collect_paths(scraper, args.n)
        print(f"  harvested {len(paths)} externalPaths", flush=True)
        if not paths:
            print(
                "  no paths — the listing itself is refused; nothing to walk",
                flush=True,
            )

    if "direct" in arms:
        if paths:
            walk("direct", scraper, paths, None, slot("direct"), save)
        else:
            slot("direct").update(error="listing returned nothing — cannot walk")
        save()

    # Straight after `direct`, so it asks its question of an origin already refusing us, and over
    # the *tail* of the paths — the ones `direct` reached after its trip, not the early ones it
    # was still being served.
    if "browser" in arms:
        print("\n== browser, paired against the API on the same address ==", flush=True)
        if paths:
            try:
                paired_browser_walk(
                    scraper, paths[-args.browser_n :], slot("browser"), save
                )
            except Exception as exc:  # noqa: BLE001 - a browser that won't start is a result
                print(f"  browser unavailable: {type(exc).__name__}: {exc}", flush=True)
                slot("browser").update(error=f"{type(exc).__name__}: {exc}")
        else:
            slot("browser").update(error="listing returned nothing — cannot walk")
        save()

    if "warp" in arms:
        print("\n== via the spare egress ==", flush=True)
        proxy = spare_egress.proxy_url()
        if proxy is None:
            print("  no spare egress on this host — skipped", flush=True)
            slot("warp").update(error="no spare egress")
        elif paths:
            walk("warp", scraper, paths, proxy, slot("warp"), save)
        else:
            slot("warp").update(error="listing returned nothing — cannot walk")
        save()

    if args.out:
        print(f"\nwrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
