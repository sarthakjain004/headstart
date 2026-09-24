"""Tests for the careers-page harvest fold (scripts/merge/merge_harvest_into_tenants.py).

It is a script under `scripts/merge`, so we put that directory on the path and import it by name,
the way `test_merge_wayback_into_tenants.py` does.
"""

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "merge"))

import merge_harvest_into_tenants as mh


def test_an_oracle_row_is_folded_under_the_pod_host_its_scraper_reads(
    tmp_path, monkeypatch
):
    """#627: the harvest's Oracle slug is a bare label (`bun`) with the pod host only in `url`,
    while the pool and the ledger hold a Board under that host. Folded as-is, all 464 such rows
    landed beside the host row, and 439 of them duplicated a Board already held. A row naming no
    pod host — a company name with no `url` (`akamai`), a vanity careers site — is not folded."""
    byp, merged = tmp_path / "by-provider", tmp_path / "ats-tenants-merged"
    byp.mkdir()
    merged.mkdir()
    host = "bun.fa.em2.oraclecloud.com"
    (byp / "oracle.csv").write_text(
        f"slug,url,n_sources,sources\nakamai,,1,x\nbun,{host},1,x\n"
        "cygl,https://cygl.fa.us2.oraclecloud.com/hcmUI/CandidateExperience,1,x\n"
        "cx_1,https://www.coherent.com/careers,1,x\n",
        encoding="utf-8",
    )
    (merged / "oracle.csv").write_text(
        f"ats,tenant,url,source\noracle,{host},https://{host},cc2026\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mh, "BYP", byp)
    monkeypatch.setattr(mh, "MERGED", merged)
    mh.main()
    with (merged / "oracle.csv").open(encoding="utf-8") as f:
        pool = {r["tenant"]: (r["url"], r["source"]) for r in csv.DictReader(f)}
    assert pool == {
        host: (f"https://{host}", "cc2026+harvest"),
        "cygl.fa.us2.oraclecloud.com": (
            "https://cygl.fa.us2.oraclecloud.com/hcmUI/CandidateExperience",
            "harvest",
        ),
    }
