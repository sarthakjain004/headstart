"""How the discovery miners read public files over a slow, unreliable link.

Four rules, one place:

- A failed read is a reason to wait and ask again, never a finding. A timeout, a reset, a truncated
  body, a 408/425/429 or a 5xx is retried with a growing pause (a `Retry-After` is honoured up to
  `MAX_RETRY_AFTER`, the same 30 s `headstart.network.http` caps it at); any other HTTP error is
  permanent and raises at once. Only when the attempts are spent does a read raise, and nothing is
  recorded for the source it was reading. The miners never write a verdict: liveness is
  `check_liveness.py`'s, and it reads such failures as unknown.
- A cache file on disk is a finished read. So it is written in one step (`save_whole`).
- A big download (`download`) resumes with `curl -C -`, aborting a stalled transfer with
  `--speed-limit/--speed-time` instead of hanging on it, and prints its size as it grows. It
  resumes **only** a file whose remote is unchanged: the remote's ETag, Last-Modified and
  Content-Length are recorded beside the partial file (`{name}.remote`) and compared on the next
  call; anything else restarts from byte zero. `curl -C -` alone appends the new tail of a
  republished file to the old head (a 100-byte file republished as 160 bytes came back as the old
  100 plus the new 60), and a same-size republish stays stale.
- `download(resume=False)` is for a server that ignores `Range` or refuses HEAD (a generated
  export, such as JobTech's JobStream snapshot), or a file that is mutable at a fixed URL: a
  partial file is dropped and the transfer starts over.
"""

from __future__ import annotations

import http.client
import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

ATTEMPTS = 40  # bounds the resumes per big download
NO_RESUME_ATTEMPTS = 8  # a download that restarts from zero each time gets fewer
STALLED_ATTEMPTS = 8  # consecutive failed attempts that added no bytes
PROGRESS_EVERY = 30  # seconds between size lines of a running download
MAX_RETRY_AFTER = 30  # seconds; past this, waiting costs more than the read buys
UA = "HeadStart-discovery/0.1 (ATS board discovery)"

_CURL_RANGE_REFUSED = 33  # curl: the server answered a resume with a whole body


class FetchFailed(RuntimeError):
    """A read that will not succeed: a permanent HTTP error, or every attempt spent."""


def _retryable(status: int) -> bool:
    return status in (408, 425, 429) or 500 <= status <= 599


def save_whole(path: Path, data: bytes) -> None:
    """Write `data` to `path` in one step, so a crash never leaves a half-written file that a
    rerun would take for a finished read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_bytes(data)
    part.replace(path)


def fetch_bytes(url: str, attempts: int = 6, pause: int = 15) -> bytes:
    """One GET, identity encoding, retried on a transport failure, a truncated body or a
    retryable HTTP status. Raises `FetchFailed` on a permanent HTTP error or when every attempt
    failed: an unreachable source is an error to report, not an empty result."""
    for attempt in range(1, attempts + 1):
        wait = pause * attempt
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": UA, "Accept-Encoding": "identity"}
            )
            with urllib.request.urlopen(request, timeout=180) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if not _retryable(exc.code):
                raise FetchFailed(f"{url}: HTTP {exc.code}") from exc
            retry_after = exc.headers.get("Retry-After", "") if exc.headers else ""
            if retry_after.isdigit():
                wait = min(max(wait, int(retry_after)), MAX_RETRY_AFTER)
            print(
                f"  {url}: HTTP {exc.code} (try {attempt}/{attempts}, waiting {wait}s)",
                flush=True,
            )
        except (OSError, http.client.HTTPException) as exc:  # incl. IncompleteRead
            print(
                f"  {url}: {exc!r} (try {attempt}/{attempts}, waiting {wait}s)",
                flush=True,
            )
        time.sleep(wait)
    raise FetchFailed(f"{url} unreachable after {attempts} tries")


def fetch_cached(url: str, path: Path, refresh: bool = False) -> bytes:
    """`path`'s bytes if an earlier run saved them, else `fetch_bytes(url)` saved there first."""
    if refresh or not path.exists():
        save_whole(path, fetch_bytes(url))
    return path.read_bytes()


