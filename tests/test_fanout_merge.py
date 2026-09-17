"""`fanout_merge`'s id-batch parser — specifically, the ATS vocabulary it splits ids on.

The per-ATS churn table is only as good as the boundary it splits `[index] add`/`evict` batches
on, and that boundary is built from the `SCRAPERS` registry of whichever checkout the *process*
imports. That is not the run's registry, and a worktree does not isolate it either: `scripts/` is
per-tree, the editable `headstart` install is not. So reading a run newer than the checkout used
to drop whole 100-id batches and blame the pipeline for it.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_RUNLOG = Path(__file__).resolve().parents[1] / "scripts" / "runlog"


@pytest.fixture(scope="module")
def merge():
    if str(_RUNLOG) not in sys.path:
        sys.path.insert(0, str(_RUNLOG))
    return importlib.import_module("fanout_merge")


def _batch(label: str, ids: list[str]) -> str:
    return f"[index] {label} [1-{len(ids)} of {len(ids)}]: " + " ".join(ids)


def test_an_ats_the_local_registry_never_heard_of_is_still_counted(merge):
    """The regression: `bamboohr` absent from this checkout's registry dropped the whole batch."""
    unknown = [f"neverheardof:acme:{i}" for i in range(merge._MIN_NAME_HITS + 2)]
    text = _batch("add", [*unknown, "greenhouse:stripe:1"])

    counts, dropped = merge._id_churn_by_ats(text)

    assert dropped["add"] == 0, "the batch was discarded instead of parsed"
    assert counts["add"]["neverheardof"] == len(unknown)
    assert counts["add"]["greenhouse"] == 1


def test_a_space_bearing_workday_id_is_not_split_into_fake_ids(merge):
    """The reason the boundary exists at all: a Workday id can carry spaces and commas, so a bare
    `.split()` shreds one id into several."""
    text = _batch(
        "evict",
        [
            "workday:gianteagle/GEExternalcareers:0018 - Shaler - Supermarket",
            "greenhouse:stripe:2",
        ],
    )

    counts, dropped = merge._id_churn_by_ats(text)

    assert dropped["evict"] == 0
    assert counts["evict"]["workday"] == 1
    assert counts["evict"]["greenhouse"] == 1


def test_colon_bearing_debris_inside_an_id_is_not_mistaken_for_a_provider(merge):
    """The regression the first version of `_boundary_from_log` shipped. Run 32624890700 carries
    `workday:rbs/rbs:Closing Date: 01/09/2026`, so a blind split leaves the token `Date:` once per
    id — five times in that run, over any sane count bar. Admitting it over-split those ids and
    dropped a 100-id evict batch that had parsed cleanly before the fix.

    What disqualifies `Date` is that nothing follows its colon: an id is `ats:tenant:native-id`,
    so a real provider prefix always carries a second colon. Recurrence alone does not separate
    them, because debris recurs too."""
    ids = [f"workday:rbs/rbs:Closing Date: 0{i}/09/2026" for i in range(1, 6)]
    text = _batch("evict", [*ids, "greenhouse:stripe:2"])

    counts, dropped = merge._id_churn_by_ats(text)

    assert dropped["evict"] == 0, (
        "`Date` was admitted as a provider and re-shredded the ids"
    )
    assert counts["evict"]["workday"] == len(ids)
    assert counts["evict"]["greenhouse"] == 1
    assert "Date" not in counts["evict"]


def test_a_batch_whose_count_still_disagrees_is_reported_not_silently_dropped(merge):
    """Trusting the `[start-end]` header over a wrong breakdown is right; doing it silently is the
    failure this warning exists to prevent."""
    text = "[index] add [1-9 of 9]: greenhouse:stripe:1 greenhouse:stripe:2"

    counts, dropped = merge._id_churn_by_ats(text)

    assert dropped["add"] == 9
    assert counts["add"] == {}
