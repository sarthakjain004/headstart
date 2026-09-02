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
             address is still clean. It is a one-directional signal, and only the clean direction
             is strong: a control that stays 400-free while the suspect 400s rules out a globally
             penalised vantage. A control that *also* refuses proves less than it looks — it is
             taking the same trip-provoking load the suspect gets, so "the vantage is penalised"
             and "we tripped this tenant ourselves" both explain it.
``direct``   the same sustained walk over the 400-heavy Board — the arm that reproduces the
             throttle.
``browser``  Chrome and curl_cffi asking the same origin for the same path at the same moment,
             while a background walk holds the tenant under load for exactly as long as the arm
             runs. See :func:`paired_browser_walk` for why the load and the pairing are both
             load-bearing.
``warp``     the same walk through the spare egress (ADR-0063) — the route production's traffic
             takes once a Board has walled, and the one ADR-0102 newly opens for a 400.

Every arm keeps the first response of each distinct non-200 status **in full** — headers and body —
because that is the evidence naming who answered. A Cloudflare HTML block page and a Workday JSON
error carry the same status code and are completely different findings. One exception, stated
rather than papered over: the browser half of the paired arm keeps status and body only, because
``browser_http`` surfaces an in-page fetch's failure as ``BrowserHTTPError(status, body)`` and
never carries the response headers out of the tab.

Two deliberate choices about what is measured:

- The walks bypass the retry ladder (``retry_on=frozenset()``): the question is what the origin
  says first, and a ladder would report what it says third.
- They also drive their own ``AsyncSession`` rather than :meth:`BaseScraper.fan_out_async`, which
  the neighbouring probes use. That is not an oversight. ``fan_out_async`` resolves its width
  through :func:`spare_egress.stream_width`, which returns ``min(ceiling, 12)`` for a group that
  has walled — and the listing harvest below *can* wall workday, since ``_post`` carries
  ``_egress()`` with ``egress_on={400, 429}``. Resolution happens once per call, so an arm that
  started after such a wall would run pinned at 12 for its whole length while still reporting
  itself as a width-25 walk. That is precisely what would make this run incomparable to the
  laptop's, which is the entire point of the exercise, so the width is pinned here instead.

**Three limitations, named because being misled by them is more expensive than admitting them.**

*Width.* Width 25 matches the laptop baseline, which is what makes the vantage the only variable
against the doc — but production observes its 400s at width **12**, the width its detail pass drops
to once a Board has walled (ADR-0102). Neither width alone can answer the question, so the workflow
runs both: two replicas at the scraper's own ``detail_streams`` and two at 12. ``--width 0`` means
"whatever ``WorkdayScraper.detail_streams`` is", so neither the workflow nor this module has to
name 25 in a second place that could go stale against the scraper.

*Rotation.* Production's walled traffic rides a **rotating** WARP address; the ``warp`` arm rides
one static tunnel. This probe cannot reproduce rotation.

*History.* The doc's own leading hypothesis — WARP exit IPs "that have been hitting these tenants
hourly for weeks" — is **not testable here at all**, and no rerun fixes it: the workflow runs
``warp-cli registration new`` per replica, so every replica draws a fresh address with no history.
A clean ``warp`` arm therefore cannot exonerate that hypothesis; it can only fail to support it.

**There is deliberately no cooldown between arms.** It would be the obvious thing to add, and the
evidence says it would not work: the doc measured thermofisher still tripping on a walk started two
minutes after the previous one. The arms also run against two different tenants, and ``warp`` from
a different address, so there is no shared per-(tenant, IP) budget for a pause to drain.

Reported figures, and what each is for:

- ``first_refusal`` — the request index where a **refusal** status first appears, comparable to the
  doc's "trips at request" column. Kept apart from ``first_non_200`` so one stray 404 (a posting
  closed between the listing and the detail) cannot be misread as the trip. ``_REFUSALS`` omits
  503 on purpose: a 5xx is as plausibly a real server fault as a refusal, and the kept body sample
  lets the writeup reclassify one if it appears.
- ``after_trip`` — the status mix settled *after* that first refusal. This is the column the doc
  calls comparable across vantages; the whole-walk mix is not, since it dilutes with however long
  the walk ran clean. It necessarily includes the up-to-``width``-1 requests already in flight when
  the trip landed — a ceiling of ``width - 1``, so 24 on the width-25 legs and 11 on the
  width-12 ones.
