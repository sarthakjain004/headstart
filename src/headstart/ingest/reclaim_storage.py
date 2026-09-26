#!/usr/bin/env python3
"""Free HF dataset storage by deleting orphaned LFS blobs, then prove the bytes actually went.

    python -m headstart.ingest.reclaim_storage

**The quota counts stored bytes, not reachable ones.** Git is content-addressed and deliberately
non-destructive: a commit's tree references a blob by hash, and LFS splits that further — the commit
holds a ~130-byte pointer file while the bytes live in a separate object store keyed by sha256.
Deleting a file, or rewriting every commit that ever mentioned it, removes only *pointers*. The blob
stays, because that is what makes history recoverable. Hugging Face bills on what it stores, so a
repo whose HEAD is 7.57 GB can legitimately cost 96.83 GB and both numbers are correct at once.

``super_squash_history`` — which is all this step used to do — collapses the branch to one commit
and therefore makes every prior blob *unreachable*. That makes them **eligible** for collection and
nothing more. Collection is asynchronous, batched, and entirely HF's to schedule; no API promises
when. On 2026-09-18 the pipeline hit ``403 Private repository storage limit reached`` on three runs
with 96.83 GB stored against 7.57 GB live — 89.26 GB of squashed-away blobs HF had not collected.
The reclaim step had reported success on every one of those runs, because it printed
``usedStorage falls as HF collects the orphans`` — a prediction — and never re-read the number.

The margin was one day wide and the old design did not know it. The run rewrites
``embeddings.f32`` and ``meta.jsonl`` **wholesale**. On 2026-09-21 that was 2.71 GB and 0.577 GB,
~3.31 GB of new blobs per run at ~38.6 runs/day: **~128 GB** of dead weight a day against a 100 GB
quota, a maximum tolerable collection lag of **~19h** for a process with no SLA and no signal.
Re-measured over runs 36200233818..36218633315 (2026-09-26), after ADR-0190 pruned the store:
1.64 GB and 0.36 GB, 2.06 GB deleted per run at ~27 runs/day (median start-to-start 54 min), so
~55 GB a day against the ~85 GB the quota leaves above ~15 GB live — a lag of ~37h, still no SLA.

So delete the blobs outright (:func:`~huggingface_hub.HfApi.permanently_delete_lfs_files`) rather
than asking for them to be collected, and **verify the number moved**. Three invariants, because
this call will happily delete a blob a live commit still points at and leave a dangling pointer:

* **Never delete a live blob.** The delete set is chosen by ``file_oid not in live_oids`` — where
  ``RepoSibling.lfs.sha256`` and ``LFSFileInfo.file_oid`` are the same identifier — and the
  intersection is asserted empty immediately before the call, not merely filtered.
* **Never race an in-flight upload.** A blob pushed in the last ``--min-age-minutes`` is left alone
  even when it looks orphaned, because a concurrent writer's object can land between the listing
  that defines "live" and the delete that acts on it.
* **Fail closed on an unreadable listing.** An empty live set against a non-empty store is the
  catastrophic case — it makes *every* blob look orphaned — so it is refused as a Hub failure
  rather than treated as a repo with nothing live in it. This is ``state_fetch``'s rule
  ("empty is a verdict, not an error") applied to the destructive direction.

Squash first, then delete: collapsing history to a single commit is what makes "not in HEAD" mean
"referenced by nothing", so the two steps compose into the procedure that was run by hand to
recover the outage.

Exit: 0 when storage is within budget (reclaimed, nothing to do, or the Hub declined to report
``usedStorage`` at all — no evidence is not evidence of failure), 1 when the reclaim ran and the
counter did **not** fall — the failure the old step could not see.
"""

from __future__ import annotations

import argparse
import os
import time
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol

from headstart import log

_log = log.get(__name__, __spec__)

# Leave anything this fresh alone: a blob pushed by a writer still running is not garbage, however
# orphaned it looks from here. The merge job's own uploads finish minutes before this step.
DEFAULT_MIN_AGE_MINUTES = 45

# Below this there is nothing worth a destructive call — one run's churn is ~3.3 GB, so this fires
# on essentially every run and the path stays continuously exercised rather than rotting until the
# one day it is needed.
DEFAULT_MIN_RECLAIM_GB = 1.0

# `usedStorage` does not update synchronously with the delete. Measured live 2026-09-18 against
# the real dataset, deleting 14 orphans worth 6.71 GB: the call returned in 2.8s and the counter
# still read the old 14.31 GB at t+3.3s and t+9.1s, falling to 7.60 GB by t+24.5s and staying
# there. So an immediate read sees the *pre-delete* figure and would fail every healthy reclaim.
# Poll instead, generously over the measured ~25s, and only call it a failure at the end.
VERIFY_TIMEOUT_S = 180.0
VERIFY_INTERVAL_S = 5.0
# How far above the store less the delete usedStorage may settle before that is said. On all
# seven runs 36200233818..36218633315 it settled exactly there (e.g. 16.59 - 2.06 = 14.53 GB).
_SETTLED_TOLERANCE = 0.01


