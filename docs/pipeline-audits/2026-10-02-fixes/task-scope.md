# Pipeline reliability follow-up — requested scope

The user requested: fix the paired metadata/vector rewrite failure (audit point 1), determine
whether spare egress solved ATS failures/partial coverage (point 3), find the root cause of
evict/re-add flapping (point 4), fix browser shutdown failures (point 7), and investigate raising
Booz Allen concurrency, potentially using spare egress.

The implementation branch starts from `48ff7c14` (fresh `origin/main`). The prior audit is pinned
to `c547ce0`; relevant store, browser, Avature and Radancy behavior is unchanged between them.
Investigations must distinguish control runs, historical evidence, live bounded samples and
projections. Network tuning must preserve parsed IDs/fields and real per-route pacing rather
than assuming extra workers or a different address supplies an unlimited budget. No production
dataset writes or full pipeline dispatch are part of these probes.

Required acceptance: interruption tests preserve exact ID/vector correspondence and publication
refuses incomplete recovery; subprocess browser-exit tests stop producing the executor error,
while real Chrome cleanup removes both process and profile; flapping conclusions identify their
per-ID evidence limits; spare-egress/concurrency conclusions use matched controls and explicitly
qualify the difference between local probes and Actions runners.