- Indices are **completion order**, not launch order: the two differ by up to one window.

Arms run against two different tenants and, for ``warp``, from a different address, so one arm's
spent budget is not another's — the throttle the doc measured is per (tenant, source IP). What does
carry across arms is this address's standing with Cloudflare, which is exactly what ``control``
exists to measure, and why it runs first.

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
import threading
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

#: Production's own detail fan-out, read off the scraper rather than restated, so the claim that
#: this matches it cannot drift. ``--width 0`` resolves to this, which is how the workflow asks for
#: it without naming 25 a second place it could go stale.
WIDTH = WorkdayScraper.detail_streams
_BLOCK = 100  # the status mix is reported per this many settled requests
_HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}
_BODY_SAMPLE = 800  # chars of a non-200 body kept as evidence
#: Every arm needing the suspect's paths reports the same thing when the harvest came back empty,
#: and points at the one record that says why rather than restating the reason.
_NO_PATHS = "no paths — see boards.suspect for why"

#: Statuses that mean "this origin is refusing us" rather than "this posting is gone". Only these
#: mark the trip, so a stray 404 mid-walk cannot masquerade as the throttle engaging.
_REFUSALS = frozenset({"400", "403", "429"})


def _proxies(proxy: str | None) -> dict:
    return {"proxies": {"http": proxy, "https": proxy}} if proxy else {}


def _evidence(index: int, headers: dict, body: str) -> dict:
    """One kept response. ``headers`` is empty for a browser answer — ``browser_http`` surfaces a
    failed in-page fetch as ``BrowserHTTPError(status, body)`` and never carries headers out."""
    return {"at_request": index, "headers": headers, "body": body[:_BODY_SAMPLE]}


def _trace(proxy: str | None) -> dict[str, str]:
    """What this route looks like from outside: exit IP, Cloudflare colo, ``warp=on/off``.

    ``warp`` is the one that matters for the spare-egress arm — it is Cloudflare's own answer to
    "is this request really riding WARP", which no amount of reading ``warp-cli status`` proves.
    ``ip``/``colo``/``org`` matter for a different reason: replicas of this probe are only four
    samples if they are four *networks*, and nothing but these fields can tell you whether two
    runners came up in the same colo or /16.
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


def collect_paths(scraper: WorkdayScraper, want: int) -> tuple[list[str], int | None]:
    """``externalPath`` for up to ``want`` postings, plus the listing's own reported total.

    The total is what separates "this Board only has 300 postings" from "the listing was cut off
    at 300", which the walk's own counts cannot distinguish — and a short walk read as a clean one
    is the failure mode this probe most needs to avoid.

    Named as its neighbour ``probe_workday_detail.py`` names it. Deliberately *not* ``harvest``:
    ``headstart.harvest`` is the curated-feed entry point, and a near-synonym for a different
    thing is the naming failure CLAUDE.md §3 calls out by name.
    """
    paths: list[str] = []
    total: int | None = None
    offset = 0
    while len(paths) < want:
        page = scraper._post({}, offset=offset)
        postings = (page or {}).get("jobPostings") or []
        if not postings:
            print(
                f"    listing stopped at offset {offset} "
                f"({'404/empty' if page is None else 'no postings'})",
                flush=True,
            )
            break
        total = page.get("total", total)
        paths += [p["externalPath"] for p in postings if p.get("externalPath")]
        offset += len(postings)
        print(
            f"    listing offset {offset}: {len(paths)} paths (total {total})",
            flush=True,
        )
    return paths[:want], total


async def _settle(
    session, scraper: WorkdayScraper, path: str, proxy: str | None
) -> tuple[str, dict, str]:
    """One detail request's settled answer as ``(status-or-exception, headers, body)``."""
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
    except Exception as exc:  # noqa: BLE001 - classifying the failure is the point
        return type(exc).__name__, {}, str(exc)
    return str(response.status_code), dict(response.headers), response.text


