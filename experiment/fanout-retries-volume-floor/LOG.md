# `fanout_retries`'s egress detector fires on a near-zero denominator

**2026-09-21.** `scripts/runlog/fanout_retries.py` classifies a scrape shard's egress from
`429-ratelimit / network` (`DIRECT_RATIO = 5.0`, `WARP_RATIO = 1.0`) and prints `DIRECT !MISMATCH`
when the ratio says direct but the shard never logged `degrading to direct`. It was firing on
healthy runs. This is the measurement behind the fix.

## Window

The 10 most recent `pipeline.yml` runs, 9 of them measurable — `35578784666` stood down, so it has
no scrape stage to read:

```
35569584172 35572417059 35575571888 35579899915 35583945947
35587348049 35590192500 35592873553 35595828212
```

135 shards. Captures (full analyser output, cached logs under `experiment/runlog/artifacts/`):

- `artifacts/2026-09-21_before_ratio-only.txt` — ratio only, as shipped.
- `artifacts/2026-09-21_after_volume-floor.txt` — with `MIN_EGRESS_RETRIES = 1000`.

## Before: 9 false positives, 4 of them with negative "excess"

`DIRECT !MISMATCH` on **6 of the 9 runs, 9 shards in total**, against **0 of 135 shards logging
`degrading to direct`** — all nine flagged shapes:

| shard | `network` | `429-ratelimit` | printed ratio |
| --- | --- | --- | --- |
| 13 | 2 | 230 | 115.00 |
| 14 | 3 | 18 | 6.00 |
| 13 | 3 | 19 | 6.33 |
| 0 | 2 | 17 | 8.50 |
| 13 | 2 | 20 | 10.00 |
| 10 | 11 | 473 | 43.00 |
| 14 | 0 | 20 | `inf` |
| 12 | 1 | 18 | 18.00 |
| 13 | 2 | 15 | 7.50 |

On 4 of the 6 runs the analyser's own excess line came out **negative** — `-71`, `-89`, `-78`,
`-74`: the shard flagged as having lost its proxy spent *fewer* rate-limit retries than a healthy
one, the exact opposite of the signature.

## Why: the regime moved, the thresholds did not

The module's calibration table is from runs `32261793515`/`32272854468` (2026-08):

| shard state (2026-08) | `network` | `429-ratelimit` |
| --- | --- | --- |
| WARP healthy | 5,000-19,000 | 1,100-2,900 |
| degraded to direct | 0-13 | **14,600-23,800** |

Re-measured over this window (135 shards):

| 2026-09 regime | `network` | `429-ratelimit` | `network` + `429` |
| --- | --- | --- | --- |
| per shard | 0-1,560 | 0-473 | 10-1,848 |
| whole run (all 15 shards) | 3,103-5,964 | 785-2,046 | — |

Whole-run retries of every class are 11,903-15,825 — comparable to what a *single* degraded shard
used to spend on 429s alone. So the ratio is now routinely computed against a denominator of 0-11,
and 15-20 429s is a shard that barely retried, not one being walled by an origin. The discriminator
that survives the regime change is the degraded population's **absolute** 429 volume (14,600+), not
the ratio.

## The floor

`MIN_EGRESS_RETRIES = 1000` on `network + 429-ratelimit`; below it `direct_ratio()` returns `None`,
so `verdict_of()` gives the existing `?` and the ratio column prints `-`. No new label: the table
already separates the two abstention causes visually (`-` = too quiet to read, a printed number
with `?` = in the ambiguous band), and the run summary now names the count.

Calibration — **calibrated, not proven**:

| anchor | value | ratio to the floor |
| --- | --- | --- |
| largest false positive in this window (11 + 473) | 484 | 2.1x below |
| smallest healthy shard the ratios were drawn from (5,000 + 1,100) | 6,100 | 6.1x above |
| degraded population's own 429 floor | 14,600 | 14.6x above |

Sweep over the 135 shards — every candidate kills all 9 false positives, and the choice is only how
much room is left to detect a *smaller* future degradation:

| floor | shards still classified | false positives surviving |
| --- | --- | --- |
| 600 | 33 / 135 | 0 |
| **1000** | **10 / 135** | **0** |
| 1500 | 2 / 135 | 0 |

600 was rejected: 1.24x over the noise already observed is too thin when the window's own 429
counts reach 473 on a single-digit-network shard. 1000 keeps ~1.5x of room under a degradation 10x
smaller than 2026-08's (which would still spend ~1,460).

That sweep is re-derivable from the committed before-capture, no script needed:

```bash
python3 -c '
rows = [(int(p[1]), int(p[2]), "MISMATCH" in ln)
        for ln in open("artifacts/2026-09-21_before_ratio-only.txt")
        for p in [ln.split()] if len(p) >= 8 and p[0].isdigit()]
for floor in (600, 1000, 1500):
    over = [r for r in rows if r[0] + r[1] >= floor]
    print(floor, len(over), "/", len(rows), "fp surviving:", sum(r[2] for r in over))'
```

## After

`!MISMATCH` **9 → 0**. 10 of 135 shards still classified (all `warp`, ratios 0.25-0.38); 125
abstain, and each run's summary says so:

```
egress: 0/15 shards look DIRECT by retry ratio; 0/15 logged 'degrading to direct';
13/15 spent under 1,000 network+429 retries, too few to read a ratio from (shown '-')
```

A genuinely degraded shard is still caught — verified as a unit test against the docstring's own
degraded population (`tests/test_fanout_retries.py`): all four corners of network 0-13 x 429
14,600-23,800 read `DIRECT`, and both corners of the healthy population still read `warp`.

## Not touched

`docs/workday/2026-09-09_parser-shaped-detail-losses.md` quotes this detector's "0 of 15 shards
DIRECT in all four runs". That is a negative finding and the floor can only turn `DIRECT` into `?`,
so it is not invalidated — but it now rests on abstention rather than on a positive healthy verdict.
Left alone deliberately.
