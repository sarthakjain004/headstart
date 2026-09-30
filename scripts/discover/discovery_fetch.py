"""How the discovery miners read public files over a slow, unreliable link.

Three rules, one place:

- A failed read is a reason to wait and ask again, never a finding. A timeout, a reset, a 429 or a
  5xx is retried with a growing pause (a 429's `Retry-After` is honoured); only when the attempts
  are spent does the read raise, and nothing is recorded for the source it was reading. The miners
  never write a verdict: liveness is `check_liveness.py`'s, and it reads such failures as unknown.
- A cache file on disk is a finished read. So it is written in one step (`save_whole`), and a big
  download resumes (`download`, `curl -C -`, aborting a stalled transfer with
  `--speed-limit/--speed-time` instead of hanging on it) rather than restarting.
- `download(resume=False)` is for a server that ignores `Range` (a generated export, such as
  JobTech's JobStream snapshot): a partial file is dropped and the transfer starts over.
"""

from __future__ import annotations

import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

ATTEMPTS = 40  # bounds the resumes per big download
UA = "HeadStart-discovery/0.1 (ATS board discovery)"


def save_whole(path: Path, data: bytes) -> None:
    """Write `data` to `path` in one step, so a crash never leaves a half-written file that a
    rerun would take for a finished read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_bytes(data)
    part.replace(path)


def fetch_bytes(url: str, attempts: int = 6, pause: int = 15) -> bytes:
    """One GET, identity encoding, retried on any transport failure or HTTP error.

    Raises `RuntimeError` when every attempt failed: an unreachable source is an error to report,
    not an empty result."""
    for attempt in range(1, attempts + 1):
        wait = pause * attempt
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": UA, "Accept-Encoding": "identity"}
            )
            with urllib.request.urlopen(request, timeout=180) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            retry_after = exc.headers.get("Retry-After", "") if exc.headers else ""
            wait = max(wait, int(retry_after)) if retry_after.isdigit() else wait
            print(
                f"  {url}: HTTP {exc.code} (try {attempt}/{attempts}, waiting {wait}s)",
                flush=True,
            )
        except OSError as exc:
            print(
                f"  {url}: {exc} (try {attempt}/{attempts}, waiting {wait}s)",
                flush=True,
            )
        time.sleep(wait)
    raise RuntimeError(f"{url} unreachable after {attempts} tries")


def fetch_cached(url: str, path: Path, refresh: bool = False) -> bytes:
    """`path`'s bytes if an earlier run saved them, else `fetch_bytes(url)` saved there first."""
    if refresh or not path.exists():
        save_whole(path, fetch_bytes(url))
    return path.read_bytes()


def download(url: str, dest: Path, resume: bool = True) -> None:
    """Fetch `url` into `dest`, retrying after every stall or drop until curl succeeds.

    With `resume`, `-C -` continues a partial file, and a file that is already complete answers
    the ranged request with HTTP 416, which curl reports as success."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, ATTEMPTS + 1):
        if not resume:
            dest.unlink(missing_ok=True)
        done = subprocess.run(
            [
                "curl", "-sS", "-L", "--fail", *(["-C", "-"] if resume else []),
                "--connect-timeout", "30", "--speed-limit", "20000", "--speed-time", "60",
                "-o", str(dest), url,
            ],
            check=False,
        )  # fmt: skip
        if done.returncode == 0:
            return
        print(f"  curl exit {done.returncode}, retry {attempt}/{ATTEMPTS}", flush=True)
        time.sleep(min(60, 5 * attempt))
    raise RuntimeError(f"could not finish {url}")
