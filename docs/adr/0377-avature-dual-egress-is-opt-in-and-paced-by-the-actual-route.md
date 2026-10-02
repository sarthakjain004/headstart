# ADR-0377: Avature dual egress is opt-in and paced by the actual route

**Status:** accepted for opt-in evaluation · **Date:** 2026-10-02 · **Relates to:** ADR-0245,
ADR-0063, ADR-0067, ADR-0081

Avature's four detail streams already saturate its process-wide one-request/second pacer. A
matched local pooled-client control read 410 Jobs in 410.02 seconds on one route and 205.09
seconds on two, preserving all IDs, titles and description hashes. A 40-Job new-code control also
preserved every parsed field. This is not a measured Actions or full-pipeline speedup.

`HEADSTART_AVATURE_DUAL_EGRESS=1` opts into eight detail streams, with alternate native IDs
preferring spare egress. The workflow reads the same repository variable; unset means the
existing four-stream, single-pacer behavior. Do not enable it by default before a matched runner
control checks completeness and time at production volume.

The shared HTTP drivers pace each resolved route before every send, including retries. A due
slot is claimed only immediately before proceeding; a delayed caller waits then resolves again,
so rotation admission cannot turn old future reservations into a burst. If direct requests fall
back onto spare egress, they share its cap; if WARP is unavailable, both preferences share the
direct cap. Listing requests share these caps too. Opt-in HTTP 406 retries recover the currently
refused request rather than only moving later requests. Preferred-spare traffic is kept separate
from wall-rescue metrics.

Increasing workers alone cannot beat the original pacer. Increasing one IP's rate from a short
burst is unsupported by the prior sustained refusal measurement. Two independent lane pacers
were rejected because fallback can put both on one IP, and wrapper-only pacing misses internal
retries. The current Avature vanity resolves without AAAA records; Radancy's deep IPv6 egress
measurement is not evidence of an equivalent Avature rotation pool. The local measurements and
their client/pacing controls are recorded in the follow-up egress report.
