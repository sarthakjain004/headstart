"""Is a GitHub runner's refusal by Radancy's fronts the address, the request rate, or both?

`docs/radancy/2026-09-29_detail-page-403-wall.md` (ADR-0346) measured that 12.1% to 36.0% of a run's
Radancy job pages answer 403 on the runners, unevenly across shards and runs, and never from a
laptop. What that leaves open, and only a runner can answer:

1. **The refused response itself** (no scraper logs it): who answered, with what headers and body.
2. **Address or rate.** A runner reads job pages one a second (``sequential``), then at the
   scraper's own shape, 16 HTTP/2 streams on one connection (``burst``). Refused only at speed is a
   rate wall; refused at one a second too is the address (or a count); clean at both says this
   runner's address is not the refused kind, which the replicas exist to catch.
3. **Client or address.** At the first refusal the refused URL is retried with three clients, and a
   control Board's page is read beside it. The clients differ in headers and TLS stack; the control
   Board tells a host-specific refusal from one that follows the address across hosts.
4. **How long a refusal lasts:** the retries come 5, 30 and 120 seconds after the refusal.
5. **Direct or spare egress.** The burst repeats through Cloudflare WARP (ADR-0063), the route the
   scraper takes after the first 403 now that Radancy opts in. After a refusal through WARP the
   probe rotates the address once (``spare_egress.rotate``) and bursts again: a second burst that
   passes about as many pages as the first says a fresh address buys a fresh allowance, which is
   what ADR-0346 assumes.

Arms, in this order, each printed as it finishes and checkpointed to ``--out``:

``where``       exit route direct and via WARP: the address's SHA-256 prefix (never the address),
                Cloudflare colo, country, WARP on/off and the network's name, so two replicas can be
                told apart, or found to share an address, without printing one.
``sitemap``     one ``/sitemap.xml`` GET per client, per Board.
``sequential``  job pages one a second on each test Board, in parallel across Boards.
``burst``       ``--burst-n`` job pages at 16 streams on the control Board, then on the test Boards
                at once. A Board already refused in ``sequential`` is skipped: its address is spent.
``warp``        the burst through WARP, then a rotation and a second burst on each refused Board.

**Stop rules, the point of the design.** A Board stops at its first 403 or 429 in every arm: the
response is kept whole (status, every header except cookie values, the first 800 characters of the
body), the retries at 5, 30 and 120 s run, and nothing else is asked of that Board. In ``burst``
every stream stops at the first refusal, so at most ``width - 1`` requests already in flight
settle after it; those are counted apart (``after_refusal``). Three errors in a row also stop a
Board. ``--pages`` and ``--burst-n`` are capped at 400, and the whole probe skips arms it has no
time for (``--budget-s``), so the run ends inside the workflow's timeout (default 720 s).

**Reading a replica** (the Markdown summary has the tables; the JSON has every response):

- ``sequential`` refused after about 260 pages and the ``burst`` skipped: the address or a count
  refuses at one a second too, so pace is not the lever. ``burst`` refused where ``sequential`` was
  clean: the rate is. Clean at both: this runner's address is not the refused kind, so compare the
  replicas (their ``address_sha256_12`` say whether they were really different addresses).
- The retries: still refused at 120 s by every client means the refusal outlasts a wait; refused
  for ``scraper`` but served to ``chrome_default`` or ``requests_browser_ua`` means the client
  matters; the ``control`` Board refused too means the refusal follows the address across Boards,
  served means it is per Board.
- ``warp`` against ``burst``: ``pages_ok_before`` through WARP against direct, and the
  ``after_rotation`` burst against the first: a fresh address that passes about as many pages
  says a rotation buys a fresh allowance. A ``sitemap`` refusal for ``scraper`` alone, or a
  refusal body that names a bot check, is the client-shaped wall the owner suspected.

**What is printed and kept.** Hosts, statuses, timings, response headers and refusal bodies. Not
the runner's address (hashed; any header or body that echoes it is scrubbed), not cookie values
(names only), and no credential of any kind: nothing here reads an environment variable.

Run (local, one request a second per Board; it never bursts when run without ``burst``/``warp``):
  PYTHONPATH=src .venv/bin/python -u scripts/bench/probe_radancy_runner_refusal.py \\
      --arms where,sitemap,sequential --pages 20 --out /tmp/radancy-probe.json
  PYTHONPATH=src .venv/bin/python scripts/bench/probe_radancy_runner_refusal.py \\
      --render /tmp/radancy-probe.json        # the Markdown summary of a result
On a runner: ``.github/workflows/radancy-runner-probe.yml`` (workflow_dispatch), three replicas,
one JSON, one log and one Markdown summary each, uploaded as an artifact, and the summary in the
job's summary page.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import hashlib
import json
import re
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
from curl_cffi import requests as curl
from curl_cffi.requests import AsyncSession

from headstart.network import spare_egress
from headstart.scrapers.base import USER_AGENT
from headstart.scrapers.radancy import RadancyScraper, sitemap_rows

#: Walled on the runner in run 36542219238 after 262 and 256 pages (`jobs.jabil.com`,
#: `careers.sysco.com`). `careers.staples.com`'s sitemap was refused on a runner in two of three
#: runs. `careers.petco.com` lost none of 3,863 pages in each of the same three runs.
TEST_HOSTS = "jobs.jabil.com,careers.sysco.com"
SITEMAP_HOST = "careers.staples.com"
CONTROL_HOST = "careers.petco.com"

#: The scraper's own fan-out, read off it rather than restated.
WIDTH = RadancyScraper.detail_streams
#: The scraper's own client (Chrome-impersonated `curl_cffi`, its `User-Agent`), `curl_cffi` with
#: its Chrome `User-Agent`, and a plain `requests` call with a browser `User-Agent`.
CLIENTS = ("scraper", "chrome_default", "requests_browser_ua")
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

#: Statuses that mean "this origin refuses us", as against a posting closed since the sitemap.
REFUSALS = frozenset({403, 429})
#: Seconds after the first refusal at which the refused URL is asked for again.
LADDER_S = (5, 30, 120)
BODY_SAMPLE = 800
#: Hard caps, so a dispatch input cannot turn this into a load test.
PAGES_CAP = 400
BURST_CAP = 400
_ERRORS_IN_A_ROW = 3
_ARMS = ("where", "sitemap", "sequential", "burst", "warp")
_HOST = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$"
)
_ADDRESS = re.compile(r"^[0-9.]+$")
_COOKIE_NAME = re.compile(r"(?:^|,\s*)([A-Za-z0-9_.\-]+)=")

_started = time.monotonic()
_print_lock = threading.Lock()
_runner_ips: set[str] = set()


def say(message: str) -> None:
    with _print_lock:
        print(f"[{time.monotonic() - _started:6.0f}s] {message}", flush=True)


def scrub(text: str) -> str:
    """``text`` with any address this probe has learned as its own removed."""
    for ip in _runner_ips:
        text = text.replace(ip, "<runner-address>")
    return text


def hosts_of(value: str) -> list[str]:
    """The hostnames in a comma-separated ``value``, each checked to be a bare public-looking name
    (no scheme, port, path or single-label host), so an input cannot aim the probe at anything but
    a front by its hostname."""
    hosts = [h.strip().lower() for h in value.split(",") if h.strip()]
    for host in hosts:
        if not _HOST.match(host) or _ADDRESS.match(host):
            raise SystemExit(f"not a hostname: {host!r}")
    return hosts


def _set_cookie_lines(headers: Any) -> str:
    """Every ``Set-Cookie`` header of ``headers``, joined, for their names alone."""
    lines = [str(v) for k, v in headers.items() if k.lower() == "set-cookie"]
    get_list = getattr(headers, "get_list", None)  # `curl_cffi` keeps repeats apart
    if get_list:
        lines += [str(v) for v in get_list("set-cookie") or []]
    return ", ".join(lines)


def reply_of(response: Any, *, keep_body: bool) -> dict[str, Any]:
    """What one answer says, kept whole where it is evidence: every header but cookie values
    (their names only), and the start of the body when ``keep_body``."""
    headers = {
        key.lower(): scrub(str(value))
        for key, value in response.headers.items()
        if key.lower() != "set-cookie"
    }
    cookies = sorted(set(_COOKIE_NAME.findall(_set_cookie_lines(response.headers))))
    reply: dict[str, Any] = {
        "status": response.status_code,
        "headers": headers,
        "set_cookie_names": cookies,
    }
    if keep_body:
        reply["body"] = scrub(response.text[:BODY_SAMPLE])
    return reply


def failure_of(exc: BaseException) -> dict[str, Any]:
    return {"status": None, "error": scrub(f"{type(exc).__name__}: {exc}")[:200]}


def _proxies(proxy: str | None) -> dict[str, Any]:
    return {"proxies": {"http": proxy, "https": proxy}} if proxy else {}


def ask(client: str, url: str) -> dict[str, Any]:
    """One direct GET of ``url`` as ``client``, on a fresh connection; never raises."""
    try:
        if client == "scraper":
            response = curl.Session(impersonate="chrome").get(
                url, headers={"User-Agent": USER_AGENT}, timeout=45
            )
        elif client == "chrome_default":
            response = curl.Session(impersonate="chrome").get(url, timeout=45)
        else:
            response = requests.get(
                url, headers={"User-Agent": _BROWSER_UA}, timeout=45
            )
    except Exception as exc:  # noqa: BLE001 - a host that will not answer is a result
        return failure_of(exc)
    reply = reply_of(response, keep_body=response.status_code != 200)
    reply["bytes"] = len(response.content)
    reply["_text"] = response.text if response.status_code == 200 else ""
    return reply


class Findings:
    """The result document, checkpointed to ``--out`` whenever an arm records something, so a
    cancelled job still leaves what it had reached."""

    def __init__(self, out: Path | None, args: dict[str, Any]) -> None:
        self.out = out
        self.lock = threading.Lock()
        self.data: dict[str, Any] = {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "args": args,
            "arms": {},
        }

    def record(self, arm: str, key: str, value: Any) -> None:
        with self.lock:
            self.data["arms"].setdefault(arm, {})[key] = value
            self.save_locked()

    def save_locked(self) -> None:
        if self.out:
            self.out.parent.mkdir(parents=True, exist_ok=True)
            self.out.write_text(json.dumps(self.data, indent=2), encoding="utf-8")


def _short_ip(ip: str) -> str:
    return hashlib.sha256(ip.encode()).hexdigest()[:12]


def trace(proxy: str | None) -> dict[str, str]:
    """What a route looks like from outside, without the address: its hash, Cloudflare's colo and
    country, whether WARP is on, and the network's name."""
    try:
        response = curl.Session(impersonate="chrome").get(
            "https://www.cloudflare.com/cdn-cgi/trace",
            headers={"User-Agent": USER_AGENT},
            timeout=20,
            **_proxies(proxy),
        )
        fields = dict(
            line.split("=", 1) for line in response.text.splitlines() if "=" in line
        )
    except Exception as exc:  # noqa: BLE001 - a vantage we cannot read is a result
        return {"error": scrub(f"{type(exc).__name__}: {exc}")[:200]}
    ip = fields.get("ip", "")
    out = {k: fields[k] for k in ("loc", "colo", "warp") if k in fields}
    if ip:
        _runner_ips.add(ip)
        out["address_sha256_12"] = _short_ip(ip)
        try:  # the network's name is what says "Microsoft" or "Cloudflare" out loud
            info = curl.Session().get(
                f"https://ipinfo.io/{ip}/json", timeout=15, **_proxies(proxy)
            )
            out["org"] = str(info.json().get("org", "?"))
        except Exception:  # noqa: BLE001, S110 - the hash alone still tells two routes apart
            pass
    return out


