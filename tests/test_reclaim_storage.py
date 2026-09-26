"""Tests for the HF storage reclaim (headstart.ingest.reclaim_storage, ADR-0168).

Shaped as the outage it exists to prevent. On 2026-09-18 three pipeline runs died on
``403 Private repository storage limit reached`` with 96.83 GB stored against 7.57 GB live: the
reclaim step squashed history every run, announced ``usedStorage falls as HF collects the
orphans``, and never re-read the number it was predicting. Squashing only makes blobs *eligible*
for HF's asynchronous collection; it frees nothing by itself.

So the properties under test are the three that were missing, plus the two that make deleting
blobs safe to do at all:

* a reclaim that does not move ``usedStorage`` must exit non-zero (the bug),
* a live blob is never in the delete set, asserted rather than filtered,
* a blob pushed seconds ago is left alone even when it looks orphaned,
* an empty live listing is a Hub failure, not permission to delete the dataset,
* live files shrinking across the reclaim is refused.

The Hub is faked at the ``HfApi`` seam — the only thing this module touches — because what is
under test is the decision, not the transport. Deliberately free of the heavy extras: nothing here
imports ``huggingface_hub``, so these run in CI where it is not installed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import headstart.ingest.reclaim_storage as rs

REPO = "imPoseidon/headstart-index"
_UNSET = object()  # `None` is a real usedStorage value, so it cannot mean "not given"
NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


def blob(
    oid: str,
    size: int,
    *,
    name: str = "data/embeddings/jobs/embeddings.f32",
    age_min=600,
):
    return SimpleNamespace(
        file_oid=oid,
        filename=name,
        size=size,
        pushed_at=NOW - timedelta(minutes=age_min),
    )


def sibling(sha: str | None, size: int, name: str = "f"):
    lfs = SimpleNamespace(sha256=sha, size=size) if sha else None
    return SimpleNamespace(rfilename=name, size=size, lfs=lfs)


class FakeHub:
    """Enough of ``HfApi`` to exercise the decision, and it records what was deleted."""

    def __init__(
        self,
        *,
        live,
        stored,
        used,
        used_after=_UNSET,
        commits=5,
        live_after=None,
        lag_reads=0,
    ):
        self._live = live
        self._stored = list(stored)
        self._used = used
        self._used_after = used if used_after is _UNSET else used_after
        self._commits = commits
        self._live_after = live_after
        # Measured live 2026-09-18: usedStorage keeps reporting the pre-delete figure for
        # 9-25s after the delete returns. `lag_reads` is how many post-delete reads still do.
        self._lag_reads = lag_reads
        self.deleted: list = []
        self.squashed = False
        self._reads = 0

    def repo_info(self, repo, repo_type=None, files_metadata=False):
        self._reads += 1
        # Read 1 is the pre-delete snapshot; the next `lag_reads` are the stale window.
        sibs = (
            self._live
            if self._reads == 1 or self._live_after is None
            else self._live_after
        )
        used = self._used if self._reads <= 1 + self._lag_reads else self._used_after
        return SimpleNamespace(siblings=sibs, used_storage=used)

    def list_lfs_files(self, repo, repo_type=None):
        return list(self._stored)

    def list_repo_commits(self, repo, repo_type=None):
        return [object()] * self._commits

    def super_squash_history(self, repo_id=None, repo_type=None):
        self.squashed = True

    def permanently_delete_lfs_files(self, repo, files, repo_type=None):
        self.deleted.extend(files)


def run(hub, **kw):
    """Poll window collapsed to nothing — no test should sit through the real 180s."""
    kw.setdefault("verify_timeout_s", 0.05)
    kw.setdefault("verify_interval_s", 0.0)
    return rs.reclaim(REPO, None, api=hub, **kw)


# --- selection ------------------------------------------------------------------------------


def test_orphans_excludes_live_blobs():
    live_blob, dead_blob = blob("live", 3_000_000_000), blob("dead", 3_000_000_000)
    got = rs.orphans(
        [live_blob, dead_blob], {"live"}, now=NOW, min_age=timedelta(minutes=45)
    )
    assert got == [dead_blob]


def test_orphans_leaves_a_freshly_pushed_blob_alone():
    """A concurrent writer's object can land between the listing and the delete."""
    fresh = blob("dead", 3_000_000_000, age_min=5)
    assert rs.orphans([fresh], set(), now=NOW, min_age=timedelta(minutes=45)) == []


