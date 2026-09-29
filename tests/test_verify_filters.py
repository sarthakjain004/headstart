import importlib.util
from pathlib import Path

_HARNESS = Path(__file__).resolve().parents[1] / "scripts/eval/verify_filters.py"


def _load_harness():
    spec = importlib.util.spec_from_file_location("verify_filters", _HARNESS)
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    return harness


def _row(job_id: str) -> dict:
    board, native = job_id.rsplit(":", 1)
    slug = board.split(":", 1)[1]
    return {"id": job_id, "url": f"https://jobs.lever.co/{slug}/{native}", "title": "T"}


def test_lever_links_are_sampled_one_per_board_across_every_query(monkeypatch):
    """ADR-0281: a Lever Board with its hosted pages off serves only dead links, and the top 3
    rows of one query reach one or two Boards. Lever reads the first row each Board ranks for,
    across the whole query battery; every other ATS still reads its top 3."""
    harness = _load_harness()
    ranked = {
        harness.QUERIES[0]: ["lever:a:1", "lever:a:2", "lever:b:1"],
        harness.QUERIES[1]: ["lever:b:2", "lever:c:1"],
    }

    def fake_get(base, params):
        if params["ats"] == "lever":
            return [_row(i) for i in ranked.get(params["q"], [])]
        return [_row(f"x:a:{n}") for n in range(5)]

    monkeypatch.setattr(harness, "_get", fake_get)
    checks = harness.run_url_checks("https://space", ["lever", "x"], http=False)
    assert [(c["ats"], c["url"]) for c in checks] == [
        ("lever", "https://jobs.lever.co/a/1"),
        ("lever", "https://jobs.lever.co/b/1"),
        ("lever", "https://jobs.lever.co/c/1"),
        ("x", "https://jobs.lever.co/a/0"),
        ("x", "https://jobs.lever.co/a/1"),
        ("x", "https://jobs.lever.co/a/2"),
    ]
