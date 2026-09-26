"""`fanout_retries`'s egress verdict — the volume the `429/network` ratio needs to mean anything.

The ratio was calibrated in 2026-08, when a shard spent thousands of retries of both classes. Retry
volume has since collapsed, and on the 9 runs re-measured 2026-09-21 the bare ratio called 9 shards
`DIRECT !MISMATCH` off denominators of 0-11 network retries while none of the 135 logged `degrading
to direct`. These pin both ends: the shapes that misfired must abstain, and the degraded population
the thresholds were drawn from must still be caught.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_RUNLOG = Path(__file__).resolve().parents[1] / "scripts" / "runlog"


@pytest.fixture(scope="module")
def retries():
    if str(_RUNLOG) not in sys.path:
        sys.path.insert(0, str(_RUNLOG))
    return importlib.import_module("fanout_retries")


def _verdict(mod, net: int, lim: int) -> str:
    return mod.verdict_of(mod.direct_ratio({"network": net, "429-ratelimit": lim}))


# Every shard the ratio flagged across runs 35569584172-35595828212, plus zero-of-both.
QUIET = [
    (2, 230),
    (3, 18),
    (3, 19),
    (2, 17),
    (2, 20),
    (11, 473),
    (0, 20),
    (1, 18),
    (2, 15),
    (0, 0),
]


@pytest.mark.parametrize(("net", "lim"), QUIET)
def test_a_shard_that_barely_retried_abstains(retries, net: int, lim: int) -> None:
    """The regression: `network=2, 429=17` reads 8.50 and printed DIRECT off two data points."""
    assert _verdict(retries, net, lim) == "?"


# The docstring's degraded population (runs 32261793515/32272854468): network 0-13, 429 14.6k-23.8k.
@pytest.mark.parametrize(
    ("net", "lim"), [(0, 14_600), (13, 14_600), (0, 23_800), (13, 23_800)]
)
def test_a_genuinely_degraded_shard_is_still_caught(
    retries, net: int, lim: int
) -> None:
    assert _verdict(retries, net, lim) == "DIRECT"


# The same runs' healthy population: network 5k-19k, 429 1.1k-2.9k.
@pytest.mark.parametrize(("net", "lim"), [(5_000, 1_100), (19_000, 2_900)])
def test_a_healthy_warp_shard_still_reads_warp(retries, net: int, lim: int) -> None:
    assert _verdict(retries, net, lim) == "warp"


def test_zohos_throttle_retries_are_a_column_and_its_centre_groups_parse(
    retries,
) -> None:
    """Zoho's deliberate `http-302` retry appeared in every run of 36200233818-36218633315, and its
    egress groups are now per data centre (`zoho.com`), which a `\\w+` group name cannot match."""
    line = (
        "[scrape_run] retries: 403-wall 20, 429-ratelimit 140, 5xx 594, http-302 1, "
        "network 700 (total 1455)"
    )
    counts, _total = retries.parse_retries(line)
    assert set(counts) <= set(retries.CLASSES)
    spent = "[spare_egress] zoho.com: origin returned 302 — spending this shard's spare egress"
    assert retries.SPENT.findall(spent) == [("zoho.com", "302")]