def test_live_oids_ignores_non_lfs_files():
    assert rs.live_oids([sibling("a", 1), sibling(None, 1), sibling("b", 1)]) == {
        "a",
        "b",
    }


# --- the bug this module exists for --------------------------------------------------------


def test_exits_nonzero_when_usedstorage_does_not_fall():
    """The 2026-09-18 failure: blobs deleted, quota unmoved, step reported success anyway."""
    hub = FakeHub(
        live=[sibling("live", 7_570_000_000)],
        stored=[blob("live", 7_570_000_000), blob("dead", 89_260_000_000)],
        used=96_830_000_000,
        used_after=96_830_000_000,  # HF freed nothing
    )
    assert run(hub) == 1
    assert hub.deleted, "it must still have attempted the delete"


def test_reclaim_succeeds_and_reports_when_storage_falls():
    hub = FakeHub(
        live=[sibling("live", 7_570_000_000)],
        stored=[blob("live", 7_570_000_000), blob("dead", 89_260_000_000)],
        used=96_830_000_000,
        used_after=7_570_000_000,
    )
    assert run(hub) == 0
    assert [b.file_oid for b in hub.deleted] == ["dead"]
    assert hub.squashed


def test_reclaimed_is_the_bytes_deleted_not_a_lagging_counter_difference(caplog):
    """Run 36218633315: usedStorage read 16.13 GB against 16.59 GB stored, because the first read
    lags the upload the run just made, so `before - after` said 1.60 GB for a 2.06 GB delete. The
    line reports the delete and prints the store less the delete beside the counter."""
    hub = FakeHub(
        live=[sibling("live", 14_530_000_000)],
        stored=[blob("live", 14_530_000_000), blob("dead", 2_060_000_000)],
        used=16_130_000_000,
        used_after=14_530_000_000,
    )
    caplog.set_level("INFO")
    assert run(hub) == 0
    assert (
        "reclaimed 2.06 GB: usedStorage 16.13 GB -> 14.53 GB (stored less deleted 14.53 GB), "
        "live 14.53 GB intact across 1 file(s)"
    ) in [r.getMessage() for r in caplog.records]


# --- safety ---------------------------------------------------------------------------------


def test_empty_live_listing_is_refused_rather_than_deleting_everything():
    """A truncated listing makes every blob look orphaned. That must never authorise a delete."""
    hub = FakeHub(
        live=[],  # the Hub answered without siblings
        stored=[blob("a", 3_000_000_000), blob("b", 3_000_000_000)],
        used=96_830_000_000,
    )
    assert run(hub) == 1
    assert hub.deleted == []
    assert not hub.squashed


def test_refuses_when_live_files_shrank_across_the_reclaim():
    hub = FakeHub(
        live=[sibling("live", 7_570_000_000)],
        stored=[blob("live", 7_570_000_000), blob("dead", 89_260_000_000)],
        used=96_830_000_000,
        used_after=1_000_000_000,
        live_after=[sibling("live", 1_000_000)],  # live data lost
    )
    assert run(hub) == 1


def test_nothing_deleted_when_orphans_are_below_the_floor():
    hub = FakeHub(
        live=[sibling("live", 7_570_000_000)],
        stored=[blob("live", 7_570_000_000), blob("dead", 500_000_000)],
        used=8_070_000_000,
    )
    assert run(hub) == 0
    assert hub.deleted == []
    assert not hub.squashed, "a sub-floor run must not churn the branch either"


def test_a_repo_with_only_live_blobs_is_a_no_op():
    hub = FakeHub(
        live=[sibling("live", 7_570_000_000)],
        stored=[blob("live", 7_570_000_000)],
        used=7_570_000_000,
    )
    assert run(hub) == 0
    assert hub.deleted == []


def test_single_commit_repo_skips_the_squash_but_still_deletes():
    hub = FakeHub(
        live=[sibling("live", 7_570_000_000)],
        stored=[blob("live", 7_570_000_000), blob("dead", 89_260_000_000)],
        used=96_830_000_000,
        used_after=7_570_000_000,
        commits=1,
    )
    assert run(hub) == 0
    assert not hub.squashed
    assert [b.file_oid for b in hub.deleted] == ["dead"]


# --- the counter lags the delete (measured live 2026-09-18) --------------------------------