def ladder(
    host: str, url: str, control_url: str | None, refused_at: float
) -> list[dict]:
    """Ask for the refused ``url`` again ``LADDER_S`` seconds after ``refused_at``, with every
    client, and for the control Board's page beside it: still refused, refused by every client or
    one, and whether another Board on this address is refused too."""
    rungs: list[dict] = []
    for target in LADDER_S:
        time.sleep(max(0.0, refused_at + target - time.monotonic()))
        rung: dict[str, Any] = {
            "after_s": target,
            "since_refusal_s": round(time.monotonic() - refused_at, 1),
            "clients": {},
        }
        for client in CLIENTS:
            reply = ask(client, url)
            rung["clients"][client] = {
                k: v for k, v in reply.items() if k in ("status", "error", "bytes")
            } | (
                {"server": reply["headers"].get("server")}
                if reply.get("headers")
                else {}
            )
            time.sleep(1.0)  # one a second to this Board, even here
        if control_url:
            control = ask("scraper", control_url)
            rung["control_board"] = {
                k: v for k, v in control.items() if k in ("status", "error")
            }
        rungs.append(rung)
        say(
            f"    {host} +{rung['since_refusal_s']}s: "
            + " ".join(f"{c}={r.get('status')}" for c, r in rung["clients"].items())
            + (f" control={rung['control_board'].get('status')}" if control_url else "")
        )
    return rungs


