"""Tests for the from-scratch rebuild guard (scripts/merge/merge_tenants.py).

It is a script under `scripts/merge`, so we put that directory on the path and import it by name,
the way `test_merge_wayback_into_tenants.py` does.

Only the guard is covered. `merge_tenants.py` rebuilds each pool file from Common Crawl ∪ Wayback
alone, opening it with `"w"`, so it erases every row sourced `harvest` / `cc2026` / `fingerprint` /
`wayback2026` — and the repo's own README used to name it as *the* rebuild command. Measured
2026-08-14 against the real pool it would have discarded 26,824 rows to gain 20,926. These pin the
refusal so nobody (human or agent) can reach that outcome by running the obvious command.
"""

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "merge"))

import merge_tenants as mt  # noqa: E402


def write_csv(path: Path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def setup_pool(tmp_path, monkeypatch, argv, *, pool_rows, wayback_rows=()):
    """One ATS (zoho) with `pool_rows` in the pool and `wayback_rows` in the harvest."""
    out, wb = tmp_path / "merged", tmp_path / "wayback"
    write_csv(out / "zoho.csv", ["ats", "tenant", "url", "source"], pool_rows)
    write_csv(wb / "zoho.csv", ["ats", "tenant", "url"], wayback_rows)
    monkeypatch.setattr(mt, "OUT", out)
    monkeypatch.setattr(mt, "WB_DIR", wb)
    monkeypatch.setattr(mt, "CC_INDIA", tmp_path / "absent.csv")
    monkeypatch.setattr(mt, "CC_GLOBAL_DIR", tmp_path / "absent")
    monkeypatch.setattr(mt, "ATSES", ["zoho"])
    monkeypatch.setattr(sys, "argv", ["merge_tenants.py", *argv])
    return out / "zoho.csv"


def read_pool(path: Path):
    with path.open(encoding="utf-8") as f:
        return {r["tenant"]: r["source"] for r in csv.DictReader(f)}


def test_a_rebuild_that_would_lose_rows_refuses_and_writes_nothing(
    tmp_path, monkeypatch
):
    path = setup_pool(
        tmp_path,
        monkeypatch,
        [],
        pool_rows=[
            ["zoho", "fromharvest", "https://fromharvest.zohorecruit.com", "harvest"],
            ["zoho", "shared", "https://shared.zohorecruit.com", "wayback"],
        ],
        wayback_rows=[["zoho", "shared", "https://shared.zohorecruit.com"]],
    )
    before = path.read_bytes()

    assert mt.main() == 1, "must exit non-zero so a caller/CI notices"
    assert path.read_bytes() == before, "a dry run must not touch the pool"
    assert "fromharvest" in read_pool(path)


def test_force_actually_rebuilds_and_discards(tmp_path, monkeypatch):
    """The escape hatch still works — the guard is a speed bump, not a removal."""
    path = setup_pool(
        tmp_path,
        monkeypatch,
        ["--force"],
        pool_rows=[
            ["zoho", "fromharvest", "https://fromharvest.zohorecruit.com", "harvest"],
            ["zoho", "shared", "https://shared.zohorecruit.com", "wayback"],
        ],
        wayback_rows=[["zoho", "shared", "https://shared.zohorecruit.com"]],
    )
    assert mt.main() == 0
    after = read_pool(path)
    assert "fromharvest" not in after, "--force means exactly this: discard the rest"
    assert after == {"shared": "wayback"}


def test_a_rebuild_that_loses_nothing_is_allowed_to_proceed(tmp_path, monkeypatch):
    path = setup_pool(
        tmp_path,
        monkeypatch,
        [],
        pool_rows=[["zoho", "shared", "https://shared.zohorecruit.com", "wayback"]],
        wayback_rows=[["zoho", "shared", "https://shared.zohorecruit.com"]],
    )
    assert mt.main() == 0, "nothing at risk, so no reason to refuse"
    assert "shared" in read_pool(path)