class _Hub(Protocol):
    """The parts of ``huggingface_hub.HfApi`` this module calls.

    Named so the seam is typed rather than ``api=None`` meaning "anything": the tests drive the
    real :func:`reclaim` over a fake, and this says exactly what a fake has to provide.
    """

    def repo_info(self, repo_id: str, /, **kwargs): ...

    def list_lfs_files(self, repo_id: str, /, **kwargs): ...

    def list_repo_commits(self, repo_id: str, /, **kwargs): ...

    def super_squash_history(self, *, repo_id: str, **kwargs) -> None: ...

    def permanently_delete_lfs_files(
        self, repo_id: str, files, /, **kwargs
    ) -> None: ...


class _Blob(Protocol):
    """The parts of ``huggingface_hub.LFSFileInfo`` this module reads."""

    file_oid: str
    filename: str
    size: int
    pushed_at: datetime


def live_oids(siblings: Iterable[object]) -> set[str]:
    """The sha256 of every LFS object reachable from HEAD.

    ``RepoSibling.lfs`` is ``None`` for files small enough to live in git proper; those cost
    nothing and are not LFS objects, so they simply do not appear.
    """
    out = set()
    for s in siblings:
        lfs = getattr(s, "lfs", None)
        if lfs is not None and getattr(lfs, "sha256", None):
            out.add(lfs.sha256)
    return out


def orphans(
    stored: Sequence[_Blob],
    live: set[str],
    *,
    now: datetime,
    min_age: timedelta,
) -> list[_Blob]:
    """Stored blobs that HEAD does not reference and that are old enough to be safe to delete.

    Both conditions are load-bearing and neither implies the other: liveness decides *correctness*
    (deleting a live blob corrupts the repo) and age decides *concurrency* (a blob pushed seconds
    ago may belong to a writer whose commit this listing predates).
    """
    cutoff = now - min_age
    return [b for b in stored if b.file_oid not in live and b.pushed_at < cutoff]


def _gb(n: float | None) -> str:
    return "unknown" if n is None else f"{n / 1e9:.2f} GB"