# --- arms ---------------------------------------------------------------------------------


def arm_where(findings: Findings, proxy: str | None) -> None:
    direct = trace(None)
    say(f"where direct: {direct}")
    warp = trace(proxy) if proxy else {"error": "no spare egress on this host"}
    say(f"where warp  : {warp}")
    findings.record("where", "direct", direct)
    findings.record("where", "warp", warp)


def arm_sitemap(
    findings: Findings, hosts: list[str], rows: dict[str, list[tuple[str, str]]]
) -> None:
    def one(host: str) -> None:
        results: dict[str, Any] = {}
        for client in CLIENTS:
            time.sleep(1.0)
            reply = ask(client, f"https://{host}/sitemap.xml")
            text = reply.pop("_text", "")
            results[client] = reply
            if reply.get("status") == 200 and host not in rows:
                found = sitemap_rows(text, host)
                if found:
                    rows[host] = found
                    reply["job_urls"] = len(found)
            say(
                f"sitemap {host} {client}: {reply.get('status')} {reply.get('error', '')}"
            )
        findings.record("sitemap", host, results)

    threads = [threading.Thread(target=one, args=(h,)) for h in hosts]
    [t.start() for t in threads]
    [t.join() for t in threads]


def arm_sequential(
    findings: Findings,
    hosts: list[str],
    rows: dict[str, list[tuple[str, str]]],
    pages: int,
    pause: float,
    control_url: str | None,
    cap_s: float,
    refused: dict[str, str],
) -> None:
    deadline = time.monotonic() + cap_s

    def one(host: str) -> None:
        record: dict[str, Any] = {"pages_wanted": pages, "statuses": {}}
        statuses: collections.Counter[str] = collections.Counter()
        session = curl.Session(impersonate="chrome")
        began = time.monotonic()
        errors = 0
        for index, (_, url) in enumerate(rows[host][:pages]):
            if time.monotonic() > deadline:
                record["stopped"] = "arm time cap"
                break
            sent = time.monotonic()
            try:
                response = session.get(
                    url, headers={"User-Agent": USER_AGENT}, timeout=45
                )
                status: int | None = response.status_code
                errors = 0
            except Exception as exc:  # noqa: BLE001
                status, errors = None, errors + 1
                response = None
                record["last_error"] = failure_of(exc)["error"]
            statuses[str(status)] += 1
            if status in REFUSALS and response is not None:
                record["first_refusal"] = {
                    "index": index,
                    "pages_ok_before": statuses["200"],
                    "t_s": round(time.monotonic() - began, 1),
                    **reply_of(response, keep_body=True),
                    "url": url,
                }
                say(
                    f"sequential {host}: {status} at page #{index} after "
                    f"{statuses['200']} ok, server={record['first_refusal']['headers'].get('server')!r}"
                )
                refused[host] = f"sequential page #{index}"
                record["ladder"] = ladder(host, url, control_url, time.monotonic())
                break
            if errors >= _ERRORS_IN_A_ROW:
                record["stopped"] = f"{errors} errors in a row"
                break
            if (index + 1) % 50 == 0:
                say(f"sequential {host}: {index + 1} pages, {dict(statuses)}")
            time.sleep(max(0.0, sent + pause - time.monotonic()))
        record["statuses"] = dict(statuses)
        record["seconds"] = round(time.monotonic() - began, 1)
        findings.record("sequential", host, record)
        say(f"sequential {host}: {dict(statuses)} in {record['seconds']}s")

    threads = [threading.Thread(target=one, args=(h,)) for h in hosts if rows.get(h)]
    [t.start() for t in threads]
    [t.join() for t in threads]