def test_waits_out_a_lagging_counter_instead_of_crying_wolf():
    """`usedStorage` still read the pre-delete figure at t+3.3s and t+9.1s, falling by t+24.5s.

    Reading it once, immediately, is how this check would fail every healthy reclaim — which is
    the same "announce what you never measured" bug the module exists to remove.
    """
    hub = FakeHub(
        live=[sibling("live", 7_570_000_000)],
        stored=[blob("live", 7_570_000_000), blob("dead", 89_260_000_000)],
        used=96_830_000_000,
        used_after=7_570_000_000,
        lag_reads=3,
    )
    assert run(hub, verify_timeout_s=5.0, verify_interval_s=0.0) == 0


def test_a_counter_that_never_moves_still_fails_after_the_timeout():
    hub = FakeHub(
        live=[sibling("live", 7_570_000_000)],
        stored=[blob("live", 7_570_000_000), blob("dead", 89_260_000_000)],
        used=96_830_000_000,
        used_after=96_830_000_000,
        lag_reads=99,
    )
    assert run(hub, verify_timeout_s=0.05, verify_interval_s=0.0) == 1


def test_an_unreported_usedstorage_is_not_read_as_zero():
    """`used_storage` comes back None when the Hub declines to report it. Coercing that to 0
    would make `0 >= 0` true and fail a reclaim that may well have worked."""
    hub = FakeHub(
        live=[sibling("live", 7_570_000_000)],
        stored=[blob("live", 7_570_000_000), blob("dead", 89_260_000_000)],
        used=None,
        used_after=None,
    )
    assert run(hub) == 0


def test_a_counter_that_stops_reporting_is_inconclusive_not_a_failure():
    """None after a number is no evidence that nothing moved.

    The symmetric case (`used_before is None`) is already treated as a pass, and erroring here
    would both cry wolf and emit `-> unknown`, which `fanout_merge.RECLAIM_NOOP` cannot match —
    a loud branch silent in the log.
    """
    hub = FakeHub(
        live=[sibling("live", 7_570_000_000)],
        stored=[blob("live", 7_570_000_000), blob("dead", 89_260_000_000)],
        used=96_830_000_000,
        used_after=None,
    )
    assert run(hub) == 0
    assert hub.deleted, "the delete itself still happened"


def test_the_live_set_assertion_fires_even_if_selection_is_wrong(monkeypatch):
    """The last line of defence, pinned.

    `orphans()` already excludes live blobs, so through the Hub seam alone this assert is
    unreachable and survives being deleted. It guards against a *future* selection bug, so the
    test has to inject one: make the planner hand back a live blob and require the abort.
    """
    live_blob = blob("live", 7_570_000_000)
    hub = FakeHub(
        live=[sibling("live", 7_570_000_000)],
        stored=[live_blob, blob("dead", 89_260_000_000)],
        used=96_830_000_000,
        used_after=7_570_000_000,
    )
    monkeypatch.setattr(rs, "orphans", lambda *a, **k: [live_blob])
    assert run(hub) == 1
    assert hub.deleted == [], "nothing may be deleted once the assertion trips"


def test_orphans_held_back_by_age_are_named(caplog):
    """`orphans` drops a young blob silently; the run says how much it left for a later one."""
    young = SimpleNamespace(
        file_oid="young", filename="f", size=2_000_000_000, pushed_at=datetime.now(UTC)
    )
    hub = FakeHub(
        live=[sibling("live", 1_000_000)],
        stored=[blob("live", 1_000_000), young],
        used=2_001_000_000,
    )
    with caplog.at_level("INFO", logger="headstart.ingest.reclaim_storage"):
        assert run(hub) == 0
    assert hub.deleted == []
    assert (
        "1 orphaned object(s), 2.00 GB held back as younger than 45 min" in caplog.text
    )


def test_a_counter_left_above_the_store_less_the_delete_is_said(caplog):
    """The counter fell, but not to what the store now holds: the gap is not read as freed."""
    hub = FakeHub(
        live=[sibling("live", 14_530_000_000)],
        stored=[blob("live", 14_530_000_000), blob("dead", 2_060_000_000)],
        used=16_130_000_000,
        used_after=15_500_000_000,
    )
    caplog.set_level("INFO")
    assert run(hub) == 0
    assert any(
        "still above the store less the delete (14.53 GB)" in r.getMessage()
        for r in caplog.records
        if r.levelname == "WARNING"
    )
