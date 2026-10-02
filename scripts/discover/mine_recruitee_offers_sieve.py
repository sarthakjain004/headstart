#!/usr/bin/env python3
"""Sieve a list of candidate labels through Recruitee's offers API and stage the ones that are Boards.

`*.recruitee.com` is a wildcard: an invented label resolves in DNS, holds the same certificate and
answers HTTP, so neither DNS nor certificates say which labels are tenants. The offers API does. The
scraper's own request, `https://{label}.recruitee.com/api/offers/`, answers a 404 for a label that is no
tenant and a 200 `{"offers": [...]}` for one that is, even when it holds no offers. That is the oracle
this script asks, once per label.

For each label in LABELS_FILE (one per line) it:
  * skips a label the ledger already holds, decided through `RecruiteeScraper.slug_from(...).lower()`,
    the identity `scrapable_boards` uses, and a label an earlier run already settled;
  * asks the API at no more than 4 workers (Recruitee rate-limits per address: 24 concurrent requests drew
    37 x 429 of 48, ADR-0301), follows redirects, and reads the verdict from the answer:
      live         200 with an `offers` list, served on the label's own host      -> staged
      absent       404: no such tenant                                            -> nothing written
      moved        the offers API redirects to another label (a renamed account,
                   or a throwaway phishing tenant with 0 offers)                  -> not staged
      unreachable  429, 5xx, a timeout, a network failure, a 403 (`Public API
                   disabled`: the tenant exists but the scraper cannot read it), or a 200 with no
                   `offers` list                                                  -> retried on the next run
    A 429, a timeout or a redirect is never read as dead and never as live, and only `live` is staged, so
    the sieve adds Live rows and never a `dead` one;
  * appends every verdict to RESULTS_FILE and every staged label to `data/wayback-ats/recruitee.csv`
    (`ats,tenant,url`, the shape `check_liveness.py --dir data/wayback-ats` reads), flushing each row as it
    lands. Re-running the same command resumes: settled labels are skipped, `unreachable` ones retried.
    Exit 0 when every label settled, 1 when any is still unreachable, 2 for a usage error.

Where the label lists came from, 2026-09-29/30, and what each returned live:
  * Tranco `.nl`/`.be` domains, `label.tld` only, in rank order: 1.4% live in the first 1,480 labels, about
    0.35% over all 20,013, and about 0.1% by rank 10k. Rank order matters: popular domains hit far more.
  * Tranco `.de`/`.at`/`.ch`, top 2,730 by rank: 0.59% live. Untargeted Tranco top-200k, sampled: 0.20%.
    Indeed employer labels, sampled: 0.10%. Stop a list whose yield falls under about 0.05%.
  * The redirect targets of held labels, from a `dedupe_boards.py --ats recruitee` scan (a renamed
    account's old label 302s to its new one): 50 of 50 live, the best list there is.
  * The candidates of `mine_recruitee_otx_urlscan.py`.

Before landing, read the staged tenants' offers: a fresh account carries a "Senior Marketer (Sample)" offer,
and an account holding only that, or only "TEST ..." offers, is a demo (`EXCLUDED_BOARDS`). Then:
  1. `python scripts/validate/check_liveness.py --dir data/wayback-ats recruitee`
  2. `python scripts/validate/dedupe_boards.py --ats recruitee --workers 4 --apply`, applied only when its
     summary shows no `unreachable` (ADR-0301): a renamed account's old label is often held, and both
     labels would otherwise serve every posting twice.

Run:  python -u scripts/discover/mine_recruitee_offers_sieve.py LABELS_FILE [RESULTS_FILE]
        [--workers 4] [--gap 0.25] [--control LABEL]
      `--control` names a Live row with offers, re-read every 25 verdicts: if it stops reading live the
      run stops with exit 3, since every later verdict would be suspect.
"""

import csv
import json
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from headstart.scrapers.recruitee import RecruiteeScraper  # needs src on sys.path first