async def burst(
    session: Any,
    urls: list[str],
    *,
    proxy: str | None,
    width: int,
    deadline: float,
) -> dict[str, Any]:
    """Send ``urls`` at ``width`` streams and stop every stream at the first refusal.

    ``session`` is one ``AsyncSession``, so the streams multiplex over one connection as the
    scraper's own do. A request already in flight when the refusal lands still settles and is
    counted in ``after_refusal`` (at most ``width - 1`` of them); one waiting for a stream is never
    sent. Indices are completion order.
    """
    stop = asyncio.Event()
    gate = asyncio.Semaphore(width)
    statuses: collections.Counter[str] = collections.Counter()
    after: collections.Counter[str] = collections.Counter()
    first: dict[str, Any] | None = None
    refused_at: float | None = None
    error_sample: str | None = None
    sent = settled = errors = 0
    began = time.monotonic()

    async def one(url: str) -> None:
        nonlocal first, refused_at, error_sample, sent, settled, errors
        async with gate:
            if stop.is_set() or time.monotonic() > deadline:
                return
            sent += 1
            try:
                response = await session.get(
                    url,
                    headers={"User-Agent": USER_AGENT},
                    timeout=45,
                    **_proxies(proxy),
                )
                status = str(response.status_code)
                errors = 0
            except Exception as exc:  # noqa: BLE001 - counted, and the first one kept
                response, status = None, "error"
                error_sample = error_sample or failure_of(exc)["error"]
                errors += 1
                if errors >= _ERRORS_IN_A_ROW:
                    stop.set()
            settled += 1
            statuses[status] += 1
            if first is not None:
                after[status] += 1
            elif response is not None and int(status) in REFUSALS:
                first = {
                    "index": settled,
                    "pages_ok_before": statuses["200"],
                    "t_s": round(time.monotonic() - began, 1),
                    **reply_of(response, keep_body=True),
                    "url": url,
                }
                refused_at = time.monotonic()
                stop.set()

    await asyncio.gather(*(one(u) for u in urls))
    seconds = round(time.monotonic() - began, 1)
    return {
        "urls": len(urls),
        "sent": sent,
        "settled": settled,
        "width": width,
        "statuses": dict(statuses),
        "first_refusal": first,
        "after_refusal": dict(after),
        "error_sample": error_sample,
        "_refused_at": refused_at,
        "seconds": seconds,
        "req_per_s": round(settled / max(seconds, 0.001), 1),
    }