def walk(
    label: str,
    scraper: WorkdayScraper,
    paths: list[str],
    proxy: str | None,
    width: int,
    slot: dict,
    save: Callable[[], None],
) -> None:
    """Walk every path at ``width`` and record what the origin settled on, into ``slot``.

    ``slot`` is updated and ``save`` called every :data:`_BLOCK` completions rather than once at
    the end, so a cancelled job (the workflow's 45-minute cap, or a runner reclaimed mid-arm)
    still leaves the measurement it had reached — CLAUDE.md forbids buffering output to the end,
    and an arm that prints nothing for ten minutes and then dies has produced nothing at all.

    The checkpoint is a synchronous write on the event loop, so it briefly stalls the fan-out once
    per :data:`_BLOCK` completions and very slightly depresses the request rate this arm reports.
    That is accepted rather than engineered around: moving it off the lock would not help, since
    nothing yields between the locked mutation and the write and so no other stream could run
    during it either way — and what the checkpoint buys, an arm that survives a cancelled job, is
    worth far more than a stall every hundredth request.
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
            width=width,
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

        gate = asyncio.Semaphore(width)
        async with AsyncSession(impersonate="chrome") as session:

            async def one(path: str) -> None:
                nonlocal first_refusal, first_non_200, seq
                async with gate:
                    code, headers, body = await _settle(session, scraper, path, proxy)
                    async with lock:
                        seq += 1
                        index = seq
                        statuses[code] += 1
                        blocks[(index - 1) // _BLOCK][code] += 1
                        if first_refusal is not None:
                            after_trip[code] += 1
                        new_status = code != "200" and code not in samples
                        if new_status:
                            samples[code] = _evidence(index, headers, body)
                        if first_non_200 is None and code != "200":
                            first_non_200 = {"at_request": index, "status": code}
                        if first_refusal is None and code in _REFUSALS:
                            first_refusal = {"at_request": index, "status": code}
                        if new_status:
                            print(
                                f"    first {code} at request #{index}\n"
                                f"      server={headers.get('server')!r} "
                                f"cf-ray={headers.get('cf-ray')!r} "
                                f"cf-mitigated={headers.get('cf-mitigated')!r} "
                                f"retry-after={headers.get('retry-after')!r}\n"
                                f"      body: {body[:200]!r}",
                                flush=True,
                            )
                        if index % _BLOCK == 0:
                            print(
                                f"    ...{index}/{len(paths)} {dict(statuses)}",
                                flush=True,
                            )
                            snapshot()
                            save()

            await asyncio.gather(*(one(p) for p in paths))

    print(f"  {label}: walking {len(paths)} details at width {width}", flush=True)
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
        # Refusals only, not every non-200: this is the doc's cross-vantage column, and letting a
        # stray 404 into it would inflate the one number `_REFUSALS` was split out to protect.
        refused = sum(c for k, c in after_trip.items() if k in _REFUSALS)
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
    scraper: WorkdayScraper,
    pressure_paths: list[str],
    probe_paths: list[str],
    width: int,
    slot: dict,
    save: Callable[[], None],
) -> None:
    """Chrome and curl_cffi asking the same origin for the same path at the same moment.

    Two things make this readable, and it is unreadable without either.

    **The pairing.** A browser 200 on its own says nothing, because a throttle window can lapse
    between arms — the doc warns that thermofisher answered 700 straight requests before tripping.
    Pairing each Chrome fetch with an API fetch of the same path makes each observation carry its
    own control: ``api 400 -> browser 200`` says the penalty is client-shaped, ``api 400 ->
    browser 400`` says it is IP-shaped, and ``api 200 -> ...`` says the origin was not refusing
    anyone at that moment and the pair is simply uninformative.

    **The pressure.** The throttle is rate-shaped, so a sequential pair at well under 1 req/s
    would let the API half recover to 200 and every pair would land in that last, useless
    category. So a background thread replays ``pressure_paths`` at ``width`` for exactly as long
    as the pairing runs, and is stopped the moment it ends.

    Two ordering details are load-bearing rather than incidental. The loader starts *after* the
    tab is open, because a cold Chrome can take up to 200s to launch (three 60s attempts plus a
    20s navigation) — longer than a whole pressure walk, which would have left the arm recording
    nothing under load at all. And the loader is asked to stop before this returns, so it cannot
    go on hammering the tenant underneath the next arm.
    """
    from headstart import browser_http

    company, instance, site = scraper._parts()
    pressure: collections.Counter[str] = collections.Counter()
    pressure_lock = (
        threading.Lock()
    )  # the loader writes it; this thread reads it every pair
    stop = threading.Event()
    pairs: collections.Counter[str] = collections.Counter()
    sequence: list[str] = []  # per-pair, in order, so a decay back to 200 is visible
    samples: dict[str, dict] = {}
    started = time.monotonic()

    def hold_under_load() -> None:
        """Replay `pressure_paths` at `width` until `stop` is set."""

        async def run() -> None:
            from curl_cffi.requests import AsyncSession

            gate = asyncio.Semaphore(width)
            async with AsyncSession(impersonate="chrome") as session:

                async def one(path: str) -> None:
                    async with gate:
                        if stop.is_set():
                            return
                        code, _, _ = await _settle(session, scraper, path, None)
                        with pressure_lock:
                            pressure[code] += 1

                # `gather()` of nothing returns without yielding, so an empty list here would
                # spin a core rather than idle. Unreachable via `main`; one line to make safe.
                while pressure_paths and not stop.is_set():
                    await asyncio.gather(*(one(p) for p in pressure_paths))

        asyncio.run(run())

    def pressure_now() -> dict:
        with pressure_lock:
            return dict(pressure)

    loader: threading.Thread | None = None
    try:
        with browser_http.origin(
            f"https://{company}.{instance}.myworkdayjobs.com/{site}"
        ) as page:
            loader = threading.Thread(
                target=hold_under_load, name="pressure", daemon=True
            )
            loader.start()
            print(
                f"  browser: tab open; {len(pressure_paths)} details now holding the tenant at "
                f"width {width} for as long as the pairing runs",
                flush=True,
            )
            for index, path in enumerate(probe_paths, 1):
                try:
                    response = http.fetch(
                        "GET",
                        scraper._detail_url(path),
                        timeout=30,
                        retry_on=frozenset(),
                        headers=_HEADERS,
                    )
                    api = str(response.status_code)
                    if api != "200":
                        samples.setdefault(
                            f"api {api}",
                            _evidence(index, dict(response.headers), response.text),
                        )
                except Exception as exc:  # noqa: BLE001 - classifying the failure is the point
                    api = type(exc).__name__
                    samples.setdefault(f"api {api}", _evidence(index, {}, str(exc)))
                try:
                    page.get_json(f"/wday/cxs/{company}/{site}{path}")
                    browser = "200"
                except browser_http.BrowserHTTPError as exc:
                    browser = str(exc.status_code)
                    samples.setdefault(
                        f"browser {browser}", _evidence(index, {}, exc.body)
                    )
                except Exception as exc:  # noqa: BLE001 - classifying the failure is the point
                    browser = type(exc).__name__
                    samples.setdefault(
                        f"browser {browser}", _evidence(index, {}, str(exc))
                    )
                pair = f"api {api} -> browser {browser}"
                pairs[pair] += 1
                sequence.append(pair)
                slot.update(
                    requested=len(probe_paths),
                    walked=index,
                    width=width,
                    pairs=dict(pairs),
                    sequence=sequence,
                    pressure=pressure_now(),
                    samples=samples,
                    seconds=round(time.monotonic() - started, 1),
                )
                save()
                if index % 5 == 0:
                    print(f"    ...{index} pairs {dict(pairs)}", flush=True)
    finally:
        stop.set()
        if loader is not None:
            loader.join(timeout=60)
            if loader.is_alive():
                # Say so rather than leaving it silent: a loader still in flight is still loading
                # the tenant the next arm is about to measure.
                print(
                    "    WARNING: pressure thread did not stop within 60s — the next arm's "
                    "measurement is not on an idle tenant",
                    flush=True,
                )
                slot.update(pressure_still_running=True)
    slot.update(pressure=pressure_now(), seconds=round(time.monotonic() - started, 1))
    save()
    print(f"  browser: {dict(pairs)}", flush=True)
    print(f"    pressure over the same window: {pressure_now()}", flush=True)


def resolved_scraper(slug: str) -> WorkdayScraper:
    """A scraper for ``slug`` with its serving instance resolved (a migrated tenant's slug host
    500s; ``_resolve_instance`` finds the one that answers)."""
    scraper = WorkdayScraper(slug, "probe")
    scraper._resolve_instance()
    return scraper


def resolve_and_collect(
    slug: str, want: int, record: dict
) -> tuple[WorkdayScraper | None, list[str]]:
    """Resolve a Board and harvest its paths, recording *why* into ``record`` if either fails.

    Guarded because the arms are not independent otherwise: ``control`` runs first, and an
    unhandled raise there would take ``direct`` — the primary measurement — down with it.
    """
    try:
        scraper = resolved_scraper(slug)
    except Exception as exc:  # noqa: BLE001 - one Board's failure must not sink the probe
        print(f"  cannot resolve {slug}: {type(exc).__name__}: {exc}", flush=True)
        record.update(error=f"resolve failed: {type(exc).__name__}: {exc}")
        return None, []
    print(f"\n== {scraper.board_key()} ==", flush=True)
    record.update(board=scraper.board_key(), url=scraper.url())
    try:
        paths, total = collect_paths(scraper, want)
    except Exception as exc:  # noqa: BLE001 - one Board's failure must not sink the probe
        print(f"  listing failed: {type(exc).__name__}: {exc}", flush=True)
        record.update(error=f"listing failed: {type(exc).__name__}: {exc}")
        return scraper, []
    print(
        f"  harvested {len(paths)} externalPaths (listing reports {total})", flush=True
    )
    record.update(listing_total=total, harvested=len(paths))
    if not paths:
        record.update(error="listing returned nothing — cannot walk")
    return scraper, paths


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
        help="paired api/Chrome fetches; 0 skips the arm (default 40)",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=0,
        help=f"fan-out width; 0 means the scraper's own detail_streams ({WIDTH}), which is the "
        "laptop baseline. Production's walled detail pass runs 12.",
    )
    parser.add_argument(
        "--arms",
        default="where,control,direct,browser,warp",
        help="comma-separated subset, run in this fixed order",
    )
    parser.add_argument("--out", type=Path, help="write the full result JSON here")
    args = parser.parse_args()
    arms = {a.strip() for a in args.arms.split(",") if a.strip()}
    width = args.width or WIDTH

    result: dict = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "n": args.n,
        "width": width,
        "boards": {},
        "arms": {},
    }

    def save() -> None:
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    def slot(name: str) -> dict:
        return result["arms"].setdefault(name, {})

    def board(name: str) -> dict:
        return result["boards"].setdefault(name, {})

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
        control, control_paths = resolve_and_collect(
            args.control, args.n, board("control")
        )
        if control and control_paths:
            walk("control", control, control_paths, None, width, slot("control"), save)
        else:
            slot("control").update(error=_NO_PATHS.replace("suspect", "control"))
        save()

    scraper: WorkdayScraper | None = None
    paths: list[str] = []
    if arms & {"direct", "warp", "browser"}:
        scraper, paths = resolve_and_collect(args.suspect, args.n, board("suspect"))
    ready = bool(scraper and paths)

    if "direct" in arms:
        if ready:
            walk("direct", scraper, paths, None, width, slot("direct"), save)
        else:
            slot("direct").update(error=_NO_PATHS)
        save()

    # Straight after `direct`, so it asks its question of an origin already refusing us, and over
    # the *tail* of the paths — the ones `direct` reached after its trip, not the early ones it
    # was still being served.
    if "browser" in arms and args.browser_n > 0:
        print("\n== browser, paired against the API under load ==", flush=True)
        if ready:
            try:
                paired_browser_walk(
                    scraper,
                    paths,
                    paths[-args.browser_n :],
                    width,
                    slot("browser"),
                    save,
                )
            except Exception as exc:  # noqa: BLE001 - a browser that won't start is a result
                print(f"  browser unavailable: {type(exc).__name__}: {exc}", flush=True)
                slot("browser").update(error=f"{type(exc).__name__}: {exc}")
        else:
            slot("browser").update(error=_NO_PATHS)
        save()

    if "warp" in arms:
        print("\n== via the spare egress ==", flush=True)
        proxy = spare_egress.proxy_url()
        if proxy is None:
            print("  no spare egress on this host — skipped", flush=True)
            slot("warp").update(error="no spare egress")
        elif ready:
            walk("warp", scraper, paths, proxy, width, slot("warp"), save)
        else:
            slot("warp").update(error=_NO_PATHS)
        save()

    if args.out:
        print(f"\nwrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
