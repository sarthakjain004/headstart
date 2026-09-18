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
        self, *, live, stored, used, used_after=None, commits=5, live_after=None
    ):
        self._live = live
        self._stored = list(stored)
        self._used = used
        self._used_after = used if used_after is None else used_after
        self._commits = commits
        self._live_after = live_after
        self.deleted: list = []
        self.squashed = False
        self._reads = 0

    def repo_info(self, repo, repo_type=None, files_metadata=False):
        self._reads += 1
        # The second read is the post-reclaim one.
        sibs = (
            self._live
            if self._reads == 1 or self._live_after is None
            else self._live_after
        )
        used = self._used if self._reads == 1 else self._used_after
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