async def burst_boards(
    boards: dict[str, list[str]],
    *,
    proxy: str | None,
    width: int,
    deadline: float,
    control_url: str | None,
    ladder_on_refusal: bool,
) -> dict[str, dict[str, Any]]:
    """Burst every Board in ``boards`` at once, each on its own ``AsyncSession`` (the scraper reads
    a shard's Boards side by side). On the direct route a refused Board's retries start the
    moment its burst ends, in a thread, while the others carry on."""

    async def one(host: str, urls: list[str]) -> tuple[str, dict[str, Any]]:
        async with AsyncSession(impersonate="chrome") as session:
            result = await burst(
                session, urls, proxy=proxy, width=width, deadline=deadline
            )
        refused_at = result.pop("_refused_at")
        first = result["first_refusal"]
        if first:
            say(
                f"burst {host}: {first['status']} after {first['pages_ok_before']} ok "
                f"(completion #{first['index']}), server={first['headers'].get('server')!r}, "
                f"{result['after_refusal']} settled after it"
            )
            if ladder_on_refusal and refused_at is not None:
                result["ladder"] = await asyncio.to_thread(
                    ladder, host, first["url"], control_url, refused_at
                )
        else:
            say(f"burst {host}: {result['statuses']} in {result['seconds']}s")
        return host, result

    return dict(await asyncio.gather(*(one(h, u) for h, u in boards.items())))


def arm_burst(
    findings: Findings,
    arm: str,
    *,
    proxy: str | None,
    rows: dict[str, list[tuple[str, str]]],
    control: str,
    tests: list[str],
    burst_n: int,
    width: int,
    left: Callable[[], float],
    control_url: str | None,
    refused: dict[str, str],
) -> None:
    """The control Board alone first (this address is still clean), then the test Boards at once.

    Direct only: a test Board already refused in ``sequential`` is skipped, since its address is
    spent and a second refusal would say nothing new. Through WARP nothing is skipped (a different
    address), and a Board refused there gets one rotation and a second burst on fresh URLs.
    """
    direct = proxy is None

    def urls(host: str, offset: int = 0) -> list[str]:
        return [u for _, u in rows[host][offset : offset + burst_n]]

    def run(
        boards: dict[str, list[str]], suffix: str = ""
    ) -> dict[str, dict[str, Any]]:
        deadline = time.monotonic() + max(30.0, min(90.0, left() - 30))
        results = asyncio.run(
            burst_boards(
                boards,
                proxy=proxy,
                width=width,
                deadline=deadline,
                control_url=control_url,
                ladder_on_refusal=direct,
            )
        )
        for host, result in results.items():
            findings.record(arm, host + suffix, result)
            if direct and result["first_refusal"]:
                refused[host] = f"burst completion #{result['first_refusal']['index']}"
        return results

    if control in rows:
        run({control: urls(control)})
    boards = {
        h: urls(h) for h in tests if rows.get(h) and not (direct and h in refused)
    }
    for host in tests:
        if direct and host in refused:
            findings.record(
                arm, host, {"skipped": f"already refused in {refused[host]}"}
            )
    if not boards:
        return
    results = run(boards)
    walled = [h for h, r in results.items() if r["first_refusal"]]
    if direct or not walled:
        return
    if left() < 90:
        findings.record(arm, "rotation", {"skipped": "out of time budget"})
        return
    fresh = spare_egress.rotate("radancy-runner-probe")
    now = trace(proxy)
    say(f"warp rotated to a fresh address: {fresh}; {now}")
    findings.record(arm, "rotation", {"fresh_address": fresh, "route_after": now})
    second = {h: urls(h, burst_n) for h in walled if len(rows[h]) > burst_n + width}
    if second:
        run(second, suffix=":after_rotation")