def reclaim(
    repo: str,
    token: str | None,
    *,
    min_age_minutes: int = DEFAULT_MIN_AGE_MINUTES,
    min_reclaim_gb: float = DEFAULT_MIN_RECLAIM_GB,
    verify_timeout_s: float = VERIFY_TIMEOUT_S,
    verify_interval_s: float = VERIFY_INTERVAL_S,
    api: _Hub | None = None,
) -> int:
    """Squash, delete the orphaned blobs, and require ``usedStorage`` to have fallen."""
    if (
        api is None
    ):  # imported here so the module itself stays importable without the extra
        from huggingface_hub import HfApi

        api = HfApi(token=token)

    def read_usage() -> tuple[int | None, int, list]:
        """``used_storage`` stays ``None`` when the Hub declines to report it rather than being
        coerced to 0 — 0 reads as "nothing stored", which would fail the check below."""
        info = api.repo_info(repo, repo_type="dataset", files_metadata=True)
        siblings = list(info.siblings or [])
        return (
            info.used_storage,
            sum(s.lfs.size for s in siblings if getattr(s, "lfs", None)),
            siblings,
        )

    used_before, live_bytes, siblings = read_usage()
    stored = list(api.list_lfs_files(repo, repo_type="dataset"))
    stored_bytes = sum(b.size for b in stored)
    _log.info(
        f"usedStorage {_gb(used_before)} · live {_gb(live_bytes)} across {len(siblings)} file(s) "
        f"· stored {_gb(stored_bytes)} across {len(stored)} LFS object(s)"
    )

    live = live_oids(siblings)
    # The catastrophic case: a listing that came back empty makes every stored blob look orphaned.
    # Refuse it as a Hub failure — an actually-empty repo has nothing stored either.
    if stored and not live:
        _log.error(
            f"ABORT: {repo} lists {len(stored)} stored LFS object(s) but no live ones. That is a "
            "failed or truncated listing, not an empty repo, and acting on it would delete the "
            "entire dataset. Refusing."
        )
        return 1

    dead = orphans(
        stored,
        live,
        now=datetime.now(UTC),
        min_age=timedelta(minutes=min_age_minutes),
    )
    dead_bytes = sum(b.size for b in dead)
    # The age filter is silent inside `orphans`; say what it held back, or a blob that stays
    # orphaned run after run is indistinguishable from one a writer is still pushing.
    kept = live | {b.file_oid for b in dead}
    young = [b for b in stored if b.file_oid not in kept]
    if young:
        _log.info(
            f"{len(young)} orphaned object(s), {_gb(sum(b.size for b in young))} held back as "
            f"younger than {min_age_minutes} min"
        )
    if dead_bytes < min_reclaim_gb * 1e9:
        _log.info(
            f"{_gb(dead_bytes)} orphaned across {len(dead)} object(s) — under the "
            f"{min_reclaim_gb:g} GB floor, nothing to reclaim"
        )
        return 0

    by_file: dict[str, int] = {}
    for b in dead:
        by_file[b.filename] = by_file.get(b.filename, 0) + b.size
    for name, size in sorted(by_file.items(), key=lambda kv: -kv[1])[:10]:
        _log.info(f"  orphaned {_gb(size)}  {name}")

    # Squash first: collapsing history to one commit is what makes "absent from HEAD" mean
    # "referenced by nothing at all", so the delete below is unambiguous.
    commits = api.list_repo_commits(repo, repo_type="dataset")
    if len(commits) > 1:
        api.super_squash_history(repo_id=repo, repo_type="dataset")
        _log.info(f"squashed {len(commits)} commit(s) to 1")

    # Assert rather than trust the filter: this is the one call that can destroy the dataset.
    doomed = {b.file_oid for b in dead}
    if doomed & live:
        _log.error(
            f"ABORT: {len(doomed & live)} object(s) are both live and marked for deletion. "
            "Refusing."
        )
        return 1

    _log.info(f"deleting {len(dead)} orphaned object(s), {_gb(dead_bytes)}")
    api.permanently_delete_lfs_files(repo, dead, repo_type="dataset")

    # The whole point of this module: the old step announced a reclaim it never measured. But
    # the counter lags the delete (see VERIFY_TIMEOUT_S), so poll it rather than read once —
    # reading immediately is how this check would have cried wolf on every healthy run.
    started = time.monotonic()
    deadline = started + verify_timeout_s
    while True:
        used_after, live_after, siblings_after = read_usage()
        if used_before is None or (used_after is not None and used_after < used_before):
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(verify_interval_s)
    _log.info(f"verified after {time.monotonic() - started:.0f}s")

    if live_after < live_bytes:
        _log.error(
            f"live files shrank ({_gb(live_bytes)} -> {_gb(live_after)}) across the reclaim — "
            "investigate before the next run"
        )
        return 1
    if used_before is None or used_after is None:
        # No evidence either way. Erroring here would cry wolf exactly as the immediate read did,
        # and the message would carry "-> unknown", which `fanout_merge.RECLAIM_NOOP` cannot
        # match — a loud branch that is silent in the log is worse than a quiet one. The delete
        # itself succeeded; the next run re-checks, since this runs every run.
        _log.warning(
            f"reclaim verification inconclusive: the Hub reported usedStorage as "
            f"{_gb(used_before)} before and {_gb(used_after)} after, so whether the "
            f"{len(dead)} deleted object(s) freed anything cannot be told from here"
        )
        return 0
    if used_after >= used_before:
        _log.error(
            f"reclaim did not free anything: usedStorage {_gb(used_before)} -> {_gb(used_after)} "
            f"after deleting {len(dead)} object(s) worth {_gb(dead_bytes)}, and still had not "
            f"moved {verify_timeout_s:.0f}s later. The quota fills at ~55 GB/day, so this will "
            "reject uploads within two days if it is not fixed."
        )
        return 1
    # The bytes deleted, not `used_before - used_after`: the first read of usedStorage lags the
    # upload this run just made (16.13 GB against 16.59 GB stored on run 36218633315), so that
    # difference read 1.60 GB for the same 2.06 GB delete. What the counter should now show is
    # the store less the delete, printed beside it so the lag stays visible.
    expected = stored_bytes - dead_bytes
    if used_after > expected * (1 + _SETTLED_TOLERANCE):
        # The counter fell, but not yet to what the store now holds: HF has not released all of
        # the delete yet, or something else is stored. Said, so the gap is not read as freed.
        _log.warning(
            f"usedStorage {_gb(used_after)} is still above the store less the delete "
            f"({_gb(expected)}) — the Hub has not released all {_gb(dead_bytes)} yet"
        )
    _log.info(
        f"reclaimed {_gb(dead_bytes)}: usedStorage {_gb(used_before)} -> "
        f"{_gb(used_after)} (stored less deleted {_gb(expected)}), "
        f"live {_gb(live_after)} intact across {len(siblings_after)} file(s)"
    )
    return 0


def main() -> int:
    log.setup()
    log.context("reclaim_storage")
    ap = argparse.ArgumentParser(
        description="Delete orphaned LFS blobs from the HF dataset."
    )
    ap.add_argument(
        "--min-age-minutes",
        type=int,
        default=DEFAULT_MIN_AGE_MINUTES,
        help=f"leave blobs pushed this recently alone (default {DEFAULT_MIN_AGE_MINUTES})",
    )
    ap.add_argument(
        "--min-reclaim-gb",
        type=float,
        default=DEFAULT_MIN_RECLAIM_GB,
        help=f"skip when less than this is orphaned (default {DEFAULT_MIN_RECLAIM_GB})",
    )
    args = ap.parse_args()
    repo = os.environ.get("HF_DATASET")
    if not repo:
        ap.error("no dataset repo — set HF_DATASET")
    return reclaim(
        repo,
        os.environ.get("HF_TOKEN"),
        min_age_minutes=args.min_age_minutes,
        min_reclaim_gb=args.min_reclaim_gb,
    )


if __name__ == "__main__":
    log.run_logging_crash(
        _log,
        main,
        "reclaim_storage failed — orphaned blobs were not deleted this run; usedStorage "
        "keeps growing until a later run reclaims",
    )
