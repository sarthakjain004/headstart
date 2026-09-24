"""The committed Eightfold -> backing Board pairs (headstart.eightfold_backing, ADR-0210)."""

from __future__ import annotations

import pytest

from headstart import eightfold_backing


def test_a_site_with_several_backing_boards_keeps_them_all_in_order(tmp_path):
    path = tmp_path / "pairs.csv"
    path.write_text(
        "eightfold,backing\n"
        "Costar.Eightfold.ai,workday:costar/CoStarCareers\n"
        "nab.eightfold.ai,workday:nab/nab_careers\n"
        "costar.eightfold.ai,workday:costar/costar_campus\n",
        encoding="utf-8",
    )
    assert eightfold_backing.load(path) == {
        "costar.eightfold.ai": (
            "workday:costar/costarcareers",
            "workday:costar/costar_campus",
        ),
        "nab.eightfold.ai": ("workday:nab/nab_careers",),
    }


def test_the_committed_pairs_name_only_the_six_atses_that_state_a_requisition():
    """A backing Board on any other ATS would never carry a `requisition`, so its pair could
    never match; the file and the scrapers that fill the column must agree."""
    pairs = eightfold_backing.load()
    atses = {board.split(":", 1)[0] for boards in pairs.values() for board in boards}
    assert atses <= {
        "eightfold",
        "workday",
        "oracle",
        "greenhouse",
        "taleo_enterprise",
        "successfactors",
    }
    assert "lumen.eightfold.ai" not in pairs  # the user's decision (ADR-0205)
    assert len(pairs) == 39


_PAIRS = {
    "jobs.acme.com": ("workday:acme/external",),
    "arcadis.eightfold.ai": ("oracle:ebcs.fa.em2.oraclecloud.com",),
}


@pytest.mark.parametrize(
    ("job_id", "stamped"),
    [
        ("eightfold:jobs.acme.com:1099", True),  # a paired Eightfold site
        ("eightfold:Jobs.Acme.com:1099", True),  # any casing of it
        ("workday:acme/External:R-100", True),  # its backing site
        ("workday:acme/Campus:R-100", True),  # another site of that Workday tenant
        ("oracle:ebcs.fa.em2.oraclecloud.com:42863", True),
        ("workday:acmecorp/External:R-100", False),  # a tenant sharing the prefix
        ("eightfold:other.eightfold.ai:1", False),
        ("greenhouse:acme:4001", False),
    ],
)
def test_a_requisition_is_kept_only_on_a_board_the_pairs_name(
    monkeypatch, job_id, stamped
):
    """ADR-0210: only these rows can ever match, so only these carry the stamp; stamping any
    other would rewrite its served row for nothing."""
    monkeypatch.setattr(eightfold_backing, "load", lambda: _PAIRS)
    assert eightfold_backing.in_scope(job_id) is stamped