def _line(cells: list[Any]) -> str:
    return "| " + " | ".join("" if c is None else str(c) for c in cells) + " |"


def render_summary(data: dict[str, Any], replica: str) -> str:
    """The job summary: what the arms found, as tables, and nothing an arm did not record."""
    arms = data["arms"]
    out = [f"## Radancy runner probe, replica {replica}", ""]
    where = arms.get("where", {})
    for route in ("direct", "warp"):
        if route in where:
            out.append(f"- **{route}**: `{where[route]}`")
    refusal_rows: list[str] = []
    for arm, route in (("sequential", "direct"), ("burst", "direct"), ("warp", "WARP")):
        for host, record in arms.get(arm, {}).items():
            if not isinstance(record, dict):
                continue
            if "skipped" in record:
                refusal_rows.append(
                    _line([arm, host, route, "", "skipped", record["skipped"]])
                )
            elif "statuses" in record:
                first = record.get("first_refusal")
                refusal_rows.append(
                    _line(
                        [
                            arm,
                            host,
                            route,
                            first["pages_ok_before"]
                            if first
                            else record["statuses"].get("200"),
                            f"{first['status']} at #{first['index']}"
                            if first
                            else "none",
                            (first or {}).get("headers", {}).get("server", ""),
                        ]
                    )
                )
    out += [
        "",
        "### Pages read, and the first refusal (whole response in the JSON artifact)",
        "",
    ]
    out += [_line(["arm", "Board", "route", "ok before", "first refusal", "server"])]
    out += [_line(["---"] * 6), *refusal_rows]
    ladder_rows: list[str] = []
    for arm in ("sequential", "burst"):
        for host, record in arms.get(arm, {}).items():
            for rung in record.get("ladder", []) if isinstance(record, dict) else []:
                ladder_rows.append(
                    _line(
                        [
                            arm,
                            host,
                            rung["since_refusal_s"],
                            *(rung["clients"][c].get("status") for c in CLIENTS),
                            rung.get("control_board", {}).get("status"),
                        ]
                    )
                )
    out += [
        "",
        "### Did the refusal last? Retries after the first refusal, direct route",
        "",
    ]
    if ladder_rows:
        out += [_line(["arm", "Board", "after (s)", *CLIENTS, "control Board"])]
        out += [_line(["---"] * (3 + len(CLIENTS) + 1)), *ladder_rows]
    else:
        out.append("No direct refusal, so no retries ran.")
    rotation = arms.get("warp", {}).get("rotation")
    if rotation:
        out += ["", f"### WARP rotation: `{rotation}`"]
    sitemaps = arms.get("sitemap", {})
    if sitemaps:
        out += ["", "### Sitemap, status per client", ""]
        out.append(_line(["Board", *CLIENTS]))
        out.append(_line(["---"] * (1 + len(CLIENTS))))
        for host, clients in sitemaps.items():
            out.append(_line([host, *(clients[c].get("status") for c in CLIENTS)]))
    skipped = {k: v for k, v in arms.get("budget", {}).items()}
    if skipped:
        out += ["", f"Skipped for time: `{skipped}`"]
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--hosts", default=TEST_HOSTS, help="test Boards, comma-separated"
    )
    parser.add_argument("--sitemap-host", default=SITEMAP_HOST)
    parser.add_argument("--control", default=CONTROL_HOST)
    parser.add_argument(
        "--pages", type=int, default=300, help=f"sequential pages (max {PAGES_CAP})"
    )
    parser.add_argument(
        "--burst-n", type=int, default=300, help=f"burst requests (max {BURST_CAP})"
    )
    parser.add_argument(
        "--width",
        type=int,
        default=0,
        help=f"burst streams; 0 is the scraper's own ({WIDTH})",
    )
    parser.add_argument(
        "--pause", type=float, default=1.0, help="seconds between sequential requests"
    )
    parser.add_argument("--arms", default=",".join(_ARMS))
    parser.add_argument(
        "--budget-s", type=float, default=720.0, help="skip arms it has no time for"
    )
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--summary", type=Path, help="write the Markdown job summary here"
    )
    parser.add_argument("--replica", default="1")
    parser.add_argument(
        "--render",
        type=Path,
        help="probe nothing: print the Markdown summary of this result JSON (a cancelled "
        "probe still leaves its checkpointed JSON, and the workflow summarises it from there)",
    )
    args = parser.parse_args()
    if args.render:
        if not args.render.exists():
            print(
                f"## Radancy runner probe, replica {args.replica}\n\nNo result was written."
            )
            return 0
        print(render_summary(json.loads(args.render.read_text("utf-8")), args.replica))
        return 0

    arms = {a.strip() for a in args.arms.split(",") if a.strip()}
    if arms - set(_ARMS):
        raise SystemExit(f"unknown arm(s): {sorted(arms - set(_ARMS))}")
    tests = hosts_of(args.hosts)
    sitemap_host = hosts_of(args.sitemap_host)
    control = hosts_of(args.control)[0]
    pages = max(1, min(args.pages, PAGES_CAP))
    burst_n = max(1, min(args.burst_n, BURST_CAP))
    pause = max(1.0, args.pause)  # never faster than one a second per Board
    width = max(1, args.width or WIDTH)

    def left() -> float:
        return args.budget_s - (time.monotonic() - _started)

    findings = Findings(
        args.out,
        {
            "hosts": tests,
            "control": control,
            "pages": pages,
            "burst_n": burst_n,
            "width": width,
            "pause": pause,
            "arms": sorted(arms),
        },
    )
    say(
        f"probe: tests={tests} control={control} pages={pages} burst_n={burst_n} width={width}"
    )

    proxy = spare_egress.proxy_url() if arms & {"where", "warp"} else None
    rows: dict[str, list[tuple[str, str]]] = {}
    refused: dict[str, str] = {}

    def guarded(arm: str, need: float, fn: Callable[[], None]) -> None:
        if arm not in arms:
            return
        if left() < need:
            findings.record(
                "budget", arm, f"skipped, {left():.0f}s left of {args.budget_s:.0f}s"
            )
            say(f"== {arm}: skipped, {left():.0f}s left")
            return
        say(f"== {arm} ==")
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - one arm failing must not lose the others
            findings.record(arm, "error", failure_of(exc)["error"])
            say(f"{arm} failed: {failure_of(exc)['error']}")

    guarded("where", 0, lambda: arm_where(findings, proxy))
    everyone = list(dict.fromkeys([*tests, *sitemap_host, control]))
    guarded("sitemap", 30, lambda: arm_sitemap(findings, everyone, rows))
    control_url = rows[control][0][1] if rows.get(control) else None
    guarded(
        "sequential",
        120,
        lambda: arm_sequential(
            findings,
            tests,
            rows,
            pages,
            pause,
            control_url,
            min(pages * pause * 1.6, left() - 60),
            refused,
        ),
    )
    guarded(
        "burst",
        150,
        lambda: arm_burst(
            findings,
            "burst",
            proxy=None,
            rows=rows,
            control=control,
            tests=tests,
            burst_n=burst_n,
            width=width,
            left=left,
            control_url=control_url,
            refused=refused,
        ),
    )

    def warp() -> None:
        if proxy is None:
            findings.record("warp", "error", "no spare egress on this host")
            say("warp: no spare egress on this host")
            return
        arm_burst(
            findings,
            "warp",
            proxy=proxy,
            rows=rows,
            control=control,
            tests=tests,
            burst_n=burst_n,
            width=width,
            left=left,
            control_url=control_url,
            refused=refused,
        )

    guarded("warp", 120, warp)
    with findings.lock:
        findings.save_locked()
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(
            render_summary(findings.data, args.replica), encoding="utf-8"
        )
    say("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