LEDGER = ROOT / "data" / "validate" / "liveness" / "recruitee.csv"
STAGING = ROOT / "data" / "wayback-ats" / "recruitee.csv"
DEFAULT_RESULTS = ROOT / "data" / "wayback-ats" / "recruitee_sieve_results.jsonl"
MAX_WORKERS = 4  # Recruitee rate-limits per address
SETTLED = (
    "live",
    "absent",
    "moved",
)  # an `unreachable` verdict is retried on the next run
VALID_LABEL = re.compile(r"[a-z0-9][a-z0-9-]*")
CONTROL_EVERY = 25
ATTEMPTS = 3

# What one request came back with: HTTP status ("000" = no answer), the host it landed on, the body
# and the Retry-After seconds it named (0 = none).
Fetch = Callable[[str], tuple[str, str, str, int]]


def offers_url(label: str) -> str:
    return f"https://{label}.recruitee.com/api/offers/"


def fetch(label: str) -> tuple[str, str, str, int]:
    """One GET of the label's offers API through `curl -m`, so the timeout covers DNS."""
    with tempfile.NamedTemporaryFile("r", encoding="utf-8") as headers:
        proc = subprocess.run(
            [
                "curl",
                "-sS",
                "-L",
                "-m",
                "60",
                "-D",
                headers.name,
                "-w",
                "\n%{http_code} %{url_effective}",
                offers_url(label),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        retry_after = 0
        for line in headers.read().splitlines():
            if line.lower().startswith("retry-after:"):
                try:
                    retry_after = int(float(line.split(":", 1)[1].strip()))
                except ValueError:
                    pass
    body, _, tail = proc.stdout.rpartition("\n")
    code, _, landed = tail.partition(" ")
    host = landed.split("//", 1)[-1].split("/", 1)[0].lower()
    return code or "000", host, body, retry_after


def classify(label: str, status: str, landed_host: str, body: str) -> dict:
    """The verdict for one answer: {status, offers, note}. Pure, so it is testable without a network."""
    if status == "200":
        try:
            offers = json.loads(body).get("offers")
        except (json.JSONDecodeError, AttributeError):
            offers = None
        if not isinstance(offers, list):
            return {
                "status": "unreachable",
                "offers": None,
                "note": "200 without an offers list",
            }
        if landed_host != f"{label}.recruitee.com":
            return {
                "status": "moved",
                "offers": len(offers),
                "note": f"lands on {landed_host}",
            }
        return {"status": "live", "offers": len(offers), "note": "200"}
    if status == "404":
        return {"status": "absent", "offers": None, "note": "404"}
    return {"status": "unreachable", "offers": None, "note": f"http {status}"}


def verify(
    label: str, fetcher: Fetch = fetch, sleep: Callable[[float], None] = time.sleep
) -> dict:
    """The label's verdict, retrying an unreachable answer up to `ATTEMPTS` times.

    Waits the Retry-After a 429 names (else 4 s x the attempt), capped at 60 s. A 403 is not retried:
    `Public API disabled` is the tenant's own setting and does not change between two requests."""
    verdict = {"status": "unreachable", "offers": None, "note": "not tried"}
    for attempt in range(1, ATTEMPTS + 1):
        status, landed, body, retry_after = fetcher(label)
        verdict = classify(label, status, landed, body)
        if verdict["status"] != "unreachable" or status == "403":
            break
        if attempt < ATTEMPTS:
            sleep(min(max(retry_after, 4 * attempt), 60))
    return {
        "label": label,
        **verdict,
        "checked_at": datetime.now(UTC).date().isoformat(),
    }


def held(ledger: Path = LEDGER) -> set[str]:
    """The Board identities the ledger holds: the scraper's slug, lowercased."""
    with ledger.open(encoding="utf-8") as f:
        return {
            RecruiteeScraper.slug_from(r["tenant"], r["url"]).lower()
            for r in csv.DictReader(f)
        }


def to_probe(lines: list[str], have: set[str], settled: set[str]) -> list[str]:
    """The labels to ask about: valid, lowercased, deduped in order, neither held nor already settled."""
    seen: set[str] = set()
    out = []
    for line in lines:
        label = line.strip().lower()
        if (
            not VALID_LABEL.fullmatch(label)
            or label in seen
            or label in have
            or label in settled
        ):
            continue
        seen.add(label)
        out.append(label)
    return out


def settled_labels(results: Path) -> set[str]:
    """The labels an earlier run settled (live, absent or moved); unreachable ones are asked again."""
    if not results.exists():
        return set()
    with results.open(encoding="utf-8") as f:
        rows = (json.loads(line) for line in f if line.strip())
        return {r["label"] for r in rows if r["status"] in SETTLED}


def staged_labels(staging: Path) -> set[str]:
    if not staging.exists():
        return set()
    with staging.open(newline="", encoding="utf-8") as f:
        return {r["tenant"].lower() for r in csv.DictReader(f)}


class Pacer:
    """One request start per `gap` seconds across every worker."""

    def __init__(self, gap: float):
        self.gap = gap
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            pause = self._last + self.gap - time.monotonic()
            if pause > 0:
                time.sleep(pause)
            self._last = time.monotonic()


def run(
    labels: list[str],
    *,
    results_path: Path,
    staging_path: Path,
    fetcher: Fetch = fetch,
    workers: int = MAX_WORKERS,
    gap: float = 0.25,
    control: str | None = None,
    have: set[str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Sieve `labels`; 0 when all settled, 1 when any stays unreachable, 3 when the control failed."""
    have = held() if have is None else have
    todo = to_probe(labels, have, settled_labels(results_path))
    already_staged = staged_labels(staging_path)
    print(f"{len(labels)} candidates, {len(todo)} to ask", flush=True)
    pacer = Pacer(gap)

    def paced(label: str) -> tuple[str, str, str, int]:
        pacer.wait()
        return fetcher(label)

    def ask(label: str) -> dict:
        return verify(label, paced, sleep)

    staging_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.parent.mkdir(parents=True, exist_ok=True)
    tally = {"live": 0, "absent": 0, "moved": 0, "unreachable": 0}
    needs_header = not staging_path.exists() or staging_path.stat().st_size == 0
    with (
        results_path.open("a", encoding="utf-8") as results,
        staging_path.open("a", newline="", encoding="utf-8") as staging,
        ThreadPoolExecutor(max_workers=min(workers, MAX_WORKERS)) as pool,
    ):
        writer = csv.writer(staging)
        if needs_header:
            writer.writerow(["ats", "tenant", "url"])
            staging.flush()
        futures = [pool.submit(ask, label) for label in todo]
        try:
            for done, future in enumerate(as_completed(futures), start=1):
                verdict = future.result()
                tally[verdict["status"]] += 1
                results.write(json.dumps(verdict) + "\n")
                results.flush()
                label = verdict["label"]
                if verdict["status"] == "live" and label not in already_staged:
                    writer.writerow(
                        ["recruitee", label, f"https://{label}.recruitee.com"]
                    )
                    staging.flush()
                    already_staged.add(label)
                    print(
                        f"[{done}/{len(todo)}] {label}: live, {verdict['offers']} offers | {tally}",
                        flush=True,
                    )
                elif done % 100 == 0:
                    print(
                        f"[{done}/{len(todo)}] {label}: {verdict['status']} | {tally}",
                        flush=True,
                    )
                if control and done % CONTROL_EVERY == 0:
                    check = verify(control, paced, sleep)
                    if check["status"] != "live":
                        print(
                            f"control {control} read {check['status']} ({check['note']}): stopping, resume later",
                            flush=True,
                        )
                        for pending in futures:
                            pending.cancel()
                        return 3
        finally:
            for pending in futures:
                pending.cancel()
    print(f"finished {tally}", flush=True)
    return 1 if tally["unreachable"] else 0


def main(argv: list[str]) -> int:
    args = argv[1:]
    options = {"--workers": str(MAX_WORKERS), "--gap": "0.25", "--control": ""}
    positional = []
    it = iter(args)
    for arg in it:
        if arg in options:
            options[arg] = next(it, "")
        else:
            positional.append(arg)
    if not positional or len(positional) > 2:
        print(__doc__)
        return 2
    lines = Path(positional[0]).read_text(encoding="utf-8").splitlines()
    results = Path(positional[1]) if len(positional) > 1 else DEFAULT_RESULTS
    return run(
        lines,
        results_path=results,
        staging_path=STAGING,
        workers=int(options["--workers"]),
        gap=float(options["--gap"]),
        control=options["--control"] or None,
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv))
