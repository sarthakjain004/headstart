#!/usr/bin/env python3
"""Pull the served LanceDB table out of the HF dataset — cancellable and resumable.

Safe to Ctrl-C at any point and re-run: every run recomputes what is missing from what is on
disk, so it costs only the bytes not yet landed. Re-running after a clean finish is a no-op.

Why this exists rather than ``snapshot_download``, and why big files are fetched as concurrent
ranged chunks: **ADR-0085**. The two operational facts worth having here:

* ``huggingface_hub`` failed four separate ways on this transfer and only the resolve endpoint
  was ever healthy, so this uses raw HTTP and nothing else;
* **when a transfer here is slow, add flows (``--workers``), never timeout.** Measured
  mid-transfer: one long-lived stream at 0.16 MB/s against 2.17 MB/s aggregated across four
  concurrent ranged GETs. Raising ``HF_HUB_DOWNLOAD_TIMEOUT`` to 300s made the hangs *longer*.

Run:  python scripts/fetch/pull_lancedb.py
      python scripts/fetch/pull_lancedb.py --workers 4     # gentler, if 429s show up
      python scripts/fetch/pull_lancedb.py --check         # report what's missing, fetch nothing
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")  # never Xet — see module docstring

import requests
from huggingface_hub import HfApi, get_token, hf_hub_url

REPO_ID = "imPoseidon/headstart-index"
PREFIX = "data/lancedb/"
BIG_FILE_BYTES = 100_000_000  # at or above this, use the ranged/resumable path

# ADR-0002 hands out one HTTP session per thread rather than sharing one across a pool. Its
# stated reason is curl_cffi-specific, but requests.Session is likewise not documented
# thread-safe, and every fetch here runs under a ThreadPoolExecutor — so this follows the repo's
# shape rather than relying on urllib3's pool happening to tolerate the sharing.
_local = threading.local()


def _session() -> requests.Session:
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
    return _local.s


def remote_files() -> list[tuple[str, int]]:
    sib = HfApi().repo_info(REPO_ID, repo_type="dataset", files_metadata=True).siblings
    return [(s.rfilename, s.size or 0) for s in sib if s.rfilename.startswith(PREFIX)]


def fetch_small(root: pathlib.Path, path: str, size: int) -> str | None:
    """Whole-body GET, written .tmp then renamed.

    The rename is the point: a run killed mid-write leaves a .tmp, never a short file at the
    real path — which the next run's ``exists()`` would otherwise count as landed and skip,
    silently corrupting the table with a truncated manifest.
    """
    dest = root / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    url = hf_hub_url(REPO_ID, path, repo_type="dataset")
    auth = {"Authorization": f"Bearer {get_token()}"}
    for attempt in range(6):
        try:
            r = _session().get(
                url, headers=auth, timeout=(15, 45), allow_redirects=True
            )
            if r.status_code == 429:
                raise requests.HTTPError("429", response=r)
            r.raise_for_status()
            if size and len(r.content) != size:
                raise OSError(f"short body {len(r.content)} != {size}")
            with open(tmp, "wb") as fh:
                fh.write(r.content)
                fh.flush()
                os.fsync(fh.fileno())
            tmp.rename(dest)
            return None
        except (requests.RequestException, OSError) as exc:
            if attempt == 5:
                tmp.unlink(missing_ok=True)
                return f"{path}: {type(exc).__name__}"
            # 120 of 2,834 files failed at 8 workers with a 1.5s-step backoff and all fetched
            # fine on retry, so these are rate-limit rejections, not absent files. Honour
            # Retry-After when the host sends one; otherwise back off hard enough to matter.
            wait = 2.0 * (attempt + 1)
            resp = getattr(exc, "response", None)
            if resp is not None and resp.headers.get("Retry-After", "").isdigit():
                wait = max(wait, int(resp.headers["Retry-After"]))
            time.sleep(wait)


def _chunks(size: int, chunk: int) -> list[tuple[int, int, int]]:
    """``(index, start, end_exclusive)`` at ABSOLUTE offsets — never relative to progress.

    Absolute boundaries are what make a cancelled run resumable: chunk *i* always covers the
    same byte range, so a chunk file's own length is enough to say how much of it is done,
    with no manifest to write, corrupt, or fall out of sync with the disk.
    """
    return [
        (i, off, min(off + chunk, size)) for i, off in enumerate(range(0, size, chunk))
    ]


def _chunk_path(dest: pathlib.Path, i: int) -> pathlib.Path:
    """Where chunk *i* of ``dest`` lives while it is being fetched."""
    return dest.with_name(f"{dest.name}.c{i:04d}")


def _on_disk(dest: pathlib.Path, plan: list[tuple[int, int, int]]) -> int:
    """Bytes of ``dest`` currently held across its chunk files."""
    return sum(
        f.stat().st_size
        for f in (_chunk_path(dest, i) for i, _, _ in plan)
        if f.exists()
    )


def _recover_partial_concat(part: pathlib.Path, dest: pathlib.Path, chunk: int) -> None:
    """Carve a whole-file .part back into per-chunk files.

    This is the recovery path for a kill during CONCATENATION, which is the one window where
    progress lives in two shapes at once: the concat loop appends each chunk to .part and
    deletes it, so an interrupt leaves a .part holding chunks 0..k and chunk files for k+1..n.
    Carving .part back into chunks 0..k restores one consistent shape and costs no refetch —
    verified by simulating that interrupt on a 4-chunk file and rebuilding it byte-exact.

    It relies on the chunk size being unchanged between runs, since the carve uses absolute
    boundaries; ``--chunk-mb`` is therefore not safe to change mid-transfer (see main()).
    """
    total = part.stat().st_size
    print(f"  recovering {total / 1e6:,.0f} MB from an interrupted concat", flush=True)
    with open(part, "rb") as src:
        i = 0
        while True:
            buf = src.read(chunk)
            if not buf:
                break
            cf = _chunk_path(dest, i)
            if not cf.exists() or cf.stat().st_size < len(buf):
                cf.write_bytes(buf)
            i += 1
    part.unlink()


def fetch_big(
    root: pathlib.Path, path: str, size: int, workers: int, chunk: int
) -> None:
    """Fetch one large file as CONCURRENT ranged chunks, then concatenate.

    Measured on this link 2026-08-25, mid-transfer: a single long-lived stream had decayed to
    **0.16 MB/s** while four concurrent ranged GETs of the same file aggregated **2.17 MB/s** -
    ~13x, with each individual segment slower than the aggregate. That shape (short flows fast,
    one long flow slow) is per-flow shaping and TCP congestion-window behaviour, not a saturated
    last mile, so the fix is more flows rather than a better single one. The first 50 MB of the
    sequential run came down at 6.36 MB/s and decayed monotonically from there, which is the
    same story seen from the other side.
    """
    dest = root / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    joined = dest.with_name(dest.name + ".part")
    url = hf_hub_url(REPO_ID, path, repo_type="dataset")
    auth = {"Authorization": f"Bearer {get_token()}"}
    print(
        f"big: {path}  ({size / 1e6:,.0f} MB, {chunk / 1e6:.0f} MB chunks)", flush=True
    )
    if joined.exists():
        _recover_partial_concat(joined, dest, chunk)

    plan = _chunks(size, chunk)

    # Chunk boundaries are absolute, so a resume with a different --chunk-mb would carve the
    # same bytes at different offsets and silently assemble a corrupt file. Refuse instead: any
    # complete non-final chunk on disk must be exactly `chunk` bytes.
    for i, lo, hi in plan[:-1]:
        cf = _chunk_path(dest, i)
        if cf.exists() and cf.stat().st_size not in (0, hi - lo):
            raise SystemExit(
                f"chunk {i} is {cf.stat().st_size:,} bytes but --chunk-mb implies {hi - lo:,}. "
                f"A part-finished transfer cannot change chunk size; re-run without --chunk-mb, "
                f"or delete {dest.name}.c* to start the file over."
            )

    def fetch_chunk(spec: tuple[int, int, int]) -> int:
        i, lo, hi = spec
        cf = _chunk_path(dest, i)
        want = hi - lo
        for attempt in range(8):
            have = cf.stat().st_size if cf.exists() else 0
            # Return as soon as the chunk is whole. Falling through to the next iteration to
            # discover that instead made every success return 0, so the progress line printed
            # "0/11 chunks" for an entire transfer that completed.
            if have >= want:
                return 1
            try:
                r = _session().get(
                    url,
                    headers=dict(auth, Range=f"bytes={lo + have}-{hi - 1}"),
                    stream=True,
                    timeout=(20, 60),
                    allow_redirects=True,
                )
                with r:
                    r.raise_for_status()
                    with open(cf, "ab") as fh:
                        fh.writelines(r.iter_content(1 << 20))
                        fh.flush()
                        os.fsync(fh.fileno())
            except (requests.RequestException, OSError):
                time.sleep(1.5 * (attempt + 1))
        raise OSError(
            f"chunk {i} short after 8 attempts: {cf.stat().st_size:,} < {want:,}"
        )

    todo = [
        c
        for c in plan
        if not (d := _chunk_path(dest, c[0])).exists() or d.stat().st_size < c[2] - c[1]
    ]
    print(f"  {len(plan) - len(todo)}/{len(plan)} chunks already complete", flush=True)
    # Bytes already on disk are excluded from the rate: counting adopted bytes against
    # fetch-only elapsed time printed "140 MB/s" on a 2 MB/s link. A rate that does not
    # reconcile with the transfer it describes is worse than printing none.
    t0, fetched = time.time(), 0
    base = _on_disk(dest, plan)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for fut in as_completed([pool.submit(fetch_chunk, c) for c in todo]):
            fetched += fut.result()
            got = _on_disk(dest, plan)
            print(
                f"      {got / 1e6:,.0f}/{size / 1e6:,.0f} MB ({100 * got / size:.1f}%)  "
                f"{fetched}/{len(todo)} chunks  "
                f"{(got - base) / max(time.time() - t0, 1) / 1e6:.2f} MB/s",
                flush=True,
            )

    # Concatenate in order, deleting each chunk as it lands so peak disk stays near one copy.
    # same path _recover_partial_concat reads on the next run if this loop is interrupted
    with open(joined, "wb") as fh:
        for i, _, _ in plan:
            cf = _chunk_path(dest, i)
            fh.write(cf.read_bytes())
            cf.unlink()
        fh.flush()
        os.fsync(fh.fileno())
    landed = joined.stat().st_size
    if landed != size:
        # Never rename a wrong-sized file into place: LanceDB would fail on it much later with
        # a decode error that says nothing about the download.
        raise SystemExit(f"SIZE MISMATCH {path}: {landed:,} on disk != {size:,} remote")
    joined.rename(dest)
    print(f"  complete: {path}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument(
        "--chunk-mb", type=int, default=32, help="ranged chunk size for big files"
    )
    ap.add_argument(
        "--check", action="store_true", help="report what's missing, fetch nothing"
    )
    args = ap.parse_args()

    root = pathlib.Path(".")  # the data/ tree hangs off the repo root; run from there
    remote = remote_files()
    missing = [(p, sz) for p, sz in remote if not (root / p).exists()]
    have_bytes = sum(sz for p, sz in remote if (root / p).exists())
    miss_bytes = sum(sz for _, sz in missing)
    print(
        f"remote  {len(remote):,} files, {(have_bytes + miss_bytes) / 1e6:,.0f} MB",
        flush=True,
    )
    print(f"missing {len(missing):,} files, {miss_bytes / 1e6:,.0f} MB", flush=True)
    if args.check or not missing:
        for p, sz in sorted(missing, key=lambda r: -r[1])[:5]:
            print(f"  {sz / 1e6:9,.0f} MB  {p}", flush=True)
        print(
            "nothing to do" if not missing else "(--check: fetched nothing)", flush=True
        )
        return 0

    small = [(p, sz) for p, sz in missing if sz < BIG_FILE_BYTES]
    big = [(p, sz) for p, sz in missing if sz >= BIG_FILE_BYTES]

    t0, done, failed = time.time(), 0, []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(fetch_small, root, p, sz) for p, sz in small]
        for fut in as_completed(futures):
            err = fut.result()
            if err:
                failed.append(err)
                print(f"  FAIL {err}", flush=True)
            else:
                done += 1
            if (done + len(failed)) % 300 == 0:
                print(
                    f"  small {done + len(failed):,}/{len(small):,}  "
                    f"({time.time() - t0:.0f}s)",
                    flush=True,
                )
    print(
        f"small: {done:,} ok, {len(failed)} failed in {time.time() - t0:.0f}s",
        flush=True,
    )

    for path, size in big:
        fetch_big(root, path, size, args.workers, args.chunk_mb * 1_000_000)

    still = [p for p, _ in remote if not (root / p).exists()]
    if still:
        print(
            f"INCOMPLETE — {len(still):,} still missing; re-run to pick them up",
            flush=True,
        )
        return 1
    print("DONE — table complete", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\ncancelled — re-run the same command to resume", flush=True)
        sys.exit(130)