def _remote(url: str) -> dict[str, str | None] | None:
    """The remote's validators from a HEAD, or None when it cannot be asked or states none."""
    try:
        request = urllib.request.Request(
            url,
            method="HEAD",
            headers={"User-Agent": UA, "Accept-Encoding": "identity"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            found = {
                "etag": response.headers.get("ETag"),
                "last_modified": response.headers.get("Last-Modified"),
                "length": response.headers.get("Content-Length"),
            }
    except (OSError, http.client.HTTPException):
        return None
    return found if any(found.values()) else None


def _sidecar(dest: Path) -> Path:
    return dest.with_name(dest.name + ".remote")


def _size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def remove_download(dest: Path) -> None:
    """Delete a downloaded file and the validators recorded beside it."""
    dest.unlink(missing_ok=True)
    _sidecar(dest).unlink(missing_ok=True)


def _curl(
    url: str, dest: Path, resume: bool, if_range: str | None, total: str | None
) -> tuple[int, str]:
    """One curl run: `(exit code, HTTP status)`, printing the file's size every PROGRESS_EVERY s."""
    command = ["curl", "-sS", "-L", "--fail", "-w", "%{http_code}"]
    if resume:
        command += ["-C", "-"]
        if if_range and _size(dest):
            command += ["-H", f"If-Range: {if_range}"]
    command += [
        "--connect-timeout", "30", "--speed-limit", "20000", "--speed-time", "60",
        "-o", str(dest), url,
    ]  # fmt: skip
    process = subprocess.Popen(command, stdout=subprocess.PIPE, text=True)
    while True:
        try:
            out, _ = process.communicate(timeout=PROGRESS_EVERY)
            return process.returncode, out.strip()
        except subprocess.TimeoutExpired:
            of = f" of {int(total) / 1e6:.1f} MB" if total and total.isdigit() else ""
            print(f"  {dest.name}: {_size(dest) / 1e6:.1f} MB{of}", flush=True)


def download(url: str, dest: Path, resume: bool = True) -> None:
    """Fetch `url` into `dest`, retrying after every stall or drop until curl succeeds.

    With `resume`, a partial `dest` continues (`curl -C -`) only when the remote's validators still
    equal those recorded when it was started; otherwise it is deleted and fetched whole. A file
    that is already complete and unchanged answers the ranged request with HTTP 416, which curl
    reports as success. Raises `FetchFailed` on a permanent 4xx, after `STALLED_ATTEMPTS` failed
    attempts in a row that added no bytes, or when the attempts are spent."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    remote = _remote(url) if resume else None
    side = _sidecar(dest)
    if resume:
        recorded = None
        if dest.exists() and side.exists():
            try:
                recorded = json.loads(side.read_text(encoding="utf-8"))
            except ValueError:
                recorded = None
        if remote is None or recorded != {"url": url, **remote}:
            dest.unlink(missing_ok=True)
        if remote is None:
            side.unlink(missing_ok=True)
        else:
            side.write_text(json.dumps({"url": url, **remote}), encoding="utf-8")
    validator = (remote or {}).get("etag") or (remote or {}).get("last_modified")
    limit = ATTEMPTS if resume else NO_RESUME_ATTEMPTS
    idle = 0
    for attempt in range(1, limit + 1):
        if not resume:
            dest.unlink(missing_ok=True)
        before = _size(dest)
        code, status = _curl(url, dest, resume, validator, (remote or {}).get("length"))
        if code == 0:
            return
        if (
            status.isdigit()
            and 400 <= int(status) < 500
            and not _retryable(int(status))
        ):
            # judged by the status curl printed, not its exit code: the same 404 exits 22 from a
            # local server and 56 from raw.githubusercontent.com (measured 2026-09-30)
            raise FetchFailed(f"{url}: HTTP {status}")
        if code == _CURL_RANGE_REFUSED:
            dest.unlink(
                missing_ok=True
            )  # the remote no longer matches what this file began as
        idle = idle + 1 if _size(dest) <= before else 0
        if idle >= STALLED_ATTEMPTS:
            raise FetchFailed(f"{url}: {idle} failed attempts in a row added no bytes")
        print(f"  curl exit {code}, retry {attempt}/{limit}", flush=True)
        time.sleep(min(60, 5 * attempt))
    raise FetchFailed(f"could not finish {url}")
