# Taleo Enterprise detail concurrency ladder

Target: find the highest safe detail width for public D.R. Horton Career Section pages.
Acceptance: no 403/429/5xx or transport failures across two 32-detail runs; retain a width only
when throughput improves without a material latency collapse. Discovery/liveness traffic is paused
while this experiment runs.

The first 32-detail all-width ladder was discarded as an execution harness: its width-1 second
pass reached an isolated 30-second timeout and exceeded the desktop run window before testing
higher widths. `run.py --width N` now runs each width independently on 16 details twice.

| width | outcomes | median successful latency | elapsed runs |
| ---: | --- | ---: | --- |
| 1 | 30x 200, 2x timeout | 1.064s | 46.95s, 46.70s |
| 4 | 29x 200, 3x timeout | 1.064s | 4.42s, 35.52s |
| 8 | 32x 200 | 1.048s | 2.11s, 2.10s |
| 16 | 32x 200 | 1.047s | 1.14s, 1.09s |
| 32 | 31x 200, 1x timeout | 0.956s | 1.06s, 30.01s |

Decision: **keep 16**. No 403, 429, or 5xx occurred at any width; 32 adds a timeout without a
reliable throughput gain over 16.
